"""Unified Task Router: one local entry point for file / office / browser / windows tasks.

Each task declares its privilege. Nothing here is registered in Workbench, MCP, Telegram
or mobile dispatch; status() checks that by reading those sources.
"""
from __future__ import annotations
from pathlib import Path
from .browser import BrowserAgent
from .home_media import MediaOperator
from .notepad import NotepadOperator
from .push import Push
from .pwa_gateway import PhoneQueue
from .session import Denied, TrustedLocalSession
from .v8 import V8Engine
from .windows import WindowsOperator
from .workspace import Workspace, execute_rollback, plan_rollback

RESEARCH = Path.home() / 'Documents' / 'Codex' / 'UFO-Research'
DEFAULT_V8 = RESEARCH / 'JARVIS_UFO_Unified_Functional_V8'
DEFAULT_V2 = RESEARCH / 'UFO_JARVIS_WindowsOperator_V2'
REMOTE_SURFACES = ('workbench.py', 'mcp_server.py', 'mobile_gateway.py', 'telegram_bridge.py', 'telegram_runtime.py',
                   'telegram_service.py', 'telegram_ai.py', 'telegram_buttons.py', 'telegram_views.py')

# privilege: read = no side effects outside internal state; write = approval + audit + rollback;
# ui = real Windows action (keyboard session only); local = opens/closes the dedicated test window.
TASKS = {
    'status': 'read', 'search': 'read', 'office-read': 'read', 'file-plan': 'read', 'office-plan': 'read',
    'browser-status': 'read', 'browser-selftest': 'read', 'browser-read': 'read', 'windows-check': 'read',
    'windows-inspect': 'read', 'windows-snapshot': 'read', 'audit': 'read',
    'file-apply': 'write', 'office-apply': 'write', 'browser-allow-host': 'write', 'rollback': 'write',
    'windows-test-window': 'local', 'windows-close-test-window': 'local', 'windows-invoke-test': 'ui',
    'notepad-inspect': 'read', 'notepad-open': 'local', 'notepad-set-text': 'ui',
    'notepad-selftest': 'selftest',  # fixed marker text only; owner-authorised, audited
    'phone-queue': 'read', 'phone-queue-close': 'local',
    'media-status': 'read', 'media-power': 'write', 'media-volume': 'write', 'media-mute': 'write',
    'media-bass': 'write', 'media-ps5': 'write', 'media-scene': 'write',
    'doctor': 'read', 'briefing': 'read', 'briefing-city': 'selftest', 'push-test': 'selftest',
    'pc-find': 'write', 'pc-send': 'write', 'pc-clip-get': 'write', 'pc-clip-set': 'write', 'pc-screen': 'write',
    'profile-show': 'read', 'profile-remember': 'write', 'profile-forget': 'write', 'profile-style': 'write',
    'speech-create': 'write',  # sends the topic to Claude (paid), writes new files only
}


def remote_registration(package_dir=None):
    package_dir = Path(package_dir or Path(__file__).resolve().parents[1])
    hits = []
    for name in REMOTE_SURFACES:
        p = package_dir / name
        if p.is_file() and 'jarvis_local' in p.read_text(encoding='utf-8', errors='replace'):
            hits.append(name)
    return hits


