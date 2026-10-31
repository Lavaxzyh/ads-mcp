"""Persistent solver ownership shared by MCP clients using one workspace.

A client timeout or death never clears a reservation: the native job can live
on independently. Only verified completion releases it automatically.
"""
import json
import os
from contextlib import contextmanager
from pathlib import Path


def activity_path(workspace):
    return Path(workspace).resolve()/'.ads-mcp'/'solver-active.json'


def assert_idle(workspace):
    p=activity_path(workspace)
    if p.exists():
        try:record=json.loads(p.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            raise RuntimeError('Workspace solver reservation cannot be read; inspect '+str(p))
        raise RuntimeError('Workspace is reserved by native task '+str(record.get('task_id'))+
            '; poll the original task using '+str(record.get('state_file'))+'. No restart or cancellation was sent.')


@contextmanager
def edit_guard(workspace):
    ws=Path(workspace).resolve()
    directory=ws/'.ads-mcp';directory.mkdir(parents=True,exist_ok=True)
    # Keep this module stdlib-only: it also runs inside ADS's bundled GUI
    # interpreter, which does not contain the MCP virtualenv's pydantic.
    with (directory/'activity.lock').open('a+b') as f:
        f.seek(0);f.write(b'0');f.flush();f.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('Another MCP operation owns this workspace') from exc
        try:
            assert_idle(ws)
            yield
        finally:
            f.seek(0)
            if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f.fileno(),fcntl.LOCK_UN)


def reserve(workspace, task_id, token, state_file, session_id):
    p=activity_path(workspace);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x',encoding='utf-8') as f:
        json.dump({'task_id':task_id,'token':token,'state_file':str(Path(state_file).resolve()),
            'session_id':session_id},f)


def release(workspace, token):
    p=activity_path(workspace)
    if not p.exists():return
    record=json.loads(p.read_text(encoding='utf-8'))
    if record.get('token')!=token:
        raise RuntimeError('Workspace reservation belongs to another native task; refusing to release it')
    p.unlink()
