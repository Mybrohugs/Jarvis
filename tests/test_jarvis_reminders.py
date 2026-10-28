"""Reminders and timers: Russian time phrases, storage, firing, voice/phone allowlist."""
import datetime
from pathlib import Path
import tempfile
import unittest
from developer_core.jarvis_local.intent import understand
from developer_core.jarvis_local.phone_executor import ALLOWED, PhoneExecutor, validate
from developer_core.jarvis_local.reminders import Reminders, parse
from developer_core.jarvis_local.router import UnifiedTaskRouter
from developer_core.jarvis_local.session import Denied, TrustedLocalSession

NOW = datetime.datetime(2026, 10, 27, 17, 0)  # Tuesday


class ParseTests(unittest.TestCase):
    def check(self, phrase, due, text, repeat=None):
        p = parse(phrase, NOW)
        self.assertIsNotNone(p, phrase)
        self.assertEqual((p['due'], p['text'], p['repeat']), (due, text, repeat), phrase)

    def test_relative(self):
        self.check('напомни через 20 минут выключить духовку', NOW + datetime.timedelta(minutes=20), 'выключить духовку')
        self.check('Напомни мне через полчаса позвонить маме', NOW + datetime.timedelta(minutes=30), 'позвонить маме')
        self.check('напомни через час что пора спать', NOW + datetime.timedelta(hours=1), 'пора спать')
        self.check('напомни через 2 часа забрать посылку', NOW + datetime.timedelta(hours=2), 'забрать посылку')
        self.check('напомни через пять минут', NOW + datetime.timedelta(minutes=5), 'Напоминание')

    def test_absolute_and_tomorrow(self):
        self.check('напомни в 18:30 про тренировку', NOW.replace(hour=18, minute=30), 'тренировку')
        self.check('напомни в 9 утра вынести мусор', (NOW + datetime.timedelta(days=1)).replace(hour=9, minute=0),
                   'вынести мусор')
        self.check('напомни в 7 вечера покормить кота', NOW.replace(hour=19, minute=0), 'покормить кота')
        self.check('напомни завтра в 10:15 о встрече', (NOW + datetime.timedelta(days=1)).replace(hour=10, minute=15),
                   'встрече')

    def test_repeating(self):
        self.check('напоминай каждый день в 8:00 пить воду', (NOW + datetime.timedelta(days=1)).replace(hour=8, minute=0),
                   'пить воду', 'daily')
        self.check('напоминай каждую пятницу в 10 отчёт', datetime.datetime(2026, 10, 30, 10, 0), 'отчёт', 'weekly:4')

    def test_timer(self):
        self.check('поставь таймер на 5 минут', NOW + datetime.timedelta(minutes=5), 'Таймер на 5 минут истёк')
        self.check('таймер 10 минут макароны', NOW + datetime.timedelta(minutes=10), 'макароны')

    def test_not_reminders(self):
        for phrase in ('включи режим игры', 'напомни', 'напомни в 25:00 что-то', 'таймер на много'):
            self.assertIsNone(parse(phrase, NOW), phrase)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = Reminders(Path(self.tmp.name))

    def test_one_shot_fires_once_and_repeat_reschedules(self):
        self.r.add(parse('напомни через 10 минут чай', NOW))
        self.r.add(parse('напоминай каждый день в 17:05 зарядка', NOW))
        self.assertEqual(self.r.due(NOW), [])
        fired = self.r.due(NOW + datetime.timedelta(minutes=11))
        self.assertEqual(sorted(i['text'] for i in fired), ['зарядка', 'чай'])
        left = self.r.items()
        self.assertEqual([i['text'] for i in left], ['зарядка'])
        self.assertEqual(left[0]['due'], '2026-10-28T17:05:00')

    def test_cancel_by_words_and_all(self):
        self.r.add(parse('напомни через 10 минут чай', NOW))
        self.r.add(parse('напомни через 20 минут кофе', NOW))
        self.assertEqual(self.r.cancel('кофе'), 1)
        self.assertEqual(self.r.cancel('все'), 1)
        self.assertEqual(self.r.items(), [])


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.router = UnifiedTaskRouter(TrustedLocalSession.phone_allowlist(ALLOWED), self.tmp.name)

    def test_intent_even_with_words_that_are_otherwise_refused(self):
        plan = understand('Джарвис, напомни через 20 минут удалить старые фото')
        self.assertEqual(plan['intent'], 'plan')
        self.assertEqual(plan['steps'][0]['task'], 'reminder-add')
        self.assertEqual(understand('какие у меня напоминания')['steps'][0]['task'], 'reminder-list')
        self.assertEqual(understand('отмени напоминание про чай')['steps'][0]['args'], {'what': 'чай'})
        self.assertEqual(understand('отмени все напоминания')['steps'][0]['args'], {'what': 'все'})
        self.assertEqual(understand('отмени последнее действие')['steps'][0]['task'], 'rollback-last')

    def test_phone_and_voice_can_add_list_cancel(self):
        task, args = validate({'task': 'reminder-add', 'args': {'phrase': 'напомни через 5 минут чай'}})
        out = self.router.dispatch(task, **args)
        self.assertIn('Напомню сегодня', out['speech'])
        self.assertIn('чай', self.router.dispatch('reminder-list')['speech'])
        with self.assertRaises(Denied):
            validate({'task': 'reminder-add', 'args': {'phrase': 'x' * 400}})
        with self.assertRaises(Denied):
            self.router.dispatch('reminder-add', phrase='напомни когда-нибудь')
        self.assertEqual(self.router.dispatch('reminder-cancel', what='чай')['cancelled'], 1)

    def test_executor_speaks_due_reminders(self):
        self.router.reminders.add(parse('напомни через 1 минуту выключить духовку', NOW))
        ex = PhoneExecutor(self.tmp.name, router=self.router)
        said = []
        fired = ex.fire_reminders(now=NOW + datetime.timedelta(minutes=2), speak=said.append)
        self.assertEqual(len(fired), 1)
        self.assertIn('напоминаю: выключить духовку', said[0])
        self.assertEqual(ex.fire_reminders(now=NOW + datetime.timedelta(minutes=3), speak=said.append), [])


if __name__ == '__main__':
    unittest.main()
