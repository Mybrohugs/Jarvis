"""Yandex Station through the owner's Home Assistant (integration yandex_station, 127.0.0.1:8123).

Say text in the speaker's own voice, pass a command to Alice, volume 0..10, pause/play/next/previous.
Only the station entity found in HA (media_player.yandex_station_*) is ever touched, only through
the fixed services below. The HA long-lived token is stored with Windows DPAPI in <DEV>/.home-assistant-dev:
  python -m developer_core.jarvis_local.station set-token --from-clipboard
  python -m developer_core.jarvis_local.station status
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from .gemini_voice import DEV_ROOT, KeyStore, take_clipboard
from .session import Denied

BASE = 'http://127.0.0.1:8123'
ROOT = DEV_ROOT / '.home-assistant-dev'
CONTROLS = {'pause': 'media_pause', 'play': 'media_play', 'next': 'media_next_track', 'previous': 'media_previous_track'}


def keys():
    return KeyStore(ROOT, name='ha-token', label='JARVIS Home Assistant DEV', service='Home Assistant')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a):
        return None


def _http(method, path, body, token):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=None if body is None else json.dumps(body).encode('utf-8'),
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as r:
            raw = r.read(2_000_001)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise Denied('Home Assistant не принял токен: создайте новый (station set-token)') from None
        raise Denied('Home Assistant HTTP %d' % exc.code) from None
    except (OSError, ValueError) as exc:
        raise Denied('Home Assistant не отвечает: ' + type(exc).__name__) from None
    if len(raw) > 2_000_000:
        raise Denied('Home Assistant response too large')
    return json.loads(raw) if raw else None


class Station:
    def __init__(self, http=_http, token=None, sleep=time.sleep):
        self.http, self._token, self.sleep, self._entity = http, token, sleep, None

    def _call(self, method, path, body=None):
        if self._token is None:
            self._token = keys().load()
        return self.http(method, path, body, self._token)

    def all_states(self):
        return self._call('GET', '/api/states') or []

    def entity(self):
        if self._entity is None:
            found = [s['entity_id'] for s in self.all_states()
                     if isinstance(s, dict) and re.fullmatch(r'media_player\.yandex_station_[a-z0-9_]{1,80}', str(s.get('entity_id')))]
            if not found:
                raise Denied('Колонка не найдена в Home Assistant: добавьте интеграцию Yandex Station')
            self._entity = sorted(found)[0]
        return self._entity

    def state(self):
        raw = self._call('GET', '/api/states/' + self.entity())
        a = raw.get('attributes') or {}
        vol = a.get('volume_level')
        return {'state': raw.get('state'), 'volume': round(vol * 10) if isinstance(vol, (int, float)) else None,
                'title': a.get('media_title') or '', 'artist': a.get('media_artist') or ''}

    def _service(self, service, **extra):
        body = {'entity_id': self.entity(), **extra}
        try:
            self._call('POST', '/api/services/media_player/' + service, body)
        except Denied as exc:  # HA answers 500 when its DNS lookup of Yandex times out now and then: one retry
            if 'HTTP 500' not in str(exc):
                raise
            self.sleep(2)
            self._call('POST', '/api/services/media_player/' + service, body)

    def say(self, text):
        self._service('play_media', media_content_id=text, media_content_type='text')

    def say_long(self, text, limit=100):
        """Cloud TTS takes ~100 characters per phrase: speak sentence chunks one after another."""
        chunks, cur = [], ''
        for part in re.split(r'(?<=[.!?;])\s+', ' '.join(text.split())):
            while len(part) > limit:  # one very long sentence: cut at a space
                cut = part.rfind(' ', 0, limit)
                cut = cut if cut > 0 else limit
                chunks, part = chunks + ([cur] if cur else []) + [part[:cut]], part[cut:].strip()
                cur = ''
            if cur and len(cur) + 1 + len(part) > limit:
                chunks, cur = chunks + [cur], part
            else:
                cur = (cur + ' ' + part).strip()
        chunks += [cur] if cur else []
        for i, chunk in enumerate(chunks):
            if i:
                self.sleep(len(chunks[i - 1]) / 14 + 0.7)  # ~14 characters a second, so a chunk is not cut off
            self.say(chunk)
        return chunks

    def command(self, text):
        self._service('play_media', media_content_id=text, media_content_type='command')

    def volume(self, level):
        self._service('volume_set', volume_level=level / 10)
        for _ in range(6):  # the station reports the new level within a second or two
            self.sleep(0.5)
            if self.state()['volume'] == level:
                return True
        return False

    def control(self, action):
        self._service(CONTROLS[action])


def main(argv=None):
    ap = argparse.ArgumentParser(description='Yandex Station via Home Assistant for JARVIS')
    sub = ap.add_subparsers(dest='cmd', required=True)
    k = sub.add_parser('set-token')
    k.add_argument('--from-clipboard', action='store_true')
    sub.add_parser('status')
    a = ap.parse_args(argv)
    if a.cmd == 'set-token':
        import getpass
        text = take_clipboard() if a.from_clipboard else getpass.getpass('Home Assistant token (input hidden): ')
        print(json.dumps(keys().save(text)))
    else:
        s = Station()
        print(json.dumps({'entity': s.entity(), **s.state()}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
