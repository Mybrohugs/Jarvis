"""Feature 8 logic (no microphone): phrase -> plan -> allowlisted action -> spoken answer."""
from pathlib import Path
import tempfile
import unittest
import numpy as np
from developer_core.jarvis_local.home_media import Bravia, MediaOperator
from developer_core.jarvis_local.intent import understand
from developer_core.jarvis_local.voice_loop import VoiceAssistant, record_phrase
from tests.test_jarvis_media import FakeTV


class VoiceLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.spoken = []
        self.va = VoiceAssistant(Path(self.tmp.name), planner=understand, speak=self.spoken.append)
        self.fake = FakeTV()
        self.va.router.media = MediaOperator(self.va.router.ws, Bravia(credentials=('192.168.1.2', 'x'),
                                                                       transport=self.fake), sleep=lambda s: None)

    def test_scene_is_announced_then_reported(self):
        self.va.handle('режим игры')
        self.assertEqual(self.spoken[0], 'Сэр, включаю режим игры.')
        self.assertIn('PlayStation на экране', self.spoken[1])

    def test_allowlisted_volume_and_read_status(self):
        self.assertEqual(self.va.handle('громкость 20'), 'Громкость 20.')
        self.assertIn('громкость 20', self.va.handle('что на телевизоре'))

    def test_writes_outside_allowlist_need_console(self):
        (self.va.router.ws.root / 'a.txt').write_text('a', encoding='utf-8')
        answer = self.va.handle('скопируй a.txt в b.txt')
        self.assertIn('подтвердить кодом', answer)
        self.assertFalse((self.va.router.ws.root / 'b.txt').exists())

    def test_refusal_and_unknown(self):
        self.assertEqual(self.va.handle('удали a.txt'), 'Это я не выполняю.')
        self.assertEqual(self.va.handle('свари кофе'), 'Не понял, повторите, пожалуйста.')

    def test_record_phrase_stops_on_pause(self):
        speech = (np.sin(np.arange(1280) / 3) * 8000).astype(np.int16)
        silence = np.zeros(1280, dtype=np.int16)
        chunks = iter([silence] * 2 + [speech] * 10 + [silence] * 30)
        audio = record_phrase(lambda: next(chunks), max_s=30)
        self.assertLess(len(audio), 1280 * 30)  # stopped well before all frames were consumed


if __name__ == '__main__':
    unittest.main()
