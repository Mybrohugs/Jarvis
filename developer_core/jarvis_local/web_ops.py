"""Browser operator: multi-step web tasks in the owner's own browser.

Read: search the web and list numbered links (own SearXNG). Write (local approval, audited):
open a site or a numbered result in the default browser, search right on a known site
(YouTube, Wikipedia, maps, shops), download a file into Downloads, save the last web answer
into Documents\\JARVIS. Downloads and saved answers roll back like Explorer copies: the file
goes to the workspace quarantine, nothing is deleted. No clicking, typing, forms or logins
inside pages: the page is the owner's to use.
"""
from __future__ import annotations
import datetime
import hashlib
import ipaddress
import json
import os
import re
import time
import urllib.request
from urllib.parse import quote_plus, unquote, urlsplit
from .apps import window_titles
from .explorer import Explorer, _human, _safe_name
from .session import Denied

BROWSERS = ('chrome', 'edge', 'firefox', 'яндекс', 'yandex', 'opera', 'brave', 'vivaldi')
# spoken site -> search URL template ({q} = the query, URL-encoded)
SITE_SEARCH = {
    'youtube': 'https://www.youtube.com/results?search_query={q}',
    'google': 'https://www.google.com/search?q={q}',
    'yandex': 'https://ya.ru/search/?text={q}',
    'wikipedia': 'https://ru.wikipedia.org/w/index.php?search={q}',
    'maps': 'https://yandex.ru/maps/?text={q}',
    'kinopoisk': 'https://www.kinopoisk.ru/index.php?kp_query={q}',
    'ozon': 'https://www.ozon.ru/search/?text={q}',
    'market': 'https://market.yandex.ru/search?text={q}',
    'wildberries': 'https://www.wildberries.ru/catalog/0/search.aspx?search={q}',
    'avito': 'https://www.avito.ru/rossiya?q={q}',
    'github': 'https://github.com/search?q={q}',
}
SITE_TITLES = {'youtube': 'YouTube', 'google': 'Google', 'yandex': 'Яндекс', 'wikipedia': 'Википедия',
               'maps': 'Яндекс Карты', 'kinopoisk': 'Кинопоиск', 'ozon': 'Ozon', 'market': 'Яндекс Маркет',
               'wildberries': 'Wildberries', 'avito': 'Avito', 'github': 'GitHub'}
# spoken forms (any case ending) -> site key; used by intent.py
SPOKEN_SITES = (('youtube', r'ютуб\w*|youtube'), ('wikipedia', r'википеди\w*|вики\b'),
                ('maps', r'карт[еау]\b|картах'), ('kinopoisk', r'кинопоиск\w*'), ('ozon', r'озон\w*|ozon'),
                ('market', r'маркет\w*'), ('wildberries', r'вайлдберриз\w*|wildberries|\bвб\b'),
                ('avito', r'авито|avito'), ('github', r'гитхаб\w*|github'), ('google', r'гугл\w*|google'),
                ('yandex', r'яндекс\w*'))
# never downloaded: programs, scripts, shortcuts (the owner installs software himself)
RISKY_EXT = {'.exe', '.msi', '.msix', '.appx', '.bat', '.cmd', '.com', '.scr', '.pif', '.ps1', '.psm1', '.vbs', '.vbe',
             '.js', '.jse', '.wsf', '.wsh', '.hta', '.lnk', '.url', '.reg', '.dll', '.sys', '.jar', '.cpl', '.msc',
             '.inf', '.application', '.gadget', '.iso', '.img', '.vhd', '.vhdx', '.apk', '.docm', '.xlsm', '.pptm'}
MAX_DOWNLOAD = 500 * 1024 ** 2
MAX_URL = 500
SAVE_DIR = 'JARVIS'  # inside Documents


def clean_url(url):
    """Only plain public http(s) addresses: no logins in the URL, no local or private hosts."""
    url = str(url or '').strip()
    if not re.match(r'^https?://', url, re.I):
        url = 'https://' + url
    if len(url) > MAX_URL or any(ch in url for ch in ' \t\r\n"<>\\^`{|}'):
        raise Denied('Неподходящий адрес сайта')
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    if parts.scheme.lower() not in ('http', 'https') or parts.username or parts.password or not host:
        raise Denied('Открываю только обычные адреса http(s)')
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            raise Denied('Локальные и внутренние адреса не открываю')
    except ValueError:
        if host == 'localhost' or host.endswith(('.local', '.lan', '.internal', '.ts.net')) or '.' not in host \
                or not re.fullmatch(r'[a-z0-9а-яё.-]+', host):
            raise Denied('Локальные и внутренние адреса не открываю') from None
    return url


