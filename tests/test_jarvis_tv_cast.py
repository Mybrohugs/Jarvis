"""A video link to the TV: link parsing, Cast, the PC relay, the YouTube-link fallback, power-on,
the phone allowlist; fakes only (no TV, no network)."""
import tempfile
import unittest
from pathlib import Path
from developer_core.jarvis_local.intent import understand
from developer_core.jarvis_local.phone_executor import ALLOWED, PhoneExecutor, validate
from developer_core.jarvis_local.router import UnifiedTaskRouter
from developer_core.jarvis_local.session import Denied, TrustedLocalSession
from developer_core.jarvis_local.tv_cast import TvCast, find_link, parse_link

VID = 'dQw4w9WgXcQ'


class MemStore:
    def __init__(self, data=None):
        self.data = data

    def configured(self):
        return self.data is not None

    def save(self, data):
        self.data = data

    def load(self):
        if self.data is None:
            raise Denied('Телевизор не привязан')
        return self.data


class FakeLounge:
    def __init__(self, log, online_after=0, accept=True):
        self.log, self.online_after, self.accept, self.checks = log, online_after, accept, 0
        self.screen_name = 'BRAVIA'

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def pair(self, code):
        self.log.append(('pair', code))
        return True

    def store_auth_state(self):
        return {'screenId': 's1', 'lounge_id_token': 't%d' % len(self.log)}

    def load_auth_state(self, data):
        self.log.append(('load', data.get('screen_id', data.get('screenId'))))

    async def refresh_auth(self):
        return True

    async def is_available(self):
        self.checks += 1
        return self.checks > self.online_after

    async def connect(self):
        return True

    async def play_video(self, vid):
        self.log.append(('play', vid))
        return self.accept

    async def seek_to(self, t):
        self.log.append(('seek', t))
        return True


async def no_sleep(_):
    return None


class FakeTv:
    def __init__(self, power='standby'):
        self.power_state, self.calls = power, []

    def state(self):
        return {'power': self.power_state}

    def power(self, on):
        self.calls.append(on)
        self.power_state = 'active' if on else 'standby'


class TvCastTests(unittest.TestCase):
    def test_links(self):
        self.assertEqual(parse_link('https://youtu.be/%s?t=42' % VID), (VID, 42))
        self.assertEqual(parse_link('https://www.youtube.com/watch?v=%s&t=1m5s' % VID), (VID, 65))
        self.assertEqual(parse_link('https://m.youtube.com/shorts/%s' % VID), (VID, None))
        self.assertEqual(parse_link('https://youtube.com/live/%s?si=x' % VID), (VID, None))
        for bad in ('https://rutube.ru/video/abc', 'https://evil.example/watch?v=%s' % VID,
                    'javascript:alert(1)', 'https://www.youtube.com/watch?v=short', 'https://www.youtube.com/'):
            with self.assertRaises(Denied, msg=bad):
                parse_link(bad)
        self.assertEqual(find_link('смотри https://youtu.be/%s, круто!' % VID), 'https://youtu.be/%s' % VID)

    def test_pair_then_play_waits_for_the_app_and_seeks(self):
        log, store = [], MemStore()
        cast = TvCast(store, lounge=lambda: FakeLounge(log, online_after=2), sleep=no_sleep)
        with self.assertRaises(Denied):
            cast.pair('12345')  # not 12 digits
        self.assertEqual(cast.pair('1234 5678 9012'), 'BRAVIA')
        self.assertEqual(store.data['screenId'], 's1')
        self.assertTrue(cast.play(VID, 30))
        self.assertEqual([e for e in log if e[0] in ('play', 'seek')], [('play', VID), ('seek', 30.0)])

    def test_not_paired_and_offline(self):
        with self.assertRaises(Denied):
            TvCast(MemStore(), lounge=lambda: FakeLounge([]), sleep=no_sleep).play(VID)
        cast = TvCast(MemStore({'screenId': 's1'}), lounge=lambda: FakeLounge([], online_after=10 ** 6), sleep=no_sleep)
        with self.assertRaises(Denied):
            cast.play(VID, wait=0)


