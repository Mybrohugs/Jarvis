"""Features 3-5: push (mock push service), doctor (mock probes), briefing (mock weather), schedule."""
import datetime
import json
from pathlib import Path
import tempfile
import unittest
from developer_core.jarvis_local.briefing import Briefing
from developer_core.jarvis_local.doctor import Doctor
from developer_core.jarvis_local.home_media import Bravia, MediaOperator
from developer_core.jarvis_local.phone_executor import PhoneExecutor
from developer_core.jarvis_local.push import Push
from developer_core.jarvis_local.router import UnifiedTaskRouter
from developer_core.jarvis_local.session import Denied, TrustedLocalSession
from tests.test_jarvis_media import FakeTV

SUB = {'endpoint': 'https://web.push.apple.com/QXYZ', 'keys': {'p256dh': 'BA' * 40, 'auth': 'AA' * 8}}


def weather(url):
    if 'geocoding' in url:
        return {'results': [{'name': 'Тула', 'latitude': 54.2, 'longitude': 37.6, 'timezone': 'Europe/Moscow',
                             'country': 'Россия'}]}
    return {'current': {'temperature_2m': 6.6, 'weather_code': 61},
            'daily': {'temperature_2m_max': [9.2], 'temperature_2m_min': [2.1], 'precipitation_probability_max': [70]}}


class Daily(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.router = UnifiedTaskRouter(TrustedLocalSession.for_tests(lambda s, c: True), Path(self.tmp.name))
        self.router.media = MediaOperator(self.router.ws, Bravia(credentials=('192.168.1.2', 'x'), transport=FakeTV()),
                                          sleep=lambda s: None)
        self.sent = []

    def sender(self, **kw):
        self.sent.append(kw)

    def test_push_keys_subscriptions_and_send(self):
        p = Push(self.router.ws)
        pub = p.ensure_keys()
        self.assertEqual(pub, p.ensure_keys())  # stable
        self.assertNotIn('PRIVATE', pub)
        with self.assertRaises(ValueError):
            p.add_subscription({'endpoint': 'https://evil.example/x', 'keys': {}})
        p.add_subscription(SUB)
        r = p.notify('Тест', 'Привет', '/?briefing=1', sender=self.sender)
        self.assertEqual(r['sent'], 1)
        payload = json.loads(self.sent[0]['data'])
        self.assertEqual((payload['title'], payload['url']), ('Тест', '/?briefing=1'))
        self.assertTrue(self.sent[0]['vapid_private_key'].endswith('push_vapid_private.pem'))

    def test_push_without_devices(self):
        with self.assertRaises(Denied):
            Push(self.router.ws).notify('a', 'b', sender=self.sender)

    def test_doctor_reports_each_component(self):
        class P:
            stdout = json.dumps({'BackendState': 'Running', 'Peer': {'x': {'OS': 'iOS', 'Online': True}}})
        self.router.files.capability = lambda: {'v8_available': True, 'office_worker_ready': True}
        d = Doctor(self.router, run=lambda *a, **k: P(), http_ok=lambda port, path: 200 if port in (10790, 8888) else 503,
                   models=lambda: ['qwen3:8b'], keys={'Gemini': True, 'Claude': False})
        r = d.report()
        by = {c['name']: c['status'] for c in r['checks']}
        self.assertEqual((by['Шлюз iPhone'], by['Голос'], by['Телевизор'], by['Tailscale']), ('PASS', 'FAIL', 'PASS', 'PASS'))
        self.assertEqual((by['Поиск'], by['Модели'], by['Ключи']), ('PASS', 'WARN', 'WARN'))
        fixes = {c['name']: c['fix'] for c in r['checks']}
        self.assertEqual(fixes['Модели'], 'ollama pull qwen2.5vl:3b')
        self.assertIn('Голос', r['summary'])
        self.assertEqual(r['status'], 'FAIL')

    def test_briefing_text(self):
        b = Briefing(self.router, fetch=weather,
                     now=lambda: datetime.datetime(2026, 10, 27, 8, 0, tzinfo=datetime.timezone.utc))
        self.assertEqual(b.set_city('Тула')['city'], 'Тула')
        out = b.build('Все системы в норме.')
        self.assertIn('Доброе утро, сэр. Сегодня вторник, 27 октября.', out['text'])
        self.assertIn('Тула сейчас плюс 7 градусов, небольшой дождь. Днём до плюс 9, ночью до плюс 2.', out['text'])
        self.assertIn('возьмите зонт', out['text'])
        self.assertTrue(out['text'].endswith('Все системы в норме.'))
        saved = json.loads((self.router.ws.state / 'briefing_latest.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['text'], out['text'])
        from developer_core.jarvis_local.reminders import parse
        self.router.reminders.add(parse('напомни в 18:30 забрать посылку', datetime.datetime(2026, 10, 27, 8, 0)))
        self.assertIn('Напоминания на сегодня: 18:30 — забрать посылку.', b.build()['text'])

    def test_briefing_without_city_or_network(self):
        def broken(url):
            raise OSError('offline')
        out = Briefing(self.router, fetch=broken).build()
        self.assertIn('город не задан', out['text'])

    def test_schedule_runs_briefing_once_and_pushes(self):
        Push(self.router.ws).add_subscription(SUB)
        self.router.push.notify = lambda t, b, u: self.sent.append((t, u))
        self.router._doctor = lambda: {'status': 'PASS', 'summary': 'Все системы в норме.', 'checks': []}
        ex = PhoneExecutor(self.router.ws.root, router=self.router)
        ex.scheduled(datetime.datetime(2026, 10, 28, 8, 1))
        ex.scheduled(datetime.datetime(2026, 10, 28, 8, 30))
        self.assertEqual([t for t, u in self.sent], ['Утренний брифинг'])
        self.assertEqual(self.sent[0][1], '/?briefing=1')


if __name__ == '__main__':
    unittest.main()
