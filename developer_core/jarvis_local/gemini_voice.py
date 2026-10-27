"""JARVIS voice from Google Gemini TTS (official API, prebuilt synthetic voices; Russian supported).

Same HTTP contract as voice_server.py (127.0.0.1:10791, GET /health, POST /speak -> audio/wav),
so the gateway, executor and voice loop need no changes. The API key is stored with Windows
user DPAPI in <DEV>/.gemini-dev and is never printed or logged. Only the reply text JARVIS is
about to say is sent to Google; replies are cached by text hash, so frequent phrases are sent once.

  python -m developer_core.jarvis_local.gemini_voice set-key        (asks for the key, hidden input)
  python -m developer_core.jarvis_local.gemini_voice samples        (renders voice samples)
  python -m developer_core.jarvis_local.gemini_voice serve --voice Schedar --effect robot-3
"""
from __future__ import annotations
import argparse
import base64
import getpass
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time
import wave
from . import voice_dsp
from .qwen_bank import bank_key
from .session import Denied

DEV_ROOT = Path(__file__).resolve().parents[2]
ROOT = DEV_ROOT / '.gemini-dev'
HOST = 'generativelanguage.googleapis.com'
MODEL = 'gemini-3.8-flash-tts'
DEFAULT_VOICE = 'Schedar'  # chosen by the user
DEFAULT_EFFECT = 'robot-3'  # film-assistant polish + strong machine timbre, chosen by the user
VOICES = ('Charon', 'Orus', 'Iapetus', 'Algenib', 'Rasalgethi', 'Sadaltager', 'Gacrux', 'Schedar',
          'Alnilam', 'Enceladus', 'Umbriel', 'Achird', 'Algieba', 'Fenrir', 'Puck', 'Kore')
STYLE = ('Спокойный, вежливый и уверенный британский дворецкий-ассистент: ровный бархатный голос, '
         'лёгкая ирония, без спешки, чёткая дикция.')
MAX_TEXT = 400
# offline fallback: frequent replies pre-recorded with the local Qwen3-TTS voice (qwen_bank.py)
LOCAL_BANK = Path.home() / 'Documents' / 'Codex' / 'JARVIS-my-voice' / 'local_voice' / 'bank'


class KeyStore:
    def __init__(self, root=ROOT, name='gemini-key', label='JARVIS Gemini DEV', service='Gemini'):
        self.root, self.name, self.label, self.service = Path(root), name, label, service
        self.path = self.root / (name + '.bin')

    def save(self, key):
        key = re.sub(r'[\x00-\x20\x7f]', '', key or '')  # Ctrl+V in a hidden prompt adds control chars
        if not re.fullmatch(r'[\x21-\x7e]{20,300}', key):
            raise Denied('This does not look like a %s API key (%d characters after cleanup)' % (self.service, len(key)))
        import win32crypt
        self.root.mkdir(parents=True, exist_ok=True)
        blob = win32crypt.CryptProtectData(key.encode(), self.label, None, None, None, 1)
        tmp = self.root / (self.name + '.' + secrets.token_hex(8) + '.bin')
        try:
            with tmp.open('xb') as stream:
                stream.write(blob)
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)
        return {'status': 'SAVED', 'storage': 'WINDOWS_USER_DPAPI'}

    def configured(self):
        return self.path.is_file()

    def load(self):
        if not self.path.is_file() or self.path.stat().st_size > 65536:
            raise Denied('%s key is not configured: run set-key' % self.service)
        import win32crypt
        try:
            return win32crypt.CryptUnprotectData(self.path.read_bytes(), None, None, None, 1)[1].decode()
        except Exception:
            raise Denied('Cannot unlock the %s key for this Windows user' % self.service) from None


def _https(path, body, key):
    conn = http.client.HTTPSConnection(HOST, timeout=60)
    try:
        conn.request('POST', path, body=json.dumps(body).encode('utf-8'),
                     headers={'Content-Type': 'application/json', 'x-goog-api-key': key})
        r = conn.getresponse()
        raw = r.read(20_000_001)
        if r.status != 200:
            try:
                msg = json.loads(raw).get('error', {}).get('message', '')
            except ValueError:
                msg = ''
            raise Denied('Gemini HTTP %d %s' % (r.status, msg[:160].replace(key, '***')))
        return json.loads(raw)
    except (OSError, http.client.HTTPException) as exc:
        raise Denied('Gemini unreachable: ' + type(exc).__name__) from None
    finally:
        conn.close()


def request_body(text, voice, style=STYLE):
    return {'model': MODEL,
            'input': [{'type': 'user_input', 'content': [{
                'type': 'text', 'text': text,
                'annotations': [{'type': 'speech_metadata', 'style': style}]}]}],
            'response_format': {'type': 'audio'},
            'generation_config': {'speech_config': [{'voice': voice}]}}


def audio_from(response):
    """Find the base64 audio in an Interactions response; returns WAV bytes."""
    for step in response.get('steps') or []:
        for part in step.get('content') or []:
            data = part.get('data') if isinstance(part, dict) else None
            if data:
                raw = base64.b64decode(data)
                return raw if raw[:4] == b'RIFF' else pcm_to_wav(raw)
    raise Denied('Gemini returned no audio')


