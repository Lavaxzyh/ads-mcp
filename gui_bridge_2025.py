"""Task-owned ADS 2025 GUI transport over authenticated local request files.

No arbitrary-code MCP tool is exposed. Only the server's existing internal
GUI implementations use execute_python. A request is claimed once before
execution; an observation timeout never reposts it or kills ADS.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path

import psutil


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def atomic_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_bytes(canonical(data))
    os.replace(temporary, path)


def registry_root():
    from ads_engine import get_workroot
    return Path(os.environ.get('ADS_MCP_GUI_ROOT') or get_workroot()/'.ads-mcp/gui').resolve()


def live_record(directory):
    directory = Path(directory).resolve()
    record = json.loads((directory/'session.json').read_text(encoding='utf-8'))
    if record.get('status') != 'ready':
        return None
    try:
        process = psutil.Process(record['pid'])
        if abs(process.create_time()-record['created']) > .01:
            return None
        if Path(process.exe()).resolve() != (Path(record['ads_install'])/'bin/hpeesofde.exe').resolve():
            return None
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    return record


def discover():
    found = []
    for directory in registry_root().glob('session-*'):
        try:
            record = live_record(directory)
        except (OSError, ValueError, KeyError):
            continue
        if record:
            bridge=Path(record['code_root'])/'gui_bridge_bootstrap.py'
            current=hashlib.sha256(bridge.read_bytes()).hexdigest() if bridge.is_file() else None
            found.append({'session_id':record['pid'], 'pid':record['pid'],
                          'workspace':record['workspace'], 'native_version':record['native_version'],
                          'transport':'managed_ads2025', 'owned':True,
                          'created':record['created'], 'session_directory':str(directory)})
            found[-1].update(bridge_version=record.get('bridge_version'),
                bridge_loaded_sha256=record.get('bridge_sha256'),bridge_disk_sha256=current,
                bridge_code_changed=None if not record.get('bridge_sha256') else record['bridge_sha256']!=current)
    return found


def _spawn_detached_ads(command, cwd, env):
    # Same user/permissions through the native broker; no service installation.
    # This keeps an explicitly owned GUI independent of the MCP job object.
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    service=startup=process=arguments=result=None
    try:
        service=win32com.client.GetObject(r'winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2')
        startup=service.Get('Win32_ProcessStartup').SpawnInstance_()
        startup.ShowWindow=0
        startup.CreateFlags=0x01000000 | 0x00000400 | 0x00000200
        startup.EnvironmentVariables=[f'{k}={v}' for k,v in env.items()]
        process=service.Get('Win32_Process')
        arguments=process.Methods_('Create').InParameters.SpawnInstance_()
        arguments.CommandLine=subprocess.list2cmdline(command)
        arguments.CurrentDirectory=str(cwd)
        arguments.ProcessStartupInformation=startup
        result=process.ExecMethod_('Create',arguments)
        if result.ReturnValue:
            raise RuntimeError(f'Owned GUI launch denied/failed with WMI code {result.ReturnValue}; no alternate account or retry used')
        pid=int(result.ProcessId)
        return {'pid':pid,'created':psutil.Process(pid).create_time()}
    finally:
        result=arguments=process=startup=service=None
        pythoncom.CoUninitialize()


def start_session(task_directory, workspace, wait_seconds=20):
    from schematic_flow import target_lock
    root=registry_root()
    root.mkdir(parents=True,exist_ok=True)
    lock=root/('workspace-'+hashlib.sha256(str(Path(workspace).resolve()).casefold().encode()).hexdigest())
    with target_lock(lock):
        return _start_session(task_directory,workspace,wait_seconds)


def _start_session(task_directory, workspace, wait_seconds=20):
    from ads_engine import get_ads_install_dir
    task = Path(task_directory).resolve(strict=True)
    ws = Path(workspace).resolve(strict=True)
    if not ws.is_relative_to(task) or ws == task or not (ws/'lib.defs').is_file():
        raise ValueError('GUI workspace must be an independent ADS workspace inside task_directory')
    for p in [ws, *ws.parents]:
        if p.is_symlink() or p.is_junction():
            raise ValueError('Linked workspace paths require explicit dependency handling')
        if p == task:
            break
    for session in discover():
        if Path(session['workspace']).resolve() == ws:
            raise RuntimeError('This workspace already has a live managed GUI session; reuse its session_id')
    for folder in registry_root().glob('session-*'):
        try:
            previous=json.loads((folder/'launch-config.json').read_text(encoding='utf-8'))
            if Path(previous['workspace']).resolve()==ws and not (folder/'session.json').exists():
                raise RuntimeError('An earlier launch is unresolved; inspect its process/boot evidence before starting again: '+str(folder))
        except (FileNotFoundError,ValueError,KeyError):
            continue
    directory = registry_root()/('session-'+uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    for name in ('requests','processing','responses'):
        (directory/name).mkdir()
    private_home = directory/'home'
    config = private_home/'hpeesof/config/SystemVue'
    config.mkdir(parents=True)
    (config/'lib.defs').write_text('# task-owned ADS GUI\n',encoding='utf-8')
    install = get_ads_install_dir()
    launch = {'schema_version':1, 'status':'starting', 'workspace':str(ws),
              'task_directory':str(task), 'ads_install':str(install),
              'code_root':str(Path(__file__).resolve().parent), 'nonce':secrets.token_hex(24)}
    atomic_json(directory/'launch-config.json',launch)
    atomic_json(directory/'credential.json',{'key':secrets.token_hex(32)})
    boot = directory/'boot.ael'
    bootstrap = Path(__file__).with_name('gui_bridge_bootstrap.py').as_posix()
    boot.write_text('python_exec_file('+json.dumps(bootstrap)+');\n',encoding='utf-8')
    env = dict(os.environ, HOME=str(private_home), HPEESOF_DIR=str(install),
               COMPL_DIR=str(install), SIMARCH='win32_64', DEINVOKE_NO_SPLASH='1',
               ADS_NO_BACKGROUND='on', ADS_MCP_GUI_SESSION_DIR=str(directory),
               PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONHOME',None)
    env.pop('PYTHONPATH',None)
    env['PATH'] = os.pathsep.join([str(install/'bin'),str(install/'tools/python'),
                                  str(install/'adsptolemy/lib.win32_64'),env['PATH']])
    launcher=_spawn_detached_ads([str(install/'bin/ads.exe'),'-m',str(boot)],task,env)
    atomic_json(directory/'launcher.json',launcher)
    deadline = time.monotonic()+min(max(wait_seconds,0),45)
    while time.monotonic()<deadline:
        if (directory/'boot-error.json').is_file():
            raise RuntimeError('Owned GUI bootstrap failed: '+str(directory/'boot-error.json'))
        if (directory/'session.json').is_file():
            record = live_record(directory)
            if record:
                return {'status':'ready','session_id':record['pid'],'workspace':str(ws),
                        'session_directory':str(directory),'transport':'managed_ads2025'}
        time.sleep(.1)
    return {'status':'starting','session_directory':str(directory),
            'launcher_pid':launcher['pid'],'note':'Startup has not been reissued. Inspect this directory/process before another start.'}


class ManagedAdsClient:
    def __init__(self, timeout=55):
        self.timeout = timeout
        self.directory = None
        self.record = None

    def __enter__(self):
        return self

    def __exit__(self,*_):
        pass

    def _request(self, operation, arguments):
        if self.directory is None or live_record(self.directory) is None:
            raise RuntimeError('Selected managed ADS session is not live; no operation dispatched')
        request_id = uuid.uuid4().hex
        payload = {'id':request_id,'operation':operation,'arguments':arguments,
                   'session_nonce':self.record['nonce']}
        key = json.loads((self.directory/'credential.json').read_text(encoding='utf-8'))['key']
        message = {'payload':payload,'signature':hmac.new(key.encode(),canonical(payload),hashlib.sha256).hexdigest()}
        atomic_json(self.directory/'requests'/f'{request_id}.json',message)
        response = self.directory/'responses'/f'{request_id}.json'
        deadline = time.monotonic()+self.timeout
        while time.monotonic()<deadline:
            if response.is_file():
                try:
                    result = json.loads(response.read_text(encoding='utf-8'))
                except PermissionError:
                    # Windows can briefly deny a read during atomic replace
                    # or an antivirus scan. Re-observe this SAME response ID.
                    time.sleep(.05)
                    continue
                if result.get('ok') is False:
                    raise RuntimeError('Managed ADS operation failed: '+str(result.get('stderr',result)))
                return result
            time.sleep(.05)
        raise TimeoutError(f'Managed ADS dispatch {request_id} is unconfirmed; inspect {response}. No retry was sent.')

    def call(self, name, arguments):
        if name == 'list_sessions':
            return {'sessions':discover()}
        if name == 'connect_session':
            session_id = arguments['session_id']
            matches = [s for s in discover() if s['session_id']==session_id]
            if len(matches)!=1:
                raise RuntimeError('Select a live task-owned ADS 2025 session; arbitrary existing GUI processes cannot be attached')
            self.directory = Path(matches[0]['session_directory'])
            self.record = live_record(self.directory)
            ping = self._request('identity',{})
            return {'connected':True,'mode':'remote',**ping}
        if name == 'execute_python':
            return self._request('execute_internal',arguments)
        if name == 'shutdown_owned':
            # This also protects an older already-running bootstrap. Never
            # force-close an editor, a cockpit or a solver merely to free RAM.
            from workspace_activity import assert_idle
            assert_idle(self.record['workspace'])
            process=psutil.Process(self.record['pid'])
            active=[p.name() for p in process.children(recursive=True)
                if any(k in p.name().lower() for k in ('hpeesofsim','momengine','hpeesofem','emds'))]
            if active:raise RuntimeError('Owned native solver processes are still active: '+str(active))
            self._request('execute_internal',{'code':'''from PySide2.QtWidgets import QApplication,QDialog
from shiboken2 import isValid
import re
windows=[w for w in QApplication.topLevelWidgets() if isValid(w) and w.isVisible()]
blocked=[w.windowTitle() for w in windows if isinstance(w,QDialog) or re.search(r'\\[[^\\]]+:[^\\]]+:[^\\]]+\\]',w.windowTitle()) or 'EM Setup for simulation' in w.windowTitle()]
if blocked:raise RuntimeError('Save and close owned design/dialog windows before stopping this session: '+str(blocked))
'''})
            return self._request('shutdown_owned',arguments)
        raise ValueError('Unsupported managed bridge method')

    def execute(self, code):
        result = self.call('execute_python',{'code':code})
        from momentum import _MARKER
        for line in reversed(result.get('stdout','').splitlines()):
            if line.startswith(_MARKER):
                return json.loads(line[len(_MARKER):])
        raise RuntimeError('ADS GUI did not return the expected Momentum response')
