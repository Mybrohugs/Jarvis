"""Relocate a frozen bundle and test it with no Python/PATH or saved account context."""
from pathlib import Path
import argparse
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from developer_core.jarvis_local.dev_package import verify_package,VERSION,EXE

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    results=[]
    with tempfile.TemporaryDirectory(prefix='jarvis-package-check-') as temp:
        root=Path(temp);moved=root/'Moved DEV Station';shutil.copytree(args.bundle.resolve(strict=True),moved);verify_package(moved)
        env=dict(os.environ);env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
        env['PATH']=str(Path(os.environ['SystemRoot'])/'System32');env['APPDATA']=str(root/'Roaming');env['LOCALAPPDATA']=str(root/'Local')
        env['JARVIS_DEV_DATA_ROOT']=str(root/'data');env['JARVIS_DEV_INSTALL_ROOT']=str(root/'Installed DEV');env['JARVIS_DEV_SKIP_SHORTCUT']='1'
        def run(mode):
            output=root/(mode[2:]+'.json')
            result=subprocess.run([str(moved/EXE),mode,'--quiet','--output',str(output)],cwd=root,env=env,timeout=60)
            data=json.loads(output.read_text(encoding='utf-8')) if output.exists() else {'missing_report':True}
            print(json.dumps({'mode':mode,'exit':result.returncode,'result':data}),flush=True)
            assert result.returncode==0,(mode,data);results.append({'mode':mode,'result':data});return data
        d=run('--doctor');assert d['frozen'] and not d['python_required_on_user_pc'] and not d['chatgpt_configured'] and d['ffmpeg'] and d['browser']
        run('--smoke-gui');run('--self-test');run('--install')
        installed=root/'Installed DEV';verify_package(installed/'versions'/VERSION)
        test=root/'test-launcher.ps1';test.write_text('param([string]$Launcher)\nfunction Start-Process {param($FilePath,$WorkingDirectory,$WindowStyle) [pscustomobject]@{File=$FilePath;Dir=$WorkingDirectory}|ConvertTo-Json}\n& $Launcher\n',encoding='ascii')
        shell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
        launched=subprocess.run([str(shell),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(test),'-Launcher',str(installed/'Run-JARVIS-DEV.ps1')],capture_output=True,text=True,timeout=15)
        assert launched.returncode==0,launched.stderr
        value=json.loads(launched.stdout);assert Path(value['File']).resolve()==(installed/'versions'/VERSION/EXE).resolve()
        print(json.dumps({'installed_launcher':'PASS','actual_launch_suppressed':True}),flush=True)
        results.append({'installed_launcher':'PASS','actual_launch_suppressed':True})
        proc=subprocess.Popen([str(moved/EXE),'--gateway','--quiet','--output',str(root/'gateway-start.json'),'--phone-host','package.test.ts.net:8443','--tv-host','192.168.1.20','--port','10827'],cwd=root,env=env)
        try:
            deadline=time.monotonic()+25;state=root/'data/phone/status.json'
            while not state.exists() and time.monotonic()<deadline:
                if proc.poll() is not None:raise RuntimeError('Packaged gateway exited: '+(root/'gateway-start.json').read_text())
                time.sleep(.2)
            assert state.exists()
            def request(method,path,body=None,token=None):
                conn=http.client.HTTPConnection('127.0.0.1',10827,timeout=10);headers={'Host':'package.test.ts.net:8443','Content-Type':'application/json'}
                if token:headers['Authorization']='Bearer '+token
                conn.request(method,path,json.dumps(body) if body else None,headers);response=conn.getresponse();data=json.loads(response.read());status=response.status;conn.close();return status,data
            assert request('GET','/_dev/health')[0]==200
            pair=json.loads(state.read_text(encoding='utf-8'));status,paired=request('POST','/api/pair',{'code':pair['pairing_code'],'name':'isolated bundle test'});assert status==201
            status,chat=request('GET','/api/chatgpt/status',token=paired['token']);assert status==200 and chat.get('needs_auth') is True
            result={'packaged_gateway':'PASS','pairing':'PASS','fresh_machine_auth_status':'auth_required','tv_command_sent':False}
            print(json.dumps(result),flush=True);results.append(result)
        finally:
            proc.terminate();proc.wait(timeout=10)
        verify_package(moved)
    args.report.write_text(json.dumps({'version':VERSION,'relocated':True,'python_path_removed':True,'account_context_empty':True,'checks':results},indent=2)+'\n',encoding='utf-8')
    print('Relocated frozen bundle and per-user install: PASS',flush=True)
if __name__=='__main__':main()
