"""Build an isolated Windows x64 DEV station. Never read/copy runtime credentials."""
import argparse
import datetime
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from developer_core.jarvis_local.dev_package import VERSION,EXE,MANIFEST,verify_package
from developer_core.jarvis_local.tv_phone_dev import FILES

def copy_ffmpeg(source,destination):
    source=Path(source).resolve(strict=True);destination=Path(destination);destination.mkdir(exist_ok=True)
    files=[source.with_name(name) for name in ('ffmpeg.exe','ffprobe.exe')]+list(source.parent.glob('*.dll'))
    for file in files:
        if file.is_symlink():raise RuntimeError('Linked FFmpeg runtime files are not allowed')
        if file.is_file():shutil.copy2(file,destination/file.name)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mobile',type=Path,required=True);parser.add_argument('--browsers',type=Path,required=True)
    parser.add_argument('--ffmpeg',type=Path,required=True)
    args=parser.parse_args()
    if sys.platform!='win32':raise SystemExit('Build the Windows bundle on Windows')
    root=Path(__file__).resolve().parents[1]
    work=root/'.packaging-dev'/('build-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    work.mkdir(parents=True,exist_ok=False)
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--onedir','--windowed','--name','JARVIS-DEV',
        '--distpath',str(work/'dist'),'--workpath',str(work/'work'),'--specpath',str(work),
        '--paths',str(root),'--collect-all','playwright','--collect-submodules','yt_dlp',
        '--collect-all','pyytlounge','--collect-data','certifi','--hidden-import','win32crypt',
        '--hidden-import','win32job','--hidden-import','win32api','--hidden-import','win32com.client',
        '--hidden-import','win32timezone','--collect-data','developer_core.jarvis_local',
        str(root/'packaging/dev_entry.py')]
    with (work/'build.log').open('w',encoding='utf-8') as log:
        result=subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT)
    if result.returncode:raise RuntimeError('PyInstaller failed; see '+str(work/'build.log'))
    bundle=work/'dist/JARVIS-DEV'
    if not (bundle/EXE).is_file():raise RuntimeError('Executable missing')
    copy_ffmpeg(args.ffmpeg,bundle/'tools')
    browser_dirs=list(args.browsers.resolve().glob('chromium_headless_shell-*'))
    if len(browser_dirs)!=1:raise RuntimeError('Expected one matching headless Chromium')
    shutil.copytree(browser_dirs[0],bundle/'browsers'/browser_dirs[0].name)
    mobile=args.mobile.resolve(strict=True)
    for name in FILES:
        source=mobile/name
        if source.is_symlink() or not source.resolve().is_relative_to(mobile):raise RuntimeError('Unsafe mobile asset')
        if source.is_file():
            destination=bundle/'mobile'/name;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
    (bundle/'Install-DEV.cmd').write_text('@echo off\r\nstart "" "%~dp0JARVIS-DEV.exe" --install\r\n',encoding='ascii')
    (bundle/'Run-DEV.cmd').write_text('@echo off\r\nstart "" "%~dp0JARVIS-DEV.exe"\r\n',encoding='ascii')
    (bundle/'Doctor-DEV.cmd').write_text('@echo off\r\nset "JARVIS_DEV_REPORT=%TEMP%\\jarvis-dev-doctor.json"\r\nstart /wait "" "%~dp0JARVIS-DEV.exe" --doctor --output "%JARVIS_DEV_REPORT%"\r\nnotepad.exe "%JARVIS_DEV_REPORT%"\r\n',encoding='ascii')
    (bundle/'Rollback-DEV.cmd').write_text('@echo off\r\nset "JARVIS_DEV_REPORT=%TEMP%\\jarvis-dev-rollback.json"\r\nstart /wait "" "%~dp0JARVIS-DEV.exe" --rollback --output "%JARVIS_DEV_REPORT%"\r\nnotepad.exe "%JARVIS_DEV_REPORT%"\r\n',encoding='ascii')
    shutil.copy2(root/'LICENSE',bundle/'LICENSE')
    licenses=bundle/'licenses';licenses.mkdir()
    for distribution in metadata.distributions():
        label=distribution.metadata.get('Name','dependency').replace('/','_')
        for entry in distribution.files or []:
            if any(part.lower() in ('licenses','license','license.txt','license.md','copying','notice') for part in entry.parts):
                source=Path(distribution.locate_file(entry))
                if source.is_file() and source.stat().st_size<2000000:
                    destination=licenses/label/str(entry).replace('..','_');destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
    python_license=Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.is_file():shutil.copy2(python_license,licenses/'Python-LICENSE.txt')
    for source in args.ffmpeg.resolve().parents[1].glob('*'):
        if source.is_file() and ('license' in source.name.lower() or 'copying' in source.name.lower()):shutil.copy2(source,licenses/('FFmpeg-'+source.name))
    ffmpeg_info=subprocess.run([str(args.ffmpeg.resolve()),'-version'],capture_output=True,text=True,timeout=15)
    if ffmpeg_info.returncode or '--enable-nonfree' in ffmpeg_info.stdout:raise RuntimeError('FFmpeg build is not suitable for this package')
    (licenses/'FFmpeg-build.txt').write_text(ffmpeg_info.stdout,encoding='utf-8')
    (bundle/'THIRD_PARTY.md').write_text('This bundle includes Python, Playwright/Chromium, FFmpeg and Python dependencies. Their license files are in licenses/ and browser directories. FFmpeg source/build: https://www.gyan.dev/ffmpeg/builds/ ; Playwright: https://github.com/microsoft/playwright ; Python: https://www.python.org/ . No account secrets or local models are redistributed.\n',encoding='utf-8')
    (bundle/'README_RU.md').write_text("""# JARVIS DEV Station 1.4.3

Распакуйте весь каталог. Run-DEV.cmd запускает автономный ChatGPT-режим ПК и отправку видео. Python устанавливать не нужно. Install-DEV.cmd помещает проверенную версию в отдельный каталог текущего пользователя; старые версии и данные не удаляются. Doctor-DEV.cmd проверяет зависимости. Rollback-DEV.cmd переключает предыдущую установленную версию, если она есть.

Вход ChatGPT — официальной кнопкой. Уже сохранённый сеанс Windows используется повторно. Ключи Claude/Gemini не требуются. Для ТВ укажите локальный IPv4-адрес во вкладке «Видео на ТВ». В разделе «Дополнительно» можно сохранить токен Home Assistant (проверяется только чтением состояния) и привязать YouTube по коду ТВ; секреты сохраняются только в Windows DPAPI. Видео проходит через существующие очередь/исполнитель/Cast/FFmpeg; закрытые и защищённые сайты не гарантируются.

Старая голосовая HUD-оболочка не включена и не заменяется этим пакетом. Здесь нет постоянного прослушивания, общего управления Windows или автоматической публикации. Закрытие окна завершает станцию; автозапуск Windows не добавляется. Телефонный шлюз можно запускать отдельно с --gateway --phone-host имя.сеть.ts.net:8443 --tv-host 192.168.x.x; он использует отдельные порты 10817/10818 и не меняет Tailscale. Голос телефона/локальный STT требуют соответствующей отдельной службы.

Данные находятся в LOCALAPPDATA/JARVIS-DEV-Station/data, OAuth — в существующем защищённом APPDATA/JarvisChatGPTPlan. Ключи и конфигурация пользователя не входят в ZIP. Production и существующие службы не изменяются.
""",encoding='utf-8')
    manifest={'version':VERSION,'platform':'windows-x64','files':{p.relative_to(bundle).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(bundle.rglob('*')) if p.is_file()}}
    (bundle/MANIFEST).write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8');verify_package(bundle)
    archive=work/('JARVIS-DEV-v'+VERSION+'-windows-x64.zip')
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as zipped:
        for file in sorted(bundle.rglob('*')):
            if file.is_file():zipped.write(file,'JARVIS-DEV/'+file.relative_to(bundle).as_posix())
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    (work/'SHA256SUMS.txt').write_text(digest+'  '+archive.name+'\n',encoding='ascii')
    result={'bundle':str(bundle),'archive':str(archive),'sha256':digest,'files':len(manifest['files'])}
    (root/'.packaging-dev/latest-build.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result),flush=True)
if __name__=='__main__':main()
