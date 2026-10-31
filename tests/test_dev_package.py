import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from developer_core.jarvis_local.dev_package import install,rollback,verify_package,EXE,MANIFEST
from developer_core.jarvis_local.dev_desktop import Backend,validate_tv_host,validate_video_url,instance_guard

class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

    def package(self,version):
        folder=self.root/('source-'+version);folder.mkdir();blob=('fake exe '+version).encode();(folder/EXE).write_bytes(blob)
        (folder/MANIFEST).write_text(json.dumps({'version':version,'files':{EXE:hashlib.sha256(blob).hexdigest()}}))
        return folder

    def test_install_upgrade_and_rollback_preserve_user_data_and_previous_version(self):
        first=self.package('1.4.1');second=self.package('1.4.2');target=self.root/'installed'
        result=install(first,target);self.assertFalse(result['autorun_changed'])
        (target/'data').mkdir();(target/'data/settings.json').write_text('private-settings')
        install(second,target);self.assertEqual(json.loads((target/'current.json').read_text())['version'],'1.4.2')
        rollback(target);self.assertEqual(json.loads((target/'current.json').read_text())['version'],'1.4.1')
        self.assertEqual((target/'data/settings.json').read_text(),'private-settings')
        self.assertTrue((target/'versions/1.4.2'/EXE).is_file())

    def test_corruption_or_extra_files_are_refused_before_install(self):
        source=self.package('1.4.2');(source/EXE).write_bytes(b'tampered')
        with self.assertRaises(ValueError):install(source,self.root/'target')
        self.assertFalse((self.root/'target').exists())
        source=self.package('1.4.3');(source/'unexpected.key').write_text('do not copy')
        with self.assertRaises(ValueError):verify_package(source)

    def test_existing_version_is_never_overwritten(self):
        source=self.package('1.4.2');target=self.root/'installed';install(source,target)
        (target/'versions/1.4.2'/EXE).write_bytes(b'tampered')
        with self.assertRaises(ValueError):install(source,target)
        self.assertEqual((target/'versions/1.4.2'/EXE).read_bytes(),b'tampered')

    def test_unsafe_version_and_missing_previous_are_refused(self):
        source=self.package('1.4.2');manifest=json.loads((source/MANIFEST).read_text());manifest['version']='../../elsewhere';(source/MANIFEST).write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):install(source,self.root/'target')
        source=self.package('1.4.4');target=self.root/'installed';install(source,target)
        with self.assertRaises(ValueError):rollback(target)

    def test_shared_ffmpeg_runtime_copies_required_dlls_but_not_private_files(self):
        from scripts.build_dev_package import copy_ffmpeg
        source=self.root/'ffmpeg';source.mkdir()
        for name in ('ffmpeg.exe','ffprobe.exe','avcodec-63.dll','private.bin'):(source/name).write_bytes(b'test')
        target=self.root/'tools';copy_ffmpeg(source/'ffmpeg.exe',target)
        self.assertEqual({p.name for p in target.iterdir()},{'ffmpeg.exe','ffprobe.exe','avcodec-63.dll'})

    def test_two_instances_are_refused_and_lock_releases(self):
        path=self.root/'instance.lock'
        with instance_guard(path):
            with self.assertRaises(RuntimeError):
                with instance_guard(path):pass
        with instance_guard(path):pass

class DesktopBackendTests(unittest.TestCase):
    def test_tv_hosts_and_page_urls_are_bounded(self):
        self.assertEqual(validate_tv_host('192.168.1.20'),'192.168.1.20')
        for host in ('127.0.0.1','8.8.8.8','example.com','0.0.0.0','169.254.169.254'):
            with self.assertRaises(ValueError):validate_tv_host(host)
        self.assertEqual(validate_video_url('https://example.com/video'),'https://example.com/video')
        for url in ('https://https://example.com/v','https://user:pass@example.com/v','http://127.0.0.1/v','file:///secret'):
            with self.assertRaises(Exception):validate_video_url(url)

    def test_desktop_chat_uses_existing_bridge_and_keeps_history_in_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            backend=Backend(temp)
            with patch('developer_core.jarvis_local.chatgpt_plan_engine.dispatch',return_value='[ОТВЕТ GPT через официальное использование подписки ChatGPT] Ответ') as call:
                self.assertTrue(backend.ask('привет')['ok']);backend.ask('продолжи')
            self.assertIn('привет',call.call_args.args[0]['prompt']);self.assertEqual(list(Path(temp).iterdir()),[])
            with patch('developer_core.jarvis_local.chatgpt_plan_engine.dispatch',return_value='[GPT НЕДОСТУПЕН: Нет связи]'):
                result=backend.ask('hi');self.assertFalse(result['ok']);self.assertEqual(result['text'],'Нет связи')

    def test_tv_uses_existing_queue_executor_and_only_explicit_play(self):
        from developer_core.jarvis_local.workspace import Workspace
        with tempfile.TemporaryDirectory() as temp:
            backend=Backend(temp);workspace=Path(temp)/'workspace';workspace.mkdir()
            router=Mock();router.ws=Workspace(workspace);router.dispatch.return_value={'status':'PASS','speech':'Команда передана'}
            backend._build_router=Mock(return_value=router)
            self.assertEqual(list(router.ws.root.glob('*.jsonl')),[])
            result=backend.play('https://example.com/video.mp4')
            self.assertTrue(result['ok']);router.dispatch.assert_called_once_with('tv-play',url='https://example.com/video.mp4')
            self.assertTrue(router.ws.state.joinpath('phone_queue.jsonl').is_file())

    def test_ha_settings_are_validated_read_only_before_encrypted_storage(self):
        with tempfile.TemporaryDirectory() as temp:
            backend=Backend(temp)
            with patch('developer_core.home_assistant.SonyConnector') as connector,patch('developer_core.jarvis_local.station.keys') as keys:
                backend.configure_ha('x'*30);connector.return_value.status.assert_called_once_with();keys.return_value.save.assert_called_once_with('x'*30)
            with patch('developer_core.home_assistant.SonyConnector') as connector,patch('developer_core.jarvis_local.station.keys') as keys:
                connector.return_value.status.side_effect=OSError('never-print-token')
                with self.assertRaises(ValueError) as error:backend.configure_ha('x'*30)
                self.assertNotIn('never-print-token',str(error.exception));keys.return_value.save.assert_not_called()
