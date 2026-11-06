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


# The iOS share sheet often sends a photo or a note with no name at all. A harmless extension is
# guessed from the first bytes so the file opens in the right app; never an executable one.
MAGIC = ((b'\xff\xd8\xff', 'jpg'), (b'\x89PNG', 'png'), (b'%PDF', 'pdf'), (b'GIF8', 'gif'),
         (b'PK\x03\x04', 'zip'))


def guess_ext(data):
    head = bytes(data[:16])
    for sig, ext in MAGIC:
        if head.startswith(sig):
            return ext
    if head[4:8] == b'ftyp':
        brand = head[8:12]
        return 'heic' if brand in (b'heic', b'heix', b'mif1', b'msf1') else 'mov' if brand == b'qt  ' else 'mp4'
    try:
        bytes(data[:4096]).decode('utf-8')
        return 'txt'
    except UnicodeDecodeError:
        return ''


def save(data, name, inbox=INBOX):
    if not isinstance(data, (bytes, bytearray)) or not 0 < len(data) <= MAX_UPLOAD:
        raise ValueError('file must be 1 byte .. 50 MB')
    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    if inbox.is_symlink() or os.path.realpath(inbox) != os.path.abspath(inbox):
        raise PermissionError('inbox must not be a link or junction')
    clean = safe_name(name)
    if '.' not in clean.lstrip('.') and guess_ext(data):
        clean += '.' + guess_ext(data)
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
