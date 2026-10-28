"""Reminders and timers: "напомни через 20 минут выключить духовку", "в 18:30 …", "завтра в 9 …",
"каждую пятницу в 10:00 …", "каждый день в 8:00 …", "поставь таймер на 5 минут".

A reminder only speaks its own text and pushes it to the phone: it never runs a command, so its
text may contain any words. Stored locally in <state>/reminders.json.
"""
from __future__ import annotations
import datetime
import json
import re
import threading
import uuid

NUM = {'одну': 1, 'одна': 1, 'один': 1, 'минуту': 1, 'две': 2, 'два': 2, 'три': 3, 'четыре': 4, 'пять': 5,
       'шесть': 6, 'семь': 7, 'восемь': 8, 'девять': 9, 'десять': 10, 'пятнадцать': 15, 'двадцать': 20,
       'тридцать': 30, 'сорок': 40, 'полчаса': 30}
WEEKDAYS = {'понедельник': 0, 'вторник': 1, 'среду': 2, 'среда': 2, 'четверг': 3, 'пятницу': 4, 'пятница': 4,
            'субботу': 5, 'суббота': 5, 'воскресенье': 6}
DAY_NAMES = ['понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу', 'воскресенье']
MAX_ITEMS = 100
TRIGGER = re.compile(r'^(?:джарвис,?\s*)?(напомни|напоминай|поставь таймер|таймер|засеки)\b', re.I)


def _num(word):
    return int(word) if word.isdigit() else NUM.get(word.lower())


def _clean(text):
    text = re.sub(r'^(?:мне\s+)?(?:,\s*)?(?:что(?:бы)?|о том,? что|про|о)\s+', '', text.strip(' ,.'), flags=re.I)
    return text.strip(' ,.')[:200]


def _at(now, hh, mm):
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return t if t > now else t + datetime.timedelta(days=1)


