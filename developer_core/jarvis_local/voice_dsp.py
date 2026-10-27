"""Pure-numpy voice processing for JARVIS (no developer_core imports).

Shared by voice_style (DEV venv) and voice_server (isolated clone venv, loaded by path).
"stark*" styles create the film-assistant character by processing only; no actor
recordings are used as a target.
"""
import io
import wave
import numpy as np

RATE = 16000
STYLES = ('assistant', 'hologram', 'intercom', 'stark-light', 'stark', 'stark-strong',
          'stark-metal-1', 'stark-metal-2', 'stark-metal-3')
METAL = {'stark-metal-1': 0.18, 'stark-metal-2': 0.30, 'stark-metal-3': 0.45}  # on top of stark-light
# "stark*": film-assistant character by processing only (deeper, measured, crisp, small metallic
# room, light digital doubling). No actor recordings are used as a target.
STARK = {'stark-light': (0.97, 0.10, 0.12), 'stark': (0.95, 0.16, 0.20), 'stark-strong': (0.93, 0.22, 0.30)}


def _resample(x, factor):
    """factor < 1 lowers pitch and slows slightly (tape-style)."""
    n = int(len(x) / factor)
    return np.interp(np.arange(n) * factor, np.arange(len(x)), x)


def _eq(x, bands, rate=RATE):
    """bands: list of (low_hz, high_hz, gain_db) applied in the frequency domain."""
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / rate)
    gain = np.ones_like(freqs)
    for lo, hi, db in bands:
        gain[(freqs >= lo) & (freqs < hi)] *= 10 ** (db / 20)
    return np.fft.irfft(spec * gain, len(x))


def _compress(x, threshold=0.25, ratio=3.0):
    env = np.convolve(np.abs(x), np.ones(160) / 160, mode='same')
    over = np.maximum(env / threshold, 1.0)
    return x / over ** (1 - 1 / ratio)


def _room(x, size=0.06, mix=0.18, seed=7):
    rng = np.random.default_rng(seed)
    n = int(RATE * size * 4)
    ir = rng.standard_normal(n) * np.exp(-np.arange(n) / (RATE * size))
    ir[0] = 0
    ir /= np.max(np.abs(ir)) * 8
    wet = np.convolve(x, ir)[:len(x)]
    return x + mix * wet / (np.max(np.abs(wet)) + 1e-9) * np.max(np.abs(x))


def _sheen(x, depth_ms=0.6, base_ms=9.0, rate_hz=0.4, mix=0.22):
    """Gentle chorus + short comb: the 'digital assistant' shimmer."""
    t = np.arange(len(x))
    delay = (base_ms + depth_ms * np.sin(2 * np.pi * rate_hz * t / RATE)) * RATE / 1000
    chorus = np.interp(t - delay, t, x, left=0.0)
    comb = np.concatenate([np.zeros(int(RATE * 0.0015)), x])[:len(x)]
    return x + mix * chorus + 0.12 * comb


def _metal(x, amount):
    """Short resonant combs (0.7/1.1/1.9 ms) + a touch of ring modulation: 'metallic' timbre."""
    y = x.copy()
    for ms, gain in ((0.7, 0.55), (1.1, 0.45), (1.9, 0.35)):
        d = int(RATE * ms / 1000)
        comb = np.zeros_like(x)
        for k in (1, 2, 3):  # a few feedback taps -> resonance
            comb[d * k:] += x[:len(x) - d * k] * (0.6 ** (k - 1))
        y += amount * gain * comb
    t = np.arange(len(x)) / RATE
    y += amount * 0.25 * x * np.sin(2 * np.pi * 45 * t)
    return y


