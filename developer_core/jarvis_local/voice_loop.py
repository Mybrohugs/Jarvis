"""Feature 8: "Эй, Джарвис" at the PC — wake word -> phrase -> plan -> allowlisted action -> voice reply.

Wake word: openWakeWord pretrained "hey_jarvis" (Apache-2.0). STT: faster-whisper "small" (CPU int8).
Planning: rules first, then local LLM (same CaMeL-lite guards). Voice can run ONLY the tasks the
user already allowed for the phone, plus read-only status tasks; everything else is refused with
"подтвердите в консоли". Audio is processed in memory and never saved; the journal keeps task names.
Protocol (feature 11): scenes are announced before they run and reported after.
"""
from __future__ import annotations
import argparse
import numpy as np
from .llm_planner import understand_smart
from .phone_executor import ALLOWED as PHONE_ALLOWED, speech_for, validate
from .pwa_gateway import fetch_speech
from .router import UnifiedTaskRouter
from .session import Denied, TrustedLocalSession

RATE = 16000
FRAME = 1280  # 80 ms, what openWakeWord expects
READ_TASKS = ('status', 'doctor', 'briefing', 'media-status', 'audit', 'phone-queue', 'profile-show')
WAKE_THRESHOLD = 0.5


def say(text, speak=fetch_speech, play=None):
    """Speak with the user's JARVIS voice; fall back to silence (text is printed anyway)."""
    print('JARVIS:', text, flush=True)
    try:
        data = speak(text)
    except Exception:
        return False
    if play is None:
        import winsound
        play = lambda b: winsound.PlaySound(b, winsound.SND_MEMORY)  # noqa: E731
    play(data)
    return True


def beep():
    try:
        import winsound
        winsound.Beep(880, 120)
    except Exception:
        pass


class VoiceAssistant:
    def __init__(self, workspace, router=None, planner=understand_smart, speak=say):
        self.router = router or UnifiedTaskRouter(
            TrustedLocalSession.phone_allowlist(PHONE_ALLOWED), workspace)
        self.planner, self.speak = planner, speak

    def handle(self, text):
        """One recognised phrase -> spoken answer. Returns the answer text (also for tests)."""
        text = (text or '').strip()
        if len(text) < 2:
            return None
        plan = self.planner(text)
        if plan['intent'] != 'plan':
            answer = 'Это я не выполняю.' if plan['intent'] == 'refused' else 'Не понял, повторите, пожалуйста.'
            self.speak(answer)
            return answer
        answers = []
        for step in plan['steps']:
            task, args = step['task'], step['args']
            try:
                if task in READ_TASKS:
                    result = self.router.dispatch(task, **args)
                    answers.append(self._read_answer(task, result))
                    continue
                task, args = validate({'task': task, 'args': args})  # same allowlist as the phone
                if task == 'media-scene':
                    names = {'game': 'игры', 'movie': 'кино', 'night': 'сна'}
                    self.speak('Сэр, включаю режим %s.' % names[args['name']])  # protocol: announce first
                result = self.router.dispatch(task, **args)
                answers.append(speech_for(task, result))
            except Denied as exc:
                answers.append('Это нужно подтвердить кодом в консоли.' if 'allowlisted' in str(exc) or
                               'Not an' in str(exc) else 'Не получилось: %s.' % str(exc)[:80])
        answer = ' '.join(a for a in answers if a)
        labels = [s['task'] + (':' + s['args'].get('name', '') if s['task'] == 'media-scene' else '')
                  for s in plan['steps']]
        try:  # learn the habit and answer in the user's style
            if not any(lb.startswith('profile-forget') for lb in labels):
                self.router.profile.observe(text, labels)
            answer = self.router.profile.adapt(answer)
        except Exception:
            pass
        self.router.ws.journal.record('voice-command', 'PASS', tasks=[s['task'] for s in plan['steps']],
                                      chars=len(text), source=plan.get('source'), rollback=[])
        self.speak(answer)
        return answer

    @staticmethod
    def _read_answer(task, result):
        if task in ('doctor',):
            return result.get('summary', 'Готово.')
        if task == 'briefing':
            return result.get('text', '')
        if task == 'media-status':
            if result.get('power') != 'active':
                return 'Телевизор выключен.'
            return 'На экране %s, громкость %s.' % (result.get('input_title'), result.get('volume'))
        if task == 'profile-show':
            return result.get('speech', '')
        if task == 'phone-queue':
            n = len(result.get('pending', []))
            return 'С телефона ждут %d запрос(а).' % n if n else 'Очередь пуста.'
        return 'JARVIS на связи, всё работает.'


def record_phrase(stream_read, max_s=8.0, silence_s=0.8, min_s=0.6):
    """Collect audio after the wake word until a pause (simple energy VAD)."""
    frames, levels, quiet, heard = [], [], 0.0, 0.0  # durations in AUDIO seconds, not wall clock
    while heard < max_s:
        chunk = stream_read()
        frames.append(chunk)
        heard += len(chunk) / RATE
        level = float(np.sqrt(np.mean((chunk.astype(np.float32) / 32768) ** 2)))
        levels.append(level)
        noise = min(levels[:4])  # the quietest of the first frames ~ room noise
        quiet = 0.0 if level > max(0.012, noise * 3) else quiet + len(chunk) / RATE
        if quiet >= silence_s and heard >= min_s:
            break
    return np.concatenate(frames)


def main():
    ap = argparse.ArgumentParser(description='"Эй, Джарвис" voice loop at the PC')
    ap.add_argument('--workspace', required=True)
    ap.add_argument('--whisper', default='small')
    a = ap.parse_args()
    import sounddevice as sd
    from faster_whisper import WhisperModel
    from openwakeword.model import Model
    wake = Model(wakeword_models=['hey_jarvis'], inference_framework='onnx')
    stt = WhisperModel(a.whisper, device='cpu', compute_type='int8', cpu_threads=4)
    assistant = VoiceAssistant(a.workspace)
    stream = sd.InputStream(samplerate=RATE, channels=1, dtype='int16', blocksize=FRAME)
    stream.start()
    read = lambda: stream.read(FRAME)[0][:, 0].copy()  # noqa: E731
    print('VOICE LOOP ready: say "Эй, Джарвис"', flush=True)
    while True:
        chunk = read()
        scores = wake.predict(chunk)
        if max(scores.values()) < WAKE_THRESHOLD:
            continue
        beep()
        audio = record_phrase(read).astype(np.float32) / 32768
        wake.reset()
        segs, _ = stt.transcribe(audio, language='ru', beam_size=3, vad_filter=True)
        text = ' '.join(s.text for s in segs).strip()
        print('ВЫ:', text, flush=True)
        try:
            assistant.handle(text)
        except Exception as exc:
            print('voice error:', type(exc).__name__, str(exc)[:200], flush=True)


if __name__ == '__main__':
    main()
