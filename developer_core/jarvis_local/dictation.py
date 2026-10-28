"""Dictation into any window: hold the key (F9 by default), speak, release — the recognised text is
pasted where the cursor is. It runs inside the "Эй, Джарвис" loop and shares its microphone and Whisper.

The owner starts it by physically holding the key, and only the words they just said are pasted
(clipboard + Ctrl+V; the previous clipboard text is restored). Fillers are removed, a personal
dictionary fixes words Whisper gets wrong. The journal keeps only the length, never the text.
Dictionary: <state>/dictation_dictionary.json, e.g. {"джарвис": "JARVIS"}.
"""
from __future__ import annotations
import json
import re
import time

FILLERS = re.compile(r'(?:^|(?<=[\s,.]))(?:э+|э-э|эм+|м+|ммм+|ну вот|типа|как бы|короче|в общем)(?=[\s,.!?]|$),?\s*', re.I)


def clean(text, dictionary=None):
    out = FILLERS.sub('', (text or '').strip())
    out = re.sub(r'^(?:ну)[,\s]+', '', out, flags=re.I)  # "ну" is a filler only at the very start
    for wrong, right in (dictionary or {}).items():
        out = re.sub(r'(?<!\w)%s(?!\w)' % re.escape(wrong), right, out, flags=re.I)
    out = re.sub(r'\s{2,}', ' ', out).strip(' ,')
    return out[:1].upper() + out[1:] if out else ''


def load_dictionary(state_dir):
    try:
        data = json.loads((state_dir / 'dictation_dictionary.json').read_text(encoding='utf-8'))
        return {str(k)[:60]: str(v)[:60] for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _get_clip():
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
    except TypeError:
        return None
    finally:
        win32clipboard.CloseClipboard()


def _set_clip(text):
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        if text is not None:
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def _ctrl_v():
    import keyboard
    keyboard.send('ctrl+v')


def paste(text, get_clip=_get_clip, set_clip=_set_clip, send=_ctrl_v, sleep=time.sleep):
    """Paste into the focused window and put the owner's previous clipboard text back."""
    if not text:
        return False
    old = get_clip()
    set_clip(text)
    send()
    sleep(0.4)  # let the target app read the clipboard before restoring it
    set_clip(old)
    return True


class Dictation:
    def __init__(self, transcribe, state_dir, journal=None, paster=paste):
        self.transcribe, self.state_dir, self.journal, self.paster = transcribe, state_dir, journal, paster

    def finish(self, audio):
        """Audio (float32 16 kHz) recorded while the key was held -> pasted text (or None)."""
        if len(audio) < 16000 * 0.3:
            return None
        text = clean(self.transcribe(audio), load_dictionary(self.state_dir))
        if not text:
            return None
        self.paster(text)
        if self.journal is not None:
            self.journal.record('dictation', 'PASS', chars=len(text), rollback=[])
        return text
