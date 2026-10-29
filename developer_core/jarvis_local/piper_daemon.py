"""Long-lived Piper voice for the voice server: the model loads once (~3 s), then a phrase takes
well under a second instead of ~5 s per phrase with the one-shot piper_worker.

Same limits as piper_worker.py: no network, plain text only, bounded output. Protocol on stdin/stdout:
4-byte little-endian length + UTF-8 JSON {"text": ...} -> 4-byte length + 16 kHz mono PCM16 WAV
(length 0 = this phrase failed). Run with: python -I piper_daemon.py <model dir>
"""
import io
import json
import socket
import struct
import sys
import threading
import wave

MAX_TEXT = 500


def _deny_network(*args, **kwargs):
    raise ValueError('Runtime networking disabled')


def _load(root):
    socket.socket.connect = _deny_network
    socket.socket.connect_ex = _deny_network
    from pathlib import Path
    from piper import PiperVoice
    root = Path(root)
    return PiperVoice.load(root / 'ru_RU-denis-medium.onnx', config_path=root / 'ru_RU-denis-medium.onnx.json',
                           download_dir=root, use_cuda=False)


def _speak(voice, text):
    import numpy as np
    from piper.config import SynthesisConfig
    if not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT:
        raise ValueError('Invalid speech text')
    generated = io.BytesIO()
    with wave.open(generated, 'wb') as stream:
        voice.synthesize_wav(text, stream, syn_config=SynthesisConfig(noise_scale=0.0, noise_w_scale=0.0))
    with wave.open(io.BytesIO(generated.getvalue()), 'rb') as stream:
        rate, frames = stream.getframerate(), stream.getnframes()
        if stream.getnchannels() != 1 or stream.getsampwidth() != 2 or not 0 < frames <= rate * 120:
            raise ValueError('Invalid voice output')
        audio = np.frombuffer(stream.readframes(frames), dtype='<i2')
    count = int(len(audio) * 16000 / rate)
    resampled = np.interp(np.arange(count) * rate / 16000, np.arange(len(audio)), audio).astype('<i2')
    out = io.BytesIO()
    with wave.open(out, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(resampled.tobytes())
    return out.getvalue()


def _read(stream, n):
    data = b''
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


def serve(root, stdin, stdout):
    voice = _load(root)
    while True:
        try:
            (n,) = struct.unpack('<I', _read(stdin, 4))
        except EOFError:
            return
        if not 0 < n <= 8192:
            return  # framing broken: exit, the parent starts a fresh daemon
        try:
            wav = _speak(voice, json.loads(_read(stdin, n).decode('utf-8'))['text'])
        except EOFError:
            return
        except Exception:
            wav = b''
        stdout.write(struct.pack('<I', len(wav)) + wav)
        stdout.flush()


class PiperDaemon:
    """Parent side: one daemon per voice server, restarted if it dies; one phrase at a time."""

    def __init__(self, root, python=sys.executable):
        self.root, self.python, self.proc, self.lock = str(root), python, None, threading.Lock()

    def _start(self):
        import subprocess
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        self.proc = subprocess.Popen([self.python, '-I', __file__, self.root], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags)

    def speak(self, text):
        payload = json.dumps({'text': text}, ensure_ascii=False).encode('utf-8')
        with self.lock:
            for attempt in (1, 2):  # a dead daemon is restarted once
                if self.proc is None or self.proc.poll() is not None:
                    self._start()
                try:
                    self.proc.stdin.write(struct.pack('<I', len(payload)) + payload)
                    self.proc.stdin.flush()
                    (n,) = struct.unpack('<I', _read(self.proc.stdout, 4))
                    if not 0 < n <= 8_000_000:
                        raise ValueError('Piper could not voice this phrase')
                    return _read(self.proc.stdout, n)
                except (OSError, EOFError, struct.error):
                    self.close()
                    if attempt == 2:
                        raise

    def close(self):
        if self.proc is not None:
            try:
                self.proc.kill()
            except OSError:
                pass
            self.proc = None


if __name__ == '__main__':
    out = sys.stdout.buffer
    sys.stdout = sys.stderr  # nothing but frames may reach the parent's pipe
    serve(sys.argv[1], sys.stdin.buffer, out)
