"""Prepare an offline Windows Sandbox build/test of the standalone DEV installer."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from developer_core.jarvis_local.dev_package import verify_package,checked_root
COMPILER_SHA256='8a5a6711c3766a6e5fb74f280ba0e3551e719ce94538d901fdbd1f8f9920b3f0'
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--compiler',type=Path,required=True)
    parser.add_argument('--previous-bundle',type=Path)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    bundle=checked_root(args.bundle).resolve(strict=True);manifest=verify_package(bundle)
    definition=root/'packaging/jarvis-dev.iss'
    version=re.search(r'#define AppVersion "([\d.]+)"',definition.read_text(encoding='utf-8-sig')).group(1)
    if manifest['version']!=version:raise ValueError('Installer and payload versions differ')
    compiler=checked_root(args.compiler).resolve(strict=True)
    if hashlib.sha256(compiler.read_bytes()).hexdigest()!=COMPILER_SHA256:raise ValueError('Unexpected official compiler hash')
    work=root/'.packaging-dev'/('installer-build-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    inputs=work/'input';inputs.mkdir(parents=True,exist_ok=False);reports=work/'reports';reports.mkdir()
    shutil.copy2(compiler,inputs/'compiler.exe');shutil.copy2(definition,inputs/'jarvis-dev.iss')
    shutil.copy2(root/'scripts/check_windows_installer_sandbox.ps1',inputs/'check.ps1')
    config=ET.Element('Configuration')
    for name in ('vGPU','Networking','AudioInput','VideoInput','ClipboardRedirection','PrinterRedirection'):ET.SubElement(config,name).text='Disable'
    ET.SubElement(config,'MemoryInMB').text='6144';folders=ET.SubElement(config,'MappedFolders')
    maps=[(bundle,r'C:\JARVIS-Payload',True),(inputs,r'C:\JARVIS-Build',True),(reports,r'C:\JARVIS-Test',False)]
    if args.previous_bundle:
        previous=checked_root(args.previous_bundle).resolve(strict=True);old=verify_package(previous)
        if old['version']!='1.4.2':raise ValueError('Expected verified v1.4.2 for upgrade regression')
        maps.append((previous,r'C:\JARVIS-Previous',True))
    for source,destination,readonly in maps:
        folder=ET.SubElement(folders,'MappedFolder');ET.SubElement(folder,'HostFolder').text=str(source);ET.SubElement(folder,'SandboxFolder').text=destination;ET.SubElement(folder,'ReadOnly').text=str(readonly).lower()
    ET.SubElement(ET.SubElement(config,'LogonCommand'),'Command').text=r'powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\JARVIS-Build\check.ps1'
    configuration=work/'build.wsb';ET.ElementTree(config).write(configuration,encoding='utf-8',xml_declaration=True)
    result={'version':version,'configuration':str(configuration),'reports':str(reports),'installer':str(reports/'output'/('JARVIS-Setup-v'+version+'-windows-x64.exe')),'compiler_sha256':COMPILER_SHA256,'definition_sha256':hashlib.sha256(definition.read_bytes()).hexdigest(),'check_sha256':hashlib.sha256((inputs/'check.ps1').read_bytes()).hexdigest()}
    (root/'.packaging-dev/latest-installer-build.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result),flush=True)
if __name__=='__main__':main()
