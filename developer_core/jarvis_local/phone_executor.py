"""Local executor for allowlisted phone quick commands (user decision 27.10.2026).

Runs on the PC next to the gateway. Reads 'auto' requests from the phone queue and executes
ONLY media-volume / media-mute through the Unified Task Router with a phone-allowlist session
(which refuses every other approval). Bounds: volume <= 40, step <= 10, 20 commands/minute.
Everything else stays in the queue for a person at the PC.
"""
from __future__ import annotations
import argparse
import collections
import re
import time
from .pwa_gateway import PhoneQueue
from .router import UnifiedTaskRouter
from .session import Denied, TrustedLocalSession

ALLOWED = ('media-volume', 'media-mute', 'media-scene',
           'pc-find', 'pc-send', 'pc-clip-get', 'pc-clip-set', 'pc-screen',  # 7, 9, 10: user-authorised
           'profile-remember', 'profile-forget', 'profile-style', 'profile-show',  # the user's own profile
           'reminder-add', 'reminder-list', 'reminder-cancel',  # speak/push only
           'music', 'music-status',  # PC player: pause/play/next/previous
           'home-devices', 'home-switch', 'home-brightness', 'home-scenario', 'home-routine',  # Yandex lights/sockets
           'station-status', 'station-say', 'station-volume', 'station-control',  # Yandex Station; Alice commands at the PC
           'phone-status',  # the iPhone as Home Assistant sees it
           'skill-teach', 'skill-save', 'skill-list', 'skill-forget', 'skill-unlearn',  # stored plans; their steps pass validate() on use
           'screen-describe',  # local vision model, the picture stays on the PC
           'tv-play', 'tv-link',  # a YouTube link to the TV (switches it on, never off)
           'web-answer',  # own SearXNG + local summary
           'web-search', 'web-open', 'web-open-result', 'web-site-search', 'web-save',  # own browser; no downloads
           'excel-summary', 'excel-column', 'excel-find', 'excel-open',  # read tables, open one; edits at the PC
           'doc-read', 'doc-find', 'doc-open',  # documents: read and open; edits, PDFs and retelling at the PC
           'app-open', 'file-open', 'desktop-show', 'windows-list',  # allowlisted apps, folders, sites
           'chat-reply')  # greetings: a fixed reply
PROFILE_KINDS = ('name', 'hobby', 'like', 'dislike', 'fact')


def label(task, args):
    """Habit label: scenes keep their name so JARVIS can suggest the right one."""
    return task + (':' + args['name'] if task == 'media-scene' else '')
SCENES = ('game', 'movie', 'night')
PHONE_MAX_VOLUME = 40
MAX_STEP = 10
RATE_LIMIT = 20  # per 60 s


