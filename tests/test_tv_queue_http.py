"""Real loopback gateway + real queue/executor; TV transport is replaced."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock
from developer_core.jarvis_local.pwa_gateway import build
from developer_core.jarvis_local.phone_executor import PhoneExecutor

class VideoQueueHTTPTests(unittest.TestCase):
    def test_mobile_existing_say_route_reaches_executor_and_returns_own_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            token = 'a' * 40
            origin = 'https://mobile-dev.example'
            server = build(root, token, 0, say_wait=0, allow_origins=[origin])
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def request(method, path, body=None):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                headers = {'Authorization': 'Bearer ' + token, 'Origin': origin,
                           'Content-Type': 'application/json'}
                connection.request(method, path, json.dumps(body) if body else None, headers)
                response = connection.getresponse()
                data = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader('Access-Control-Allow-Origin'), origin)
                connection.close()
                return data
            try:
                executor = PhoneExecutor(root)
                executor.router._tv_play = Mock(return_value={
                    'status': 'PASS', 'playback': {'confirmed': True},
                    'speech': 'На телевизоре воспроизводится выбранное видео.'})
                url = 'https://www.youtube.com/watch?v=5-wdT_xbVDQ'
                reply = request('POST', '/api/say', {'text': 'на телевизор ' + url})
                self.assertTrue(reply['queued'])
                executor.router._tv_play.assert_not_called()  # gateway only queues
                executor.tick()
                items = request('GET', '/api/queue')['items']
                own = next(item for item in items if item['id'] == reply['id'])
                self.assertEqual(own['status'], 'done')
                self.assertIn('воспроизводится выбранное видео', own['speech'])
                executor.router._tv_play.assert_called_once_with(url=url)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
