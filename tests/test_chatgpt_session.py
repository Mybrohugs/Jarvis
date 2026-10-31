import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import patch
from developer_core.jarvis_local import chatgpt_plan_engine as bridge

class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.account={'client_id':'test-client','ext_agent_host_id':'test-host','scope':'test-scope',
                      'access_token':'test-access','refresh_token':'test-refresh','id_token':'test-id','expires_at':time.time()-1}
        def load():return copy.deepcopy(self.account)
        def save(value):self.account=copy.deepcopy(value)
        for name,value in [('SECRET_FILE',self.root/'credential.dpapi'),('_load_credential',load),('_save_credential',save)]:
            patcher=patch.object(bridge,name,value);patcher.start();self.addCleanup(patcher.stop)

    def test_fresh_token_never_refreshes(self):
        self.account['expires_at']=time.time()+3600
        with patch.object(bridge,'_json_request') as request:self.assertEqual(bridge._token(),'test-access')
        request.assert_not_called()

    def test_rotation_saved_and_reused(self):
        response={'access_token':'replacement-access','refresh_token':'replacement-refresh','expires_in':3600}
        with patch.object(bridge,'_json_request',return_value=response) as request:
            self.assertEqual(bridge._token(),'replacement-access');self.assertEqual(bridge._token(),'replacement-access')
        self.assertEqual(request.call_count,1);self.assertEqual(self.account['refresh_token'],'replacement-refresh')
        self.assertEqual(request.call_args.kwargs['body']['client_id'],'test-client')

    def test_transient_refresh_preserves_valid_access_and_cools_down(self):
        self.account['expires_at']=time.time()+100
        with patch.object(bridge,'_json_request',side_effect=bridge.ChatGPTError('network')) as request:
            self.assertEqual(bridge._token(),'test-access');self.assertEqual(bridge._token(),'test-access')
        self.assertEqual(request.call_count,1);self.assertEqual(self.account['refresh_token'],'test-refresh')

    def test_expired_network_failure_never_erases_credentials(self):
        with patch.object(bridge,'_json_request',side_effect=bridge.ChatGPTError('network')) as request:
            for _ in range(2):
                with self.assertRaises(bridge.ChatGPTError) as error:bridge._token()
                self.assertEqual(error.exception.kind,'network');self.assertFalse(error.exception.needs_auth)
        self.assertEqual(request.call_count,1);self.assertEqual(self.account['refresh_token'],'test-refresh')

    def test_terminal_refresh_keeps_registration_but_stops_reusing_tokens(self):
        with patch.object(bridge,'_json_request',side_effect=bridge.ChatGPTError('auth_required')) as request:
            for _ in range(2):
                with self.assertRaises(bridge.ChatGPTError) as error:bridge._token()
                self.assertTrue(error.exception.needs_auth)
        self.assertEqual(request.call_count,1)
        self.assertEqual(self.account['client_id'],'test-client');self.assertEqual(self.account['ext_agent_host_id'],'test-host')
        self.assertNotIn('refresh_token',self.account);self.assertNotIn('access_token',self.account)

    def test_recovery_after_cooldown_and_process_restart(self):
        self.account['refresh_retry_at']=time.time()-1;self.account['refresh_error_kind']='network'
        with patch.object(bridge,'_json_request',return_value={'access_token':'new','refresh_token':'rotated','expires_in':3600}):
            self.assertEqual(bridge._token(),'new')
        self.assertNotIn('refresh_retry_at',self.account)

    def test_refresh_result_invalid_does_not_replace_old_session(self):
        before=copy.deepcopy(self.account)
        with patch.object(bridge,'_json_request',return_value={'access_token':'new'}):
            with self.assertRaises(bridge.ChatGPTError) as error:bridge._token()
        self.assertEqual(error.exception.kind,'invalid_response');self.assertEqual(self.account,before)

    def test_invalid_client_is_configuration_not_relogin(self):
        with patch.object(bridge,'_json_request',side_effect=bridge.ChatGPTError('configuration')):
            with self.assertRaises(bridge.ChatGPTError) as error:bridge._token()
        self.assertEqual(error.exception.kind,'configuration');self.assertFalse(error.exception.needs_auth)
        self.assertEqual(self.account['refresh_token'],'test-refresh')

    def test_os_lock_released_after_exception(self):
        with self.assertRaises(ValueError):
            with bridge._credential_lock():raise ValueError('test')
        with bridge._credential_lock(timeout=.1):pass

    def test_http_errors_classified_by_code_and_never_expose_body(self):
        cases=[(400,'invalid_grant','auth_required'),(400,'invalid_client','configuration'),
               (401,'subscription_sharing_invalid_user','session_unavailable'),(403,'subscription_sharing_user_not_eligible','access_denied'),
               (429,'subscription_sharing_usage_limit_exceeded','rate_limited'),(503,'subscription_sharing_usage_unavailable','service_unavailable')]
        for status,code,expected in cases:
            with self.subTest(code=code):
                error=urllib.error.HTTPError('https://api.openai.com/private?token=never-print',status,'never-print',{'Retry-After':'25'},
                    io.BytesIO(json.dumps({'error':{'code':code,'message':'never-print'}}).encode()))
                result=bridge.http_error(error)
                self.assertEqual(result.kind,expected);self.assertNotIn('never-print',json.dumps(result.public()))
                self.assertEqual(result.needs_auth,expected=='auth_required')
                if expected=='rate_limited':self.assertEqual(result.retry_after,25)

    def test_desktop_status_checks_real_session_not_just_presence_of_file(self):
        with patch.object(bridge,'_token',side_effect=bridge.ChatGPTError('network')):
            status=bridge.status()
        self.assertIn('Нет связи',status);self.assertNotIn('повторный вход',status)
        with patch.object(bridge,'_token',return_value='fake'),patch.object(bridge,'_model',return_value='account-model'):
            self.assertIn('account-model',bridge.status())

    def test_desktop_unknown_error_never_leaks_details_or_demands_login(self):
        with patch.object(bridge,'_token',side_effect=RuntimeError('never-print')):
            response=bridge.dispatch({'operation':'ask','prompt':'hi'})
        self.assertTrue(response.startswith('[GPT НЕДОСТУПЕН:'));self.assertNotIn('never-print',response);self.assertNotIn('повторный вход',response)

