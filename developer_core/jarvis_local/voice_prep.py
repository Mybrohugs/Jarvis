"""Prepare the user's OWN voice recording for local cloning (DEV venv: PyAV + faster-whisper).

1. Decode the newest recording (m4a/wav/mp3/aac) to mono 24 kHz.
2. Require the spoken consent sentence at the start (checked with local Whisper).
3. Split on pauses and keep clean 4..12 s segments as references.
Outputs <voice_dir>/prepared/{ref_*.wav, consent.json}. Nothing leaves the PC.
"""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import sys
import wave
import numpy as np

RATE = 24000
AUDIO = ('.m4a', '.wav', '.mp3', '.aac', '.caf', '.ogg', '.opus')
CONSENT_REQUIRED = ('разреша', 'голос')
CONSENT_ANY = ('коп', 'синтет')  # tiny Whisper may mishear "копию"
WHISPER = Path(__file__).resolve().parents[2] / '.models' / 'whisper-tiny'


def decode(path):
    import av
    frames = []
    with av.open(str(path)) as box:
        resampler = av.AudioResampler(format='flt', layout='mono', rate=RATE)
        for frame in box.decode(audio=0):
            for out in resampler.resample(frame):
                frames.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            frames.append(out.to_ndarray().reshape(-1))
    x = np.concatenate(frames).astype(np.float64)
    if len(x) < RATE * 20:
        raise SystemExit('DENIED: recording shorter than 20 seconds')
    if len(x) > RATE * 60 * 90:
        raise SystemExit('DENIED: recording longer than 90 minutes')
    return x / (np.max(np.abs(x)) + 1e-9) * 0.9


def segments(x, frame=0.03, min_len=4.0, max_len=12.0, gap=0.35):
    n = int(RATE * frame)
    energy = np.array([np.sqrt(np.mean(x[i:i + n] ** 2)) for i in range(0, len(x) - n, n)])
    loud = energy > max(0.02, np.percentile(energy, 30) * 2.5)
    out, start, quiet = [], None, 0
    for i, v in enumerate(loud):
        if v:
            start = i if start is None else start
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet * frame >= gap:
                out.append((start * n, (i - quiet + 1) * n))
                start, quiet = None, 0
    if start is not None:
        out.append((start * n, len(x)))
    merged = []
    for a, b in out:  # merge short phrases into 4..12 s chunks
        if merged and (b - merged[-1][0]) / RATE <= max_len and (merged[-1][1] - merged[-1][0]) / RATE < min_len:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return [(a, b) for a, b in merged if min_len <= (b - a) / RATE <= max_len]


def check_consent(x):
    from faster_whisper import WhisperModel
    model = WhisperModel(str(WHISPER), device='cpu', compute_type='int8', cpu_threads=2)
    head = x[:RATE * 25].astype(np.float32)
    head16 = np.interp(np.arange(0, len(head), RATE / 16000), np.arange(len(head)), head).astype(np.float32)
    parts, _ = model.transcribe(head16, language='ru', beam_size=1)
    text = ' '.join(p.text for p in parts).lower()
    return text, all(w in text for w in CONSENT_REQUIRED) and any(w in text for w in CONSENT_ANY)


def save(path, y):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype('<i2').tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--voice-dir', type=Path, required=True)
    a = ap.parse_args()
    rec_dir = a.voice_dir / 'recordings'
    files = sorted((p for p in rec_dir.iterdir() if p.suffix.lower() in AUDIO and p.is_file() and not p.is_symlink()),
                   key=lambda p: p.stat().st_mtime)
    if not files:
        raise SystemExit('DENIED: no recording in ' + str(rec_dir))
    src = files[-1]
    x = decode(src)
    heard, ok = check_consent(x)
    if not ok:
        raise SystemExit('DENIED: consent sentence not found at the start of the recording. Heard: ' + heard[:200])
    segs = segments(x)
    if len(segs) < 3:
        raise SystemExit('DENIED: not enough clean speech segments (%d); record in a quieter room' % len(segs))
    out = a.voice_dir / 'prepared'
    out.mkdir(exist_ok=True)
    for old in out.glob('ref_*.wav'):
        old.unlink()
    # Skip the consent segment(s) in the first ~12 s; prefer the loudest-steady chunks after it.
    usable = [s for s in segs if s[0] > RATE * 12] or segs
    scored = sorted(usable, key=lambda s: -np.sqrt(np.mean(x[s[0]:s[1]] ** 2)) / (np.std(np.abs(x[s[0]:s[1]])) + 1e-9))
    picked = sorted(scored[:6])
    for i, (s, e) in enumerate(picked, 1):
        save(out / ('ref_%d.wav' % i), x[s:e])
    info = {'source': src.name, 'source_sha256': hashlib.sha256(src.read_bytes()).hexdigest(),
            'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'consent_heard': heard[:300],
            'consent_ok': True, 'references': len(picked), 'duration_s': round(len(x) / RATE, 1),
            'use': 'personal JARVIS replies only; never calls, messages or impersonation'}
    (out / 'consent.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'PASS', **info}, ensure_ascii=False))


if __name__ == '__main__':
    sys.exit(main())
