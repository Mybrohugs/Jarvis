"""Learned skills: teach, save the last command, reuse LLM plans; no network, fake LLM."""
import json
import tempfile
import unittest
from pathlib import Path
from developer_core.jarvis_local.intent import understand
from developer_core.jarvis_local.llm_planner import llm_plan, understand_smart
from developer_core.jarvis_local.phone_executor import ALLOWED, PhoneExecutor, validate
from developer_core.jarvis_local.router import UnifiedTaskRouter
from developer_core.jarvis_local.session import Denied, TrustedLocalSession
from developer_core.jarvis_local.skills import key


class SkillTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.router = UnifiedTaskRouter(TrustedLocalSession.phone_allowlist(ALLOWED), Path(tmp.name))
        self.router.planner = understand  # rules only: no Ollama in tests

    def teach(self, phrase):
        step = understand(phrase)['steps'][0]
        task, args = validate(step)  # the phone allowlist accepts it
        return self.router.dispatch(task, **args)

    def test_teaching_phrases(self):
        self.assertEqual(understand('Джарвис, когда я говорю «кино», включи режим кино')['steps'][0],
                         {'task': 'skill-teach', 'args': {'name': 'кино', 'phrase': 'включи режим кино'}})
        self.assertEqual(understand('когда я скажу спокойной ночи, то включи режим сна')['steps'][0]['args'],
                         {'name': 'спокойной ночи', 'phrase': 'включи режим сна'})
        self.assertEqual(understand('запомни это как «отчёт»')['steps'][0], {'task': 'skill-save', 'args': {'name': 'отчёт'}})
        self.assertEqual(understand('забудь навык отчёт')['steps'][0]['task'], 'skill-forget')
        self.assertEqual(understand('какие у тебя навыки?')['steps'][0]['task'], 'skill-list')
        # the profile still owns "запомни, что …"
        self.assertEqual(understand('запомни, что я люблю кофе')['steps'][0]['task'], 'profile-remember')
        self.assertEqual(key('Джарвис, сделай Отчёт!'), key('отчет'))

    def test_teach_then_the_name_runs_the_plan(self):
        out = self.teach('когда я говорю «кино», включи режим кино')
        self.assertIn('Запомнил навык «кино»', out['speech'])
        plan = understand_smart('Джарвис, кино', skills=self.router.skills)
        self.assertEqual((plan['source'], plan['skill']), ('skill', 'кино'))
        self.assertEqual(plan['steps'], [{'task': 'media-scene', 'args': {'name': 'movie'}}])
        self.assertIn('«кино»', self.router.dispatch('skill-list')['speech'])
        self.assertIn('Забыл', self.teach('забудь навык кино')['speech'])
        self.assertIsNone(self.router.skills.match('кино'))

    def test_teach_refuses_what_rules_refuse(self):
        with self.assertRaises(Denied):
            self.teach('когда я говорю «чистка», удали все файлы в загрузках')
        with self.assertRaises(Denied):
            self.teach('когда я говорю «фу», сделай что-нибудь непонятное')
        self.assertEqual(self.router.skills.items(), [])

    def test_save_last_successful_command(self):
        with self.assertRaises(Denied):
            self.router.dispatch('skill-save', name='отчёт')
        plan = understand('включи режим игры')
        self.router.skills.done('включи режим игры', plan)
        self.teach('запомни это как «играем»')
        self.assertEqual(self.router.skills.match('играем')['steps'], plan['steps'])

    def test_llm_plan_is_reused_for_the_same_phrase(self):
        calls = []

        def chat(messages):
            calls.append(1)
            return json.dumps({'intent': 'plan', 'steps': [{'task': 'doctor', 'args': {}}]})
        phrase = 'глянь, всё ли у тебя в порядке с системами'
        first = understand_smart(phrase, chat=chat, skills=self.router.skills)
        self.assertEqual(first['source'], 'llm')
        self.router.skills.done(phrase, first)
        again = understand_smart(phrase + '!', chat=chat, skills=self.router.skills)
        self.assertEqual((again['source'], again['steps'], len(calls)), ('cache', first['steps'], 1))

    def test_llm_cannot_teach_and_skills_hold_no_skill_steps(self):
        chat = lambda m: json.dumps({'intent': 'plan', 'steps': [{'task': 'skill-forget', 'args': {'name': 'кино'}}]})
        self.assertEqual(llm_plan('сделай то самое', chat)['intent'], 'refused')
        self.router.skills.done('x', {'steps': [{'task': 'skill-list', 'args': {}}]})
        self.assertIsNone(self.router.skills.last())

    def test_a_tampered_store_is_ignored(self):
        (self.router.ws.state / 'skills.json').write_text(json.dumps(
            {'named': {'кино': {'name': 'кино', 'steps': [{'task': 'file-apply', 'args': {}}]}}}), encoding='utf-8')
        self.assertIsNone(self.router.skills.match('кино'))

    def test_phone_runs_a_skill_only_through_its_allowlist(self):
        self.teach('когда я говорю «кино», включи режим кино')
        ex = PhoneExecutor(self.dir, router=self.router, chat=lambda m: '{"intent": "unknown"}')
        ran = []
        self.router._media_scene = lambda name: ran.append(name) or {'status': 'PASS', 'speech': 'Кино.'}
        ex.queue.add('кино', interpret=True)
        ex.tick()
        self.assertEqual(ran, ['movie'])
        self.assertEqual(self.router.skills.match('кино')['source'], 'skill')
        self.assertEqual(self.router.skills.items()[0]['uses'], 1)


