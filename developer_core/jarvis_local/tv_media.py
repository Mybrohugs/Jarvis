"""Any web video -> the TV over Google Cast (the TV's built-in Chromecast).

  * YouTube: the TV's own YouTube app gets the video id (best quality, the app handles it);
  * anything else (Rutube, VK, news sites, direct .mp4/.m3u8 ...): yt-dlp finds the video in the page,
    ffmpeg copies it (no re-encoding) into a short-segment HLS stream in <state>/tvrelay/<token>/ and a
    small HTTP server on the PC's home-network address hands it to the TV. Relaying through the PC means
    sites that only open through the PC's VPN, links bound to the PC's IP and split video/audio all work.
Only one relay runs at a time; a new video stops the old one. The server serves only files of a live
relay, under a random token, and only on the LAN address.
"""
from __future__ import annotations
import functools
import http.server
import ipaddress
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path
from .session import Denied

RELAY_PORT = 10795
PLAYING = ('PLAYING', 'BUFFERING')
DEFAULT_RECEIVER = 'CC1AD845'  # Google's Default Media Receiver
FFMPEG_HINTS = (Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'WinGet' / 'Packages',)


def check_url(url):
    """A public http(s) page or video. Never a LAN/loopback address: the phone must not make the PC
    fetch from the home network."""
    try:
        u = urllib.parse.urlsplit((url or '').strip())
    except ValueError:
        raise Denied('Это не ссылка') from None
    host = (u.hostname or '').lower()
    if u.scheme not in ('http', 'https') or not host or len(url) > 500:
        raise Denied('Нужна ссылка http(s) на страницу с видео')
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            raise Denied('Ссылки на адреса домашней сети не открываю')
    except ValueError:
        if host == 'localhost' or host.endswith(('.local', '.lan', '.home', '.internal')):
            raise Denied('Ссылки на адреса домашней сети не открываю') from None
    return url.strip()


