"""DEV-only Home Assistant Sony connector.

No persistence or token retrieval. Caller must provide a secret from an
approved local credential store, and must authorize each state-changing action.
No model-generated entity IDs, services, URLs or command payloads are accepted.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

BASE_URL = 'http://127.0.0.1:8123'
SONY_ENTITY = 'media_player.bravia_kd_55x81j'

class HomeAssistantError(RuntimeError):
    """Safe error class; never includes secret or raw network exceptions."""

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

@dataclass(frozen=True)
class SonyState:
    entity_id: str
    state: str
    friendly_name: str
    volume_level: float | None

class SonyConnector:
    def __init__(self, token: str, *, allow_control: bool = False, timeout: float = 8.0):
        if not isinstance(token, str) or not token or '\r' in token or '\n' in token:
            raise HomeAssistantError('Invalid credentials')
        if not isinstance(allow_control, bool):
            raise HomeAssistantError('Invalid control policy')
        if not isinstance(timeout, (int, float)) or not (0 < timeout <= 15):
            raise HomeAssistantError('Invalid timeout')
        self._token = token
        self._allow_control = allow_control
        self._timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect(), urllib.request.ProxyHandler({}))

    def _request(self, path: str, *, payload: dict | None = None) -> dict:
        if path not in (
            f'/api/states/{SONY_ENTITY}',
            '/api/services/media_player/turn_on',
            '/api/services/media_player/turn_off',
            '/api/services/media_player/volume_set',
            '/api/services/media_player/play_media',
        ):
            raise HomeAssistantError('Action is not allowlisted')
        url = BASE_URL + path
        headers = {'Authorization': 'Bearer ' + self._token, 'Accept': 'application/json'}
        method = 'GET' if payload is None else 'POST'
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, headers=headers, method=method, data=data)
        try:
            with self._opener.open(req, timeout=self._timeout) as response:
                if response.status < 200 or response.status >= 300:
                    raise HomeAssistantError('Home Assistant returned a non-success status')
                body = response.read(65537)
                if len(body) > 65536:
                    raise HomeAssistantError('Home Assistant response too large')
                decoded = json.loads(body)
                if path.startswith('/api/states/') and not isinstance(decoded, dict):
                    raise HomeAssistantError('Invalid state response')
                return decoded
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise HomeAssistantError('Home Assistant authorization denied') from None
            raise HomeAssistantError('Home Assistant request failed') from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, UnicodeError):
            raise HomeAssistantError('Home Assistant unavailable or invalid response') from None

    def status(self) -> SonyState:
        raw = self._request(f'/api/states/{SONY_ENTITY}')
        if raw.get('entity_id') != SONY_ENTITY or not isinstance(raw.get('state'), str):
            raise HomeAssistantError('Unexpected entity response')
        attrs = raw.get('attributes') or {}
        if not isinstance(attrs, dict):
            raise HomeAssistantError('Invalid entity attributes')
        volume = attrs.get('volume_level')
        if not isinstance(volume, (float, int)) or isinstance(volume, bool) or not 0 <= volume <= 1:
            volume = None
        name = attrs.get('friendly_name')
        return SonyState(SONY_ENTITY, raw['state'], name if isinstance(name, str) else '', volume)

    def _control(self, action: str, extra: dict | None = None) -> None:
        if not self._allow_control:
            raise HomeAssistantError('Device control not authorized')
        payload = {'entity_id': SONY_ENTITY}
        if extra:
            payload.update(extra)
        self._request(f'/api/services/media_player/{action}', payload=payload)

    def turn_on(self) -> None:
        self._control('turn_on')

    def turn_off(self) -> None:
        self._control('turn_off')

    def set_volume(self, percent: int) -> None:
        if type(percent) is not int or not 0 <= percent <= 100:
            raise HomeAssistantError('Volume must be an integer from 0 to 100')
        self._control('volume_set', {'volume_level': percent / 100})

    def open_youtube(self) -> None:
        """Open the installed app, not an arbitrary URL or an unverified video."""
        self._control('play_media', {'media_content_id': 'YouTube', 'media_content_type': 'app'})
