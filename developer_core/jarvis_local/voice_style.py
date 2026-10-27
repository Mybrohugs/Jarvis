"""JARVIS-style voice: Piper 'Denis' (CC0 dataset) + audio processing. No voice cloning.

The character comes from processing a licensed synthetic voice (lower pitch, presence,
compression, small room, digital sheen), not from imitating any real person's recordings.
"""
from __future__ import annotations
import io
import json
from pathlib import Path
import sys
import wave
import numpy as np
from . import voice_dsp
from .voice_dsp import RATE, STYLES, to_wav  # noqa: F401
from ..piper_voice import PiperVoice
from ..process_tree import run_tree
from .session import Denied


WORKER = Path(__file__).resolve().parents[1] / 'piper_worker.py'


def synthesize(text):
    """Raw Denis PCM16 @16 kHz through the existing verified Piper worker (Job Object limits)."""
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
        raise Denied('Speech text must be 1..500 characters')
    root = PiperVoice(None).model()
    if root is None:
        raise Denied('Piper voice not installed')
    result = run_tree([sys.executable, '-I', str(WORKER), str(root)],
                      json.dumps({'text': text}, ensure_ascii=False).encode('utf-8'), 60, label='Piper synthesis')
    if result.returncode or len(result.stdout) > 4_000_000:
        raise Denied('Piper synthesis failed')
    with wave.open(io.BytesIO(result.stdout), 'rb') as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float64) / 32768.0


def style(x, name):
    try:
        return voice_dsp.style(x, name)
    except ValueError as exc:
        raise Denied(str(exc)) from None


def speak_wav(text, name='hologram'):
    return to_wav(style(synthesize(text), name))
