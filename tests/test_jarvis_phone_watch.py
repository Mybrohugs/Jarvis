"""The iPhone via Home Assistant (presence, Sleep / Focus, battery) with fake HA states, no network."""
import datetime
import tempfile
import unittest
from pathlib import Path
from developer_core.jarvis_local.intent import understand
from developer_core.jarvis_local.phone_executor import ALLOWED, PhoneExecutor, validate
from developer_core.jarvis_local.phone_watch import HOLD, PhoneWatch, read_phone
from developer_core.jarvis_local.reminders import parse
from developer_core.jarvis_local.router import UnifiedTaskRouter
from developer_core.jarvis_local.session import TrustedLocalSession

DAY = datetime.datetime(2026, 10, 29, 18, 0)
NIGHT = datetime.datetime(2026, 10, 30, 2, 0)


def states(where='home', battery=80, charge='Not Charging', focus='off', sleep=None):
    out = [{'entity_id': 'device_tracker.iphone_roman', 'state': where,
            'attributes': {'source_type': 'gps', 'battery_level': battery}},
           {'entity_id': 'device_tracker.router_thing', 'state': 'home', 'attributes': {'source_type': 'router'}},
           {'entity_id': 'sensor.iphone_roman_battery_level', 'state': str(battery)},
           {'entity_id': 'sensor.iphone_roman_battery_state', 'state': charge},
           {'entity_id': 'binary_sensor.iphone_roman_focus', 'state': focus}]
    if sleep is not None:
        out.append({'entity_id': 'input_boolean.jarvis_sleep', 'state': sleep})
    return out


class FakeSpeaker:
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)

    def say_long(self, text):
        self.said.append(text)


class PhoneWatchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.router = UnifiedTaskRouter(TrustedLocalSession.phone_allowlist(ALLOWED), Path(tmp.name))
        self.routines, self.pushes, self.now = [], [], 1000.0
        self.router._station = self.speaker = FakeSpeaker()

        def routine(name):
            self.routines.append(name)
            self.router.set_presence(name != 'away')
            return {'status': 'PASS', 'speech': 'Выключил: Люстра.' if name == 'away' else 'Включил: Люстра.'}
        self.router._home_routine = routine
        self.ha = states()
        self.watch = PhoneWatch(self.router, fetch=lambda: self.ha, push=lambda t, b: self.pushes.append(b),
                                clock=lambda: self.now)

    def tick(self, seconds, when=DAY):
        self.now += seconds
        return self.watch.step(when)

    def test_reads_only_the_gps_tracker_and_its_sensors(self):
        phone = read_phone(states('not_home', 15, 'Charging', 'on'))
        self.assertEqual(phone, {'tracker': 'device_tracker.iphone_roman', 'home': False, 'battery': 15,
                                 'charging': True, 'focus': True, 'sleep': None})
        self.assertIsNone(read_phone([{'entity_id': 'sensor.sun_next_dawn', 'state': 'x'}]))
        self.assertIsNone(read_phone(states('unknown'))['home'])

    def test_leaving_runs_away_once_after_the_hold(self):
        self.tick(0)
        self.ha = states('not_home')
        self.assertEqual(self.tick(30), [])  # GPS jitter: not yet
        self.assertEqual(self.tick(HOLD), ['routine-away'])
        self.assertEqual(self.routines, ['away'])
        self.assertIn('Люстра', self.pushes[-1])
        self.assertFalse(self.router.at_home())
        self.assertEqual(self.tick(60), [])  # once per change
        self.ha = states('home')
        self.tick(30)
        self.assertEqual(self.tick(HOLD), ['routine-home'])
        self.assertEqual(self.routines, ['away', 'home'])

    def test_short_trip_and_unknown_do_nothing(self):
        self.ha = states('not_home')
        self.tick(30)
        self.ha = states('home')
        self.tick(30)
        self.ha = states('unknown')
        self.tick(HOLD * 2)
        self.assertEqual(self.routines, [])

    def test_at_night_only_the_flag_changes(self):
        self.ha = states('not_home')
        self.tick(0, NIGHT)
        self.assertEqual(self.tick(HOLD, NIGHT), ['presence-away'])
        self.assertEqual(self.routines, [])
        self.assertFalse(self.router.at_home())

    def test_failed_routine_is_not_retried_every_30_s(self):
        def broken(name):
            raise OSError('Yandex offline')
        self.router._home_routine = broken
        self.ha = states('not_home')
        self.tick(0)
        self.tick(HOLD)
        self.tick(30)
        self.assertEqual(len(self.pushes), 1)
        self.assertIn('Не получилось', self.pushes[0])
        self.assertFalse(self.router.at_home())

    def test_sleep_or_night_focus_makes_jarvis_quiet(self):
        self.ha = states(sleep='on')
        self.tick(0)
        self.assertTrue(self.router.quiet())
        self.ha = states(focus='on')
        self.tick(30)
        self.assertFalse(self.router.quiet())  # a Focus in the daytime is not sleep
        self.tick(30, NIGHT)
        self.assertTrue(self.router.quiet())

    def test_quiet_reminder_is_pushed_not_spoken(self):
        self.ha = states(sleep='on')
        self.watch.step(NIGHT)
        self.router.reminders.add(parse('напомни через 1 минуту выпить таблетку', NIGHT))
        ex = PhoneExecutor(self.dir, router=self.router)
        said = []
        fired = ex.fire_reminders(now=NIGHT + datetime.timedelta(minutes=2), speak=said.append)
        self.assertEqual((len(fired), said, self.speaker.said), (1, [], []))

    def test_low_battery_is_mentioned_once_per_discharge(self):
        self.ha = states(battery=18)
        self.assertEqual(self.tick(0), ['battery'])
        self.assertIn('18%', self.speaker.said[-1])
        self.assertEqual(self.tick(30), [])
        self.ha = states(battery=18, charge='Charging')
        self.tick(30)
        self.ha = states(battery=17)
        self.assertEqual(self.tick(30), ['battery'])
        self.ha = states(battery=10)
        self.assertEqual(self.tick(30, NIGHT), [])  # but never at night

    def test_low_battery_away_from_home_is_silent(self):
        self.router.set_presence(False)
        self.ha = states('not_home', battery=5)
        self.assertEqual(self.tick(0), [])

    def test_phone_status_phrase_and_allowlist(self):
        self.assertEqual(understand('где мой телефон')['steps'][0]['task'], 'phone-status')
        self.assertEqual(understand('сколько заряда на телефоне?')['steps'][0]['task'], 'phone-status')
        self.assertIn('пока не виден', self.router.dispatch('phone-status')['speech'])
        self.ha = states(battery=64, charge='Charging')
        self.tick(0)
        task, args = validate({'task': 'phone-status', 'args': {}})
        self.assertEqual(self.router.dispatch(task, **args)['speech'], 'Телефон: дома, заряд 64%, заряжается.')


if __name__ == '__main__':
    unittest.main()
