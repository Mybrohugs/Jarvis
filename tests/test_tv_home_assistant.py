import json
import unittest
from unittest.mock import Mock, patch
from developer_core.home_assistant import SonyConnector, SONY_ENTITY, HomeAssistantError
from developer_core.jarvis_local.tv_home_assistant import HomeAssistantTv


class HomeAssistantTvTests(unittest.TestCase):
    def test_wake_only_when_off_and_never_switch_off(self):
        client = Mock()
        tv = HomeAssistantTv(lambda: client)
        for state, expected in [('off', True), ('on', False), ('playing', False), ('unavailable', None)]:
            client.status.return_value.state = state
            self.assertIs(tv.wake(), expected)
        client.turn_on.assert_called_once_with()
        client.turn_off.assert_not_called()

    def test_unconfigured_uses_existing_transport(self):
        tv = HomeAssistantTv(lambda: None)
        self.assertIsNone(tv.wake())
        tv.open_youtube()

    def test_only_fixed_app_and_entity_and_control_policy(self):
        client = SonyConnector('test', allow_control=True)
        with patch.object(client, '_request') as request:
            client.open_youtube()
        request.assert_called_once_with('/api/services/media_player/play_media', payload={
            'entity_id': SONY_ENTITY, 'media_content_id': 'YouTube', 'media_content_type': 'app'})
        client = SonyConnector('test')
        with patch.object(client, '_request') as request:
            with self.assertRaises(HomeAssistantError):
                client.open_youtube()
        request.assert_not_called()
