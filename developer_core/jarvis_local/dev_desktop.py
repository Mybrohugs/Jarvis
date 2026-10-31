"""Standalone DEV station: official ChatGPT text and the existing video queue.
The legacy Gemini Live HUD is a separate application and is never replaced here.
"""
import argparse
import contextlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import queue
import socket
import sys
import tempfile
import threading
import urllib.parse
from .dev_package import VERSION, EXE, atomic_json, install, install_root, rollback

SUCCESS_PREFIX = '[ОТВЕТ GPT через официальное использование подписки ChatGPT] '

class AlreadyRunning(RuntimeError):
    pass

def bundle_root():
    return Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]

def data_root():
    return Path(os.environ.get('JARVIS_DEV_DATA_ROOT') or Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'JARVIS-DEV-Station' / 'data')

def configure_runtime():
    root=bundle_root();state=data_root()
    os.environ['JARVIS_DEV_STATE_DIR']=str(state/'integrations')
    if (root/'browsers').is_dir():os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(root/'browsers')
    if (root/'tools').is_dir():os.environ['PATH']=str(root/'tools')+os.pathsep+os.environ.get('PATH','')
    for name in ('stdout','stderr'):
        if getattr(sys,name) is None:setattr(sys,name,open(os.devnull,'w',encoding='utf-8'))
    return state