def validate(auto):
    if not isinstance(auto, dict) or auto.get('task') not in ALLOWED or not isinstance(auto.get('args'), dict):
        raise Denied('Not an allowlisted phone command')
    task, args = auto['task'], auto['args']
    if task == 'chat-reply':
        if set(args) != {'kind'} or args['kind'] not in ('greet', 'how', 'thanks', 'bye'):
            raise Denied('Bad chat reply')
        return task, {'kind': args['kind']}
    if task == 'profile-remember':
        if set(args) != {'kind', 'text'} or args['kind'] not in PROFILE_KINDS or not isinstance(args['text'], str) \
                or not 1 <= len(args['text']) <= 200:
            raise Denied('Bad profile fact')
        return task, {'kind': args['kind'], 'text': args['text']}
    if task == 'profile-forget':
        if set(args) - {'what'} or not isinstance(args.get('what', ''), str) or len(args.get('what', '')) > 100:
            raise Denied('Bad forget')
        return task, {'what': args.get('what')}
    if task == 'profile-style':
        if set(args) != {'style'} or args['style'] not in ('short', 'detailed'):
            raise Denied('Bad style')
        return task, {'style': args['style']}
    if task == 'reminder-add':
        if set(args) != {'phrase'} or not isinstance(args['phrase'], str) or not 3 <= len(args['phrase']) <= 300:
            raise Denied('Bad reminder')
        return task, {'phrase': args['phrase']}
    if task == 'reminder-cancel':
        if set(args) - {'what'} or not isinstance(args.get('what', ''), str) or len(args.get('what', '')) > 100:
            raise Denied('Bad reminder cancel')
        return task, {'what': args.get('what', '')}
    if task == 'music':
        from .media_pc import ACTIONS
        if set(args) != {'action'} or args['action'] not in ACTIONS:
            raise Denied('Bad music action')
        return task, {'action': args['action']}
    if task in ('home-switch', 'home-brightness'):
        want = {'phrase', 'on'} if task == 'home-switch' else {'phrase', 'level'}
        if set(args) != want or not isinstance(args['phrase'], str) or not 2 <= len(args['phrase']) <= 200:
            raise Denied('Bad home command')
        if task == 'home-switch' and type(args['on']) is not bool:
            raise Denied('Bad on/off')
        if task == 'home-brightness' and (type(args['level']) is not int or not 1 <= args['level'] <= 100):
            raise Denied('Bad brightness')
        return task, dict(args)
    if task == 'home-scenario':
        if set(args) != {'name'} or not isinstance(args['name'], str) or not 1 <= len(args['name']) <= 60:
            raise Denied('Bad scenario')
        return task, {'name': args['name']}
    if task == 'home-routine':
        if set(args) != {'name'} or args['name'] not in ('home', 'away', 'morning', 'evening'):
            raise Denied('Bad routine')
        return task, {'name': args['name']}
    if task == 'station-say':
        if set(args) != {'text'} or not isinstance(args['text'], str) or not 1 <= len(args['text']) <= 300:
            raise Denied('Bad station text')
        return task, {'text': args['text']}
    if task == 'station-volume':
        if set(args) != {'level'} or type(args['level']) is not int or not 0 <= args['level'] <= 10:
            raise Denied('Bad station volume')
        return task, {'level': args['level']}
    if task == 'station-control':
        if set(args) != {'action'} or args['action'] not in ('pause', 'play', 'next', 'previous'):
            raise Denied('Bad station action')
        return task, {'action': args['action']}
    if task == 'skill-teach':
        if set(args) != {'name', 'phrase'} or not isinstance(args['name'], str) or not 2 <= len(args['name']) <= 40 \
                or not isinstance(args['phrase'], str) or not 3 <= len(args['phrase']) <= 300:
            raise Denied('Bad skill')
        return task, {'name': args['name'], 'phrase': args['phrase']}
    if task == 'tv-play':
        if set(args) != {'url'} or not isinstance(args['url'], str) or not 10 <= len(args['url']) <= 500                 or not args['url'].lower().startswith(('http://', 'https://')):
            raise Denied('Bad link')
        return task, {'url': args['url']}  # the router keeps only a YouTube video id from it
    if task == 'tv-link':
        if set(args) != {'code'} or not isinstance(args['code'], str) or not re.fullmatch(r'\d{12}', args['code']):
            raise Denied('Bad code')
        return task, {'code': args['code']}
    if task == 'skill-unlearn':
        if set(args) != {'phrase'} or not isinstance(args['phrase'], str) or not 2 <= len(args['phrase']) <= 300:
            raise Denied('Bad phrase')
        return task, {'phrase': args['phrase']}
    if task in ('skill-save', 'skill-forget'):
        if set(args) != {'name'} or not isinstance(args['name'], str) or not 2 <= len(args['name']) <= 40:
            raise Denied('Bad skill name')
        return task, {'name': args['name']}
    if task in ('home-devices', 'station-status', 'phone-status', 'skill-list'):
        if args:
            raise Denied('No arguments expected')
        return task, {}
    if task == 'music-status':
        if args:
            raise Denied('No arguments expected')
        return task, {}
    if task == 'reminder-list':
        if args:
            raise Denied('No arguments expected')
        return task, {}
    if task == 'profile-show':
        if args:
            raise Denied('No arguments expected')
        return task, {}
    if task == 'pc-find':
        if set(args) != {'query'} or not isinstance(args['query'], str) or not 2 <= len(args['query']) <= 100:
            raise Denied('Bad search')
        return task, {'query': args['query']}
    if task == 'pc-send':
        if set(args) != {'index'} or type(args['index']) is not int or not 1 <= args['index'] <= 10:
            raise Denied('Bad file number')
        return task, {'index': args['index']}
    if task == 'app-open':
        if set(args) != {'target'} or not isinstance(args['target'], str) or not 1 <= len(args['target']) <= 40:
            raise Denied('Bad app')
        return task, {'target': args['target']}
    if task == 'file-open':
        if set(args) != {'index'} or type(args['index']) is not int or not 1 <= args['index'] <= 10:
            raise Denied('Bad file number')
        return task, {'index': args['index']}
    if task in ('desktop-show', 'windows-list'):
        if args:
            raise Denied('No arguments expected')
        return task, {}
    if task == 'web-answer':
        if set(args) != {'question'} or not isinstance(args['question'], str) or not 2 <= len(args['question']) <= 300:
            raise Denied('Bad question')
        return task, {'question': args['question']}
    if task == 'web-search':
        if set(args) != {'query'} or not isinstance(args['query'], str) or not 2 <= len(args['query']) <= 300:
            raise Denied('Bad query')
        return task, {'query': args['query']}
    if task == 'web-open':
        if set(args) != {'url'} or not isinstance(args['url'], str) or not 4 <= len(args['url']) <= 500:
            raise Denied('Bad site')
        return task, {'url': args['url']}
    if task == 'web-open-result':
        if set(args) != {'index'} or type(args['index']) is not int or not 1 <= args['index'] <= 10:
            raise Denied('Bad link number')
        return task, {'index': args['index']}
    if task == 'web-site-search':
        from .web_ops import SITE_SEARCH
        if set(args) != {'site', 'query'} or args['site'] not in SITE_SEARCH or not isinstance(args['query'], str) \
                or not 1 <= len(args['query']) <= 200:
            raise Denied('Bad site search')
        return task, {'site': args['site'], 'query': args['query']}
    if task in ('excel-summary', 'excel-column', 'excel-find', 'excel-open'):
        need = {'excel-summary': {'name'}, 'excel-column': {'name', 'column', 'func'}, 'excel-find': {'name', 'text'},
                'excel-open': {'name'}}[task]
        if set(args) - {'sheet'} != need or any(not isinstance(args[k], str) or not 1 <= len(args[k]) <= 120 for k in args):
            raise Denied('Bad table command')
        if task == 'excel-column' and args['func'] not in ('sum', 'avg', 'max', 'min', 'count'):
            raise Denied('Bad table function')
        return task, dict(args)
    if task in ('doc-read', 'doc-find', 'doc-open'):
        need = {'name', 'text'} if task == 'doc-find' else {'name'}
        if set(args) != need or any(not isinstance(args[k], str) or not 1 <= len(args[k]) <= 120 for k in args):
            raise Denied('Bad document command')
        return task, dict(args)
    if task == 'web-save':
        if args:
            raise Denied('No arguments expected')
        return task, {}
    if task == 'screen-describe':
        if set(args) - {'question'} or not isinstance(args.get('question', ''), str) or len(args.get('question', '')) > 300:
            raise Denied('Bad screen question')
        return task, {'question': args.get('question', '')}
    if task in ('pc-clip-get', 'pc-screen'):
        if args:
            raise Denied('No arguments expected')
        return task, {}
    if task == 'pc-clip-set':
        if set(args) != {'text'} or not isinstance(args['text'], str) or not 1 <= len(args['text']) <= 20000:
            raise Denied('Bad clipboard text')
        return task, {'text': args['text']}
    if task == 'media-scene':
        if set(args) != {'name'} or args['name'] not in SCENES:
            raise Denied('Unknown scene')
        return task, {'name': args['name']}
    if task == 'media-mute':
        if set(args) != {'on'} or type(args['on']) is not bool:
            raise Denied('Bad mute arguments')
        return task, {'on': args['on']}
    if set(args) == {'level'} and type(args['level']) is int and 0 <= args['level'] <= PHONE_MAX_VOLUME:
        return task, {'level': args['level']}
    if set(args) == {'delta'} and type(args['delta']) is int and 1 <= abs(args['delta']) <= MAX_STEP:
        return task, {'delta': args['delta']}
    raise Denied('С телефона: громкость 0..%d, шаг до %d' % (PHONE_MAX_VOLUME, MAX_STEP))


