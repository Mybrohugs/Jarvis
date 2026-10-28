"""PC music control (no real player): phrases, allowlist, speech."""
import tempfile
import unittest
from developer_core.jarvis_local.intent import understand
from developer_core.jarvis_local.media_pc import MediaPC, app_name
from developer_core.jarvis_local.phone_executor import ALLOWED, validate
from developer_core.jarvis_local.router import UnifiedTaskRouter
from developer_core.jarvis_local.session import Denied, TrustedLocalSession


class FakePlayer:
    def __init__(self, status=None, ok=True):
        self.st, self.ok, self.calls = status, ok, []

    def status(self):
        return self.st

    def control(self, action):
        self.calls.append(action)
        return self.ok


class MusicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.router = UnifiedTaskRouter(TrustedLocalSession.phone_allowlist(ALLOWED), self.tmp.name)
        self.player = FakePlayer({'app': 'Spotify.exe', 'title': 'Back in Black', 'artist': 'AC/DC', 'playing': True})
        self.router._music_pc = MediaPC(self.player)

    def test_phrases(self):
        cases = {'пауза': 'pause', 'Джарвис, поставь на паузу': 'pause', 'останови музыку': 'pause',
                 'продолжи': 'play', 'сними с паузы': 'play', 'следующий трек': 'next', 'пропусти': 'next',
                 'предыдущая песня': 'previous', 'верни трек': 'previous'}
        for phrase, action in cases.items():
            plan = understand(phrase)
            self.assertEqual(plan['steps'], [{'task': 'music', 'args': {'action': action}}], phrase)
        self.assertEqual(understand('что сейчас играет?')['steps'][0]['task'], 'music-status')
        self.assertNotEqual(understand('следующий канал на телевизоре')['steps'][:1],
                            [{'task': 'music', 'args': {'action': 'next'}}])

    def test_control_and_status(self):
        task, args = validate({'task': 'music', 'args': {'action': 'next'}})
        self.assertEqual(self.router.dispatch(task, **args)['speech'], 'Следующий трек.')
        self.assertEqual(self.player.calls, ['next'])
        self.assertEqual(self.router.dispatch('music-status')['speech'], 'Играет в Spotify: AC/DC — Back in Black.')
        with self.assertRaises(Denied):
            validate({'task': 'music', 'args': {'action': 'format'}})

    def test_nothing_playing_and_player_refusal(self):
        self.router._music_pc = MediaPC(FakePlayer(None, ok=False))
        self.assertIn('ничего не играет', self.router.dispatch('music-status')['speech'])
        self.assertEqual(self.router.dispatch('music', action='pause')['status'], 'WARN')
        self.assertEqual(app_name('308046B0AF4A39CB.YandexMusic'), 'Яндекс Музыка')


if __name__ == '__main__':
    unittest.main()
