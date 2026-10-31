"""Russian command understanding: phrase -> multi-step plan of router tasks.

The plan dict is also the contract for a future LLM planner (Claude / Ollama):
a model may PROPOSE the same JSON, and validate_plan() + the router decide what runs.
Nothing here executes anything.

Plan: {"intent": str, "steps": [{"task": str, "args": {...}}], "summary": str}
      or {"intent": "refused" | "unknown", "steps": [], "reason": str}
"""
from __future__ import annotations
import re
from .router import TASKS

QUOTED = r'[«"“„\']([^»"”\']{1,2000})[»"”\']'
PATH = r'((?:[\w\-.]+[/\\])*[\w\-]+\.(?:txt|md|csv|json|docx|xlsx|pdf|log))\b'
DANGEROUS = re.compile(r'\b(удали\w*|удал\w+|стер\w*|сотри|очисти диск|формат\w*|format|rm\s+-rf|del\s|shutdown|'
                       r'выключи компьютер|перезагрузи компьютер|парол\w*|оплат\w*|купи\w*|переведи деньг\w*|'
                       r'перевод\w* денег)\b', re.I)
UNSUPPORTED = re.compile(r'\b(отправь|напиши письмо|позвони|опубликуй)\b', re.I)
# Tasks a plan may contain. Plan/apply pairs are expanded by the executor.
PLANNABLE = set(TASKS) - {'file-apply', 'office-apply', 'rollback', 'phone-queue-close'} | {'rollback-last'}


def _quoted(text):
    m = re.search(QUOTED, text)
    return m.group(1).strip() if m else None


def _paths(text):
    return [p.strip().replace('\\', '/') for p in re.findall(PATH, text, re.I)]


def _copy_name(path, suffix='-jarvis'):
    stem, dot, ext = path.rpartition('.')
    return stem + suffix + dot + ext


def _same_dir(src, name):
    if '/' in name:
        return name
    head = src.rpartition('/')[0]
    return (head + '/' if head else '') + name


def _step(task, **args):
    return {'task': task, 'args': args}


def scene_of(t):
    """Shared with the phone quick-command parser (pwa_gateway keeps its own copy, no imports)."""
    if re.search(r'режим\w* игр\w*|\bпоиграем\b|\bигровой режим\b|\bвремя игр\w*', t):
        return 'game'
    if re.search(r'режим\w* кино|\bкинорежим\b|\bсмотрим фильм\b|\bвремя кино\b', t):
        return 'movie'
    if re.search(r'спокойной ночи|режим\w* сна|\bотбой\b', t):
        return 'night'
    return None


def _folders(t):
    """Allow-listed real folders named in the phrase, in the order spoken."""
    from .explorer import SPOKEN
    hits = []
    for key, pat in SPOKEN:
        for m in re.finditer(pat, t):
            hits.append((m.start(), key))
    return [k for _, k in sorted(hits)]


def explorer_clause(t, c, quoted):
    """Explorer phrases about the owner's real folders; None when the clause is about something else."""
    folders = _folders(t)
    if not folders:
        return None
    m = re.match(r'^(?:скопируй|перемести|перенеси)\s+(?:файл\s+)?(.+?)\s+из\s+(.+?)\s+(?:в|на)\s+(.+)$', t)
    if m and len(_folders(m.group(2))) == 1 and len(_folders(m.group(3))) == 1:
        name = quoted or c.strip().rstrip('.!?')[m.start(1):m.end(1)].strip(' «»"')
        task = 'explorer-copy' if t.startswith('скоп') else 'explorer-move'
        return [_step(task, src=_folders(m.group(2))[0], name=name, dst=_folders(m.group(3))[0])]
    if re.search(r'\b(?:разложи|рассортируй|отсортируй|разбери|наведи порядок|прибери\w*|упорядочи)', t):
        return [_step('explorer-sort', folder=folders[0])]
    if re.search(r'дубл\w*|дубликат\w*|повтор\w* файл|одинаков\w+ файл', t):
        return [_step('explorer-duplicates', folder=folders[0])]
    m = re.search(r'(?:создай|сделай|заведи)\s+(?:новую\s+)?папку\s+(?:[«"](.+?)[»"]|([\w\-]+))', c, re.I)
    if m:
        return [_step('explorer-mkdir', folder=folders[-1], name=(m.group(1) or m.group(2)).strip())]
    if re.search(r'^(?:что|чего)\s+(?:нового\s+|сейчас\s+)?(?:лежит|есть|нового)\b|^покажи\b|^сколько\s+файлов'
                 r'|^что\s+получилось', t):
        return [_step('explorer-list', folder=folders[0])]
    return None


TABLE_FOLDERS = (('Desktop', r'на\s+рабоч\w+\s+стол\w*'), ('Downloads', r'в\s+загрузк\w*'), ('Documents', r'в\s+документ\w*'))
COLUMN_FUNCS = (('sum', r'сумм\w*|итог\w*|сколько всего'), ('avg', r'средн\w*'), ('max', r'максимум\w*|максимальн\w*|наибольш\w*'),
                ('min', r'минимум\w*|минимальн\w*|наименьш\w*'), ('count', r'количеств\w*|сколько чисел'))


def _table_name(c):
    """'...в таблице «Бюджет 2026»' / '...таблицу расходы' -> the spoken table name (no .xlsx: those are workspace files)."""
    m = re.search(r'таблиц\w*\s+(?:excel\s+|эксель\s+)?(?:[«"“](.+?)[»"”]|([\wё\- ]+?))(?=\s+(?:с\s+(?:колонк|столбц)|на\s+лист|'
                  r'в\s+(?:документ|загрузк)|на\s+рабоч|по\s+столбц|строк)|[,.!?]|$)', c, re.I)
    if not m:
        return None
    name = (m.group(1) or m.group(2) or '').strip()
    return name if name and not re.search(r'\.\w{2,5}$', name) and name.lower() not in ('её', 'ее', 'его', 'эту') else None


