"""Feature 6: free Russian speech -> the same validated plan JSON, via a local LLM (Ollama).

Rules (intent.py) go first; the LLM is used only when they do not understand the phrase.
CaMeL-lite guards:
  * the model sees ONLY the user's own words (never document/web/screen content);
  * validate_plan(): only known tasks, 1..8 steps;
  * provenance check: every string argument (path, text, host) must literally occur in the
    user's phrase, so the model cannot invent a file or smuggle text; numbers must be small.
The plan is executed by the same router with the same approvals.
"""
from __future__ import annotations
import http.client
import json
import re
from .intent import DANGEROUS, QUOTED, UNSUPPORTED, understand, validate_plan

MODEL = 'qwen3:8b'
TASK_HELP = '''Доступные задачи (task и args):
status {} — статус JARVIS; audit {} — журнал действий; doctor {} — самопроверка; briefing {} — сводка дня;
search {"pattern": "*.xlsx" или "*слово*"} — поиск файлов;
file-plan {"entries": [{"op": "copy"|"move"|"rename", "src": путь, "dst": путь}]};
office-read {"path": путь .docx/.xlsx}; office-plan {"src","dst","op","payload"} (op: docx-append-paragraph {"text"},
xlsx-append-row {"cells": [...]}, xlsx-set-cell {"cell","value"}; dst = src с суффиксом -jarvis перед расширением);
browser-read {"url": "https://хост/"}; notepad-open-if-needed {}; notepad-set-text {"text"};
media-status {}; media-power {"on": true|false}; media-volume {"level": 0..60} или {"delta": -10..10};
media-mute {"on": true|false}; media-bass {"level": 0..12} или {"delta": -6..6} — басы/сабвуфер;
media-ps5 {} — включить PlayStation (только приставку); media-scene {"name": "game"|"movie"|"night"} — сценарии;
phone-queue {} — запросы, пришедшие с телефона; browser-selftest {} — проверить браузер;
browser-allow-host {"host"} — разрешить сайт; notepad-open {} — открыть блокнот;
windows-inspect {} — проверить тестовое окно; windows-invoke-test {} — нажать кнопку тестового окна;
rollback-last {} — отменить последнее действие.'''
SYSTEM = ('Ты планировщик ассистента JARVIS. Переведи русскую фразу пользователя в JSON-план. '
          'Отвечай ТОЛЬКО JSON: {"intent":"plan","steps":[{"task":...,"args":{...}}]} или '
          '{"intent":"unknown","reason":"..."}. Используй только задачи из списка. Не выдумывай пути, файлы, '
          'тексты и сайты: бери их дословно из фразы. Удаление, пароли, платежи, отправку сообщений — intent unknown.\n'
          + TASK_HELP)
# Few-shot examples: deliberately different from tests/data/jarvis_ru_commands.json (the eval set).
EXAMPLES = [
    ('как ты, всё работает?', {'intent': 'plan', 'steps': [{'task': 'doctor', 'args': {}}]}),
    ('что я делал сегодня?', {'intent': 'plan', 'steps': [{'task': 'audit', 'args': {}}]}),
    ('чуть громче сделай пожалуйста', {'intent': 'plan', 'steps': [{'task': 'media-volume', 'args': {'delta': 3}}]}),
    ('потише немного', {'intent': 'plan', 'steps': [{'task': 'media-volume', 'args': {'delta': -3}}]}),
    ('выруби звук на минутку', {'intent': 'plan', 'steps': [{'task': 'media-mute', 'args': {'on': True}}]}),
    ('погасите телик', {'intent': 'plan', 'steps': [{'task': 'media-power', 'args': {'on': False}}]}),
    ('давай сыграем в приставку', {'intent': 'plan', 'steps': [{'task': 'media-scene', 'args': {'name': 'game'}}]}),
    ('всё, я спать', {'intent': 'plan', 'steps': [{'task': 'media-scene', 'args': {'name': 'night'}}]}),
    ('глянь, какие есть таблицы xlsx', {'intent': 'plan', 'steps': [{'task': 'search', 'args': {'pattern': '*.xlsx'}}]}),
    ('продублируй файл docs/plan.docx', {'intent': 'plan', 'steps': [{'task': 'file-plan', 'args': {'entries': [
        {'op': 'copy', 'src': 'docs/plan.docx', 'dst': 'docs/plan-копия.docx'}]}}]}),
    ('допиши в docs/x.docx строчку «Срок — пятница»', {'intent': 'plan', 'steps': [{'task': 'office-plan', 'args': {
        'src': 'docs/x.docx', 'dst': 'docs/x-jarvis.docx', 'op': 'docx-append-paragraph',
        'payload': {'text': 'Срок — пятница'}}}]}),
    ('черкни в блокнот «позвонить врачу»', {'intent': 'plan', 'steps': [
        {'task': 'notepad-open-if-needed', 'args': {}}, {'task': 'notepad-set-text', 'args': {'text': 'позвонить врачу'}}]}),
    ('сотри все мои фотографии', {'intent': 'unknown', 'reason': 'удаление запрещено'}),
]
SCHEMA = {'type': 'object', 'properties': {
    'intent': {'type': 'string', 'enum': ['plan', 'unknown']},
    'reason': {'type': 'string'},
    'steps': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'task': {'type': 'string'}, 'args': {'type': 'object'}}, 'required': ['task', 'args']}}},
    'required': ['intent']}