def lan_ip(tv_host):
    """The PC's address on the network the TV is in (the route the OS would use to reach it)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((tv_host, 9))
        return s.getsockname()[0]
    finally:
        s.close()


def find_ffmpeg():
    found = shutil.which('ffmpeg')
    if found:
        return found
    for root in FFMPEG_HINTS:
        for p in sorted(root.glob('*FFmpeg*/**/bin/ffmpeg.exe')) if root.is_dir() else []:
            return str(p)
    raise Denied('Нет ffmpeg: установите его (winget install Gyan.FFmpeg)')


def resolve(url):
    """yt-dlp: page -> (title, [inputs], headers). Two inputs when video and audio are separate."""
    import yt_dlp
    opts = {'quiet': True, 'no_warnings': True, 'skip_download': True, 'noplaylist': True, 'socket_timeout': 15,
            'format': 'bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/b[height<=1080]/bv*+ba/b'}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise Denied('Не нашёл видео на этой странице: %s' % str(exc).splitlines()[0][:120]) from None
    if info.get('_type') == 'playlist' and info.get('entries'):
        info = next(e for e in info['entries'] if e)
    parts = info.get('requested_formats') or [info]
    inputs = [p['url'] for p in parts if p.get('url')]
    if not inputs:
        raise Denied('Не нашёл видео на этой странице')
    headers = dict(parts[0].get('http_headers') or info.get('http_headers') or {})
    return (info.get('title') or 'Видео')[:120], inputs, headers


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')  # the Cast receiver fetches with CORS
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404)


def _aia_intermediate(host):
    """Complete a chain the server sends incomplete (a CDN that omits its intermediate certificate),
    the way browsers do: read the presented certificate, fetch the issuer named in its AIA field and
    keep it ONLY if the full chain then verifies against the system roots for this host name.
    Returns the intermediate as PEM, or None. Nothing is trusted that does not verify."""
    import datetime
    import ssl
    import urllib.request
    import certifi
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding
    from cryptography.x509.verification import PolicyBuilder, Store
    leaf = x509.load_pem_x509_certificate(ssl.get_server_certificate((host, 443), timeout=8).encode())
    try:
        aia = leaf.extensions.get_extension_for_class(x509.AuthorityInformationAccess).value
    except x509.ExtensionNotFound:
        return None
    urls = [a.access_location.value for a in aia if a.access_method == x509.AuthorityInformationAccessOID.CA_ISSUERS]
    if not urls or not urls[0].lower().startswith(('http://', 'https://')):
        return None
    raw = urllib.request.urlopen(urls[0], timeout=8).read(65536)
    inter = x509.load_der_x509_certificate(raw) if raw[:1] == b'\x30' else x509.load_pem_x509_certificate(raw)
    import warnings
    with warnings.catch_warnings():  # one legacy root in certifi has a non-positive serial
        warnings.simplefilter('ignore')
        roots = x509.load_pem_x509_certificates(Path(certifi.where()).read_bytes())
    verifier = PolicyBuilder().store(Store(roots)).time(datetime.datetime.now(datetime.timezone.utc)) \
        .build_server_verifier(x509.DNSName(host))
    verifier.verify(leaf, [inter])  # raises unless leaf -> intermediate -> trusted root, for this host
    return inter.public_bytes(Encoding.PEM).decode()


def ca_bundle(path, hosts):
    """System roots plus verified intermediates for hosts whose servers omit them; written for ffmpeg."""
    import certifi
    pem = Path(certifi.where()).read_text(encoding='ascii')
    for host in sorted(set(hosts)):
        try:
            extra = _aia_intermediate(host)
        except Exception:  # unreachable, no AIA, or the chain does not verify: add nothing
            extra = None
        if extra:
            pem += '\n' + extra
    Path(path).write_text(pem, encoding='ascii')
    return str(path)


class RelayError(Denied):
    def __init__(self, reason):
        super().__init__('Не удалось подготовить видео для телевизора' +
                         (' (сертификат сайта не прошёл проверку)' if reason == 'certificate' else
                          ' (локальный HTTP-релей не выдаёт поток)' if reason == 'http' else ''))
        self.reason = reason


class Relay:
    """ffmpeg -> HLS in <state>/tvrelay/<token>/, served on <lan ip>:RELAY_PORT."""

    def __init__(self, state, host_ip, ffmpeg=None, popen=subprocess.Popen, port=None):
        self.root = Path(state) / 'tvrelay'
        self.host_ip, self.ffmpeg, self.popen = host_ip, ffmpeg, popen
        self.port = RELAY_PORT if port is None else port
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise Denied('Invalid relay port')
        self.proc = self.server = None

    def _serve(self):
        if self.server is None:
            self.root.mkdir(parents=True, exist_ok=True)
            self.root = self.root.resolve(strict=True)
            handler = functools.partial(_Handler, directory=str(self.root))
            self.server = http.server.ThreadingHTTPServer((self.host_ip, self.port), handler)
            threading.Thread(target=self.server.serve_forever, daemon=True, name='tv-relay').start()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None
        if self.root.is_dir():
            for old in self.root.iterdir():
                shutil.rmtree(old, ignore_errors=True)

    def start(self, inputs, headers, wait=25.0, sleep=time.sleep, ca_file=None):
        self.stop()
        self._serve()
        token = secrets.token_urlsafe(16)
        out = self.root / token
        out.mkdir(parents=True)
        cmd = [self.ffmpeg or find_ffmpeg(), '-hide_banner', '-loglevel', 'error', '-nostdin']
        hdr = ''.join('%s: %s\r\n' % (k, v) for k, v in headers.items() if k.lower() in ('user-agent', 'referer', 'cookie', 'origin'))
        for src in inputs:
            cmd += (['-headers', hdr] if hdr else []) + (['-ca_file', ca_file] if ca_file else []) + ['-i', src]
        cmd += ['-map', '0:v:0?', '-map', ('1:a:0?' if len(inputs) > 1 else '0:a:0?'), '-c', 'copy',
                '-f', 'hls', '-hls_time', '4', '-hls_list_size', '0', '-hls_playlist_type', 'event',
                '-hls_segment_filename', str(out / 's%05d.ts'), str(out / 'index.m3u8')]
        with (out / 'ffmpeg.log').open('wb') as log:
            self.proc = self.popen(cmd, stdout=subprocess.DEVNULL, stderr=log,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        end = time.monotonic() + wait
        while len(list(out.glob('s*.ts'))) < 2:  # the TV needs a couple of segments to start
            if self.proc.poll() is not None or time.monotonic() > end:
                try:
                    why = (out / 'ffmpeg.log').read_text(encoding='utf-8', errors='replace').lower()
                except OSError:
                    why = ''
                self.stop()
                raise RelayError('certificate' if 'certificate' in why else 'ffmpeg')
            sleep(0.5)
        url = 'http://%s:%d/%s/index.m3u8' % (self.host_ip, self.port, token)
        try:
            import http.client
            connection = http.client.HTTPConnection(self.host_ip, self.port, timeout=5)
            try:
                connection.request('GET', '/' + token + '/index.m3u8')
                response = connection.getresponse()
                valid = response.status == 200 and response.read(65536).startswith(b'#EXTM3U')
            finally:
                connection.close()
            if not valid:
                raise OSError('relay preflight failed')
        except OSError:
            self.stop()
            raise RelayError('http') from None
        return url


class CastTv:
    def __init__(self, host, connect=None):
        self.host, self._connect = host, connect

    def _cast(self):
        if self._connect:
            return self._connect(self.host)
        import pychromecast
        cc = pychromecast.get_chromecast_from_host((self.host, 8009, None, None, None), tries=2, timeout=8)
        cc.wait(timeout=10)
        return cc

    def youtube(self, video_id, start=None):
        from pychromecast.quick_play import quick_play
        cc = self._cast()
        try:
            data = {'media_id': video_id}
            if start:
                data['start_time'] = start
            quick_play(cc, 'youtube', data, timeout=20)
            return True
        finally:
            cc.disconnect(timeout=5)

    def media(self, url, title, wait=20.0, sleep=time.sleep):
        """True only once the TV's player reports THIS stream playing: while another app (YouTube)
        is in front, its own "PLAYING" must not count, and play_media alone does not take over."""
        cc = self._cast()
        try:
            cc.start_app(DEFAULT_RECEIVER, force_launch=True, timeout=20)
            end = time.monotonic() + min(15.0, wait)
            while cc.status.app_id != DEFAULT_RECEIVER and time.monotonic() < end:
                sleep(0.5)
            mc = cc.media_controller
            mc.play_media(url, 'application/vnd.apple.mpegurl', title=title, stream_type='BUFFERED')
            end = time.monotonic() + wait
            while time.monotonic() < end:
                st = mc.status
                if st and st.content_id == url:
                    if st.player_state in PLAYING:
                        return True
                    if st.player_state == 'IDLE' and st.idle_reason == 'ERROR':
                        return False
                sleep(1)
            return False
        finally:
            cc.disconnect(timeout=5)


class TvMedia:
    """Router-side: one object per process keeps the relay alive while the TV plays."""

    def __init__(self, state, tv_host, cast=None, relay=None, resolver=resolve):
        self.state, self.tv_host, self.resolver = state, tv_host, resolver
        self.cast = cast or CastTv(tv_host)
        self._relay = relay

    @property
    def relay(self):
        if self._relay is None:
            self._relay = Relay(self.state, lan_ip(self.tv_host))
        return self._relay

    def play(self, url):
        from .tv_cast import parse_link
        try:
            vid, start = parse_link(url)
        except Denied:
            vid = None
        if vid:
            self.cast.youtube(vid, start)
            return {'kind': 'youtube', 'video': vid, 'title': 'YouTube'}
        title, inputs, headers = self.resolver(check_url(url))
        try:
            stream = self.relay.start(inputs, headers)
        except RelayError as exc:
            if exc.reason != 'certificate':
                raise
            # the CDN omitted its intermediate certificate: complete and verify the chain, check stays on
            bundle = ca_bundle(Path(self.state) / 'tv_ca.pem', [urllib.parse.urlsplit(i).hostname for i in inputs])
            stream = self.relay.start(inputs, headers, ca_file=bundle)
        if not self.cast.media(stream, title):
            self.relay.stop()
            raise Denied('Телевизор не смог открыть это видео')
        return {'kind': 'relay', 'title': title, 'host': urllib.parse.urlsplit(url).hostname}
