"""Ephemeral browser fallback for public video pages unsupported by yt-dlp.
No saved profile, account cookies, arbitrary clicks, or direct TV commands.
Only known play controls inside video players may be activated.
"""
import ipaddress
import re
import socket
import time
from pathlib import Path
from urllib.parse import urlsplit
from .session import Denied

MEDIA_TYPES = ('video/mp4','video/webm','application/vnd.apple.mpegurl','application/x-mpegurl')

def public_url(url, lookup=socket.getaddrinfo):
    try:
        parsed=urlsplit(url)
        if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password:
            return False
        if parsed.port not in (None,80,443):return False
        host=parsed.hostname.lower()
        if host=='localhost' or host.endswith(('.local','.lan','.home','.internal','.ts.net')):return False
        addresses={row[4][0] for row in lookup(host,parsed.port or (443 if parsed.scheme=='https' else 80),type=socket.SOCK_STREAM)}
        return bool(addresses) and all(ipaddress.ip_address(address).is_global for address in addresses)
    except (ValueError,OSError):return False

def media_url(url, content_type=''):
    path=urlsplit(url).path.lower()
    return bool(re.search(r'\.(?:mp4|m3u8|webm)$',path)) or content_type.split(';')[0].lower() in MEDIA_TYPES

def browser_resolve(url, *, timeout=22, setup=None, validator=public_url):
    if not validator(url):raise Denied('Нужна ссылка на общедоступную страницу с видео.')
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    executable=Path(__file__).resolve().parents[2]/'.browsers/chromium_headless_shell-1248/chrome-headless-shell-win64/chrome-headless-shell.exe'
    candidates={};address_cache={}
    def allowed(address):
        parsed=urlsplit(address);key=(parsed.scheme,parsed.netloc)
        if key not in address_cache:address_cache[key]=validator(address)
        return address_cache[key]
    with sync_playwright() as play:
        browser=play.chromium.launch(**({'executable_path':str(executable)} if executable.is_file() else {}),headless=True,
            args=['--autoplay-policy=no-user-gesture-required'])
        context=browser.new_context(accept_downloads=False,service_workers='block')
        context.set_default_timeout(1500)
        context.set_default_navigation_timeout(15000)
        page=context.new_page()
        try:
            def remember(request, content_type=''):
                address=request.url
                if media_url(address,content_type) and allowed(address):
                    headers=request.all_headers()
                    candidates[address]={'Referer':headers.get('referer',url),
                                         'User-Agent':headers.get('user-agent','')}
                    if headers.get('cookie'):candidates[address]['Cookie']=headers['cookie']
            def guard(route):
                request=route.request
                if request.method not in ('GET','HEAD') or not allowed(request.url):return route.abort()
                if request.resource_type in ('image','font'):return route.abort()
                if media_url(request.url):
                    remember(request)
                    return route.abort()  # only discover the source, do not download video in the browser
                return route.continue_()
            context.route('**/*',guard)
            context.on('response',lambda response:remember(response.request,response.headers.get('content-type','')) if response.status in (200,206) else None)
            page.on('dialog',lambda dialog:dialog.dismiss())
            context.on('page',lambda popup:popup.close() if popup!=page else None)
            if setup:setup(context)
            end=time.monotonic()+min(timeout,30)
            try:page.goto(url,wait_until='domcontentloaded')
            except BrowserTimeout:pass
            attempted = set()
            # Activate only video elements or known player play controls, never forms.
            while time.monotonic()<end:
                sources=[]
                for frame in page.frames:
                    try:
                        if frame.url not in attempted:
                            controls = frame.locator('.kt-player .fp-play, .video-js .vjs-big-play-button, .jwplayer .jw-icon-playback, video + button[aria-label="Play"]')
                            if controls.count() and controls.first.is_visible():
                                attempted.add(frame.url)
                                controls.first.click(timeout=1200, no_wait_after=True)
                        sources.extend(frame.locator('video, video source').evaluate_all(
                            "nodes => nodes.map(n => n.currentSrc || n.src).filter(Boolean)"))
                        frame.locator('video').evaluate_all(
                            "nodes => nodes.forEach(n => { n.muted = true; n.play().catch(() => {}); })")
                    except Exception:continue
                for source in sources:
                    if source.startswith(('https://','http://')) and allowed(source) and media_url(source):
                        candidates.setdefault(source,{'Referer':page.url})
                        title=page.title()[:120] or 'Видео'
                        return title,[source],candidates[source]
                if candidates:
                    # HLS usually contains both tracks and works with the existing ffmpeg relay.
                    source=sorted(candidates,key=lambda value:('.m3u8' not in value.lower(),list(candidates).index(value)))[0]
                    return (page.title() or 'Видео')[:120],[source],candidates[source]
                page.wait_for_timeout(500)
        except Denied:raise
        except Exception:
            raise Denied('Сайт не отдал видеопоток резервному браузеру. Нужна прямая ссылка на видео или поддерживаемая страница.') from None
        finally:
            context.close();browser.close()
    raise Denied('Сайт не отдал видеопоток. Возможны вход в аккаунт или защищённый плеер; попробуйте прямую ссылку MP4/M3U8.')

def resolve_with_browser(url):
    from .tv_media import resolve
    try:return resolve(url)
    except Denied:return browser_resolve(url)
