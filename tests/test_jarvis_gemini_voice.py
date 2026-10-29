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

    def test_long_answers_and_used_up_quota_use_the_local_voice(self):
        import numpy as np
        said = []

        def piper(text):
            said.append(text)
            return np.zeros(1600), 16000
        v = GeminiVoice('Schedar', keys=FakeKeys(), transport=self.transport, cache=Path(self.tmp.name),
                        effect=None, bank=None, local=piper)
        long = 'Кубит может быть в суперпозиции. ' * 12
        self.assertEqual(v.speak(long)[:4], b'RIFF')
        self.assertEqual((said, self.calls), ([long.strip()], []))  # whole answer, nothing sent to Gemini

        def quota(*a):
            raise Denied('Gemini HTTP 429 Rate limit exceeded')
        v.transport = quota
        self.assertEqual(v.speak('Короткий ответ.')[:4], b'RIFF')
        self.assertEqual(said[-1], 'Короткий ответ.')

    def test_used_up_quota_is_remembered_across_restarts(self):
        def quota(path, body, key):
            raise Denied('Gemini HTTP 429 Rate limit exceeded (limit: 10 requests per day on Free Tier)')
        with self.assertRaises(Denied):
            GeminiVoice('Schedar', keys=FakeKeys(), transport=quota, cache=Path(self.tmp.name), bank=None).speak('Раз.')
        again = GeminiVoice('Schedar', keys=FakeKeys(), transport=lambda *a: self.fail('must not ask Gemini again'),
                            cache=Path(self.tmp.name), bank=None)
        with self.assertRaises(Denied):  # every model is resting after the restart too: nothing is sent
            again.speak('Два.')

    def test_no_audio_and_bad_input_are_refused(self):
        with self.assertRaises(Denied):
            audio_from({'steps': [{'content': [{'type': 'text', 'text': 'no'}]}]})
        with self.assertRaises(Denied):
            GeminiVoice('NotAVoice', keys=FakeKeys(), transport=self.transport, cache=Path(self.tmp.name))
        with self.assertRaises(ValueError):
            self.voice().speak('x' * 1001)

    def test_used_up_quota_moves_to_the_next_model(self):
        tried = []

        def quota(path, body, key):
            tried.append(body['model'])
            if body['model'] in ('gemini-3.8-flash-tts', 'gemini-3.8-flash-lite-tts'):
                raise Denied('Gemini HTTP 429 Rate limit exceeded (limit: 10 requests per day on Free Tier)')
            return self.transport(path, body, key)
        v = GeminiVoice('Schedar', keys=FakeKeys(), transport=quota, cache=Path(self.tmp.name), effect=None, bank=None)
        self.assertTrue(v.speak('Первая фраза.').startswith(b'RIFF'))
        self.assertEqual(tried, ['gemini-3.8-flash-tts', 'gemini-3.8-flash-lite-tts', 'gemini-3.1-flash-tts-preview'])
        # older models get the style as an instruction, never as metadata
        text = self.calls[-1][1]['input'][0]['content'][0]
        self.assertNotIn('annotations', text)
        self.assertTrue(text['text'].endswith('Первая фраза.'))
        tried.clear()
        v.speak('Вторая фраза.')  # resting models are skipped, no wasted requests
        self.assertEqual(tried, ['gemini-3.1-flash-tts-preview'])

    def test_offline_does_not_walk_the_chain(self):
        tried = []

        def offline(path, body, key):
            tried.append(body['model'])
            raise Denied('Gemini unreachable: OSError')
        v = GeminiVoice('Schedar', keys=FakeKeys(), transport=offline, cache=Path(self.tmp.name), effect=None, bank=None)
        with self.assertRaises(Denied):
            v.speak('Нет сети.')
        self.assertEqual(len(tried), 1)


if __name__ == '__main__':
    unittest.main()
