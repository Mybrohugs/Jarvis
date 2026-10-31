"""A video found on the PC or the phone -> the TV with one button: the YouTube link channel and the PC button.

Any site is played by tv_media.py over Cast; this module is the YouTube fallback and the hotkey.

The TV does not take a local Cast connection from the PC, but its YouTube app is reachable through
YouTube's own "link with TV code" channel (the same one the phone's cast button uses). JARVIS pairs
once with the 12-digit code from YouTube on the TV (Settings -> Link with TV code); the link is kept
with Windows DPAPI in <DEV>/.youtube-tv-dev. After that a YouTube link is reduced to its 11-character
video id (and an optional start time) — nothing else from the link ever leaves the PC — and the TV
plays it. The TV is switched on first if it is in standby.

PC button: `pythonw -m developer_core.jarvis_local.tv_cast --from-browser` (a desktop shortcut with a
hotkey) takes the address of the browser tab in front, or a link in the clipboard, and queues
"на телевизор <link>" for the phone executor, which runs it with the phone allowlist.
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import secrets
import time
import urllib.parse
from pathlib import Path
from .gemini_voice import DEV_ROOT
from .session import Denied

ROOT = DEV_ROOT / '.youtube-tv-dev'
VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}')
YT_HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be', 'www.youtu.be'}
LINK = re.compile(r'https?://[^\s<>"«»]{4,500}', re.I)


def parse_link(url):
    """'https://youtu.be/dQw4w9WgXcQ?t=42' -> ('dQw4w9WgXcQ', 42). Raises Denied for anything else."""
    try:
        u = urllib.parse.urlsplit((url or '').strip())
    except ValueError:
        raise Denied('Это не ссылка') from None
    host = (u.hostname or '').lower()
    if u.scheme not in ('http', 'https') or host not in YT_HOSTS:
        raise Denied('На телевизор я пока отправляю только видео YouTube')
    q = urllib.parse.parse_qs(u.query)
    parts = [p for p in u.path.split('/') if p]
    if host.endswith('youtu.be'):
        vid = parts[0] if parts else ''
    elif parts[:1] == ['watch']:
        vid = (q.get('v') or [''])[0]
    elif len(parts) >= 2 and parts[0] in ('shorts', 'live', 'embed', 'v'):
        vid = parts[1]
    else:
        vid = ''
    if not VIDEO_ID.fullmatch(vid):
        raise Denied('В ссылке нет видео YouTube')
    start = None
    t = (q.get('t') or q.get('start') or [''])[0]
    m = re.fullmatch(r'(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?', t)
    if t and m:
        start = int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0) or None
    return vid, start


def find_link(text):
    m = LINK.search(text or '')
    return m.group(0).rstrip('.,;)!?') if m else None


class AuthStore:
    """The YouTube TV link (screen id + lounge token), DPAPI-protected for this Windows user."""

    def __init__(self, root=ROOT):
        self.path = Path(root) / 'lounge.bin'

    def configured(self):
        return self.path.is_file()

    def save(self, data):
        import win32crypt
        self.path.parent.mkdir(parents=True, exist_ok=True)
        blob = win32crypt.CryptProtectData(json.dumps(data).encode(), 'JARVIS YouTube TV', None, None, None, 1)
        tmp = self.path.with_name('lounge.%s.bin' % secrets.token_hex(6))
        try:
            with tmp.open('xb') as f:
                f.write(blob)
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def load(self):
        if not self.configured():
            raise Denied('Телевизор не привязан: в YouTube на телевизоре откройте «Настройки → Подключить '
                         'телефон → Подключить по коду» и скажите «привяжи телевизор, код …»')
        import win32crypt
        try:
            return json.loads(win32crypt.CryptUnprotectData(self.path.read_bytes(), None, None, None, 1)[1].decode())
        except Exception:
            raise Denied('Не удалось открыть привязку телевизора для этого пользователя Windows') from None


def _lounge():
    from pyytlounge import YtLoungeApi
    return YtLoungeApi('JARVIS')


def _auth_state(data):
    """Normalize the installed wrapper's legacy export for AuthState.deserialize."""
    if not isinstance(data, dict):
        raise Denied('Некорректная привязка YouTube')
    if 'version' in data:
        return dict(data)
    from pyytlounge.models import CURRENT_AUTH_VERSION
    return {'version': CURRENT_AUTH_VERSION, 'screen_id': data.get('screenId'),
            'lounge_id_token': data.get('lounge_id_token'),
            'refresh_token': data.get('refresh_token'), 'expiry': data.get('expiry')}


def _export_auth(api):
    auth = getattr(api, 'auth', None)
    if auth is not None and callable(getattr(auth, 'serialize', None)):
        return dict(auth.serialize())
    return api.store_auth_state()  # alternate clients / test doubles


async def _observe_playback(api, wait):
    observed = {'video_id': None, 'state': 'unknown'}
    ready = asyncio.Event()

    async def updated(state):
        video = getattr(state, 'videoId', '')
        if not isinstance(video, str) or not VIDEO_ID.fullmatch(video):
            return
        name = getattr(getattr(state, 'state', None), 'name', 'unknown').lower()
        observed.update(video_id=video, state=name)
        if name not in ('starting', 'buffering', 'unknown'):
            ready.set()

    listener = asyncio.create_task(api.subscribe(updated))
    completed = asyncio.create_task(ready.wait())
    try:
        done, _ = await asyncio.wait({listener, completed}, timeout=wait,
                                     return_when=asyncio.FIRST_COMPLETED)
        if listener in done:
            listener.result()  # transport errors are not a playback confirmation
        return observed
    finally:
        listener.cancel()
        completed.cancel()
        await asyncio.gather(listener, completed, return_exceptions=True)


