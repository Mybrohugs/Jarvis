"""TV / soundbar / PS5 operator against a fake Bravia that mirrors real KD-55X81J replies (mock test)."""
from pathlib import Path
import tempfile
import unittest
from developer_core.jarvis_local.home_media import Bravia, MediaOperator
from developer_core.jarvis_local.session import Denied, TrustedLocalSession
from developer_core.jarvis_local.workspace import Workspace


class FakeTV:
    def __init__(self, power='active'):
        self.s = {'power': power, 'volume': 30, 'mute': False, 'bass': 0,
                  'uri': 'extInput:hdmi?port=3', 'title': 'HDMI 3'}
        self.calls = []

    def __call__(self, service, body):
        m, p = body['method'], body['params']
        self.calls.append(m)
        s = self.s
        if m == 'getPowerStatus':
            return {'result': [{'status': s['power']}], 'id': 1}
        if m == 'setPowerStatus':
            s['power'] = 'active' if p[0]['status'] else 'standby'
            return {'result': [], 'id': 1}
        if s['power'] != 'active':
            return {'error': [40005, 'Display Is Turned off'], 'id': 1}
        if m == 'getVolumeInformation':
            return {'result': [[{'target': 'speaker', 'volume': s['volume'], 'mute': s['mute'], 'maxVolume': 100, 'minVolume': 0}]]}
        if m == 'getPlayingContentInfo':
            return {'result': [{'uri': s['uri'], 'source': '', 'title': s['title']}]}
        if m == 'getSpeakerSettings':
            return {'result': [[{'target': 'subwooferLevel', 'currentValue': str(s['bass'])}]]}
        if m == 'getSoundSettings':
            return {'result': [[{'target': 'outputTerminal', 'currentValue': 'audioSystem'}]]}
        if m == 'getCurrentExternalInputsStatus':
            return {'result': [[{'uri': 'extInput:cec?type=player&port=1&logicalAddr=4', 'title': 'PlayStation 5'},
                                {'uri': 'extInput:cec?type=freeuse&port=3&logicalAddr=5', 'title': 'HT-S400'}]]}
        if m == 'setAudioVolume':
            s['volume'] = int(p[0]['volume'])
            return {'result': [0]}
        if m == 'setAudioMute':
            s['mute'] = p[0]['status']
            return {'result': [0]}
        if m == 'setPlayContent':
            s['uri'], s['title'] = p[0]['uri'], 'PlayStation 5'
            return {'result': []}
        if m == 'setSpeakerSettings':
            s['bass'] = int(p[0]['settings'][0]['value'])
            return {'result': []}
        return {'error': [12, 'No Such Method']}


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Workspace(Path(self.tmp.name))
        self.fake = FakeTV()
        self.seen = []
        self.session = TrustedLocalSession.for_tests(lambda s, c: self.seen.append(s) or True)
        self.op = MediaOperator(self.ws, Bravia(credentials=('192.168.1.2', 'x'), transport=self.fake), sleep=lambda s: None)

    def test_status(self):
        st = self.op.status()
        self.assertEqual((st['volume'], st['sound_output'], st['bass']), (30, 'audioSystem', 0))

    def test_volume_set_relative_and_bounds(self):
        self.assertEqual(self.op.volume(self.session, level=25)['status'], 'PASS')
        self.assertEqual(self.op.volume(self.session, delta=-5)['after']['volume'], 20)
        with self.assertRaises(Denied):
            self.op.volume(self.session, level=90)  # capped at 60
        self.assertEqual(self.seen[0], {'task': 'media-volume', 'from': 30, 'to': 25})

    def test_mute_bass_and_audit(self):
        self.assertTrue(self.op.mute(True, self.session)['after']['mute'])
        r = self.op.bass(self.session, delta=2)
        self.assertEqual((r['status'], r['after']['bass']), ('PASS', 2))
        with self.assertRaises(Denied):
            self.op.bass(self.session, level=50)
        tasks = [e['task'] for e in self.ws.journal.entries()]
        self.assertEqual(tasks, ['media-mute', 'media-bass'])

    def test_ps5_scenario_powers_on_then_switches_input(self):
        self.fake.s['power'] = 'standby'
        r = self.op.ps5(self.session)
        self.assertEqual(r['status'], 'PASS', r)
        self.assertEqual(r['after']['input_title'], 'PlayStation 5')
        self.assertLess(self.fake.calls.index('setPowerStatus'), self.fake.calls.index('setPlayContent'))

    def test_declined_changes_nothing(self):
        op = MediaOperator(self.ws, Bravia(credentials=('192.168.1.2', 'x'), transport=self.fake), sleep=lambda s: None)
        with self.assertRaises(Denied):
            op.volume(TrustedLocalSession.for_tests(lambda s, c: False), level=10)
        self.assertEqual(self.fake.s['volume'], 30)
        self.assertNotIn('setAudioVolume', self.fake.calls)

    def test_read_only_session_cannot_change(self):
        with self.assertRaises(Denied):
            self.op.mute(True, TrustedLocalSession.read_only())
        self.assertFalse(self.fake.s['mute'])

    def test_scenes_and_learned_volume(self):
        clock = [1000.0]
        op = MediaOperator(self.ws, Bravia(credentials=('192.168.1.2', 'x'), transport=self.fake),
                           sleep=lambda s: None, clock=lambda: clock[0])
        self.fake.s['power'] = 'standby'
        r = op.scene('game', self.session)
        self.assertEqual((r['status'], r['after']['volume'], r['after']['input_title']), ('PASS', 25, 'PlayStation 5'))
        self.assertIn('режим игры', r['announce'])
        clock[0] += 600
        self.assertEqual(op.volume(self.session, level=33)['learned'], {'scene': 'game', 'volume': 33})
        self.assertEqual(op.scene('game', self.session)['after']['volume'], 33)  # remembered
        clock[0] += 4 * 3600
        self.assertNotIn('learned', op.volume(self.session, level=20))  # outside the 3 h window
        self.assertEqual(op.scene('night', self.session)['after']['power'], 'standby')
        self.assertEqual(op.scene('movie', self.session)['after']['volume'], 30)

    def test_phone_scene_volume_capped(self):
        op = MediaOperator(self.ws, Bravia(credentials=('192.168.1.2', 'x'), transport=self.fake), sleep=lambda s: None)
        op.prefs.save({'volume': {'game': 55}})
        phone = TrustedLocalSession.phone_allowlist(('media-scene',))
        self.assertEqual(op.scene('game', phone)['after']['volume'], 40)
        self.assertEqual(op.scene('game', self.session)['after']['volume'], 55)

    def test_tv_off_volume_refused(self):
        self.fake.s['power'] = 'standby'
        with self.assertRaises(Denied):
            self.op.volume(self.session, level=10)


if __name__ == '__main__':
    unittest.main()
