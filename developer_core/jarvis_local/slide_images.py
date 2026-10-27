"""Pictures for slides that Claude marked with an `image` description (English, no text in the picture).

Providers, tried in order:
  gemini - draws the picture (Nano Banana, Gemini API key; needs a paid tier, free tier returns 429);
  pexels - finds a stock photo (free API key, Pexels licence: free use, no attribution required).
Downloads are untrusted: only known image hosts, size caps, decoded and re-encoded by Pillow
(cropped to the slot), so nothing but plain pixels reaches the deck. Credits go to speaker notes.
  python -m developer_core.jarvis_local.slide_images set-pexels-key --from-clipboard
"""
from __future__ import annotations
import argparse
import base64
import http.client
import io
import json
import sys
from urllib.parse import quote, urlsplit
from .gemini_voice import DEV_ROOT, KeyStore, _https as gemini_https, take_clipboard
from .session import Denied

ROOT = DEV_ROOT / '.images-dev'
GEMINI_IMAGE_MODEL = 'gemini-nano-banana-2.1'
PEXELS_HOST = 'api.pexels.com'
IMAGE_HOSTS = ('images.pexels.com',)
MAX_BYTES = 15_000_000
STYLE = ('Cinematic, high-quality, modern, clean composition, soft natural light, '
         'no text, no letters, no logos, no watermarks.')


def pexels_keys():
    return KeyStore(ROOT, name='pexels-key', label='JARVIS Pexels DEV', service='Pexels')


def _get(host, path, headers=None, limit=MAX_BYTES):
    conn = http.client.HTTPSConnection(host, timeout=30)
    try:
        conn.request('GET', path, headers={'User-Agent': 'JARVIS-local/1.0', **(headers or {})})
        r = conn.getresponse()
        raw = r.read(limit + 1)
        if r.status != 200 or len(raw) > limit:
            raise Denied('%s HTTP %d' % (host, r.status))
        return raw
    except (OSError, http.client.HTTPException) as exc:
        raise Denied('%s unreachable: %s' % (host, type(exc).__name__)) from None
    finally:
        conn.close()


def clean_image(raw, ratio):
    """Decode untrusted bytes, crop to the slot aspect ratio, re-encode as JPEG."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 60_000_000
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:
        raise Denied('Not a valid image') from None
    im = im.convert('RGB')
    w, h = im.size
    if w / h > ratio:
        nw = int(h * ratio)
        im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = int(w / ratio)
        im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    if im.width > 1600:
        im = im.resize((1600, int(1600 / ratio)))
    out = io.BytesIO()
    im.save(out, 'JPEG', quality=86)
    return out.getvalue()


class Gemini:
    name = 'gemini'

    def __init__(self, keys=None, transport=gemini_https):
        self.keys, self.transport = keys or KeyStore(), transport

    def fetch(self, description):
        r = self.transport('/v1beta/interactions', {
            'model': GEMINI_IMAGE_MODEL, 'input': description.strip() + ' ' + STYLE,
            'response_format': {'type': 'image', 'aspect_ratio': '4:3'}}, self.keys.load())
        data = (r.get('output_image') or {}).get('data')
        if not data:
            raise Denied('Gemini returned no image')
        return base64.b64decode(data), 'Иллюстрация: Gemini (сгенерировано ИИ)'


class Pexels:
    name = 'pexels'

    def __init__(self, keys=None, get=_get):
        self.keys, self.get = keys or pexels_keys(), get

    def fetch(self, description):
        query = ' '.join(description.replace(',', ' ').split()[:8])
        found = json.loads(self.get(PEXELS_HOST, '/v1/search?per_page=5&orientation=landscape&query=' + quote(query),
                                    {'Authorization': self.keys.load()}, 2_000_000))
        for photo in found.get('photos') or []:
            url = (photo.get('src') or {}).get('large2x') or (photo.get('src') or {}).get('large')
            parts = urlsplit(url or '')
            if parts.scheme == 'https' and parts.hostname in IMAGE_HOSTS:
                raw = self.get(parts.hostname, parts.path + ('?' + parts.query if parts.query else ''))
                return raw, 'Фото: %s / Pexels' % (photo.get('photographer') or 'автор')
        raise Denied('Pexels found nothing for: ' + query)


def providers(order=('gemini', 'pexels')):
    out = []
    for name in order:
        if name == 'gemini' and KeyStore().configured():
            out.append(Gemini())
        elif name == 'pexels' and pexels_keys().configured():
            out.append(Pexels())
    return out


def fetch_all(plan, folder, provs, ratio=4 / 3, limit=8):
    """Fill slide['image_file'] for slides with an image description. Never fails the deck:
    a picture that cannot be fetched is skipped and reported."""
    folder.mkdir(exist_ok=True)
    report, dead = [], set()
    for i, sl in enumerate(plan['slides'], 1):
        desc = (sl.get('image') or '').strip()
        if not desc or len([r for r in report if r['ok']]) >= limit:
            continue
        for prov in provs:
            if prov.name in dead:
                continue
            try:
                raw, credit = prov.fetch(desc)
                path = folder / ('slide-%02d.jpg' % i)
                with path.open('xb') as f:
                    f.write(clean_image(raw, ratio))
                sl['image_file'], sl['image_credit'] = str(path), credit
                report.append({'slide': i, 'ok': True, 'provider': prov.name})
                break
            except Denied as exc:
                if 'HTTP 4' in str(exc):  # no quota / bad key: do not burn more requests on this provider
                    dead.add(prov.name)
                report.append({'slide': i, 'ok': False, 'provider': prov.name, 'error': str(exc)[:160]})
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description='Slide pictures for JARVIS presentations')
    sub = ap.add_subparsers(dest='cmd', required=True)
    k = sub.add_parser('set-pexels-key')
    k.add_argument('--from-clipboard', action='store_true')
    a = ap.parse_args(argv)
    if a.cmd == 'set-pexels-key':
        import getpass
        text = take_clipboard() if a.from_clipboard else getpass.getpass('Pexels API key (input hidden): ')
        print(json.dumps(pexels_keys().save(text)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
