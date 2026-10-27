"""TV + soundbar + PS5 through the Sony Bravia LAN API (verified on KD-55X81J).

HT-S400 has no network API: it follows the TV over HDMI ARC/CEC, so volume and mute go
through the TV (outputTerminal=audioSystem). PS5 is selected as the TV's CEC input; CEC
wakes it. Bass uses the TV's subwooferLevel setting; its effect on HT-S400 is unverified.
Every change: local approval -> command -> read back -> audit. Credentials are the existing
DPAPI-protected Sony config; the key is never logged or returned.
"""
from __future__ import annotations
import http.client
import json
from pathlib import Path
import time
from .session import Denied

DEV_ROOT = Path(__file__).resolve().parents[2]
VOLUME_RANGE = (0, 60)       # hard cap below the TV maximum of 100
BASS_RANGE = (0, 12)


class Bravia:
    def __init__(self, credentials=None, sony_root=DEV_ROOT / '.sony-dev', transport=None):
        self._creds = credentials
        self.sony_root = sony_root
        self.transport = transport or self._http

    def _credentials(self):
        if self._creds is None:
            from ..sony_bravia import Sony
            from ..core import Denied as CoreDenied
            try:
                self._creds = Sony(self.sony_root)._credentials()
            except CoreDenied as exc:
                raise Denied(str(exc)) from None
        return self._creds

    def _http(self, service, body):
        host, key = self._credentials()
        c = http.client.HTTPConnection(host, 80, timeout=6)
        try:
            c.request('POST', '/sony/' + service, body=json.dumps(body),
                      headers={'Content-Type': 'application/json', 'X-Auth-PSK': key})
            r = c.getresponse()
            raw = r.read(200001)
            if r.status != 200 or len(raw) > 200000:
                raise Denied('TV HTTP %d' % r.status)
            return json.loads(raw)
        except OSError as exc:
            raise Denied('TV unreachable: ' + type(exc).__name__) from None
        finally:
            c.close()

    def call(self, service, method, version='1.0', params=None):
        data = self.transport(service, {'method': method, 'id': 1, 'params': params or [], 'version': version})
        if not isinstance(data, dict) or data.get('error'):
            raise Denied('TV rejected %s: %s' % (method, (data or {}).get('error')))
        return data.get('result')

    # ---- reads ----
    def state(self):
        power = self.call('system', 'getPowerStatus')[0]['status']
        out = {'power': power}
        if power != 'active':
            return out
        vol = self.call('audio', 'getVolumeInformation')[0]
        speaker = next((v for v in vol if v.get('target') == 'speaker'), vol[0])
        out.update(volume=speaker['volume'], mute=speaker['mute'])
        try:
            playing = self.call('avContent', 'getPlayingContentInfo')[0]
            out.update(input_uri=playing.get('uri'), input_title=playing.get('title') or playing.get('uri'))
        except Denied:
            out.update(input_uri=None, input_title='ТВ / приложение')
        speakers = {s['target']: s['currentValue'] for s in self.call('audio', 'getSpeakerSettings', '1.0', [{'target': ''}])[0]}
        out['bass'] = int(speakers.get('subwooferLevel', 0))
        terminal = self.call('audio', 'getSoundSettings', '1.1', [{'target': 'outputTerminal'}])[0]
        out['sound_output'] = terminal[0]['currentValue'] if terminal else None
        return out

    def ps5_uri(self):
        inputs = self.call('avContent', 'getCurrentExternalInputsStatus', '1.1')[0]
        cec = [i['uri'] for i in inputs if 'playstation' in (i.get('title') or '').lower()]
        return cec[0] if cec else 'extInput:hdmi?port=1'

    # ---- writes (no approval here; MediaOperator owns that) ----
    def power(self, on):
        self.call('system', 'setPowerStatus', '1.0', [{'status': bool(on)}])

    def volume(self, level):
        self.call('audio', 'setAudioVolume', '1.0', [{'target': 'speaker', 'volume': str(level)}])

    def mute(self, on):
        self.call('audio', 'setAudioMute', '1.0', [{'status': bool(on)}])

    def play(self, uri):
        self.call('avContent', 'setPlayContent', '1.0', [{'uri': uri}])

    def bass(self, level):
        self.call('audio', 'setSpeakerSettings', '1.0', [{'settings': [{'target': 'subwooferLevel', 'value': str(level)}]}])


