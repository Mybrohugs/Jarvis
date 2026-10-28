"""Yandex smart home ("Дом с Алисой"): lights, bulbs, sockets and the user's own scenarios.

Official API api.iot.yandex.net/v1.0 with an OAuth token (scopes iot:view, iot:control), stored with
Windows DPAPI in <DEV>/.yandex-iot-dev and never printed. Device names and room names come from the
user's account and are untrusted data: they are only matched against the spoken phrase.
  python -m developer_core.jarvis_local.yandex_iot set-token --from-clipboard
  python -m developer_core.jarvis_local.yandex_iot devices
"""
from __future__ import annotations
import argparse
import http.client
import json
import re
import sys
from .gemini_voice import DEV_ROOT, KeyStore, take_clipboard
from .session import Denied

HOST = 'api.iot.yandex.net'
ROOT = DEV_ROOT / '.yandex-iot-dev'
KINDS = {'light': ('devices.types.light',), 'socket': ('devices.types.socket',)}
KIND_WORDS = (('light', r'свет|ламп|люстр|торшер|подсветк|бра\b|светильник'), ('socket', r'розетк'))


def keys():
    return KeyStore(ROOT, name='yandex-iot-token', label='JARVIS Yandex IoT DEV', service='Yandex')


def _https(method, path, body, token):
    conn = http.client.HTTPSConnection(HOST, timeout=15)
    try:
        conn.request(method, '/v1.0/' + path, body=json.dumps(body).encode() if body is not None else None,
                      headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        r = conn.getresponse()
        raw = r.read(2_000_001)
        if r.status == 401:
            raise Denied('Яндекс не принял токен: получите новый (set-token)')
        if r.status != 200 or len(raw) > 2_000_000:
            raise Denied('Yandex HTTP %d' % r.status)
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('status') != 'ok':
            raise Denied('Yandex returned an error')
        return data
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise Denied('Yandex unreachable: ' + type(exc).__name__) from None
    finally:
        conn.close()


def kind_of(dev):
    kind = next((k for k, types in KINDS.items() if dev.get('type') in types), None)
    # a wall switch counts as a light only when its name says so ("Свет", "Люстра"); others stay untouched
    if kind is None and dev.get('type') == 'devices.types.switch' and re.match(KIND_WORDS[0][1], str(dev.get('name', '')).lower()):
        kind = 'light'
    return kind


def is_on(dev):
    for cap in dev.get('capabilities') or []:
        if cap.get('type') == 'devices.capabilities.on_off':
            return (cap.get('state') or {}).get('value')
    return None


def has_temperature(dev):
    return any(c.get('type') == 'devices.capabilities.color_setting' and 'temperature_k' in (c.get('parameters') or {})
               for c in dev.get('capabilities') or [])


def has_brightness(dev):
    return any(c.get('type') == 'devices.capabilities.range' and (c.get('parameters') or {}).get('instance') == 'brightness'
               for c in dev.get('capabilities') or [])


class YandexHome:
    def __init__(self, keys_=None, transport=_https):
        self.keys, self.transport = keys_ or keys(), transport

    def _call(self, method, path, body=None):
        return self.transport(method, path, body, self.keys.load())

    def info(self):
        data = self._call('GET', 'user/info')
        rooms = {r.get('id'): str(r.get('name', ''))[:60] for r in data.get('rooms') or [] if isinstance(r, dict)}
        devices = [{'id': str(d.get('id', ''))[:128], 'name': str(d.get('name', ''))[:60],
                    'room': rooms.get(d.get('room'), ''), 'kind': kind_of(d), 'on': is_on(d),
                    'brightness': has_brightness(d), 'temperature': has_temperature(d)}
                   for d in data.get('devices') or [] if isinstance(d, dict)]
        scenarios = [{'id': str(s.get('id', ''))[:128], 'name': str(s.get('name', ''))[:60]}
                     for s in data.get('scenarios') or [] if isinstance(s, dict)]
        return {'devices': devices, 'scenarios': scenarios}

    def pick(self, phrase, devices):
        """Devices the phrase is about. Only lights and sockets. Priority: a mentioned room, then a
        specific device name ("чайник"), then a generic name ("Свет", "Розетка"), then every device of the kind."""
        low = phrase.lower()
        known = [d for d in devices if d['kind']]
        everything = re.search(r'\b(?:весь|все|всю|везде)\b', low)
        kind = next((k for k, pat in KIND_WORDS if re.search(pat, low)), None)
        rooms = {d['room'] for d in known if d['room'] and _stem_in(d['room'], low)}
        if rooms:
            known = [d for d in known if d['room'] in rooms]
        named = [d for d in known if d['name'] and _stem_in(d['name'], low)]
        specific = [d for d in named if not re.fullmatch(r'(?:%s)\w*' % '|'.join(p for _, p in KIND_WORDS), d['name'].lower())]
        if (specific or named) and not everything:
            return specific or named
        if kind is None:
            return []
        of_kind = [d for d in known if d['kind'] == kind]
        if rooms:
            return of_kind
        if kind == 'socket' and len(of_kind) > 1 and not everything:
            raise Denied('Розеток несколько: скажите, какую (%s)' % ', '.join(d['name'] for d in of_kind))
        return of_kind

    def act(self, devices, on=None, brightness=None, temperature=None):
        actions = []
        for d in devices:
            acts = []
            if on is not None:
                acts.append({'type': 'devices.capabilities.on_off', 'state': {'instance': 'on', 'value': bool(on)}})
            if brightness is not None and d['brightness']:
                acts.append({'type': 'devices.capabilities.range', 'state': {'instance': 'brightness', 'value': brightness}})
            if temperature is not None and d.get('temperature'):
                acts.append({'type': 'devices.capabilities.color_setting',
                             'state': {'instance': 'temperature_k', 'value': temperature}})
            if acts:
                actions.append({'id': d['id'], 'actions': acts})
        if not actions:
            raise Denied('Этим устройствам нельзя задать такую команду')
        data = self._call('POST', 'devices/actions', {'devices': actions})
        done, failed = [], []
        by_id = {d['id']: d['name'] for d in devices}
        for d in data.get('devices') or []:
            statuses = [((c.get('state') or {}).get('action_result') or {}).get('status') for c in d.get('capabilities') or []]
            (done if statuses and all(s == 'DONE' for s in statuses) else failed).append(by_id.get(d.get('id'), '?'))
        return done, failed

    def scenario(self, scenario_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', scenario_id):
            raise Denied('Bad scenario id')
        self._call('POST', 'scenarios/%s/actions' % scenario_id, {})


def _stem_in(name, low):
    """Russian endings change: "Лампочка" ~ "лампочку", "Кухня" ~ "на кухне"."""
    return all(w[:max(4, len(w) - 2)] in low for w in name.lower().split())


def main(argv=None):
    ap = argparse.ArgumentParser(description='Yandex smart home for JARVIS')
    sub = ap.add_subparsers(dest='cmd', required=True)
    k = sub.add_parser('set-token')
    k.add_argument('--from-clipboard', action='store_true')
    sub.add_parser('devices')
    a = ap.parse_args(argv)
    if a.cmd == 'set-token':
        import getpass
        text = take_clipboard() if a.from_clipboard else getpass.getpass('Yandex OAuth token (input hidden): ')
        print(json.dumps(keys().save(text)))
    else:
        info = YandexHome().info()
        print(json.dumps({'devices': [{k: d[k] for k in ('name', 'room', 'kind', 'on')} for d in info['devices']],
                          'scenarios': [s['name'] for s in info['scenarios']]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