def _filename(url, disposition=''):
    m = re.search(r"filename\*\s*=\s*(?:UTF-8'')?([^;]+)", disposition or '', re.I) or \
        re.search(r'filename\s*=\s*"?([^";]+)"?', disposition or '', re.I)
    name = unquote(m.group(1)).strip() if m else unquote(urlsplit(url).path.rstrip('/').rsplit('/', 1)[-1])
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')[:120]
    return name or 'download'


def _default_fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 JARVIS-local'})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (scheme checked by clean_url)


class WebOps:
    def __init__(self, ws, start=os.startfile, titles=window_titles, sleep=time.sleep, search=None, fetch=None,
                 explorer=None):
        self.ws, self.start, self.titles, self.sleep = ws, start, titles, sleep
        self._search = search
        self.fetch = fetch or _default_fetch
        self.explorer = explorer or Explorer(ws)

    # ---- browser ----
    def _browser_windows(self):
        return [t for t in self.titles() if any(b in t.lower() for b in BROWSERS)]

    def _verify(self, before, wait=8.0):
        """A browser window appeared or its title changed (the new tab is in front)."""
        for _ in range(int(wait / 0.5)):
            now = self._browser_windows()
            if now and now != before:
                return True
            self.sleep(0.5)
        return bool(self._browser_windows()) and None  # browser open, change not seen: unconfirmed

    def open_url(self, url, label=None):
        url = clean_url(url)
        before = self._browser_windows()
        self.start(url)
        ok = self._verify(before)
        host = urlsplit(url).hostname
        self.ws.journal.record('web-open', 'PASS' if ok is not False else 'WARN', host=host, verified=ok, rollback=[])
        what = label or host
        speech = {True: 'Открыл %s в браузере.' % what, None: 'Открываю %s в браузере.' % what,
                  False: 'Отправил %s в браузер, но окно браузера не появилось.' % what}[ok]
        return {'status': 'PASS' if ok is not False else 'WARN', 'url': url, 'verified': ok, 'speech': speech}

    # ---- search ----
    def search(self, query):
        query = str(query or '').strip()
        if not 2 <= len(query) <= 300:
            raise Denied('Запрос должен быть от 2 до 300 символов')
        find = self._search
        if find is None:
            from .web_answer import search as find
        results = find(query)
        (self.ws.state / 'web_last_search.json').write_text(
            json.dumps({'query': query, 'results': results}, ensure_ascii=False), encoding='utf-8')
        self.ws.journal.record('web-search', 'PASS', found=len(results), rollback=[])
        if not results:
            return {'status': 'PASS', 'results': [], 'speech': 'По запросу «%s» ничего не нашёл.' % query}
        lines = ['%d: %s, сайт %s' % (i + 1, r['title'][:80], urlsplit(r['url']).hostname) for i, r in enumerate(results[:3])]
        return {'status': 'PASS', 'results': results,
                'text': '\n'.join('%d. %s\n   %s' % (i + 1, r['title'], r['url']) for i, r in enumerate(results)),
                'speech': 'Нашёл. %s. Скажите, какую ссылку открыть.' % '; '.join(lines)}

    def open_result(self, index):
        try:
            data = json.loads((self.ws.state / 'web_last_search.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise Denied('Сначала найдите что-нибудь: «найди в интернете … и покажи ссылки»') from None
        results = data.get('results') or []
        if type(index) is not int or not 1 <= index <= len(results):
            raise Denied('Нет ссылки с номером %s' % index)
        r = results[index - 1]
        return self.open_url(r['url'], label='ссылку %d, %s' % (index, urlsplit(r['url']).hostname))

    def site_search(self, site, query):
        if site not in SITE_SEARCH:
            raise Denied('Этот сайт не знаю. Могу искать на: ' + ', '.join(SITE_TITLES.values()))
        query = str(query or '').strip()
        if not 1 <= len(query) <= 200:
            raise Denied('Что искать?')
        result = self.open_url(SITE_SEARCH[site].format(q=quote_plus(query)), label=SITE_TITLES[site])
        result['speech'] = {True: 'Ищу «%s» на %s.', None: 'Ищу «%s» на %s, открываю браузер.',
                            False: 'Отправил поиск «%s» на %s, но браузер не появился.'}[result['verified']] % (
            query, SITE_TITLES[site])
        return result

    # ---- files ----
    def download(self, url, session):
        url = clean_url(url)
        if not url.lower().startswith('https://'):
            raise Denied('Скачиваю только по защищённой ссылке https')
        guess = _filename(url)
        if os.path.splitext(guess)[1].lower() in RISKY_EXT:
            raise Denied('Программы и скрипты не скачиваю (%s): поставьте их сами' % os.path.splitext(guess)[1])
        approved = session.confirm({'task': 'web-download', 'url': url, 'to': 'Загрузки',
                                    'limit': _human(MAX_DOWNLOAD)})
        folder = self.explorer.root('Downloads')
        with self.fetch(url) as r:
            final = getattr(r, 'geturl', lambda: url)()
            if not str(final).lower().startswith('https://'):
                raise Denied('Ссылка увела на незащищённый адрес, отменено')
            clean_url(final)
            ctype = (r.headers.get('Content-Type') or '').lower()
            if ctype.startswith('text/html'):
                raise Denied('По ссылке веб-страница, а не файл. Открыть её в браузере?')
            length = int(r.headers.get('Content-Length') or 0)
            if length > MAX_DOWNLOAD:
                raise Denied('Файл больше %s, не скачиваю' % _human(MAX_DOWNLOAD))
            name = _filename(final, r.headers.get('Content-Disposition', ''))
            if os.path.splitext(name)[1].lower() in RISKY_EXT:
                raise Denied('Программы и скрипты не скачиваю (%s): поставьте их сами' % os.path.splitext(name)[1])
            dst = self.explorer._free_name(folder, _safe_name(name))
            part = dst.with_name(dst.name + '.jarvis-part')
            digest, size = hashlib.sha256(), 0
            try:
                with part.open('xb') as out:
                    while True:
                        chunk = r.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_DOWNLOAD:
                            raise Denied('Файл больше %s, остановил' % _human(MAX_DOWNLOAD))
                        digest.update(chunk)
                        out.write(chunk)
                if length and size != length:
                    raise Denied('Файл скачался не целиком (%s из %s)' % (_human(size), _human(length)))
                os.rename(part, dst)  # fails instead of overwriting
            except BaseException:
                part.unlink(missing_ok=True)
                raise
        try:  # Mark of the Web: Windows and Office treat the file as coming from the internet
            with open(str(dst) + ':Zone.Identifier', 'w', encoding='ascii', errors='replace') as z:
                z.write('[ZoneTransfer]\nZoneId=3\nHostUrl=%s\n' % final)
        except OSError:
            pass
        sha = digest.hexdigest()
        ok = dst.is_file() and dst.stat().st_size == size
        entry = self.ws.journal.record('web-download', 'PASS' if ok else 'VERIFY_FAILED', host=urlsplit(final).hostname,
                                       file=dst.name, size=size, sha256=sha,
                                       rollback=[{'type': 'explorer_remove_copy', 'root': 'Downloads',
                                                  'path': dst.name, 'sha256': sha}], **approved)
        return {'status': entry['status'], 'audit_id': entry['id'], 'path': str(dst), 'size': size,
                'speech': 'Скачал «%s» в Загрузки, %s. Отменить: «отмени последнее действие».' % (dst.name, _human(size))}

    def save_answer(self, session, name=''):
        try:
            last = json.loads((self.ws.state / 'web_last_answer.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise Denied('Нечего сохранять: сначала спросите что-нибудь в интернете') from None
        stamp = datetime.datetime.fromtimestamp(last.get('at', time.time()))
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', name or last['question'])
        title = re.sub(r'\s+', ' ', title).strip(' .')[:60] or 'Ответ'
        docs = self.explorer.root('Documents')
        folder = docs / SAVE_DIR
        if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
            raise Denied('Documents\\JARVIS занят чем-то другим')
        dst = self.explorer._free_name(folder, _safe_name('%s %s.txt' % (stamp.strftime('%Y-%m-%d'), title))) \
            if folder.is_dir() else folder / _safe_name('%s %s.txt' % (stamp.strftime('%Y-%m-%d'), title))
        approved = session.confirm({'task': 'web-save', 'to': 'Документы\\%s\\%s' % (SAVE_DIR, dst.name)})
        created = not folder.exists()
        if created:
            folder.mkdir()
        body = 'Вопрос: %s\nКогда: %s\n\n%s\n' % (last['question'], stamp.strftime('%d.%m.%Y %H:%M'), last['text'])
        with dst.open('x', encoding='utf-8', newline='\r\n') as f:
            f.write(body)
        sha = hashlib.sha256(dst.read_bytes()).hexdigest()
        rel = SAVE_DIR + '/' + dst.name
        rollback = [{'type': 'explorer_remove_copy', 'root': 'Documents', 'path': rel, 'sha256': sha}]
        if created:
            rollback.append({'type': 'explorer_rmdir', 'root': 'Documents', 'path': SAVE_DIR})
        entry = self.ws.journal.record('web-save', 'PASS' if dst.is_file() else 'VERIFY_FAILED', file=rel,
                                       rollback=rollback, **approved)
        return {'status': entry['status'], 'audit_id': entry['id'], 'path': str(dst),
                'speech': 'Сохранил ответ в Документы, папка JARVIS, файл «%s».' % dst.name}
