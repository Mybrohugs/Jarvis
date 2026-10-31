"""Isolated mobile preview exercises existing pairing/queue and denies other actions."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock
from developer_core.jarvis_local.pwa_gateway import build
from developer_core.jarvis_local.tv_phone_dev import build_preview
from developer_core.jarvis_local.phone_chatgpt import ChatGPT
from developer_core.jarvis_local.chatgpt_plan_engine import ChatGPTError
from developer_core.jarvis_local.phone_executor import PhoneExecutor
from developer_core.jarvis_local.router import UnifiedTaskRouter
from developer_core.jarvis_local.session import TrustedLocalSession, Denied

class PhoneDevTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.mobile=root/'mobile';self.mobile.mkdir()
        (self.mobile/'index.html').write_text('<h1>DEV</h1>')
        (self.mobile/'secret.txt').write_text('not an app asset')
        self.workspace=root/'workspace';self.workspace.mkdir()
        self.gateway=build(self.workspace,'',0,say_wait=0)
        self.host='pc.test.ts.net:8443';self.origin='https://'+self.host
        self.chat=ChatGPT();self.chat.bridge=Mock()
        self.preview=build_preview(self.mobile,self.gateway,port=0,public_host=self.host,chat=self.chat)
        for server in (self.gateway,self.preview):
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)

    def request(self,method,path,body=None,token=None,host=None,origin=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.preview.server_port,timeout=5)
        headers={'Host':host or self.host,'Origin':origin or self.origin,'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        connection.request(method,path,json.dumps(body) if body is not None else None,headers)
        response=connection.getresponse();raw=response.read();status=response.status;connection.close()
        return status,raw

    def test_static_and_guards_do_not_expose_workspace_or_other_apis(self):
        self.assertEqual(self.request('GET','/')[0],200)
        for path in ('/secret.txt','/.git/config','/../secret.txt','/api/audit','/api/file/x'):
            self.assertEqual(self.request('GET',path)[0],404,path)
        self.assertEqual(self.request('GET','/',host='other.example')[0],421)
        self.assertEqual(self.request('GET','/',origin='https://other.example')[0],403)
        self.assertEqual(self.request('GET','/api/queue')[0],401)
        self.assertEqual(self.request('POST','/api/upload',{})[0],404)
        self.assertEqual(self.request('POST','/api/say',{'text':'открой блокнот'})[0],400)

    def test_standard_https_alias_stays_inside_preview(self):
        bare=self.host.split(':',1)[0]
        status,body=self.request('GET','/jarvis-dev/_dev/health',host=bare,origin='https://'+bare)
        self.assertEqual(status,200)
        self.assertTrue(json.loads(body)['tv_only'])
        self.assertEqual(self.request('GET','/jarvis-dev/',host=bare,origin='https://'+bare)[0],200)
        self.assertEqual(self.request('GET','/jarvis-dev/api/queue',host=bare,origin='https://'+bare)[0],401)

    def test_separate_pairing_and_existing_executor_path(self):
        status,raw=self.request('POST','/api/pair',{'code':self.gateway.pairing.code,'name':'DEV test'})
        self.assertEqual(status,201);token=json.loads(raw)['token']
        url='https://www.youtube.com/watch?v=5-wdT_xbVDQ'
        status,raw=self.request('POST','/api/say',{'text':'на телевизор '+url},token)
        self.assertEqual(status,200);item=json.loads(raw);self.assertTrue(item['queued'])
        router=UnifiedTaskRouter(TrustedLocalSession.phone_allowlist(('tv-play',)),self.workspace)
        router._tv_play=Mock(return_value={'status':'PASS','speech':'На телевизоре воспроизводится выбранное видео.'})
        executor=PhoneExecutor(self.workspace,router=router);executor.tick()
        status,raw=self.request('GET','/api/queue',token=token)
        done=next(x for x in json.loads(raw)['items'] if x['id']==item['id'])
        self.assertEqual(done['status'],'done')
        router._tv_play.assert_called_once_with(url=url)
        with self.assertRaises(Denied):router.session.confirm({'task':'tv-link'})

    def test_chat_requires_existing_device_auth_and_remains_text_only(self):
        self.assertEqual(self.request('GET','/api/chatgpt/status')[0],401)
        self.assertEqual(self.request('POST','/api/chatgpt/turn',{'input':[{'role':'user','content':'hi'}]})[0],401)
        status,raw=self.request('POST','/api/pair',{'code':self.gateway.pairing.code,'name':'chat test'})
        token=json.loads(raw)['token']
        self.assertEqual(self.request('POST','/api/chatgpt/turn',{'input':[]},token)[0],400)
        self.assertEqual(self.request('POST','/api/chatgpt/connect',{},token)[0],404)
        self.assertEqual(self.request('POST','/api/say',{'text':'открой блокнот'},token)[0],400)

    def test_typed_chat_failures_keep_device_paired_and_do_not_leak(self):
        status,raw=self.request('POST','/api/pair',{'code':self.gateway.pairing.code,'name':'chat recovery'})
        token=json.loads(raw)['token']
        for kind,expected in (('network',503),('rate_limited',429),('auth_required',503),('access_denied',403)):
            with self.subTest(kind=kind):
                self.chat.bridge._token.side_effect=ChatGPTError(kind)
                status,body=self.request('POST','/api/chatgpt/turn',{'input':[{'role':'user','content':'hi'}]},token)
                data=json.loads(body);self.assertEqual(status,expected);self.assertEqual(data['code'],kind)
                self.assertEqual(data['needs_auth'],kind=='auth_required')
                self.assertEqual(self.request('GET','/api/summary',token=token)[0],200)
        self.chat.turn=Mock(side_effect=RuntimeError('private-token-do-not-leak'))
        status,body=self.request('POST','/api/chatgpt/turn',{'input':[{'role':'user','content':'hi'}]},token)
        self.assertEqual(status,503);self.assertNotIn(b'private-token',body);self.assertFalse(json.loads(body)['needs_auth'])
