import io
import json
import unittest
from unittest.mock import patch
from developer_core.home_assistant import SonyConnector, HomeAssistantError, SONY_ENTITY

class FakeResponse:
    status = 200
    def __init__(self, value): self.value = value
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, size): return json.dumps(self.value).encode()

class ConnectorTests(unittest.TestCase):
    def test_read_only_sony(self):
        client = SonyConnector('fake-token')
        data = {'entity_id': SONY_ENTITY, 'state':'on', 'attributes':{'friendly_name':'BRAVIA', 'volume_level':0.25}}
        with patch.object(client._opener, 'open', return_value=FakeResponse(data)) as mock:
            result = client.status()
        self.assertEqual(result.state, 'on')
        self.assertEqual(result.volume_level, 0.25)
        request = mock.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertIn(SONY_ENTITY, request.full_url)
    def test_default_blocks_controls_before_network(self):
        client = SonyConnector('fake-token')
        with patch.object(client._opener, 'open') as mock:
            with self.assertRaises(HomeAssistantError): client.turn_off()
        mock.assert_not_called()
    def test_allowlisted_control_payload(self):
        client = SonyConnector('fake-token', allow_control=True)
        with patch.object(client._opener, 'open', return_value=FakeResponse([])) as mock:
            client.set_volume(25)
        req=mock.call_args.args[0]
        self.assertEqual(req.get_method(), 'POST')
        self.assertEqual(json.loads(req.data), {'entity_id': SONY_ENTITY, 'volume_level':0.25})
    def test_volume_rejects_invalid_values(self):
        client=SonyConnector('fake-token',allow_control=True)
        for val in (-1,101, True,25.5,'25'):
            with self.subTest(val=val), self.assertRaises(HomeAssistantError):
                client.set_volume(val)
    def test_wrong_entity_rejected(self):
        client=SonyConnector('fake-token')
        with patch.object(client._opener, 'open', return_value=FakeResponse({'entity_id':'media_player.other','state':'on'})):
            with self.assertRaises(HomeAssistantError):client.status()
    def test_token_never_in_error(self):
        client=SonyConnector('SECRET_FOR_TESTING')
        with patch.object(client._opener, 'open', side_effect=OSError('SECRET_FOR_TESTING')):
            with self.assertRaises(HomeAssistantError) as raised:client.status()
        self.assertNotIn('SECRET_FOR_TESTING',str(raised.exception))

if __name__ == '__main__': unittest.main()
