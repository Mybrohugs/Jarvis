"""Learned skills: JARVIS keeps plans that worked and reuses them (the Hermes Agent idea, kept small).

  * named: "когда я говорю «кино», приглуши свет и включи режим кино" or "запомни это как «отчёт»";
    saying the name (also "сделай отчёт", "навык отчёт") runs the stored plan;
  * cached: a plan the local LLM built and that ran without failures is reused for the same phrase,
    so the model is not asked again (seconds saved on every repeat);
  * learned: a correction. "нет, я имел в виду …" right after a misunderstood phrase runs the meant
    command, and once it worked the misunderstood phrase means that from now on;
  * offers: two commands said one after the other again and again ("свет", then "музыку") make JARVIS
    offer to join them into one skill; it is saved only when the owner names it.
A stored plan is data only: on use it passes validate_plan() again and every step goes through the
router with its usual approvals (the phone allowlist on the phone). Skills never hold skill-* steps.
State: <state>/skills.json.
"""
from __future__ import annotations
import datetime
import json
import re
from .intent import describe, validate_plan
from .session import Denied

MAX_NAMED, MAX_CACHED, MAX_LEARNED, MAX_RUNS = 50, 200, 200, 60
LEAD = r'^(?:(?:джарвис|jarvis) )?(?:(?:пожалуйста|давай|сделай|выполни|запусти|включи) )?(?:навык )?'
FRESH = 600          # a correction or an offer refers to what happened in the last 10 minutes
PAIR_GAP = 180       # "then" = the second command within 3 minutes of the first
PAIR_TIMES = 3       # a pair seen this many times is worth offering


def norm(text):
    t = re.sub(r'[«»"“”„!?.,;:]', ' ', (text or '').lower().replace('ё', 'е'))
    return re.sub(r'\s+', ' ', t).strip()


def key(text):
    """Named-skill key: 'Джарвис, сделай отчёт' and 'отчёт' are the same skill."""
    return re.sub(r' пожалуйста$', '', re.sub(LEAD, '', norm(text))).strip()


def _clean_steps(steps):
    plan = validate_plan({'intent': 'plan', 'steps': steps})
    if any(s['task'].startswith('skill-') for s in plan['steps']):
        raise ValueError('a skill cannot contain skill commands')
    return [{'task': s['task'], 'args': s['args']} for s in plan['steps']]


def _ts(item):
    try:
        return datetime.datetime.fromisoformat(item['at']).timestamp()
    except (KeyError, TypeError, ValueError):
        return 0.0


