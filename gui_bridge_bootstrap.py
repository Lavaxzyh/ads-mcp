"""Loaded by ADS -m/python_exec_file inside a task-owned native GUI process."""
import atexit
import contextlib
import ctypes
import hashlib
import hmac
import io
import json
import os
import sys
import traceback
import uuid
from pathlib import Path
from ctypes import wintypes

ROOT = Path(os.environ['ADS_MCP_GUI_SESSION_DIR']).resolve()

def canonical(data):
    return json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()

def write(path,data):
    tmp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_bytes(canonical(data))
    os.replace(tmp,path)

def birth_time():
    created,exited,kernel,user = [wintypes.FILETIME() for _ in range(4)]
    api = ctypes.windll.kernel32
    api.GetCurrentProcess.restype = wintypes.HANDLE
    api.GetProcessTimes.argtypes = [wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
    if not api.GetProcessTimes(api.GetCurrentProcess(),*[ctypes.byref(x) for x in (created,exited,kernel,user)]):
        raise ctypes.WinError()
    return ((created.dwHighDateTime<<32)|created.dwLowDateTime)/10000000-11644473600

try:
    launch = json.loads((ROOT/'launch-config.json').read_text(encoding='utf-8'))
    key = json.loads((ROOT/'credential.json').read_text(encoding='utf-8'))['key']
    sys.path.insert(0,launch['code_root'])
    from keysight.ads import de
    from keysight.ads.de import app as de_app
    from PySide2.QtCore import QTimer
    from PySide2.QtWidgets import QApplication
    application = QApplication.instance()
    if application is None or de.running_automation():
        raise RuntimeError('Managed bootstrap requires native ADS 2025 GUI')
    if de.workspace_is_open():
        raise RuntimeError('Owned GUI unexpectedly opened a workspace before bootstrap; refusing to switch it')
    de.open_workspace(launch['workspace'])
    from version import __version__
    bridge_file=Path(launch['code_root'])/'gui_bridge_bootstrap.py'
    record = dict(launch,status='ready',pid=os.getpid(),created=birth_time(),native_version=de.product_version(),
        bridge_version=__version__,bridge_sha256=hashlib.sha256(bridge_file.read_bytes()).hexdigest())
    write(ROOT/'session.json',record)
    busy = False

    def close_record():
        write(ROOT/'session.json',dict(record,status='closed'))
    atexit.register(close_record)

    def poll():
        global busy
        if busy:
            return
        candidates = sorted((ROOT/'requests').glob('*.json'),key=lambda p:p.stat().st_mtime_ns)
        if not candidates:
            return
        busy = True
        path = candidates[0]
        claimed = ROOT/'processing'/path.name
        try:
            os.replace(path,claimed)
            message = json.loads(claimed.read_text(encoding='utf-8'))
            payload = message['payload']
            expected = hmac.new(key.encode(),canonical(payload),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected,message.get('signature','')):
                raise ValueError('Invalid local request signature')
            if payload['session_nonce']!=record['nonce'] or payload['id']+'.json'!=path.name:
                raise ValueError('Session identity/request identity mismatch')
            response = ROOT/'responses'/path.name
            if response.exists():
                return
            operation = payload['operation']
            arguments = payload['arguments']
            current = str(Path(de.active_workspace().path).resolve()) if de.workspace_is_open() else ''
            if current != str(Path(record['workspace']).resolve()):
                raise RuntimeError('Owned GUI workspace changed externally; refusing operation')
            if operation == 'identity':
                result = {'ok':True,'mode':'remote','pid':os.getpid(),'workspace':current}
            elif operation == 'execute_internal':
                output,errors = io.StringIO(),io.StringIO()
                try:
                    with contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):
                        exec(compile(arguments['code'],'<ads-mcp-internal>','exec'),{'__name__':'__ads_mcp_internal__'})
                    result = {'ok':True,'mode':'remote','stdout':output.getvalue(),'stderr':errors.getvalue()}
                except BaseException:
                    result = {'ok':False,'mode':'remote','stdout':output.getvalue(),
                              'stderr':errors.getvalue()+traceback.format_exc()}
            elif operation == 'shutdown_owned':
                # Caller must finish/archive owned tasks before requesting shutdown.
                if getattr(application,'_ads_mcp_dispatches',{}):
                    raise RuntimeError('Owned circuit dispatch is still pending')
                from workspace_activity import assert_idle
                assert_idle(current)
                write(ROOT/'session.json',dict(record,status='closing'))
                result = {'ok':True,'mode':'remote','quitting_owned_pid':os.getpid()}
                QTimer.singleShot(200,lambda:de_app.exit_application(0))
            else:
                raise ValueError('Unknown managed bridge operation')
            result['request_id'] = payload['id']
            write(response,result)
        except BaseException:
            write(ROOT/'responses'/path.name,{'ok':False,'stderr':traceback.format_exc()})
        finally:
            busy = False

    timer = QTimer(application)
    timer.timeout.connect(poll)
    timer.start(100)
    application._ads_mcp_managed_timer = timer
except BaseException:
    write(ROOT/'boot-error.json',{'pid':os.getpid(),'error':traceback.format_exc()})
