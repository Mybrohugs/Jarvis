"""Yandex Station through Home Assistant with a fake HA (no network, no token)."""
import tempfile
import unittest
from pathlib import Path
from developer_core.jarvis_local.intent import understand
from developer_core.jarvis_local.phone_executor import validate
from developer_core.jarvis_local.plan_runner import run_plan
from developer_core.jarvis_local.router import UnifiedTaskRouter
from developer_core.jarvis_local.session import Denied, TrustedLocalSession
from developer_core.jarvis_local.station import Station

ENTITY = 'media_player.yandex_station_abc123'


class FakeHA:
    def __init__(self):
        self.calls, self.volume, self.stuck = [], 0.3, False

    def __call__(self, method, path, body, token):
        self.calls.append((method, path, body))
        if path == '/api/states':
            return [{'entity_id': 'media_player.bravia_kd_55x81j'}, {'entity_id': ENTITY}]
        if path == '/api/states/' + ENTITY:
            return {'state': 'playing', 'attributes': {'volume_level': self.volume, 'media_title': 'Yesterday',
                                                       'media_artist': 'The Beatles'}}
        if path == '/api/services/media_player/volume_set' and not self.stuck:
            self.volume = body['volume_level']
        return []


class StationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.approve, self.asked = True, []

        def confirmer(summary, challenge):
            self.asked.append(summary)
            return self.approve
        self.router = UnifiedTaskRouter(TrustedLocalSession.for_tests(confirmer), Path(tmp.name))
        self.ha = FakeHA()
        self.router._station = Station(http=self.ha, token='t', sleep=lambda s: None)

    def services(self):
        return [(p.rsplit('/', 1)[1], b) for m, p, b in self.ha.calls if m == 'POST']

    def run_phrase(self, phrase):
        return run_plan(self.router, understand(phrase))

    def test_say_in_the_speaker_voice(self):
        out = self.run_phrase('скажи на колонке ужин готов')
        self.assertEqual(out['status'], 'PASS', out)
        self.assertEqual(self.services(), [('play_media', {'entity_id': ENTITY, 'media_content_id': 'ужин готов',
                                                          'media_content_type': 'text'})])
        self.assertEqual(self.asked[-1]['task'], 'station-say')

    def test_alice_command(self):
        self.run_phrase('попроси алису включить джаз')
        self.run_phrase('включи на колонке Queen')
        texts = [(b['media_content_type'], b['media_content_id']) for _, b in self.services()]
        self.assertEqual(texts, [('command', 'включить джаз'), ('command', 'включи Queen')])

    def test_volume_is_verified(self):
        out = self.run_phrase('громкость колонки 5')
        self.assertEqual(out['status'], 'PASS', out)
        self.assertTrue(out['completed'][0]['result']['verified'])
        self.assertEqual(self.ha.volume, 0.5)
        self.ha.stuck = True  # the station ignores the new level
        warn = self.router.dispatch('station-volume', level=7)
        self.assertEqual(warn['status'], 'WARN')
        with self.assertRaises(Denied):
            self.router.dispatch('station-volume', level=11)

    def test_playback_and_status(self):
        for phrase, action in (('поставь колонку на паузу', 'pause'), ('останови колонку', 'pause'),
                               ('следующий трек на колонке', 'next'), ('продолжи на колонке', 'play')):
            self.assertEqual(understand(phrase)['steps'], [{'task': 'station-control', 'args': {'action': action}}], phrase)
        self.run_phrase('поставь колонку на паузу')
        self.assertEqual(self.services()[-1][0], 'media_pause')
        out = self.run_phrase('что играет на колонке')
        self.assertIn('The Beatles — Yesterday', out['completed'][0]['result']['speech'])

    def test_one_retry_after_ha_error(self):
        fails = [1]

        def flaky(method, path, body, token):
            if method == 'POST' and fails:
                fails.pop()
                raise Denied('Home Assistant HTTP 500')
            return self.ha(method, path, body, token)
        self.router._station.http = flaky
        self.assertEqual(self.router.dispatch('station-say', text='привет')['status'], 'PASS')
        self.assertEqual(len(self.services()), 1)

    def with_home(self, scenarios):
        from developer_core.jarvis_local.yandex_iot import YandexHome
        from tests.test_jarvis_yandex import FakeKeys, FakeYandex, INFO
        self.api = FakeYandex()
        self.router._yandex = YandexHome(FakeKeys(), self.api)
        saved = INFO['scenarios']
        INFO['scenarios'] = scenarios
        self.addCleanup(INFO.__setitem__, 'scenarios', saved)

    def test_home_greets_then_owner_scenario_plays(self):
        self.with_home([{'id': 'sc3', 'name': 'Я дома'}])
        out = self.router.dispatch('home-routine', name='home')
        said = [b['media_content_id'] for s, b in self.services() if s == 'play_media']
        self.assertEqual(said, ['С возвращением, сэр.'])  # music comes from the owner's scenario, not Alice
        self.assertEqual(self.api.posts[-1][0], 'scenarios/sc3/actions')
        self.assertIn('Колонка поздоровалась', out['speech'])

    def test_home_without_scenario_asks_alice_for_music(self):
        self.with_home([])
        self.router.dispatch('home-routine', name='home')
        self.assertEqual([(b['media_content_type'], b['media_content_id']) for _, b in self.services()],
                         [('text', 'С возвращением, сэр.'), ('command', 'включи мою музыку')])

    def test_morning_reads_briefing_then_music(self):
        import threading
        self.with_home([{'id': 'sc2', 'name': 'Доброе утро'}])
        self.router._briefing = lambda with_doctor=True: {'text': 'Доброе утро, сэр. ' + 'Сегодня солнечно и тепло. ' * 8}
        out = self.router.dispatch('home-routine', name='morning')
        for t in threading.enumerate():
            if t.name == 'jarvis-morning-briefing':
                t.join(5)
        said = [b['media_content_id'] for s, b in self.services() if s == 'play_media']
        self.assertGreater(len(said), 1)
        self.assertTrue(all(len(c) <= 100 for c in said))
        self.assertEqual(' '.join(said), ' '.join(('Доброе утро, сэр. ' + 'Сегодня солнечно и тепло. ' * 8).split()))
        self.assertEqual(self.services()[0], ('volume_set', {'entity_id': ENTITY, 'volume_level': 0.4}))
        self.assertEqual(self.api.posts[-1][0], 'scenarios/sc2/actions')  # the playlist after the briefing
        self.assertIn('утреннюю сводку', out['speech'])

    def test_reminders_on_the_speaker_only_at_home(self):
        import datetime
        from developer_core.jarvis_local.phone_executor import PhoneExecutor
        ex = PhoneExecutor(self.router.ws.root, router=self.router)
        now = datetime.datetime(2026, 10, 29, 12, 0)
        self.router.reminders.add({'text': 'выключить духовку', 'due': now, 'repeat': None})
        ex.fire_reminders(now=now, speak=lambda t: None)
        self.assertEqual(self.services()[-1][1]['media_content_id'], 'Сэр, напоминаю: выключить духовку.')
        self.with_home([])
        self.router.dispatch('home-routine', name='away')
        self.assertFalse(self.router.at_home())
        before = len(self.services())
        self.router.reminders.add({'text': 'полить цветы', 'due': now, 'repeat': None})
        self.assertEqual(len(ex.fire_reminders(now=now, speak=lambda t: None)), 1)
        self.assertEqual(len(self.services()), before)  # away: phone push only

    def test_refusal_touches_nothing(self):
        self.approve = False
        with self.assertRaises(Denied):
            self.router.dispatch('station-say', text='привет')
        self.assertEqual(self.services(), [])

    def test_other_phrases_unchanged(self):
        self.assertEqual(understand('поставь на паузу')['steps'][0]['task'], 'music')
        self.assertEqual(understand('включи свет на кухне')['steps'][0]['task'], 'home-switch')
        self.assertEqual(understand('громкость 20')['steps'][0]['task'], 'media-volume')

    def test_phone_allowlist(self):
        self.assertEqual(validate({'task': 'station-volume', 'args': {'level': 4}}), ('station-volume', {'level': 4}))
        for bad in ({'task': 'station-command', 'args': {'text': 'купи'}},  # Alice commands only at the PC
                    {'task': 'station-volume', 'args': {'level': 40}},
                    {'task': 'station-control', 'args': {'action': 'off'}}):
            with self.assertRaises(Denied):
                validate(bad)


if __name__ == '__main__':
    unittest.main()