# Actual independent processes use fake credentials, never the user's DPAPI store.
HELPER = r"""
from pathlib import Path
import importlib.util,json,os,sys,time
spec=importlib.util.spec_from_file_location('desktop_or_phone_bridge',sys.argv[1]);b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
root=Path(sys.argv[2]);mode=sys.argv[3];b.SECRET_FILE=root/'fake-credential.json'
b._load_credential=lambda:json.loads(b.SECRET_FILE.read_text())
b._save_credential=lambda value:b._atomic_write(b.SECRET_FILE,json.dumps(value).encode())
def refresh(*args,**kwargs):
    if kwargs['body']['refresh_token']!='initial-refresh':raise RuntimeError('reused rotation')
    with (root/'refresh-count').open('a') as stream:stream.write('refresh\n')
    time.sleep(.25)
    return {'access_token':'replacement-access','refresh_token':'replacement-refresh','expires_in':3600}
b._json_request=refresh
if mode in ('hold','crash'):
    with b._credential_lock():
        (root/'held').write_text('ready')
        if mode=='crash':os._exit(0)
        time.sleep(1)
else:
    (root/('ready-'+mode)).write_text('ready')
    end=time.monotonic()+5
    while not (root/'go').exists():
        if time.monotonic()>end:raise RuntimeError('barrier timeout')
        time.sleep(.01)
    assert b._token()=='replacement-access'
"""
class ProcessSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.script=self.root/'worker.py';self.script.write_text(HELPER,encoding='utf-8')
        self.credential=self.root/'fake-credential.json';self.credential.write_text(json.dumps({'client_id':'test','scope':'test','refresh_token':'initial-refresh','access_token':'expired','expires_at':0}))

    def start(self,mode):
        process=subprocess.Popen([sys.executable,'-B',str(self.script),bridge.__file__,str(self.root),mode],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def cleanup():
            if process.poll() is None:process.kill()
            process.communicate(timeout=5)
        self.addCleanup(cleanup);return process

    def wait_file(self,name):
        end=time.monotonic()+5
        while not (self.root/name).exists():
            if time.monotonic()>end:self.fail('worker barrier timeout')
            time.sleep(.01)

    def test_two_processes_refresh_once_and_share_rotated_result(self):
        first=self.start('desktop');second=self.start('phone');self.wait_file('ready-desktop');self.wait_file('ready-phone');(self.root/'go').write_text('go')
        for process in (first,second):
            output,error=process.communicate(timeout=10);self.assertEqual(process.returncode,0,error)
        self.assertEqual((self.root/'refresh-count').read_text().splitlines(),['refresh'])
        self.assertEqual(json.loads(self.credential.read_text())['refresh_token'],'replacement-refresh')
        restarted=self.start('after-restart');output,error=restarted.communicate(timeout=10)
        self.assertEqual(restarted.returncode,0,error);self.assertEqual((self.root/'refresh-count').read_text().splitlines(),['refresh'])

    def test_lock_timeout_is_bounded_and_requires_no_login(self):
        process=self.start('hold');self.wait_file('held')
        with patch.object(bridge,'SECRET_FILE',self.credential):
            started=time.monotonic()
            with self.assertRaises(bridge.ChatGPTError) as error:
                with bridge._credential_lock(timeout=.1):pass
            self.assertLess(time.monotonic()-started,.8);self.assertEqual(error.exception.kind,'busy');self.assertFalse(error.exception.needs_auth)
        process.communicate(timeout=5)

    def test_lock_is_released_when_owner_process_exits_abruptly(self):
        process=self.start('crash');process.communicate(timeout=5);self.assertEqual(process.returncode,0)
        with patch.object(bridge,'SECRET_FILE',self.credential):
            with bridge._credential_lock(timeout=.3):pass