def style(x, name):
    if name not in STYLES:
        raise ValueError('Unknown voice style')
    if name in METAL:
        y = style(x, 'stark-light')
        y = _eq(_metal(y, METAL[name]), [(0, 90, -12), (2500, 6000, 1.5)])
    elif name in STARK:
        pitch, room, sheen = STARK[name]
        y = _resample(x, pitch)
        y = _eq(y, [(0, 80, -24), (80, 200, 1.5), (200, 500, 2.5), (1800, 3500, 2.0), (3500, 7000, 4.0)])
        y = _compress(y, threshold=0.2, ratio=4.0)
        y = _room(y, size=0.035, mix=room)
        y = _sheen(y, depth_ms=0.3, base_ms=6.0, rate_hz=0.25, mix=sheen)
    elif name == 'intercom':
        y = _eq(x, [(0, 300, -30), (300, 3400, 2), (3400, RATE, -30)])
        y = np.tanh(y * 2.2) / 2.2
        y = _room(y, size=0.03, mix=0.10)
    else:
        y = _resample(x, 0.93)  # about 1.2 semitones lower, calm pace
        y = _eq(y, [(0, 90, -18), (90, 250, 2.5), (2500, 5000, 3.5), (6500, RATE, -4)])
        y = _compress(y)
        y = _room(y, size=0.05 if name == 'assistant' else 0.08, mix=0.14 if name == 'assistant' else 0.22)
        if name == 'hologram':
            y = _sheen(y)
    peak = np.max(np.abs(y)) + 1e-9
    return np.clip(y / peak * 0.89, -1, 1)


FILM = {'film-1': (0.6, 0.0), 'film-2': (1.0, 0.0), 'film-3': (1.5, 0.0),  # (room/double level, robot)
        'robot-1': (1.0, 0.5), 'robot-2': (1.2, 0.8), 'robot-3': (1.4, 1.1)}


def _robot(x, rate, amount):
    """Resonant metal combs + a little ring modulation: synthetic 'machine' timbre, words stay clear."""
    y = x.copy()
    for ms, gain in ((1.1, 0.30), (2.3, 0.22), (3.7, 0.15)):
        d = int(rate * ms / 1000)
        for k in (1, 2):
            if d * k < len(x):
                y[d * k:] += amount * gain * (0.55 ** (k - 1)) * x[:len(x) - d * k]
    t = np.arange(len(x)) / rate
    ring = x * np.sin(2 * np.pi * 60 * t)
    return (1 - 0.18 * amount) * y + 0.18 * amount * ring


def film(x, rate, name):
    """Film-assistant polish for an already good synthetic voice (no pitch change): clean low end,
    crisp presence, small reflective 'suit/lab' room, faint digital double and a hint of metal.
    robot-*: the same plus a stronger synthetic machine timbre."""
    level, robot = FILM[name]
    if robot:
        x = _robot(x, rate, robot)
    y = _eq(x, [(0, 90, -24), (150, 300, 1.0), (2500, 5000, 2.0 * level), (9000, rate, -2.0)], rate)
    y = _compress(y, threshold=0.22, ratio=3.0)
    out = y.copy()
    for ms, gain in ((7, 0.22), (13, 0.16), (19, 0.12), (29, 0.08)):  # early reflections
        d = int(rate * ms / 1000)
        if d < len(y):
            out[d:] += level * gain * y[:len(y) - d]
    t = np.arange(len(y))
    delay = (11 + 0.25 * np.sin(2 * np.pi * 0.3 * t / rate)) * rate / 1000
    out += level * 0.14 * np.interp(t - delay, t, y, left=0.0)  # digital double
    d = int(rate * 0.0011)
    if d < len(y):
        out[d:] += level * 0.07 * y[:len(y) - d]  # hint of metal
    peak = np.max(np.abs(out)) + 1e-9
    return np.clip(out / peak * 0.89, -1, 1)


def wav_to_array(data):
    with wave.open(io.BytesIO(data)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError('expected 16-bit mono WAV')
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float64) / 32768
    return x, rate


def to_wav(y, rate=RATE):
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((y * 32767).astype('<i2').tobytes())
    return out.getvalue()
