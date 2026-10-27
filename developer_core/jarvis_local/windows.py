"""WindowsOperator bridge to the reviewed V2 UIA prototype (test window ONLY).

select -> validate -> visible plan -> local keyboard approval (JARVIS) -> V2 challenge
typed in the same local console -> invoke -> V2 verification -> independent re-inspection.
No other application/window is targetable. UIA snapshots are summarised read-only and are
never treated as proof of control.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import uuid
from .session import Denied

TEST_WINDOW_TITLE = 'JARVIS-UFO-V2-TEST-ONLY'
WRAPPER = Path(__file__).with_name('windows_v2_invoke.ps1')
INSPECT_RE = re.compile(r'UFO_V2_INSPECT=PASS pid=(\d+) button=UNIQUE counter=COUNTER: (\d+)')
RESULT_RE = re.compile(r'UFO_V2=PASS before=COUNTER: (\d+) after=COUNTER: (\d+)')
CREATE_NO_WINDOW = 0x08000000


def powershell():
    return str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe')


def read_transcript(path):
    raw = Path(path).read_bytes()
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16', errors='replace')
    return raw.decode('utf-8-sig', errors='replace')


def parse_invoke(text):
    m = RESULT_RE.search(text)
    if m:
        return {'outcome': 'PASS', 'before': int(m.group(1)), 'after': int(m.group(2))}
    for marker in ('UFO_V2=CANCELLED', 'UFO_V2=UNKNOWN', 'DENIED'):
        if marker in text:
            return {'outcome': marker.split('=')[-1]}
    return {'outcome': 'NO_RESULT'}


class WindowsOperator:
    def __init__(self, v2_root, ws, runner=subprocess.run):
        self.root = Path(v2_root)
        self.ws = ws
        self.run = runner
        self.state = ws.state / 'windows'
        self.state.mkdir(exist_ok=True)

    def _script(self, name):
        p = self.root / name
        if not p.is_file() or p.is_symlink():
            raise Denied('V2 script missing: ' + name)
        return p

    def _ps(self, script, *args, timeout=60):
        if os.name != 'nt':
            raise Denied('Windows only')
        return self.run([powershell(), '-NoProfile', '-NonInteractive', '-STA', '-ExecutionPolicy', 'Bypass',
                         '-File', str(script), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        encoding='utf-8', errors='replace', timeout=timeout, shell=False)

    def check(self):
        proc = self._ps(self._script('Check.ps1'))
        ok = proc.returncode == 0 and 'UFO_V2_SYNTAX=PASS' in proc.stdout and 'UFO_V2_MODE=TEST_WINDOW_ONLY' in proc.stdout
        return {'status': 'PASS' if ok else 'FAIL', 'syntax_checked': ok, 'mode': 'TEST_WINDOW_ONLY',
                'windows_write_enabled': False, 'detail': (proc.stderr or '').strip()[:300] or None}

    def inspect(self):
        proc = self._ps(self._script('Operator.ps1'), '-InspectOnly')
        m = INSPECT_RE.search(proc.stdout or '')
        if proc.returncode or not m or 'UFO_V2_READ_ONLY=PASS' not in proc.stdout:
            reason = ((proc.stderr or '') + (proc.stdout or '')).strip().splitlines()
            return {'status': 'FAIL', 'test_window_found': False, 'reason': (reason[0] if reason else 'no output')[:300]}
        return {'status': 'PASS', 'test_window_found': True, 'pid': int(m.group(1)), 'counter': int(m.group(2)),
                'read_only': True, 'title': TEST_WINDOW_TITLE}

    # ---- the dedicated test window ------------------------------------
    def _pidfile(self):
        return self.state / 'test_window.json'

    def open_test_window(self):
        if self.inspect()['status'] == 'PASS':
            raise Denied('A test window is already open')
        proc = subprocess.Popen([powershell(), '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File',
                                 str(self._script('TestWindow.ps1'))], creationflags=CREATE_NO_WINDOW,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._pidfile().write_text(json.dumps({'pid': proc.pid}), encoding='utf-8')
        self.ws.journal.record('windows-test-window-open', 'PASS', pid=proc.pid, rollback=[])
        return {'status': 'STARTED', 'pid': proc.pid, 'title': TEST_WINDOW_TITLE}

    def close_test_window(self):
        """Closes only the PowerShell process this tool started for the test window."""
        try:
            pid = int(json.loads(self._pidfile().read_text(encoding='utf-8'))['pid'])
        except (OSError, ValueError, KeyError):
            raise Denied('No test window started by JARVIS is recorded') from None
        found = self.run(['tasklist', '/FI', 'PID eq %d' % pid, '/FO', 'CSV', '/NH'], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, encoding='utf-8', errors='replace', timeout=20)
        if not found.stdout.lower().startswith('"powershell.exe","%d"' % pid):
            self._pidfile().unlink(missing_ok=True)
            return {'status': 'ALREADY_CLOSED', 'pid': pid}
        self.run(['taskkill', '/PID', str(pid), '/T', '/F'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                 encoding='utf-8', errors='replace', timeout=20)
        self._pidfile().unlink(missing_ok=True)
        self.ws.journal.record('windows-test-window-close', 'PASS', pid=pid, rollback=[])
        return {'status': 'CLOSED', 'pid': pid}

    # ---- the single real UI action ------------------------------------
    def invoke_test_button(self, session):
        if not session.interactive or session.approval_mode != 'local-keyboard':
            raise Denied('Real UI action requires an interactive keyboard session (tests may not click)')
        before = self.inspect()
        if before['status'] != 'PASS':
            raise Denied('Test window not found; run windows-test-window first')
        plan = {'task': 'windows-invoke-test', 'window': TEST_WINDOW_TITLE, 'pid': before['pid'],
                'control': 'JARVIS_UFO_V2_BUTTON (InvokePattern)', 'expected': 'COUNTER %d -> %d' % (
                    before['counter'], before['counter'] + 1),
                'note': 'V2 will ask for its own second code in this console'}
        approved = session.confirm(plan)
        transcript = self.state / ('transcript-' + uuid.uuid4().hex + '.txt')
        # Inherit this console: V2 refuses redirected stdio, so no capture here.
        proc = subprocess.run([powershell(), '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File',
                               str(WRAPPER), '-Operator', str(self._script('Operator.ps1')),
                               '-Transcript', str(transcript)], timeout=300, shell=False)
        outcome = parse_invoke(read_transcript(transcript)) if transcript.is_file() else {'outcome': 'NO_TRANSCRIPT'}
        after = self.inspect()
        verified = (outcome['outcome'] == 'PASS' and after['status'] == 'PASS' and after['pid'] == before['pid']
                    and outcome['before'] == before['counter'] and after['counter'] == outcome['after'] == before['counter'] + 1)
        status = 'PASS' if verified else ('CANCELLED' if outcome['outcome'] == 'CANCELLED' else 'UNVERIFIED')
        entry = self.ws.journal.record('windows-invoke-test', status, plan=plan, v2_outcome=outcome,
                                       reinspected_counter=after.get('counter'), exit_code=proc.returncode,
                                       irreversible=True, rollback=[], **approved)
        return {'status': status, 'v2': outcome, 'reinspected': after, 'audit_id': entry['id']}

    def snapshot_summary(self, report):
        try:
            from ..ufo_unified import cli as v9  # optional: the V9 bridge of the full JARVIS DEV tree
        except ImportError:
            raise Denied('V9 bridge (developer_core.ufo_unified) is not installed') from None
        try:
            result = v9.read_snapshot(Path(report))
        except v9.Denied as exc:
            raise Denied(str(exc)) from None
        return {**result, 'proof_of_control': False}
