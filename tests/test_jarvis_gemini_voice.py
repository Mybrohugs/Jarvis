"""Gemini TTS voice (no network): request shape, audio parsing, cache, key never leaks."""
import base64
from pathlib import Path
import tempfile
import unittest
from developer_core.jarvis_local.gemini_voice import GeminiVoice, audio_from, pcm_to_wav, request_body
from developer_core.jarvis_local.session import Denied


class FakeKeys:
    def load(self):
        return 'AIzaFAKEKEY0123456789abcdef'


class GeminiVoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = []

    def transport(self, path, body, key):
        self.calls.append((path, body, key))
        return {'steps': [{'content': [{'type': 'audio', 'data': base64.b64encode(b'\0\0' * 240).decode()}]}]}

    def voice(self, name='Charon'):
        return GeminiVoice(name, keys=FakeKeys(), transport=self.transport, cache=Path(self.tmp.name))

    def test_request_uses_voice_text_and_style(self):
        body = request_body('Готово.', 'Orus')
        self.assertEqual(body['generation_config']['speech_config'], [{'voice': 'Orus'}])
        part = body['input'][0]['content'][0]
        self.assertEqual(part['text'], 'Готово.')
        self.assertEqual(part['annotations'][0]['type'], 'speech_metadata')

    def test_pcm_is_wrapped_as_wav_and_cached(self):
        v = self.voice()
        first = v.speak('Готово.')
        self.assertEqual(first[:4], b'RIFF')
        self.assertEqual(v.speak('Готово.'), first)
        self.assertEqual(len(self.calls), 1)  # second time from cache, nothing sent

    def test_wav_passthrough(self):
        wav = pcm_to_wav(b'\0\0' * 10)
        self.assertEqual(audio_from({'steps': [{'content': [{'data': base64.b64encode(wav).decode()}]}]}), wav)

    def test_effect_applies_on_raw_cache(self):
        import numpy as np
        from developer_core.jarvis_local import voice_dsp
        tone = (np.sin(np.arange(4800) / 5) * 8000).astype('<i2').tobytes()
        self.transport = lambda path, body, key: {'steps': [{'content': [{'data': base64.b64encode(tone).decode()}]}]}
        raw = GeminiVoice('Schedar', keys=FakeKeys(), transport=self.transport, cache=Path(self.tmp.name),
                          effect=None).speak('Готово.')
        robot = GeminiVoice('Schedar', keys=FakeKeys(), transport=lambda *a: self.fail('must use cache'),
                            cache=Path(self.tmp.name), effect='robot-3').speak('Готово.')
        self.assertNotEqual(raw, robot)
        x, rate = voice_dsp.wav_to_array(robot)
        self.assertEqual((rate, len(x)), (24000, 4800))
        self.assertLessEqual(float(np.max(np.abs(x))), 0.9)

    def test_offline_falls_back_to_local_bank(self):
        from developer_core.jarvis_local.qwen_bank import bank_key
        bank = Path(self.tmp.name) / 'bank'
        bank.mkdir()
        local = pcm_to_wav(b'\1\0' * 2400)
        (bank / (bank_key('Готово.') + '.wav')).write_bytes(local)

        def offline(*a):
            raise Denied('Gemini unreachable: OSError')
        v = GeminiVoice('Schedar', keys=FakeKeys(), transport=offline, cache=Path(self.tmp.name) / 'c',
                        effect=None, bank=bank)
        self.assertEqual(v.speak('Готово.'), local)
        self.assertFalse(v.cached('Готово.'))  # local audio is not stored as Gemini audio
        with self.assertRaises(Denied):
            v.speak('Такой фразы в банке нет.')  # -> the reply stays text only

    def test_no_audio_and_bad_input_are_refused(self):
        with self.assertRaises(Denied):
            audio_from({'steps': [{'content': [{'type': 'text', 'text': 'no'}]}]})
        with self.assertRaises(Denied):
            GeminiVoice('NotAVoice', keys=FakeKeys(), transport=self.transport, cache=Path(self.tmp.name))
        with self.assertRaises(ValueError):
            self.voice().speak('x' * 401)


if __name__ == '__main__':
    unittest.main()
