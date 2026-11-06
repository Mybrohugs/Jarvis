"""Phone PWA gateway: real loopback HTTP. Read + queue only; no execution path exists."""
import ast
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from developer_core.jarvis_local import pwa_gateway
from developer_core.jarvis_local.pwa_gateway import PhoneQueue, build
from developer_core.jarvis_local.workspace import Workspace

TOKEN = 'a' * 40


class PWATests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'ws'
        self.root.mkdir()
        self.server = build(self.root, TOKEN, 0, ['mypc.example.ts.net'], say_wait=0.5)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def req(self, method, path, body=None, token=TOKEN, host=None, ctype='application/json'):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        headers = {'Host': host or '127.0.0.1:%d' % self.port}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            headers['Content-Type'] = ctype
        c.request(method, path, body=data, headers=headers)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r, raw

    def test_shell_assets_and_security_headers(self):
        for path in ('/', '/app.js', '/app.css', '/sw.js', '/manifest.webmanifest', '/icon-180.png', '/icon-512.png',
                     '/fonts/exo2-cyrillic.woff2', '/fonts/exo2-latin.woff2'):
            r, raw = self.req('GET', path, token=None)
            self.assertEqual(r.status, 200, path)
            self.assertIn("default-src 'self'", r.getheader('Content-Security-Policy'))
            self.assertEqual(r.getheader('X-Content-Type-Options'), 'nosniff')
            self.assertTrue(raw)
        r, raw = self.req('GET', '/', token=None)
        html = raw.decode()
        self.assertIn('apple-touch-icon', html)
        self.assertNotIn('<script>', html)  # no inline script (strict CSP)
        self.assertNotIn('style="', html)

    def test_pc_exchange_menu(self):
        import re
        html = self.req('GET', '/', token=None)[1].decode()
        js = self.req('GET', '/app.js', token=None)[1].decode()
        for el_id in ('topc', 'topc2', 'pickfile', 'findpc'):
            self.assertIn('id="%s"' % el_id, html)
            self.assertIn("$('%s')" % el_id, js)
        self.assertIn("fetch('/api/upload'", js)
        self.assertIn("'X-Filename': encodeURIComponent(", js)
        # every chip phrase in the PC section reaches a PC task, not the slow LLM path
        section = html.split('Обмен с ПК', 1)[1].split('</div>', 1)[0]
        phrases = re.findall(r'data-phrase="([^"]+)"', section)
        self.assertEqual([pwa_gateway.phone_quick_command(p)['task'] for p in phrases], ['pc-clip-get', 'pc-screen'])
        self.assertEqual(pwa_gateway.phone_quick_command('найди на компьютере отчёт')['task'], 'pc-find')

    def test_auth_and_host_guard(self):
        self.assertEqual(self.req('GET', '/api/summary', token=None)[0].status, 401)
        self.assertEqual(self.req('GET', '/api/summary', token='b' * 40)[0].status, 401)
        self.assertEqual(self.req('GET', '/api/summary', host='evil.example')[0].status, 421)
        self.assertEqual(self.req('GET', '/api/summary', host='mypc.example.ts.net')[0].status, 200)
        self.assertEqual(self.req('GET', '/../../etc/passwd', token=None)[0].status, 401)

    def test_queue_and_station_intents(self):
        r, raw = self.req('POST', '/api/say', {'text': 'скопируй отчёт в архив'})
        reply = json.loads(raw)
        self.assertEqual(r.status, 200)
        self.assertTrue(reply['queued'])
        self.assertIn('подтверждения', reply['speech'])
        reply = json.loads(self.req('POST', '/api/say', {'text': 'Статус'})[1])
        self.assertEqual(reply['intent'], 'status')
        self.assertIn('В очереди 1 запрос.', reply['speech'])
        reply = json.loads(self.req('POST', '/api/say', {'text': 'что в очереди?'})[1])
        self.assertIn('скопируй отчёт', reply['speech'])
        items = json.loads(self.req('GET', '/api/queue')[1])['items']
        self.assertEqual([i['status'] for i in items], ['pending'])
        summary = json.loads(self.req('GET', '/api/summary')[1])
        self.assertFalse(summary['phone_can_execute'])

    def test_input_limits(self):
        self.assertEqual(self.req('POST', '/api/queue', {'text': 'x' * 501})[0].status, 400)
        self.assertEqual(self.req('POST', '/api/queue', {'text': 'a\x07'})[0].status, 400)
        self.assertEqual(self.req('POST', '/api/queue', b'x' * 5000)[0].status, 413)  # MAX_BODY 4096
        self.assertEqual(self.req('POST', '/api/queue', {'text': 'hi'}, ctype='text/plain')[0].status, 415)
        self.assertEqual(self.req('POST', '/api/run', {'task': 'file-apply'})[0].status, 404)

    def test_audit_is_sanitized(self):
        Workspace(self.root).journal.record('office-apply', 'PASS', src='secret/path.docx', approval_mode='local-keyboard')
        entry = json.loads(self.req('GET', '/api/audit')[1])['entries'][0]
        self.assertEqual(set(entry), {'id', 'utc', 'task', 'status'})

    def test_only_local_cli_closes_requests(self):
        q = PhoneQueue(Workspace(self.root))
        item = q.add('проверь почту')
        self.assertEqual(q.close(item['id'], 'done')['status'], 'done')
        with self.assertRaises(ValueError):
            q.close(item['id'], 'done')

    def test_gateway_cannot_reach_executors(self):
        tree = ast.parse(Path(pwa_gateway.__file__).read_text(encoding='utf-8'))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or '')
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        forbidden = {'router', 'session', 'v8', 'windows', 'notepad', 'browser', 'subprocess', 'cli'}
        self.assertFalse({m.split('.')[-1] for m in imported} & forbidden, imported)

    def test_pairing_code_gives_device_key_once(self):
        code = self.server.pairing.code
        wrong = '000000' if code != '000000' else '111111'
        self.assertEqual(self.req('POST', '/api/pair', {'code': wrong}, token=None)[0].status, 403)
        r, raw = self.req('POST', '/api/pair', {'code': code, 'name': 'iPhone'}, token=None)
        self.assertEqual(r.status, 201)
        device = json.loads(raw)['token']
        self.assertEqual(self.req('GET', '/api/summary', token=device)[0].status, 200)
        self.assertEqual(self.req('POST', '/api/pair', {'code': code}, token=None)[0].status, 403)  # single use
        stored = (self.root / '.jarvis_local' / 'pwa_devices.json').read_text(encoding='utf-8')
        self.assertNotIn(device, stored)  # only the hash is kept
        pwa_gateway.Devices(Workspace(self.root)).revoke_all()
        self.assertEqual(self.req('GET', '/api/summary', token=device)[0].status, 401)

    def test_pairing_locks_after_five_attempts(self):
        code = self.server.pairing.code
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(5):
            self.assertEqual(self.req('POST', '/api/pair', {'code': wrong}, token=None)[0].status, 403)
        self.assertEqual(self.req('POST', '/api/pair', {'code': code}, token=None)[0].status, 403)

    def test_master_key_optional(self):
        server = build(self.root, '', 0)
        server.server_close()

    def test_speech_route_proxies_audio_only(self):
        said = []

        def fake_speech(text):
            said.append(text)
            if text == 'fail':
                raise OSError('down')
            return b'RIFF....WAVEfmt '
        server = build(self.root, TOKEN, 0, speech=fake_speech)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        old = self.port
        self.port = server.server_port
        try:
            r, raw = self.req('POST', '/api/speech', {'text': 'Громкость 28.'})
            self.assertEqual((r.status, r.getheader('Content-Type'), raw[:4]), (200, 'audio/wav', b'RIFF'))
            self.assertEqual(self.req('POST', '/api/speech', {'text': 'fail'})[0].status, 503)
            self.assertEqual(self.req('POST', '/api/speech', {'text': 'x'}, token=None)[0].status, 401)
            self.assertEqual(said, ['Громкость 28.', 'fail'])
        finally:
            self.port = old

    def test_short_token_refused(self):
        with self.assertRaises(ValueError):
            build(self.root, 'short', 0)


