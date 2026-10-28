"""Allowlisted real programs (no real launches): phrases, allowlist, verification, safe file types."""
import json
from pathlib import Path
import tempfile
import unittest
from developer_core.jarvis_local.apps import Apps, resolve
from developer_core.jarvis_local.intent import understand
from developer_core.jarvis_local.phone_executor import ALLOWED, validate
from developer_core.jarvis_local.router import UnifiedTaskRouter
from developer_core.jarvis_local.session import Denied, TrustedLocalSession


class AppsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.router = UnifiedTaskRouter(TrustedLocalSession.phone_allowlist(ALLOWED), self.tmp.name)
        self.started, self.windows, self.keys = [], ['Visual Studio Code'], []

        def start(target):
            self.started.append(target)
            self.windows.append({'excel': 'Книга1 - Excel', 'winword': 'Документ1 - Word'}.get(target, 'Загрузки')
                                if not str(target).endswith('.docx') else 'отчёт.docx - Word')
        self.router._apps = Apps(self.router.ws, start=start, titles=lambda: list(self.windows), sleep=lambda s: None,
                                 keys=self.keys.append)

    def run_phrase(self, phrase):
        task, args = validate(understand(phrase)['steps'][0])
        return self.router.dispatch(task, **args)

    def test_phrases_only_for_allowlisted_targets(self):
        self.assertEqual(understand('открой эксель')['steps'][0], {'task': 'app-open', 'args': {'target': 'эксель'}})
        self.assertEqual(understand('Джарвис, открой папку загрузки')['steps'][0]['args']['target'], 'загрузки')
        self.assertEqual(understand('открой второй файл')['steps'][0], {'task': 'file-open', 'args': {'index': 2}})
        self.assertEqual(understand('открой файл номер 3')['steps'][0]['args']['index'], 3)
        self.assertEqual(understand('сверни все окна')['steps'][0]['task'], 'desktop-show')
        self.assertEqual(understand('открой блокнот')['steps'][0]['task'], 'notepad-open')  # JARVIS tab, as before
        self.assertNotEqual(understand('включи свет')['steps'][0]['task'], 'app-open')
        with self.assertRaises(Denied):
            resolve('regedit')

    def test_open_app_is_verified_and_journaled(self):
        out = self.run_phrase('открой эксель')
        self.assertEqual((out['verified'], out['speech']), (True, 'Открыл эксель.'))
        self.assertEqual(self.started, ['excel'])
        self.assertEqual(self.router.ws.journal.entries()[-1]['task'], 'app-open')
        self.assertTrue(self.run_phrase('открой папку загрузки')['verified'])

    def test_site_and_unverified_launch(self):
        self.assertIn('в браузере', self.run_phrase('открой ютуб')['speech'])
        self.assertEqual(self.started[-1], 'https://www.youtube.com')
        self.router._apps.titles = lambda: []  # nothing appears
        self.assertEqual(self.run_phrase('открой ворд')['status'], 'WARN')

    def test_found_files_safe_types_only(self):
        folder = Path(self.tmp.name)
        doc, bad = folder / 'отчёт.docx', folder / 'macro.xlsm'
        doc.write_bytes(b'x')
        bad.write_bytes(b'x')
        (self.router.ws.state / 'pc_last_search.json').write_text(json.dumps([str(doc), str(bad)]), encoding='utf-8')
        self.assertEqual(self.run_phrase('открой первый файл')['speech'], 'Открыл отчёт.docx.')
        with self.assertRaises(Denied):
            self.run_phrase('открой второй файл')

    def test_desktop_and_window_list(self):
        self.assertEqual(self.run_phrase('сверни все окна')['speech'], 'Свернул все окна.')
        self.assertEqual(self.keys, ['windows+d'])
        self.windows.append('Telegram')
        self.assertIn('Telegram', self.run_phrase('какие программы открыты')['speech'])


if __name__ == '__main__':
    unittest.main()