def excel_clause(t, c, quoted):
    """Excel phrases about the owner's real tables; None when the clause is about something else."""
    if not re.search(r'таблиц', t):
        return None
    name = _table_name(c)
    m = re.search(r'(?:создай|сделай|заведи)\s+(?:новую\s+)?таблиц\w*', t)
    if m:
        cols = re.search(r'(?:колонк\w*|столбц\w*)\s+(.+?)(?=\s+(?:в\s+(?:документ|загрузк)|на\s+рабоч)|$)', c, re.I)
        if not name or not cols:
            return {'intent': 'unknown', 'steps': [], 'reason': 'Скажите так: создай таблицу «Расходы» с колонками Дата, Сумма, Категория'}
        heads = [h.strip(' «»"') for h in re.split(r',|;|\s+и\s+', cols.group(1)) if h.strip(' «»"')]
        folder = next((k for k, pat in TABLE_FOLDERS if re.search(pat, t)), 'Documents')
        return [_step('excel-create', name=name, headers=heads, folder=folder)]
    if not name:
        return None
    if re.search(r'добав\w*\s+(?:в\s+.+?\s+)?строк', t):
        src = quoted or (re.search(r'строк\w*\s+(.+?)\s+(?:в|к)\s+таблиц', c, re.I) or [None, None])[1]
        if not src:
            return {'intent': 'unknown', 'steps': [], 'reason': 'Какие значения добавить? Разделяйте «;»: добавь строку «29.10; 500; еда»'}
        return [_step('excel-append', name=name, cells=[x.strip() for x in re.split(r'[;|]', src) if x.strip()])]
    col = re.search(r'(?:столбц\w*|колонк\w*|графе?)\s+(?:[«"](.+?)[»"]|([\wё\-]+))', c, re.I)
    if col:  # the function word is looked for outside the column name ("среднее по столбцу Сумма")
        rest = re.sub(r'(?:столбц\w*|колонк\w*|графе?)\s+(?:[«"].+?[»"]|[\wё\-]+)', ' ', t)
        func = next((f for f, pat in COLUMN_FUNCS if re.search(pat, rest)), None)
        if func:
            return [_step('excel-column', name=name, column=(col.group(1) or col.group(2)).strip(), func=func)]
    if re.match(r'найди|поищи', t):
        # "найди «такси» в таблице расходы" / "найди такси в таблице расходы" / "...в таблице расходы строки с такси"
        m = re.match(r'(?:найди|поищи)\s+(?:[«"](.+?)[»"]|(.+?))\s+в\s+таблиц', c, re.I) or \
            re.search(r'строк\w*\s+(?:с|где\s+есть|где)\s+(?:[«"](.+?)[»"]|(.+))$', c, re.I)
        if m and (m.group(1) or m.group(2) or '').strip():
            return [_step('excel-find', name=name, text=(m.group(1) or m.group(2)).strip(' «»"'))]
        return {'intent': 'unknown', 'steps': [], 'reason': 'Скажите так: найди «такси» в таблице Расходы'}
    if re.match(r'(?:открой|запусти)\b', t):
        return [_step('excel-open', name=name)]
    if re.match(r'(?:что|покажи|расскажи|сводк|опиши|сколько строк)', t):
        return [_step('excel-summary', name=name)]
    return None


DOC_NOUN = r'(?:документ(?:а|е|у|ом)?|pdf|пдф|файл(?:а|е|у)?|скан(?:а|е)?)'
DOC_STOP = (r'(?=\s+(?:в\s+(?:pdf|пдф|документ\w*|загрузк\w*)|на\s+рабоч|с\s+текстом|абзац|текст\b|слово|строк)'
            r'|\s+[«"“]|[,.!?]|$)')


def _doc_name(c):
    """'документ «Договор аренды»' / 'pdf отчёт за май' -> the spoken name (no extension: those are workspace files).
    'pdf из документа Письмо' -> 'Письмо', 'pdf скан паспорта' -> 'скан паспорта': the longest plausible name wins."""
    # a lookahead, so "pdf из документа Письмо" also yields the overlapping "документа Письмо"
    names = []
    for m in re.finditer(r'(?=(?<![\wё])' + DOC_NOUN + r'\s+(?:word\s+|ворд\s+)?(?:[«"“](.+?)[»"”]|'
                         r'([\wё\- ]+?))' + DOC_STOP + ')', c, re.I):
        name = (m.group(1) or m.group(2) or '').strip()
        if name and not re.search(r'\.\w{2,5}$', name) and not re.match(r'(?:из|в|во|на|с|для|и|или)\s', name, re.I) \
                and not re.fullmatch(DOC_NOUN, name, re.I) and name.lower() not in ('его', 'её', 'ее', 'этот'):
            names.append(name)
    return max(names, key=len) if names else None


def docs_merge_plan(text):
    """'объедини pdf «Акт» и «Счёт»' -> one step (must run before the clause split on ' и ')."""
    t = re.sub(r'^(джарвис|jarvis),?\s*', '', text.strip(), flags=re.I).strip(' .!?')
    m = re.match(r'(?:объедини|склей|собери)\s+(?:в\s+один\s+)?(?:pdf|пдф|файлы\s+pdf)\w*\s+(.+)$', t, re.I)
    if not m:
        return []
    quoted = re.findall(r'[«"“]([^»"”]+)[»"”]', m.group(1))
    names = quoted or [x.strip() for x in re.split(r',|\s+и\s+', m.group(1)) if x.strip()]
    return [_step('pdf-merge', names=names)] if 2 <= len(names) <= 10 else []


def docs_clause(t, c, quoted):
    """Word/PDF phrases about the owner's real documents; None when the clause is about something else."""
    if not re.search(r'(?<![\wё])' + DOC_NOUN + r'(?![\wё])', t):
        return None
    m = re.search(r'(?:создай|сделай|напиши|заведи)\s+(?:новый\s+)?документ\w*', t)
    if m:
        name = _doc_name(c)
        if not name:
            return {'intent': 'unknown', 'steps': [], 'reason': 'Скажите так: создай документ «Письмо» с текстом «…»'}
        body = re.search(r'с\s+текстом\s+[«"“](.+?)[»"”]', c, re.I)
        folder = next((k for k, pat in TABLE_FOLDERS if re.search(pat, t)), 'Documents')
        return [_step('doc-create', name=name, text=body.group(1) if body else '', folder=folder)]
    name = _doc_name(c)
    if not name:
        return None
    if re.search(r'(?:добавь|допиши|дополни)', t):
        if not quoted:
            return {'intent': 'unknown', 'steps': [], 'reason': 'Какой текст дописать? Например: допиши в документ Письмо «С уважением»'}
        return [_step('doc-append', name=name, text=quoted)]
    if re.search(r'распознай|сделай\s+текстов|оцифруй', t):
        return [_step('pdf-ocr', name=name)]
    if re.search(r'(?<![\wё])(?:pdf|пдф)(?![\wё])', t) and re.match(r'(?:сделай|сохрани|конверт|преврати|экспорт)', t):
        return [_step('doc-pdf', name=name)]
    if re.match(r'(?:найди|поищи)', t):
        m = re.match(r'(?:найди|поищи)\s+(?:слово\s+)?(?:[«"](.+?)[»"]|(.+?))\s+в\s+' + DOC_NOUN + r'\b', c, re.I) or \
            re.search(r'слово\s+(?:[«"](.+?)[»"]|(\S+))', c, re.I)
        if m and (m.group(1) or m.group(2) or '').strip():
            return [_step('doc-find', name=name, text=(m.group(1) or m.group(2)).strip(' «»"'))]
        return None  # "найди файлы pdf и docx": a file search, not a search inside one document
    if re.search(r'перескажи|кратко|о\s+ч[её]м', t):
        return [_step('doc-summary', name=name)]
    if re.match(r'(?:открой|запусти)\b', t):
        return [_step('doc-open', name=name)]
    if re.match(r'(?:прочитай|что\s+в|покажи|сколько\s+страниц|расскажи)', t):
        return [_step('doc-read', name=name)]
    return None


