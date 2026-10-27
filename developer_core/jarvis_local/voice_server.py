"""JARVIS voice server: the user's OWN cloned voice (XTTS-v2) + 'stark-metal-2' processing.

Runs ONLY in the isolated clone venv: python -I voice_server.py <voice_dir> [--port 10791]
Binds 127.0.0.1. POST /speak {"text": "..."} -> audio/wav (16 kHz mono). Same pipeline as the
chosen sample: per-sentence synthesis (speed 0.9, temperature 0.55) -> Whisper word timestamps
trim XTTS tail artefacts -> 0.38 s pauses -> voice_dsp style. Results are cached by text hash.
Refuses to start without prepared/consent.json (consent + accepted model licence).
Text only produces sound; this server cannot execute anything.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = Path(__file__).resolve().parent
STYLE = 'stark-metal-2'
MAX_TEXT = 400
WHISPER = HERE.parents[1] / '.models' / 'whisper-tiny'


def load_dsp():
    spec = importlib.util.spec_from_file_location('voice_dsp', HERE / 'voice_dsp.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sentences(text):
    parts = [p.strip() for p in re.split(r'(?<=[.!?…])\s+', text.strip()) if p.strip()]
    return parts[:12]


def norm(word):
    return re.sub(r'[^а-яёa-z0-9]', '', word.lower())


class Voice:
    def __init__(self, voice_dir):
        self.dir = Path(voice_dir)
        consent = json.loads((self.dir / 'prepared' / 'consent.json').read_text(encoding='utf-8'))
        if consent.get('consent_ok') is not True or consent.get('model_license_accepted') != 'CPML (non-commercial)':
            raise SystemExit('DENIED: consent or model licence missing')
        self.refs = sorted(str(p) for p in (self.dir / 'prepared').glob('ref_*.wav'))
        if not self.refs:
            raise SystemExit('DENIED: no reference audio')
        self.cache = self.dir / 'cache'
        self.cache.mkdir(exist_ok=True)
        for d in os.environ.get('JARVIS_FFMPEG_BIN', '').split(';'):
            if d and os.path.isdir(d):
                os.add_dll_directory(d)
                os.environ['PATH'] = d + os.pathsep + os.environ.get('PATH', '')
        os.environ.setdefault('COQUI_TOS_AGREED', '1')  # recorded in consent.json by the user
        import numpy as np
        import torch
        from TTS.api import TTS
        from faster_whisper import WhisperModel
        self.np = np
        self.dsp = load_dsp()
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.tts = TTS('tts_models/multilingual/multi-dataset/xtts_v2').to(self.device)
        self.whisper = WhisperModel(str(WHISPER), device='cpu', compute_type='int8', cpu_threads=2)
        self.lock = threading.Lock()

    def _sentence(self, text):
        np = self.np
        wav = self.tts.tts(text=text, speaker_wav=self.refs, language='ru', speed=0.9, temperature=0.55)
        x = np.asarray(wav, dtype=np.float64)
        rate = self.tts.synthesizer.output_sample_rate
        x16 = np.interp(np.arange(0, len(x), rate / 16000), np.arange(len(x)), x)
        segs, _ = self.whisper.transcribe(x16.astype(np.float32), language='ru', beam_size=1, word_timestamps=True)
        words = [w for s in segs for w in (s.words or [])]
        last = norm(text.split()[-1])[:4]
        ends = [w.end for w in words if last and norm(w.word)[:4] == last]
        if ends:
            x16 = x16[:min(len(x16), int((ends[-1] + 0.12) * 16000))]
        fade = min(len(x16), 800)
        x16[-fade:] *= np.linspace(1, 0, fade)
        return x16

    def speak(self, text):
        key = hashlib.sha256((STYLE + '|' + text).encode('utf-8')).hexdigest()[:32]
        hit = self.cache / (key + '.wav')
        if hit.is_file():
            return hit.read_bytes()
        np = self.np
        with self.lock:
            pieces = []
            for s in sentences(text):
                pieces += [self._sentence(s), np.zeros(int(16000 * 0.38))]
            if not pieces:
                raise ValueError('empty text')
            full = np.concatenate(pieces[:-1])
            data = self.dsp.to_wav(self.dsp.style(full / (np.max(np.abs(full)) + 1e-9) * 0.89, STYLE))
        tmp = hit.with_suffix('.tmp')
        tmp.write_bytes(data)
        os.replace(tmp, hit)
        return data


def prewarm_phrases():
    """Feature 2: replies JARVIS says most often, synthesized ahead of time (exact texts as sent)."""
    out = ['Готово.', 'Звук выключен.', 'Звук включён.', 'Звук работает. На связи JARVIS.',
           'Записал. Выполню после подтверждения за компьютером.', 'Очередь пуста.',
           'Телевизор выключен. Спокойной ночи, сэр.', 'Команда отправлена, но телевизор не подтвердил результат.',
           'Сценарий выполнен не полностью, проверьте телевизор.', 'Слишком много команд подряд, подождите минуту.']
    out += ['Громкость %d.' % n for n in range(0, 41)]
    out += ['Готово. PlayStation на экране, громкость %d. Приятной игры.' % n for n in range(15, 41, 5)]
    out += ['Готово. Громкость %d. Приятного просмотра.' % n for n in range(15, 41, 5)]
    return out


def prewarm(voice, phrases):
    done = 0
    for p in phrases:
        try:
            voice.speak(p)  # cached ones return instantly; lock released between phrases
            done += 1
        except Exception:
            continue
    print('VOICE PREWARM done: %d/%d phrases cached' % (done, len(phrases)), flush=True)


def build(voice, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype='application/json'):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == '/health':
                return self._send(200, {'status': 'PASS', 'device': voice.device, 'style': getattr(voice, 'style', STYLE)})
            self._send(404, {'error': 'not found'})

        def do_POST(self):
            if self.path != '/speak' or self.headers.get('Host') not in ('127.0.0.1:%d' % port, 'localhost:%d' % port):
                return self._send(404, {'error': 'not found'})
            try:
                n = int(self.headers.get('Content-Length', '0'))
                if not 0 < n <= 4096:
                    raise ValueError('body size')
                text = json.loads(self.rfile.read(n).decode('utf-8')).get('text')
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_TEXT:
                    raise ValueError('text must be 1..%d characters' % MAX_TEXT)
                self._send(200, voice.speak(text.strip()), 'audio/wav')
            except Exception as exc:
                self._send(400, {'error': type(exc).__name__ + ': ' + str(exc)[:200]})

    return HTTPServer(('127.0.0.1', port), Handler)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('voice_dir')
    ap.add_argument('--port', type=int, default=10791)
    ap.add_argument('--no-prewarm', action='store_true')
    a = ap.parse_args()
    voice = Voice(a.voice_dir)
    server = build(voice, a.port)
    print('VOICE SERVER ready on 127.0.0.1:%d device=%s style=%s' % (a.port, voice.device, STYLE), flush=True)
    if not a.no_prewarm:
        threading.Thread(target=prewarm, args=(voice, prewarm_phrases()), daemon=True).start()
    server.serve_forever()


if __name__ == '__main__':
    sys.exit(main())
