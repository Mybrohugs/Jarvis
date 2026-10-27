"""Feature 4: morning briefing (text for the voice + a push to the iPhone).

Sources: greeting/date, weather (Open-Meteo, no key; city set by the user), phone queue,
yesterday's actions, jarvis doctor summary. Google Calendar stays off: its tokens live in the
Telegram bot root, which JARVIS does not touch.
"""
from __future__ import annotations
import datetime
import json
import urllib.parse
import urllib.request

DAYS = ('понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье')
MONTHS = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября',
          'ноября', 'декабря')
WMO = {0: 'ясно', 1: 'преимущественно ясно', 2: 'переменная облачность', 3: 'пасмурно', 45: 'туман', 48: 'туман',
       51: 'морось', 53: 'морось', 55: 'сильная морось', 61: 'небольшой дождь', 63: 'дождь', 65: 'сильный дождь',
       66: 'ледяной дождь', 67: 'ледяной дождь', 71: 'небольшой снег', 73: 'снег', 75: 'сильный снег', 77: 'снежная крупа',
       80: 'ливень', 81: 'ливни', 82: 'сильные ливни', 85: 'снегопад', 86: 'сильный снегопад', 95: 'гроза',
       96: 'гроза с градом', 99: 'гроза с градом'}


def _get(url, timeout=8):
    req = urllib.request.Request(url, headers={'User-Agent': 'JARVIS-local/1'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status != 200:
            raise OSError('HTTP %d' % r.status)
        return json.loads(r.read(200_000))


def signed(n):
    n = int(n)
    return 'плюс %d' % n if n > 0 else ('минус %d' % -n if n < 0 else 'ноль')


def deg(n):
    n = abs(int(n))
    return 'градус' if n % 10 == 1 and n % 100 != 11 else (
        'градуса' if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else 'градусов')


class Briefing:
    def __init__(self, router, fetch=_get, now=None):
        self.r, self.fetch = router, fetch
        self.now = now or (lambda: datetime.datetime.now().astimezone())
        self.cfg_file = router.ws.state / 'briefing_config.json'

    def config(self):
        try:
            return json.loads(self.cfg_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {'time': '08:00'}

    def set_city(self, city):
        if not isinstance(city, str) or not 2 <= len(city) <= 60:
            raise ValueError('Укажите город')
        data = self.fetch('https://geocoding-api.open-meteo.com/v1/search?' +
                          urllib.parse.urlencode({'name': city, 'count': 1, 'language': 'ru'}))
        hits = data.get('results') or []
        if not hits:
            raise ValueError('Город не найден: ' + city)
        h = hits[0]
        cfg = {**self.config(), 'city': h.get('name'), 'lat': h['latitude'], 'lon': h['longitude'],
               'tz': h.get('timezone', 'auto')}
        self.cfg_file.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
        return {'status': 'PASS', 'city': cfg['city'], 'country': h.get('country')}

    def weather(self, cfg):
        if 'lat' not in cfg:
            return 'Погода: город не задан.'
        q = urllib.parse.urlencode({'latitude': cfg['lat'], 'longitude': cfg['lon'], 'timezone': cfg.get('tz', 'auto'),
                                    'current': 'temperature_2m,weather_code',
                                    'daily': 'temperature_2m_max,temperature_2m_min,precipitation_probability_max',
                                    'forecast_days': 1})
        d = self.fetch('https://api.open-meteo.com/v1/forecast?' + q)
        t = round(d['current']['temperature_2m'])
        desc = WMO.get(d['current'].get('weather_code'), 'без осадков')
        hi, lo = round(d['daily']['temperature_2m_max'][0]), round(d['daily']['temperature_2m_min'][0])
        rain = d['daily'].get('precipitation_probability_max', [None])[0]
        text = '%s сейчас %s %s, %s. Днём до %s, ночью до %s.' % (
            cfg.get('city', ''), signed(t), deg(t), desc, signed(hi), signed(lo))
        if rain is not None and rain >= 50:
            text += ' Вероятность осадков %d%%, возьмите зонт.' % rain
        return text

    def build(self, doctor_summary=None):
        now = self.now()
        hour = now.hour
        profile = self.r.profile.load()
        who = profile.get('name') or 'сэр'
        greet = ('Доброе утро, %s.' if 5 <= hour < 12 else ('Добрый день, %s.' if hour < 18 else 'Добрый вечер, %s.')) % who
        parts = [greet, 'Сегодня %s, %d %s.' % (DAYS[now.weekday()], now.day, MONTHS[now.month - 1])]
        try:
            parts.append(self.weather(self.config()))
        except Exception:
            parts.append('Погоду получить не удалось.')
        hobbies = [f['text'] for f in profile.get('facts', []) if f['kind'] == 'hobby']
        if hobbies and now.weekday() >= 5:  # weekends: a nod to what the user loves
            parts.append('Выходной — отличный повод для: %s.' % hobbies[now.toordinal() % len(hobbies)])
        pending = [i for i in self.r.phone_queue_items() if i['status'] == 'pending' and not i.get('auto')]
        if pending:
            parts.append('С телефона ждут %d запрос(а). Первый: %s.' % (len(pending), pending[0]['text'][:80]))
        if doctor_summary:
            parts.append(doctor_summary)
        text = ' '.join(parts)
        out = {'status': 'PASS', 'text': text, 'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        (self.r.ws.state / 'briefing_latest.json').write_text(json.dumps(out, ensure_ascii=False), encoding='utf-8')
        self.r.ws.journal.record('briefing', 'PASS', chars=len(text), rollback=[])
        return out