def _clause(c):
    """One clause -> list of steps, or a refusal/unknown dict."""
    t = c.strip().lower().rstrip('.!?')
    paths = _paths(c)
    quoted = _quoted(c)
    part = (excel_clause(t, c, quoted) or docs_clause(t, c, quoted)) if not paths else None
    if part:
        return part
    part = explorer_clause(t, c, quoted)
    if part:
        return part
    if re.fullmatch(r'(какой )?(статус|сводка|как дела|как ты|ты тут)', t):
        return [_step('status')]
    if re.search(r'\bжурнал|\bистори\w+ действий|что ты (сделал|делал)', t):
        return [_step('audit')]
    if re.search(r'очеред\w*', t) and re.search(r'телефон|айфон|iphone|запрос', t):
        return [_step('phone-queue')]
    if re.search(r'\bотмени\w*|\bоткати\w*|верни как было', t):
        return [_step('rollback-last')]
    if re.search(r'провер\w* браузер', t):
        return [_step('browser-selftest')]
    m = re.search(r'разреши\w* (?:сайт )?([a-z0-9.-]+\.[a-z]{2,})', t)
    if m:
        return [_step('browser-allow-host', host=m.group(1))]
    m = re.search(r'(?:прочитай|загрузи|покажи) (?:сайт|страницу) (?:https://)?([a-z0-9.-]+\.[a-z]{2,})(/\S*)?', t)
    if m:
        return [_step('browser-read', url='https://' + m.group(1) + (m.group(2) or '/'))]
    if re.search(r'блокнот', t):
        if re.search(r'напиши|запиши|введи|набери', t):
            if not quoted:
                return {'intent': 'unknown', 'steps': [], 'reason': 'Укажите текст в кавычках: напиши в блокноте «…»'}
            return [_step('notepad-open-if-needed'), _step('notepad-set-text', text=quoted)]
        if re.search(r'открой|запусти', t):
            return [_step('notepad-open')]
        if re.search(r'провер|что в', t):
            return [_step('notepad-inspect')]
    if re.search(r'тестов\w+ окн', t):
        if re.search(r'нажми|кликни', t):
            return [_step('windows-invoke-test')]
        if re.search(r'открой', t):
            return [_step('windows-test-window')]
        return [_step('windows-inspect')]
    # ---- scenes (feature 1) ----
    scene = scene_of(t)
    if scene:
        return [_step('media-scene', name=scene)]
    # ---- TV / soundbar / PS5 ----
    if re.search(r'плейстейшн|playstation|приставк|\bps5\b|\bпс5\b|\bпс 5\b', t) and re.search(r'включи|запусти|давай', t):
        return [_step('media-ps5')]
    num = re.search(r'(\d{1,3})', t)
    if re.search(r'\bбас\w*|сабвуфер\w*|низк\w+ частот', t):
        if re.search(r'больше|прибав|громче|сильнее|подними|\+', t):
            return [_step('media-bass', delta=int(num.group(1)) if num else 2)]
        if re.search(r'меньше|убав|тише|слабее|опусти|-', t):
            return [_step('media-bass', delta=-(int(num.group(1)) if num else 2))]
        if num:
            return [_step('media-bass', level=int(num.group(1)))]
        return [_step('media-status')]
    if re.search(r'громкост\w*', t) and num:
        return [_step('media-volume', level=int(num.group(1)))]
    if re.search(r'\bгромче\b', t):
        return [_step('media-volume', delta=int(num.group(1)) if num else 5)]
    if re.search(r'\bтише\b', t):
        return [_step('media-volume', delta=-(int(num.group(1)) if num else 5))]
    if re.search(r'без звука|выключи звук|отключи звук|убери звук|заглуши', t):
        return [_step('media-mute', on=True)]
    if re.search(r'включи звук|верни звук', t):
        return [_step('media-mute', on=False)]
    if re.search(r'телевизор|\bтв\b|телек', t) or re.fullmatch(r'выключи вс[её]', t):
        if re.search(r'выключи', t):
            return [_step('media-power', on=False)]
        if re.search(r'включи', t):
            return [_step('media-power', on=True)]
        if re.search(r'что|статус|состояни', t):
            return [_step('media-status')]
    # ---- office ----
    m = re.search(r'(?:ячейк\w* )([a-z]{1,3}[1-9]\d{0,6})', t)
    if m and paths and re.search(r'запиши|впиши|поставь|измени', t):
        value = quoted or (re.search(r'значени\w* (\S+)', c) or [None, None])[1]
        if not value:
            return {'intent': 'unknown', 'steps': [], 'reason': 'Какое значение записать в ячейку?'}
        src = paths[0]
        return [_step('office-plan', src=src, dst=_copy_name(src), op='xlsx-set-cell',
                      payload={'cell': m.group(1).upper(), 'value': value})]
    if re.search(r'добав\w* строк', t) and paths:
        cells_src = quoted or (re.search(r'строк\w* (.+?) (?:в|к) (?:таблиц|файл)', c) or [None, None])[1]
        if not cells_src:
            return {'intent': 'unknown', 'steps': [], 'reason': 'Какие значения добавить строкой? Разделяйте «;».'}
        src = paths[0]
        return [_step('office-plan', src=src, dst=_copy_name(src), op='xlsx-append-row',
                      payload={'cells': [x.strip() for x in re.split(r'[;|]', cells_src) if x.strip()]})]
    if re.search(r'добав\w* (?:в .+? )?(?:абзац|текст|параграф)', t) and paths and paths[0].lower().endswith('.docx'):
        if not quoted:
            return {'intent': 'unknown', 'steps': [], 'reason': 'Укажите текст абзаца в кавычках.'}
        src = paths[0]
        return [_step('office-plan', src=src, dst=_copy_name(src), op='docx-append-paragraph',
                      payload={'text': quoted})]
    if re.search(r'прочитай|открой|покажи', t) and paths and paths[0].lower().endswith(('.docx', '.xlsx')):
        return [_step('office-read', path=paths[0])]
    # ---- files ----
    if re.search(r'сделай копию|продублируй', t) and paths:
        return [_step('file-plan', entries=[{'op': 'copy', 'src': paths[0], 'dst': _copy_name(paths[0], '-копия')}])]
    for verb, op in (('скопируй', 'copy'), ('перемести', 'move'), ('перенеси', 'move'), ('переименуй', 'rename')):
        if t.startswith(verb) or re.search(r'\b' + verb, t):
            if len(paths) < 2:
                return {'intent': 'unknown', 'steps': [], 'reason': 'Нужны исходный и новый путь: %s A в B' % verb}
            dst = _same_dir(paths[0], paths[1]) if op == 'rename' else paths[1]
            return [_step('file-plan', entries=[{'op': op, 'src': paths[0], 'dst': dst}])]
    if re.search(r'\bнайди|\bпоищи|\bгде\b', t):
        exts = re.findall(r'\b(txt|md|csv|json|docx|xlsx|pdf|log)\b', t)
        if exts:
            return [_step('search', pattern='*.' + e) for e in dict.fromkeys(exts)]
        m = re.search(r'(?:найди|поищи)\s+(?:файл\w*\s+)?(\w{3,})', t)
        if m:
            return [_step('search', pattern='*' + m.group(1)[:-1 if len(m.group(1)) > 5 else None] + '*')]
    return None


