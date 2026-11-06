import unittest
from developer_core.jarvis_local.tv_cast import _auth_state, _export_auth

class AuthCompatibilityTests(unittest.TestCase):
    def test_installed_library_round_trip_and_legacy_migration(self):
        from pyytlounge.models import AuthState
        from types import SimpleNamespace
        auth = AuthState()
        auth.screen_id = 'fake-screen'
        auth.lounge_id_token = 'fake-token'
        auth.refresh_token = 'fake-refresh'
        exported = _export_auth(SimpleNamespace(auth=auth))
        loaded = AuthState()
        loaded.deserialize(_auth_state(exported))
        self.assertEqual(loaded.screen_id, 'fake-screen')
        legacy = {'screenId': 'fake-screen', 'lounge_id_token': 'fake-token', 'refresh_token': 'fake-refresh'}
        loaded.deserialize(_auth_state(legacy))
        self.assertEqual(loaded.screen_id, 'fake-screen')
        self.assertEqual(loaded.refresh_token, 'fake-refresh')


class PlaybackObservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_for_real_event_and_closes_subscription(self):
        import asyncio
        from types import SimpleNamespace as NS
        from developer_core.jarvis_local.tv_cast import _observe_playback
        class Api:
            closed = False
            async def subscribe(self, callback):
                try:
                    await callback(NS(videoId='', state=NS(name='Stopped')))
                    await asyncio.sleep(0)
                    await callback(NS(videoId='5-wdT_xbVDQ', state=NS(name='Playing')))
                    await asyncio.Event().wait()
                finally:
                    self.closed = True
        api = Api()
        result = await _observe_playback(api, .1)
        self.assertEqual(result, {'video_id': '5-wdT_xbVDQ', 'state': 'playing'})
        self.assertTrue(api.closed)

    async def test_no_real_event_is_unknown_and_transport_error_propagates(self):
        import asyncio
        from developer_core.jarvis_local.tv_cast import _observe_playback
        class Quiet:
            async def subscribe(self, callback):
                await asyncio.Event().wait()
        self.assertEqual(await _observe_playback(Quiet(), .01), {'video_id': None, 'state': 'unknown'})
        class Broken:
            async def subscribe(self, callback):
                raise OSError('offline')
        with self.assertRaises(OSError):
            await _observe_playback(Broken(), .1)
