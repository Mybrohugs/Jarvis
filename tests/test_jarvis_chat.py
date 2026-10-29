"""Small talk gets a fixed polite reply (never a web search); two different questions are not a 'repeat'."""
import datetime
import tempfile
import unittest
from developer_core.jarvis_local.intent import understand
from developer_core.jarvis_local.phone_executor import ALLOWED, validate
from developer_core.jarvis_local.profile import Profile
from developer_core.jarvis_local.router import UnifiedTaskRouter
from developer_core.jarvis_local.session import TrustedLocalSession
from developer_core.jarvis_local.workspace import Workspace


class ChatTests(unittest.TestCase):
    def test_small_talk_is_not_a_web_search(self):
        for phrase, kind in (('Привет, Жарис. Как дела?', 'how'), ('Здравствуй, Джарвис, как ты?', 'how'), ('привет', 'greet'),
                             ('Спасибо!', 'thanks'), ('пока', 'bye')):
            self.assertEqual(understand(phrase)['steps'], [{'task': 'chat-reply', 'args': {'kind': kind}}], phrase)
        self.assertEqual(understand('как устроен квантовый компьютер')['steps'][0]['task'], 'web-answer')
        self.assertEqual(understand('привет, включи свет')['steps'][0]['task'], 'home-switch')

    def test_phone_can_run_it_and_gets_a_reply(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        router = UnifiedTaskRouter(TrustedLocalSession.phone_allowlist(ALLOWED), tmp.name)
        task, args = validate(understand('привет, как дела?')['steps'][0])
        self.assertIn('сэр', router.dispatch(task, **args)['speech'])

    def test_two_different_questions_are_not_frustration(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        clock = [datetime.datetime(2026, 10, 28, 21, 0)]
        profile = Profile(Workspace(tmp.name), now=lambda: clock[0])
        profile.observe('как устроен квантовый компьютер', ['web-answer'])
        clock[0] += datetime.timedelta(seconds=10)
        self.assertFalse(profile.observe('что такое чёрная дыра', ['web-answer'])['frustrated'])
        clock[0] += datetime.timedelta(seconds=10)
        self.assertTrue(profile.observe('что такое чёрная дыра?', ['web-answer'])['frustrated'])  # the same again


if __name__ == '__main__':
    unittest.main()
