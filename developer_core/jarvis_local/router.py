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
    'reminder-add': 'write', 'reminder-list': 'read', 'reminder-cancel': 'write',  # speak/push only, never execute
    'music': 'write', 'music-status': 'read',  # any PC player via Windows media controls
    'home-devices': 'read', 'home-switch': 'write', 'home-brightness': 'write', 'home-scenario': 'write',  # Yandex
    'home-routine': 'write',  # fixed routines: lights/sockets via Yandex, TV off, speaker via a Yandex scenario
    'screen-describe': 'write',  # local vision model reads the screen; approval like other pc-* tasks
    'web-answer': 'write',  # the question leaves the PC (own SearXNG -> search engines); answer is text only
    'app-open': 'write', 'file-open': 'write', 'desktop-show': 'write', 'windows-list': 'read',  # allowlisted apps
    # Explorer: the owner's real folders (Downloads, Documents, ...); writes never overwrite and roll back
    'explorer-list': 'read', 'explorer-duplicates': 'read', 'explorer-mkdir': 'write', 'explorer-sort': 'write',
    'explorer-copy': 'write', 'explorer-move': 'write',
    'chat-reply': 'read',  # greetings / thanks: a fixed polite reply, nothing runs or leaves the PC
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

    SCENE_LIGHTS = {'movie': 20, 'game': 40, 'night': 0}  # brightness %, 0 = all lights off

    def _media_scene(self, name):
        result = self.media.scene(name, self.session)
        level = self.SCENE_LIGHTS.get(name)
        try:
            from .yandex_iot import keys as yandex_keys
            if level is None or not (hasattr(self, '_yandex') or yandex_keys().configured()):
                return result
            lights = [d for d in self.yandex.info()['devices'] if d['kind'] == 'light']
            if not lights:
                return result
            done, failed = self.yandex.act(lights, on=False) if level == 0 else                 self.yandex.act(lights, on=True, brightness=level)
            self.ws.journal.record('scene-lights', 'PASS' if done and not failed else 'WARN', scene=name, level=level,
                                   devices=done, failed=failed, rollback=[])
            if done:
                result['speech'] = result['speech'].rstrip() + (' Свет выключен.' if level == 0 else
                                                                ' Свет приглушён до %d%%.' % level)
        except Exception as exc:  # lights are a bonus: the TV part of the scene already happened
            self.ws.journal.record('scene-lights', 'FAILED', scene=name, reason=type(exc).__name__, rollback=[])
        return result

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

    def _screen_describe(self, question=''):
        def run(pc):
            from PIL import ImageGrab
            from .screen_vision import describe, png_of
            grab = pc.grab or (lambda: ImageGrab.grab(all_screens=False))
            png = png_of(grab())
            text = getattr(self, 'vision', describe)(png, question)
            link = pc._publish(data=png, name='screen-%s.png' % __import__('datetime').datetime.now().strftime('%Y%m%d-%H%M%S'))
            self.ws.journal.record('screen-describe', 'PASS', chars=len(text), rollback=[])
            return {'status': 'PASS', 'speech': text, 'text': text, 'link': link}
        return self._pc('screen-describe', run)

    @property
    def apps(self):
        if not hasattr(self, '_apps'):
            from .apps import Apps
            self._apps = Apps(self.ws)
        return self._apps

    def _app_open(self, target):
        from .apps import resolve
        kind, value, _ = resolve(target)  # refuse before asking for approval
        self.session.confirm({'task': 'app-open', 'kind': kind, 'target': target[:40]})
        return self.apps.open(target)

    def _file_open(self, index):
        self.session.confirm({'task': 'file-open', 'index': index})
        return self.apps.open_found(index)

    def _desktop_show(self):
        self.session.confirm({'task': 'desktop-show'})
        return self.apps.show_desktop()

    def _windows_list(self):
        return self.apps.windows()

    CHAT = {'greet': 'Здравствуйте, сэр. Чем могу помочь?',
            'how': 'Всё в порядке, сэр, системы работают. Чем займёмся?',
            'thanks': 'Всегда пожалуйста, сэр.',
            'bye': 'До связи, сэр.'}

    def _chat_reply(self, kind):
        if kind not in self.CHAT:
            raise Denied('Unknown chat reply')
        return {'status': 'PASS', 'speech': self.CHAT[kind]}

    @property
    def explorer(self):
        if not hasattr(self, '_explorer'):
            from .explorer import Explorer
            self._explorer = Explorer(self.ws)
        return self._explorer

    def _explorer_list(self, folder):
        return self.explorer.listing(folder)

    def _explorer_duplicates(self, folder):
        return self.explorer.duplicates(folder)

    def _explorer_mkdir(self, folder, name):
        return self.explorer.mkdir(folder, name, self.session)

    def _explorer_sort(self, folder):
        return self.explorer.sort_apply(folder, self.session)

    def _explorer_copy(self, src, name, dst):
        return self.explorer.transfer('copy', src, name, dst, self.session)

    def _explorer_move(self, src, name, dst):
        return self.explorer.transfer('move', src, name, dst, self.session)

    def _web_answer(self, question):
        self.session.confirm({'task': 'web-answer', 'question': question[:200],
                              'sent_to': 'SearXNG -> search engines; snippets -> Claude Haiku (if key set, ~$0.001)'})
        answer = getattr(self, 'web', None)
        if answer is None:
            from .web_answer import answer
        result = answer(question)
        self.ws.journal.record('web-answer', 'PASS', chars=len(question), sources=[s['url'] for s in result['sources']],
                               rollback=[])
        return result

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

    @property
    def reminders(self):
        from .reminders import Reminders
        return Reminders(self.ws.state)

    def _reminder_add(self, phrase):
        from .reminders import parse, when_words
        parsed = parse(phrase)
        if parsed is None:
            raise Denied('Не понял время напоминания: скажите «через 20 минут …» или «в 18:30 …»')
        self.session.confirm({'task': 'reminder-add', 'text': parsed['text'], 'due': parsed['due'].isoformat(),
                              'repeat': parsed['repeat']})
        item = self.reminders.add(parsed)
        self.ws.journal.record('reminder-add', 'PASS', reminder=item['id'], due=item['due'], repeat=item['repeat'],
                               rollback=[])
        every = 'каждый день, начиная ' if item['repeat'] == 'daily' else 'каждую неделю, начиная ' if item['repeat'] else ''
        return {'status': 'PASS', 'reminder': item,
                'speech': 'Напомню %s%s: %s.' % (every, when_words(parsed['due']), item['text'])}

    def _reminder_list(self):
        import datetime
        from .reminders import when_words
        items = self.reminders.items()
        if not items:
            return {'status': 'PASS', 'items': [], 'speech': 'Напоминаний нет.'}
        parts = ['%s — %s' % (when_words(datetime.datetime.fromisoformat(i['due'])), i['text']) for i in items[:5]]
        return {'status': 'PASS', 'items': items,
                'speech': 'Напоминаний: %d. %s.' % (len(items), '; '.join(parts))}

    def _reminder_cancel(self, what=''):
        self.session.confirm({'task': 'reminder-cancel', 'what': what})
        n = self.reminders.cancel(what)
        self.ws.journal.record('reminder-cancel', 'PASS', cancelled=n, rollback=[])
        return {'status': 'PASS', 'cancelled': n,
                'speech': ('Отменил напоминаний: %d.' % n) if n else 'Таких напоминаний нет.'}

    @property
    def music_pc(self):
        if not hasattr(self, '_music_pc'):
            from .media_pc import MediaPC
            self._music_pc = MediaPC()
        return self._music_pc

    def _music(self, action):
        self.session.confirm({'task': 'music', 'action': action})
        result = self.music_pc.control(action)
        self.ws.journal.record('music', result['status'], action=action, rollback=[])
        return result

    def _music_status(self):
        return self.music_pc.now_playing()

    @property
    def yandex(self):
        if not hasattr(self, '_yandex'):
            from .yandex_iot import YandexHome
            self._yandex = YandexHome()
        return self._yandex

    def _home_devices(self):
        info = self.yandex.info()
        lit = [d['name'] for d in info['devices'] if d['kind'] and d['on']]
        names = ', '.join('%s%s' % (d['name'], ' (вкл)' if d['on'] else '') for d in info['devices'] if d['kind'])
        return {'status': 'PASS', **info,
                'speech': ('Устройства: %s.' % names if names else 'Света и розеток в умном доме не нашёл.') +
                          (' Сейчас включено: %s.' % ', '.join(lit) if lit else '')}

    def _home_act(self, task, phrase, **change):
        info = self.yandex.info()
        devices = self.yandex.pick(phrase, info['devices'])
        if not devices:
            raise Denied('Не понял, какое устройство: назовите его как в приложении «Дом с Алисой»')
        self.session.confirm({'task': task, 'devices': [d['name'] for d in devices], **change})
        done, failed = self.yandex.act(devices, **change)
        self.ws.journal.record(task, 'PASS' if done and not failed else 'WARN', devices=done, failed=failed,
                               rollback=[], **change)
        if 'brightness' in change:
            speech = 'Яркость %d%%: %s.' % (change['brightness'], ', '.join(done)) if done else ''
        else:
            speech = '%s: %s.' % ('Включил' if change.get('on') else 'Выключил', ', '.join(done)) if done else ''
        if failed:
            speech += ' Не ответили: %s.' % ', '.join(failed)
        return {'status': 'PASS' if done and not failed else 'WARN', 'done': done, 'failed': failed,
                'speech': speech.strip() or 'Устройства не подтвердили команду.'}

    def _home_switch(self, phrase, on):
        return self._home_act('home-switch', phrase, on=bool(on))

    def _home_brightness(self, phrase, level):
        if type(level) is not int or not 1 <= level <= 100:
            raise Denied('Яркость 1..100')
        return self._home_act('home-brightness', phrase, on=True, brightness=level)

    def _home_scenario(self, name):
        scenarios = self.yandex.info()['scenarios']
        hit = [s for s in scenarios if s['name'].lower() == name.lower()] or \
              [s for s in scenarios if name.lower() in s['name'].lower()]
        if len(hit) != 1:
            raise Denied('Сценарий не найден. Есть: ' + ', '.join(s['name'] for s in scenarios)[:200])
        self.session.confirm({'task': 'home-scenario', 'scenario': hit[0]['name']})
        self.yandex.scenario(hit[0]['id'])
        self.ws.journal.record('home-scenario', 'PASS', scenario=hit[0]['name'], rollback=[])
        return {'status': 'PASS', 'speech': 'Сценарий «%s» запущен.' % hit[0]['name']}

    # name -> (Yandex scenario with the speaker part, what the lights do, speaker part for the reply)
    ROUTINES = {
        'home': ('Я дома', {'on': True}, 'включила музыку'),
        'away': ('Я ушёл', None, ''),  # everything off + TV
        'morning': ('Доброе утро', {'on': True, 'brightness': 30}, 'поставила громкость 4 и ваш плейлист'),
        'evening': ('Вечер добрый', {'on': True, 'brightness': 60, 'temperature': 2700}, ''),
    }

    def _home_routine(self, name):
        if name not in self.ROUTINES:
            raise Denied('Unknown routine')
        title, lights, speaker = self.ROUTINES[name]
        info = self.yandex.info()
        if lights is None:
            targets, change = [d for d in info['devices'] if d['kind']], {'on': False}
        else:  # the dimmable bulbs; plain switches only when there is no bulb at all
            lamps = [d for d in info['devices'] if d['kind'] == 'light']
            targets, change = [d for d in lamps if d['brightness']] or lamps, lights
        self.session.confirm({'task': 'home-routine', 'routine': name, 'devices': [d['name'] for d in targets]})
        done, failed = self.yandex.act(targets, **change) if targets else ([], [])
        norm = lambda s: s.lower().replace('ё', 'е')
        hit = [s for s in info['scenarios'] if norm(s['name']) == norm(title)]
        if hit:
            self.yandex.scenario(hit[0]['id'])
        tv = False
        if name == 'away':
            try:
                self._media_power(False)
                tv = True
            except Exception:  # no TV configured or it is already off: the house part already happened
                pass
        self.ws.journal.record('home-routine', 'PASS' if not failed else 'WARN', routine=name, devices=done,
                               failed=failed, scenario=bool(hit), tv_off=tv, rollback=[])
        if lights is None:
            speech = 'Выключил: %s.' % ', '.join(done + (['Телевизор'] if tv else [])) if done or tv else ''
        else:
            speech = '%s: %s.' % ('Свет %d%%' % lights['brightness'] if 'brightness' in lights else 'Включил',
                                   ', '.join(done)) if done else ''
            if lights.get('temperature') and done:
                speech += ' Тёплый оттенок.'
        if failed:
            speech += ' Не ответили: %s.' % ', '.join(failed)
        if hit:
            speech += ' Сценарий «%s» запущен.' % hit[0]['name']
        elif speaker:
            speech += ' Чтобы колонка %s, создайте сценарий «%s» в «Доме с Алисой».' % (speaker, title)
        return {'status': 'PASS' if not failed else 'WARN', 'done': done, 'failed': failed, 'scenario': bool(hit),
                'speech': speech.strip() or 'Нечего было переключать.'}

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
        outside = str(entry.get('task', '')).startswith('explorer-')  # real folders, not the workspace
        prepared = self.explorer.plan_rollback(entry) if outside else plan_rollback(self.ws, entry)
        approved = self.session.confirm({'task': 'rollback', 'target': entry_id, 'of': entry['task'],
                                         'steps': entry['rollback'][:20]})
        done = self.explorer.execute_rollback(entry_id, prepared) if outside else \
            execute_rollback(self.ws, entry_id, prepared)
        self.ws.journal.record('rollback', 'PASS', target=entry_id, done=done, **approved)
        return {'status': 'PASS', 'target': entry_id, 'done': done}