PROFILE_RULES = (
    (r'^(?:запомни,? что )?(?:меня зовут|зови меня|называй меня) ([А-ЯЁA-Zа-яёa-z\-]{2,30})$', 'name'),
    (r'^(?:запомни,? что )?(?:я не люблю|мне не нравится|я терпеть не могу) (.{2,200})$', 'dislike'),
    (r'^(?:запомни,? что )?(?:я люблю|мне нравится|я обожаю) (.{2,200})$', 'like'),
    (r'^(?:запомни,? что )?(?:я увлекаюсь|я занимаюсь|мо[её] хобби(?: —| -|:)?|мои хобби(?: —| -|:)?) (.{2,200})$', 'hobby'),
    (r'^запомни,? что (.{2,200})$', 'fact'),
    # said in passing, without "запомни": facts about the owner are learned too (shown by "что ты обо мне знаешь")
    (r'^(?:кстати,? )?((?:у меня (?:есть )?|мо(?:й|я|ю|его|ей) )(?:собак|пёс|пес|пса|кошк|кот|жен|муж|сын|доч|'
     r'брат|сестр|девушк|парн|друг|подруг|мам|пап|отц|бабушк|дедушк)\w*(?: зовут| по имени)? [а-яёa-z\-]{2,30}'
     r'(?: [а-яёa-z\-]{2,30})?)$', 'fact'),
    (r'^(?:кстати,? )?(я (?:живу|работаю|учусь|родился|родилась) .{2,80})$', 'fact'),
    (r'^(?:кстати,? )?(мо(?:й|ё|е) день рожд\w+ .{2,40})$', 'fact'),
    (r'^(?:кстати,? )?(мне \d{1,2} (?:год|года|лет))$', 'fact'),
    (r'^(?:кстати,? )?(мо(?:й|я|ё|е|и) любим\w+ .{2,80})$', 'fact'),
)


def profile_plan(text):
    """Explicit 'remember me' phrases, matched on the WHOLE phrase (before splitting on «и»)."""
    t = text.strip().rstrip('.!')
    low = t.lower()
    low = re.sub(r'^(джарвис|jarvis),?\s*', '', low)
    t = t[len(t) - len(low):] if len(low) <= len(t) else t
    if re.fullmatch(r'что ты (?:обо|про|о) мне (?:знаешь|помнишь)\??|что ты помнишь обо мне\??|кто я\??|как меня зовут\??', low):
        return [_step('profile-show')]
    if re.fullmatch(r'забудь вс[её](?: обо мне| про меня)?', low):
        return [_step('profile-forget', what='всё')]
    m = re.fullmatch(r'забудь(?:,? что| про| о)? (.{2,100})', low)
    if m:
        return [_step('profile-forget', what=m.group(1))]
    if re.fullmatch(r'(?:отвечай|говори) (?:покороче|короче|кратко|коротко)', low):
        return [_step('profile-style', style='short')]
    if re.fullmatch(r'(?:отвечай|говори) (?:подробнее|подробно|развёрнуто|развернуто)', low):
        return [_step('profile-style', style='detailed')]
    for pattern, kind in PROFILE_RULES:
        m = re.match(pattern, low)
        if m:
            start = m.start(1)
            value = t[start:start + len(m.group(1))]  # keep the user's original casing
            return [_step('profile-remember', kind=kind, text=value.strip(' «»"'))]
    return None


def reminder_plan(text):
    from .reminders import TRIGGER
    t = re.sub(r'^(джарвис|jarvis),?\s*', '', text.strip(), flags=re.I)
    low = t.lower()
    if TRIGGER.match(t):
        return [_step('reminder-add', phrase=t)]
    if re.search(r'\b(какие|список|покажи|мои)\s+(у меня\s+)?(напоминани|таймер)', low):
        return [_step('reminder-list')]
    m = re.match(r'(?:отмени|убери|сними|выключи)\s+(все\s+)?(?:напоминани\w*|таймер\w*)\s*(?:про|о|об)?\s*(.*)$', low)
    if m:
        return [_step('reminder-cancel', what='все' if m.group(1) and not m.group(2) else m.group(2).strip())]
    return []


MUSIC_RULES = (
    (r'^(?:поставь\s+)?(?:на\s+)?пауз[ауы]\b|останови\s+(?:музыку|видео|трек|песню)|^стоп\s+(?:музыка|видео)', 'pause'),
    (r'^(?:продолжи|продолжай|играй\s+дальше|сними\s+с\s+паузы|сними\s+паузу|сними\s+с\s+пауз\w+)'
     r'|^включи\s+(?:музыку\s+)?дальше', 'play'),
    (r'\bследующ\w+\s*(?:трек|песн\w*|композици\w*|видео)?|^пропусти\b|^переключи\s+(?:трек|песню)', 'next'),
    (r'\bпредыдущ\w+\s*(?:трек|песн\w*|композици\w*|видео)?|^верни\s+(?:трек|песню)|^прошл\w+\s+(?:трек|песн\w*)',
     'previous'),
)


def music_plan(text):
    low = re.sub(r'^(джарвис|jarvis),?\s*', '', text.strip().lower()).strip(' .!?')
    if re.search(r'^что\s+(?:сейчас\s+)?(?:играет|звучит)|^что\s+за\s+(?:песня|трек|музыка)', low):
        return [_step('music-status')]
    for pattern, action in MUSIC_RULES:
        if re.search(pattern, low) and not re.search(r'телевизор|саундбар|громкост', low):
            return [_step('music', action=action)]
    return []


STATION = r'(?:(?:на|через|в|у)\s+)?(?:яндекс\s+)?(?:колонк[аеуиой]\w*|станци(?:я|ю|и|ей)|алис[аеуы])\b'