class UnifiedTaskRouter:
    def __init__(self, session, workspace, v8_root=DEFAULT_V8, v2_root=DEFAULT_V2):
        if not isinstance(session, TrustedLocalSession):
            raise Denied('Trusted Local Session required')
        self.session = session
        self.ws = Workspace(workspace)
        self.files = self.office = V8Engine(v8_root, self.ws)
        self.browser = BrowserAgent(self.ws)
        self.windows = WindowsOperator(v2_root, self.ws)
        self.notepad = NotepadOperator(self.ws)
        self.media = MediaOperator(self.ws)
        self.push = Push(self.ws)

    def dispatch(self, task, **kw):
        self.session.check()
        privilege = TASKS.get(task)
        if privilege is None:
            raise Denied('Unknown task: ' + str(task))
        if privilege == 'local' and self.session.approval_mode == 'read-only':
            raise Denied('Opening/closing the test window needs an interactive local session')
        handler = getattr(self, '_' + task.replace('-', '_'))
        if privilege == 'read':
            return handler(**kw)
        try:
            return handler(**kw)
        except Exception as exc:
            # Every refused or failed write/UI attempt is audited too (declined code, TV error, ...).
            self.ws.journal.record(task, 'DENIED', reason=type(exc).__name__ + ': ' + str(exc)[:300],
                                   approval_mode=self.session.approval_mode, rollback=[])
            raise

    def _status(self):
        v8 = self.files.capability()
        hits = remote_registration()
        return {'status': 'PASS' if v8['v8_available'] else 'DEGRADED', 'local_only': True,
                'workbench_registered': bool(hits), 'remote_surfaces_referencing': hits,
                'v8_available': v8['v8_available'], 'v8': v8,
                'office_writes': 'copies only, approval hash + local keyboard',
                'windows_write_enabled': 'test-window-only' if self.windows.root.is_dir() else False,
                'windows_v2_present': (self.windows.root / 'Operator.ps1').is_file(),
                'browser_interactive_control': False,
                'session': {'approval_mode': self.session.approval_mode, 'interactive': self.session.interactive},
                'workspace': str(self.ws.root)}

    def _search(self, pattern):
        return self.files.search(pattern)

    def _office_read(self, path):
        return self.office.office_read(path)

    def _file_plan(self, entries):
        return self.files.file_plan(entries)

    def _file_apply(self, entries, approval):
        return self.files.file_apply(entries, approval, self.session)

    def _office_plan(self, src, dst, op, payload):
        return self.office.office_plan(src, dst, op, payload)

    def _office_apply(self, src, dst, op, payload, approval):
        return self.office.office_apply(src, dst, op, payload, approval, self.session)

    def _browser_status(self):
        return self.browser.status()

    def _browser_selftest(self):
        return self.browser.selftest()

    def _browser_read(self, url):
        return self.browser.read(url)

    def _browser_allow_host(self, host):
        return self.browser.allow_host(host, self.session)

    def _windows_check(self):
        return self.windows.check()

    def _windows_inspect(self):
        return self.windows.inspect()

    def _windows_snapshot(self, report):
        return self.windows.snapshot_summary(report)

    def _windows_test_window(self):
        return self.windows.open_test_window()

    def _windows_close_test_window(self):
        return self.windows.close_test_window()

    def _windows_invoke_test(self):
        return self.windows.invoke_test_button(self.session)

    def _notepad_inspect(self):
        return self.notepad.inspect()

    def _notepad_open(self):
        return self.notepad.open()

    def _notepad_selftest(self):
        return self.notepad.self_test()

    def _notepad_set_text(self, text):
        return self.notepad.set_text(text, self.session)

    def _media_status(self):
        return self.media.status()

    def _media_power(self, on):
        return self.media.power(on, self.session)

    def _media_volume(self, level=None, delta=None):
        return self.media.volume(self.session, level, delta)

    def _media_mute(self, on):
        return self.media.mute(on, self.session)

    def _media_bass(self, level=None, delta=None):
        return self.media.bass(self.session, level, delta)

    def _media_ps5(self):
        return self.media.ps5(self.session)

    def _media_scene(self, name):
        return self.media.scene(name, self.session)

    def _pc(self, task, fn):
        """pc-* tasks reach the phone, so even reads go through an approval (allowlist or keyboard)."""
        self.session.confirm({'task': task})
        from .pc_tools import PCTools
        if not hasattr(self, 'pc'):
            self.pc = PCTools(self.ws)
        return fn(self.pc)

    def _pc_find(self, query):
        return self._pc('pc-find', lambda pc: pc.find(query))

    def _pc_send(self, index):
        return self._pc('pc-send', lambda pc: pc.send(index))

    def _pc_clip_get(self):
        return self._pc('pc-clip-get', lambda pc: pc.clip_get())

    def _pc_clip_set(self, text):
        return self._pc('pc-clip-set', lambda pc: pc.clip_set(text))

    def _pc_screen(self):
        return self._pc('pc-screen', lambda pc: pc.screen())

    @property
    def profile(self):
        if not hasattr(self, '_profile'):
            from .profile import Profile
            self._profile = Profile(self.ws)
        return self._profile

    def _profile_show(self):
        return self.profile.show()

    def _profile_remember(self, kind, text):
        self.session.confirm({'task': 'profile-remember', 'kind': kind})
        return self.profile.remember(kind, text)

    def _profile_forget(self, what=None):
        self.session.confirm({'task': 'profile-forget'})
        return self.profile.forget(what)

    def _profile_style(self, style):
        self.session.confirm({'task': 'profile-style'})
        return self.profile.set_style(style)

    def _speech_create(self, topic, minutes=10, audience='', tone='', notes='', themes=('hud',)):
        from .presentation import THEMES, create
        from .slide_images import providers
        if not themes or any(t not in THEMES for t in themes):
            raise Denied('Themes must be from: ' + ', '.join(THEMES))
        images = getattr(self, 'speech_images', None)
        images = providers() if images is None else images
        self.session.confirm({'task': 'speech-create', 'topic': topic[:120], 'minutes': minutes,
                              'sent_to': 'Anthropic Claude API (paid, about $0.3-0.6)', 'notes_chars': len(notes),
                              'pictures_from': [p.name for p in images] or 'none', 'themes': list(themes)})
        planner = getattr(self, 'speech_planner', None)
        if planner is None:
            from .claude_writer import speech_plan as planner
        plan = planner(topic, minutes, audience=audience, tone=tone, notes=notes)
        return create(self.ws, plan, minutes, themes=tuple(themes), image_providers=images)

    def phone_queue_items(self):
        return PhoneQueue(self.ws).items()

    def _doctor(self):
        from .doctor import Doctor
        return Doctor(self).report()

    def _briefing(self, with_doctor=True):
        from .briefing import Briefing
        summary = self._doctor()['summary'] if with_doctor else None
        return Briefing(self).build(summary)

    def _briefing_city(self, city):
        from .briefing import Briefing
        return Briefing(self).set_city(city)

    def _push_test(self):
        return self.push.notify('JARVIS', 'Проверка уведомлений: всё работает.', '/')

    def _phone_queue(self):
        items = PhoneQueue(self.ws).items()
        return {'status': 'PASS', 'pending': [i for i in items if i['status'] == 'pending'],
                'note': 'Requests are data typed on the phone; run real tasks yourself with normal approvals.'}

    def _phone_queue_close(self, item_id, outcome):
        result = PhoneQueue(self.ws).close(item_id, outcome)
        self.ws.journal.record('phone-queue-close', 'PASS', item=item_id, outcome=outcome, rollback=[])
        return {'status': 'PASS', **result}

    def _audit(self, limit=20):
        entries = self.ws.journal.entries()[-limit:]
        return {'status': 'PASS', 'entries': entries, 'journal': str(self.ws.journal.path)}

    def _rollback(self, entry_id):
        if self.ws.journal.rolled_back(entry_id):
            raise Denied('Entry already rolled back')
        entry = self.ws.journal.find(entry_id)
        if entry.get('status') not in ('PASS', 'FAILED_OR_PARTIAL', 'VERIFY_FAILED'):
            raise Denied('Entry is not reversible')
        prepared = plan_rollback(self.ws, entry)
        approved = self.session.confirm({'task': 'rollback', 'target': entry_id, 'of': entry['task'],
                                         'steps': entry['rollback']})
        done = execute_rollback(self.ws, entry_id, prepared)
        self.ws.journal.record('rollback', 'PASS', target=entry_id, done=done, **approved)
        return {'status': 'PASS', 'target': entry_id, 'done': done}