class FakeCast:
    def __init__(self, log, youtube_ok=True, media_ok=True):
        self.log, self.youtube_ok, self.media_ok = log, youtube_ok, media_ok

    def youtube(self, vid, start=None):
        self.log.append(('cast-youtube', vid, start))
        if not self.youtube_ok:
            raise OSError('cast down')
        return True

    def media(self, url, title):
        self.log.append(('cast-media', url, title))
        return self.media_ok


class FakeRelay:
    def __init__(self, log):
        self.log = log

    def start(self, inputs, headers):
        self.log.append(('relay', tuple(inputs)))
        return 'http://192.168.10.87:10795/tok/index.m3u8'

    def stop(self):
        self.log.append(('relay-stop',))


def fake_resolve(url):
    return 'Кенгуру', ['https://cdn.example/v.m3u8'], {'User-Agent': 'x'}


class RouterTvTests(unittest.TestCase):
    def setUp(self):
        from developer_core.jarvis_local.tv_media import TvMedia
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.router = UnifiedTaskRouter(TrustedLocalSession.phone_allowlist(ALLOWED), Path(tmp.name))
        self.router.tv_wake_wait = 0
        from unittest.mock import Mock
        self.router._ha_tv = Mock()
        self.router._ha_tv.wake.return_value = None  # no live HA access in fake tests
        self.log = []
        self.cast = FakeCast(self.log)
        self.router._tv_media = TvMedia(self.router.ws.state, 'tv', cast=self.cast, relay=FakeRelay(self.log),
                                        resolver=fake_resolve)
        self.router._tv_cast = TvCast(MemStore(None), lounge=lambda: FakeLounge(self.log), sleep=no_sleep)
        self.tv = FakeTv('standby')
        self.router.media.tv = self.tv

    def run_phrase(self, phrase):
        step = understand(phrase)['steps'][0]
        task, args = validate(step)
        return self.router.dispatch(task, **args)

    def test_youtube_goes_to_the_tv_app_and_switches_the_tv_on(self):
        out = self.run_phrase('Джарвис, покажи на телевизоре https://youtu.be/%s?t=42' % VID)
        self.assertEqual((out['status'], self.tv.calls), ('PASS', [True]))
        self.assertEqual(self.log, [('cast-youtube', VID, 42)])
        self.run_phrase('https://www.youtube.com/watch?v=%s на тв' % VID)
        self.assertEqual(self.tv.calls, [True])  # already on: not touched again, never switched off

    def test_any_other_site_is_relayed_through_the_pc(self):
        out = self.run_phrase('на телевизор https://rutube.ru/video/3eac3b4561676c17df9132a9a1e62e3e/')
        self.assertIn('Кенгуру', out['speech'])
        self.assertEqual(self.log, [('relay', ('https://cdn.example/v.m3u8',)),
                                    ('cast-media', 'http://192.168.10.87:10795/tok/index.m3u8', 'Кенгуру')])
        self.cast.media_ok = False
        with self.assertRaises(Denied):
            self.run_phrase('на телевизор https://vk.com/video-1_2')
        self.assertEqual(self.log[-1], ('relay-stop',))

    def test_home_network_links_are_refused(self):
        for bad in ('https://192.168.10.1/admin', 'http://localhost:8123/', 'http://router.lan/x', 'http://10.0.0.5/v.mp4'):
            with self.assertRaises(Denied, msg=bad):
                self.run_phrase('на телевизор ' + bad)
        self.assertEqual(self.log, [])

    def test_youtube_falls_back_to_the_paired_link_when_cast_fails(self):
        self.cast.youtube_ok = False
        with self.assertRaises(Denied):  # not paired: a clear refusal
            self.run_phrase('на телевизор https://youtu.be/%s' % VID)
        self.router._tv_cast.store.data = {'screenId': 's1'}
        self.assertEqual(self.run_phrase('на телевизор https://youtu.be/%s' % VID)['status'], 'PASS')
        self.assertIn(('play', VID), self.log)

    def test_phone_queue_runs_it_end_to_end(self):
        ex = PhoneExecutor(self.dir, router=self.router, chat=lambda m: '{"intent": "unknown"}')
        ex.queue.add('на телевизор https://youtu.be/%s' % VID, interpret=True)
        ex.tick()
        item = ex.queue.items()[-1]
        self.assertEqual(item['status'], 'done')
        self.assertIn('телевизоре', str(item))

    def test_ha_wakes_tv_without_native_power_call(self):
        self.router._ha_tv.wake.return_value = True
        out = self.run_phrase('на телевизор https://youtu.be/%s' % VID)
        self.assertEqual(out['status'], 'PASS')
        self.assertEqual(self.tv.calls, [])
        self.router._ha_tv.wake.assert_called_once_with()

    def test_ha_failure_keeps_native_wake_and_opens_youtube_for_fallback(self):
        self.router._ha_tv.wake.side_effect = OSError('offline')
        self.cast.youtube_ok = False
        self.router._tv_cast.store.data = {'screenId': 's1'}
        self.assertEqual(self.run_phrase('на телевизор https://youtu.be/%s' % VID)['status'], 'PASS')
        self.assertEqual(self.tv.calls, [True])
        self.router._ha_tv.open_youtube.assert_called_once_with()

    def test_rejected_paired_video_is_not_success(self):
        from unittest.mock import Mock
        self.cast.youtube_ok = False
        self.router._tv_cast.store.data = {'screenId': 's1'}
        self.router._tv_cast.play = Mock(return_value=False)
        with self.assertRaises(Denied):
            self.run_phrase('на телевизор https://youtu.be/%s' % VID)

    def test_playback_confirms_only_matching_playing_video(self):
        from unittest.mock import Mock
        self.router._tv_cast.store.data = {'screenId': 's1'}
        for video, state, expected in [(VID, 'playing', True), ('5-wdT_xbVDQ', 'playing', False),
                                       (VID, 'paused', False), (None, 'unknown', False)]:
            self.router._tv_cast.status = Mock(return_value={'video_id': video, 'state': state})
            result = self.run_phrase('на телевизор https://youtu.be/%s' % VID)
            self.assertIs(result['playback']['confirmed'], expected)
            self.assertEqual('ещё не подтверждено' in result['speech'], not expected)

    def test_paired_transport_error_closes_phone_request_as_failure(self):
        from unittest.mock import Mock
        self.cast.youtube_ok = False
        self.router._tv_cast.store.data = {'screenId': 's1'}
        self.router._tv_cast.play = Mock(side_effect=RuntimeError('opaque library failure'))
        executor = PhoneExecutor(self.dir, router=self.router)
        item = executor.queue.add('на телевизор https://youtu.be/%s' % VID,
                                  auto={'task': 'tv-play', 'args': {'url': 'https://youtu.be/%s' % VID}})
        executor.tick()
        result = executor.queue.get(item['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('opaque library failure', result['speech'])

    def test_pairing_phrase_and_validation(self):
        self.assertEqual(understand('привяжи телевизор, код 1234 5678 9012')['steps'][0],
                         {'task': 'tv-link', 'args': {'code': '123456789012'}})
        for bad in ({'task': 'tv-link', 'args': {'code': '123'}}, {'task': 'tv-play', 'args': {'url': 'file:///c:/x'}}):
            with self.assertRaises(Denied):
                validate(bad)


class CertificateRetryTests(unittest.TestCase):
    def test_incomplete_chain_is_completed_not_skipped(self):
        from unittest import mock
        from developer_core.jarvis_local import tv_media
        calls = []

        class R:
            def start(self, inputs, headers, ca_file=None):
                calls.append(ca_file)
                if ca_file is None:
                    raise tv_media.RelayError('certificate')
                return 'http://pc/tok/index.m3u8'

            def stop(self):
                pass

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cast = FakeCast([])
        m = tv_media.TvMedia(tmp.name, 'tv', cast=cast, relay=R(),
                             resolver=lambda u: ('T', ['https://river-1.rutube.ru/v.m3u8'], {}))
        with mock.patch.object(tv_media, 'ca_bundle', return_value='bundle.pem') as bundle:
            self.assertEqual(m.play('https://rutube.ru/video/x/')['kind'], 'relay')
        self.assertEqual(calls, [None, 'bundle.pem'])  # retried with a completed chain, never without checks
        self.assertEqual(bundle.call_args[0][1], ['river-1.rutube.ru'])


class CastTvTests(unittest.TestCase):
    def test_another_app_playing_is_not_success(self):
        from types import SimpleNamespace as NS
        from developer_core.jarvis_local.tv_media import DEFAULT_RECEIVER, CastTv

        class CC:
            def __init__(self, takes_over):
                self.takes_over, self.launched = takes_over, []
                self.status = NS(app_id='2C6A6E3D')  # YouTube in front, playing its own video

            def start_app(self, app_id, force_launch=False, timeout=0):
                self.launched.append((app_id, force_launch))
                if self.takes_over:
                    self.status = NS(app_id=app_id)

            def disconnect(self, timeout=0):
                pass

        for takes_over, expected in ((True, True), (False, False)):
            cc = CC(takes_over)
            other = NS(content_id='yt-video', player_state='PLAYING', idle_reason=None)

            class MC:
                @property
                def status(self_inner):
                    if cc.status.app_id == DEFAULT_RECEIVER:
                        return NS(content_id=cc.url, player_state='PLAYING', idle_reason=None)
                    return other

                def play_media(self_inner, url, ctype, **kw):
                    cc.url = url

            cc.media_controller = MC()
            ok = CastTv('tv', connect=lambda host: cc).media('http://pc/x.m3u8', 'T', wait=2, sleep=lambda s: None)
            self.assertEqual(ok, expected)
            self.assertEqual(cc.launched, [(DEFAULT_RECEIVER, True)])


class RelayTests(unittest.TestCase):
    def test_ffmpeg_copies_to_hls_and_only_live_files_are_served(self):
        import http.client
        from developer_core.jarvis_local.tv_media import RELAY_PORT, Relay
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        seen = []

        class Proc:
            def __init__(self, cmd, **kw):
                seen.append(cmd)
                out = Path(cmd[-1]).parent
                for n in range(2):
                    (out / ('s%05d.ts' % n)).write_bytes(b'TS')
                (out / 'index.m3u8').write_bytes(b'#EXTM3U\n')

            def poll(self):
                return None

            def terminate(self):
                seen.append('terminated')

        relay = Relay(tmp.name, '127.0.0.1', ffmpeg='ffmpeg', popen=Proc)
        self.addCleanup(lambda: relay.server and (relay.server.shutdown(), relay.server.server_close()))
        url = relay.start(['https://cdn/v', 'https://cdn/a'], {'User-Agent': 'UA', 'X-Secret': 'no'}, sleep=lambda s: None)
        cmd = seen[0]
        self.assertEqual(cmd.count('-i'), 2)
        self.assertIn('copy', cmd)
        self.assertNotIn('X-Secret', ' '.join(cmd))
        self.assertTrue(url.startswith('http://127.0.0.1:%d/' % RELAY_PORT) and url.endswith('/index.m3u8'))
        c = http.client.HTTPConnection('127.0.0.1', RELAY_PORT, timeout=5)
        c.request('GET', url.split(str(RELAY_PORT), 1)[1])
        r = c.getresponse()
        self.assertEqual((r.status, r.read()), (200, b'#EXTM3U\n'))
        c.request('GET', '/')
        r = c.getresponse()
        r.read()
        self.assertEqual(r.status, 404)  # no directory listing
        relay.start(['https://cdn/v2'], {}, sleep=lambda s: None)
        self.assertIn('terminated', seen)  # one relay at a time


if __name__ == '__main__':
    unittest.main()