def _wait(check, timeout=12.0, step=1.0, sleep=time.sleep):
    end = time.monotonic() + timeout
    while True:
        last = check()
        if last[0] or time.monotonic() >= end:
            return last
        sleep(step)


SCENES = {
    'game': {'name': 'игры', 'volume': 25},
    'movie': {'name': 'кино', 'volume': 30},
    'night': {'name': 'сна', 'volume': None},
}
PHONE_SCENE_MAX = 40  # same cap as phone volume commands
PREF_WINDOW = 3 * 3600  # a volume change within 3 h of a scene teaches that scene's level


class Prefs:
    """Feature 12: remembers the user's preferred volume per scene (local JSON, no personal data)."""

    def __init__(self, ws):
        self.path = ws.state / 'media_prefs.json'

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, data):
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        tmp.replace(self.path)

    def volume_for(self, scene):
        return int(self.load().get('volume', {}).get(scene, SCENES[scene]['volume']))

    def started(self, scene, now):
        data = self.load()
        data['scene'], data['scene_at'] = scene, now
        self.save(data)

    def learn(self, volume, now):
        data = self.load()
        scene = data.get('scene')
        if scene in SCENES and SCENES[scene]['volume'] is not None and now - data.get('scene_at', 0) <= PREF_WINDOW:
            data.setdefault('volume', {})[scene] = int(volume)
            self.save(data)
            return scene
        return None


