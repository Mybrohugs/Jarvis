"""JARVIS remembers the user and adapts (explicit memory, sensitive filter, behaviour learning)."""
import datetime
from pathlib import Path
import tempfile
import time
import unittest
from developer_core.jarvis_local.intent import understand
from developer_core.jarvis_local.phone_executor import PhoneExecutor
from developer_core.jarvis_local.profile import Profile
from developer_core.jarvis_local.pwa_gateway import PhoneQueue
from developer_core.jarvis_local.router import UnifiedTaskRouter
from developer_core.jarvis_local.session import TrustedLocalSession
from developer_core.jarvis_local.workspace import Workspace


class Clock:
    def __init__(self):
        self.t = datetime.datetime(2026, 10, 30, 20, 5).astimezone()  # Friday 20:05

    def __call__(self):
        return self.t


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Workspace(Path(self.tmp.name))
        self.clock = Clock()
        self.p = Profile(self.ws, now=self.clock)

    def test_explicit_memory_and_show(self):
        self.p.remember('name', 'Роман')
        self.p.remember('hobby', 'игры на PS5')
        self.p.remember('like', 'шахматные партии')
        self.p.remember('dislike', 'громкую рекламу')
        shown = self.p.show()['speech']
        for piece in ('Вас зовут Роман', 'игры на PS5', 'шахматные партии', 'громкую рекламу'):
            self.assertIn(piece, shown)
        self.assertEqual(self.p.remember('hobby', 'игры на PS5')['speech'], 'Я это уже помню.')

    def test_sensitive_never_stored(self):
        for text in ('у меня диабет и я пью лекарства', 'я голосую за партию X', 'мой пароль 12345',
                     'карта 1234 5678 9012 3456'):
            self.assertEqual(self.p.remember('fact', text)['status'], 'REFUSED', text)
        self.assertEqual(self.p.load()['facts'], [])
        for ok in ('я геймер', 'проверю почту утром', 'шахматные партии по вечерам'):
            self.assertEqual(self.p.remember('fact', ok)['status'], 'PASS', ok)

    def test_like_replaces_dislike_and_forget(self):
        self.p.remember('dislike', 'джаз')
        self.p.remember('like', 'джаз')
        self.assertEqual([f['kind'] for f in self.p.load()['facts']], ['like'])
        self.assertEqual(self.p.forget('джаз')['speech'], 'Забыл (1).')
        self.p.remember('name', 'Роман')
        self.p.forget('всё')
        self.assertFalse(self.p.path.exists())

    def test_adapts_to_behaviour(self):
        self.p.remember('name', 'Роман')
        self.assertEqual(self.p.adapt('Готово. PlayStation на экране, громкость 25. Приятной игры, сэр.'),
                         'Готово. PlayStation на экране, громкость 25. Приятной игры, Роман.')
        for _ in range(12):  # very short commands -> short replies
            self.p.observe('громче', ['media-volume'])
        self.assertTrue(self.p.persona()['short'])
        self.assertNotIn('Приятной игры', self.p.adapt('Готово. Громкость 25. Приятной игры.'))
        for _ in range(3):  # same command repeated quickly + "опять" -> frustration
            self.p.observe('опять не то, громче', ['media-volume'])
        self.assertTrue(self.p.adapt('Громкость 30.').startswith('Понял, исправляюсь.'))

    def test_polite_user_gets_warmth(self):
        p = Profile(self.ws, now=self.clock)
        for i in range(8):
            self.clock.t += datetime.timedelta(minutes=5)
            p.observe('включи, пожалуйста, режим кино на вечер', ['media-scene:movie' if i % 2 else 'briefing'])
        self.assertTrue(p.adapt('Громкость 30.').endswith('Рад помочь.'))

    def test_habit_suggestion_once_per_day(self):
        for week in range(3):
            self.clock.t = datetime.datetime(2026, 10, 9 + 7 * week, 20, 10).astimezone()  # Fridays 20:xx
            self.p.observe('режим игры', ['media-scene:game'])
        self.clock.t = datetime.datetime(2026, 10, 30, 20, 1).astimezone()
        self.assertTrue(self.p.suggestion('media-scene:game'))
        self.assertFalse(self.p.suggestion('media-scene:game'))  # once per day
        self.assertIn('режим игры', self.p.show()['speech'])

    def test_phrases(self):
        cases = {'запомни, что я люблю кофе и шахматы': ('profile-remember', {'kind': 'like', 'text': 'кофе и шахматы'}),
                 'Меня зовут Роман': ('profile-remember', {'kind': 'name', 'text': 'Роман'}),
                 'я увлекаюсь фотографией': ('profile-remember', {'kind': 'hobby', 'text': 'фотографией'}),
                 'мне не нравится громкий звук': ('profile-remember', {'kind': 'dislike', 'text': 'громкий звук'}),
                 'что ты обо мне знаешь?': ('profile-show', {}),
                 'забудь всё обо мне': ('profile-forget', {'what': 'всё'}),
                 'отвечай короче': ('profile-style', {'style': 'short'})}
        for phrase, (task, args) in cases.items():
            plan = understand(phrase)
            self.assertEqual([(s['task'], s['args']) for s in plan['steps']], [(task, args)], phrase)
        self.assertEqual(understand('удали всё обо мне')['intent'], 'refused')

    def test_phone_free_speech_remembers(self):
        router = UnifiedTaskRouter(TrustedLocalSession.phone_allowlist(('profile-remember',)), self.ws.root)
        q = PhoneQueue(router.ws)
        item = q.add('я увлекаюсь горными велосипедами', interpret=True)
        PhoneExecutor(router.ws.root, router=router, clock=time.monotonic,
                      chat=lambda m: '{"intent":"unknown"}').tick()
        got = q.get(item['id'])
        self.assertEqual(got['status'], 'done')
        self.assertIn('горными велосипедами', got['speech'])


if __name__ == '__main__':
    unittest.main()
