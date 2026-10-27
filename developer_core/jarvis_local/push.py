"""Feature 3: Web Push to the iPhone home-screen app (iOS 16.4+), via Apple's push service.

VAPID keys live in the workspace state folder (private key never leaves the PC; only the
public key is served to the phone). Payloads are encrypted end-to-end by Web Push (RFC 8291),
so Apple cannot read them. Sending happens only from local processes (executor/briefing),
never from the phone gateway.
"""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
from .session import Denied

LOCAL_CONFIG = Path(__file__).resolve().parents[2] / '.jarvis-local.json'  # machine-only, not in git


def tailnet_host(config=LOCAL_CONFIG):
    """The owner's private Tailscale name: env JARVIS_TAILNET_HOST or .jarvis-local.json (never committed)."""
    host = os.environ.get('JARVIS_TAILNET_HOST', '')
    if not host and Path(config).is_file():
        host = json.loads(Path(config).read_text(encoding='utf-8')).get('tailnet_host', '')
    return host


def vapid_sub():
    host = tailnet_host()
    return 'https://' + host if host else 'mailto:jarvis@localhost.invalid'  # contact claim for push services
ALLOWED_PUSH_HOSTS = ('web.push.apple.com', 'fcm.googleapis.com', 'updates.push.services.mozilla.com')


class Push:
    def __init__(self, ws):
        self.ws = ws
        self.key_file = ws.state / 'push_vapid_private.pem'
        self.pub_file = ws.state / 'push_vapid_public.txt'
        self.subs_file = ws.state / 'push_subscriptions.json'

    def ensure_keys(self):
        if self.key_file.is_file() and self.pub_file.is_file():
            return self.pub_file.read_text(encoding='ascii').strip()
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        pub = base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')
        with self.key_file.open('xb') as f:
            f.write(pem)
        self.pub_file.write_text(pub, encoding='ascii')
        return pub

    def subscriptions(self):
        try:
            data = json.loads(self.subs_file.read_text(encoding='utf-8'))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def add_subscription(self, sub):
        from urllib.parse import urlsplit
        if not isinstance(sub, dict) or not isinstance(sub.get('endpoint'), str) or not isinstance(sub.get('keys'), dict):
            raise ValueError('invalid subscription')
        host = urlsplit(sub['endpoint']).hostname or ''
        if urlsplit(sub['endpoint']).scheme != 'https' or not host.endswith(ALLOWED_PUSH_HOSTS):
            raise ValueError('push endpoint host not allowed')
        clean = {'endpoint': sub['endpoint'][:1000],
                 'keys': {'p256dh': str(sub['keys'].get('p256dh', ''))[:200], 'auth': str(sub['keys'].get('auth', ''))[:100]}}
        subs = [s for s in self.subscriptions() if s['endpoint'] != clean['endpoint']][-4:] + [clean]
        self.subs_file.write_text(json.dumps(subs), encoding='utf-8')
        return {'status': 'PASS', 'devices': len(subs)}

    def notify(self, title, body, url='/', sender=None):
        """Returns per-device results; drops subscriptions the push service reports as gone."""
        from pywebpush import WebPushException, webpush
        sender = sender or webpush
        self.ensure_keys()
        payload = json.dumps({'title': str(title)[:80], 'body': str(body)[:300], 'url': url if url.startswith('/') else '/'},
                             ensure_ascii=False)
        sent, keep = 0, []
        for sub in self.subscriptions():
            try:
                sender(subscription_info=sub, data=payload, vapid_private_key=str(self.key_file),
                       vapid_claims={'sub': vapid_sub()}, ttl=3600)
                sent += 1
                keep.append(sub)
            except WebPushException as exc:
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                if status not in (404, 410):
                    keep.append(sub)
        self.subs_file.write_text(json.dumps(keep), encoding='utf-8')
        self.ws.journal.record('push-notify', 'PASS' if sent else 'NO_DEVICE', title=str(title)[:80], devices=sent,
                               rollback=[])
        if not sent and not keep:
            raise Denied('No iPhone subscribed to notifications yet')
        return {'status': 'PASS' if sent else 'FAILED', 'sent': sent}
