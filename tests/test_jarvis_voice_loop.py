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


class FakePlayer:
    def __init__(self, frames):
        self.left, self.played, self.stopped = frames, [], False

    def play(self, data):
        self.played.append(data)

    def active(self):
        self.left -= 1
        return self.left > 0 and not self.stopped

    def stop(self):
        self.stopped = True


class FakeWake:
    def __init__(self, hit_at=None):
        self.n, self.hit_at, self.resets = 0, hit_at, 0

    def predict(self, chunk):
        self.n += 1
        return {'hey_jarvis': 0.9 if self.n == self.hit_at else 0.01}

    def reset(self):
        self.resets += 1


class BargeInTests(unittest.TestCase):
    def speaker(self, wake, frames=20):
        from developer_core.jarvis_local.voice_loop import BargeInSpeaker
        self.player = FakePlayer(frames)
        return BargeInSpeaker(lambda: np.zeros(1280, dtype=np.int16), wake, fetch=lambda t: b'RIFF', player=self.player)

    def test_reply_plays_to_the_end_without_wake_word(self):
        s = self.speaker(FakeWake())
        self.assertTrue(s('Готово.'))
        self.assertFalse(s.interrupted or self.player.stopped)

    def test_wake_word_cuts_in_and_later_phrases_stay_silent(self):
        wake = FakeWake(hit_at=3)
        s = self.speaker(wake)
        s('Сэр, включаю режим игры.')
        self.assertTrue(s.interrupted and self.player.stopped)
        self.assertEqual(wake.resets, 1)
        self.assertFalse(s('Готово. Приятной игры.'))  # the rest of this answer is not spoken
        self.assertEqual(len(self.player.played), 1)

    def test_voice_server_down_is_silent_not_fatal(self):
        from developer_core.jarvis_local.voice_loop import BargeInSpeaker

        def down(text):
            raise OSError('voice server down')
        s = BargeInSpeaker(lambda: None, FakeWake(), fetch=down, player=FakePlayer(5))
        self.assertFalse(s('Готово.'))


if __name__ == '__main__':
    unittest.main()
