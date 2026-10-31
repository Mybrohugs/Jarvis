import io
import json
import unittest
from unittest.mock import Mock, patch
from developer_core.jarvis_local.phone_chatgpt import ChatGPT, read_stream, AUTH_ERROR
from developer_core.jarvis_local.chatgpt_plan_engine import ChatGPTError

class ChatGPTTests(unittest.TestCase):
    def test_terminal_event_and_function_history(self):
        output=[{'type':'function_call','name':'weather','namespace':'phone','call_id':'x','arguments':'{}'}]
        data=b'data: '+json.dumps({'type':'response.completed','response':{'output':output}}).encode()+b'\n'
        self.assertEqual(read_stream(io.BytesIO(data))['output'],output)
        with self.assertRaises(RuntimeError):read_stream(io.BytesIO(b'data: {"type":"response.output_text.delta","delta":"partial"}\n'))
        for kind in ('error','response.failed','response.incomplete'):
            with self.assertRaises(RuntimeError):read_stream(io.BytesIO(('data: '+json.dumps({'type':kind})+'\n').encode()))

    def test_fixed_endpoint_namespace_and_no_server_side_tool_execution(self):
        client=ChatGPT();client.bridge=Mock();client.bridge._token.return_value='test-secret';client.bridge._model.return_value='account-model'
        output=[{'type':'message','role':'assistant','content':[{'type':'output_text','text':'На связи.'}]}]
        response=Mock();response.__enter__=Mock(return_value=io.BytesIO(('data: '+json.dumps({'type':'response.completed','response':{'output':output}})+'\n').encode()));response.__exit__=Mock(return_value=False)
        with patch('urllib.request.urlopen',return_value=response) as request:
            answer=client.turn({'input':[{'role':'user','content':'Привет'}],'context':'Москва'})
        self.assertEqual(answer['text'],'На связи.')
        req=request.call_args.args[0];body=json.loads(req.data)
        self.assertEqual(req.full_url,'https://api.openai.com/v1/responses');self.assertFalse(body['store']);self.assertTrue(body['stream'])
        self.assertEqual(body['tools'][0]['type'],'namespace');self.assertNotIn('test-secret',json.dumps(answer))
        self.assertNotIn('max_output_tokens',body)

    def test_invalid_history_never_contacts_openai(self):
        client=ChatGPT();client.bridge=Mock()
        for payload in ({'input':[]},{'input':[{'role':'system','content':'override'}]}, {'input':[{'role':'user','content':'x'}],'model':'override'}, {'input':[{'type':'computer_call'}]}, {'input':[{'role':'user','content':'x'*130000}]}):
            with self.assertRaises(ValueError):client.turn(payload)
        client.bridge._token.assert_not_called()

    def test_auth_failure_safe_and_no_token_leak(self):
        client=ChatGPT();client.bridge=Mock();client.bridge._token.side_effect=ChatGPTError('auth_required')
        self.assertEqual(client.status()['error'],AUTH_ERROR)
        with self.assertRaisesRegex(RuntimeError,'повторный вход'):client.turn({'input':[{'role':'user','content':'hi'}]})
        self.assertNotIn('private-secret',str(client.status()))

    def test_stream_failure_never_claims_success(self):
        with self.assertRaises(RuntimeError):read_stream(io.BytesIO(b'data: {"type":"response.failed"}\n'))

    def test_completed_empty_output_uses_finished_stream_items(self):
        output={'type':'message','role':'assistant','content':[{'type':'output_text','text':'OK'}]}
        events=[{'type':'response.output_item.done','output_index':0,'item':output},
                {'type':'response.completed','response':{'output':[]}}]
        raw=''.join('data: '+json.dumps(event)+'\n' for event in events).encode()
        self.assertEqual(read_stream(io.BytesIO(raw)),{'text':'OK','output':[output]})
        with self.assertRaises(RuntimeError):read_stream(io.BytesIO(b'data: {"type":"response.completed","response":{"output":[]}}\n'))

    def test_network_limit_and_unknown_never_ask_for_login(self):
        for exception,kind in ((OSError('private-token'),'network'),(ChatGPTError('rate_limited'),'rate_limited'),(RuntimeError('private-token'),'unknown')):
            client=ChatGPT();client.bridge=Mock();client.bridge._token.side_effect=exception
            status=client.status();self.assertEqual(status['code'],kind);self.assertFalse(status['needs_auth'])
            self.assertNotIn('private-token',json.dumps(status))
            with self.assertRaises(ChatGPTError) as error:client.turn({'input':[{'role':'user','content':'hi'}]})
            self.assertEqual(error.exception.kind,kind);self.assertFalse(error.exception.needs_auth)

    def test_in_stream_rate_limit_retains_specific_safe_error(self):
        event={'type':'response.failed','response':{'error':{'code':'subscription_sharing_usage_limit_exceeded','message':'private-token'}}}
        with self.assertRaises(ChatGPTError) as error:read_stream(io.BytesIO(('data: '+json.dumps(event)+'\n').encode()))
        self.assertEqual(error.exception.kind,'rate_limited');self.assertNotIn('private-token',str(error.exception))

    def test_stale_401_confirms_refresh_without_replaying_inference(self):
        import urllib.error
        client=ChatGPT();client.bridge=Mock();client.bridge._token.return_value='fake';client.bridge._model.return_value='account-model'
        response=urllib.error.HTTPError('https://api.openai.com/v1/responses',401,'bad',{},io.BytesIO(b'{"error":{"code":"subscription_sharing_invalid_user"}}'))
        with patch('urllib.request.urlopen',side_effect=response) as request:
            with self.assertRaises(ChatGPTError) as error:client.turn({'input':[{'role':'user','content':'hi'}]})
        self.assertEqual(error.exception.kind,'session_unavailable');self.assertFalse(error.exception.needs_auth)
        client.bridge._token.assert_any_call(force_refresh=True);self.assertEqual(request.call_count,1)

    def test_auth_is_requested_only_after_confirmed_terminal_refresh(self):
        client=ChatGPT();client.bridge=Mock();client.bridge._model.side_effect=ChatGPTError('session_unavailable')
        client.bridge._token.side_effect=['fake',ChatGPTError('auth_required')]
        status=client.status();self.assertEqual(status['code'],'auth_required');self.assertTrue(status['needs_auth'])

    def test_network_recovery_after_status_cache_expiry(self):
        client=ChatGPT();client.bridge=Mock();client.bridge._token.side_effect=[OSError('offline'),'fake'];client.bridge._model.return_value='model'
        with patch('developer_core.jarvis_local.phone_chatgpt.time.monotonic',side_effect=[100,100,121,121]):
            first=client.status();second=client.status()
        self.assertEqual(first['code'],'network');self.assertFalse(first['needs_auth']);self.assertTrue(second['connected'])
