"""Russian command understanding + multi-step plan execution.

Unit: the 31-command Russian set must map exactly to the expected plans (incl. refusals).
Integration (real V8): a multi-step plan runs, verifies, and "отмени последнее" undoes it.
Approvals are approval_mode='test-auto'.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from developer_core.jarvis_local.intent import understand, validate_plan
from developer_core.jarvis_local.plan_runner import run_plan
from developer_core.jarvis_local.router import DEFAULT_V8, UnifiedTaskRouter
from developer_core.jarvis_local.session import TrustedLocalSession

CASES = json.loads((Path(__file__).parent / 'data' / 'jarvis_ru_commands.json').read_text(encoding='utf-8'))
V8_READY = ((DEFAULT_V8 / '.venv-ufo-v8' / 'Scripts' / 'python.exe').is_file()
            and importlib.util.find_spec('developer_core.ufo_unified') is not None)  # full JARVIS DEV tree


class RussianCommandSet(unittest.TestCase):
    def test_every_command(self):
        failures = []
        for case in CASES:
            plan = understand(case['say'])
            if plan['intent'] != case['intent']:
                failures.append((case['say'], 'intent', plan))
                continue
            if case['intent'] != 'plan':
                continue
            tasks = [s['task'] for s in plan['steps']]
            if tasks != case['tasks']:
                failures.append((case['say'], 'tasks', tasks))
                continue
            for want, step in zip(case.get('args', []), plan['steps']):
                for k, v in want.items():
                    if step['args'].get(k) != v:
                        failures.append((case['say'], k, step['args'].get(k)))
        self.assertEqual(failures, [], json.dumps(failures, ensure_ascii=False, indent=1))
        print('\nRU_COMMAND_SET=%d/%d' % (len(CASES) - len(failures), len(CASES)))

    def test_llm_style_plans_are_validated(self):
        for bad in ({'intent': 'plan', 'steps': [{'task': 'file-apply', 'args': {}}]},
                    {'intent': 'plan', 'steps': [{'task': 'shell', 'args': {'cmd': 'whoami'}}]},
                    {'intent': 'plan', 'steps': []},
                    {'intent': 'plan', 'steps': [{'task': 'status', 'args': {}, 'extra': 1}]}):
            with self.assertRaises(ValueError):
                validate_plan(bad)


@unittest.skipUnless(V8_READY, 'V8 engine/venv not installed')
class PlanExecution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'ws'
        (self.root / 'docs').mkdir(parents=True)
        (self.root / 'archive').mkdir()
        (self.root / 'docs' / 'a.txt').write_text('alpha', encoding='utf-8')
        from docx import Document
        d = Document()
        d.add_paragraph('Отчёт')
        d.save(self.root / 'docs' / 'r.docx')
        self.seen = []
        self.router = UnifiedTaskRouter(TrustedLocalSession.for_tests(lambda s, c: self.seen.append(s) or True), self.root)

    def test_multistep_then_undo(self):
        result = run_plan(self.router, understand('добавь в docs/r.docx абзац «Итоги» и прочитай результат'))
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['completed'][1]['result']['paragraphs'], ['Отчёт', 'Итоги'])
        self.assertTrue((self.root / 'docs' / 'r-jarvis.docx').exists())
        undo = run_plan(self.router, understand('отмени последнее действие'))
        self.assertEqual(undo['status'], 'PASS', undo)
        self.assertFalse((self.root / 'docs' / 'r-jarvis.docx').exists())
        nothing = run_plan(self.router, understand('отмени последнее действие'))
        self.assertIn('Нечего отменять', nothing['completed'][0]['result']['note'])

    def test_copy_and_journal(self):
        result = run_plan(self.router, understand('скопируй docs/a.txt в archive/a.txt и покажи журнал'))
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual((self.root / 'archive' / 'a.txt').read_text(encoding='utf-8'), 'alpha')
        self.assertEqual(len(result['undo_available']), 1)
        self.assertEqual(self.seen[0]['task'], 'file-apply')

    def test_stops_and_reports_undo(self):
        (self.root / 'archive' / 'b.txt').write_text('keep', encoding='utf-8')
        plan = {'intent': 'plan', 'steps': [
            {'task': 'file-plan', 'args': {'entries': [{'op': 'copy', 'src': 'docs/a.txt', 'dst': 'archive/a.txt'}]}},
            {'task': 'file-plan', 'args': {'entries': [{'op': 'copy', 'src': 'docs/a.txt', 'dst': 'archive/b.txt'}]}}]}
        result = run_plan(self.router, plan)
        self.assertEqual(result['status'], 'STOPPED')
        self.assertEqual(result['failed_step'], 2)
        self.assertEqual(len(result['undo_available']), 1)
        self.assertEqual((self.root / 'archive' / 'b.txt').read_text(encoding='utf-8'), 'keep')

    def test_declined_approval_changes_nothing(self):
        router = UnifiedTaskRouter(TrustedLocalSession.for_tests(lambda s, c: False), self.root)
        result = run_plan(router, understand('скопируй docs/a.txt в archive/a.txt'))
        self.assertEqual(result['status'], 'STOPPED')
        self.assertFalse((self.root / 'archive' / 'a.txt').exists())


if __name__ == '__main__':
    unittest.main()
