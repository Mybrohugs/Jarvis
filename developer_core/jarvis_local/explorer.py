"""Explorer operator: the owner's real folders (allow-list only), not just the workspace.

Read: list a folder, find duplicates. Write: create a folder, sort files into type folders,
copy or move a file between allowed folders. Every write goes plan -> local approval ->
act -> verify -> audit with a rollback. Rollback never deletes user data: moved files go
back, created copies go to the workspace quarantine, created folders are removed only if
they are still empty. Nothing is ever overwritten (Windows rename fails on an existing name).
"""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import time
from .session import Denied
from .workspace import sha256

HOME = Path.home()
ROOTS = {'Downloads': HOME / 'Downloads', 'Documents': HOME / 'Documents', 'Desktop': HOME / 'Desktop',
         'Pictures': HOME / 'Pictures', 'Music': HOME / 'Music', 'Videos': HOME / 'Videos'}
TITLES = {'Downloads': 'Загрузки', 'Documents': 'Документы', 'Desktop': 'Рабочий стол', 'Pictures': 'Изображения',
          'Music': 'Музыка', 'Videos': 'Видео'}
# spoken forms (any case ending) -> root key; used by intent.py too
SPOKEN = (('Downloads', r'загрузк\w*|загрузок'), ('Documents', r'документ\w*'), ('Desktop', r'рабоч\w+\s+стол\w*'),
          ('Pictures', r'изображени\w*|картинк\w*|фотографи\w*|\bфото\b'), ('Music', r'музык\w*'),
          ('Videos', r'\bвидео\b'))
