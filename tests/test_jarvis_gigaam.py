"""GigaAM speech recognition: the gateway uses the local server when it runs and falls back to Whisper."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
import unittest
import numpy as np
from developer_core.jarvis_local import pwa_gateway
from developer_core.jarvis_local.pwa_gateway import Listener, gigaam_text


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class FakeGigaAM(BaseHTTPRequestHandler):
    got = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        FakeGigaAM.got.append(np.frombuffer(body, dtype='<f4'))
        out = json.dumps({'text': ' Привет, Джарвис '}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class GigaAMTests(unittest.TestCase):
    def test_client_sends_float32_and_reads_text(self):
        srv = ThreadingHTTPServer(('127.0.0.1', 0), FakeGigaAM)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        audio = np.linspace(-1, 1, 16000).astype(np.float32)
        self.assertEqual(gigaam_text(audio, addr=srv.server_address), 'Привет, Джарвис')
        np.testing.assert_array_equal(FakeGigaAM.got[-1], audio)

    def test_server_down_gives_none(self):
        self.assertIsNone(gigaam_text(np.zeros(1600, dtype=np.float32), addr=('127.0.0.1', free_port()), timeout=2))

    def test_listener_prefers_gigaam_and_falls_back(self):
        old = pwa_gateway.decode_audio
        pwa_gateway.decode_audio = lambda data: np.zeros(1600, dtype=np.float32)
        self.addCleanup(setattr, pwa_gateway, 'decode_audio', old)
        self.assertEqual(Listener(gigaam=lambda a: 'быстро')(b'x'), 'быстро')

        class Whisper:
            def transcribe(self, audio, **kw):
                return [type('S', (), {'text': ' запасной путь'})()], None
        slow = Listener(gigaam=lambda a: None)
        slow.stt = Whisper()
        self.assertEqual(slow(b'x'), 'запасной путь')


if __name__ == '__main__':
    unittest.main()
