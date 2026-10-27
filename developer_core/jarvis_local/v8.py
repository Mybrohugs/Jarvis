"""File Operator + Office Agent, both executed by the existing V8 engine and V8 venv.

File search/plan/apply and Office reading go through V8 `operator_plus.cli` (via the
V9 `invoke_v8` bridge). Office edits run office_v8_worker.py in the V8 interpreter
and always produce a NEW file; the original is never written.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
from .session import Denied
from .workspace import STATE_DIR, canonical_hash, sha256

REQUIRED_PACKAGES = ('openpyxl', 'python-docx', 'defusedxml')
OFFICE_OPS = {'docx-append-paragraph': '.docx', 'xlsx-append-row': '.xlsx', 'xlsx-set-cell': '.xlsx'}
WORKER = Path(__file__).with_name('office_v8_worker.py')
MAX_OFFICE_BYTES = 12_000_000


def _text(value, name, limit=1000, *, spreadsheet=True):
    if not isinstance(value, str) or not value or len(value) > limit or '\x00' in value:
        raise Denied('Invalid ' + name)
    if spreadsheet and value[0] in '=+-@\t\r':
        raise Denied(name + ' may not start with a formula/control character')
    return value


class V8Engine:
    def __init__(self, root, ws):
        self.root = Path(root)
        self.ws = ws

    @property
    def python(self):
        return self.root / '.venv-ufo-v8' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')

    @staticmethod
    def _env():
        # Child stdio defaults to the ANSI code page (cp1251) when piped; force UTF-8.
        return {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}

    def call(self, command, args):
        """Same checks as the V9 bridge (invoke_v8 dry run), plus a UTF-8 child environment."""
        try:
            from ..ufo_unified import cli as v9  # optional: the V9 bridge of the full JARVIS DEV tree
        except ImportError:
            raise Denied('V9 bridge (developer_core.ufo_unified) is not installed') from None
        try:
            v9.invoke_v8(self.root, self.ws.root, command, args, dry_run=True)
        except v9.Denied as exc:
            raise Denied(str(exc)) from None
        argv = [str(self.python), '-B', '-m', 'operator_plus.cli', '--workspace', str(self.ws.root), command, *args]
        proc = subprocess.run(argv, cwd=self.root.resolve(strict=True), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding='utf-8', errors='strict', timeout=120, shell=False, env=self._env())
        if proc.returncode:
            raise Denied('V8 operation rejected: ' + (proc.stderr.strip()[:500] or str(proc.returncode)))
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise Denied('Unexpected V8 output') from None

    def worker(self, request, timeout=120):
        if not self.python.is_file():
            raise Denied('V8 virtual environment missing')
        # ASCII JSON both ways: immune to the child's code page.
        proc = subprocess.run([str(self.python), '-I', '-B', str(WORKER)], input=json.dumps(request, ensure_ascii=True),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='ascii', errors='replace',
                              timeout=timeout, shell=False, env=self._env())
        try:
            result = json.loads(proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            raise Denied('Office worker returned no result') from None
        if proc.returncode or result.get('status') != 'PASS':
            raise Denied('Office worker failed: ' + str(result.get('error', proc.returncode)))
        return result

    def capability(self):
        """Real check: executes V8 `deps` and the worker probe in the V8 venv."""
        info = {'v8_root': str(self.root), 'v8_available': False, 'office_worker_ready': False,
                'packages': None, 'reason': None, 'verified_by': 'executed V8 deps + worker probe'}
        try:
            deps = self.call('deps', [])
        except (Denied, OSError, subprocess.TimeoutExpired) as exc:
            info['reason'] = str(exc)[:300]
            return info
        packages = deps.get('packages') or {}
        missing = [k for k in REQUIRED_PACKAGES if not packages.get(k)]
        info['packages'] = packages
        info['v8_available'] = deps.get('status') == 'PASS' and not missing
        if missing:
            info['reason'] = 'missing packages: ' + ', '.join(missing)
            return info
        try:
            info['office_worker_ready'] = self.worker({'op': 'probe'}, timeout=60)['status'] == 'PASS'
        except (Denied, OSError, subprocess.TimeoutExpired) as exc:
            info['reason'] = 'worker probe: ' + str(exc)[:300]
        return info

    # ---- File Operator -------------------------------------------------
    def search(self, pattern):
        result = self.call('search', [pattern])
        result['files'] = [f for f in result.get('files', [])
                           if not f.split('/')[0].lower().startswith(('.jarvis_local', '.ufo_'))
                           and not Path(f).name.startswith('.~jarvis_tmp_')]
        return result

    def file_plan(self, entries):
        if not isinstance(entries, list) or not 1 <= len(entries) <= 25:
            raise Denied('Batch size must be 1..25')
        clean = []
        for e in entries:
            if not isinstance(e, dict) or set(e) != {'op', 'src', 'dst'} or e['op'] not in ('copy', 'move', 'rename'):
                raise Denied('Invalid entry')
            self.ws.resolve(e['src'], exists=True)
            self.ws.resolve(e['dst'])
            clean.append({'op': e['op'], 'src': e['src'].replace('\\', '/'), 'dst': e['dst'].replace('\\', '/')})
        name = canonical_hash(clean)[:32] + '.json'
        manifest = self.ws.state / 'manifests' / name
        if not manifest.exists():
            with manifest.open('x', encoding='utf-8') as f:
                json.dump(clean, f, ensure_ascii=False)
        plan = self.call('plan', [STATE_DIR + '/manifests/' + name])
        return {'status': 'PLAN_ONLY', 'entries': plan['entries'], 'approval': plan['plan_sha256'],
                'manifest': name, 'engine': 'V8'}

    def file_apply(self, entries, approval, session):
        plan = self.file_plan(entries)
        if approval != plan['approval']:
            raise Denied('Approval hash mismatch: plan changed or wrong hash')
        approved = session.confirm({'task': 'file-apply', 'entries': plan['entries'], 'approval': approval})
        rollback = []
        try:
            self.call('apply', [STATE_DIR + '/manifests/' + plan['manifest'], '--sha256', approval, '--approve'])
        except Denied as exc:
            rollback = self._observed_rollback(plan['entries'])
            self.ws.journal.record('file-apply', 'FAILED_OR_PARTIAL', reason=str(exc)[:300], rollback=rollback, **approved)
            raise
        rollback = self._observed_rollback(plan['entries'])
        if len(rollback) != len(plan['entries']):
            self.ws.journal.record('file-apply', 'VERIFY_FAILED', rollback=rollback, **approved)
            raise Denied('Post-action verification failed')
        entry = self.ws.journal.record('file-apply', 'PASS', entries=plan['entries'], rollback=rollback, **approved)
        return {'status': 'PASS', 'completed': len(rollback), 'audit_id': entry['id'], 'verified': True}

    def _observed_rollback(self, entries):
        steps = []
        for e in entries:
            dst = self.ws.resolve(e['dst'])
            if not dst.is_file() or sha256(dst) != e['sha256']:
                continue
            if e['op'] == 'copy':
                steps.append({'type': 'remove_created', 'path': e['dst'], 'sha256': e['sha256']})
            elif not self.ws.resolve(e['src']).exists():
                steps.append({'type': 'reverse_move', 'src': e['src'], 'dst': e['dst'], 'sha256': e['sha256']})
        return steps

    # ---- Office Agent --------------------------------------------------
    def office_read(self, rel):
        self.ws.resolve(rel, exists=True)
        return self.call('office-read', [rel.replace('\\', '/')])

    def office_plan(self, src, dst, op, payload):
        if op not in OFFICE_OPS:
            raise Denied('Office operation not allowlisted')
        a, b = self.ws.resolve(src, exists=True), self.ws.resolve(dst)
        if a.suffix.lower() != OFFICE_OPS[op] or b.suffix.lower() != OFFICE_OPS[op]:
            raise Denied('Operation does not match document type')
        if a == b or b.exists() or not b.parent.is_dir() or a.stat().st_size > MAX_OFFICE_BYTES:
            raise Denied('Destination must be a new file (originals are never edited)')
        payload = self._payload(op, payload)
        plan = {'task': 'office-edit-copy', 'op': op, 'src': src.replace('\\', '/'), 'dst': dst.replace('\\', '/'),
                'source_sha256': sha256(a), 'payload': payload}
        return {'status': 'PLAN_ONLY', 'plan': plan, 'approval': canonical_hash(plan), 'engine': 'V8 venv worker'}

    @staticmethod
    def _payload(op, payload):
        if not isinstance(payload, dict):
            raise Denied('Invalid payload')
        if op == 'docx-append-paragraph':
            return {'text': _text(payload.get('text'), 'text', 2000, spreadsheet=False)}
        sheet = payload.get('sheet')
        if sheet is not None and (not isinstance(sheet, str) or not 0 < len(sheet) <= 31):
            raise Denied('Invalid sheet name')
        if op == 'xlsx-append-row':
            cells = payload.get('cells')
            if not isinstance(cells, list) or not 1 <= len(cells) <= 30:
                raise Denied('Row must have 1..30 cells')
            return {'sheet': sheet, 'cells': [_text(c, 'cell', 500) for c in cells]}
        cell = payload.get('cell')
        if not isinstance(cell, str) or not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,6}', cell):
            raise Denied('Cell reference must look like B2')
        return {'sheet': sheet, 'cell': cell, 'value': _text(payload.get('value'), 'value', 500)}

    def office_apply(self, src, dst, op, payload, approval, session):
        planned = self.office_plan(src, dst, op, payload)
        if approval != planned['approval']:
            raise Denied('Approval hash mismatch: plan changed or wrong hash')
        plan = planned['plan']
        approved = session.confirm({'task': 'office-apply', 'plan': plan, 'approval': approval})
        a, b = self.ws.resolve(src, exists=True), self.ws.resolve(dst)
        try:
            result = self.worker({'op': op, 'src': str(a), 'dst': str(b), 'payload': plan['payload'],
                                  'source_sha256': plan['source_sha256']})
        except Denied as exc:
            leftover = b.is_file()
            self.ws.journal.record('office-apply', 'FAILED', reason=str(exc)[:300], dst=plan['dst'],
                                   partial_destination=leftover, **approved)
            raise
        actual = sha256(b)
        original_unchanged = sha256(a) == plan['source_sha256']
        rollback = [{'type': 'remove_created', 'path': plan['dst'], 'sha256': actual}]
        if actual != result['dst_sha256'] or not original_unchanged:
            self.ws.journal.record('office-apply', 'VERIFY_FAILED', rollback=rollback, **approved)
            raise Denied('Post-action verification failed')
        entry = self.ws.journal.record('office-apply', 'PASS', op=op, src=plan['src'], dst=plan['dst'],
                                       source_sha256=plan['source_sha256'], dst_sha256=actual,
                                       rollback=rollback, **approved)
        return {'status': 'PASS', 'created': plan['dst'], 'dst_sha256': actual, 'original_unchanged': True,
                'verified_by_reload': result['verified_by_reload'], 'audit_id': entry['id']}
