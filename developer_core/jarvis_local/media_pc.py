"""Music and video on the PC: pause / play / next / previous / "what is playing" for ANY player
(Spotify, Яндекс Музыка, YouTube in a browser, VLC...) through Windows System Media Transport
Controls. No key injection and no window focus: Windows routes the command to the current session.
"""
from __future__ import annotations
import asyncio

ACTIONS = ('play', 'pause', 'toggle', 'next', 'previous', 'stop')
SPEECH = {'play': 'Продолжаю.', 'pause': 'Пауза.', 'toggle': 'Готово.', 'next': 'Следующий трек.',
          'previous': 'Предыдущий трек.', 'stop': 'Остановил.'}
APP_NAMES = (('spotify', 'Spotify'), ('yandex', 'Яндекс Музыка'), ('chrome', 'Chrome'), ('msedge', 'Edge'),
             ('firefox', 'Firefox'), ('vlc', 'VLC'), ('zunemusic', 'Музыка Windows'), ('telegram', 'Telegram'))


def app_name(app_id):
    low = (app_id or '').lower()
    return next((name for key, name in APP_NAMES if key in low), app_id or 'плеер')


class WinRTMedia:
    """Thin async wrapper over GlobalSystemMediaTransportControlsSessionManager."""

    async def _session(self):
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
        return (await Manager.request_async()).get_current_session()

    async def _status(self):
        s = await self._session()
        if s is None:
            return None
        props = await s.try_get_media_properties_async()
        state = int(s.get_playback_info().playback_status)  # 4 playing, 5 paused, 3 stopped
        return {'app': s.source_app_user_model_id, 'title': props.title or '', 'artist': props.artist or '',
                'playing': state == 4}

    async def _control(self, action):
        s = await self._session()
        if s is None:
            return False
        fn = {'play': s.try_play_async, 'pause': s.try_pause_async, 'toggle': s.try_toggle_play_pause_async,
              'next': s.try_skip_next_async, 'previous': s.try_skip_previous_async, 'stop': s.try_stop_async}[action]
        return bool(await fn())

    def status(self):
        return asyncio.run(self._status())

    def control(self, action):
        return asyncio.run(self._control(action))


class MediaPC:
    def __init__(self, backend=None):
        self.backend = backend or WinRTMedia()

    def now_playing(self):
        st = self.backend.status()
        if not st:
            return {'status': 'PASS', 'playing': False, 'speech': 'Сейчас на компьютере ничего не играет.'}
        what = ' — '.join(x for x in (st['artist'], st['title']) if x) or 'без названия'
        verb = 'Играет' if st['playing'] else 'На паузе'
        return {'status': 'PASS', **st, 'app_name': app_name(st['app']),
                'speech': '%s в %s: %s.' % (verb, app_name(st['app']), what)}

    def control(self, action):
        if action not in ACTIONS:
            raise ValueError('Unknown media action')
        ok = self.backend.control(action)
        if not ok:
            return {'status': 'WARN', 'action': action,
                    'speech': 'Плеер не ответил: возможно, ничего не открыто или он не поддерживает эту команду.'}
        return {'status': 'PASS', 'action': action, 'speech': SPEECH[action]}