class ListenTests(unittest.TestCase):
    """Push-to-talk: audio in, text out. Nothing is queued or run by /api/listen itself."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.heard = []
        self.server = build(self.tmp.name, TOKEN, 0, say_wait=0.2,
                            listen=lambda data: self.heard.append(data) or 'включи свет')
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    req = PWATests.req

    def test_audio_becomes_text_only(self):
        r, raw = self.req('POST', '/api/listen', b'\x00' * 5000, ctype='audio/mp4')
        self.assertEqual((r.status, json.loads(raw)['text']), (200, 'включи свет'))
        self.assertEqual(len(self.heard[0]), 5000)
        self.assertEqual(PhoneQueue(Workspace(self.tmp.name)).items(), [])  # transcription never queues

    def test_listen_guards(self):
        self.assertEqual(self.req('POST', '/api/listen', b'x' * 100, token=None, ctype='audio/mp4')[0].status, 401)
        self.assertEqual(self.req('POST', '/api/listen', b'x' * 100, ctype='application/json')[0].status, 415)
        big = b'x' * (pwa_gateway.MAX_AUDIO + 1)
        try:  # refused before reading: Windows may reset the socket while the client is still sending
            self.assertEqual(self.req('POST', '/api/listen', big, ctype='audio/mp4')[0].status, 413)
        except ConnectionError:
            pass
        self.assertEqual(self.heard, [])  # the oversized audio never reached the transcriber

    def test_busy_and_bad_audio(self):
        def busy(data):
            raise BlockingIOError
        srv = build(self.tmp.name, TOKEN, 0, listen=busy)  # a second server whose transcriber is busy
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.port = srv.server_port
        self.assertEqual(self.req('POST', '/api/listen', b'x' * 100, ctype='audio/mp4')[0].status, 429)
        with self.assertRaises(Exception):
            pwa_gateway.decode_audio(b'not audio at all')


if __name__ == '__main__':
    unittest.main()


class SlowAnswerTests(unittest.TestCase):
    def test_free_speech_waits_for_a_slow_pc_answer(self):
        """The local model needs 10-40 s: the phone must get the answer, not 'Записал…' after 10 s."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ws = Workspace(tmp.name)
        queue = PhoneQueue(ws)

        def pc_answers_late():
            for _ in range(200):
                pending = [i for i in queue.items() if i['status'] == 'pending']
                if pending:
                    threading.Event().wait(1.2)  # longer than the old wait*2 limit (0.5 s here)
                    queue.close(pending[0]['id'], 'done', 'Кубиты бывают в суперпозиции.', origin='phone-executor')
                    return
                threading.Event().wait(0.02)
        threading.Thread(target=pc_answers_late, daemon=True).start()
        out = pwa_gateway.station_reply('как устроен квантовый компьютер', ws, queue, wait=0.25)
        self.assertEqual(out['speech'], 'Кубиты бывают в суперпозиции.')
        self.assertFalse(out['queued'])


class AccessLogTests(unittest.TestCase):
    def test_log_has_path_status_time_only(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = Path(tmp.name) / 'gateway.log'
        server = build(Path(tmp.name), TOKEN, 0, say_wait=0.1)
        server.log_path = log
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def post(path, body):
            c = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=10)
            c.request('POST', path, json.dumps(body), {'Authorization': 'Bearer ' + TOKEN,
                                                       'Content-Type': 'application/json', 'Host': '127.0.0.1:%d' % server.server_address[1]})
            status = c.getresponse().status
            c.close()
            return status
        self.assertEqual(post('/api/clientlog', {'event': 'voice failed: timeout <script>'}), 204)
        self.assertEqual(post('/api/queue', {'text': 'секретный текст'}), 201)
        text = log.read_text(encoding='utf-8')
        self.assertIn('phone: voice failed: timeout ?script?', text)
        self.assertIn('POST /api/queue 201', text)
        self.assertNotIn('секретный', text)
        self.assertNotIn(TOKEN, text)
