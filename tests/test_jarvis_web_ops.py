"""Browser operator with a fake browser, fake search and fake downloads (no network, no real folders)."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from developer_core.jarvis_local.explorer import Explorer
from developer_core.jarvis_local.intent import understand
from developer_core.jarvis_local.phone_executor import validate
from developer_core.jarvis_local.plan_runner import run_plan
from developer_core.jarvis_local.router import UnifiedTaskRouter
from developer_core.jarvis_local.session import Denied, TrustedLocalSession
from developer_core.jarvis_local.web_ops import WebOps, clean_url

RESULTS = [{'title': 'Борщ классический', 'url': 'https://eda.example.ru/borsch', 'snippet': 'свёкла'},
           {'title': 'Борщ с пампушками', 'url': 'https://povar.example.ru/b', 'snippet': 'чеснок'}]


class FakeResponse(io.BytesIO):
    def __init__(self, data, url, headers):
        super().__init__(data)
        self.url, self.headers = url, headers

    def geturl(self):
        return self.url


class WebOpsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.dl, self.docs, ws = base / 'Downloads', base / 'Documents', base / 'ws'
        for d in (self.dl, self.docs, ws):
            d.mkdir()
        self.approve, self.asked, self.opened, self.windows = True, [], [], ['Проводник']

        def confirmer(summary, challenge):
            self.asked.append(summary)
            return self.approve
        self.router = UnifiedTaskRouter(TrustedLocalSession.for_tests(confirmer), ws)
        self.router._explorer = Explorer(self.router.ws, roots={'Downloads': self.dl, 'Documents': self.docs})
        self.response = None

        def start(url):
            self.opened.append(url)
            self.windows = self.windows + ['Новая вкладка - Google Chrome']
        self.router._web_ops = WebOps(self.router.ws, start=start, titles=lambda: self.windows, sleep=lambda s: None,
                                      search=lambda q: list(RESULTS), fetch=lambda url: self.response,
                                      explorer=self.router._explorer)
        self.router.web = lambda q: {'status': 'PASS', 'speech': 'Квазар — ядро галактики.', 'sources': [],
                                     'text': 'Квазар — ядро галактики.\n\nИсточники:\nhttps://ru.wikipedia.org/x'}

    def run_phrase(self, phrase):
        return run_plan(self.router, understand(phrase))

    def test_search_then_open_the_chosen_link(self):
        out = self.run_phrase('найди в интернете рецепт борща и открой вторую ссылку')
        self.assertEqual(out['status'], 'PASS', out)
        self.assertEqual(self.opened, ['https://povar.example.ru/b'])
        self.assertTrue(out['completed'][1]['result']['verified'])
        self.assertIn('1: Борщ классический', out['completed'][0]['result']['speech'])
        again = self.run_phrase('открой первую ссылку')  # follow-up uses the remembered results
        self.assertEqual(again['status'], 'PASS')
        self.assertEqual(self.opened[-1], 'https://eda.example.ru/borsch')
        with self.assertRaises(Denied):
            self.router.dispatch('web-open-result', index=5)

    def test_open_without_search_explains(self):
        out = self.run_phrase('открой первую ссылку')
        self.assertEqual(out['status'], 'STOPPED')
        self.assertIn('Сначала найдите', out['reason'])

    def test_open_site_and_site_search(self):
        self.assertEqual(self.run_phrase('открой сайт python.org')['status'], 'PASS')
        self.assertEqual(self.opened[-1], 'https://python.org')
        self.run_phrase('включи на ютубе Queen Bohemian Rhapsody')
        self.assertEqual(self.opened[-1], 'https://www.youtube.com/results?search_query=Queen+Bohemian+Rhapsody')
        out = self.run_phrase('покажи на карте Эрмитаж')
        self.assertIn('yandex.ru/maps/?text=%D0%AD', self.opened[-1])
        self.assertIn('Яндекс Карты', out['completed'][0]['result']['speech'])

    def test_no_browser_window_is_reported(self):
        self.router._web_ops.start = lambda url: None  # nothing happens on screen
        out = self.router.dispatch('web-open', url='https://python.org')
        self.assertEqual(out['status'], 'WARN')
        self.assertFalse(out['verified'])

    def test_clean_url_refuses_local_and_odd_addresses(self):
        for bad in ('http://localhost:10790/', 'https://192.168.1.5/', 'https://my-pc.example-tailnet.ts.net',
                    'https://user:pw@example.com/', 'file:///C:/Windows', 'javascript:alert(1)', 'https://127.0.0.1',
                    'https://exa mple.com'):
            with self.assertRaises(Denied, msg=bad):
                clean_url(bad)
        self.assertEqual(clean_url('habr.com/ru'), 'https://habr.com/ru')
        self.assertEqual(self.opened, [])

    def test_download_verifies_marks_and_rolls_back(self):
        data = b'%PDF-1.7 hello' * 1000
        self.response = FakeResponse(data, 'https://files.example.org/docs/manual.pdf',
                                     {'Content-Type': 'application/pdf', 'Content-Length': str(len(data))})
        out = self.run_phrase('скачай https://files.example.org/docs/manual.pdf')
        self.assertEqual(out['status'], 'PASS', out)
        f = self.dl / 'manual.pdf'
        self.assertEqual(f.read_bytes(), data)
        self.assertEqual(self.asked[-1]['task'], 'web-download')
        self.assertEqual(out['undo_available'], [])  # undo goes through "отмени последнее действие"
        self.response = FakeResponse(data, 'https://files.example.org/docs/manual.pdf', {'Content-Type': 'application/pdf'})
        second = self.router.dispatch('web-download', url='https://files.example.org/docs/manual.pdf')
        self.assertTrue(second['path'].endswith('manual (2).pdf'))  # never overwrites
        undo = self.run_phrase('отмени последнее действие')
        self.assertEqual(undo['status'], 'PASS', undo)
        self.assertFalse((self.dl / 'manual (2).pdf').exists())
        self.assertTrue(f.exists())
        q = list((self.router.ws.state / 'quarantine').rglob('manual (2).pdf'))
        self.assertEqual(len(q), 1)  # moved aside, not deleted
        self.assertFalse(list(self.dl.glob('*.jarvis-part')))

    def test_download_refusals(self):
        with self.assertRaises(Denied):
            self.router.dispatch('web-download', url='https://example.com/setup.exe')
        with self.assertRaises(Denied):
            self.router.dispatch('web-download', url='http://example.com/a.pdf')
        self.response = FakeResponse(b'<html>', 'https://example.com/page', {'Content-Type': 'text/html; charset=utf-8'})
        with self.assertRaises(Denied):
            self.router.dispatch('web-download', url='https://example.com/page')
        self.response = FakeResponse(b'MZ', 'https://cdn.example.com/x.zip', {'Content-Type': 'application/octet-stream',
                                     'Content-Disposition': 'attachment; filename="tool.exe"'})
        with self.assertRaises(Denied):
            self.router.dispatch('web-download', url='https://cdn.example.com/x.zip')
        self.response = FakeResponse(b'123', 'https://example.com/a.zip', {'Content-Type': 'application/zip',
                                                                         'Content-Length': '10'})
        with self.assertRaises(Denied):
            self.router.dispatch('web-download', url='https://example.com/a.zip')
        self.response = FakeResponse(b'123', 'http://evil.example.com/a.zip', {'Content-Type': 'application/zip'})
        with self.assertRaises(Denied):
            self.router.dispatch('web-download', url='https://example.com/a.zip')
        self.assertEqual(list(self.dl.iterdir()), [])  # nothing left behind
        self.approve = False
        self.response = FakeResponse(b'ok', 'https://example.com/b.zip', {'Content-Type': 'application/zip'})
        with self.assertRaises(Denied):
            self.router.dispatch('web-download', url='https://example.com/b.zip')
        self.assertEqual(list(self.dl.iterdir()), [])

    def test_answer_saved_to_documents_and_rolled_back(self):
        out = self.run_phrase('найди в интернете что такое квазар и сохрани в документы')
        self.assertEqual(out['status'], 'PASS', out)
        files = list((self.docs / 'JARVIS').glob('*.txt'))
        self.assertEqual(len(files), 1)
        body = files[0].read_text(encoding='utf-8')
        self.assertIn('Вопрос: что такое квазар', body)
        self.assertIn('https://ru.wikipedia.org/x', body)
        self.assertEqual(self.run_phrase('отмени последнее действие')['status'], 'PASS')
        self.assertFalse((self.docs / 'JARVIS').exists())  # the folder JARVIS made is gone, it was empty

    def test_save_without_answer(self):
        with self.assertRaises(Denied):
            self.router.dispatch('web-save')

    def test_phone_allowlist(self):
        self.assertEqual(validate({'task': 'web-open-result', 'args': {'index': 2}}), ('web-open-result', {'index': 2}))
        self.assertEqual(validate({'task': 'web-site-search', 'args': {'site': 'youtube', 'query': 'кот'}})[0],
                         'web-site-search')
        for bad in ({'task': 'web-download', 'args': {'url': 'https://example.com/a.pdf'}},
                    {'task': 'web-site-search', 'args': {'site': 'evil', 'query': 'x'}},
                    {'task': 'web-open-result', 'args': {'index': 0}}):
            with self.assertRaises(Denied):
                validate(bad)

    def test_phrases_do_not_steal_other_commands(self):
        self.assertEqual(understand('открой ютуб')['steps'][0]['task'], 'app-open')
        self.assertEqual(understand('открой отчет.docx')['steps'][0]['task'], 'office-read')
        self.assertEqual(understand('прочитай сайт python.org')['steps'][0]['task'], 'browser-read')
        self.assertEqual(understand('найди в интернете курс биткоина')['steps'][0]['task'], 'web-answer')
        self.assertEqual(understand('найди в интернете курс биткоина и покажи ссылки')['steps'],
                         [{'task': 'web-search', 'args': {'query': 'курс биткоина'}}])


if __name__ == '__main__':
    unittest.main()