class Clock:
    def __init__(self):
        import datetime
        self.t = datetime.datetime(2026, 10, 30, 18, 0, 0)

    def __call__(self):
        return self.t

    def tick(self, seconds):
        import datetime
        self.t += datetime.timedelta(seconds=seconds)


class SelfLearningTests(unittest.TestCase):
    def setUp(self):
        from developer_core.jarvis_local.skills import Skills
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.router = UnifiedTaskRouter(TrustedLocalSession.phone_allowlist(ALLOWED), Path(tmp.name))
        self.router.planner = understand
        self.clock = Clock()
        self.skills = Skills(self.router.ws.state, clock=self.clock)
        self.router._skills = self.skills
        self.chat = lambda m: json.dumps({'intent': 'unknown'})

    def smart(self, phrase):
        return understand_smart(phrase, chat=self.chat, skills=self.skills)

    def test_a_correction_is_learned_once_the_meant_command_ran(self):
        self.assertEqual(self.smart('сделай уютно')['intent'], 'unknown')
        self.clock.tick(20)
        plan = self.smart('нет, я имел в виду режим кино')
        self.assertEqual((plan['steps'], plan['learn']), ([{'task': 'media-scene', 'args': {'name': 'movie'}}], 'сделай уютно'))
        self.assertIsNone(self.skills.match('сделай уютно'))  # not before it actually ran
        self.skills.done('нет, я имел в виду режим кино', plan)
        again = self.smart('Сделай уютно!')
        self.assertEqual((again['source'], again['steps']), ('learned', plan['steps']))
        self.assertIn('«сделай уютно»', self.router.dispatch('skill-list')['speech'])
        step = understand('забудь поправку сделай уютно')['steps'][0]
        self.assertIn('Забыл поправку', self.router.dispatch(*validate(step)[:1], **validate(step)[1])['speech'])
        self.assertIsNone(self.skills.match('сделай уютно'))

    def test_correction_phrasings_and_staleness(self):
        for p in ('не то, я хотел режим кино', 'Джарвис, нет, я имела в виду режим кино', 'я имел в виду режим кино'):
            self.smart('сделай уютно')
            self.assertEqual(self.smart(p).get('learn'), 'сделай уютно', p)
        self.smart('сделай уютно')
        self.clock.tick(11 * 60)  # too late to be a correction of that phrase
        self.assertNotIn('learn', self.smart('нет, я имел в виду режим кино'))
        self.smart('сделай уютно')  # a correction cannot teach skill commands
        self.assertNotIn('learn', self.smart('нет, я имел в виду забудь навык кино'))

    def run_pair(self):
        a, b = understand('включи свет'), understand('режим кино')
        first = self.skills.done('включи свет', a)
        self.clock.tick(60)
        offer = self.skills.done('режим кино', b)
        self.clock.tick(3600)
        return first, offer, a['steps'] + b['steps']

    def test_a_repeated_pair_is_offered_as_one_skill(self):
        self.assertEqual(self.run_pair()[:2], (None, None))
        self.assertEqual(self.run_pair()[:2], (None, None))
        _, offer, joined = self.run_pair()
        self.assertIn('«включи свет», а потом «режим кино»', offer)
        self.clock.tick(-3600 + 30)  # the owner answers right away
        out = self.router.dispatch('skill-save', name='вечер')
        self.assertIn('Запомнил навык «вечер»', out['speech'])
        self.assertEqual(self.skills.match('вечер')['steps'], joined)
        self.clock.tick(3600)
        self.assertIsNone(self.run_pair()[1])  # offered once, and now a skill anyway

    def test_no_offer_for_far_apart_commands_and_offer_expires(self):
        for _ in range(4):
            self.skills.done('включи свет', understand('включи свет'))
            self.clock.tick(600)
            self.assertIsNone(self.skills.done('режим кино', understand('режим кино')))
            self.clock.tick(600)
        self.run_pair(), self.run_pair()
        self.skills.done('включи свет', understand('включи свет'))
        self.clock.tick(60)
        self.assertIsNotNone(self.skills.done('режим кино', understand('режим кино')))
        self.skills.done('статус', understand('статус'))  # something else ran: "запомни это" means that now
        self.assertEqual(self.skills.last()['phrase'], 'статус')

    def test_facts_said_in_passing_are_remembered(self):
        cases = {'у меня есть собака Рекс': 'у вас есть собака Рекс', 'мою жену зовут Анна': 'вашу жену зовут Анна',
                 'я живу в Москве': 'вы живёте в Москве', 'мне 34 года': 'вам 34 года',
                 'кстати, моя любимая группа Сплин': 'ваша любимая группа Сплин'}
        for said, heard in cases.items():
            step = understand(said)['steps'][0]
            self.assertEqual((step['task'], step['args']['kind']), ('profile-remember', 'fact'), said)
            self.assertEqual(self.router.dispatch(*validate(step)[:1], **validate(step)[1])['speech'], 'Запомнил: %s.' % heard)
        self.assertIn('Рекс', self.router.dispatch('profile-show')['speech'])
        self.assertEqual(understand('включи свет')['steps'][0]['task'], 'home-switch')
        out = self.router.dispatch('profile-remember', kind='fact', text='у меня есть болезнь')
        self.assertEqual(out['status'], 'REFUSED')  # sensitive facts are still never stored


if __name__ == '__main__':
    unittest.main()
