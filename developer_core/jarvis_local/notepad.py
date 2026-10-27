"""Allowlisted Notepad operator (first real app after the V2 test-window trial).

Windows 11 Notepad restores previous tabs, so a freshly started Notepad may show the
user's own documents. JARVIS therefore never targets "the active Notepad": it creates
its own empty file .jarvis_local/notepad/jarvis-<id>.txt, opens it as a tab, and every
action re-identifies exactly that tab (process + package path + window class + tab name
+ selected + editor class + document hash). Writes use UIA ValuePattern (no keystrokes),
are never saved to disk by JARVIS, and are read back and compared.
"""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import time
import uuid
from .session import Denied
from .windows import powershell, read_transcript

SCRIPT = Path(__file__).with_name('notepad_operator.ps1')
INSPECT_RE = re.compile(r'NOTEPAD_INSPECT=PASS pid=(\d+) hwnd=(\d+) tabs=(\d+) length=(\d+) sha256=([0-9a-f]{64})')
RESULT_RE = re.compile(r'NOTEPAD=PASS pid=(\d+) before_sha256=([0-9a-f]{64}) after_sha256=([0-9a-f]{64}) length=(\d+)')
MAX_TEXT = 2000


def text_sha256(text):
    return hashlib.sha256(text.replace('\r\n', '\n').replace('\r', '\n').encode('utf-8')).hexdigest()


def check_text(text):
    if not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT:
        raise Denied('Text must be 1..%d characters' % MAX_TEXT)
    if any(ord(c) < 32 and c not in '\n\t' for c in text):
        raise Denied('Control characters are not allowed')
    return text


def parse_result(text):
    m = RESULT_RE.search(text)
    if m:
        return {'outcome': 'PASS', 'pid': int(m.group(1)), 'before_sha256': m.group(2), 'after_sha256': m.group(3)}
    if 'NOTEPAD=CANCELLED' in text:
        return {'outcome': 'CANCELLED'}
    if 'NOTEPAD=UNKNOWN' in text:
        return {'outcome': 'UNKNOWN'}
    m = re.search(r'NOTEPAD_ERROR=(.*)', text)
    return {'outcome': 'DENIED', 'reason': m.group(1).strip()[:300]} if m else {'outcome': 'NO_RESULT'}


