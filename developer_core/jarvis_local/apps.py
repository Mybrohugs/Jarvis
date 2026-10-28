"""Real programs, the safe way: open an allowlisted app, folder or site, open a found file, show the
desktop, list open windows. No free-form clicking or typing into other programs.

Allowlist (the owner's first five apps + a harmless helper): Проводник (known folders only),
browser (known sites only), Excel, Word, Telegram Desktop, Калькулятор. Files open only from
the last "найди на компьютере" result and only with document/media extensions (no macros, scripts,
executables or shortcuts). After each launch JARVIS verifies that a matching window appeared.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import time
from .session import Denied

HOME = Path.home()
FOLDERS = {'загрузки': HOME / 'Downloads', 'документы': HOME / 'Documents', 'рабочий стол': HOME / 'Desktop',
           'изображения': HOME / 'Pictures', 'картинки': HOME / 'Pictures', 'фото': HOME / 'Pictures',
           'музыка': HOME / 'Music', 'видео': HOME / 'Videos'}
FOLDER_TITLES = {'Downloads': 'Загрузки', 'Documents': 'Документы', 'Desktop': 'Рабочий стол',
                 'Pictures': 'Изображения', 'Music': 'Музыка', 'Videos': 'Видео'}  # Russian Explorer titles
SITES = {'youtube': 'https://www.youtube.com', 'ютуб': 'https://www.youtube.com', 'гугл': 'https://www.google.com',
         'google': 'https://www.google.com', 'яндекс': 'https://ya.ru', 'почту': 'https://mail.google.com',
         'почта': 'https://mail.google.com', 'gmail': 'https://mail.google.com', 'вк': 'https://vk.com',
         'вконтакте': 'https://vk.com', 'кинопоиск': 'https://www.kinopoisk.ru', 'github': 'https://github.com',
         'гитхаб': 'https://github.com', 'карты': 'https://yandex.ru/maps', 'погоду': 'https://yandex.ru/pogoda'}
APPS = {  # spoken name -> (launch target, window-title marker for verification)
    'excel': ('excel', 'Excel'), 'эксель': ('excel', 'Excel'), 'word': ('winword', 'Word'), 'ворд': ('winword', 'Word'),
    'телеграм': ('telegram', 'Telegram'), 'telegram': ('telegram', 'Telegram'),
    'калькулятор': ('calc', 'Калькулятор'),  # "блокнот" stays with the JARVIS Notepad tab operator
    'проводник': ('explorer', ''), 'браузер': ('https://ya.ru', ''),
}
OPEN_EXT = {'.docx', '.doc', '.xlsx', '.xls', '.csv', '.pdf', '.txt', '.md', '.pptx', '.jpg', '.jpeg', '.png',
            '.gif', '.webp', '.mp3', '.mp4', '.mkv', '.avi', '.mov', '.wav', '.flac'}


def telegram_exe():
    p = Path(os.environ.get('APPDATA', '')) / 'Telegram Desktop' / 'Telegram.exe'
    return str(p) if p.is_file() else 'tg://'


def window_titles():
    import win32gui
    out = []
    win32gui.EnumWindows(lambda h, acc: acc.append(win32gui.GetWindowText(h))
                         if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) else None, out)
    return out


def resolve(target):
    """Spoken target -> ('folder'|'site'|'app', value, marker) from the allowlists only."""
    t = re.sub(r'^(?:папк\w*|сайт|программ\w*|приложени\w*)\s+', '', target.strip().lower())
    if t in FOLDERS:
        return 'folder', str(FOLDERS[t]), FOLDER_TITLES[FOLDERS[t].name]
    if t in SITES:
        return 'site', SITES[t], ''
    if t in APPS:
        launch, marker = APPS[t]
        if launch == 'telegram':
            launch = telegram_exe()
        return ('site' if launch.startswith('https://') else 'app'), launch, marker
    raise Denied('Этого нет в разрешённом списке. Могу открыть: папки (%s), сайты (%s), программы (%s).'
                 % (', '.join(sorted(set(FOLDERS))[:6]), ', '.join(sorted({'ютуб', 'гугл', 'яндекс', 'почту', 'вк'})),
                    'Excel, Word, Telegram, калькулятор, блокнот'))


class Apps:
    def __init__(self, ws, start=os.startfile, titles=window_titles, sleep=time.sleep, keys=None):
        self.ws, self.start, self.titles, self.sleep = ws, start, titles, sleep
        self.keys = keys

    def _verify(self, marker, wait=6.0):
        """Post-action check: a window whose title contains the marker is open (new or brought forward)."""
        if not marker:
            return None  # sites/Explorer home: nothing reliable to check
        for _ in range(int(wait / 0.5)):
            if any(marker.lower() in t.lower() for t in self.titles()):
                return True
            self.sleep(0.5)
        return False

    def open(self, target):
        kind, value, marker = resolve(target)
        self.start(value)
        ok = self._verify(marker)
        self.ws.journal.record('app-open', 'PASS' if ok is not False else 'WARN', kind=kind, target=target[:40],
                               verified=ok, rollback=[])
        name = target.strip()
        speech = {True: 'Открыл %s.' % name, None: 'Открываю %s в браузере.' % name,
                  False: 'Запустил %s, но окно пока не появилось.' % name}[ok]
        return {'status': 'PASS' if ok is not False else 'WARN', 'kind': kind, 'verified': ok, 'speech': speech}

    def open_found(self, index):
        try:
            files = json.loads((self.ws.state / 'pc_last_search.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise Denied('Сначала найдите файл: «найди на компьютере …»') from None
        if type(index) is not int or not 1 <= index <= len(files):
            raise Denied('Нет файла с таким номером')
        from .pc_tools import SENSITIVE
        path = Path(files[index - 1])
        if path.suffix.lower() not in OPEN_EXT or SENSITIVE.search(str(path)) or path.is_symlink() or not path.is_file():
            raise Denied('Этот файл открывать не буду (тип %s)' % (path.suffix or 'без расширения'))
        self.start(str(path))
        ok = self._verify(path.stem[:30])
        self.ws.journal.record('file-open', 'PASS' if ok else 'WARN', file=path.name, verified=ok, rollback=[])
        return {'status': 'PASS' if ok else 'WARN', 'verified': ok,
                'speech': ('Открыл %s.' if ok else 'Открываю %s, окно пока не подтвердилось.') % path.name}

    def show_desktop(self):
        send = self.keys
        if send is None:
            import keyboard
            send = keyboard.send
        send('windows+d')
        self.ws.journal.record('desktop-show', 'PASS', rollback=[])
        return {'status': 'PASS', 'speech': 'Свернул все окна.'}

    def windows(self):
        known = ('Excel', 'Word', 'Telegram', 'Проводник', 'Chrome', 'Edge', 'Firefox', 'Блокнот', 'Калькулятор',
                 'PowerPoint', 'Visual Studio Code', 'Яндекс')
        seen = []
        for t in self.titles():
            app = next((k for k in known if k.lower() in t.lower()), None)
            if app and app not in seen:
                seen.append(app)
        return {'status': 'PASS', 'apps': seen,
                'speech': ('Открыто: %s.' % ', '.join(seen)) if seen else 'Из знакомых программ ничего не открыто.'}
