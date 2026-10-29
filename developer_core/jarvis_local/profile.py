"""JARVIS remembers the user and adapts (local only, viewable, erasable).

Explicit memory: name/how to address, hobbies, likes, dislikes, facts, reply style.
Never stored (even on request): health, religion, politics, intimate life, passwords, card /
document numbers. Behaviour learning (self-development): hours and weekdays of tasks, phrase
length, politeness, frustration signals. Adaptation: address, verbosity, warmth, apologies,
and SUGGESTIONS of habitual actions — never automatic execution.
File: <state>/profile.json. "забудь всё обо мне" deletes it.
"""
from __future__ import annotations
import datetime
import json
import re
import secrets
from .session import Denied

SENSITIVE = re.compile(
    r'\b(здоровь\w*|болезн\w*|болею|болен|больна|диагноз\w*|лекарств\w*|таблетк\w*|депресси\w*|психиатр\w*|'
    r'беремен\w*|инвалид\w*|вич|рак|онколог\w*|религи\w*|верую|верующ\w*|православ\w*|мусульман\w*|церк\w+|'
    r'мечет\w*|политик\w*|политическ\w*|партия|выборы|голосую|голосовал\w*|ориентаци\w*|гей|лесби\w*|секс\w*|'
    r'интим\w*|парол\w*|пин-?код\w*|cvv|паспорт\w*|снилс|инн)\b|номер карт|\d{4}[ -]?\d{4}[ -]?\d{4}', re.I)
KINDS = ('hobby', 'like', 'dislike', 'fact')
KIND_RU = {'hobby': 'увлечения', 'like': 'нравится', 'dislike': 'не нравится', 'fact': 'факты'}
POLITE = re.compile(r'\b(пожалуйста|спасибо|благодарю|будь добр)\b', re.I)
FRUSTRATED = re.compile(r'\b(не то|опять|снова не|да что ж|бесит|не работает|ну сколько можно|блин)\b', re.I)
MAX_FACTS = 200


def _now():
    return datetime.datetime.now().astimezone()


