"""Files sent from the phone land in one inbox folder on the PC and nowhere else.

Name reduced to a safe basename, never overwrites (a numbered copy instead), no links followed, the
file is marked as coming from the internet (Windows Mark of the Web), and nothing is ever opened or run.
"""
from __future__ import annotations
import hashlib
import os
import re
from pathlib import Path

INBOX = Path.home() / 'Downloads' / 'JARVIS с телефона'
MAX_UPLOAD = 50_000_000
RESERVED = {'con', 'prn', 'aux', 'nul', *('com%d' % i for i in range(1, 10)), *('lpt%d' % i for i in range(1, 10))}


def safe_name(name):
    name = str(name or '').replace('\\', '/').split('/')[-1]
    name = re.sub(r'[\x00-\x1f<>:"|?*]', '_', name).strip().rstrip('. ')
    if not name or set(name) <= {'.', '_'}:
        name = 'file'
    stem, dot, ext = name.rpartition('.')
    if not dot:
        stem, ext = name, ''
    if stem.lower() in RESERVED:
        stem = '_' + stem
    stem, ext = stem[:100], ext[:15]
    return stem + ('.' + ext if ext else '')


def save(data, name, inbox=INBOX):
    if not isinstance(data, (bytes, bytearray)) or not 0 < len(data) <= MAX_UPLOAD:
        raise ValueError('file must be 1 byte .. 50 MB')
    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    if inbox.is_symlink() or os.path.realpath(inbox) != os.path.abspath(inbox):
        raise PermissionError('inbox must not be a link or junction')
    clean = safe_name(name)
    stem, dot, ext = clean.rpartition('.')
    if not dot:
        stem, ext = clean, ''
    for n in range(1, 1000):
        target = inbox / (clean if n == 1 else '%s (%d)%s' % (stem, n, '.' + ext if ext else ''))
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        if os.name == 'nt':  # Mark of the Web: Windows treats it like any downloaded file
            try:
                with open(str(target) + ':Zone.Identifier', 'w', encoding='ascii') as z:
                    z.write('[ZoneTransfer]\r\nZoneId=3\r\n')
            except OSError:
                pass
        return {'path': target, 'name': target.name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    raise FileExistsError('too many files with this name')
