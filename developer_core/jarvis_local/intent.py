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


def _clause(c):
    """One clause -> list of steps, or a refusal/unknown dict."""
    t = c.strip().lower().rstrip('.!?')
    paths = _paths(c)
    quoted = _quoted(c)
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
    m = re.search(r'(?:открой|прочитай|загрузи|покажи) (?:сайт|страницу) (?:https://)?([a-z0-9.-]+\.[a-z]{2,})(/\S*)?', t)
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
)


def profile_plan(text):
    """Explicit 'remember me' phrases, matched on the WHOLE phrase (before splitting on «и»)."""
    t = text.strip().rstrip('.!')
    low = t.lower()
    low = re.sub(r'^(джарвис|jarvis),?\s*', '', low)
    t = t[len(t) - len(low):] if len(low) <= len(t) else t
    if re.fullmatch(r'что ты (?:обо|про|о) мне (?:знаешь|помнишь)\??|что ты помнишь обо мне\??|кто я\??', low):
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


def understand(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        return {'intent': 'unknown', 'steps': [], 'reason': 'Пустая или слишком длинная команда'}
    if not DANGEROUS.search(re.sub(QUOTED, ' ', text)):
        steps = profile_plan(text)
        if steps:
            return {'intent': 'plan', 'steps': steps, 'summary': describe(steps)}
        # The phone quick-command phrases (PC search, send, clipboard, screen, scenes...) are
        # understood everywhere: PC CLI, the "Hey JARVIS" voice loop and phone free speech.
        from .pwa_gateway import phone_quick_command
        quick = phone_quick_command(re.sub(r'^(джарвис|jarvis),?\s*', '', text.strip(), flags=re.I))
        if quick and quick['task'].startswith('pc-') and quick['task'] in TASKS:  # media phrases: rules below
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
    protected = re.sub(QUOTED, lambda m: m.group(0).replace(' и ', ' \x00 ').replace(',', '\x01'), text)
    clauses = [c.replace('\x00', 'и').replace('\x01', ',')
               for c in re.split(r',?\s+(?:и|затем|потом|после этого)\s+(?=[а-яё]{3,}\w*\b)|;\s*(?=[а-яё])', protected)]
    steps, last_dst = [], None
    for c in clauses:
        if last_dst and re.search(r'результат|её|ее|его|копию', c.lower()) and not _paths(c):
            c = c + ' ' + last_dst
        part = _clause(c)
        if part is None:
            return {'intent': 'unknown', 'steps': [], 'reason': 'Не понял: «%s»' % c.strip()[:120]}
        if isinstance(part, dict):
            return part
        for s in part:
            if s['task'] == 'office-plan':
                last_dst = s['args']['dst']
            elif s['task'] == 'file-plan':
                last_dst = s['args']['entries'][-1]['dst']
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
         'pc-screen': 'снимок экрана'}


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
