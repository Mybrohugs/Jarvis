"""Explicit video-link handoff to one locally configured Google Cast TV.

The TV fetches media itself. No browser cookies, downloads or screen mirroring.
YouTube uses its Cast application; direct MP4/WebM/HLS uses Default Media Receiver.
"""
from __future__ import annotations
import ipaddress
import json
import re
import threading
from urllib.parse import urlsplit, parse_qs
from .session import Denied


def video_command(text):
    match = re.fullmatch(r'(?:покажи|включи|отправь|передай)\s+(?:видео\s+)?на\s+(?:тв|телевизор)\s+(https?://\S+)',
                         text.strip(), re.I)
    if match:
        video_request(match.group(1))
        return match.group(1)
    return None


def video_request(url):
    if not isinstance(url, str) or not 1 <= len(url) <= 2000 or any(c.isspace() or ord(c) < 32 for c in url):
        raise ValueError('Нужна ссылка на видео длиной до 2000 символов')
    try:
        p = urlsplit(url)
        host = (p.hostname or '').lower()
        port = p.port
    except ValueError:
        raise ValueError('Некорректная ссылка') from None
    if p.scheme not in ('http', 'https') or not host or p.username or p.password or port not in (None, 80, 443):
        raise ValueError('Нужна общедоступная HTTP(S)-ссылка без пароля')
    if host == 'localhost' or host.endswith(('.local', '.localhost', '.internal', '.ts.net')) or '.' not in host:
        raise ValueError('Нужна интернет-ссылка, а не адрес локального устройства')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError('Локальные адреса не поддерживаются')
    if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'):
        if host == 'youtu.be':
            video_id = p.path.strip('/')
        elif p.path == '/watch':
            video_id = parse_qs(p.query).get('v', [''])[0]
        else:
            match = re.fullmatch(r'/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})/?', p.path)
            video_id = match.group(1) if match else ''
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise ValueError('Нужна ссылка на конкретное видео YouTube')
        return {'app': 'youtube', 'media_id': video_id, 'source_host': host}
    mime = {'.mp4': 'video/mp4', '.webm': 'video/webm', '.m3u8': 'application/x-mpegURL'}
    ext = next((e for e in mime if p.path.lower().endswith(e)), None)
    if ext is None:
        raise ValueError('Поддерживаются YouTube и прямые ссылки MP4, WebM, M3U8; ссылка на страницу сайта не подходит')
    return {'app': 'default_media_receiver', 'media_id': url, 'media_type': mime[ext], 'source_host': host}


def cast_video(name, request):
    try:
        import pychromecast
        from pychromecast.quick_play import quick_play
    except ImportError:
        raise Denied('Установите pychromecast в Python JARVIS: python -m pip install pychromecast') from None
    try:
        casts, browser = pychromecast.get_listed_chromecasts(
            friendly_names=[name], discovery_timeout=5, tries=1, timeout=5)
    except Exception:
        raise Denied('Не удалось найти ТВ Google Cast в сети ПК') from None
    try:
        if len(casts) != 1:
            raise Denied('ТВ не найден однозначно. Проверьте имя Cast, питание и общую сеть с ПК')
        cast = casts[0]
        cast.wait(timeout=8)
        data = {k: request[k] for k in ('media_id', 'media_type') if k in request}
        quick_play(cast, request['app'], data, timeout=10)
    except Denied:
        raise
    except Exception:
        # Exceptions from Cast may contain signed media URLs. Keep them out of logs.
        raise Denied('Телевизор не подтвердил запуск Cast. Проверьте экран и доступность видео') from None
    finally:
        for cast in casts:
            try:
                cast.disconnect()
            except Exception:
                pass
        browser.stop_discovery()


class VideoCaster:
    _lock = threading.Lock()

    def __init__(self, ws, transport=cast_video):
        self.ws, self.transport = ws, transport
        self.path = ws.state / 'tv_cast.json'

    def configure(self, name, session):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100 or any(ord(c) < 32 for c in name):
            raise ValueError('Укажите точное имя телевизора Google Cast')
        approved = session.confirm({'task': 'media-cast-config', 'device': name.strip()})
        self.path.write_text(json.dumps({'name': name.strip()}, ensure_ascii=False), encoding='utf-8')
        self.ws.journal.record('media-cast-config', 'PASS', device=name.strip(), rollback=[], **approved)
        return {'status': 'PASS', 'device': name.strip()}

    def play(self, url, session):
        request = video_request(url)
        try:
            name = json.loads(self.path.read_text(encoding='utf-8'))['name']
        except (OSError, ValueError, KeyError, TypeError):
            raise Denied('Сначала задайте имя ТВ на ПК командой media-cast-config') from None
        if not isinstance(name, str) or not 1 <= len(name) <= 100:
            raise Denied('Некорректная настройка имени ТВ')
        approved = session.confirm({'task': 'media-cast', 'device': name, 'source': request['source_host']})
        if not self._lock.acquire(blocking=False):
            raise Denied('Предыдущее видео ещё передаётся на ТВ, повторите позже')
        try:
            self.transport(name, request)
        finally:
            self._lock.release()
        entry = self.ws.journal.record('media-cast', 'REQUESTED', device=name, source=request['source_host'],
                                       app=request['app'], rollback=[], **approved)
        return {'status': 'REQUESTED', 'speech': 'Видео передано приложению ТВ. Проверьте воспроизведение на экране.',
                'audit_id': entry['id']}