def station_plan(text):
    """Yandex Station: 'скажи на колонке ...', 'попроси Алису ...', 'громкость колонки 5', 'колонку на паузу'."""
    t = re.sub(r'^(джарвис|jarvis),?\s*', '', text.strip(), flags=re.I).strip(' .!?')
    low = t.lower()
    if not re.search(STATION, low):
        return []
    m = re.match(r'^(?:попроси|скажи|передай)\s+алис[еуы]\b[\s,:]*(.+)$', t, re.I) or re.match(r'^алиса[\s,]+(.+)$', t, re.I)
    if m:
        return [_step('station-command', text=m.group(1).strip(' «»"'))]
    m = re.match(r'^(?:скажи|объяви|произнеси)\s+%s[\s,:]+(.+)$' % STATION, t, re.I) or \
        re.match(r'^%s[\s,]+(?:скажи|объяви)[\s,:]+(.+)$' % STATION, t, re.I)
    if m:
        return [_step('station-say', text=m.group(1).strip(' «»"'))]
    m = re.search(r'громкост\w*\s+(?:на\s+)?%s\s+(?:на\s+)?(\d{1,2})\b|%s\s+(?:на\s+)?громкост\w*\s+(?:на\s+)?(\d{1,2})\b'
                  % (STATION, STATION), low)
    if m:
        level = int(m.group(1) or m.group(2))
        return [_step('station-volume', level=level)] if level <= 10 else []
    if re.search(r'^что\s+(?:сейчас\s+)?(?:играет|звучит)|^что\s+(?:с|на)\s+колонк', low):
        return [_step('station-status')]
    rest = re.sub(r'\s+', ' ', re.sub(STATION, ' ', low)).strip()
    if re.fullmatch(r'(?:останови|выключи|стоп|заглуши)', rest):
        return [_step('station-control', action='pause')]
    for pattern, action in MUSIC_RULES:
        if re.search(pattern, rest):
            return [_step('station-control', action=action)]
    said = re.sub(r'\s+', ' ', re.sub(STATION, ' ', t, flags=re.I)).strip(' ,')
    if re.match(r'(?:включи|поставь|запусти)\s+\S', said, re.I):  # "включи на колонке джаз" -> Alice: "включи джаз"
        return [_step('station-command', text=said)]
    return []


HOME_WORDS = r'свет|ламп|люстр|торшер|подсветк|светильник|розетк|\bбра\b'


ROUTINE_PHRASES = (('home', r'я\s+(?:дома|пришёл|пришел|вернулся)'), ('away', r'я\s+(?:ушёл|ушел|ухожу)'),
                   ('morning', r'доброе\s+утро'), ('evening', r'(?:вечер\s+добрый|добрый\s+вечер)'))


def home_plan(text):
    t = re.sub(r'^(джарвис|jarvis),?\s*', '', text.strip(), flags=re.I).strip(' .!?')
    low = t.lower()
    if re.search(r'^(?:какие|покажи)\s+(?:у меня\s+)?(?:устройства|лампы|розетки)|^что\s+(?:сейчас\s+)?включено(?:\s+дома)?$', low):
        return [_step('home-devices')]
    for name, pat in ROUTINE_PHRASES:
        if re.fullmatch(r'(?:%s)(?:\s*,?\s*(?:джарвис|jarvis))?' % pat, low):
            return [_step('home-routine', name=name)]
    m = re.match(r'^(?:запусти|включи)\s+сценари\w*\s+[«"]?(.+?)[»"]?$', low)
    if m:
        return [_step('home-scenario', name=m.group(1).strip())]
    if not re.search(HOME_WORDS, low):
        return []
    m = re.search(r'(?:яркост\w*|свет\w*|ламп\w*)\s+(?:на\s+)?(\d{1,3})\s*(?:%|процент\w*)?', low)
    if m and 1 <= int(m.group(1)) <= 100:
        return [_step('home-brightness', phrase=t, level=int(m.group(1)))]
    if re.search(r'^(?:приглуши|убавь)\b', low):
        return [_step('home-brightness', phrase=t, level=30)]
    if re.search(r'^(?:включи|зажги|вруби)\b', low):
        return [_step('home-switch', phrase=t, on=True)]
    if re.search(r'^(?:выключи|погаси|отключи|выруби)\b', low):
        return [_step('home-switch', phrase=t, on=False)]
    return []


def web_plan(text):
    t = re.sub(r'^(джарвис|jarvis),?\s*', '', text.strip(), flags=re.I).strip(' .!?')
    m = re.match(r'^(?:найди|поищи|загугли|узнай|посмотри)\s+(?:мне\s+)?(?:в\s+интернете|в\s+сети|в\s+гугле|онлайн)'
                 r'\s*,?\s*(.{2,300})$', t, re.I) or re.match(r'^загугли\s+(.{2,300})$', t, re.I)
    if m:
        q = m.group(1).strip()
        tail = re.match(r'^(.{2,300}?),?\s+(?:и|затем|потом|а потом)\s+(.+)$', q, re.I)
        if tail:
            rest = tail.group(2).strip().lower()
            n = _result_number(rest)
            if n:  # "...и открой первую ссылку"
                return [_step('web-search', query=tail.group(1).strip()), _step('web-open-result', index=n)]
            if re.fullmatch(r'(?:покажи|дай|назови|перечисли)\s+(?:мне\s+)?(?:ссылки|результаты|сайты)', rest):
                return [_step('web-search', query=tail.group(1).strip())]
            if re.fullmatch(r'сохрани\s*(?:это|его|ответ|результат)?\s*(?:в\s+(?:документы|файл))?', rest):
                return [_step('web-answer', question=tail.group(1).strip()), _step('web-save')]
        return [_step('web-answer', question=q)]
    m = re.match(r'^(?:что нового|какие новости|последние новости)\s+(?:про|о|об|в|по)\s+(.{2,200})$', t, re.I)
    if m:
        return [_step('web-answer', question='новости ' + m.group(1).strip())]
    return []


RESULT_ORDINALS = {'перв': 1, 'втор': 2, 'трет': 3, 'четв': 4, 'пят': 5, 'шест': 6}


def _result_number(t):
    """'открой первую ссылку' / 'открой ссылку 2' / 'открой второй результат' -> 2; else 0."""
    m = re.fullmatch(r'открой\s+(?:(\d)(?:-?(?:ю|й|ую))?\s+|(перв|втор|трет|четв|пят|шест)\w*\s+)?'
                     r'(?:ссылк\w*|результат\w*|сайт)(?:\s+(?:номер\s+)?(\d))?', t.strip(' .!?'))
    if not m:
        return 0
    if m.group(1) or m.group(3):
        return int(m.group(1) or m.group(3))
    return RESULT_ORDINALS.get(m.group(2), 0)


