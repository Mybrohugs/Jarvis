"""Versioned per-user DEV deployment. No production paths, autorun or credential copies."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil

VERSION = '1.4.3'
EXE = 'JARVIS-DEV.exe'
MANIFEST = 'package-manifest.json'

def checked_root(path):
    path = Path(path).expanduser().absolute()
    if path == Path(path.anchor) or path == Path.home().absolute():
        raise ValueError('A dedicated DEV installation directory is required')
    for item in (path, *path.parents):
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise ValueError('Links and junctions are not allowed in the deployment path')
    return path

def install_root():
    return checked_root(Path(os.environ.get('JARVIS_DEV_INSTALL_ROOT') or
        Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'JARVIS-DEV-Station'))

def atomic_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp-'+secrets.token_hex(8))
    try:
        with temp.open('x',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)

def valid_version(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d+\.\d+\.\d+',value):
        raise ValueError('Invalid DEV version')
    return value

def verify_package(folder):
    folder=checked_root(folder).resolve(strict=True)
    manifest=json.loads((folder/MANIFEST).read_text(encoding='utf-8'))
    valid_version(manifest.get('version'))
    files=manifest.get('files')
    if not isinstance(files,dict) or EXE not in files:raise ValueError('Invalid package manifest')
    actual={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if actual != set(files)|{MANIFEST}:raise ValueError('Unexpected or missing package files')
    for name,digest in files.items():
        parts=Path(name).parts
        if not parts or Path(name).is_absolute() or any(part in ('.','..') or ':' in part for part in parts):
            raise ValueError('Unsafe package path')
        item=folder/name
        if item.is_symlink() or not item.resolve(strict=True).is_relative_to(folder):raise ValueError('Unsafe package file')
        if any(parent.is_symlink() or getattr(parent,'is_junction',lambda:False)() for parent in item.parents if parent!=folder.parent):
            raise ValueError('Unsafe package directory')
        if not isinstance(digest,str) or hashlib.sha256(item.read_bytes()).hexdigest()!=digest:
            raise ValueError('Package checksum mismatch')
    return manifest

RUNNER = r"""$ErrorActionPreference='Stop'
$root=Split-Path -Parent $MyInvocation.MyCommand.Path
$config=Get-Content -LiteralPath (Join-Path $root 'current.json') -Raw -Encoding utf8 | ConvertFrom-Json
if($config.version -notmatch '^\d+\.\d+\.\d+$'){throw 'Invalid DEV version'}
$folder=Join-Path $root ('versions\'+$config.version)
$exe=Join-Path $folder 'JARVIS-DEV.exe'
if(-not (Test-Path -LiteralPath $exe -PathType Leaf)){throw 'DEV executable is missing'}
Start-Process -FilePath $exe -WorkingDirectory $folder -WindowStyle Hidden
"""

def install(source, target=None, create_shortcut=False):
    source=checked_root(source).resolve(strict=True);manifest=verify_package(source)
    target=checked_root(target or install_root());target.mkdir(parents=True,exist_ok=True)
    version=valid_version(manifest['version']);destination=checked_root(target/'versions'/version)
    if destination.exists():
        if verify_package(destination)!=manifest:raise ValueError('This version already exists with different content')
    else:
        # Never overwrite an existing installation. Failed staging is left for inspection.
        stage=checked_root(target/'versions'/('staging-'+secrets.token_hex(6)))
        shutil.copytree(source,stage);verify_package(stage);stage.rename(destination)
    current_path=target/'current.json'
    old=json.loads(current_path.read_text(encoding='utf-8')) if current_path.exists() else {}
    if old.get('version')!=version:
        previous=valid_version(old['version']) if old.get('version') else None
        atomic_json(current_path,{'version':version,'previous':previous})
    (target/'Run-JARVIS-DEV.ps1').write_text(RUNNER,encoding='ascii')
    (target/'Run-JARVIS-DEV.cmd').write_text('@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Run-JARVIS-DEV.ps1"\r\n',encoding='ascii')
    shortcut=False
    if create_shortcut and os.name=='nt':
        import win32com.client
        shell=win32com.client.Dispatch('WScript.Shell')
        programs=Path(shell.SpecialFolders('Programs'))/'JARVIS DEV Station'
        programs.mkdir(exist_ok=True)
        path=programs/'JARVIS DEV Station.lnk'
        link=shell.CreateShortcut(str(path))
        launcher=str(target/'Run-JARVIS-DEV.ps1')
        if not path.exists() or launcher in link.Arguments:
            link.TargetPath=str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe')
            link.Arguments='-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File '+chr(34)+launcher+chr(34)
            link.WorkingDirectory=str(target)
            link.IconLocation=str(destination/EXE)+',0'
            link.Save();shortcut=True
    return {'installed':True,'version':version,'previous':old.get('version') if old.get('version')!=version else old.get('previous'),'autorun_changed':False,'start_menu_shortcut':shortcut}

def rollback(target=None):
    target=checked_root(target or install_root());path=target/'current.json'
    current=json.loads(path.read_text(encoding='utf-8'))
    if not current.get('previous'):raise ValueError('Предыдущая DEV-версия ещё не установлена.')
    previous=valid_version(current.get('previous'))
    verify_package(target/'versions'/previous)
    atomic_json(path,{'version':previous,'previous':valid_version(current['version'])})
    return {'rolled_back':True,'version':previous,'data_preserved':True}
