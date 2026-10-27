"""Feature 5: jarvis doctor — self-check of every JARVIS component (read-only, bounded timeouts).

Result: list of {name, status PASS|WARN|FAIL, detail, fix} + a one-sentence Russian summary.
Level 1 of self-repair: it diagnoses and says exactly what to run; it does not change anything.
"""
from __future__ import annotations
import datetime
import http.client
import json
import shutil
import subprocess
import time

TAILSCALE = r'C:\Program Files\Tailscale\tailscale.exe'
START = r'scripts\jarvis_phone_start.ps1'


def _http_ok(port, path, timeout=3):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        c.request('GET', path, headers={'Host': '127.0.0.1:%d' % port})
        return c.getresponse().status
    finally:
        c.close()


class Doctor:
    def __init__(self, router, run=subprocess.run, http_ok=_http_ok, clock=time.time):
        self.r, self.run, self.http_ok, self.clock = router, run, http_ok, clock

    def _check(self, name, fn):
        try:
            status, detail, fix = fn()
        except Exception as exc:  # a broken component must not break the whole report
            status, detail, fix = 'FAIL', type(exc).__name__ + ': ' + str(exc)[:160], None
        return {'name': name, 'status': status, 'detail': detail, 'fix': fix}

    def v8(self):
        cap = self.r.files.capability()
        return ('PASS' if cap['v8_available'] and cap['office_worker_ready'] else 'FAIL',
                'V8 + Office: ' + ('готов' if cap['v8_available'] else str(cap.get('reason'))), None)

    def journal(self):
        entries = self.r.ws.journal.entries()
        corrupt = sum(1 for e in entries if e.get('status') == 'CORRUPT_LINE')
        day = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
        recent = [e for e in entries if e.get('utc', '') >= day.isoformat()]
        bad = [e for e in recent if e.get('status') in ('FAILED', 'VERIFY_FAILED', 'UNVERIFIED', 'FAILED_OR_PARTIAL')]
        status = 'FAIL' if corrupt else ('WARN' if bad else 'PASS')
        return status, 'записей %d, за сутки %d, проблемных %d, повреждённых %d' % (
            len(entries), len(recent), len(bad), corrupt), None

    def tv(self):
        state = self.r.media.tv.state()
        return 'PASS', 'ТВ: %s' % ('включён, громкость %s' % state.get('volume') if state['power'] == 'active'
                                   else 'в режиме ожидания'), None

    def service(self, port, label):
        def fn():
            code = self.http_ok(port, '/health' if port == 10791 else '/')
            return ('PASS', label + ': работает', None) if code == 200 else (
                'FAIL', label + ': HTTP %s' % code, 'запустите ' + START)
        return fn

    def executor(self):
        hb = self.r.ws.state / 'executor_heartbeat.txt'
        age = self.clock() - float(hb.read_text(encoding='ascii')) if hb.is_file() else None
        if age is not None and age < 30:
            return 'PASS', 'исполнитель команд телефона: работает', None
        return 'FAIL', 'исполнитель команд телефона не отвечает', 'запустите ' + START

    def tailscale(self):
        p = self.run([TAILSCALE, 'status', '--json'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     encoding='utf-8', errors='replace', timeout=10)
        data = json.loads(p.stdout or '{}')
        if data.get('BackendState') != 'Running':
            return 'FAIL', 'Tailscale: ' + str(data.get('BackendState')), 'откройте Tailscale и войдите'
        peers = [v for v in (data.get('Peer') or {}).values() if v.get('OS') == 'iOS']
        online = any(v.get('Online') for v in peers)
        return ('PASS' if online else 'WARN'), 'Tailscale: работает, iPhone %s' % ('в сети' if online else 'не в сети'), None

    def disk(self):
        free = shutil.disk_usage('C:\\').free / 2 ** 30
        return ('PASS' if free > 20 else 'WARN'), 'свободно на C: %.0f ГБ' % free, None

    def push(self):
        n = len(self.r.push.subscriptions()) if hasattr(self.r, 'push') else 0
        return ('PASS' if n else 'WARN'), 'уведомления: %d устр.' % n, (
            None if n else 'на iPhone нажмите «Включить уведомления»')

    def report(self):
        checks = [self._check('V8/Office', self.v8), self._check('Журнал', self.journal),
                  self._check('Телевизор', self.tv), self._check('Шлюз iPhone', self.service(10790, 'шлюз iPhone')),
                  self._check('Голос', self.service(10791, 'голосовой сервер')),
                  self._check('Исполнитель', self.executor), self._check('Tailscale', self.tailscale),
                  self._check('Диск', self.disk), self._check('Уведомления', self.push)]
        fails = [c for c in checks if c['status'] == 'FAIL']
        warns = [c for c in checks if c['status'] == 'WARN']
        if not fails and not warns:
            summary = 'Все системы в норме.'
        elif not fails:
            summary = 'Системы работают, есть замечания: %s.' % ', '.join(c['name'] for c in warns)
        else:
            summary = 'Требуют внимания: %s.' % ', '.join(c['name'] for c in fails)
        status = 'FAIL' if fails else ('WARN' if warns else 'PASS')
        self.r.ws.journal.record('doctor', status, failed=[c['name'] for c in fails], rollback=[])
        return {'status': status, 'summary': summary, 'checks': checks}
