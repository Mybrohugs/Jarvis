"""The owner's iPhone as the Home Assistant companion app reports it: presence, Focus, battery.

The phone executor calls PhoneWatch.step() every 30 s:
  * presence: device_tracker.<iphone> is 'home' or away for HOLD seconds in a row (GPS jitter) ->
    the 'Я дома' / 'Я ушёл' routine runs once per change and the phone gets a push about it;
    at night or while quiet only the presence flag changes, nothing switches or speaks;
  * quiet: Sleep (input_boolean.jarvis_sleep, flipped by an iPhone Shortcuts automation) or any Focus
    at night -> reminders are pushed only, the PC and the station stay silent (router.quiet());
  * battery: at home, not charging, <= LOW %, 09-23 h -> the station (or a push) asks to charge it,
    once per discharge.
Only HA's own zone verdict is used: coordinates are never read or stored. State: <state>/phone_watch.json.
"""
from __future__ import annotations
import datetime
import json
import re
import time
from .session import Denied

HOLD = 120
LOW = 20
FRESH = 600  # router.quiet() trusts a step at most this old
UNKNOWN = ('unknown', 'unavailable', '', None)


def night(now):
    return now.hour >= 23 or now.hour < 7


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def read_phone(states):
    """HA states -> {'tracker', 'home' (True/False/None), 'battery', 'charging', 'focus', 'sleep'} or None."""
    by = {s.get('entity_id'): s for s in states if isinstance(s, dict)}
    trackers = sorted((i for i, s in by.items() if re.fullmatch(r'device_tracker\.[a-z0-9_]{1,80}', str(i))
                       and (s.get('attributes') or {}).get('source_type') == 'gps'),
                      key=lambda i: ('iphone' not in i, i))
    if not trackers:
        return None
    tid = trackers[0]
    slug, t = tid.split('.', 1)[1], by[tid]

    def state(entity):
        s = by.get(entity)
        return None if s is None or s.get('state') in UNKNOWN else str(s['state']).lower()
    where = state(tid)
    level = state('sensor.%s_battery_level' % slug)
    charge = state('sensor.%s_battery_state' % slug)
    focus, sleep = state('binary_sensor.%s_focus' % slug), state('input_boolean.jarvis_sleep')
    return {'tracker': tid, 'home': None if where is None else where == 'home',
            'battery': _int(level if level is not None else (t.get('attributes') or {}).get('battery_level')),
            'charging': None if charge is None else charge in ('charging', 'full'),
            'focus': None if focus is None else focus == 'on', 'sleep': None if sleep is None else sleep == 'on'}


class PhoneWatch:
    def __init__(self, router, fetch=None, push=None, clock=time.time):
        self.router, self.clock = router, clock
        self.fetch = fetch or (lambda: router.station.all_states())
        self.push = push or (lambda title, body: router.push.notify(title, body, '/'))
        self.path = router.ws.state / 'phone_watch.json'

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def _save(self, s):
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(s, ensure_ascii=False), encoding='utf-8')
        tmp.replace(self.path)

    def _notify(self, title, body):
        try:
            self.push(title, body)
        except Exception:
            pass  # no subscribed phone: the journal still has it

    def step(self, now=None):
        now, ts = now or datetime.datetime.now(), self.clock()
        phone = read_phone(self.fetch())
        s, events = self._load(), []
        if phone is None:
            s.update(quiet=False, seen=time.time(), phone=None)
            self._save(s)
            return events
        quiet = bool(phone['sleep']) or (bool(phone['focus']) and night(now))
        s.update(quiet=quiet, seen=time.time(), phone={k: v for k, v in phone.items() if k != 'tracker'})
        if phone['home'] is not None:
            if s.get('seen_home') != phone['home']:
                s['seen_home'], s['since'] = phone['home'], ts
            elif ts - s['since'] >= HOLD and phone['home'] != self.router.at_home() \
                    and s.get('acted') != [phone['home'], s['since']]:
                s['acted'] = [phone['home'], s['since']]  # once per change, even if the routine fails
                events.append(self._presence(phone['home'], now, quiet))
        events += self._battery(phone, s, now, quiet, ts)
        self._save(s)
        return events

    def _presence(self, home, now, quiet):
        name = 'home' if home else 'away'
        if night(now) or quiet:
            self.router.set_presence(home)
            self.router.ws.journal.record('phone-presence', 'PASS', routine=None, home=home, rollback=[])
            return 'presence-' + name
        try:
            result = self.router.dispatch('home-routine', name=name)
            self._notify('JARVIS: ' + ('вы дома' if home else 'вы ушли'), result['speech'][:180])
        except (Denied, OSError, ValueError) as exc:
            self.router.set_presence(home)  # the routine failed, the owner still moved
            self._notify('JARVIS', 'Не получилось выполнить «%s»: %s' % ('Я дома' if home else 'Я ушёл', str(exc)[:100]))
        self.router.ws.journal.record('phone-presence', 'PASS', routine=name, home=home, rollback=[])
        return 'routine-' + name

    def _battery(self, phone, s, now, quiet, ts):
        level, charging = phone['battery'], phone['charging']
        if charging or (level is not None and level > LOW + 10):
            s.pop('battery_warned', None)
            return []
        if level is None or level > LOW or s.get('battery_warned') or quiet or not 9 <= now.hour < 23 \
                or not self.router.at_home():
            return []
        s['battery_warned'] = ts
        text = 'Сэр, телефон разряжен до %d%%, поставьте его на зарядку.' % level
        said = False
        try:
            sp = self.router.speaker()
            if sp is not None:
                sp.say(text)
                said = True
        except Exception:
            pass
        self._notify('Телефон разряжается', text)
        self.router.ws.journal.record('phone-battery', 'PASS', level=level, station=said, rollback=[])
        return ['battery']


def status(router):
    """What the last step saw, for 'где мой телефон' / 'сколько заряда на телефоне'."""
    try:
        s = json.loads((router.ws.state / 'phone_watch.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        s = {}
    phone = s.get('phone')
    if not phone:
        return {'status': 'WARN', 'speech': 'Телефон пока не виден в Home Assistant: включите геопозицию '
                                            '«Всегда» и датчики в приложении Home Assistant на iPhone.'}
    parts = ['дома' if phone['home'] else 'не дома' if phone['home'] is False else 'где — неизвестно']
    if phone['battery'] is not None:
        parts.append('заряд %d%%%s' % (phone['battery'], ', заряжается' if phone['charging'] else ''))
    if s.get('quiet'):
        parts.append('режим сна')
    elif phone['focus']:
        parts.append('включён фокус')
    return {'status': 'PASS', **phone, 'quiet': bool(s.get('quiet')), 'speech': 'Телефон: %s.' % ', '.join(parts)}
