"""Features 7, 9, 10: PC <-> iPhone helpers (user-authorised 27.10.2026, all audited).

7  pc-find   — instant whole-disk file NAME search via Everything (es.exe); no file contents read.
   pc-send   — copy ONE found file into a one-time outbox; the phone downloads it once within 10 min.
9  pc-clip-get / pc-clip-set — PC clipboard text <-> phone (text only, 20 000 chars).
10 pc-screen — screenshot of the PC screen into the one-time outbox (PNG).
Sensitive locations (keys, browser profiles, credential stores, JARVIS secrets) are never sent.
"""
from __future__ import annotations
import datetime
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import time
from .session import Denied

ES = [Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'WinGet' / 'Links' / 'es.exe',
      Path(r'C:\Program Files\Everything\es.exe'), Path(r'C:\Program Files (x86)\Everything\es.exe')]
EVERYTHING = Path(r'C:\Program Files\Everything\Everything.exe')
MAX_SEND = 50 * 1024 * 1024
TTL = 600
SENSITIVE = re.compile(
    r'(\\\.ssh\\|\\\.gnupg\\|\\appdata\\local\\(google|microsoft\\edge|bravesoftware|yandex)\\.*\\user data\\|'
    r'\\appdata\\roaming\\mozilla\\firefox\\profiles\\|\\microsoft\\credentials\\|\\microsoft\\protect\\|'
    r'\.telegram-dev|\.sony-dev|\.mobile-dev|credentials?[^\\]*\.(bin|json)$|client_secret|api_keys\.json|'
    r'\.(pem|key|pfx|p12|kdbx|ovpn)$|\\\.jarvis_local\\|\\config\\certs\\|wallet)', re.I)


def es_path():
    for p in ES:
        if p.is_file():
            return p
    found = shutil.which('es.exe') or shutil.which('es')
    return Path(found) if found else None


class PCTools:
    def __init__(self, ws, run=subprocess.run, grab=None, clipboard=None):
        self.ws, self.run = ws, run
        self.grab = grab
        self.clipboard = clipboard
        self.outbox = ws.state / 'outbox'
        self.outbox.mkdir(exist_ok=True)
        self.last_file = ws.state / 'pc_last_search.json'

    # ---- 7: Everything search + one-time send ----
    def find(self, query, limit=10):
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 100 or any(c in query for c in '"|<>\x00'):
            raise Denied('Поисковый запрос: 2..100 символов, без кавычек')
        es = es_path()
        if es is None:
            raise Denied('Everything не установлен')
        argv = [str(es), '-n', str(limit * 3), '-sort', 'date-modified-descending', query.strip()]
        p = self.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace', timeout=15)
        if p.returncode == 8 and EVERYTHING.is_file():
            # Self-repair (level 2, approved scenario): Everything client not running -> start it once, retry.
            subprocess.Popen([str(EVERYTHING), '-startup'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            time.sleep(6)
            p = self.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace',
                         timeout=15)
            self.ws.journal.record('self-repair', 'PASS' if not p.returncode else 'FAILED',
                                   component='Everything client', action='started -startup', rollback=[])
        if p.returncode:
            raise Denied('Поиск Everything не удался (служба Everything запущена?)')
        files = [line.strip() for line in p.stdout.splitlines() if line.strip()]
        files = [f for f in files if not SENSITIVE.search(f)][:limit]
        self.last_file.write_text(json.dumps(files, ensure_ascii=False), encoding='utf-8')
        self.ws.journal.record('pc-find', 'PASS', query=query[:100], hits=len(files), rollback=[])
        if not files:
            return {'status': 'PASS', 'files': [], 'speech': 'Ничего не нашёл.'}
        names = '; '.join('%d — %s' % (i + 1, Path(f).name) for i, f in enumerate(files[:5]))
        return {'status': 'PASS', 'files': files,
                'speech': 'Нашёл %d. %s. Скажите «отправь номер», чтобы получить файл.' % (len(files), names)}

    def _publish(self, src_path=None, data=None, name='file'):
        token = secrets.token_urlsafe(18)
        folder = self.outbox / token
        folder.mkdir()
        target = folder / re.sub(r'[^\w.\-]+', '_', name)[:120]
        if src_path is not None:
            with open(src_path, 'rb') as a, target.open('xb') as b:
                shutil.copyfileobj(a, b, 1024 * 1024)
        else:
            with target.open('xb') as b:
                b.write(data)
        meta = {'file': target.name, 'expires': time.time() + TTL, 'used': False}
        (folder / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
        return '/api/file/' + token

    def send(self, index):
        try:
            files = json.loads(self.last_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise Denied('Сначала найдите файл') from None
        if type(index) is not int or not 1 <= index <= len(files):
            raise Denied('Нет файла с таким номером')
        path = Path(files[index - 1])
        if SENSITIVE.search(str(path)) or path.is_symlink() or not path.is_file():
            raise Denied('Этот файл отправить нельзя')
        if path.stat().st_size > MAX_SEND:
            raise Denied('Файл больше 50 МБ')
        link = self._publish(path, name=path.name)
        self.ws.journal.record('pc-send', 'PASS', file=path.name, size=path.stat().st_size, rollback=[])
        return {'status': 'PASS', 'link': link, 'speech': 'Файл %s готов: нажмите ссылку в течение 10 минут.' % path.name}

    # ---- 9: clipboard ----
    def _clip(self):
        if self.clipboard:
            return self.clipboard
        import win32clipboard
        import win32con

        class Win:
            def get(self):
                win32clipboard.OpenClipboard()
                try:
                    if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                        return ''
                    return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                finally:
                    win32clipboard.CloseClipboard()

            def set(self, text):
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                finally:
                    win32clipboard.CloseClipboard()
        return Win()

    def clip_get(self):
        text = self._clip().get() or ''
        self.ws.journal.record('pc-clip-get', 'PASS', chars=len(text), rollback=[])
        return {'status': 'PASS', 'text': text[:20000],
                'speech': 'В буфере компьютера пусто.' if not text else 'Текст из буфера компьютера на экране.'}

    def clip_set(self, text):
        if not isinstance(text, str) or not 1 <= len(text) <= 20000:
            raise Denied('Текст для буфера: 1..20000 символов')
        self._clip().set(text)
        self.ws.journal.record('pc-clip-set', 'PASS', chars=len(text), rollback=[])
        return {'status': 'PASS', 'speech': 'Скопировал в буфер компьютера.'}

    # ---- 10: screenshot ----
    def screen(self):
        grab = self.grab
        if grab is None:
            from PIL import ImageGrab
            grab = lambda: ImageGrab.grab(all_screens=False)  # noqa: E731
        import io
        img = grab()
        img.thumbnail((1920, 1920))
        buf = io.BytesIO()
        img.save(buf, 'PNG', optimize=True)
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        link = self._publish(data=buf.getvalue(), name='screen-%s.png' % stamp)
        self.ws.journal.record('pc-screen', 'PASS', bytes=len(buf.getvalue()), rollback=[])
        return {'status': 'PASS', 'link': link, 'speech': 'Снимок экрана готов: нажмите ссылку.'}
