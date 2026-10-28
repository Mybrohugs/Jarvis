"""Features 7, 9, 10 (mock Everything / clipboard / screen) + one-time outbox + phone allowlist."""
from pathlib import Path
import tempfile
import time
import unittest
from developer_core.jarvis_local import pc_tools
from developer_core.jarvis_local.outbox import sweep, take_outbox
from developer_core.jarvis_local.pc_tools import PCTools
from developer_core.jarvis_local.phone_executor import PhoneExecutor
from developer_core.jarvis_local.pwa_gateway import PhoneQueue, phone_quick_command
from developer_core.jarvis_local.router import UnifiedTaskRouter
from developer_core.jarvis_local.session import Denied, TrustedLocalSession
from developer_core.jarvis_local.workspace import Workspace


class Clip:
    def __init__(self):
        self.value = 'текст с компьютера'

    def get(self):
        return self.value

    def set(self, text):
        self.value = text


class PCToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        (base / 'ws').mkdir()
        self.ws = Workspace(base / 'ws')
        docs = base / 'Documents'
        docs.mkdir()
        self.report = docs / 'Отчёт октябрь.docx'
        self.report.write_bytes(b'PK report')
        self.secret = base / 'id_rsa.pem'
        self.secret.write_text('KEY')
        listing = '\n'.join([str(self.report), str(self.secret), r'C:\Users\x\.ssh\config'])

        class Run:
            returncode, stdout = 0, listing

        self.calls = []
        self.orig_es = pc_tools.es_path
        pc_tools.es_path = lambda: Path('es.exe')
        self.addCleanup(lambda: setattr(pc_tools, 'es_path', self.orig_es))
        self.clip = Clip()
        from PIL import Image
        self.pc = PCTools(self.ws, run=lambda *a, **k: self.calls.append(a) or Run(), clipboard=self.clip,
                          grab=lambda: Image.new('RGB', (64, 32), (10, 120, 200)))

    def test_find_hides_sensitive_and_send_is_one_time(self):
        found = self.pc.find('отчёт')
        self.assertEqual(found['files'], [str(self.report)])
        self.assertIn('1 — Отчёт октябрь.docx', found['speech'])
        sent = self.pc.send(1)
        token = sent['link'].rsplit('/', 1)[1]
        self.assertEqual(take_outbox(self.ws.state, token), ('Отчёт_октябрь.docx', b'PK report'))
        self.assertIsNone(take_outbox(self.ws.state, token))  # single use
        with self.assertRaises(Denied):
            self.pc.send(2)

    def test_bad_queries_and_tokens(self):
        for q in ('a', 'x" | del', ''):
            with self.assertRaises(Denied):
                self.pc.find(q)
        self.assertIsNone(take_outbox(self.ws.state, '../../etc'))

    def test_expired_link(self):
        self.pc.find('отчёт')
        token = self.pc.send(1)['link'].rsplit('/', 1)[1]
        meta = self.ws.state / 'outbox' / token / 'meta.json'
        meta.write_text(meta.read_text(encoding='utf-8').replace('"expires": ', '"expires": -'), encoding='utf-8')
        sweep(self.ws.state)
        self.assertIsNone(take_outbox(self.ws.state, token))

    def test_clipboard_and_screen(self):
        self.assertEqual(self.pc.clip_get()['text'], 'текст с компьютера')
        self.pc.clip_set('с телефона')
        self.assertEqual(self.clip.value, 'с телефона')
        shot = self.pc.screen()
        name, data = take_outbox(self.ws.state, shot['link'].rsplit('/', 1)[1])
        self.assertTrue(name.endswith('.png') and data.startswith(b'\x89PNG'))

    def test_phone_phrases(self):
        self.assertEqual(phone_quick_command('найди на компьютере отчёт октябрь'),
                         {'task': 'pc-find', 'args': {'query': 'отчёт октябрь'}})
        self.assertEqual(phone_quick_command('скинь 2'), {'task': 'pc-send', 'args': {'index': 2}})
        self.assertEqual(phone_quick_command('что в буфере'), {'task': 'pc-clip-get', 'args': {}})
        self.assertEqual(phone_quick_command('вставь в буфер «Привет, мир»'),
                         {'task': 'pc-clip-set', 'args': {'text': 'Привет, мир'}})
        self.assertEqual(phone_quick_command('что на экране?'), {'task': 'screen-describe', 'args': {}})
        self.assertEqual(phone_quick_command('сделай скриншот'), {'task': 'pc-screen', 'args': {}})

    def test_phone_executor_runs_clipboard_and_read_only_cli_cannot(self):
        router = UnifiedTaskRouter(TrustedLocalSession.phone_allowlist(('pc-clip-get',)), self.ws.root)
        router.pc = self.pc
        q = PhoneQueue(router.ws)
        item = q.add('что в буфере', auto={'task': 'pc-clip-get', 'args': {}})
        PhoneExecutor(router.ws.root, router=router, clock=time.monotonic).tick()
        got = q.get(item['id'])
        self.assertEqual((got['status'], got['text']), ('done', 'текст с компьютера'))
        ro = UnifiedTaskRouter(TrustedLocalSession.read_only(), self.ws.root)
        ro.pc = self.pc
        with self.assertRaises(Denied):
            ro.dispatch('pc-screen')


if __name__ == '__main__':
    unittest.main()