class Skills:
    def __init__(self, state, clock=None):
        self.path = state / 'skills.json'
        self.clock = clock or (lambda: datetime.datetime.now())

    def _now(self):
        return self.clock().isoformat(timespec='seconds')

    def _age(self, item):
        return self.clock().timestamp() - _ts(item) if item else float('inf')

    def _load(self):
        try:
            d = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            d = {}
        return {'named': d.get('named') or {}, 'cached': d.get('cached') or {}, 'learned': d.get('learned') or {},
                'last': d.get('last'), 'heard': d.get('heard'), 'runs': d.get('runs') or [],
                'offer': d.get('offer'), 'offered': d.get('offered') or [], 'seq': d.get('seq', 0)}

    def _save(self, d):
        tmp = self.path.with_suffix('.tmp')  # executor and voice loop are separate processes
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
        tmp.replace(self.path)

    def match(self, phrase):
        """Phrase -> stored plan (source 'skill', 'learned' or 'cache') or None."""
        d = self._load()
        hit, source = d['named'].get(key(phrase)), 'skill'
        if hit is None:
            hit, source = d['learned'].get(norm(phrase)), 'learned'
        if hit is None:
            hit, source = d['cached'].get(norm(phrase)), 'cache'
        if hit is None:
            return None
        try:
            steps = _clean_steps(hit['steps'])
        except (ValueError, KeyError, TypeError):
            return None
        plan = {'intent': 'plan', 'steps': steps, 'summary': describe(steps), 'source': source}
        if source == 'skill':
            plan['skill'] = hit['name']
        return plan

    # ---- corrections ----
    def heard(self, phrase):
        """Every phrase JARVIS tried to understand: the one a later "нет, я имел в виду" corrects."""
        d = self._load()
        d['heard'] = {'phrase': phrase[:300], 'at': self._now()}
        self._save(d)

    def recent(self):
        """The previous phrase, if it was said in the last 10 minutes."""
        h = self._load()['heard']
        return h['phrase'] if h and self._age(h) <= FRESH else None

    def done(self, phrase, plan):
        """The plan ran without failures: it becomes 'last'; an LLM plan is cached for this phrase; a
        corrected plan is learned for the misunderstood phrase. Returns an offer to speak, or None."""
        try:
            steps = _clean_steps(plan['steps'])
        except (ValueError, KeyError, TypeError):
            return None
        d, now = self._load(), self._now()
        d['seq'] = d.get('seq', 0) + 1  # orders 'last' and 'offer' even within one second
        d['last'] = {'phrase': phrase[:300], 'steps': steps, 'at': now, 'seq': d['seq']}
        if plan.get('learn') and norm(plan['learn']) and norm(plan['learn']) != norm(phrase):
            d['learned'][norm(plan['learn'])] = {'steps': steps, 'at': now, 'meant': phrase[:300]}
            d['cached'].pop(norm(plan['learn']), None)
            for old in sorted(d['learned'], key=lambda k: d['learned'][k]['at'])[:-MAX_LEARNED]:
                del d['learned'][old]
        elif plan.get('source') == 'llm':
            d['cached'][norm(phrase)] = {'steps': steps, 'at': now}
            for old in sorted(d['cached'], key=lambda k: d['cached'][k]['at'])[:-MAX_CACHED]:
                del d['cached'][old]
        elif plan.get('source') == 'skill' and key(plan.get('skill', '')) in d['named']:
            item = d['named'][key(plan['skill'])]
            item['uses'] = item.get('uses', 0) + 1
        offer = None
        if plan.get('source') != 'skill' and not plan.get('learn'):
            offer = self._note_run(d, phrase, steps, now)
        self._save(d)
        return offer

    # ---- offers ----
    def _note_run(self, d, phrase, steps, now):
        runs = d['runs']
        prev = runs[-1] if runs else None
        runs.append({'phrase': norm(phrase), 'said': phrase[:120], 'steps': steps, 'at': now})
        d['runs'] = runs[-MAX_RUNS:]
        if not prev or prev['phrase'] == norm(phrase) or _ts(runs[-1]) - _ts(prev) > PAIR_GAP:
            return None
        pair = prev['phrase'] + ' → ' + norm(phrase)
        if pair in d['offered']:
            return None
        seen = sum(1 for a, b in zip(runs, runs[1:])
                   if (a['phrase'], b['phrase']) == (prev['phrase'], norm(phrase)) and _ts(b) - _ts(a) <= PAIR_GAP)
        joined = prev['steps'] + steps
        if seen < PAIR_TIMES or any(n.get('steps') == joined for n in d['named'].values()):
            return None
        d['offered'] = (d['offered'] + [pair])[-100:]
        d['offer'] = {'steps': joined, 'phrase': '%s, затем %s' % (prev['said'], phrase[:120]), 'at': now,
                      'seq': d['seq']}
        return ('Вы часто говорите «%s», а потом «%s». Могу делать это одной командой: '
                'скажите «запомни это как …» и название.' % (prev['said'], phrase[:120]))

    def last(self):
        """What "запомни это как …" saves: a fresh offer (two joined commands) or the last command."""
        d = self._load()
        if d['offer'] and self._age(d['offer']) <= FRESH and d['offer'].get('seq') == (d['last'] or {}).get('seq'):
            return d['offer']  # nothing was run since the offer was made
        return d['last']

    def teach(self, name, steps, phrase):
        k = key(name)
        if not 2 <= len(k) <= 40:
            raise Denied('Название навыка: от 2 до 40 букв')
        try:
            steps = _clean_steps(steps)
        except ValueError as exc:
            raise Denied('Такой навык не сохранить: ' + str(exc)) from None
        d = self._load()
        if k not in d['named'] and len(d['named']) >= MAX_NAMED:
            raise Denied('Навыков уже %d: забудьте ненужный' % MAX_NAMED)
        item = {'name': name.strip(' «»"'), 'phrase': phrase[:300], 'steps': steps, 'uses': 0,
                'created': self._now()}
        d['named'][k] = item
        d['offer'] = None
        self._save(d)
        return item

    def forget(self, name):
        d = self._load()
        item = d['named'].pop(key(name), None)
        if item:
            self._save(d)
        return item

    def unlearn(self, phrase):
        """Drop a learned correction ("забудь поправку …")."""
        d = self._load()
        item = d['learned'].pop(norm(phrase), None)
        if item:
            self._save(d)
        return item

    def items(self):
        return sorted(self._load()['named'].values(), key=lambda i: i['name'])

    def corrections(self):
        return [{'phrase': k, 'meant': v.get('meant', '')} for k, v in self._load()['learned'].items()]
