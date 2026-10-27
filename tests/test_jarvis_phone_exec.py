"""Phone quick commands (user-approved boundary): only volume / mute, bounded, audited (mock TV)."""
from pathlib import Path
import tempfile
import threading
import unittest
from developer_core.jarvis_local.home_media import Bravia, MediaOperator
from developer_core.jarvis_local.phone_executor import PhoneExecutor
from developer_core.jarvis_local.pwa_gateway import PhoneQueue, phone_quick_command, station_reply
from developer_core.jarvis_local.router import UnifiedTaskRouter
from developer_core.jarvis_local.session import Denied, TrustedLocalSession
from tests.test_jarvis_media import FakeTV


class PhoneExecTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'docs').mkdir()
        (root / 'docs' / 'a.txt').write_text('a', encoding='utf-8')
        self.fake = FakeTV()
        self.router = UnifiedTaskRouter(TrustedLocalSession.phone_allowlist(('media-volume', 'media-mute')), root)
        self.router.media = MediaOperator(self.router.ws, Bravia(credentials=('192.168.1.2', 'x'), transport=self.fake),
                                          sleep=lambda s: None)
        self.t = [0.0]
        self.ex = PhoneExecutor(root, router=self.router, clock=lambda: self.t[0])
        self.q = PhoneQueue(self.router.ws)

    def test_quick_command_parser(self):
        self.assertEqual(phone_quick_command('Громче'), {'task': 'media-volume', 'args': {'delta': 3}})
        self.assertEqual(phone_quick_command('тише на 5'), {'task': 'media-volume', 'args': {'delta': -5}})
        self.assertEqual(phone_quick_command('громкость 25'), {'task': 'media-volume', 'args': {'level': 25}})
        self.assertEqual(phone_quick_command('без звука'), {'task': 'media-mute', 'args': {'on': True}})
        for other in ('скопируй docs/a.txt в b.txt', 'включи плейстейшн', 'выключи телевизор', 'громче и удали всё'):
            self.assertIsNone(phone_quick_command(other), other)

    def test_executes_allowlisted_and_audits(self):
        item = self.q.add('громче', auto=phone_quick_command('громче'))
        self.assertEqual(self.ex.tick(), 1)
        done = self.q.get(item['id'])
        self.assertEqual((done['status'], done['speech']), ('done', 'Громкость 33.'))
        entry = [e for e in self.router.ws.journal.entries() if e['task'] == 'media-volume'][-1]
        self.assertEqual((entry['status'], entry['approval_mode']), ('PASS', 'phone-allowlist'))

    def test_bounds(self):
        bad = self.q.add('громкость 55', auto={'task': 'media-volume', 'args': {'level': 55}})
        self.fake.s['volume'] = 38
        capped = self.q.add('громче на 9', auto={'task': 'media-volume', 'args': {'delta': 9}})
        self.ex.tick()
        self.assertEqual(self.q.get(bad['id'])['status'], 'failed')
        self.assertEqual(self.q.get(capped['id'])['status'], 'done')
        self.assertEqual(self.fake.s['volume'], 40)

    def test_forged_queue_item_cannot_escalate(self):
        forged = self.q.add('x', auto={'task': 'file-apply', 'args': {'entries': [], 'approval': 'x'}})
        self.ex.tick()
        self.assertEqual(self.q.get(forged['id'])['status'], 'failed')
        with self.assertRaises(Denied):  # the session itself refuses non-allowlisted approvals too
            plan = self.router.dispatch('file-plan', entries=[{'op': 'copy', 'src': 'docs/a.txt', 'dst': 'docs/b.txt'}])
            self.router.dispatch('file-apply', entries=[{'op': 'copy', 'src': 'docs/a.txt', 'dst': 'docs/b.txt'}],
                                 approval=plan['approval'])
        self.assertFalse((self.router.ws.root / 'docs' / 'b.txt').exists())
        with self.assertRaises(Denied):
            self.router.dispatch('media-ps5')

    def test_rate_limit(self):
        for _ in range(22):
            self.q.add('тише', auto={'task': 'media-volume', 'args': {'delta': -1}})
        self.ex.tick()
        statuses = [i['status'] for i in self.q.items()]
        self.assertEqual(statuses.count('done'), 20)
        self.assertEqual(statuses.count('failed'), 2)

    def test_free_speech_respects_cap_and_rate_limit(self):
        ex = PhoneExecutor(self.router.ws.root, router=self.router, clock=lambda: self.t[0],
                           chat=lambda m: '{"intent":"plan","steps":[{"task":"media-volume","args":{"delta":9}}]}')
        self.fake.s['volume'] = 38
        item = self.q.add('погромче бы, раз так девять', interpret=True)
        ex.tick()
        self.assertEqual(self.q.get(item['id'])['status'], 'done')
        self.assertEqual(self.fake.s['volume'], 40)  # ceiling also on the LLM path
        for _ in range(25):
            self.q.add('погромче бы, раз так девять', interpret=True)
        ex.tick()
        failed = [i for i in self.q.items() if i['status'] == 'failed']
        self.assertGreaterEqual(len(failed), 5)  # shared 20/min limit

    def test_gateway_reply_with_running_executor(self):
        stop = threading.Event()

        def loop():
            while not stop.is_set():
                self.ex.tick()
                stop.wait(0.05)
        th = threading.Thread(target=loop, daemon=True)
        th.start()
        self.addCleanup(stop.set)
        reply = station_reply('без звука', self.router.ws, self.q, wait=3.0)
        self.assertEqual((reply['intent'], reply['speech']), ('quick', 'Звук выключен.'))
        self.assertTrue(self.fake.s['mute'])

    def test_gateway_reply_without_executor_does_not_execute(self):
        reply = station_reply('громче', self.router.ws, self.q, wait=0.5)
        self.assertTrue(reply['queued'])
        self.assertEqual(self.fake.s['volume'], 30)


if __name__ == '__main__':
    unittest.main()
