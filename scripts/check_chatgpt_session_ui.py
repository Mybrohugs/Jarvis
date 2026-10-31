"""Browser/real DEV HTTP/auth integration; OpenAI inference is mocked, never TV actions."""
import http.client
import argparse
import json
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from developer_core.jarvis_local.pwa_gateway import build
from developer_core.jarvis_local.tv_phone_dev import build_preview
from developer_core.jarvis_local.chatgpt_plan_engine import ChatGPTError
class FakeChat:
    def __init__(self):self.calls=[];self.failure=None
    def status(self):
        return {'connected':False, **self.failure.public()} if self.failure else {'connected':True,'provider':'chatgpt','model':'test-account-model'}
    def turn(self,data):
        if self.failure:raise self.failure
        self.calls.append(data)
        return {'provider':'chatgpt','model':'test-account-model','text':'На связи, сэр.',
                'output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':'На связи, сэр.'}]}]}
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--mobile',type=Path,required=True);args=parser.parse_args();mobile=args.mobile.resolve(strict=True)
with tempfile.TemporaryDirectory() as temp:
    token='test-device-token-'+'b'*32
    gateway=build(Path(temp),token,0)
    chat=FakeChat();host='mobile-dev.test.ts.net:8443'
    preview=build_preview(mobile,gateway,port=0,public_host=host,chat=chat)
    for server in (gateway,preview):threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as play:
            executable=root/'.browsers/chromium_headless_shell-1248/chrome-headless-shell-win64/chrome-headless-shell.exe'
            browser=play.chromium.launch(**({'executable_path':str(executable)} if executable.exists() else {}),headless=True)
            page=browser.new_page(viewport={'width':390,'height':844},service_workers='block')
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            def route(handler):
                from urllib.parse import urlsplit
                url=urlsplit(handler.request.url)
                if url.netloc!=host:return handler.abort()
                conn=http.client.HTTPConnection('127.0.0.1',preview.server_port,timeout=20)
                headers=dict(handler.request.headers);headers['Host']=host
                conn.request(handler.request.method,url.path+('?' + url.query if url.query else ''),handler.request.post_data_buffer,headers)
                response=conn.getresponse();body=response.read()
                handler.fulfill(status=response.status,body=body,headers={'Content-Type':response.getheader('Content-Type','application/json')})
                conn.close()
            page.route('**/*',route)
            page.add_init_script("localStorage.setItem('jarvis.mobile.pcUrl','https://"+host+"');localStorage.setItem('jarvis.mobile.pcToken','"+token+"');localStorage.setItem('jarvis.mobile.speak','0');")
            page.goto('https://'+host+'/jarvis-dev/')
            expect(page.locator('#conn')).to_have_text('ChatGPT')
            expect(page.locator('#say')).to_be_visible()
            page.locator('#say').fill('Привет');page.locator('#say').press('Enter')
            expect(page.locator('#reply')).to_have_text('На связи, сэр.')
            assert len(chat.calls)==1
            expect(page.locator('#tvform')).to_be_attached()
            page.locator('#gear').click()
            expect(page.locator('#chatgptstate')).to_contain_text('ChatGPT подключён')
            expect(page.locator('#claudekey')).to_have_count(0)
            expect(page.locator('#geminikey')).to_have_count(0)
            page.locator('#close').click()
            assert not errors,errors
            for kind,label in [('network','нет интернета'),('rate_limited','лимит ChatGPT'),('auth_required','нужен вход')]:
                chat.failure=ChatGPTError(kind);page.reload();expect(page.locator('#conn')).to_have_text(label)
            chat.failure=None;page.reload();expect(page.locator('#conn')).to_have_text('ChatGPT')
            browser.close()
            print(json.dumps({'browser_http_auth_chat':'PASS','recovery_statuses':'PASS','model':'mocked','viewport':'390x844','js_errors':errors}))
    finally:
        for server in (preview,gateway):server.shutdown();server.server_close()