CATEGORIES = (
    ('Документы', {'.doc', '.docx', '.odt', '.rtf', '.txt', '.md', '.djvu', '.fb2', '.epub'}),
    ('PDF', {'.pdf'}),
    ('Таблицы', {'.xls', '.xlsx', '.xlsm', '.ods', '.csv'}),
    ('Презентации', {'.ppt', '.pptx', '.odp', '.key'}),
    ('Картинки', {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.heic', '.svg', '.tif', '.tiff', '.ico', '.raw'}),
    ('Видео', {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.webm', '.m4v', '.flv'}),
    ('Музыка', {'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.wma'}),
    ('Архивы', {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.iso'}),
    ('Установщики', {'.exe', '.msi', '.msix', '.appx', '.apk'}),
    ('Код', {'.py', '.js', '.ts', '.json', '.html', '.css', '.ps1', '.bat', '.sh', '.xml', '.yaml', '.yml', '.ipynb'}),
)
CATEGORY_NAMES = {name for name, _ in CATEGORIES} | {'Прочее'}
# never touched by "sort": in-progress downloads, shortcuts, system files
SKIP_EXT = {'.crdownload', '.part', '.partial', '.tmp', '.download', '.lnk', '.url', '.ini', '.sys'}
FRESH_SECONDS = 120          # a file changed in the last 2 min may still be downloading
MAX_SORT = 500               # files per sort run
MAX_DUP_FILES = 3000
MAX_HASH_BYTES = 2 * 1024 ** 3


def category(name):
    ext = Path(name).suffix.lower()
    for cat, exts in CATEGORIES:
        if ext in exts:
            return cat
    return 'Прочее'


def _is_link(p):
    return p.is_symlink() or getattr(p, 'is_junction', lambda: False)()


def _hidden(p):
    if p.name.startswith(('.', '~$')):
        return True
    attrs = getattr(p.stat(), 'st_file_attributes', 0)
    return bool(attrs & 0x6)  # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM


def _safe_name(name):
    if not isinstance(name, str) or not name.strip() or len(name) > 120 or name in ('.', '..') \
            or any(ch in name for ch in '<>:"/\\|?*\x00') or name.rstrip(' .') != name:
        raise Denied('Недопустимое имя: ' + str(name)[:60])
    return name


def _human(n):
    for unit in ('Б', 'КБ', 'МБ', 'ГБ'):
        if n < 1024 or unit == 'ГБ':
            return ('%d %s' % (n, unit)) if unit == 'Б' else ('%.1f %s' % (n, unit))
        n /= 1024


def _identity(p):
    st = p.stat()
    return {'size': st.st_size, 'ino': st.st_ino}


class Explorer:
    def __init__(self, ws, roots=None, now=time.time):
        self.ws, self.now = ws, now
        self.roots = dict(roots or ROOTS)

    # ---- paths ----
    def root(self, key):
        if key not in self.roots:
            raise Denied('Папка не в разрешённом списке: ' + str(key))
        r = Path(self.roots[key])
        if _is_link(r) or not r.is_dir():
            raise Denied('Папка недоступна: ' + TITLES.get(key, key))
        return r.resolve()

    def path(self, key, rel, *, must_exist=None):
        base = self.root(key)
        parts = [_safe_name(x) for x in str(rel).replace('\\', '/').split('/') if x]
        if not parts:
            raise Denied('Пустой путь')
        cur = base
        for part in parts:
            cur = cur / part
            if _is_link(cur):
                raise Denied('Ярлыки-ссылки (symlink/junction) не открываю')
        if not cur.resolve().is_relative_to(base):
            raise Denied('Вне разрешённой папки')
        if must_exist == 'file' and not cur.is_file():
            raise Denied('Файл не найден: %s' % '/'.join(parts))
        if must_exist == 'dir' and not cur.is_dir():
            raise Denied('Папка не найдена: %s' % '/'.join(parts))
        return cur

    def _files(self, key, sub=''):
        base = self.path(key, sub, must_exist='dir') if sub else self.root(key)
        out = []
        for p in base.iterdir():
            try:
                if p.is_file() and not _is_link(p) and not _hidden(p):
                    out.append(p)
            except OSError:
                continue
        return out

    def find_file(self, key, name):
        """Exact name, else the only file whose name contains the words (case-insensitive)."""
        base = self.root(key)
        exact = base / _safe_name(name)
        if exact.is_file() and not _is_link(exact):
            return exact
        low = name.lower()
        hits = [p for p in self._files(key) if low in p.name.lower()]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise Denied('В папке «%s» нет файла «%s»' % (TITLES[key], name))
        raise Denied('Подходит несколько файлов: %s. Назовите точнее.' % ', '.join(p.name for p in hits[:5]))

    # ---- read ----
    def listing(self, key, limit=15):
        base = self.root(key)
        files, dirs = [], []
        for p in base.iterdir():
            try:
                if _is_link(p) or _hidden(p):
                    continue
                st = p.stat()
                (dirs if p.is_dir() else files).append({'name': p.name, 'size': st.st_size, 'modified': st.st_mtime,
                                                        'kind': 'folder' if p.is_dir() else category(p.name)})
            except OSError:
                continue
        files.sort(key=lambda x: -x['modified'])
        cats = {}
        for f in files:
            cats[f['kind']] = cats.get(f['kind'], 0) + 1
        top = ', '.join('%s %d' % kv for kv in sorted(cats.items(), key=lambda kv: -kv[1])[:4])
        speech = 'В папке «%s» %d файлов и %d папок.' % (TITLES[key], len(files), len(dirs))
        if files:
            speech += ' Больше всего: %s. Последний: %s.' % (top, files[0]['name'])
        return {'status': 'PASS', 'folder': TITLES[key], 'path': str(base), 'files': files[:limit],
                'folders': sorted(d['name'] for d in dirs)[:limit], 'counts': cats, 'speech': speech}

    def duplicates(self, key):
        files = self._files(key)[:MAX_DUP_FILES]
        by_size = {}
        for p in files:
            size = p.stat().st_size
            if 0 < size <= MAX_HASH_BYTES:
                by_size.setdefault(size, []).append(p)
        groups = []
        for size, same in by_size.items():
            if len(same) < 2:
                continue
            by_hash = {}
            for p in same:
                by_hash.setdefault(sha256(p), []).append(p)
            for digest, ps in by_hash.items():
                if len(ps) > 1:
                    ps.sort(key=lambda p: p.stat().st_mtime)
                    groups.append({'size': size, 'sha256': digest, 'files': [p.name for p in ps]})
        groups.sort(key=lambda g: -g['size'] * (len(g['files']) - 1))
        wasted = sum(g['size'] * (len(g['files']) - 1) for g in groups)
        if groups:
            speech = 'Нашёл %d групп одинаковых файлов в папке «%s», лишнее занимает %s. Например: %s. Удалять сам не буду.' % (
                len(groups), TITLES[key], _human(wasted), ' = '.join(groups[0]['files'][:3]))
        else:
            speech = 'В папке «%s» одинаковых файлов нет.' % TITLES[key]
        return {'status': 'PASS', 'groups': groups[:50], 'wasted_bytes': wasted, 'checked': len(files),
                'speech': speech}

    # ---- write ----
    def _confirm(self, session, summary):
        return session.confirm(summary)

    def mkdir(self, key, name, session):
        target = self.path(key, _safe_name(name))
        if target.exists():
            raise Denied('Папка «%s» уже есть в «%s»' % (name, TITLES[key]))
        approved = self._confirm(session, {'task': 'explorer-mkdir', 'folder': TITLES[key], 'name': name})
        target.mkdir()
        ok = target.is_dir()
        entry = self.ws.journal.record('explorer-mkdir', 'PASS' if ok else 'VERIFY_FAILED', root=key, path=name,
                                       rollback=[{'type': 'explorer_rmdir', 'root': key, 'path': name}], **approved)
        return {'status': entry['status'], 'audit_id': entry['id'], 'path': str(target),
                'speech': 'Создал папку «%s» в «%s».' % (name, TITLES[key])}

    def sort_plan(self, key):
        moves, now = [], self.now()
        for p in sorted(self._files(key), key=lambda p: p.name.lower()):
            if p.suffix.lower() in SKIP_EXT or now - p.stat().st_mtime < FRESH_SECONDS:
                continue
            moves.append({'src': p.name, 'dst': category(p.name) + '/' + p.name})
            if len(moves) >= MAX_SORT:
                break
        counts = {}
        for m in moves:
            counts[m['dst'].split('/')[0]] = counts.get(m['dst'].split('/')[0], 0) + 1
        return {'folder': TITLES[key], 'moves': moves, 'counts': counts}

    def _free_name(self, folder, name):
        stem, ext = os.path.splitext(name)
        for i in range(1, 100):
            cand = folder / (name if i == 1 else '%s (%d)%s' % (stem, i, ext))
            if not cand.exists():
                return cand
        raise Denied('Не нашёл свободного имени для ' + name)

    def sort_apply(self, key, session):
        plan = self.sort_plan(key)
        if not plan['moves']:
            return {'status': 'PASS', 'moved': 0, 'speech': 'В папке «%s» нечего раскладывать.' % TITLES[key]}
        approved = self._confirm(session, {'task': 'explorer-sort', 'folder': plan['folder'], 'files': len(plan['moves']),
                                           'into': plan['counts'], 'first': [m['src'] for m in plan['moves'][:10]]})
        base = self.root(key)
        rollback, created, moved, failed = [], [], [], []
        for m in plan['moves']:
            src = base / m['src']
            cat = m['dst'].split('/')[0]
            folder = base / cat
            try:
                if not folder.exists():
                    folder.mkdir()
                    created.append(cat)
                if _is_link(folder) or not folder.is_dir():
                    raise OSError('target is not a plain folder')
                ident = _identity(src)
                dst = self._free_name(folder, src.name)
                os.rename(src, dst)  # same volume: no copy; fails instead of overwriting
                if src.exists() or _identity(dst) != ident:
                    raise OSError('verify failed')
                rel = cat + '/' + dst.name
                rollback.append({'type': 'explorer_move', 'root': key, 'src': m['src'], 'dst': rel, **ident})
                moved.append(rel)
            except OSError as exc:
                failed.append({'file': m['src'], 'reason': type(exc).__name__})
        # created folders are undone after the files have gone back
        rollback += [{'type': 'explorer_rmdir', 'root': key, 'path': c} for c in created]
        status = 'PASS' if moved and not failed else ('FAILED_OR_PARTIAL' if moved else 'FAILED')
        entry = self.ws.journal.record('explorer-sort', status, root=key, moved=len(moved), failed=failed,
                                       created=created, rollback=rollback, **approved)
        speech = 'Разложил %d файлов в папке «%s»: %s.' % (len(moved), TITLES[key], ', '.join(
            '%s %d' % kv for kv in plan['counts'].items()))
        if failed:
            speech += ' Не получилось: %d (файл занят?).' % len(failed)
        return {'status': status, 'audit_id': entry['id'], 'moved': len(moved), 'failed': failed,
                'counts': plan['counts'], 'speech': speech + ' Отменить: «отмени последнее действие».'}

    def transfer(self, op, src_key, name, dst_key, session):
        if op not in ('copy', 'move'):
            raise Denied('Unknown transfer')
        src = self.find_file(src_key, name)
        dst_dir = self.root(dst_key)
        dst = self._free_name(dst_dir, src.name)
        size = src.stat().st_size
        approved = self._confirm(session, {'task': 'explorer-' + op, 'file': src.name, 'size': _human(size),
                                           'from': TITLES[src_key], 'to': TITLES[dst_key], 'as': dst.name})
        digest = sha256(src)
        if op == 'copy' or os.stat(src).st_dev != os.stat(dst_dir).st_dev:
            with src.open('rb') as a, dst.open('xb') as b:
                shutil.copyfileobj(a, b, 1024 * 1024)
            shutil.copystat(src, dst)
            if sha256(dst) != digest:
                dst.unlink()
                raise Denied('Копия не совпала с оригиналом, отменено')
            if op == 'move':
                src.unlink()  # only after the verified copy exists on the other volume
        else:
            os.rename(src, dst)
        ok = dst.is_file() and (op == 'copy' or not src.exists())
        if op == 'copy':
            rollback = [{'type': 'explorer_remove_copy', 'root': dst_key, 'path': dst.name, 'sha256': digest}]
        else:
            rollback = [{'type': 'explorer_move_back', 'from_root': dst_key, 'path': dst.name, 'to_root': src_key,
                         'name': src.name, 'sha256': digest}]
        entry = self.ws.journal.record('explorer-' + op, 'PASS' if ok else 'VERIFY_FAILED', file=src.name, src=src_key,
                                       dst=dst_key, size=size, rollback=rollback, **approved)
        verb = 'Скопировал' if op == 'copy' else 'Перенёс'
        return {'status': entry['status'], 'audit_id': entry['id'], 'path': str(dst),
                'speech': '%s «%s» из «%s» в «%s»%s.' % (verb, src.name, TITLES[src_key], TITLES[dst_key],
                                                       '' if dst.name == src.name else ' под именем «%s»' % dst.name)}

    # ---- rollback ----
    def plan_rollback(self, entry):
        """Check every step first; nothing moves unless all of them are still safe."""
        prepared = []
        for step in entry.get('rollback') or []:
            kind = step.get('type')
            if kind == 'explorer_move':
                dst = self.path(step['root'], step['dst'], must_exist='file')
                src = self.path(step['root'], step['src'])
                if src.exists() or _identity(dst) != {'size': step['size'], 'ino': step['ino']}:
                    raise Denied('Файл изменился или место занято, верните вручную: ' + step['dst'])
                prepared.append((kind, (dst, src), step))
            elif kind == 'explorer_rmdir':
                prepared.append((kind, self.path(step['root'], step['path']), step))
            elif kind == 'explorer_remove_copy':
                p = self.path(step['root'], step['path'], must_exist='file')
                if sha256(p) != step['sha256']:
                    raise Denied('Копию уже меняли, уберите вручную: ' + step['path'])
                prepared.append((kind, p, step))
            elif kind == 'explorer_move_back':
                p = self.path(step['from_root'], step['path'], must_exist='file')
                back = self.path(step['to_root'], step['name'])
                if back.exists() or sha256(p) != step['sha256']:
                    raise Denied('Не могу безопасно вернуть: ' + step['path'])
                prepared.append((kind, (p, back), step))
            else:
                raise Denied('Unknown rollback step')
        if not prepared:
            raise Denied('Entry has no rollback')
        return prepared

    def execute_rollback(self, entry_id, prepared):
        done = []
        for kind, target, step in prepared:
            if kind == 'explorer_move':
                dst, src = target
                os.rename(dst, src)
                done.append({'restored': step['src']})
            elif kind == 'explorer_rmdir':
                try:
                    target.rmdir()  # only succeeds when empty: user files are never removed
                    done.append({'removed_empty_folder': step['path']})
                except OSError:
                    done.append({'kept_folder': step['path']})
            elif kind == 'explorer_remove_copy':
                q = self.ws.state / 'quarantine' / entry_id
                q.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(q / target.name))
                done.append({'quarantined': step['path']})
            else:
                p, back = target
                if os.stat(p).st_dev == os.stat(back.parent).st_dev:
                    os.rename(p, back)
                else:
                    shutil.copy2(p, back)
                    if sha256(back) != step['sha256']:
                        back.unlink()
                        raise Denied('Rollback copy verification failed')
                    p.unlink()
                done.append({'restored': step['name']})
        return done