def validate_tv_host(value):
    try:address=ipaddress.IPv4Address(value.strip())
    except (ValueError,AttributeError):raise ValueError('Введите локальный IPv4-адрес телевизора.') from None
    if not any(address in ipaddress.IPv4Network(network) for network in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')):
        raise ValueError('Нужен адрес телевизора в домашней сети.')
    return str(address)

def validate_video_url(value):
    from .tv_media import check_url
    value=str(value).strip()
    parsed=urllib.parse.urlsplit(value)
    if value.startswith(('https://https://','http://http://')) or parsed.username or parsed.password or len(value)>450 or any(c.isspace() for c in value):
        raise ValueError('Вставьте полную ссылку на видео без пароля и повторного https://.')
    return check_url(value)

@contextlib.contextmanager
def instance_guard(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    stream=path.open('a+b');stream.seek(0,2)
    if not stream.tell():stream.write(b'0');stream.flush()
    stream.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        stream.close();raise AlreadyRunning('DEV-станция уже запущена. Откройте существующее окно.') from None
    try:yield
    finally:stream.close()  # OS releases the instance lock, including on abnormal exit

def own_child_processes():
    """Own only our FFmpeg and headless browser driver, never the user's browser."""
    if os.name!='nt':return False
    import subprocess
    import asyncio.windows_utils as windows_utils
    import win32job
    handle=win32job.CreateJobObject(None,'Local\\JARVIS_DEV_TOOLS_'+str(os.getpid()))
    limits=win32job.QueryInformationJobObject(handle,win32job.JobObjectExtendedLimitInformation)
    limits['BasicLimitInformation']['LimitFlags']|=win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(handle,win32job.JobObjectExtendedLimitInformation,limits)
    raw=handle.Detach()  # the OS closes this raw handle at station exit
    def owned_class(original):
        class OwnedPopen(original):
            def __init__(self,args,*pos,**kwargs):
                name=Path(args[0] if isinstance(args,(list,tuple)) else '').name.lower()
                owned=name in ('node.exe','ffmpeg.exe','chrome-headless-shell.exe')
                if owned:kwargs['creationflags']=kwargs.get('creationflags',0)|subprocess.CREATE_NO_WINDOW
                super().__init__(args,*pos,**kwargs)
                if owned:
                    try:win32job.AssignProcessToJobObject(raw,int(self._handle))
                    except Exception:
                        if self.poll() is not None:return
                        self.terminate();self.wait(timeout=5)
                        raise RuntimeError('Could not own the DEV helper process') from None
        return OwnedPopen
    subprocess.Popen=owned_class(subprocess.Popen)
    windows_utils.Popen=owned_class(windows_utils.Popen)
    return True

class Backend:
    def __init__(self,root=None):
        self.root=Path(root or data_root());self.root.mkdir(parents=True,exist_ok=True)
        self.settings_path=self.root/'settings.json';self.history=[];self.router=None;self.closed=False
        try:self.settings=json.loads(self.settings_path.read_text(encoding='utf-8'))
        except (OSError,ValueError):self.settings={}
        if not isinstance(self.settings,dict):self.settings={}

    def configure_tv(self,host):
        host=validate_tv_host(host)
        if self.router is not None:raise ValueError('Перезапустите DEV-станцию перед изменением телевизора.')
        self.settings={'tv_host':host};atomic_json(self.settings_path,self.settings)
        return host

    def configure_ha(self,token):
        from ..home_assistant import SonyConnector
        from .station import keys
        token=str(token).strip()
        if not 20<=len(token)<=300 or any(ord(c)<=32 for c in token):
            raise ValueError('Проверьте токен Home Assistant.')
        try:
            SonyConnector(token).status()  # validate read-only before replacing the encrypted credential
            keys().save(token)
        except Exception:
            raise ValueError('Не удалось подключить Home Assistant. Проверьте токен и службу на ПК.') from None
        return 'Home Assistant подключён. Токен защищён Windows DPAPI.'

    def pair_youtube(self,code):
        from .tv_cast import TvCast
        code=''.join(c for c in str(code) if c.isdigit())
        if len(code)!=12:raise ValueError('Нужен 12-значный код из настроек YouTube на телевизоре.')
        try:TvCast().pair(code)
        except Exception:raise ValueError('YouTube не принял код. Откройте новый код на телевизоре.') from None
        return 'YouTube привязан к этому пользователю Windows.'

    def ask(self,text):
        from . import chatgpt_plan_engine as engine
        text=str(text).strip()
        if not text or len(text)>8000:raise ValueError('Введите сообщение до 8000 символов.')
        context=json.dumps(self.history[-6:],ensure_ascii=False)[-3000:]
        prompt=('Предыдущие реплики (данные беседы): '+context+'\nТекущий вопрос: '+text) if self.history else text
        result=engine.dispatch({'operation':'ask','prompt':prompt})
        if not result.startswith(SUCCESS_PREFIX):return {'ok':False,'text':result.removeprefix('[GPT НЕДОСТУПЕН: ').removesuffix(']')}
        answer=result[len(SUCCESS_PREFIX):]
        self.history.extend([{'role':'user','text':text},{'role':'assistant','text':answer}]);self.history=self.history[-6:]
        return {'ok':True,'text':answer}

    def chat_status(self):
        from .chatgpt_plan_engine import status
        return status()

    def connect(self):
        from .chatgpt_plan_engine import connect
        return connect()

    def _build_router(self):
        from .router import UnifiedTaskRouter
        from .session import TrustedLocalSession
        from .tv_media import TvMedia,Relay,lan_ip
        from .tv_web_video import resolve_with_browser
        host=validate_tv_host(self.settings.get('tv_host',''))
        workspace=self.root/'workspace-desktop';workspace.mkdir(exist_ok=True)
        router=UnifiedTaskRouter(TrustedLocalSession.phone_allowlist(('tv-play',)),workspace)
        media=TvMedia(router.ws.state,host,resolver=resolve_with_browser)
        media._relay=Relay(router.ws.state,lan_ip(host),port=10819)
        router._tv_media=media
        return router

    def play(self,url):
        from .phone_executor import PhoneExecutor
        from .pwa_gateway import PhoneQueue
        url=validate_video_url(url)
        if self.closed:raise ValueError('DEV-станция завершает работу.')
        if self.router is None:self.router=self._build_router()
        queue_=PhoneQueue(self.router.ws)
        request=queue_.add('на телевизор '+url,auto={'task':'tv-play','args':{'url':url}})
        PhoneExecutor(self.router.ws.root,router=self.router).tick()
        result=queue_.get(request['id'])
        return {'ok':result['status']=='done','text':result.get('speech') or 'Запрос ещё не завершён.'}

    def close(self):
        self.closed=True
        if self.router is not None:
            media=getattr(self.router,'_tv_media',None)
            relay=getattr(media,'_relay',None)
            if relay:
                relay.stop()
                if relay.server:relay.server.shutdown();relay.server.server_close()

def doctor(online=False):
    from . import chatgpt_plan_engine as engine
    from .tv_media import find_ffmpeg
    try:ffmpeg=bool(Path(find_ffmpeg()).is_file())
    except Exception:ffmpeg=False
    browser=bool(list((bundle_root()/'browsers').glob('chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe')))
    try:
        session=engine._load_credential()
        configured=bool(session and session.get('access_token') and session.get('refresh_token'))
        readable=True
    except Exception:configured=False;readable=False
    result={'version':VERSION,'frozen':bool(getattr(sys,'frozen',False)),'python_required_on_user_pc':not bool(getattr(sys,'frozen',False)),
            'chatgpt_configured':configured,'credential_storage_readable':readable,'ffmpeg':ffmpeg,'browser':browser,
            'legacy_voice_included':False,'production_changed':False}
    if online:result['chatgpt_status']=engine.status()
    return result

class App:
    def __init__(self,root,backend,auto_status=True):
        import tkinter as tk
        from tkinter import ttk,scrolledtext
        self.root=root;self.backend=backend;self.events=queue.Queue();self.busy=False;self.closed=False
        root.title('JARVIS DEV — ChatGPT');root.geometry('900x650');root.minsize(680,500);root.configure(bg='#08111c')
        style=ttk.Style(root);style.theme_use('clam');style.configure('.',background='#08111c',foreground='#d3eff8',fieldbackground='#132234')
        style.configure('TButton',padding=8);style.map('TButton',background=[('active','#20465b')])
        ttk.Label(root,text='J.A.R.V.I.S.  ·  DEV '+VERSION,font=('Segoe UI',19,'bold')).pack(anchor='w',padx=18,pady=(16,5))
        self.state=tk.StringVar(value='ChatGPT на вашем ПК · без ключей Claude/Gemini')
        ttk.Label(root,textvariable=self.state,wraplength=850).pack(anchor='w',padx=18,pady=5)
        tabs=ttk.Notebook(root);tabs.pack(fill='both',expand=True,padx=16,pady=10)
        chat=ttk.Frame(tabs);tv=ttk.Frame(tabs);health=ttk.Frame(tabs)
        tabs.add(chat,text='Чат');tabs.add(tv,text='Видео на ТВ');tabs.add(health,text='Состояние')
        self.reply=scrolledtext.ScrolledText(chat,wrap='word',font=('Segoe UI',12),bg='#0d1b2a',fg='#d3eff8',insertbackground='white',state='disabled')
        self.reply.pack(fill='both',expand=True,padx=10,pady=10)
        row=ttk.Frame(chat);row.pack(fill='x',padx=10,pady=(0,10))
        self.input=ttk.Entry(row,font=('Segoe UI',12));self.input.pack(side='left',fill='x',expand=True);self.input.bind('<Return>',lambda e:self.send())
        self.send_button=ttk.Button(row,text='Отправить',command=self.send);self.send_button.pack(side='left',padx=(8,0))
        ttk.Label(tv,text='Отправка через существующую очередь ПК. Телевизор должен быть в домашней сети.',wraplength=780).pack(anchor='w',padx=15,pady=15)
        ttk.Label(tv,text='IPv4-адрес телевизора').pack(anchor='w',padx=15)
        self.tv_host=ttk.Entry(tv);self.tv_host.insert(0,backend.settings.get('tv_host',''));self.tv_host.pack(fill='x',padx=15,pady=6)
        ttk.Label(tv,text='Ссылка на видео').pack(anchor='w',padx=15,pady=(12,0))
        self.video=ttk.Entry(tv);self.video.pack(fill='x',padx=15,pady=6)
        self.tv_button=ttk.Button(tv,text='На ТВ',command=self.play);self.tv_button.pack(anchor='w',padx=15,pady=12)
        self.tv_message=tk.StringVar(value='Видео отправляется только по кнопке «На ТВ».')
        ttk.Label(tv,textvariable=self.tv_message,wraplength=780).pack(anchor='w',padx=15,pady=10)
        options=ttk.LabelFrame(tv,text='Дополнительно — если нужно включать ТВ или привязать YouTube')
        options.pack(fill='x',padx=15,pady=8)
        ha=ttk.Frame(options);ha.pack(fill='x',padx=8,pady=7)
        ttk.Label(ha,text='Токен Home Assistant').pack(side='left')
        self.ha_key=ttk.Entry(ha,show='*');self.ha_key.pack(side='left',fill='x',expand=True,padx=8)
        self.ha_button=ttk.Button(ha,text='Подключить',command=self.save_ha);self.ha_button.pack(side='left')
        yt=ttk.Frame(options);yt.pack(fill='x',padx=8,pady=7)
        ttk.Label(yt,text='Код YouTube с ТВ').pack(side='left')
        self.yt_code=ttk.Entry(yt);self.yt_code.pack(side='left',fill='x',expand=True,padx=8)
        self.yt_button=ttk.Button(yt,text='Привязать',command=self.link_youtube);self.yt_button.pack(side='left')
        self.health=scrolledtext.ScrolledText(health,wrap='word',font=('Segoe UI',11),bg='#0d1b2a',fg='#d3eff8',state='disabled');self.health.pack(fill='both',expand=True,padx=10,pady=10)
        footer=ttk.Frame(root);footer.pack(fill='x',padx=16,pady=(0,15))
        self.connect_button=ttk.Button(footer,text='Войти в ChatGPT',command=lambda:self.work('connect',backend.connect));self.connect_button.pack(side='left')
        self.check_button=ttk.Button(footer,text='Проверить подключение',command=self.refresh);self.check_button.pack(side='left',padx=8)
        ttk.Label(footer,text='Закрытие окна завершает эту DEV-станцию.').pack(side='right')
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(80,self.poll)
        if auto_status:root.after(200,self.refresh)

    def append(self,text):
        self.reply.configure(state='normal');self.reply.insert('end',text+'\n\n');self.reply.see('end');self.reply.configure(state='disabled')

    def work(self,kind,callback):
        if self.busy or self.closed:return
        self.busy=True;self.state.set('Выполняю запрос…')
        for button in (self.send_button,self.tv_button,self.connect_button,self.check_button,self.ha_button,self.yt_button):button.configure(state='disabled')
        def run():
            try:self.events.put((kind,callback()))
            except ValueError as exc:self.events.put(('error',str(exc)))
            except Exception as exc:
                from .chatgpt_plan_engine import classify_error
                self.events.put(('error',str(classify_error(exc))))
        threading.Thread(target=run,daemon=True).start()

    def send(self):
        if self.busy:return
        text=self.input.get().strip()
        if text:self.input.delete(0,'end');self.append('Вы: '+text);self.work('answer',lambda:self.backend.ask(text))

    def play(self):
        if self.busy:return
        try:
            host=self.tv_host.get().strip()
            if self.backend.settings.get('tv_host')!=host:self.backend.configure_tv(host)
            url=validate_video_url(self.video.get())
        except Exception as exc:self.tv_message.set(str(exc));return
        self.tv_message.set('Подготавливаю видео…');self.work('video',lambda:self.backend.play(url))

    def save_ha(self):
        if self.busy:return
        token=self.ha_key.get();self.ha_key.delete(0,'end');self.work('ha',lambda:self.backend.configure_ha(token))

    def link_youtube(self):
        if self.busy:return
        code=self.yt_code.get();self.yt_code.delete(0,'end');self.work('pair',lambda:self.backend.pair_youtube(code))

    def refresh(self):
        self.work('status',lambda:{'message':self.backend.chat_status(),'checks':doctor()})

    def poll(self):
        if self.closed:return
        try:
            kind,value=self.events.get_nowait();self.busy=False
            for button in (self.send_button,self.tv_button,self.connect_button,self.check_button,self.ha_button,self.yt_button):button.configure(state='normal')
            if kind=='answer':self.append('JARVIS: '+value['text']);self.state.set('Готов к сообщению.' if value['ok'] else value['text'])
            elif kind=='video':self.tv_message.set(value['text']);self.state.set('Запрос ТВ завершён.')
            elif kind=='status':
                self.state.set(value['message']);checks=value['checks']
                lines=[value['message'],'','FFmpeg: '+('готов' if checks['ffmpeg'] else 'не найден'),'Браузер видео: '+('готов' if checks['browser'] else 'не найден'),'Голосовая оболочка: подключается отдельно.','Личные ключи не входят в пакет.']
                self.health.configure(state='normal');self.health.delete('1.0','end');self.health.insert('end','\n'.join(lines));self.health.configure(state='disabled')
            elif kind in ('ha','pair'):self.tv_message.set(value);self.state.set('Настройка телевизора сохранена.')
            elif kind=='connect':self.state.set(value)
            else:self.state.set(value);self.append(value)
        except queue.Empty:pass
        self.root.after(80,self.poll)

    def close(self):
        self.closed=True;self.backend.close();self.root.destroy()

def emit(value,output=None):
    if output:atomic_json(Path(output),value)
    elif sys.stdout:print(json.dumps(value,ensure_ascii=False))

def main():
    state=configure_runtime()
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--doctor',action='store_true');modes.add_argument('--smoke-gui',action='store_true')
    modes.add_argument('--self-test',action='store_true');modes.add_argument('--ask')
    modes.add_argument('--install',action='store_true');modes.add_argument('--rollback',action='store_true');modes.add_argument('--gateway',action='store_true')
    parser.add_argument('--online',action='store_true');parser.add_argument('--quiet',action='store_true');parser.add_argument('--output',type=Path)
    parser.add_argument('--phone-host');parser.add_argument('--tv-host');parser.add_argument('--port',type=int,default=10817)
    args=parser.parse_args()
    try:
        if args.doctor:emit(doctor(args.online),args.output);return
        if args.install:
            result=install(bundle_root(),create_shortcut=os.environ.get('JARVIS_DEV_SKIP_SHORTCUT')!='1');emit(result,args.output)
            if not args.quiet:
                import tkinter.messagebox
                tkinter.messagebox.showinfo('JARVIS DEV','Установка завершена. Запуск: '+str(install_root()/'Run-JARVIS-DEV.cmd'))
            return
        if args.rollback:emit(rollback(),args.output);return
        if args.self_test:
            own_child_processes()
            from playwright.sync_api import sync_playwright
            with sync_playwright() as play:
                browser=play.chromium.launch(headless=True);page=browser.new_page();page.set_content('<title>JARVIS DEV test</title><p>OK</p>');assert page.title()=='JARVIS DEV test';browser.close()
            from .tv_media import find_ffmpeg
            import subprocess
            assert subprocess.run([find_ffmpeg(),'-version'],capture_output=True,timeout=10).returncode==0
            result=doctor();assert result['ffmpeg'] and result['browser'];emit({'self_test':'PASS','browser':'PASS','ffmpeg':'PASS','network_used':False},args.output);return
        if args.gateway:
            if not args.phone_host:raise ValueError('Укажите приватное имя ПК: --phone-host имя.сеть.ts.net:8443')
            host=validate_tv_host(args.tv_host or Backend(state).settings.get('tv_host',''))
            own_child_processes()
            from . import tv_phone_dev
            sys.argv=[sys.argv[0],'--mobile',str(bundle_root()/'mobile'),'--host',args.phone_host,
                      '--port',str(args.port),'--state-root',str(state/'phone'),'--tv-host',host,'--relay-port','10818']
            tv_phone_dev.main();return
        if args.ask:
            result=Backend(state).ask(args.ask);emit(result,args.output)
            if not result['ok']:raise SystemExit(2)
            return
        with instance_guard(state/'desktop-instance.lock'):
            own_child_processes()
            import tkinter as tk
            root=tk.Tk();backend=Backend(state);app=App(root,backend,auto_status=not args.smoke_gui)
            if args.smoke_gui:
                root.withdraw();root.update_idletasks();app.close();emit({'gui_smoke':'PASS'},args.output);return
            root.mainloop()
    except Exception as exc:
        # Never emit a raw provider exception, URL, account identity or credential.
        from .chatgpt_plan_engine import classify_error
        message=str(exc) if isinstance(exc,(ValueError,AlreadyRunning)) else str(classify_error(exc))
        emit({'ok':False,'error':message,'error_type':type(exc).__name__},args.output)
        if not args.quiet and not args.output:
            import tkinter.messagebox
            tkinter.messagebox.showerror('JARVIS DEV',message)
        raise SystemExit(1)

if __name__ == "__main__":
    main()
