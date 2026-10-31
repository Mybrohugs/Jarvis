import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from developer_core.jarvis_local.tv_media import Relay, RelayError
from developer_core.jarvis_local.session import Denied

class RelayHttpTests(unittest.TestCase):
    def test_missing_or_invalid_hls_is_refused_before_tv_and_stops_ffmpeg(self):
        for status, body in ((404,b'missing'),(200,b'not a playlist')):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                process=Mock();process.poll.return_value=None
                def popen(cmd,**kwargs):
                    out=Path(cmd[-1]).parent
                    (out/'index.m3u8').write_bytes(b'#EXTM3U\n')
                    (out/'s00000.ts').write_bytes(b'ts');(out/'s00001.ts').write_bytes(b'ts')
                    return process
                relay=Relay(temp,'127.0.0.1',ffmpeg='fake-ffmpeg',popen=popen)
                relay._serve=Mock()
                response=Mock(status=status);response.read.return_value=body
                connection=Mock();connection.getresponse.return_value=response
                with patch('http.client.HTTPConnection',return_value=connection):
                    with self.assertRaises(RelayError) as error:
                        relay.start(['neutral.mp4'],{})
                self.assertEqual(error.exception.reason,'http')
                process.terminate.assert_called_once_with()
                connection.close.assert_called_once_with()

    def test_dev_relay_port_is_explicit_and_validated(self):
        self.assertEqual(Relay('unused','127.0.0.1',port=10799).port,10799)
        for port in (True,0,65536,'10799'):
            with self.assertRaises(Denied):Relay('unused','127.0.0.1',port=port)
