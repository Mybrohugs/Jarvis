"""Video handoff boundaries and queue protocol, no TV or external network needed."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from developer_core.jarvis_local.tv_cast import VideoCaster, video_request, video_command, cast_video
from developer_core.jarvis_local.session import Denied, TrustedLocalSession
from developer_core.jarvis_local.workspace import Workspace
from developer_core.jarvis_local.pwa_gateway import build, PhoneQueue
from developer_core.jarvis_local.phone_executor import validate, speech_for, PhoneExecutor

URL = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'


class CastTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Workspace(Path(self.tmp.name))

    def test_video_routes(self):
        for url in (URL, 'https://youtu.be/dQw4w9WgXcQ?t=20', 'https://youtube.com/shorts/dQw4w9WgXcQ'):
            self.assertEqual(video_request(url)['media_id'], 'dQw4w9WgXcQ')
        for ext, mime in (('mp4', 'video/mp4'), ('webm', 'video/webm'), ('m3u8', 'application/x-mpegURL')):
            r = video_request('https://example.org/clip.' + ext + '?signed=secret')
            self.assertEqual((r['app'], r['media_type']), ('default_media_receiver', mime))

    def test_unsupported_and_unsafe_inputs(self):
        for url in ('file:///video.mp4', 'https://user:pw@example.org/a.mp4', 'http://127.0.0.1/a.mp4',
                    'http://192.168.1.2/a.mp4', 'http://localhost/a.mp4', 'https://example.org/page',
                    'https://youtube.com/playlist?list=123', 'https://example.org/a.mp4\n', None):
            with self.subTest(url=url), self.assertRaises(ValueError):
                video_request(url)

    def test_confirmation_and_audit_without_signed_url(self):
        sent = []
        v = VideoCaster(self.ws, lambda name, req: sent.append((name, req)))
        session = TrustedLocalSession.for_tests(lambda *a: True)
        with self.assertRaises(Denied):
            v.configure('BRAVIA', TrustedLocalSession.read_only())
        v.configure('BRAVIA', session)
        with self.assertRaises(Denied):
            v.play(URL, TrustedLocalSession.read_only())
        result = v.play('https://example.org/movie.mp4?token=SECRET', session)
        self.assertEqual(result['status'], 'REQUESTED')
        self.assertEqual(sent[0][0], 'BRAVIA')
        self.assertNotIn('SECRET', json.dumps(self.ws.journal.entries()))

    def test_phone_can_play_but_not_reconfigure(self):
        task = {'task': 'media-cast', 'args': {'url': URL}}
        self.assertEqual(validate(task), ('media-cast', {'url': URL}))
        with self.assertRaises(Denied):
            validate({'task': 'media-cast-config', 'args': {'name': 'Other'}})
        self.assertIn('экран', speech_for('media-cast', {'status': 'REQUESTED', 'speech': 'Проверьте экран'}))
        self.assertEqual(video_command('покажи на ТВ ' + URL), URL)
        self.assertIsNone(video_command('я нашёл видео ' + URL))

    def test_queued_video_is_dispatched_once_by_executor(self):
        from unittest.mock import Mock
        dispatch = Mock(return_value={'status': 'REQUESTED', 'speech': 'Проверьте экран'})
        router = SimpleNamespace(ws=self.ws, dispatch=dispatch)
        executor = PhoneExecutor(self.ws.root, router=router)
        item = executor.queue.add('Видео на ТВ', auto={'task': 'media-cast', 'args': {'url': URL}})
        self.assertEqual(executor.tick(), 1)
        self.assertEqual(executor.queue.get(item['id'])['status'], 'done')
        self.assertEqual(executor.tick(), 0)
        dispatch.assert_called_once_with('media-cast', url=URL)

    def test_russian_command_and_plan_runner_accept_requested(self):
        from unittest.mock import Mock
        from developer_core.jarvis_local.intent import understand
        from developer_core.jarvis_local.plan_runner import run_plan
        plan = understand('покажи на ТВ ' + URL)
        self.assertEqual(plan['steps'], [{'task': 'media-cast', 'args': {'url': URL}}])
        router = SimpleNamespace(ws=self.ws, dispatch=Mock(return_value={'status': 'REQUESTED'}))
        self.assertEqual(run_plan(router, plan)['status'], 'PASS')

    def test_cast_transport_and_cleanup(self):
        from unittest.mock import Mock
        cast, browser, quick = Mock(), Mock(), Mock()
        library = SimpleNamespace(get_listed_chromecasts=Mock(return_value=([cast], browser)))
        with patch.dict('sys.modules', {'pychromecast': library,
                                      'pychromecast.quick_play': SimpleNamespace(quick_play=quick)}):
            cast_video('BRAVIA', video_request(URL))
        quick.assert_called_once_with(cast, 'youtube', {'media_id': 'dQw4w9WgXcQ'}, timeout=10)
        cast.disconnect.assert_called_once()
        browser.stop_discovery.assert_called_once()

    def test_gateway_auth_validation_and_queue_only(self):
        server = build(self.ws.root, 'a' * 40)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def post(body, token='a' * 40):
            c = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            c.request('POST', '/api/tv/play', json.dumps(body),
                      {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
            r = c.getresponse()
            status, data = r.status, json.loads(r.read())
            c.close()
            return status, data
        self.assertEqual(post({'url': URL}, 'wrong')[0], 401)
        self.assertEqual(post({'url': 'https://example.org/page'})[0], 400)
        self.assertEqual(post({'url': URL, 'device': 'other'})[0], 400)
        status, result = post({'url': URL})
        self.assertEqual((status, result['status'], result['executed']), (202, 'pending', False))
        item = PhoneQueue(self.ws).get(result['id'])
        self.assertEqual(item['auto'], {'task': 'media-cast', 'args': {'url': URL}})


if __name__ == '__main__':
    unittest.main()
