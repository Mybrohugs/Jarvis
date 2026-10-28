"""'Что на экране?' (vision model replaced by a fake) and dictation (no microphone, no real clipboard)."""
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from developer_core.jarvis_local.dictation import Dictation, clean, paste
from developer_core.jarvis_local.intent import understand
from developer_core.jarvis_local.pc_tools import PCTools
from developer_core.jarvis_local.phone_executor import ALLOWED, validate
from developer_core.jarvis_local.router import UnifiedTaskRouter
from developer_core.jarvis_local.screen_vision import describe
from developer_core.jarvis_local.session import Denied, TrustedLocalSession


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.router = UnifiedTaskRouter(TrustedLocalSession.phone_allowlist(ALLOWED), self.tmp.name)
        from PIL import Image
        self.router.pc = PCTools(self.router.ws, grab=lambda: Image.new('RGB', (2560, 1440), (20, 20, 20)))
        self.seen = []
        self.router.vision = lambda png, q: self.seen.append((len(png), q)) or 'Открыт Excel, видна ошибка «#ДЕЛ/0!».'

    def test_phrase_and_answer_with_link(self):
        step = understand('Джарвис, что у меня на экране?')['steps'][0]
        self.assertEqual(step['task'], 'screen-describe')
        out = self.router.dispatch(*validate(step)[:1], **validate(step)[1])
        self.assertIn('#ДЕЛ/0!', out['speech'])
        self.assertTrue(out['link'].startswith('/api/file/'))
        self.assertEqual(understand('сделай скриншот')['steps'][0]['task'], 'pc-screen')

    def test_picture_is_downscaled_before_the_model(self):
        self.router.dispatch('screen-describe')
        self.assertLess(self.seen[0][0], 200_000)

    def test_prompt_carries_question_and_empty_answer_fails(self):
        got = {}
        describe(b'png', 'какая ошибка?', chat=lambda m, p, img: got.update(p=p) or 'ok')
        self.assertIn('какая ошибка?', got['p'])
        with self.assertRaises(Denied):
            describe(b'png', chat=lambda m, p, img: '')


class DictationTests(unittest.TestCase):
    def test_clean_fillers_and_dictionary(self):
        self.assertEqual(clean('э ну вот короче отправь отчёт джарвису'), 'Отправь отчёт джарвису')
        self.assertEqual(clean('скажи джарвис привет', {'джарвис': 'JARVIS'}), 'Скажи JARVIS привет')
        self.assertEqual(clean('   эээ  '), '')

    def test_paste_restores_previous_clipboard(self):
        clip = {'v': 'старый текст'}
        events = []
        paste('новый', get_clip=lambda: clip['v'], set_clip=lambda t: clip.update(v=t) or events.append(t),
              send=lambda: events.append('CTRL+V'), sleep=lambda s: None)
        self.assertEqual(events, ['новый', 'CTRL+V', 'старый текст'])
        self.assertEqual(clip['v'], 'старый текст')

    def test_finish_pastes_and_journals_only_length(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            (state / 'dictation_dictionary.json').write_text(json.dumps({'джарвис': 'JARVIS'}), encoding='utf-8')
            pasted, records = [], []

            class J:
                def record(self, task, status, **kw):
                    records.append((task, kw))
            dic = Dictation(lambda audio: 'ну привет джарвис', state, J(), paster=pasted.append)
            self.assertIsNone(dic.finish(np.zeros(1000, dtype=np.float32)))  # too short: ignored
            self.assertEqual(dic.finish(np.zeros(16000, dtype=np.float32)), 'Привет JARVIS')
            self.assertEqual(pasted, ['Привет JARVIS'])
            self.assertEqual(records, [('dictation', {'chars': 13, 'rollback': []})])


if __name__ == '__main__':
    unittest.main()
