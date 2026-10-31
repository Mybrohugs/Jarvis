import unittest
from unittest.mock import patch
from developer_core.jarvis_local.tv_web_video import public_url, media_url, resolve_with_browser
from developer_core.jarvis_local.session import Denied
class WebVideoTests(unittest.TestCase):
    def test_only_public_destinations(self):
        def dns(address):return lambda *a,**kw:[(None,None,None,None,(address,443))]
        self.assertTrue(public_url('https://public.example/v',lookup=dns('8.8.8.8')))
        for address in ('127.0.0.1','192.168.1.1','100.113.165.25','::1','169.254.169.254'):
            self.assertFalse(public_url('https://public.example/v',lookup=dns(address)))
        for url in ('file:///secret','https://localhost/x','https://pc.ts.net/v','https://u:p@public.example/v','https://public.example:8123/v'):
            self.assertFalse(public_url(url,lookup=dns('8.8.8.8')))
    def test_stream_classification(self):
        self.assertTrue(media_url('https://cdn.example/main.m3u8?token=x'))
        self.assertTrue(media_url('https://cdn.example/stream','video/mp4; charset=binary'))
        self.assertFalse(media_url('https://cdn.example/player.js'))
    def test_existing_extractor_first_and_fallback_only_on_failure(self):
        expected=('title',['https://cdn.example/v.mp4'],{})
        with patch('developer_core.jarvis_local.tv_media.resolve',return_value=expected),patch('developer_core.jarvis_local.tv_web_video.browser_resolve') as fallback:
            self.assertEqual(resolve_with_browser('https://public.example/v'),expected);fallback.assert_not_called()
        with patch('developer_core.jarvis_local.tv_media.resolve',side_effect=Denied('unsupported')),patch('developer_core.jarvis_local.tv_web_video.browser_resolve',return_value=expected) as fallback:
            self.assertEqual(resolve_with_browser('https://public.example/v'),expected);fallback.assert_called_once()

    def test_real_browser_activates_embedded_player_and_discovers_source(self):
        from developer_core.jarvis_local.tv_web_video import browser_resolve
        def setup(context):
            def fixture(route):
                url=route.request.url
                if '/embed' in url:
                    html='<div class="kt-player"><a class="fp-play" onclick="const v=document.createElement(\'video\');v.src=\'https://cdn.fixture.example/video.mp4\';document.body.append(v)">Play</a></div>'
                    route.fulfill(body=html,content_type='text/html')
                elif '/video.mp4' in url:route.fulfill(status=200,body=b'',content_type='video/mp4')
                else:route.fulfill(body='<title>Fixture</title><iframe src="https://player.fixture.example/embed"></iframe>',content_type='text/html')
            context.route('**/*',fixture)
        title,inputs,headers=browser_resolve('https://page.fixture.example/watch',timeout=5,setup=setup,
            validator=lambda url:'.fixture.example/' in url)
        self.assertEqual(title,'Fixture');self.assertEqual(inputs,['https://cdn.fixture.example/video.mp4'])
