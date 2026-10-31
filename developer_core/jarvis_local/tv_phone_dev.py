"""Isolated phone TV preview: existing gateway/queue/executor, private HTTPS upstream.

Does not edit Tailscale, restart existing services or run scheduled actions.
"""
from __future__ import annotations
import argparse
import datetime
import http.client
import json
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from .pwa_gateway import build
from .phone_executor import PhoneExecutor
from .router import UnifiedTaskRouter
from .session import TrustedLocalSession
from .intent import understand

FILES = frozenset(('connect.html', 'connect.js', 'connect.css', 'index.html', 'main.js', 'pc.js', 'tv.js', 'brain.js', 'voice.js',
    'store.js', 'tv_ui.js', 'google.js', 'orb.js', 'app.css', 'mobile.css', 'sw.js', 'manifest.webmanifest',
    'icon-180.png', 'icon-512.png', 'fonts/exo2-cyrillic.woff2', 'fonts/exo2-latin.woff2'))
API = {'GET': {'/api/summary', '/api/queue'}, 'POST': {'/api/pair', '/api/say', '/api/listen', '/api/speech'}}


def build_preview(mobile, gateway, *, port=10797, public_host, diagnostics=None, chat=None):
    from .phone_chatgpt import ChatGPT
    chat = chat or ChatGPT()
    mobile = Path(mobile).resolve(strict=True)
    origin = 'https://' + public_host
    bare_host = public_host.split(':', 1)[0]
    alias_origin = 'https://' + bare_host
    event_lock = threading.Lock()
    def event(kind, status):
        if diagnostics is None:
            return
        record = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  'event': kind, 'status': status}
        try:
            with event_lock, Path(diagnostics).open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record) + '\n')
        except OSError:
            pass
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # no request bodies, tokens or pairing codes in access logs

        def send(self, status, data, content_type='application/json'):
            if not isinstance(data, bytes):
                data = json.dumps(data).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(data)

        def guard(self):
            allowed = {public_host, bare_host, bare_host + ':443', '127.0.0.1:%d' % self.server.server_port,
                       'localhost:%d' % self.server.server_port}
            if self.headers.get('Host') not in allowed:
                self.send(421, {'error': 'host'})
                return False
            if self.headers.get('Origin') not in (None, origin, alias_origin):
                self.send(403, {'error': 'origin'})
                return False
            return True

        def chat_api(self, method, path):
            # Reuse the existing gateway's device authentication, including revocation.
            connection = http.client.HTTPConnection('127.0.0.1', gateway.server_port, timeout=5)
            try:
                connection.request('GET', '/api/summary', headers={
                    'Host':'127.0.0.1:%d' % gateway.server_port,
                    'Authorization':self.headers.get('Authorization','')})
                response = connection.getresponse(); response.read()
                if response.status != 200:
                    return self.send(401, {'error':'auth'})
            except OSError:
                return self.send(503, {'error':'DEV gateway unavailable'})
            finally:
                connection.close()
            if method == 'GET' and path == '/api/chatgpt/status':
                return self.send(200, chat.status())
            if method != 'POST' or path != '/api/chatgpt/turn':
                return self.send(404, {'error':'not found'})
            try:
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= 140000 or self.headers.get('Transfer-Encoding'):
                    return self.send(413, {'error':'body size'})
                self.connection.settimeout(15)
                data = json.loads(self.rfile.read(size))
                result = chat.turn(data)
                return self.send(200, result)
            except (ValueError, TypeError):
                return self.send(400, {'error':'Некорректный или слишком длинный запрос чата.'})
            except RuntimeError as exc:
                return self.send(503, {'error':str(exc)})
            except Exception:
                return self.send(503, {'error':'ChatGPT временно недоступен.'})

        def proxy(self, method, path):
            if path.startswith('/api/chatgpt/'):
                return self.chat_api(method, path)
            if path not in API.get(method, set()):
                return self.send(404, {'error': 'not available in TV DEV preview'})
            data = None
            if method == 'POST':
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    size = 0
                if not 0 < size <= (6000000 if path == '/api/listen' else 4096) or self.headers.get('Transfer-Encoding'):
                    return self.send(413, {'error': 'body size'})
                data = self.rfile.read(size)
                if path == '/api/say':
                    try:
                        request = json.loads(data)
                        plan = understand(request.get('text', ''))
                        steps = plan.get('steps') or []
                        if len(steps) != 1 or steps[0].get('task') != 'tv-play':
                            raise ValueError()
                    except (ValueError, TypeError, AttributeError):
                        return self.send(400, {'error': 'DEV preview accepts only video-to-TV requests'})
            connection = http.client.HTTPConnection('127.0.0.1', gateway.server_port, timeout=90)
            try:
                connection.request(method, path, body=data, headers={
                    'Host': '127.0.0.1:%d' % gateway.server_port,
                    'Authorization': self.headers.get('Authorization', ''),
                    'Content-Type': 'application/json'})
                response = connection.getresponse()
                body = response.read(8_000_001)
                if len(body) > 8_000_000:
                    return self.send(502, {'error': 'upstream too large'})
                if path == '/api/pair':
                    try:
                        message = json.loads(body).get('error', '')
                    except (ValueError, AttributeError):
                        message = ''
                    kind = ('paired' if response.status == 201 else
                            'pairing_closed' if 'pairing closed' in str(message) else
                            'wrong_code' if 'wrong code' in str(message) else 'pairing_failed')
                    event(kind, response.status)
                return self.send(response.status, body, response.getheader('Content-Type', 'application/json'))
            except OSError:
                return self.send(502, {'error': 'DEV gateway unavailable'})
            finally:
                connection.close()

        def do_GET(self):
            if not self.guard():
                return
            path = urlsplit(self.path).path
            if path.startswith('/jarvis-dev/'):
                path = path[len('/jarvis-dev'):]
            if path == '/_dev/health':
                return self.send(200, {'dev': True, 'tv_only': True, 'chatgpt': True})
            if path.startswith('/api/'):
                return self.proxy('GET', path)
            name = 'index.html' if path == '/' else path.lstrip('/')
            if name not in FILES:
                return self.send(404, {'error': 'not found'})
            asset = mobile / name
            if asset.is_symlink() or not asset.resolve().is_relative_to(mobile) or not asset.is_file():
                return self.send(404, {'error': 'not found'})
            mime = {'.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
                    '.html': 'text/html; charset=utf-8', '.webmanifest': 'application/manifest+json'}.get(
                        asset.suffix, mimetypes.guess_type(str(asset))[0] or 'application/octet-stream')
            if name == 'connect.html':
                event('connect_page', 200)
            return self.send(200, asset.read_bytes(), mime)

        def do_POST(self):
            if self.guard():
                return self.proxy('POST', self.path[len('/jarvis-dev'):] if self.path.startswith('/jarvis-dev/') else self.path)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mobile', type=Path, required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=10797)
    args = parser.parse_args()
    import re
    if not re.fullmatch(r'[a-z0-9.-]+\.ts\.net:8443', args.host):
        parser.error('expected private Tailscale host with :8443')
    root = Path(__file__).resolve().parents[2] / '.tv-phone-dev'
    workspace = root / 'workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    gateway = build(workspace, '', 0, say_wait=.5)
    router = UnifiedTaskRouter(TrustedLocalSession.phone_allowlist(('tv-play',)), workspace)
    executor = PhoneExecutor(workspace, router=router)
    # Prepare this DEV instance's relay under the same canonical workspace.
    from .tv_media import Relay, lan_ip
    media = router.tv_media
    from .tv_web_video import resolve_with_browser
    media.resolver = resolve_with_browser
    relay = Relay(media.state, lan_ip(media.tv_host), port=10799)
    media._relay = relay  # dedicated DEV port; never compete with the existing 10795 relay
    relay._serve()
    (root / 'relay-status.json').write_text(json.dumps({
        'root': str(relay.root), 'http_root': relay.server.RequestHandlerClass.keywords['directory'],
        'bind': relay.server.server_address}), encoding='utf-8')
    preview = build_preview(args.mobile, gateway, port=args.port, public_host=args.host,
                            diagnostics=root / 'pairing-events.jsonl')
    stopped = threading.Event()
    def tick():
        while not stopped.wait(.25):
            try:
                executor.tick()
            except Exception as exc:
                print('DEV executor error:', type(exc).__name__, flush=True)
    threads = [threading.Thread(target=gateway.serve_forever, daemon=True),
               threading.Thread(target=tick, daemon=True)]
    for thread in threads:
        thread.start()
    status = {'pid': os.getpid(), 'url': 'https://' + args.host,
              'phone_url': 'https://' + args.host.split(':', 1)[0] + '/jarvis-dev',
              'pairing_code': gateway.pairing.code,
              'pairing_expires': (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10)).isoformat(),
              'port': preview.server_port, 'workspace': str(workspace)}
    (root / 'status.json').write_text(json.dumps(status), encoding='utf-8')
    print('TV phone DEV ready on loopback port', preview.server_port, flush=True)
    try:
        preview.serve_forever()
    finally:
        stopped.set()
        preview.server_close()
        gateway.shutdown()
        gateway.server_close()
        relay.stop()
        if relay.server:
            relay.server.shutdown()
            relay.server.server_close()
        for thread in threads:
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
