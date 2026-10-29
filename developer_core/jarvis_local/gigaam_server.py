"""GigaAM v3 speech-to-text server for this PC only (127.0.0.1). Runs in the separate speech venv
(torch + gigaam), launched by path with -I, so it imports nothing from developer_core.

POST /stt  body = 16 kHz mono float32 little-endian PCM (<= 60 s)  ->  {"text": "..."}
GET  /health                                                       ->  {"status": "PASS", "model": ...}
The model is loaded once (GPU if available) and warmed up; one phrase at a time. Audio stays in memory.
"""
import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RATE = 16000
MAX_SECONDS = 60
CHUNK = 20 * RATE  # GigaAM's short-form limit is 25 s


class Engine:
    def __init__(self, name):
        import gigaam
        import torch
        self.torch, self.name = torch, name
        self.model = gigaam.load_model(name)
        self.lock = threading.Lock()
        self.text(__import__('numpy').zeros(RATE, dtype='float32'))  # warm-up: first real phrase is fast

    def text(self, pcm):
        torch, m = self.torch, self.model
        out = []
        with self.lock, torch.inference_mode():
            for start in range(0, len(pcm), CHUNK):
                piece = pcm[start:start + CHUNK]
                if len(piece) < RATE // 4:
                    continue
                wav = torch.from_numpy(piece).to(m._device).to(m._dtype).unsqueeze(0)
                length = torch.full([1], wav.shape[-1], device=m._device)
                encoded, encoded_len = m.forward(wav, length)
                out.append(m._decode(encoded, encoded_len, length, False)[0][0])
        return ' '.join(t.strip() for t in out if t and t.strip())


def handler(engine):
    import numpy as np

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/health':
                return self.send(200, {'status': 'PASS', 'model': engine.name,
                                       'device': str(engine.model._device)})
            self.send(404, {'error': 'not found'})

        def do_POST(self):
            if self.path != '/stt':
                return self.send(404, {'error': 'not found'})
            n = int(self.headers.get('Content-Length') or 0)
            if n <= 0 or n % 4 or n > MAX_SECONDS * RATE * 4:
                return self.send(413, {'error': 'audio: 16 kHz float32, up to 60 s'})
            pcm = np.frombuffer(self.rfile.read(n), dtype='<f4').copy()
            t = time.time()
            try:
                text = engine.text(pcm)
            except Exception as exc:  # a bad phrase must not stop the server
                return self.send(500, {'error': type(exc).__name__})
            self.send(200, {'text': text, 'seconds': round(time.time() - t, 3)})

    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=10792)
    ap.add_argument('--model', default='v3_e2e_rnnt')
    a = ap.parse_args()
    t = time.time()
    engine = Engine(a.model)
    print('GigaAM %s ready on 127.0.0.1:%d (%s, %.1f s)' % (a.model, a.port, engine.model._device, time.time() - t),
          flush=True)
    ThreadingHTTPServer(('127.0.0.1', a.port), handler(engine)).serve_forever()


if __name__ == '__main__':
    sys.exit(main())