def _ollama(messages, model=MODEL, timeout=60):
    c = http.client.HTTPConnection('127.0.0.1', 11434, timeout=timeout)
    try:
        # same keep_alive as web_answer: a request without it resets the model's VRAM lifetime to 5 min
        body = {'model': model, 'messages': messages, 'stream': False, 'format': SCHEMA, 'think': False,
                'keep_alive': '2h', 'options': {'temperature': 0, 'num_ctx': 4096}}
        c.request('POST', '/api/chat', body=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                  headers={'Content-Type': 'application/json'})
        r = c.getresponse()
        data = json.loads(r.read(2_000_000))
        if r.status != 200:
            raise OSError('ollama HTTP %d: %s' % (r.status, str(data)[:200]))
        return data['message']['content']
    finally:
        c.close()


def _norm(s):
    s = re.sub(r'[«»"“”„]', '', s.lower().replace('ё', 'е'))
    return re.sub(r'\s+', ' ', s).strip()


def provenance_ok(plan, phrase):
    """Every string argument must come from the user's phrase (paths, texts, hosts); derived dst allowed."""
    said = _norm(phrase)
    problems = []

    def walk(value, key=''):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif isinstance(value, str):
            v = _norm(value)
            if key in ('op', 'name', 'cell'):
                return  # enums / cell refs: checked by the task validators downstream
            for suffix in ('-jarvis.', '-копия.'):
                if key == 'dst' and suffix in v and v.replace(suffix, '.') in said:
                    return
            if key == 'url':
                host = re.sub(r'^https://', '', v).split('/')[0]
                if host and host in said:
                    return
            if key == 'pattern':
                core = v.strip('*.').strip()
                if not core or core in said:
                    return
            if v and v not in said:
                problems.append('%s=%r' % (key, value[:60]))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if abs(value) > 100:
                problems.append('%s=%r' % (key, value))
    for step in plan.get('steps', []):
        walk(step.get('args', {}))
    return problems


def llm_plan(phrase, chat=_ollama):
    # Independent second barrier: dangerous phrases never reach the model, whatever calls this.
    unquoted = re.sub(QUOTED, ' ', phrase)
    if DANGEROUS.search(unquoted) or UNSUPPORTED.search(unquoted):
        return {'intent': 'refused', 'steps': [], 'source': 'llm-guard',
                'reason': 'Удаление, платежи, пароли и отправка сообщений не выполняются.'}
    messages = [{'role': 'system', 'content': SYSTEM}]
    for said, plan_example in EXAMPLES:
        messages += [{'role': 'user', 'content': said},
                     {'role': 'assistant', 'content': json.dumps(plan_example, ensure_ascii=False)}]
    raw = chat(messages + [{'role': 'user', 'content': phrase}])
    plan = json.loads(raw)
    if plan.get('intent') != 'plan':
        return {'intent': 'unknown', 'steps': [], 'reason': 'LLM: ' + str(plan.get('reason', 'не понял'))[:200],
                'source': 'llm'}
    plan = {'intent': 'plan', 'steps': [{'task': s.get('task'), 'args': s.get('args') or {}} for s in plan.get('steps', [])]}
    try:
        validate_plan(plan)
    except ValueError as exc:
        return {'intent': 'refused', 'steps': [], 'source': 'llm', 'reason': 'Недопустимый план: ' + str(exc)}
    if any(s['task'].startswith('skill-') for s in plan['steps']):  # only the owner's own words teach skills
        return {'intent': 'refused', 'steps': [], 'source': 'llm', 'reason': 'Навыки учатся только по правилам'}
    bad = provenance_ok(plan, phrase)
    if bad:
        return {'intent': 'refused', 'steps': [], 'source': 'llm',
                'reason': 'План ссылается на то, чего не было во фразе: ' + ', '.join(bad)}
    from .intent import describe
    plan['summary'] = describe(plan['steps'])
    plan['source'] = 'llm'
    return plan


CORRECTION = re.compile(
    r'^(?:(?:джарвис|jarvis)[,\s]+)?(?:(?:нет|не то|неправильно|не так)[,.!\s]+(?:я\s+)?'
    r'(?:имел|имела|хотел|хотела|просил|просила)(?:\s+в\s+виду|\s+сказать)?|я\s+(?:имел|имела)\s+в\s+виду)'
    r'[,:\s]+(.{2,300}?)[.!]?$', re.I | re.S)


def understand_smart(phrase, chat=_ollama, skills=None):
    """Learned skills first (the owner's own names), then rules (fast, exact); LLM only for phrases
    the rules do not understand. Refusals stay refusals.
    "нет, я имел в виду X" plans X and marks it so that, once X has run, the previous phrase is
    learned as X (Skills.done)."""
    m = CORRECTION.match((phrase or '').strip())
    if m:
        before = skills.recent() if skills is not None else None
        plan = understand_smart(m.group(1), chat, skills)
        if plan['intent'] == 'plan' and before and not any(s['task'].startswith('skill-') for s in plan['steps']):
            plan['learn'] = before
        return plan
    if skills is not None:
        try:
            skills.heard(phrase)
        except OSError:
            pass
    hit = skills.match(phrase) if skills is not None else None
    if hit:
        return hit
    plan = understand(phrase)
    if plan['intent'] in ('plan', 'refused'):
        plan.setdefault('source', 'rules')
        return plan
    try:
        return llm_plan(phrase, chat)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return {'intent': 'unknown', 'steps': [], 'reason': plan.get('reason', '') + ' (LLM недоступна: %s)' % type(exc).__name__}