class NotepadOperator:
    def __init__(self, ws, runner=subprocess.run, popen=subprocess.Popen):
        self.ws, self.run, self.popen = ws, runner, popen
        self.dir = ws.state / 'notepad'
        self.dir.mkdir(exist_ok=True)
        self.current = self.dir / 'current.json'

    def _target(self):
        try:
            name = json.loads(self.current.read_text(encoding='utf-8'))['file']
        except (OSError, ValueError, KeyError):
            raise Denied('No JARVIS Notepad tab recorded; run notepad-open first') from None
        if not re.fullmatch(r'jarvis-[0-9a-f]{12}\.txt', name):
            raise Denied('Corrupt Notepad state')
        return name

    def inspect(self):
        name = self._target()
        proc = self.run([powershell(), '-NoProfile', '-NonInteractive', '-STA', '-ExecutionPolicy', 'Bypass', '-File',
                         str(SCRIPT), '-FileName', name, '-Inspect'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        encoding='utf-8', errors='replace', timeout=60, shell=False)
        m = INSPECT_RE.search(proc.stdout or '')
        if proc.returncode or not m:
            lines = ((proc.stderr or '') + (proc.stdout or '')).strip().splitlines()
            return {'status': 'FAIL', 'file': name, 'reason': (lines[0] if lines else 'no output')[:300]}
        return {'status': 'PASS', 'file': name, 'pid': int(m.group(1)), 'hwnd': int(m.group(2)),
                'tabs_in_window': int(m.group(3)), 'length': int(m.group(4)), 'sha256': m.group(5), 'read_only': True}

    def open(self):
        """Creates an empty JARVIS file and opens it as a Notepad tab (no text is written)."""
        name = 'jarvis-' + secrets.token_hex(6) + '.txt'
        path = self.dir / name
        with path.open('x', encoding='utf-8'):
            pass
        self.current.write_text(json.dumps({'file': name}), encoding='utf-8')
        self.popen(['notepad.exe', str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
        found = {'status': 'FAIL'}
        for _ in range(30):
            time.sleep(0.5)
            found = self.inspect()
            if found['status'] == 'PASS':
                break
        ok = found['status'] == 'PASS' and found['length'] == 0
        self.ws.journal.record('notepad-open', 'PASS' if ok else 'UNVERIFIED', file=name, inspect=found, rollback=[])
        return {'status': 'PASS' if ok else 'UNVERIFIED', 'file': name, 'inspect': found}

    def self_test(self):
        """Owner-authorised E2E self-test (27.10.2026): writes ONLY 'JARVIS SELFTEST <hex>' into the
        JARVIS tab, without a keyboard code, then reads it back. Arbitrary text still needs set_text."""
        before = self.inspect()
        if before['status'] != 'PASS':
            opened = self.open()
            before = opened['inspect']
            if before.get('status') != 'PASS':
                raise Denied('JARVIS Notepad tab could not be opened/selected')
        marker = 'JARVIS SELFTEST ' + secrets.token_hex(4)
        transcript = self.dir / ('selftest-' + uuid.uuid4().hex + '.txt')
        proc = self.run([powershell(), '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPT),
                         '-FileName', before['file'], '-SetText', '-SelfTest',
                         '-TextBase64', base64.b64encode(marker.encode('utf-8')).decode('ascii'),
                         '-ExpectedBeforeSha256', before['sha256'], '-Transcript', str(transcript)],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace',
                        timeout=120, shell=False)
        text = read_transcript(transcript) if transcript.is_file() else ''
        outcome = parse_result(text)
        foreground = re.search(r'NOTEPAD_FOREGROUND=(True|False)', text)
        after = self.inspect()
        verified = (outcome['outcome'] == 'PASS' and after['status'] == 'PASS' and after['pid'] == before['pid']
                    and after['sha256'] == text_sha256(marker))
        entry = self.ws.journal.record('notepad-selftest', 'PASS' if verified else 'UNVERIFIED',
                                       tab=before['file'], marker=marker, operator_outcome=outcome,
                                       foreground=foreground.group(1) if foreground else None,
                                       approval_mode='chat-authorized-selftest', saved_to_disk=False,
                                       exit_code=proc.returncode, rollback=[])
        return {'status': 'PASS' if verified else 'UNVERIFIED', 'marker': marker, 'operator': outcome,
                'foreground': foreground.group(1) if foreground else None, 'reinspected': after,
                'audit_id': entry['id']}

    def set_text(self, text, session):
        text = check_text(text)
        if not session.interactive or session.approval_mode != 'local-keyboard':
            raise Denied('Real UI action requires an interactive keyboard session (tests may not type)')
        before = self.inspect()
        if before['status'] != 'PASS':
            raise Denied('JARVIS Notepad tab not found or not selected: ' + before.get('reason', ''))
        plan = {'task': 'notepad-set-text', 'app': 'Windows Notepad (allowlisted)', 'tab': before['file'],
                'pid': before['pid'], 'method': 'UIA ValuePattern.SetValue (no keystrokes, not saved)',
                'replaces_length': before['length'], 'new_length': len(text), 'new_sha256': text_sha256(text),
                'preview': text[:120], 'note': 'Notepad operator will ask for its own second code'}
        approved = session.confirm(plan)
        transcript = self.dir / ('transcript-' + uuid.uuid4().hex + '.txt')
        proc = subprocess.run([powershell(), '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPT),
                               '-FileName', before['file'], '-SetText',
                               '-TextBase64', base64.b64encode(text.encode('utf-8')).decode('ascii'),
                               '-ExpectedBeforeSha256', before['sha256'], '-Transcript', str(transcript)],
                              timeout=300, shell=False)
        outcome = parse_result(read_transcript(transcript)) if transcript.is_file() else {'outcome': 'NO_TRANSCRIPT'}
        after = self.inspect()
        verified = (outcome['outcome'] == 'PASS' and after['status'] == 'PASS' and after['pid'] == before['pid']
                    and after['sha256'] == outcome['after_sha256'] == text_sha256(text))
        status = 'PASS' if verified else ('CANCELLED' if outcome['outcome'] == 'CANCELLED' else 'UNVERIFIED')
        # Text content is not journaled; only lengths and hashes.
        plan.pop('preview')
        entry = self.ws.journal.record('notepad-set-text', status, plan=plan, operator_outcome=outcome,
                                       reinspected_sha256=after.get('sha256'), exit_code=proc.returncode,
                                       saved_to_disk=False, irreversible=True, rollback=[], **approved)
        return {'status': status, 'operator': outcome, 'reinspected': after, 'audit_id': entry['id']}
