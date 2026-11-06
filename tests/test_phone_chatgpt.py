import io
import json
import unittest
from unittest.mock import Mock, patch
from developer_core.jarvis_local.phone_chatgpt import ChatGPT, read_stream, AUTH_ERROR

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
        client=ChatGPT();client.bridge=Mock();client.bridge._token.side_effect=RuntimeError('private-secret')
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
