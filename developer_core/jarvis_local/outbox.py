"""Gateway side of the one-time outbox (features 7 and 10): expiring, single-use downloads.

Only files that the local executor already placed in <state>/outbox/<token>/ can be fetched;
the gateway never reads arbitrary paths.
"""
import json
from pathlib import Path
import re
import shutil
import time


def take_outbox(state_dir, token):
    """Returns (filename, bytes) once, or None if unknown, expired or already used."""
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_\-]{20,40}', token):
        return None
    source = Path(state_dir) / 'outbox' / token
    folder = source.with_name(token + '.taken')
    try:
        source.rename(folder)  # atomic claim: two simultaneous requests cannot both get the file
    except OSError:
        return None
    try:
        meta = json.loads((folder / 'meta.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        shutil.rmtree(folder, ignore_errors=True)
        return None
    target = folder / str(meta.get('file', ''))
    if time.time() > meta.get('expires', 0) or not target.is_file() or target.parent != folder:
        shutil.rmtree(folder, ignore_errors=True)
        return None
    data = target.read_bytes()
    shutil.rmtree(folder, ignore_errors=True)  # single use
    return target.name, data


def sweep(state_dir):
    """Removes expired outbox entries (called by the executor)."""
    root = Path(state_dir) / 'outbox'
    for folder in root.glob('*') if root.is_dir() else []:
        try:
            meta = json.loads((folder / 'meta.json').read_text(encoding='utf-8'))
            if time.time() > meta.get('expires', 0):
                shutil.rmtree(folder, ignore_errors=True)
        except (OSError, ValueError):
            shutil.rmtree(folder, ignore_errors=True)