class Profile:
    def __init__(self, ws, now=_now):
        self.ws, self.now = ws, now
        self.path = ws.state / 'profile.json'

    # ---- storage ----
    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                data.setdefault('facts', [])
                data.setdefault('stats', {})
                return data
        except (OSError, ValueError):
            pass
        return {'facts': [], 'stats': {}, 'name': None, 'style': None}

    def save(self, data):
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(self.path)

    # ---- explicit memory ----
    def remember(self, kind, text):
        text = (text or '').strip().strip('«»"').strip()
        if kind == 'name':
            if not re.fullmatch(r'[А-ЯЁA-Z][а-яёa-z\-]{1,30}( [А-ЯЁA-Z][а-яёa-z\-]{1,30})?', text):
                raise Denied('Скажите имя, например: зови меня Роман')
            data = self.load()
            data['name'] = text
            self.save(data)
            self.ws.journal.record('profile-remember', 'PASS', kind='name', rollback=[])
            return {'status': 'PASS', 'speech': 'Хорошо, %s, запомнил.' % text}
        if kind not in KINDS or not 2 <= len(text) <= 200:
            raise Denied('Не понял, что запомнить')
        if SENSITIVE.search(text):
            self.ws.journal.record('profile-remember', 'REFUSED', kind=kind, reason='sensitive', rollback=[])
            return {'status': 'REFUSED', 'speech': 'Такое я не храню: это личные данные особой категории.'}
        data = self.load()
        if any(f['text'].lower() == text.lower() and f['kind'] == kind for f in data['facts']):
            return {'status': 'PASS', 'speech': 'Я это уже помню.'}
        if kind in ('like', 'dislike'):  # a new opinion replaces the opposite one
            other = 'dislike' if kind == 'like' else 'like'
            data['facts'] = [f for f in data['facts'] if not (f['kind'] == other and f['text'].lower() == text.lower())]
        data['facts'] = (data['facts'] + [{'id': secrets.token_hex(4), 'kind': kind, 'text': text,
                                           'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}])[-MAX_FACTS:]
        self.save(data)
        self.ws.journal.record('profile-remember', 'PASS', kind=kind, chars=len(text), rollback=[])
        return {'status': 'PASS', 'speech': {'hobby': 'Запомнил ваше увлечение: %s.', 'like': 'Запомнил: вам нравится %s.',
                                             'dislike': 'Запомнил: вам не нравится %s.',
                                             'fact': 'Запомнил: %s.'}[kind] % text}

    def set_style(self, style):
        if style not in ('short', 'detailed', None):
            raise Denied('Стиль: короче или подробнее')
        data = self.load()
        data['style'] = style
        self.save(data)
        return {'status': 'PASS', 'speech': {'short': 'Понял, буду отвечать короче.',
                                             'detailed': 'Хорошо, буду отвечать подробнее.'}.get(style, 'Готово.')}

    def forget(self, what=None):
        data = self.load()
        if not what or re.fullmatch(r'вс[её]( обо мне)?|всё', what.strip().lower()):
            self.path.unlink(missing_ok=True)
            self.ws.journal.record('profile-forget', 'PASS', scope='all', rollback=[])
            return {'status': 'PASS', 'speech': 'Я стёр всё, что знал о вас.'}
        key = what.strip().lower()
        before = len(data['facts'])
        data['facts'] = [f for f in data['facts'] if key not in f['text'].lower()]
        if data.get('name') and key in data['name'].lower():
            data['name'] = None
        self.save(data)
        n = before - len(data['facts'])
        self.ws.journal.record('profile-forget', 'PASS', removed=n, rollback=[])
        return {'status': 'PASS', 'speech': 'Забыл (%d).' % n if n else 'Про это я ничего не помнил.'}

    def show(self):
        data = self.load()
        parts = []
        if data.get('name'):
            parts.append('Вас зовут %s.' % data['name'])
        for kind in KINDS:
            items = [f['text'] for f in data['facts'] if f['kind'] == kind]
            if items:
                parts.append('%s: %s.' % (KIND_RU[kind].capitalize(), ', '.join(items[-8:])))
        habit = self.habits_summary(data)
        if habit:
            parts.append(habit)
        style = {'short': 'Вы просили отвечать коротко.', 'detailed': 'Вы просили отвечать подробно.'}.get(data.get('style'))
        if style:
            parts.append(style)
        text = ' '.join(parts) or 'Пока я почти ничего о вас не знаю. Расскажите о себе: «запомни, что я люблю…».'
        return {'status': 'PASS', 'speech': text, 'facts': data['facts'], 'name': data.get('name'),
                'style': data.get('style')}

    # ---- behaviour learning ----
    def observe(self, phrase, tasks):
        """Called after each understood command. Stores counters only (no raw phrases)."""
        data = self.load()
        s = data['stats']
        now = self.now()
        s['total'] = s.get('total', 0) + 1
        words = len((phrase or '').split())
        s['avg_words'] = round((s.get('avg_words', words) * 0.9) + words * 0.1, 2)
        s['polite'] = round(s.get('polite', 0.0) * 0.9 + (1.0 if POLITE.search(phrase or '') else 0.0) * 0.1, 3)
        # a repeat = the same phrase again (only its hash is kept), not two different questions of one kind
        import hashlib
        said = re.sub(r'\W+', ' ', (phrase or '').lower()).strip()
        key = '|'.join(tasks) + '|' + hashlib.sha256(said.encode('utf-8')).hexdigest()[:12]
        last = s.get('last', {})
        repeat = last.get('key') == key and (now.timestamp() - last.get('ts', 0)) < 45
        frustrated = bool(FRUSTRATED.search(phrase or '')) or repeat
        s['frustration'] = round(s.get('frustration', 0.0) * 0.7 + (1.0 if frustrated else 0.0) * 0.3, 3)
        s['last'] = {'key': key, 'ts': now.timestamp()}
        habits = s.setdefault('habits', {})
        for t in tasks:
            slot = '%s|%d|%02d' % (t, now.weekday(), now.hour)
            habits[slot] = habits.get(slot, 0) + 1
        if len(habits) > 2000:  # keep the most frequent
            s['habits'] = dict(sorted(habits.items(), key=lambda kv: -kv[1])[:1500])
        self.save(data)
        return {'frustrated': frustrated}

    def habits_summary(self, data=None):
        data = data or self.load()
        habits = data['stats'].get('habits', {})
        if not habits:
            return ''
        top = max(habits.items(), key=lambda kv: kv[1])
        task, day, hour = top[0].split('|')
        if top[1] < 3:
            return ''
        names = {'media-scene:game': 'режим игры', 'media-scene:movie': 'режим кино', 'media-scene:night': 'режим сна',
                 'media-volume': 'громкость', 'briefing': 'брифинг', 'doctor': 'самопроверку'}
        days = ('понедельникам', 'вторникам', 'средам', 'четвергам', 'пятницам', 'субботам', 'воскресеньям')
        return 'Чаще всего вы используете %s по %s около %s часов.' % (names.get(task, task), days[int(day)], int(hour))

    def suggestion(self, label):
        """True if the user did `label` (e.g. 'media-scene:game') at this weekday+hour >= 3 times.
        Suggest only, never run; at most once per day per label."""
        data = self.load()
        now = self.now()
        n = data['stats'].get('habits', {}).get('%s|%d|%02d' % (label, now.weekday(), now.hour), 0)
        today = now.strftime('%Y-%m-%d')
        if n < 3 or data['stats'].get('suggested', {}).get(label) == today:
            return False
        data['stats'].setdefault('suggested', {})[label] = today
        self.save(data)
        return True

    # ---- adaptation ----
    def persona(self, data=None):
        data = data or self.load()
        s = data['stats']
        short = data.get('style') == 'short' or (data.get('style') is None and s.get('avg_words', 5) < 2.5)
        return {'address': data.get('name') or 'сэр', 'short': short, 'warm': s.get('polite', 0) > 0.3,
                'frustrated': s.get('frustration', 0) > 0.4}

    def adapt(self, speech, data=None):
        """Rewrites a reply to the user's style. Keeps facts (numbers, names) untouched."""
        if not speech:
            return speech
        p = self.persona(data)
        out = speech.replace('сэр', p['address']) if p['address'] != 'сэр' else speech
        if p['short']:
            out = re.sub(r'\s*(Приятной игры|Приятного просмотра|Чем ещё могу помочь\?)\.?', '', out).strip()
        if p['frustrated']:
            out = 'Понял, исправляюсь. ' + out
        elif p['warm'] and not p['short'] and not out.endswith('?'):
            out += ' Рад помочь.'
        return out
