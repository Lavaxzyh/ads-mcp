"""Bounded design operations; originals remain read-only and execution is explicit."""
from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from schematic_flow import atomic_json, digest
from schematic_templates import catalog, read_request
from workflow_trace import Trace


@lru_cache(maxsize=1)
def implementation_hash():
    root=Path(__file__).resolve().parent
    return digest(b''.join(p.read_bytes() for p in sorted(root.glob('*.py'))))


def dispatch(action, request_file, directory, engine):
    root = Path(directory).resolve()
    assigned = os.environ.get('ADS_MCP_TASK_DIR')
    if assigned and Path(assigned).resolve() != root:
        raise ValueError('This private session belongs to another task directory')
    trace = Trace(root)
    if action == 'templates': return {'ok':True,'templates':catalog()}
    if action == 'timing': return {'ok':True,**trace.summary()}
    with trace.span('materials','read workflow request'):
        source=Path(request_file).resolve(strict=True)
        if source.stat().st_size>512000: raise ValueError('Request exceeds 512 KB')
        raw=source.read_bytes(); request=json.loads(raw.decode('utf-8-sig'))
    request_hash=digest(action.encode()+raw); build_hash=implementation_hash()
    trace.check_retry(request_hash,build_hash)
    try:
        if action == 'run':
            from single_schematic import run
            return run(request,root,engine)
        if action in ('expand','validate','build'):
            from schematic_flow import execute
            with trace.span('configuration','expand template and check engineering contract'):
                plan, info=read_request(str(source))
                artifact=root/'requests'/f'{request_hash}.expanded.json'
                atomic_json(artifact,plan.model_dump())
            if action=='expand':
                return {'ok':True,'status':'expanded','expanded_request':str(artifact),**info}
            with trace.span('build' if action=='build' else 'preparation',action):
                result=execute(engine,plan,validate_only=action=='validate')
            trace.native_timings(result.get('timings',{}))
            result.update(expanded_request=str(artifact),template=info.get('template'),timing_file=str(trace.path))
            return result
        if action == 'export':
            from design_snapshots import export
            with trace.span('materials','copy and extract saved schematic'):
                return export(engine,request,root)
        if action == 'plan_patch':
            from design_changes import plan_patch
            with trace.span('preparation','generate frozen change review'):
                return plan_patch(request,root)
        if action == 'apply_patch':
            from design_changes import apply_patch
            with trace.span('rework','apply and verify reviewed changes on a new copy'):
                return apply_patch(engine,request,root)
        if action == 'capabilities':
            from design_capabilities import check
            if set(request)!={'profile'}:raise ValueError('capabilities requires exactly a profile path')
            return check(request['profile'])
        if action in ('plan_candidate','materialize','simulate','compare'):
            from design_results import plan_candidate,materialize,simulate,compare
            phase={'plan_candidate':'preparation','materialize':'rework','simulate':'simulation','compare':'acceptance'}[action]
            with trace.span(phase,action):
                if action=='materialize':return materialize(engine,request,root)
                return {'plan_candidate':plan_candidate,'simulate':simulate,'compare':compare}[action](request,root)
        raise ValueError('Unsupported workflow action; do not discover/patch native APIs inside a design task')
    except Exception as exc:
        trace.failure(request_hash,build_hash,exc)
        raise


def register(mcp,engine,state,dispatch_lock):
    @mcp.tool()
    def ads_design_workflow(action: Literal['run','templates','expand','validate','build','timing','export','plan_patch','apply_patch','capabilities','plan_candidate','materialize','simulate','compare'],
                            task_directory: str, request_file: str='') -> dict:
        """Use a verified recipe and explicit conditions in a private task session.

        Prefer action=run for one bound schematic, edits and compact receipts;
        read SINGLE-SCHEMATIC.zh-CN.md. Existing actions remain compatible.
        Legacy build never simulates; run requires explicit SP authorization. Long-lived
        isolated CLI supports consecutive commands without restarting ADS. Each
        operation is bounded; no retries or automatic adapter development.
        """
        try:
            if not Path(task_directory).is_absolute(): raise ValueError('task_directory must be absolute')
            with dispatch_lock:
                if any(t['status']=='running' for t in state.snapshot()['tasks'].values()):
                    raise RuntimeError('Session has another running operation; use the private client')
                return dispatch(action,request_file,task_directory,engine)
        except Exception as exc:
            error=f'{type(exc).__name__}: {exc}'
            detail={}
            root=Path(task_directory)
            assigned=os.environ.get('ADS_MCP_TASK_DIR')
            if root.is_absolute() and root.is_dir() and (not assigned or root.resolve()==Path(assigned).resolve()):
                import uuid
                path=root/'errors'/(uuid.uuid4().hex+'.json')
                atomic_json(path,{'action':action,'request_file':request_file,'error':error})
                detail['record']=str(path)
            return {'ok':False,'error':error[:600],**detail,
                    'stop_condition':'Inspect this gap; no automatic retry, topology change or tool development',
                    'simulation_run':None if action in ('run','simulate','materialize') else False,
                    'simulation_status':'Inspect the persisted task/job record; execution may have started' if action in ('run','simulate','materialize') else 'not_requested'}