class MediaOperator:
    def __init__(self, ws, tv=None, sleep=time.sleep, clock=time.time):
        self.ws, self.tv, self.sleep, self.clock = ws, tv or Bravia(), sleep, clock
        self.prefs = Prefs(ws)

    def scene(self, name, session):
        """Feature 1 + 11: one-phrase scenes with a spoken 'protocol' (announce before, report after)."""
        if name not in SCENES:
            raise Denied('Неизвестный сценарий')
        volume = self.prefs.volume_for(name) if SCENES[name]['volume'] is not None else None
        if volume is not None and session.approval_mode == 'phone-allowlist':
            volume = min(volume, PHONE_SCENE_MAX)
        steps = {'game': ['ТВ включить', 'вход PlayStation 5', 'громкость %s' % volume],
                 'movie': ['ТВ включить', 'громкость %s' % volume],
                 'night': ['выключить ТВ (PS5 уйдёт в покой по HDMI-CEC)']}[name]
        announce = 'Сэр, включаю режим %s: %s.' % (SCENES[name]['name'], ', '.join(steps))
        approved = session.confirm({'task': 'media-scene', 'scene': name, 'steps': steps})
        before = self.tv.state()
        ok = True
        if name == 'night':
            if before['power'] == 'active':
                self.tv.power(False)
            ok, after = _wait(lambda: self._check(lambda s: s['power'] == 'standby'), timeout=15, sleep=self.sleep)
        else:
            if before['power'] != 'active':
                self.tv.power(True)
                ok, _ = _wait(lambda: self._check(lambda s: s['power'] == 'active'), timeout=20, sleep=self.sleep)
            if ok and name == 'game':
                self.tv.play(self.tv.ps5_uri())
                ok, _ = _wait(lambda: self._check(lambda s: 'port=1' in (s.get('input_uri') or '')), timeout=15,
                              sleep=self.sleep)
            if ok:
                self.tv.volume(volume)
                ok, after = _wait(lambda: self._check(lambda s: s.get('volume') == volume), sleep=self.sleep)
            else:
                after = self.tv.state()
        self.prefs.started(name, self.clock())
        status = 'PASS' if ok else 'UNVERIFIED'
        report = {'game': 'Готово. PlayStation на экране, громкость %s. Приятной игры.' % volume,
                  'movie': 'Готово. Громкость %s. Приятного просмотра.' % volume,
                  'night': 'Телевизор выключен. Спокойной ночи, сэр.'}[name] if ok else \
            'Сценарий выполнен не полностью, проверьте телевизор.'
        entry = self.ws.journal.record('media-scene', status, scene=name, steps=steps, before=before, after=after,
                                       rollback=[], **approved)
        return {'status': status, 'scene': name, 'announce': announce, 'speech': report, 'after': after,
                'audit_id': entry['id']}

    def status(self):
        return {'status': 'PASS', **self.tv.state(), 'soundbar': 'HT-S400 via HDMI ARC (follows TV)',
                'bass_note': 'TV subwooferLevel; effect on HT-S400 not yet confirmed by ear'}

    def _do(self, session, task, plan, act, verify):
        approved = session.confirm({'task': task, **plan})
        before = self.tv.state()
        act(before)
        ok, after = _wait(lambda: self._check(verify), sleep=self.sleep)
        status = 'PASS' if ok else 'UNVERIFIED'
        entry = self.ws.journal.record(task, status, plan=plan, before=before, after=after, rollback=[],
                                       reversible_by='inverse command', **approved)
        return {'status': status, 'before': before, 'after': after, 'audit_id': entry['id']}

    def _check(self, verify):
        state = self.tv.state()
        return verify(state), state

    def power(self, on, session):
        return self._do(session, 'media-power', {'power': 'on' if on else 'off'},
                        lambda b: self.tv.power(on), lambda s: s['power'] == ('active' if on else 'standby'))

    def volume(self, session, level=None, delta=None):
        cur = self.tv.state()
        if cur['power'] != 'active':
            raise Denied('Телевизор выключен')
        target = level if level is not None else cur['volume'] + int(delta or 0)
        if not isinstance(target, int) or not VOLUME_RANGE[0] <= target <= VOLUME_RANGE[1]:
            raise Denied('Громкость должна быть %d..%d' % VOLUME_RANGE)
        result = self._do(session, 'media-volume', {'from': cur['volume'], 'to': target},
                          lambda b: self.tv.volume(target), lambda s: s.get('volume') == target)
        if result['status'] == 'PASS':
            learned = self.prefs.learn(target, self.clock())
            if learned:
                result['learned'] = {'scene': learned, 'volume': target}
        return result

    def mute(self, on, session):
        return self._do(session, 'media-mute', {'mute': bool(on)},
                        lambda b: self.tv.mute(on), lambda s: s.get('mute') is bool(on))

    def bass(self, session, level=None, delta=None):
        cur = self.tv.state()
        if cur['power'] != 'active':
            raise Denied('Телевизор выключен')
        target = level if level is not None else cur['bass'] + int(delta or 0)
        if not isinstance(target, int) or not BASS_RANGE[0] <= target <= BASS_RANGE[1]:
            raise Denied('Уровень баса должен быть %d..%d' % BASS_RANGE)
        return self._do(session, 'media-bass', {'from': cur['bass'], 'to': target,
                                                'note': 'проверьте на слух, меняются ли басы HT-S400'},
                        lambda b: self.tv.bass(target), lambda s: s.get('bass') == target)

    def ps5(self, session):
        """Scenario: TV on (if needed) -> PS5 input via CEC (wakes the console) -> verify."""
        plan = {'steps': ['включить телевизор, если он выключен', 'переключить вход на PlayStation 5 (HDMI 1, CEC)']}
        approved = session.confirm({'task': 'media-ps5', **plan})
        before = self.tv.state()
        if before['power'] != 'active':
            self.tv.power(True)
            ok, _ = _wait(lambda: self._check(lambda s: s['power'] == 'active'), timeout=20, sleep=self.sleep)
            if not ok:
                entry = self.ws.journal.record('media-ps5', 'UNVERIFIED', step='power', rollback=[], **approved)
                return {'status': 'UNVERIFIED', 'reason': 'ТВ не включился', 'audit_id': entry['id']}
        uri = self.tv.ps5_uri()
        self.tv.play(uri)
        ok, after = _wait(lambda: self._check(lambda s: 'port=1' in (s.get('input_uri') or '')), timeout=15, sleep=self.sleep)
        status = 'PASS' if ok else 'UNVERIFIED'
        entry = self.ws.journal.record('media-ps5', status, plan=plan, before=before, after=after, uri=uri,
                                       rollback=[], **approved)
        return {'status': status, 'before': before, 'after': after, 'audit_id': entry['id'],
                'note': 'PS5 выходит из режима покоя по HDMI-CEC; прямого статуса приставки у JARVIS нет'}
