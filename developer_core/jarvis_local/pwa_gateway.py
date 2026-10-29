"""Phone PWA gateway (iPhone home-screen app). A REMOTE surface: read + queue only, plus one inbox
folder for files sent from the phone (phone_inbox: never overwrites, never opens or runs anything).

Deliberately imports nothing that can act (no router, session, V8, browser, windows,
notepad). The phone can read sanitized status/audit and append a text request to a
queue; the queue is executed only by a person at the PC through the local CLI.

Binds 127.0.0.1 only. Reach it from the phone through a private HTTPS proxy such as
`tailscale serve`, and pass that hostname with --allow-host. Bearer token comes from
the JARVIS_PWA_TOKEN environment variable, never from arguments.
"""
from __future__ import annotations
import argparse
import datetime
import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.parse
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import uuid
from .outbox import take_outbox
from . import phone_inbox
from .push import Push
from .workspace import Workspace

ASSETS = Path(__file__).with_name('pwa')
STATIC = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/orb.js': ('orb.js', 'text/javascript; charset=utf-8'),
    '/app.css': ('app.css', 'text/css; charset=utf-8'),
    '/sw.js': ('sw.js', 'text/javascript; charset=utf-8'),
    '/manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json'),
    '/icon-180.png': ('icon-180.png', 'image/png'),
    '/icon-512.png': ('icon-512.png', 'image/png'),
    '/fonts/exo2-cyrillic.woff2': ('fonts/exo2-cyrillic.woff2', 'font/woff2'),  # Exo 2, SIL OFL 1.1
    '/fonts/exo2-latin.woff2': ('fonts/exo2-latin.woff2', 'font/woff2'),
}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
AUDIT_FIELDS = ('id', 'utc', 'task', 'status')
MAX_BODY = 4096  # /api/speech carries a whole answer (<= 1000 chars, 2 bytes each in UTF-8)
MAX_REQUEST = 500
MAX_PENDING = 50
MAX_AUDIO = 6_000_000  # iPhone push-to-talk: a 2 MB limit refused a long phrase (413) on 2026-10-28