def speech_for(task, result):
    after = result.get('after') or {}
    if task == 'media-scene' or task.startswith(('pc-', 'profile-', 'reminder-', 'music', 'home-', 'station-', 'screen-', 'web-', 'chat-',
                                                  'phone-', 'skill-', 'tv-',
                                                  'app-', 'file-open', 'desktop-', 'windows-list')):
        return result.get('speech') or 'Готово.'
    if result.get('status') != 'PASS':
        return 'Команда отправлена, но телевизор не подтвердил результат.'
    if task == 'media-mute':
        return 'Звук выключен.' if after.get('mute') else 'Звук включён.'
    return 'Громкость %s.' % after.get('volume')


class PhoneExecutor:
    def __init__(self, workspace, router=None, clock=time.monotonic, chat=None):
        self.chat = chat  # tests inject a fake LLM
        self.router = router or UnifiedTaskRouter(TrustedLocalSession.phone_allowlist(ALLOWED), workspace)
        self.queue = PhoneQueue(self.router.ws)
        self.clock = clock
        self.recent = collections.deque()

    def interpret(self, item):
        """Feature 6 for the phone: plan free speech; run it only if EVERY step is allowlisted."""
        from .llm_planner import understand_smart
        plan = understand_smart(item['text'], skills=self.router.skills, **({'chat': self.chat} if self.chat else {}))
        if plan['intent'] != 'plan':
            self.queue.note(item['id'], 'Не понял команду. Запрос оставлен в очереди для компьютера.'
                            if plan['intent'] == 'unknown' else 'Это нельзя: ' + plan.get('reason', '')[:120])
            return
        try:
            checked = [validate({'task': s['task'], 'args': s['args']}) for s in plan['steps']]
        except Denied:
            self.queue.note(item['id'], 'План: %s. Нужно подтверждение за компьютером.' % plan['summary'][:200])
            return
        speech = []
        if not self.allow_rate():
            self.queue.close(item['id'], 'failed', 'Слишком много команд подряд, подождите минуту.', origin='phone-executor')
            return
        try:
            for task, args in checked:
                speech.append(speech_for(task, self.router.dispatch(task, **self.capped(task, args))))
            offer = self.router.skills.done(item['text'], plan)
            if plan.get('learn'):
                speech.append('Запомнил: «%s» теперь значит это.' % plan['learn'][:60])
            if offer:
                speech.append(offer)
            self.queue.close(item['id'], 'done',
                             self.personal(item['text'], [label(t, a) for t, a in checked], ' '.join(speech)),
                             origin='phone-executor')
        except (Denied, OSError, ValueError) as exc:
            self.queue.close(item['id'], 'failed', 'Не получилось: ' + str(exc)[:120], origin='phone-executor')

    def tick(self):
        handled = 0
        for item in self.queue.items():
            if item['status'] == 'pending' and item.get('interpret'):
                try:
                    self.interpret(item)
                except Exception as exc:
                    self.queue.note(item['id'], 'Ошибка разбора: ' + type(exc).__name__)
                handled += 1
                continue
            if item['status'] != 'pending' or not item.get('auto'):
                continue
            if not self.allow_rate():
                self.queue.close(item['id'], 'failed', 'Слишком много команд подряд, подождите минуту.', origin='phone-executor')
                continue
            try:
                task, args = validate(item['auto'])
                args = self.capped(task, args)
                result = self.router.dispatch(task, **args)
                self.queue.close(item['id'], 'done', self.personal(item['text'], [label(task, args)],
                                                                   speech_for(task, result)),
                                 origin='phone-executor', link=result.get('link'), text=result.get('text'))
            except (Denied, OSError, ValueError) as exc:
                self.queue.close(item['id'], 'failed', 'Не получилось: ' + str(exc)[:120], origin='phone-executor')
            handled += 1
        return handled

    def allow_rate(self):
        """One shared limit for quick commands AND free speech: RATE_LIMIT per 60 s."""
        now = self.clock()
        while self.recent and now - self.recent[0] > 60:
            self.recent.popleft()
        if len(self.recent) >= RATE_LIMIT:
            return False
        self.recent.append(now)
        return True

    def capped(self, task, args):
        """The phone volume ceiling applies to relative steps on every path."""
        if task == 'media-volume' and 'delta' in args:
            current = self.router.dispatch('media-status').get('volume', 0)
            if current + args['delta'] > PHONE_MAX_VOLUME:
                return {'level': PHONE_MAX_VOLUME}
        return args

    def personal(self, phrase, labels, speech):
        """Learn from the command (counters only) and adapt the reply to the user's style."""
        try:
            profile = self.router.profile
            if not any(lb.startswith('profile-forget') for lb in labels):
                profile.observe(phrase, labels)
            return profile.adapt(speech)
        except Exception:
            return speech

    def suggest(self, now):
        """Proactive but polite: push a SUGGESTION of a habitual scene (never runs it)."""
        names = {'game': 'режим игры', 'movie': 'режим кино', 'night': 'режим сна'}
        for scene, title in names.items():
            if self.router.profile.suggestion('media-scene:' + scene):
                self._push('JARVIS', 'Обычно в это время вы включаете %s. Сказать «%s»?' % (title, title), '/')
                return scene
        return None

    def heartbeat(self):
        (self.router.ws.state / 'executor_heartbeat.txt').write_text('%.0f' % time.time(), encoding='ascii')

    def scheduled(self, now=None):
        """Feature 4/5: morning briefing at the configured time + hourly doctor alerts (push)."""
        import datetime
        import json
        now = now or datetime.datetime.now()
        state = self.router.ws.state / 'schedule_state.json'
        try:
            s = json.loads(state.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            s = {}
        cfg_time = '08:00'
        try:
            cfg_time = json.loads((self.router.ws.state / 'briefing_config.json').read_text(encoding='utf-8')).get('time', '08:00')
        except (OSError, ValueError):
            pass
        today = now.strftime('%Y-%m-%d')
        if now.strftime('%H:%M') >= cfg_time and s.get('briefing_day') != today and now.hour < 12:
            s['briefing_day'] = today
            state.write_text(json.dumps(s), encoding='utf-8')
            b = self.router.dispatch('briefing')
            self._push('Утренний брифинг', b['text'][:180], '/?briefing=1')
        if s.get('doctor_hour') != now.strftime('%Y-%m-%d %H'):
            s['doctor_hour'] = now.strftime('%Y-%m-%d %H')
            state.write_text(json.dumps(s), encoding='utf-8')
            if now.minute < 15:
                self.suggest(now)
            report = self.router.dispatch('doctor')
            new = [c['name'] for c in report['checks'] if c['status'] == 'FAIL'
                   and today + c['name'] not in s.get('alerted', [])]
            if new:
                s['alerted'] = [a for a in s.get('alerted', []) if a.startswith(today)] + [today + n for n in new]
                state.write_text(json.dumps(s), encoding='utf-8')
                self._push('JARVIS: нужна помощь', report['summary'], '/')

    def _push(self, title, body, url):
        try:
            self.router.push.notify(title, body, url)
        except Exception:
            pass  # no subscribed phone yet: the briefing is still available in the app

    def fire_reminders(self, now=None, speak=None):
        """Due reminders: say them aloud at the PC (JARVIS voice) and push them to the phone."""
        fired = self.router.reminders.due(now)
        quiet = bool(fired) and self.router.quiet()  # Sleep on the iPhone: push only, nobody speaks
        for item in fired:
            text = 'Сэр, напоминаю: %s.' % item['text'].rstrip('.')
            try:
                text = self.router.profile.adapt(text)
            except Exception:
                pass
            if speak is None and not quiet:
                from .voice_loop import say
                speak = say
            try:
                if not quiet:
                    speak(text)
            except Exception:
                pass
            station = False
            try:  # at home the Yandex Station says it too, so it is heard away from the PC
                sp = self.router.speaker() if self.router.at_home() and not quiet else None
                if sp is not None:
                    sp.say_long(text)
                    station = True
            except Exception:
                pass
            self._push('Напоминание', item['text'][:180], '/')
            self.router.ws.journal.record('reminder-fired', 'PASS', reminder=item['id'], repeat=item.get('repeat'),
                                          station=station, quiet=quiet, rollback=[])
        return fired

    def watch_phone(self):
        """Presence / Sleep / battery of the iPhone via Home Assistant (phone_watch), when HA is set up."""
        if not hasattr(self, 'phone_watch'):
            from .station import keys
            from .phone_watch import PhoneWatch
            self.phone_watch = PhoneWatch(self.router) if keys().configured() else None
        return self.phone_watch.step() if self.phone_watch else []

    def run(self, interval=0.5):
        last_sched = last_remind = 0
        while True:
            self.tick()
            self.heartbeat()
            if time.monotonic() - last_remind > 5:
                last_remind = time.monotonic()
                try:
                    self.fire_reminders()
                except Exception as exc:
                    print('reminder error:', type(exc).__name__, str(exc)[:200], flush=True)
            if time.monotonic() - last_sched > 30:
                last_sched = time.monotonic()
                try:
                    from .outbox import sweep
                    sweep(self.router.ws.state)  # expired one-time links
                    self.scheduled()
                except Exception as exc:
                    print('schedule error:', type(exc).__name__, str(exc)[:200], flush=True)
                try:
                    self.watch_phone()
                except Exception as exc:  # HA down or restarting: try again in 30 s
                    print('phone watch error:', type(exc).__name__, str(exc)[:200], flush=True)
            time.sleep(interval)


def main():
    ap = argparse.ArgumentParser(description='Executes allowlisted phone quick commands (volume / mute only)')
    ap.add_argument('--workspace', required=True)
    a = ap.parse_args()
    print('PHONE EXECUTOR ready: allowlist=%s, volume<=%d' % (','.join(ALLOWED), PHONE_MAX_VOLUME), flush=True)
    try:
        PhoneExecutor(a.workspace).run()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