DOC_EXT = r'(?:txt|md|csv|json|docx?|xlsx?|xlsm|pptx?|pdf|log|rtf|odt|zip|rar|7z|jpg|jpeg|png|mp3|mp4)'


def browser_plan(text):
    """Browser operator phrases (own browser): links from the last search, any site, search on a site,
    download a file, save the last web answer."""
    from .web_ops import SPOKEN_SITES
    raw = re.sub(r'^(джарвис|jarvis),?\s*', '', text.strip(), flags=re.I).strip(' .!?')
    low = raw.lower()
    n = _result_number(low)
    if n:
        return [_step('web-open-result', index=n)]
    if re.fullmatch(r'сохрани\s+(?:этот\s+|последний\s+)?(?:ответ|это|найденное|результат)(?:\s+в\s+(?:документы|файл))?'
                    r'|сохрани\s+в\s+(?:документы|файл)', low):
        return [_step('web-save')]
    m = re.fullmatch(r'(?:скачай|загрузи)\s+(?:мне\s+)?(?:файл\s+)?(?:по\s+ссылке\s+)?(https://\S+?)(?:\s+в\s+загрузки)?', raw, re.I)
    if m:
        return [_step('web-download', url=m.group(1))]
    m = re.fullmatch(r'(?:открой|зайди на|перейди на)\s+(?:сайт\s+)?((?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,24}(?:/\S*)?)', raw, re.I)
    if m and not re.search(r'\.' + DOC_EXT + r'$', m.group(1), re.I):
        url = m.group(1) if re.match(r'^https?://', m.group(1), re.I) else 'https://' + m.group(1)
        return [_step('web-open', url=url)]
    sites = '|'.join('(?P<%s>%s)' % kv for kv in SPOKEN_SITES)
    verbs = r'(?:найди|поищи|ищи|покажи|включи|открой|посмотри)\s+(?:мне\s+)?'
    m = re.fullmatch(verbs + r'(?:на|в)\s+(?:%s)\s+(?P<q>.{1,200})' % sites, low) or \
        re.fullmatch(verbs + r'(?P<q>.{1,200}?)\s+(?:на|в)\s+(?:%s)' % sites, low)
    if m:
        site = next(k for k, _ in SPOKEN_SITES if m.group(k))
        q = raw[m.start('q'):m.end('q')].strip(' «»"')
        return [_step('web-site-search', site=site, query=q)]
    return []


ORDINALS = {'первый': 1, 'второй': 2, 'третий': 3, 'четвёртый': 4, 'четвертый': 4, 'пятый': 5}


def apps_plan(text):
    from .apps import APPS, FOLDERS, SITES
    low = re.sub(r'^(джарвис|jarvis),?\s*', '', text.strip().lower()).strip(' .!?')
    if re.match(r'^(?:сверни все окна|покажи рабочий стол)$', low):
        return [_step('desktop-show')]
    if re.match(r'^(?:какие|что за)\s+(?:окна|программы)\s+(?:сейчас\s+)?(?:открыты|запущены)', low):
        return [_step('windows-list')]
    m = re.match(r'^открой\s+(?:файл\s+(?:номер\s+)?(\d{1,2})|(\d{1,2})(?:-?й)?\s+файл|(\w+)\s+файл)$', low)
    if m:
        n = int(m.group(1) or m.group(2) or 0) or ORDINALS.get(m.group(3) or '', 0)
        return [_step('file-open', index=n)] if n else []
    m = re.match(r'^(?:открой|запусти|включи)\s+(?:мне\s+)?(?:(папку|сайт|программу|приложение)\s+)?(.+)$', low)
    if m and m.group(2).strip() in set(FOLDERS) | set(SITES) | set(APPS):
        return [_step('app-open', target=m.group(2).strip())]
    return []


QUESTION = re.compile(r'^(?:как|что такое|что значит|что это|что за|кто|почему|зачем|сколько|когда|где(?!\s+(?:мо[йяиеё]\w*|лежит|находится файл))|какой|какая|какое|'
                      r'какие|каков\w*|чем|откуда|куда|можно ли|правда ли|есть ли|расскажи (?:про|о|об)|объясни)\s+\S+\s+\S|'
                      r'^\S+(?:\s+\S+){2,}\?$', re.I)

# Questions about the owner or JARVIS itself never leave the PC ("как меня зовут", "что ты умеешь").
PERSONAL = re.compile(r'(?<!\w)(?:меня|мне|мной|мой|моя|моё|мое|мои|моих|ты|тебя|тебе|тобой|твой|твоя|твои|вы|вас|вам)(?!\w)', re.I)


def _bare(text):
    return re.sub(r'^(джарвис|jarvis),?\s*', '', text.strip(), flags=re.I).strip()[:300]


GREET = r'(?:привет|здравствуй(?:те)?|добрый\s+день|хай|салют|здорово)'  # «добрый вечер» is a home routine
HOW = r'(?:как\s+(?:у\s+тебя\s+)?дела|как\s+ты|как\s+поживаешь|как\s+жизнь)'


def chat_plan(text):
    """Small talk gets a polite fixed reply: "привет, как дела?" must not become a web search."""
    low = re.sub(r'[!?.,…]+', ' ', text.lower())
    low = re.sub(r'\b(?:джарвис|jarvis|жарвис|жарис)\b', ' ', low)
    low = re.sub(r'\s+', ' ', low).strip()
    if re.fullmatch(r'%s\s*%s' % (GREET, HOW), low):  # a bare «как дела?» stays the JARVIS status
        return [_step('chat-reply', kind='how')]
    if re.fullmatch(GREET, low):
        return [_step('chat-reply', kind='greet')]
    if re.fullmatch(r'(?:спасибо|благодарю|спасибо большое|большое спасибо)', low):
        return [_step('chat-reply', kind='thanks')]
    if re.fullmatch(r'(?:пока|до свидания|до связи|спокойной ночи джарвис)', low):
        return [_step('chat-reply', kind='bye')]
    return []


SKILL_NAME = r'[«"“]?([^«»"“”,]{2,40}?)[»"”]?'


TV_WORD = r'(?:телевизор\w*|тв|телик\w*|ящик)'


