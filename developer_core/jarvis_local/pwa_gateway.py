"""Phone PWA gateway (iPhone home-screen app). A REMOTE surface: read + queue only.

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
from .push import Push
from .workspace import Workspace

ASSETS = Path(__file__).with_name('pwa')
STATIC = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
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
MAX_BODY = 2048
MAX_REQUEST = 500
MAX_PENDING = 50


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
            record['speech'] = str(speech)[:400]
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
    if re.search(r'что на экране|скриншот|снимок экрана', t):
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


def fetch_speech(text, port=VOICE_PORT, timeout=60):
    """Ask the local voice server (user's own voice, stark-metal-2) for a WAV. Sound only."""
    import http.client
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 400:
        raise ValueError('text must be 1..400 characters')
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        c.request('POST', '/speak', body=json.dumps({'text': text.strip()}, ensure_ascii=False).encode('utf-8'),
                  headers={'Content-Type': 'application/json', 'Host': '127.0.0.1:%d' % port})
        r = c.getresponse()
        data = r.read(8_000_001)
        if r.status != 200 or not data.startswith(b'RIFF') or len(data) > 8_000_000:
            raise OSError('voice server error %d' % r.status)
        return data
    finally:
        c.close()


def plural(n, one, few, many):
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def station_reply(text, ws, queue, wait=5.0):
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
    for _ in range(int(wait * 2 / 0.25)):  # LLM planning takes a few seconds
        got = queue.get(item['id'])
        if got and (got['status'] != 'pending' or got.get('speech')):
            return {'intent': 'request', 'queued': got['status'] == 'pending', 'id': item['id'],
                    'status': got['status'], 'speech': got['speech'] or 'Готово.'}
        time.sleep(0.25)
    return {'intent': 'request', 'queued': True, 'id': item['id'],
            'speech': 'Записал. Выполню после подтверждения за компьютером.'}


def build(workspace, token, port=0, allow_hosts=(), speech=fetch_speech, say_wait=5.0):
    """token: optional master key (Siri Shortcut / scripts); phones normally pair by code."""
    if token and len(token) < 32:
        raise ValueError('JARVIS_PWA_TOKEN must be at least 32 characters')
    ws = Workspace(workspace)
    queue = PhoneQueue(ws)
    devices = Devices(ws)
    pairing = Pairing()
    push = Push(ws)
    for host in allow_hosts:
        if not re.fullmatch(r'[a-z0-9.-]{1,253}', host):
            raise ValueError('invalid --allow-host')

    class Handler(BaseHTTPRequestHandler):
        server_version = 'JARVIS-PWA'
        sys_version = ''

        def setup(self):
            self.request.settimeout(10)
            super().setup()

        def log_message(self, *args):
            pass

        def send(self, status, body, ctype='application/json; charset=utf-8', cache='no-store'):
            data = body if isinstance(body, bytes) else (
                body.encode('utf-8') if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode('utf-8'))
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', cache)
            self.send_header('Content-Security-Policy', CSP)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(data)

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
    p.add_argument('--revoke-all', action='store_true', help='unpair every phone and exit')
    a = p.parse_args()
    if a.revoke_all:
        Devices(Workspace(a.workspace)).revoke_all()
        print('All paired devices revoked.')
        return
    if not 1024 <= a.port <= 65535:
        raise SystemExit('port must be 1024..65535')
    server = build(a.workspace, os.environ.get('JARVIS_PWA_TOKEN', ''), a.port, a.allow_host)
    print('JARVIS PWA on http://127.0.0.1:%d (phone: via private HTTPS proxy only)' % a.port, flush=True)
    print('PAIRING CODE: %s  (10 minutes, single use)' % server.pairing.code, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
