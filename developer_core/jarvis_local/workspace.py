"""Workspace containment, SHA-256 and the audit/rollback journal."""
from __future__ import annotations
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid
from .session import Denied

STATE_DIR = '.jarvis_local'
INTERNAL_PREFIXES = ('.jarvis_local', '.ufo_')


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def canonical_hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def _is_link(p):
    return p.is_symlink() or getattr(p, 'is_junction', lambda: False)()


class Workspace:
    def __init__(self, root):
        root = Path(root).expanduser()
        if _is_link(root):
            raise Denied('Workspace root may not be a symlink/junction')
        self.root = root.resolve(strict=True)
        if not self.root.is_dir() or self.root == Path(self.root.anchor) or self.root == Path.home().resolve():
            raise Denied('Workspace must be a dedicated directory')
        self.state = self.root / STATE_DIR
        if _is_link(self.state):
            raise Denied('State directory may not be a symlink/junction')
        for sub in ('', 'manifests', 'quarantine'):
            (self.state / sub).mkdir(exist_ok=True)
        self.journal = Journal(self.state / 'audit.jsonl')

    def resolve(self, rel, *, exists=False):
        if not isinstance(rel, str) or not rel or len(rel) > 400 or '\x00' in rel or ':' in rel:
            raise Denied('Invalid relative path')
        raw = rel.replace('\\', '/')
        p = Path(raw)
        if p.is_absolute() or p.drive or any(part in ('', '.', '..') for part in raw.split('/')):
            raise Denied('Unsafe path')
        if p.parts[0].lower().startswith(INTERNAL_PREFIXES):
            raise Denied('Internal JARVIS/UFO state is not addressable')
        cur = self.root
        for part in p.parts:
            cur = cur / part
            if _is_link(cur):
                raise Denied('Symlink/junction traversal denied')
        out = cur.resolve(strict=False)
        if not out.is_relative_to(self.root) or out == self.root:
            raise Denied('Outside workspace')
        if exists and not out.is_file():
            raise Denied('File not found: ' + rel)
        return out

    def rel(self, path):
        return Path(path).relative_to(self.root).as_posix()

    def quarantine(self, path, entry_id):
        """Rollback never deletes: it moves the file into a per-entry quarantine folder."""
        target_dir = self.state / 'quarantine' / entry_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / (uuid.uuid4().hex[:8] + '-' + Path(path).name)
        os.replace(path, target)
        return target


class Journal:
    def __init__(self, path):
        self.path = Path(path)

    def record(self, task, status, **fields):
        entry = {'id': uuid.uuid4().hex, 'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 'task': task, 'status': status, **fields}
        with self.path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        return entry

    def entries(self):
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding='utf-8').splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({'status': 'CORRUPT_LINE'})
        return out

    def find(self, entry_id):
        matches = [e for e in self.entries() if e.get('id') == entry_id]
        if len(matches) != 1:
            raise Denied('Audit entry not found')
        return matches[0]

    def rolled_back(self, entry_id):
        return any(e.get('task') == 'rollback' and e.get('status') == 'PASS' and e.get('target') == entry_id
                   for e in self.entries())


def plan_rollback(ws, entry):
    """Validate every rollback step before touching anything; returns a list of callables."""
    steps = entry.get('rollback') or []
    if not steps:
        raise Denied('Entry has no rollback (irreversible or read-only)')
    prepared = []
    for step in steps:
        if step.get('type') == 'remove_created':
            p = ws.resolve(step['path'], exists=True)
            if sha256(p) != step['sha256']:
                raise Denied('Created file was modified since; manual review required: ' + step['path'])
            prepared.append(('quarantine', p, step))
        elif step.get('type') == 'reverse_move':
            src, dst = ws.resolve(step['src']), ws.resolve(step['dst'], exists=True)
            if src.exists() or sha256(dst) != step['sha256'] or not src.parent.is_dir():
                raise Denied('Cannot reverse move safely: ' + step['dst'])
            prepared.append(('move_back', (dst, src), step))
        else:
            raise Denied('Unknown rollback step')
    return prepared


def execute_rollback(ws, entry_id, prepared):
    done = []
    for kind, target, step in prepared:
        if kind == 'quarantine':
            done.append({'quarantined': step['path'], 'to': ws.rel(ws.quarantine(target, entry_id))})
        else:
            dst, src = target
            with dst.open('rb') as a, src.open('xb') as b:
                shutil.copyfileobj(a, b)
            if sha256(src) != step['sha256']:
                src.unlink()
                raise Denied('Rollback copy verification failed')
            dst.unlink()
            done.append({'restored': step['src']})
    return done