def tv_plan(text):
    # A video link to the TV, and the one-time YouTube pairing code. Whole phrase, before any split.
    t = re.sub(r'^(?:джарвис|jarvis),?\s*', '', text.strip().rstrip('.!'), flags=re.I)
    m = re.fullmatch(r'(?:(?:покажи|включи|запусти|отправь|выведи|кинь|поставь|открой)(?: это| видео| ролик)? )?'
                     r'на %s[:,]?\s+(https?://\S{4,500})' % TV_WORD, t, re.I) or \
        re.fullmatch(r'(https?://\S{4,500})\s+на %s' % TV_WORD, t, re.I)
    if m:
        return [_step('tv-play', url=m.group(1))]
    m = re.fullmatch(r'(?:привяжи|подключи|свяжи) (?:%s|ютуб\w*|youtube)(?: к (?:ютубу|youtube|%s))?,?'
                     r'(?: по)?(?: код\w*)?[:,\s]+([\d][\d\s-]{10,22}\d)' % (TV_WORD, TV_WORD), t, re.I)
    if m and len(re.sub(r'\D', '', m.group(1))) == 12:
        return [_step('tv-link', code=re.sub(r'\D', '', m.group(1)))]
    return None


def skill_plan(text):
    """Teaching JARVIS a skill: matched on the whole phrase, before anything else splits it."""
    t = re.sub(r'^(?:джарвис|jarvis),?\s*', '', text.strip().rstrip('.!'), flags=re.I)
    m = re.fullmatch(r'когда я (?:говорю|скажу) (?:[«"“]([^«»"“”]{2,40})[»"”],?|([^«»"“”,]{2,40}),)\s*(?:то\s+|ты\s+)?(.{3,300})',
                     t, re.I | re.S)
    if m:
        return [_step('skill-teach', name=(m.group(1) or m.group(2)).strip(), phrase=m.group(3).strip())]
    m = re.fullmatch(r'запомни (?:это|последнее|последнюю команду|эту команду) как ' + SKILL_NAME, t, re.I)
    if m:
        return [_step('skill-save', name=m.group(1).strip())]
    m = re.fullmatch(r'забудь навык ' + SKILL_NAME, t, re.I)
    if m:
        return [_step('skill-forget', name=m.group(1).strip())]
    m = re.fullmatch(r'забудь поправку [«"“]?(.{2,300}?)[»"”]?', t, re.I)
    if m:
        return [_step('skill-unlearn', phrase=m.group(1).strip())]
    if re.fullmatch(r'(?:какие (?:у тебя )?навыки(?: ты знаешь| есть)?|покажи (?:мои )?навыки|мои навыки|'
                    r'чему ты научился)\??', t, re.I):
        return [_step('skill-list')]
    if re.fullmatch(r'(?:где (?:мой )?телефон|телефон дома|(?:сколько|какой) заряд\w* (?:у |на )?(?:моего )?телефон\w*|'
                    r'заряд телефона)\??', t, re.I):
        return [_step('phone-status')]
    return None