def parse(phrase, now=None):
    """Phrase -> {'text', 'due' (datetime), 'repeat' (None|'daily'|'weekly:N')} or None if not a reminder."""
    now = now or datetime.datetime.now()
    p = (phrase or '').strip()
    m = TRIGGER.match(p)
    if not m:
        return None
    rest = p[m.end():].strip(' ,')
    rest = re.sub(r'^мне\s+', '', rest, flags=re.I)
    if m.group(1).lower() in ('поставь таймер', 'таймер', 'засеки'):
        t = re.match(r'(?:на\s+)?(\d+|\w+)\s*(минут\w*|мин|час\w*|секунд\w*|сек)\b(.*)$', rest, re.I)
        if not t or _num(t.group(1)) is None:
            return None
        n, unit = _num(t.group(1)), t.group(2).lower()
        delta = datetime.timedelta(hours=n) if unit.startswith('час') else \
            datetime.timedelta(seconds=n) if unit.startswith('сек') else datetime.timedelta(minutes=n)
        label = '%d %s' % (n, unit)
        return {'text': _clean(t.group(3)) or 'Таймер на %s истёк' % label, 'due': now + delta, 'repeat': None}
    # "через полчаса / через час / через 20 минут / через 2 часа"
    t = re.match(r'через\s+(полчаса|час(?!\w)|(\d+|\w+)\s*(минут\w*|мин|час\w*|секунд\w*))\s*(.*)$', rest, re.I)
    if t:
        if t.group(1).lower() == 'полчаса':
            delta = datetime.timedelta(minutes=30)
        elif t.group(1).lower() == 'час':
            delta = datetime.timedelta(hours=1)
        else:
            n = _num(t.group(2))
            if n is None:
                return None
            unit = t.group(3).lower()
            delta = datetime.timedelta(hours=n) if unit.startswith('час') else \
                datetime.timedelta(seconds=n) if unit.startswith('секунд') else datetime.timedelta(minutes=n)
        return {'text': _clean(t.group(4)) or 'Напоминание', 'due': now + delta, 'repeat': None}
    # "каждый день в 8:00 …" / "каждую пятницу в 10 …"
    t = re.match(r'кажд\w+\s+(день|утро|вечер|' + '|'.join(WEEKDAYS) + r')\s+в\s+(\d{1,2})(?:[:.](\d{2}))?'
                 r'(?:\s*(?:час\w*|утра|вечера))?\s*(.*)$', rest, re.I)
    if t:
        hh, mm = int(t.group(2)), int(t.group(3) or 0)
        if 'вечера' in rest.lower() and hh < 12:
            hh += 12
        if not (0 <= hh < 24 and 0 <= mm < 60):
            return None
        word = t.group(1).lower()
        if word in WEEKDAYS:
            wd = WEEKDAYS[word]
            due = _at(now, hh, mm)
            while due.weekday() != wd:
                due += datetime.timedelta(days=1)
            return {'text': _clean(t.group(4)) or 'Напоминание', 'due': due, 'repeat': 'weekly:%d' % wd}
        return {'text': _clean(t.group(4)) or 'Напоминание', 'due': _at(now, hh, mm), 'repeat': 'daily'}
    # "завтра в 9 …" / "в 18:30 …" / "сегодня в 7 вечера …"
    t = re.match(r'(завтра|сегодня)?\s*в\s+(\d{1,2})(?:[:.](\d{2}))?(?:\s*(час\w*|утра|вечера|дня))?\s*(.*)$', rest, re.I)
    if t:
        hh, mm = int(t.group(2)), int(t.group(3) or 0)
        if (t.group(4) or '').lower() in ('вечера', 'дня') and hh < 12:
            hh += 12
        if not (0 <= hh < 24 and 0 <= mm < 60):
            return None
        if (t.group(1) or '').lower() == 'завтра':
            due = (now + datetime.timedelta(days=1)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        else:
            due = _at(now, hh, mm)
        return {'text': _clean(t.group(5)) or 'Напоминание', 'due': due, 'repeat': None}
    return None


def when_words(due, now=None):
    now = now or datetime.datetime.now()
    day = 'сегодня' if due.date() == now.date() else 'завтра' if due.date() == (now + datetime.timedelta(days=1)).date() \
        else 'в ' + DAY_NAMES[due.weekday()]
    return '%s в %s' % (day, due.strftime('%H:%M'))


class Reminders:
    def __init__(self, state_dir):
        self.path = state_dir / 'reminders.json'
        self.lock = threading.Lock()

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []

    def _save(self, items):
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(self.path)

    def add(self, parsed):
        with self.lock:
            items = self._load()
            if len(items) >= MAX_ITEMS:
                raise ValueError('Слишком много напоминаний')
            item = {'id': uuid.uuid4().hex[:8], 'text': parsed['text'], 'due': parsed['due'].isoformat(timespec='seconds'),
                    'repeat': parsed['repeat']}
            items.append(item)
            self._save(items)
            return item

    def items(self):
        return sorted(self._load(), key=lambda i: i['due'])

    def cancel(self, what=''):
        """Cancel by id, by words of the text, or everything ("все")."""
        with self.lock:
            items = self._load()
            w = (what or '').strip().lower()
            if w in ('', 'все', 'всё', 'все напоминания'):
                keep = []
            else:
                keep = [i for i in items if i['id'] != w and w not in i['text'].lower()]
            self._save(keep)
            return len(items) - len(keep)

    def due(self, now=None):
        """Pop reminders that are due; repeating ones are moved to their next time."""
        now = now or datetime.datetime.now()
        with self.lock:
            items, fired, keep = self._load(), [], []
            for i in items:
                t = datetime.datetime.fromisoformat(i['due'])
                if t > now:
                    keep.append(i)
                    continue
                fired.append(i)
                if i.get('repeat'):
                    step = datetime.timedelta(days=7 if i['repeat'].startswith('weekly') else 1)
                    while t <= now:
                        t += step
                    keep.append({**i, 'due': t.isoformat(timespec='seconds')})
            if fired:
                self._save(keep)
            return fired