def pcm_to_wav(pcm, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class GeminiVoice:
    device = 'gemini'

    def __init__(self, voice=DEFAULT_VOICE, keys=None, transport=_https, cache=None, effect=DEFAULT_EFFECT,
                 bank=LOCAL_BANK):
        if voice not in VOICES:
            raise Denied('Unknown Gemini voice: %s' % voice)
        if effect is not None and effect not in voice_dsp.FILM:
            raise Denied('Unknown voice effect: %s' % effect)
        self.voice, self.keys, self.transport, self.effect = voice, keys or KeyStore(), transport, effect
        self.cache = Path(cache or ROOT / 'cache')
        self.cache.mkdir(parents=True, exist_ok=True)
        self.bank = Path(bank) if bank else None
        self.lock = threading.Lock()

    @property
    def style(self):
        return '%s/%s' % (self.voice, self.effect or 'clean')

    def _hit(self, text):
        key = hashlib.sha256((MODEL + '|' + self.voice + '|' + STYLE + '|' + text).encode('utf-8')).hexdigest()[:32]
        return self.cache / (key + '.wav')

    def cached(self, text):
        return self._hit(text.strip()).is_file()

    def speak(self, text):
        text = text.strip()
        if not 1 <= len(text) <= MAX_TEXT:
            raise ValueError('text must be 1..%d characters' % MAX_TEXT)
        hit = self._hit(text)  # the cache keeps Gemini's raw audio, so the effect can change freely
        if hit.is_file():
            data = hit.read_bytes()
        else:
            try:
                with self.lock:
                    data = audio_from(self.transport('/v1beta/interactions', request_body(text, self.voice),
                                                     self.keys.load()))
            except Denied:  # offline or rate-limited: use the local pre-recorded voice if this phrase exists
                local = self.bank / (bank_key(text) + '.wav') if self.bank else None
                if local is None or not local.is_file():
                    raise
                data = local.read_bytes()
            else:
                tmp = hit.with_suffix('.tmp')
                tmp.write_bytes(data)
                os.replace(tmp, hit)
        if self.effect is None:
            return data
        x, rate = voice_dsp.wav_to_array(data)
        return voice_dsp.to_wav(voice_dsp.film(x, rate, self.effect), rate)


def take_clipboard():
    """Read the copied key and clear the clipboard so it does not linger there."""
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        win32clipboard.EmptyClipboard()
    except TypeError:
        raise Denied('Clipboard has no text: copy the key first') from None
    finally:
        win32clipboard.CloseClipboard()
    return text


def paced_prewarm(voice, phrases, pause=25.0, sleep=time.sleep):
    """Cache frequent replies without tripping the free-tier limit; stops at the first refusal."""
    done = 0
    for p in phrases:
        if voice.cached(p):
            continue
        try:
            voice.speak(p)
            done += 1
        except Exception as exc:
            print('VOICE PREWARM stopped:', str(exc)[:120], flush=True)
            break
        sleep(pause)
    print('VOICE PREWARM done: %d new phrases cached' % done, flush=True)
    return done


SAMPLE = 'Добрый вечер, сэр. Все системы в норме. Громкость двадцать, PlayStation на экране. Чем могу помочь?'


def main(argv=None):
    ap = argparse.ArgumentParser(description='JARVIS voice from Gemini TTS')
    sub = ap.add_subparsers(dest='cmd', required=True)
    k = sub.add_parser('set-key')
    k.add_argument('--from-clipboard', action='store_true', help='read the copied key, then clear the clipboard')
    s = sub.add_parser('samples')
    s.add_argument('--out', default=str(ROOT / 'samples'))
    s.add_argument('--voices', default='Charon,Orus,Iapetus,Algenib,Rasalgethi,Sadaltager,Gacrux,Schedar')
    v = sub.add_parser('serve')
    v.add_argument('--voice', default=DEFAULT_VOICE)
    v.add_argument('--effect', default=DEFAULT_EFFECT, help='film-1..3, robot-1..3 or none')
    v.add_argument('--port', type=int, default=10791)
    v.add_argument('--prewarm', action='store_true', help='cache frequent replies slowly (free tier: 3 req/min)')
    a = ap.parse_args(argv)
    if a.cmd == 'set-key':
        if a.from_clipboard:
            print(json.dumps(KeyStore().save(take_clipboard())))
        else:
            print(json.dumps(KeyStore().save(getpass.getpass('Gemini API key (input hidden): '))))
        return 0
    if a.cmd == 'samples':
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        for i, name in enumerate(a.voices.split(','), 1):
            voice = GeminiVoice(name.strip(), effect=None)  # raw samples for choosing
            if i > 1 and not voice.cached(SAMPLE):
                time.sleep(21)  # free tier: 3 requests per minute
            data = voice.speak(SAMPLE)
            (out / ('%d_%s.wav' % (i, name.strip()))).write_bytes(data)
            print('OK', i, name.strip(), flush=True)
        return 0
    from .voice_server import build, prewarm_phrases
    voice = GeminiVoice(a.voice, effect=None if a.effect == 'none' else a.effect)
    voice.keys.load()  # fail fast without a key
    server = build(voice, a.port)
    print('VOICE SERVER ready on 127.0.0.1:%d engine=gemini voice=%s effect=%s' % (a.port, a.voice, a.effect), flush=True)
    if a.prewarm:
        threading.Thread(target=paced_prewarm, args=(voice, prewarm_phrases()), daemon=True).start()
    server.serve_forever()


if __name__ == '__main__':
    sys.exit(main())