def understand(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        return {'intent': 'unknown', 'steps': [], 'reason': 'Пустая или слишком длинная команда'}
    steps = tv_plan(text) or skill_plan(text) or chat_plan(text)
    if steps:
        return {'intent': 'plan', 'steps': steps, 'summary': describe(steps)}
    # "привет, включи свет": the greeting is politeness, the rest is the command
    rest = re.sub(r'^\s*(?:%s)[\s,!.]+(?:(?:джарвис|jarvis)[\s,!.]+)?' % GREET, '', text, flags=re.I)
    if rest != text and rest.strip():
        text = rest
    steps = reminder_plan(text) or station_plan(text) or music_plan(text) or home_plan(text) or web_plan(text) or browser_plan(text) or \
        docs_merge_plan(text) or apps_plan(text)
    if steps:
        return {'intent': 'plan', 'steps': steps, 'summary': describe(steps)}
    if not DANGEROUS.search(re.sub(QUOTED, ' ', text)):
        steps = profile_plan(text)
        if steps:
            return {'intent': 'plan', 'steps': steps, 'summary': describe(steps)}
        # The phone quick-command phrases (PC search, send, clipboard, screen, scenes...) are
        # understood everywhere: PC CLI, the "Hey JARVIS" voice loop and phone free speech.
        from .pwa_gateway import phone_quick_command
        quick = phone_quick_command(re.sub(r'^(джарвис|jarvis),?\s*', '', text.strip(), flags=re.I))
        if quick and (quick['task'].startswith('pc-') or quick['task'] == 'screen-describe') \
                and quick['task'] in TASKS:  # media phrases: rules below
            steps = [_step(quick['task'], **quick['args'])]
            return {'intent': 'plan', 'steps': steps, 'summary': describe(steps)}
    # Quoted text is user content (e.g. a note "купить молоко"), not an instruction.
    unquoted = re.sub(QUOTED, ' ', text)
    if DANGEROUS.search(unquoted):
        return {'intent': 'refused', 'steps': [],
                'reason': 'Удаление, форматирование, выключение, пароли и платежи JARVIS не выполняет.'}
    if UNSUPPORTED.search(unquoted):
        return {'intent': 'refused', 'steps': [], 'reason': 'Отправка сообщений и публикация пока не подключены.'}
    # Split "A и B" / "A, затем B" into clauses, but never inside quotes.
    protected = re.sub(QUOTED, lambda m: m.group(0).replace(' и ', ' \x00 ').replace(',', '\x01').replace(';', '\x02'), text)
    clauses = [c.replace('\x00', 'и').replace('\x01', ',').replace('\x02', ';')
               for c in re.split(r',?\s+(?:и|затем|потом|после этого)\s+(?=[а-яё]{3,}\w*\b)|;\s*(?=[а-яё])', protected)]
    steps, last_dst, last_folder, last_table, last_doc = [], None, None, None, None
    for c in clauses:
        if last_table and not re.search(r'таблиц', c.lower()) and \
                re.search(r'строк|столбц|колонк|^\s*(?:открой|покажи)\s+(?:её|ее|его)\b', c.lower()):
            c = re.sub(r'\s+(?:её|ее|его)\b', '', c) + ' в таблице «%s»' % last_table  # "...и открой её"
        elif last_doc and not _doc_name(c) and \
                re.search(r'(?:pdf|пдф)|допиши|перескажи|^\s*(?:открой|покажи)\s+(?:его|её|ее)\b', c.lower()):
            # "...и сделай из него pdf" / "...и открой его" -> the document named before
            c = re.sub(r'\s+(?:из\s+)?(?:него|неё|нее|его|её|ее)\b', '', c) + ' документ «%s»' % last_doc
        if last_dst and re.search(r'результат|её|ее|его|копию', c.lower()) and not _paths(c):
            c = c + ' ' + last_dst
        if last_folder and not _folders(c.lower()) and re.search(r'покажи|что получилось|результат', c.lower()):
            from .explorer import TITLES
            c = c + ' в ' + TITLES[last_folder].lower()  # "...и покажи, что получилось" -> same folder
        part = _clause(c)
        if part is None and QUESTION.match(_bare(text)) and not PERSONAL.search(_bare(text)):
            # A plain question nothing else understood: answer it from the internet (text only).
            steps = [_step('web-answer', question=_bare(text))]
            return {'intent': 'plan', 'steps': steps, 'summary': describe(steps)}
        if part is None:
            return {'intent': 'unknown', 'steps': [], 'reason': 'Не понял: «%s»' % c.strip()[:120]}
        if isinstance(part, dict):
            return part
        for s in part:
            if s['task'] == 'office-plan':
                last_dst = s['args']['dst']
            elif s['task'] == 'file-plan':
                last_dst = s['args']['entries'][-1]['dst']
            elif s['task'].startswith('explorer-'):
                last_folder = s['args'].get('folder') or s['args'].get('dst')
            elif s['task'].startswith('excel-'):
                last_table = s['args']['name']
            elif s['task'].startswith('doc-') or s['task'] == 'pdf-ocr':
                last_doc = s['args']['name']
        steps.extend(part)
    plan = {'intent': 'plan', 'steps': steps, 'summary': describe(steps)}
    validate_plan(plan)
    return plan


NAMES = {'status': 'показать статус', 'audit': 'показать журнал', 'phone-queue': 'показать очередь телефона',
         'rollback-last': 'отменить последнее обратимое действие', 'search': 'найти файлы',
         'file-plan': 'файловая операция (план → подтверждение → выполнение)', 'office-read': 'прочитать документ',
         'office-plan': 'правка документа в новой копии (план → подтверждение)', 'browser-read': 'прочитать сайт',
         'browser-allow-host': 'разрешить сайт', 'browser-selftest': 'проверить браузер',
         'notepad-open': 'открыть вкладку JARVIS в Блокноте', 'notepad-open-if-needed': 'открыть вкладку JARVIS, если её нет',
         'notepad-set-text': 'записать текст в Блокнот', 'notepad-inspect': 'проверить вкладку Блокнота',
         'windows-inspect': 'проверить тестовое окно', 'windows-invoke-test': 'нажать кнопку тестового окна',
         'windows-test-window': 'открыть тестовое окно', 'media-status': 'состояние телевизора и звука',
         'media-power': 'включить/выключить телевизор', 'media-volume': 'изменить громкость саундбара',
         'media-mute': 'выключить/включить звук', 'media-bass': 'изменить уровень баса',
         'media-ps5': 'включить PlayStation 5 (ТВ + вход HDMI 1)',
         'media-scene': 'сценарий (игра / кино / сон)', 'profile-remember': 'запомнить о вас',
         'profile-show': 'рассказать, что я о вас знаю', 'profile-forget': 'забыть', 'profile-style': 'стиль ответов',
         'pc-find': 'найти файл на компьютере', 'pc-send': 'отправить файл на iPhone',
         'pc-clip-get': 'показать буфер обмена ПК', 'pc-clip-set': 'положить текст в буфер ПК',
         'pc-screen': 'снимок экрана', 'reminder-add': 'поставить напоминание',
         'reminder-list': 'показать напоминания', 'reminder-cancel': 'отменить напоминание',
         'music': 'управлять музыкой на компьютере', 'music-status': 'что играет на компьютере',
         'home-devices': 'устройства умного дома', 'home-switch': 'включить/выключить свет или розетку',
         'home-brightness': 'яркость света', 'home-scenario': 'сценарий умного дома',
         'home-routine': 'домашний режим (я дома / ушёл / утро / вечер)',
         'station-status': 'что на колонке', 'station-say': 'сказать на колонке', 'station-command': 'попросить Алису',
         'station-volume': 'громкость колонки', 'station-control': 'пауза / дальше / трек на колонке',
         'phone-status': 'где телефон и сколько заряда', 'skill-teach': 'выучить навык',
         'skill-save': 'запомнить последнюю команду как навык', 'skill-list': 'показать навыки',
         'skill-forget': 'забыть навык', 'skill-unlearn': 'забыть поправку',
         'tv-play': 'показать видео на телевизоре', 'tv-link': 'привязать YouTube на телевизоре',
         'screen-describe': 'рассказать, что на экране', 'web-answer': 'найти ответ в интернете',
         'app-open': 'открыть программу, папку или сайт', 'file-open': 'открыть найденный файл',
         'desktop-show': 'свернуть все окна', 'windows-list': 'какие программы открыты',
         'explorer-list': 'показать, что в папке', 'explorer-duplicates': 'найти одинаковые файлы (без удаления)',
         'explorer-mkdir': 'создать папку', 'explorer-sort': 'разложить файлы по папкам-типам (с отменой)',
         'chat-reply': 'ответить на приветствие',
         'explorer-copy': 'скопировать файл между папками', 'explorer-move': 'перенести файл между папками (с отменой)',
         'excel-summary': 'рассказать, что в таблице', 'excel-column': 'посчитать по столбцу таблицы',
         'excel-find': 'найти строки в таблице', 'excel-create': 'создать таблицу Excel (с отменой)',
         'excel-append': 'добавить строку в таблицу (резервная копия, с отменой)', 'excel-open': 'открыть таблицу в Excel',
         'doc-read': 'рассказать, что в документе', 'doc-find': 'найти слово в документе',
         'doc-summary': 'пересказать документ (текст уходит в Claude/Qwen)', 'doc-create': 'создать документ Word (с отменой)',
         'doc-append': 'дописать абзац (резервная копия, с отменой)', 'doc-pdf': 'сохранить документ в PDF (с отменой)',
         'pdf-merge': 'объединить PDF в новый файл (с отменой)', 'pdf-ocr': 'распознать скан в текстовый PDF (с отменой)',
         'doc-open': 'открыть документ',
         'web-search': 'найти ссылки в интернете', 'web-open': 'открыть сайт в браузере',
         'web-open-result': 'открыть ссылку из найденного', 'web-site-search': 'искать на сайте в браузере',
         'web-download': 'скачать файл в Загрузки (с отменой)', 'web-save': 'сохранить ответ в Документы (с отменой)'}


def describe(steps):
    return '; '.join('%d) %s' % (i + 1, NAMES.get(s['task'], s['task'])) for i, s in enumerate(steps))


def validate_plan(plan):
    """Same check applies to plans proposed by an LLM: only known tasks, bounded size."""
    if not isinstance(plan, dict) or plan.get('intent') != 'plan':
        raise ValueError('not a plan')
    steps = plan.get('steps')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 8:
        raise ValueError('plan must have 1..8 steps')
    for s in steps:
        if not isinstance(s, dict) or set(s) != {'task', 'args'} or not isinstance(s['args'], dict):
            raise ValueError('invalid step')
        if s['task'] not in PLANNABLE | {'notepad-open-if-needed'}:
            raise ValueError('task not allowed in plans: ' + str(s['task']))
    return plan