class TvCast:
    def __init__(self, store=None, lounge=_lounge, sleep=asyncio.sleep):
        self.store, self.lounge, self.sleep = store or AuthStore(), lounge, sleep

    def pair(self, code):
        code = re.sub(r'\D', '', str(code or ''))
        if len(code) != 12:
            raise Denied('Код привязки — 12 цифр с экрана YouTube на телевизоре')

        async def run():
            async with self.lounge() as api:
                if not await api.pair(code):
                    raise Denied('YouTube не принял код: возьмите новый на телевизоре')
                self.store.save(_export_auth(api))
                return api.screen_name or 'телевизор'
        return asyncio.run(run())

    def status(self, wait=8.0):
        """Observe events: initial empty state after connect is not a playback report."""
        if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not 0 < wait <= 15:
            raise Denied('Invalid status timeout')
        auth = self.store.load()

        async def run():
            async with self.lounge() as api:
                api.load_auth_state(_auth_state(auth))
                if not await api.refresh_auth() or not await api.connect():
                    raise Denied('YouTube не подтвердил подключение')
                return await _observe_playback(api, wait)
        try:
            return asyncio.run(asyncio.wait_for(run(), timeout=wait + 15))
        except Denied:
            raise
        except Exception as exc:
            raise Denied('Не удалось прочитать состояние YouTube: ' + type(exc).__name__) from None

    def play(self, video_id, start=None, wait=30.0):
        """True once the TV accepted the video. Waits up to `wait` s for the YouTube app to come online
        (it needs a few seconds after the TV wakes up)."""
        if not VIDEO_ID.fullmatch(video_id or ''):
            raise Denied('Bad video id')
        auth = self.store.load()

        async def run():
            async with self.lounge() as api:
                api.load_auth_state(_auth_state(auth))
                await api.refresh_auth()
                self.store.save(_export_auth(api))
                deadline = time.monotonic() + wait
                while not await api.is_available():
                    if time.monotonic() > deadline:
                        raise Denied('YouTube на телевизоре не отвечает: откройте его один раз пультом')
                    await self.sleep(2)
                if not await api.connect():
                    raise Denied('Не удалось подключиться к YouTube на телевизоре')
                ok = await api.play_video(video_id)
                if ok and start:
                    await self.sleep(3)
                    await api.seek_to(float(start))
                return ok
        return asyncio.run(run())


# ---- PC button --------------------------------------------------------------------------------------
BROWSERS = re.compile(r'^(chrome|msedge|firefox|opera|browser|yandex|brave|vivaldi)$', re.I)


def _foreground_browser():
    import ctypes
    from ctypes import wintypes
    import psutil
    user32 = ctypes.windll.user32
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    try:
        return bool(BROWSERS.match(Path(psutil.Process(pid.value).name()).stem))
    except Exception:
        return False


def _clipboard_text():
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
    except TypeError:
        return ''
    finally:
        win32clipboard.CloseClipboard()


def _set_clipboard(text):
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        if text:
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def browser_link():
    """The address of the browser tab in front (Ctrl+L, Ctrl+C, Esc; the clipboard is put back),
    else a link already in the clipboard."""
    before = _clipboard_text()
    if _foreground_browser():
        import keyboard
        keyboard.send('ctrl+l')
        time.sleep(0.15)
        keyboard.send('ctrl+c')
        time.sleep(0.25)
        keyboard.send('esc')
        url = find_link(_clipboard_text())
        _set_clipboard(before)
        if url:
            return url
    return find_link(before)


def main(argv=None):
    import argparse
    from .pwa_gateway import PhoneQueue
    from .workspace import Workspace
    p = argparse.ArgumentParser(description='Send the video in the browser (or a copied link) to the TV')
    p.add_argument('--workspace', default=str(Path.home() / 'Documents' / 'Codex' / 'UFO-Research' / 'FunctionalWorkspace'))
    p.add_argument('--from-browser', action='store_true')
    p.add_argument('--status', action='store_true', help='read YouTube playback without sending a video')
    p.add_argument('url', nargs='?')
    a = p.parse_args(argv)
    if a.status:
        try:
            print(json.dumps(TvCast().status(), ensure_ascii=False))
            return 0
        except Denied as exc:
            print(str(exc))
            return 1
    url = a.url or (browser_link() if a.from_browser else None)
    if not url:
        _toast('Не нашёл ссылку: откройте видео в браузере или скопируйте ссылку')
        return 1
    try:
        from .tv_media import check_url
        check_url(url)
    except Denied as exc:
        _toast(str(exc))
        return 1
    PhoneQueue(Workspace(Path(a.workspace))).add('на телевизор ' + url, interpret=True)
    _toast('Отправляю на телевизор…')
    return 0


def _toast(text):
    try:
        import ctypes
        ctypes.windll.user32.MessageBeep(0x40)
    except Exception:
        pass
    print(text)


if __name__ == '__main__':
    raise SystemExit(main())
