"""Yandex smart home (no network): phrases, device matching, actions, allowlist."""
import tempfile
import unittest
from developer_core.jarvis_local.intent import understand
from developer_core.jarvis_local.phone_executor import ALLOWED, validate
from developer_core.jarvis_local.router import UnifiedTaskRouter
from developer_core.jarvis_local.session import Denied, TrustedLocalSession
from developer_core.jarvis_local.yandex_iot import YandexHome

INFO = {'status': 'ok', 'rooms': [{'id': 'r1', 'name': 'Кухня'}, {'id': 'r2', 'name': 'Спальня'}],
        'devices': [
            {'id': 'l1', 'name': 'Лампочка', 'room': 'r2', 'type': 'devices.types.light',
             'capabilities': [{'type': 'devices.capabilities.on_off', 'state': {'instance': 'on', 'value': False}},
                              {'type': 'devices.capabilities.range', 'parameters': {'instance': 'brightness'}},
                              {'type': 'devices.capabilities.color_setting', 'parameters': {'temperature_k': {'min': 1500, 'max': 6500}}}]},
            {'id': 'l2', 'name': 'Свет', 'room': 'r1', 'type': 'devices.types.switch',
             'capabilities': [{'type': 'devices.capabilities.on_off', 'state': {'instance': 'on', 'value': True}}]},
            {'id': 's1', 'name': 'Розетка', 'room': 'r1', 'type': 'devices.types.socket',
             'capabilities': [{'type': 'devices.capabilities.on_off', 'state': {'instance': 'on', 'value': False}}]},
            {'id': 'tv', 'name': 'Телевизор', 'room': 'r2', 'type': 'devices.types.media_device.tv', 'capabilities': []},
            {'id': 'w1', 'name': 'Вентилятор', 'room': 'r1', 'type': 'devices.types.switch', 'capabilities': []}],
        'scenarios': [{'id': 'sc1', 'name': 'Кино'}, {'id': 'sc2', 'name': 'Доброе утро'}]}


class FakeKeys:
    def load(self):
        return 'y0_FAKE'


class FakeYandex:
    def __init__(self):
        self.posts = []

    def __call__(self, method, path, body, token):
        if method == 'GET':
            return INFO
        self.posts.append((path, body))
        if path.startswith('scenarios/'):
            return {'status': 'ok'}
        return {'status': 'ok', 'devices': [{'id': d['id'], 'capabilities': [
            {'type': a['type'], 'state': {'instance': a['state']['instance'], 'action_result': {'status': 'DONE'}}}
            for a in d['actions']]} for d in body['devices']]}


class YandexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.router = UnifiedTaskRouter(TrustedLocalSession.phone_allowlist(ALLOWED), self.tmp.name)
        self.api = FakeYandex()
        self.router._yandex = YandexHome(FakeKeys(), self.api)

    def run_phrase(self, phrase):
        step = understand(phrase)['steps'][0]
        task, args = validate(step)
        return self.router.dispatch(task, **args)

    def test_phrases(self):
        self.assertEqual(understand('включи свет')['steps'][0], {'task': 'home-switch', 'args': {'phrase': 'включи свет', 'on': True}})
        self.assertEqual(understand('Джарвис, погаси свет на кухне')['steps'][0]['args']['on'], False)
        self.assertEqual(understand('свет на 40%')['steps'][0]['args']['level'], 40)
        self.assertEqual(understand('приглуши свет')['steps'][0]['args']['level'], 30)
        self.assertEqual(understand('запусти сценарий кино')['steps'][0], {'task': 'home-scenario', 'args': {'name': 'кино'}})
        self.assertEqual(understand('что включено дома')['steps'][0]['task'], 'home-devices')
        self.assertNotEqual(understand('включи режим игры')['steps'][0]['task'], 'home-switch')

    def test_lights_by_kind_room_and_name(self):
        self.assertEqual(self.run_phrase('выключи свет')['done'], ['Свет'])  # the device named "Свет"
        out = self.run_phrase('выключи весь свет')
        self.assertEqual(sorted(out['done']), ['Лампочка', 'Свет'])  # all lights, never the TV
        self.assertEqual(self.run_phrase('включи лампочку')['done'], ['Лампочка'])
        out = self.run_phrase('включи свет в спальне')
        self.assertEqual(out['done'], ['Лампочка'])
        self.assertEqual(self.api.posts[-1][1]['devices'][0]['actions'][0]['state'], {'instance': 'on', 'value': True})
        self.assertEqual(self.run_phrase('включи розетку')['speech'], 'Включил: Розетка.')

    def test_brightness_only_where_supported(self):
        out = self.run_phrase('весь свет на 40%')
        actions = {d['id']: [a['type'] for a in d['actions']] for d in self.api.posts[-1][1]['devices']}
        self.assertIn('devices.capabilities.range', actions['l1'])
        self.assertNotIn('devices.capabilities.range', actions['l2'])
        self.assertIn('Яркость 40%', out['speech'])

    def test_scenarios_and_list(self):
        self.assertEqual(self.run_phrase('запусти сценарий доброе утро')['speech'], 'Сценарий «Доброе утро» запущен.')
        self.assertEqual(self.api.posts[-1][0], 'scenarios/sc2/actions')
        self.assertIn('Свет (вкл)', self.run_phrase('что включено дома')['speech'])
        with self.assertRaises(Denied):
            self.router.dispatch('home-scenario', name='нет такого')

    def test_tv_scenes_set_the_lights(self):
        from developer_core.jarvis_local.home_media import Bravia, MediaOperator
        from tests.test_jarvis_media import FakeTV
        self.router.media = MediaOperator(self.router.ws, Bravia(credentials=('192.168.1.2', 'x'), transport=FakeTV()),
                                          sleep=lambda s: None)
        out = self.router.dispatch('media-scene', name='movie')
        self.assertIn('Свет приглушён до 20%.', out['speech'])
        body = self.api.posts[-1][1]['devices']
        self.assertEqual(sorted(d['id'] for d in body), ['l1', 'l2'])  # lights only, never the socket
        out = self.router.dispatch('media-scene', name='night')
        self.assertIn('Свет выключен.', out['speech'])

    def test_scene_survives_a_lights_failure(self):
        from developer_core.jarvis_local.home_media import Bravia, MediaOperator
        from tests.test_jarvis_media import FakeTV
        self.router.media = MediaOperator(self.router.ws, Bravia(credentials=('192.168.1.2', 'x'), transport=FakeTV()),
                                          sleep=lambda s: None)

        def offline(*a):
            raise Denied('Yandex unreachable: OSError')
        self.router._yandex = YandexHome(FakeKeys(), offline)
        out = self.router.dispatch('media-scene', name='movie')
        self.assertNotIn('Свет', out['speech'])
        self.assertEqual(self.router.ws.journal.entries()[-1]['task'], 'scene-lights')

    def test_ambiguous_socket_is_refused(self):
        saved = INFO['devices'][2]
        INFO['devices'][2] = {'id': 's1', 'name': 'Обогреватель', 'room': 'r1', 'type': 'devices.types.socket',
                              'capabilities': []}
        INFO['devices'].append({'id': 's2', 'name': 'Чайник', 'room': 'r1', 'type': 'devices.types.socket', 'capabilities': []})
        try:
            with self.assertRaises(Denied):
                self.run_phrase('включи розетку')  # two sockets, neither named: ask which one
            self.assertEqual(self.run_phrase('включи розетку чайник')['done'], ['Чайник'])
        finally:
            INFO['devices'].pop()
            INFO['devices'][2] = saved

    def test_routine_phrases(self):
        for phrase, name in (('я дома', 'home'), ('Джарвис, я ушёл', 'away'), ('я ухожу', 'away'),
                             ('доброе утро', 'morning'), ('Добрый вечер, Джарвис', 'evening'), ('вечер добрый', 'evening')):
            self.assertEqual(understand(phrase)['steps'][0], {'task': 'home-routine', 'args': {'name': name}}, phrase)
        self.assertNotEqual(understand('я дома буду в шесть')['steps'][:1], [{'task': 'home-routine', 'args': {'name': 'home'}}])
        with self.assertRaises(Denied):
            validate({'task': 'home-routine', 'args': {'name': 'party'}})

    def test_morning_and_evening_set_the_bulb_only(self):
        out = self.run_phrase('доброе утро')
        (lights, body), (scenario, _) = self.api.posts[-2:]
        self.assertEqual([d['id'] for d in body['devices']], ['l1'])  # the dimmable bulb, not the wall switch
        self.assertIn({'type': 'devices.capabilities.range', 'state': {'instance': 'brightness', 'value': 30}},
                      body['devices'][0]['actions'])
        self.assertEqual(scenario, 'scenarios/sc2/actions')  # the speaker part: volume 4 + playlist
        self.assertIn('Сценарий «Доброе утро» запущен.', out['speech'])
        out = self.run_phrase('я дома')  # no "Я дома" scenario yet: say how to add the speaker part
        self.assertIn('создайте сценарий «Я дома»', out['speech'])
        out = self.run_phrase('добрый вечер')
        acts = self.api.posts[-1][1]['devices'][0]['actions']
        self.assertIn({'type': 'devices.capabilities.color_setting', 'state': {'instance': 'temperature_k', 'value': 2700}}, acts)
        self.assertIn('Тёплый оттенок', out['speech'])

    def test_routine_runs_the_speaker_scenario_when_it_exists(self):
        INFO['scenarios'].append({'id': 'sc3', 'name': 'Я дома'})
        try:
            out = self.run_phrase('я дома')
        finally:
            INFO['scenarios'].pop()
        self.assertEqual(self.api.posts[-1][0], 'scenarios/sc3/actions')
        self.assertIn('Сценарий «Я дома» запущен.', out['speech'])
        self.assertEqual(out['done'], ['Лампочка'])

    def test_away_switches_everything_off_but_not_tv_or_unknown(self):
        out = self.run_phrase('я ушёл')
        body = self.api.posts[-1][1]['devices']
        self.assertEqual(sorted(d['id'] for d in body), ['l1', 'l2', 's1'])  # never the TV/fan via Yandex
        self.assertTrue(all(d['actions'] == [{'type': 'devices.capabilities.on_off', 'state': {'instance': 'on', 'value': False}}]
                            for d in body))
        self.assertTrue(out['speech'].startswith('Выключил:'))

    def test_doctor_names_missing_speaker_scenarios(self):
        from unittest import mock
        from developer_core.jarvis_local.doctor import Doctor

        class Configured:
            def configured(self):
                return True
        with mock.patch('developer_core.jarvis_local.yandex_iot.keys', Configured):
            status, detail, fix = Doctor(self.router).smart_home()
        self.assertEqual(status, 'WARN')
        self.assertIn('нет сценария: Я дома', detail)  # "Доброе утро" exists, evening needs no speaker
        self.assertNotIn('Доброе утро', detail.split('нет сценария')[1])


if __name__ == '__main__':
    unittest.main()
