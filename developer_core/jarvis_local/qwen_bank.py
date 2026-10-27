"""Offline JARVIS voice bank: frequent replies pre-recorded with the local Qwen3-TTS voice.

Runs ONLY in the isolated Qwen venv:  python -I qwen_bank.py <local_voice_dir> [--extra phrases.txt]
<local_voice_dir> holds ref.wav + ref.txt: the voice the user chose (designed by Qwen3-TTS VoiceDesign
from a text description, no real person's recording). The Base model clones it, so every phrase has
the same voice. Raw audio goes to <dir>/bank/<key>.wav; gemini_voice plays it when Gemini is
unreachable or rate-limited. The model is loaded only while this job runs (~4.4 GB VRAM).
"""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
MODEL = 'Qwen/Qwen3-TTS-12Hz-1.7B-Base'


def bank_key(text):
    return hashlib.sha256(('qwen-local|' + text.strip()).encode('utf-8')).hexdigest()[:32]


def phrases(extra=None):
    spec = importlib.util.spec_from_file_location('voice_server', HERE / 'voice_server.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # stdlib-only at import time
    out = list(mod.prewarm_phrases())
    if extra:
        out += [line.strip() for line in Path(extra).read_text(encoding='utf-8').splitlines() if line.strip()]
    return list(dict.fromkeys(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('voice_dir')
    ap.add_argument('--extra')
    a = ap.parse_args()
    root = Path(a.voice_dir)
    bank = root / 'bank'
    bank.mkdir(exist_ok=True)
    todo = [p for p in phrases(a.extra) if not (bank / (bank_key(p) + '.wav')).is_file()]
    print('BANK todo: %d phrases' % len(todo), flush=True)
    if not todo:
        return 0
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    model = Qwen3TTSModel.from_pretrained(MODEL, device_map='cuda:0', dtype=torch.bfloat16, attn_implementation='sdpa')
    x, sr = sf.read(str(root / 'ref.wav'))
    prompt = model.create_voice_clone_prompt(ref_audio=(x, sr), ref_text=(root / 'ref.txt').read_text(encoding='utf-8'))
    for i, text in enumerate(todo, 1):
        s = time.time()
        wavs, rate = model.generate_voice_clone(text=text, language='Russian', voice_clone_prompt=prompt)
        y = np.asarray(wavs[0], dtype=np.float64)
        y = y / (np.max(np.abs(y)) + 1e-9) * 0.89
        target = bank / (bank_key(text) + '.wav')
        tmp = target.with_suffix('.tmp.wav')
        sf.write(str(tmp), y, rate, subtype='PCM_16')
        os.replace(tmp, target)
        print('BANK %d/%d %.1f s' % (i, len(todo), time.time() - s), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