class PhoneQueue:
    """Append-only JSONL. Phone may add 'pending'; only the local CLI marks 'done'/'dismissed'."""

    def __init__(self, ws):
        self.path = ws.state / 'phone_queue.jsonl'

    def _events(self):
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding='utf-8').splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def items(self):
        state = {}
        for e in self._events():
            if e.get('event') == 'add':
                state[e['id']] = {'id': e['id'], 'utc': e['utc'], 'text': e['text'], 'status': 'pending'}
                if e.get('auto'):
                    state[e['id']]['auto'] = e['auto']
                if e.get('interpret'):
                    state[e['id']]['interpret'] = True
            elif e.get('event') in ('done', 'dismissed', 'failed') and e.get('id') in state:
                state[e['id']]['status'] = e['event']
                for k in ('speech', 'link', 'text'):
                    if e.get(k):
                        state[e['id']][k] = e[k]
            elif e.get('event') == 'note' and e.get('id') in state:
                state[e['id']]['speech'] = e.get('speech')
                state[e['id']]['interpret'] = False
        return list(state.values())

    _lock = threading.Lock()  # the gateway is multi-threaded; one writer at a time

    def _append(self, event):
        with self._lock, self.path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')

    def note(self, item_id, speech):
        """Executor's answer for a free-speech request that still needs the PC (status stays pending)."""
        self._append({'event': 'note', 'id': item_id, 'speech': str(speech)[:300], 'origin': 'phone-executor',
                      'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()})

    def add(self, text, auto=None, interpret=False):
        if not isinstance(text, str):
            raise ValueError('text required')
        text = text.strip()
        if not 1 <= len(text) <= MAX_REQUEST or any(ord(c) < 32 and c not in '\n\t' for c in text):
            raise ValueError('text must be 1..%d printable characters' % MAX_REQUEST)
        if sum(1 for i in self.items() if i['status'] == 'pending') >= MAX_PENDING:
            raise ValueError('queue full')
        item = {'event': 'add', 'id': uuid.uuid4().hex[:12], 'origin': 'phone',
                'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'text': text}
        if auto:
            item['auto'] = auto  # a REQUEST for the local phone_executor; the gateway never runs it
        if interpret:
            item['interpret'] = True  # free speech: the executor plans it (LLM) and runs only allowlisted plans
        self._append(item)
        return {'id': item['id'], 'status': 'pending', 'executed': False}

    def close(self, item_id, event, speech=None, origin='local-cli', link=None, text=None):
        if event not in ('done', 'dismissed', 'failed'):
            raise ValueError('invalid event')
        if not any(i['id'] == item_id and i['status'] == 'pending' for i in self.items()):
            raise ValueError('no such pending request')
        record = {'event': event, 'id': item_id, 'origin': origin,
                  'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        if speech:
            record['speech'] = str(speech)[:1000]  # web answers are up to 900 chars
        if link and re.fullmatch(r'/api/file/[A-Za-z0-9_\-]{20,40}', link):
            record['link'] = link
        if text:
            record['text'] = str(text)[:20000]
        self._append(record)
        return {'id': item_id, 'status': event}

    def get(self, item_id):
        return next((i for i in self.items() if i['id'] == item_id), None)


def phone_quick_command(text):
    """Allowlisted quick media commands (user decision 27.10.2026). Returns {'task','args'} or None."""
    raw = text.strip()
    t = raw.lower().rstrip('.!?')
    num = re.search(r'(\d{1,3})', t)
    m = re.match(r'^(?:найди|поищи|где) (?:на (?:компьютере|пк|диске) )(.{2,100})$', t)
    if m:
        return {'task': 'pc-find', 'args': {'query': m.group(1).strip()}}
    m = re.fullmatch(r'(?:отправь|скинь|пришли)(?: мне)?(?: файл)?(?: номер)? (\d{1,2})', t)
    if m:
        return {'task': 'pc-send', 'args': {'index': int(m.group(1))}}
    m = re.match(r'^(?:вставь|положи|скопируй) в буфер\w*(?: компьютера)? [«"“](.{1,20000})[»"”]$', raw, re.I | re.S)
    if m:
        return {'task': 'pc-clip-set', 'args': {'text': m.group(1)}}
    if re.search(r'(скинь|отправь|покажи|пришли|что в) (мне )?буфер\w*', t):
        return {'task': 'pc-clip-get', 'args': {}}
    m = re.search(r'что (?:у меня |там |сейчас )*на экране|прочитай (?:ошибку|текст)? ?(?:на|с) экран\w*|'
                  r'что за ошибка на экране', t)
    if m:
        return {'task': 'screen-describe', 'args': {}}
    if re.search(r'скриншот|снимок экрана', t):
        return {'task': 'pc-screen', 'args': {}}
    if re.search(r'режим\w* игр\w*|\bпоиграем\b|\bигровой режим\b|\bвремя игр\w*', t):
        return {'task': 'media-scene', 'args': {'name': 'game'}}
    if re.search(r'режим\w* кино|\bкинорежим\b|\bсмотрим фильм\b|\bвремя кино\b', t):
        return {'task': 'media-scene', 'args': {'name': 'movie'}}
    if re.search(r'спокойной ночи|режим\w* сна|\bотбой\b', t):
        return {'task': 'media-scene', 'args': {'name': 'night'}}
    if re.search(r'громкост\w*', t) and num:
        return {'task': 'media-volume', 'args': {'level': int(num.group(1))}}
    if re.fullmatch(r'(сделай |ещё |еще )?громче( на \d{1,2})?', t):
        return {'task': 'media-volume', 'args': {'delta': int(num.group(1)) if num else 3}}
    if re.fullmatch(r'(сделай |ещё |еще )?тише( на \d{1,2})?', t):
        return {'task': 'media-volume', 'args': {'delta': -(int(num.group(1)) if num else 3)}}
    if re.fullmatch(r'(без звука|выключи звук|отключи звук|убери звук)', t):
        return {'task': 'media-mute', 'args': {'on': True}}
    if re.fullmatch(r'(включи звук|верни звук)', t):
        return {'task': 'media-mute', 'args': {'on': False}}
    return None


def summary(ws, queue):
    entries = ws.journal.entries()
    now = datetime.datetime.now(datetime.timezone.utc)
    day = []
    for e in entries:
        try:
            if now - datetime.datetime.fromisoformat(e['utc']) <= datetime.timedelta(days=1):
                day.append(e)
        except (KeyError, ValueError, TypeError):
            continue
    counts = {}
    for e in day:
        counts[e.get('status', '?')] = counts.get(e.get('status', '?'), 0) + 1
    return {'status': 'PASS', 'server_utc': now.isoformat(), 'actions_24h': len(day), 'by_status_24h': counts,
            'needs_attention_24h': sum(v for k, v in counts.items() if k not in ('PASS', 'CANCELLED')),
            'pending_requests': sum(1 for i in queue.items() if i['status'] == 'pending'),
            'phone_can_execute': False}


class Devices:
    """Paired phones. Only SHA-256 of each device token is stored; delete the file to revoke all."""

    def __init__(self, ws):
        self.path = ws.state / 'pwa_devices.json'

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    _lock = threading.Lock()

    def add(self, name):
        with self._lock:
            return self._add(name)

    def _add(self, name):
        token = secrets.token_urlsafe(32)
        data = self._load()
        if len(data) >= 10:
            raise ValueError('too many paired devices; revoke first')
        data[hashlib.sha256(token.encode()).hexdigest()] = {
            'name': name, 'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return token

    def valid(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        return any(hmac.compare_digest(digest, known) for known in self._load())

    def revoke_all(self):
        self.path.unlink(missing_ok=True)


class Pairing:
    """One-time 6-digit code printed on the PC console: 10 minutes, 5 attempts, single use."""

    def __init__(self, ttl=600, attempts=5):
        self.code = '%06d' % secrets.randbelow(10 ** 6)
        self.expires = time.monotonic() + ttl
        self.left = attempts

    _lock = threading.Lock()

    def redeem(self, code):
        with self._lock:  # threaded gateway: attempts must not race
            return self._redeem(code)

    def _redeem(self, code):
        if self.code is None or time.monotonic() > self.expires or self.left <= 0:
            self.code = None
            raise PermissionError('pairing closed; restart the gateway for a new code')
        self.left -= 1
        if not isinstance(code, str) or not hmac.compare_digest(code.strip(), self.code):
            raise PermissionError('wrong code (%d attempts left)' % self.left)
        self.code = None
        return True


VOICE_PORT = 10791


def fetch_speech(text, port=VOICE_PORT, timeout=60, local=False):
    """Ask the local voice server (user's own voice, stark-metal-2) for a WAV. Sound only."""
    import http.client
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000:
        raise ValueError('text must be 1..1000 characters')
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        c.request('POST', '/speak', body=json.dumps({'text': text.strip(), **({'local': True} if local else {})},
                                                    ensure_ascii=False).encode('utf-8'),
                  headers={'Content-Type': 'application/json', 'Host': '127.0.0.1:%d' % port})
        r = c.getresponse()
        data = r.read(8_000_001)
        if r.status != 200 or not data.startswith(b'RIFF') or len(data) > 8_000_000:
            raise OSError('voice server error %d' % r.status)
        return data
    finally:
        c.close()


def decode_audio(data, rate=16000, limit_s=60):
    """iPhone AAC/M4A, WebM/Opus or WAV bytes -> 16 kHz mono float32 (PyAV, in memory)."""
    import io
    import av
    import numpy as np
    parts, total = [], 0
    with av.open(io.BytesIO(data), mode='r') as box:
        resampler = av.AudioResampler(format='s16', layout='mono', rate=rate)
        for frame in box.decode(audio=0):
            for out in resampler.resample(frame):
                pcm = out.to_ndarray().reshape(-1)
                parts.append(pcm)
                total += len(pcm)
                if total > rate * limit_s:
                    raise ValueError('audio too long')
        for out in resampler.resample(None):
            parts.append(out.to_ndarray().reshape(-1))
    if not parts:
        raise ValueError('no audio')
    return np.concatenate(parts).astype(np.float32) / 32768


class Listener:
    """Push-to-talk from the phone: audio -> text on this PC (faster-whisper, CPU). Text only, nothing is run.
    The audio is decoded in memory and never saved; one phrase at a time."""

    def __init__(self, model='small'):
        self.model, self.stt, self.lock = model, None, threading.Lock()

    def __call__(self, data):
        if not self.lock.acquire(blocking=False):
            raise BlockingIOError('busy')
        try:
            if self.stt is None:
                from faster_whisper import WhisperModel
                self.stt = WhisperModel(self.model, device='cpu', compute_type='int8', cpu_threads=4)
            audio = decode_audio(data)
            segments, _ = self.stt.transcribe(audio, language='ru', beam_size=3, vad_filter=True)
            return ' '.join(seg.text for seg in segments).strip()[:MAX_REQUEST]
        finally:
            self.lock.release()


def plural(n, one, few, many):
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


ACCESS_LOG = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'JARVIS' / 'gateway.log'


def access_log(line, path):
    if path is None:  # tests and embedded servers: no log
        return
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > 1_000_000:
            os.replace(path, path.with_suffix('.log.1'))
        with path.open('a', encoding='utf-8') as f:
            f.write(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S ') + line + '\n')
    except OSError:
        pass  # logging must never break the phone


THINK_WAIT = 60.0  # free speech goes through the PC planner and maybe a web answer: allow a slow local model


def station_reply(text, ws, queue, wait=5.0, think_wait=THINK_WAIT):
    """Mini-station (Siri Shortcut / PWA). Fixed read-only intents; anything else is queued, never run."""
    t = text.strip().lower().rstrip('.!?')
    if re.fullmatch(r'(какой )?(статус|сводка|как дела|как ты)', t):
        s = summary(ws, queue)
        n, bad, q = s['actions_24h'], s['needs_attention_24h'], s['pending_requests']
        speech = 'За сутки %d %s.' % (n, plural(n, 'действие', 'действия', 'действий'))
        speech += ' Требуют внимания: %d.' % bad if bad else ' Ошибок нет.'
        speech += ' В очереди %d %s.' % (q, plural(q, 'запрос', 'запроса', 'запросов'))
        return {'intent': 'status', 'speech': speech, 'queued': False}
    if re.fullmatch(r'(что в )?очередь|что в очереди|мои запросы', t):
        pending = [i for i in queue.items() if i['status'] == 'pending']
        if not pending:
            return {'intent': 'queue', 'speech': 'Очередь пуста.', 'queued': False}
        return {'intent': 'queue', 'queued': False,
                'speech': 'В очереди %d. Последний: %s' % (len(pending), pending[-1]['text'][:120])}
    quick = phone_quick_command(text)
    if quick:
        item = queue.add(text, auto=quick)
        # The local phone_executor usually answers within a second; never block longer than 5 s.
        for _ in range(int(wait / 0.25)):
            done = queue.get(item['id'])
            if done and done['status'] != 'pending':
                return {'intent': 'quick', 'queued': False, 'id': item['id'], 'status': done['status'],
                        'link': done.get('link'), 'text': done.get('text'),
                        'speech': done.get('speech') or ('Готово.' if done['status'] == 'done' else 'Не получилось.')}
            time.sleep(0.25)
        return {'intent': 'quick', 'queued': True, 'id': item['id'],
                'speech': 'Принял. Исполнитель на компьютере сейчас не отвечает.'}
    item = queue.add(text, interpret=True)
    for _ in range(int(min(think_wait, wait * 12) / 0.25)):  # planning + web answer: 5-40 s on the local model
        got = queue.get(item['id'])
        if got and (got['status'] != 'pending' or got.get('speech')):
            return {'intent': 'request', 'queued': got['status'] == 'pending', 'id': item['id'],
                    'status': got['status'], 'speech': got['speech'] or 'Готово.'}
        time.sleep(0.25)
    return {'intent': 'request', 'queued': True, 'id': item['id'],
            'speech': 'Записал. Выполню после подтверждения за компьютером.'}


def build(workspace, token, port=0, allow_hosts=(), speech=fetch_speech, say_wait=5.0, listen=None, allow_origins=(),
          inbox=None):
    """token: optional master key (Siri Shortcut / scripts); phones normally pair by code."""
    if token and len(token) < 32:
        raise ValueError('JARVIS_PWA_TOKEN must be at least 32 characters')
    ws = Workspace(workspace)
    queue = PhoneQueue(ws)
    devices = Devices(ws)
    pairing = Pairing()
    push = Push(ws)
    listen = listen or Listener()
    for host in allow_hosts:
        if not re.fullmatch(r'[a-z0-9.-]{1,253}', host):
            raise ValueError('invalid --allow-host')
    for origin in allow_origins:  # JARVIS Mobile pages (e.g. GitHub Pages) that may call this gateway
        if not re.fullmatch(r'https://[a-z0-9.-]{1,253}', origin):
            raise ValueError('invalid --allow-origin')
    inbox = inbox or phone_inbox.INBOX

    class Handler(BaseHTTPRequestHandler):
        server_version = 'JARVIS-PWA'
        sys_version = ''

        def setup(self):
            self.request.settimeout(10)
            self.t0 = time.monotonic()
            super().setup()

        def log_message(self, *args):
            pass

        def access(self, status, note=''):
            # path, status, time only: never text, audio, tokens or file links
            path = self.path.split('?', 1)[0]
            path = ('/api/file/*' if path.startswith('/api/file/') else path)[:60]
            access_log('%s %s %d %dms%s' % (self.command, path, status, (time.monotonic() - self.t0) * 1000,
                                            ' ' + note if note else ''), getattr(self.server, 'log_path', None))

        def send(self, status, body, ctype='application/json; charset=utf-8', cache='no-store'):
            data = body if isinstance(body, bytes) else (
                body.encode('utf-8') if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode('utf-8'))
            self.access(status)
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', cache)
            self.send_header('Content-Security-Policy', CSP)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.cors()
            self.end_headers()
            self.wfile.write(data)

        def cors(self):
            origin = self.headers.get('Origin', '')
            if origin in allow_origins:
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Access-Control-Expose-Headers', 'Content-Disposition')
                self.send_header('Vary', 'Origin')

        def do_OPTIONS(self):  # CORS preflight for an allowed JARVIS Mobile origin only
            if not self.guard():
                return
            if self.headers.get('Origin', '') not in allow_origins:
                return self.send(403, {'error': 'origin'})
            self.access(204, 'preflight')  # JARVIS Mobile attempts show up even if the real request fails
            self.send_response(204)
            self.cors()
            self.send_header('Access-Control-Allow-Methods', 'GET, POST')
            self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type, X-Filename')
            self.send_header('Access-Control-Max-Age', '600')
            if self.headers.get('Access-Control-Request-Private-Network') == 'true':
                self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def upload(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = -1
            if not 0 < length <= phone_inbox.MAX_UPLOAD:
                return self.send(413, {'error': 'file size 1 byte .. 50 MB'})
            name = urllib.parse.unquote(self.headers.get('X-Filename', ''))[:300]
            data = self.rfile.read(length)
            try:
                saved = phone_inbox.save(data, name, inbox)
            except (OSError, ValueError) as exc:
                ws.journal.record('phone-upload', 'DENIED', reason=type(exc).__name__, rollback=[])
                return self.send(400, {'error': str(exc)[:200]})
            ws.journal.record('phone-upload', 'PASS', name=saved['name'], bytes=saved['bytes'], sha256=saved['sha256'],
                              rollback=[{'action': 'delete-copy', 'path': str(saved['path'])}])
            return self.send(201, {'status': 'PASS', 'saved_as': saved['name'], 'bytes': saved['bytes']})

        def guard(self):
            port_ = self.server.server_port
            allowed = {f'127.0.0.1:{port_}', f'localhost:{port_}', *allow_hosts}
            if self.headers.get('Host') not in allowed:  # DNS-rebinding guard
                self.send(421, {'error': 'host'})
                return False
            return True

        def authed(self):
            got = self.headers.get('Authorization', '')
            presented = got[7:] if got.startswith('Bearer ') else ''
            ok = bool(presented) and ((token and hmac.compare_digest(presented.encode(), token.encode()))
                                      or devices.valid(presented))
            if not ok:
                self.send(401, {'error': 'auth'})
            return ok

        def do_GET(self):
            if not self.guard():
                return
            path = self.path.split('?', 1)[0]
            if path in STATIC:
                name, ctype = STATIC[path]
                return self.send(200, (ASSETS / name).read_bytes(), ctype, 'no-cache')
            if not self.authed():
                return
            if path == '/api/summary':
                return self.send(200, summary(ws, queue))
            if path == '/api/audit':
                entries = [{k: e.get(k) for k in AUDIT_FIELDS} for e in ws.journal.entries()[-30:]]
                return self.send(200, {'status': 'PASS', 'entries': entries[::-1]})
            if path == '/api/queue':
                return self.send(200, {'status': 'PASS', 'items': queue.items()[-30:][::-1]})
            if path.startswith('/api/file/'):
                got = take_outbox(ws.state, path[len('/api/file/'):])
                if got is None:
                    return self.send(410, {'error': 'link expired or already used'})
                name, data = got
                ctype = 'image/png' if name.endswith('.png') else 'application/octet-stream'
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + urllib.parse.quote(name))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.cors()
                self.end_headers()
                return self.wfile.write(data)
            if path == '/api/push/key':
                return self.send(200, {'status': 'PASS', 'key': push.ensure_keys()})
            if path == '/api/briefing':
                try:
                    data = json.loads((ws.state / 'briefing_latest.json').read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    data = {'status': 'NONE', 'text': 'Брифинг ещё не готов.'}
                return self.send(200, data)
            self.send(404, {'error': 'not found'})

        def body(self):
            if 'application/json' not in self.headers.get('Content-Type', ''):
                self.send(415, {'error': 'json required'})
                return None
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY:
                self.send(413, {'error': 'body size'})
                return None
            try:
                data = json.loads(self.rfile.read(length).decode('utf-8'))
            except (ValueError, UnicodeDecodeError):
                self.send(400, {'error': 'bad json'})
                return None
            if not isinstance(data, dict):
                self.send(400, {'error': 'bad json'})
                return None
            return data

        def listen(self):
            if not self.headers.get('Content-Type', '').startswith('audio/'):
                return self.send(415, {'error': 'audio required'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = -1
            if not 0 < length <= MAX_AUDIO:
                return self.send(413, {'error': 'audio size'})
            data = self.rfile.read(length)
            try:
                text = listen(data)
            except BlockingIOError:
                return self.send(429, {'error': 'busy'})
            except Exception as exc:  # undecodable audio, model missing: the phone shows the reason
                access_log('listen error %s (%d bytes, %s)' % (type(exc).__name__, length,
                                                            self.headers.get('Content-Type', '')[:30]),
                           getattr(self.server, 'log_path', None))
                return self.send(400, {'error': 'не удалось распознать: ' + type(exc).__name__})
            return self.send(200, {'status': 'PASS', 'text': text})

        def do_POST(self):
            if not self.guard():
                return
            if self.path == '/api/pair':  # the only unauthenticated write: code -> device token
                data = self.body()
                if data is None:
                    return
                try:
                    pairing.redeem(data.get('code'))
                    name = str(data.get('name') or 'iPhone')[:40]
                    return self.send(201, {'status': 'PASS', 'token': devices.add(name)})
                except (PermissionError, ValueError) as exc:
                    return self.send(403, {'error': str(exc)[:200]})
            if not self.authed():
                return
            if self.path == '/api/listen':
                return self.listen()
            if self.path == '/api/upload':
                return self.upload()
            if self.path == '/api/clientlog':  # the phone reports its own errors (a short fixed label only)
                data = self.body()
                if data is not None:
                    access_log('phone: ' + re.sub(r'[^\w .:/()-]', '?', str(data.get('event', '')))[:120],
                               getattr(self.server, 'log_path', None))
                    self.send(204, b'')
                return
            if self.path not in ('/api/queue', '/api/say', '/api/speech', '/api/push/subscribe'):
                return self.send(404, {'error': 'not found'})
            data = self.body()
            if data is None:
                return
            if self.path == '/api/push/subscribe':
                try:
                    return self.send(200, push.add_subscription(data.get('subscription')))
                except ValueError as exc:
                    return self.send(400, {'error': str(exc)[:200]})
            if self.path == '/api/speech':
                try:
                    if data.get('local') is True:  # JARVIS Mobile: Gemini already refused on the phone
                        return self.send(200, speech(data.get('text'), local=True), 'audio/wav')
                    return self.send(200, speech(data.get('text')), 'audio/wav')
                except (OSError, ValueError) as exc:
                    return self.send(503, {'error': 'voice unavailable: ' + str(exc)[:120]})
            try:
                text = data.get('text')
                if self.path == '/api/say':
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError('text required')
                    return self.send(200, {'status': 'PASS', **station_reply(text, ws, queue, wait=say_wait)})
                result = queue.add(text)
            except (ValueError, AttributeError) as exc:
                return self.send(400, {'error': str(exc)[:200]})
            self.send(201, {'status': 'PASS', **result})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.pairing = pairing
    return server


def main():
    p = argparse.ArgumentParser(description='JARVIS phone PWA gateway (read + queue only)')
    p.add_argument('--workspace', type=Path, required=True)
    p.add_argument('--port', type=int, default=10790)
    p.add_argument('--allow-host', action='append', default=[], help='e.g. mypc.tailnet-name.ts.net')
    p.add_argument('--allow-origin', action='append', default=[], help='JARVIS Mobile page, e.g. https://name.github.io')
    p.add_argument('--revoke-all', action='store_true', help='unpair every phone and exit')
    a = p.parse_args()
    if a.revoke_all:
        Devices(Workspace(a.workspace)).revoke_all()
        print('All paired devices revoked.')
        return
    if not 1024 <= a.port <= 65535:
        raise SystemExit('port must be 1024..65535')
    server = build(a.workspace, os.environ.get('JARVIS_PWA_TOKEN', ''), a.port, a.allow_host, allow_origins=a.allow_origin)
    server.log_path = ACCESS_LOG  # %LOCALAPPDATA%\JARVIS\gateway.log: path, status, ms only
    print('JARVIS PWA on http://127.0.0.1:%d (phone: via private HTTPS proxy only)' % a.port, flush=True)
    print('PAIRING CODE: %s  (10 minutes, single use)' % server.pairing.code, flush=True)
    try:  # the console runs minimized at logon: the owner (or Claude at the owner's request) reads it here
        until = (datetime.datetime.now() + datetime.timedelta(minutes=10)).strftime('%H:%M')
        ACCESS_LOG.with_name('pairing-code.txt').write_text('%s (single use, valid until %s)\n' % (
            server.pairing.code, until), encoding='utf-8')
    except OSError:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
