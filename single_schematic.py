"""One owned schematic per task; compose existing native operations, preserve evidence."""
from __future__ import annotations

from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
import time
from typing import Literal
import uuid

from pydantic import Field,model_validator
from schematic_flow import Model,Target,Contract,atomic_json,digest,target_lock
from schematic_templates import Override,read_request
from design_changes import Replacement,PatchRequest,plan_patch,review_changes,mutate_design,verify_delta,same_netlist_after_replacement,load_snapshot
from design_snapshots import manifest,file_hash,opened_copy,read_design,structural,clone_workspace
from workflow_trace import Trace


class TaskRequest(Model):
    operation: Literal['build','import','edit','inspect','simulate','status']
    request_id: str = Field(default='',pattern=r'^[A-Za-z0-9_-]{0,64}$')
    target: Target | None = None
    expected_revision: int | None = Field(default=None,ge=0)
    build_request: str = ''
    source: Target | None = None
    contract: Contract | None = None
    changes: list[Override] = Field(default_factory=list,max_length=150)
    replacements: list[Replacement] = Field(default_factory=list,max_length=30)
    contract_after: Contract | None = None
    fresh: bool = False
    simulation: dict | None = None
    after_revision: int = -1
    wait_seconds: int = Field(default=0,ge=0,le=25)

    @model_validator(mode='after')
    def scope(self):
        if self.operation not in ('inspect','status') and not self.request_id:
            raise ValueError('A stable request_id is required; reuse it after an observation timeout')
        allowed={'operation','request_id','target','expected_revision'}
        allowed|={'build':{'build_request','simulation'},'import':{'source','contract','simulation'},
                  'edit':{'changes','replacements','contract_after','simulation'},'inspect':{'fresh'},
                  'simulate':{'simulation'},'status':{'after_revision','wait_seconds'}}[self.operation]
        if self.model_fields_set-allowed:raise ValueError('Fields outside this operation: '+str(sorted(self.model_fields_set-allowed)))
        if self.operation=='build' and not self.build_request:raise ValueError('build_request is required')
        if self.operation=='import' and (self.source is None or self.target is None or self.contract is None):
            raise ValueError('import requires source, independent target and explicit contract')
        if self.operation=='edit' and not (self.changes or self.replacements):raise ValueError('Empty change set')
        if self.operation=='simulate' and self.simulation is None:raise ValueError('Explicit SP simulation authorization and evaluation required')
        if self.simulation is not None:
            from design_results import SimulateRequest
            if set(self.simulation)-{'authorization','evaluation','timeout_seconds','data_directory','use_cache'}:
                raise ValueError('Unsupported simulation field')
            if 'use_cache' in self.simulation and type(self.simulation['use_cache']) is not bool:
                raise ValueError('use_cache must be boolean')
            SimulateRequest.model_validate({**{k:v for k,v in self.simulation.items() if k!='use_cache'},
                'snapshot':'validated-later','snapshot_sha256':'0'*64})
        return self


def safe_target(root,target):
    ws=Path(target.workspace)
    if not ws.is_absolute():raise ValueError('Absolute independent workspace required')
    for p in (ws,*ws.parents):
        if p.is_symlink() or p.is_junction():raise ValueError('Linked task workspace is not supported')
        if p==root:break
    ws=ws.resolve()
    if not ws.is_relative_to(root) or ws==root:raise ValueError('Workspace must be inside this independent task directory')
    return target.model_copy(update={'workspace':str(ws)})


def snapshot(state):
    snap=load_snapshot(state['snapshot'],state['snapshot_sha256'])
    if snap['source']!=state['target']:raise ValueError('TASK_IDENTITY_MISMATCH: snapshot target changed')
    if manifest(Path(state['target']['workspace']))!=snap['source_files']:
        raise ValueError('CONCURRENT_TASK_EDIT: saved workspace changed; no overwrite or stale cache reuse')
    if file_hash(snap['netlist'])!=snap['netlist_sha256']:raise ValueError('Saved netlist evidence changed')
    return snap


def environment_current(state):
    profile=state.get('capabilities')
    if profile:
        from design_capabilities import check
        if not check(profile['profile'])['valid']:
            raise ValueError('ENVIRONMENT_CHANGED: ADS/model libraries changed; explicit revalidation required')


def save_snapshot(snap,netlist,target,folder):
    folder.mkdir(parents=True,exist_ok=True)
    net=folder/'verified.net';net.write_text(netlist,encoding='utf-8')
    path=folder/'snapshot.json'
    snap.update(source=target.model_dump(),source_files=manifest(Path(target.workspace)),
        netlist=str(net),netlist_sha256=file_hash(net))
    atomic_json(path,snap)
    return {'snapshot':str(path),'snapshot_sha256':file_hash(path)}


def bind_snapshot(state,result):
    previous=state.get('simulation')
    if previous:
        if not previous.get('finalized'):
            raise ValueError('SIMULATION_UNRESOLVED: retain the original job before changing revision')
        state['previous_simulation']={'revision':state['revision'],'evidence':previous['evidence'],
            'job_id':previous.get('job_id'),'status':previous['status']}
        state.pop('simulation')
    state.update(snapshot=result['snapshot'],snapshot_sha256=result['snapshot_sha256'])
    snap=load_snapshot(state['snapshot'],state['snapshot_sha256'])
    if snap.get('capability_profile'):state['capabilities']=snap['capability_profile']
    state['contract']=snap['contract']


def build(engine,req,root,folder,trace,state):
    from schematic_flow import execute
    with trace.span('configuration','expand existing recipe and bind one schematic'):
        raw=Path(req.build_request).read_bytes()
        original=json.loads((folder/'request.json').read_text(encoding='utf-8'))
        if digest(raw)!=original['build_request_sha256']:raise ValueError('Build input changed before expansion')
        frozen=folder/'build-input.json';frozen.write_bytes(raw)
        plan,info=read_request(str(frozen))
        target=safe_target(root,plan.target)
        if req.target and req.target.model_dump()!=target.model_dump():raise ValueError('Task target differs from build input')
        if state.get('target') and state['target']!=target.model_dump():raise ValueError('TASK_IDENTITY_MISMATCH: keep the original task target')
        if Path(target.workspace).exists():raise ValueError('Use a new workspace; import copies an existing design once')
        if req.simulation:
            from design_results import same_conditions
            same_conditions({'contract':plan.contract.model_dump()},req.simulation['evaluation'])
        plan=plan.model_copy(update={'target':target})
        state.update(target=target.model_dump(),contract=plan.contract.model_dump(),view='schematic',
            input_source=str(Path(req.build_request).resolve()),template=info.get('template'))
        atomic_json(folder/'expanded.json',plan.model_dump())
        atomic_json(root/'task.json',state)
    with trace.span('build','build once and capture reopened native state'):
        result=execute(engine,plan,capture_snapshot=True)
    trace.native_timings(result['timings'])
    original=load_snapshot(result['snapshot'],result['snapshot_sha256'])
    result.update(save_snapshot(original,Path(original['netlist']).read_text(encoding='utf-8'),target,folder))
    return result


def import_once(engine,req,root,folder,trace,state):
    from design_capabilities import capture
    target=safe_target(root,req.target)
    state.update(target=target.model_dump(),contract=req.contract.model_dump(),view='schematic',input_source=req.source.model_dump())
    atomic_json(root/'task.json',state)
    with trace.span('materials','copy the original once; original is never opened by ADS'):
        source=Path(req.source.workspace).resolve(strict=True)
        if (target.library,target.cell)!=(req.source.library,req.source.cell):raise ValueError('Import must preserve library and cell identity')
        before=clone_workspace(source,Path(target.workspace))
    with trace.span('acceptance','read independent copy and record exact native graph'):
        with opened_copy(engine,target) as design:
            snap,netlist=read_design(design,req.contract)
            if not snap['complete']:raise ValueError('Unreadable native fields; cannot verify bounded edits')
            snap['capability_profile']=capture(design,root/'profiles')
    if manifest(source)!=before:raise ValueError('CONCURRENT_SOURCE_EDIT: original changed during import')
    state['original']={'target':req.source.model_dump(),'files':before,'read_only':True}
    return {**save_snapshot(snap,netlist,target,folder),'native_open_verified':True,'source_unchanged':True}


def edit(engine,req,root,folder,trace,state,before):
    from ads_engine import get_de
    target=Target.model_validate(state['target']);ws=Path(target.workspace)
    patch=PatchRequest(snapshot=state['snapshot'],snapshot_sha256=state['snapshot_sha256'],destination=str(ws),
        changes=req.changes,replacements=req.replacements,contract_after=req.contract_after)
    with trace.span('preparation','freeze exact change list before native mutation'):
        review=plan_patch(patch.model_dump(),folder,owned_in_place=True)
    contract=req.contract_after or Contract.model_validate(state['contract'])
    # Retain only the target cell's native files, not additional ADS workspaces.
    backup=folder/'before-native';native=ws/target.library/target.cell
    backup.mkdir()
    for name,h in before['source_files'].items():
        p=ws/name
        if p.is_relative_to(native):
            dest=backup/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
            if file_hash(dest)!=h:raise ValueError('Concurrent change while preserving native revision')
    with trace.span('rework','apply only requested edits to the same owned schematic'):
        with opened_copy(engine,target,writable=True) as design:
            with get_de().db.Transaction(design,'Single-task bounded delta') as tx:
                mutate_design(design,patch);tx.commit()
            after,netlist=read_design(design,contract)
            delta=verify_delta(before,after,patch)
            if manifest(ws)!=before['source_files']:raise ValueError('CONCURRENT_TASK_EDIT: disk changed before save')
            design.save_design()
    with trace.span('acceptance','reopen and verify parameters, connectivity and saved netlist'):
        with opened_copy(engine,target) as design:saved,saved_netlist=read_design(design,contract)
        if structural(saved)!=structural(after) or not same_netlist_after_replacement(netlist,saved_netlist,{r.instance for r in req.replacements}):
            raise ValueError('SAVE_REOPEN_MISMATCH: keep revision evidence and stop')
    return {**save_snapshot(saved,saved_netlist,target,folder),'changes':delta,'review':review,
        'connectivity_unchanged':True,'save_reopen_verified':True,'native_backup':str(backup)}


def summary(state,op,record,status='completed',**extra):
    t=state.get('target',{})
    return {'ok':True,'status':status,'operation':op,'revision':state.get('revision',0),
        'target':{'workspace':t.get('workspace'),'design':':'.join(t.get(k,'') for k in ('library','cell'))+':schematic'},
        'record':str(record),**extra}


def run(payload,directory,engine):
    req=TaskRequest.model_validate(payload);root=Path(directory).resolve();trace=Trace(root)
    root.mkdir(parents=True,exist_ok=True)
    with target_lock(root/'single-schematic'):
        return _run(req,root,engine,trace)


def _run(req,root,engine,trace):
    started=time.perf_counter();statefile=root/'task.json'
    state=json.loads(statefile.read_text(encoding='utf-8')) if statefile.exists() else None
    if state:
        if state.get('kind')!='single_schematic_task' or state.get('task_directory')!=str(root):
            raise ValueError('Task record identity differs from this directory')
        if state.get('target'):safe_target(root,Target.model_validate(state['target']))
        if req.target and state.get('target') and safe_target(root,req.target).model_dump()!=state['target']:
            raise ValueError('TASK_IDENTITY_MISMATCH: one task cannot switch schematic')
    if req.operation=='status':
        if state is None:raise ValueError('No schematic is bound to this task')
        if state.get('simulation'):
            from single_schematic_sim import poll
            return poll(state,root,req.after_revision,req.wait_seconds)
        return summary(state,'status',statefile,status=state['status'],pending=state.get('pending'))
    request_hash=digest(json.dumps(req.model_dump(),sort_keys=True).encode())
    input_hash=file_hash(req.build_request) if req.operation=='build' else None
    if input_hash:request_hash=digest((request_hash+input_hash).encode())
    folder=root/'operations'/(req.request_id or ('inspect-'+uuid.uuid4().hex))
    record=folder/'result.json'
    replay=bool(req.request_id and (folder/'request.json').exists())
    if replay:
        old=json.loads((folder/'request.json').read_text(encoding='utf-8'))
        if old['request_hash']!=request_hash:raise ValueError('REQUEST_ID_CONFLICT: same ID has different input')
    unbound=state is None or state.get('status')=='rejected_before_creation'
    if unbound and req.operation not in ('build','import'):raise ValueError('Start with build or import once')
    if unbound and state and state.get('target') and Path(state['target']['workspace']).exists():
        raise ValueError('TASK_NOT_READY: a workspace appeared after rejection; review before retrying')
    if state and not unbound:
        if not replay and req.expected_revision is not None and req.expected_revision!=state['revision']:
            raise ValueError('REVISION_CONFLICT: read task status before editing')
        if state['status']!='ready':raise ValueError('TASK_NOT_READY: inspect task.json and the original pending request; no redispatch')
        # Check physical inputs before any snapshot/result replay.
        before=snapshot(state)
        if req.operation=='edit' and not replay:
            preflight=PatchRequest(snapshot=state['snapshot'],snapshot_sha256=state['snapshot_sha256'],
                destination=state['target']['workspace'],changes=req.changes,replacements=req.replacements,contract_after=req.contract_after)
            review_changes(preflight,before)
        environment_current(state)
        if req.simulation and not replay:
            from design_results import same_conditions
            contract=req.contract_after.model_dump() if req.contract_after else state['contract']
            same_conditions({'contract':contract},req.simulation['evaluation'])
    else:before=None
    if replay:
        if not record.exists():raise ValueError('REQUEST_UNRESOLVED: inspect original evidence; never automatically reissue')
        previous=json.loads(record.read_text(encoding='utf-8'))
        if not previous.get('ok'):raise ValueError('PREVIOUS_REQUEST_FAILED: inspect it and submit a corrected request with a new ID')
        return summary(state,req.operation,record,status='already_completed',applied_revision=previous['revision'],replayed=True)
    if not unbound and req.operation in ('build','import'):raise ValueError('TASK_ALREADY_BOUND: use edit/inspect/simulate on this schematic')
    if req.operation=='inspect' and not req.fresh:
        return summary(state,'inspect',state['snapshot'],status='verified_snapshot',cache_hit=True,
            acceptance={'native_saved_snapshot':True,'inputs_unchanged':True})
    folder.mkdir(parents=True,exist_ok=False)
    atomic_json(folder/'request.json',{'request_hash':request_hash,'request':req.model_dump(),'build_request_sha256':input_hash})
    state=state or {'schema_version':1,'kind':'single_schematic_task','task_directory':str(root),'revision':0,
        'scope':{'simulation':'only explicitly authorized linear SP','one_target_view':'schematic',
            'acceptance':['requested parameters match','no unintended topology/connectivity change','save and reopen agree'],
            'engineering_inputs':'Explicit contract plus frozen expanded input/native snapshot; retain all units and model/board parameters without inferred defaults',
            'stops':['input error','concurrent edit','unadapted interface','failed acceptance','two identical failures under the same loaded implementation']},
        'measurements':{'model_tokens':None,'model_tokens_status':'unobserved','task_reasoning_time':'unobserved'}}
    # A persisted pending receipt protects operations even if a caller times out.
    state.update(status='working',pending=req.request_id or folder.name)
    atomic_json(statefile,state)
    ws=Path(state['target']['workspace']) if state.get('target') else None
    from workspace_activity import edit_guard
    guard=edit_guard(ws) if ws and ws.exists() else nullcontext()
    try:
        with guard:
            if req.operation=='build':result=build(engine,req,root,folder,trace,state)
            elif req.operation=='import':result=import_once(engine,req,root,folder,trace,state)
            elif req.operation=='edit':result=edit(engine,req,root,folder,trace,state,before)
            elif req.operation=='inspect':
                target=Target.model_validate(state['target'])
                with trace.span('acceptance','explicit fresh native inspection'):
                    with opened_copy(engine,target) as design:snap,netlist=read_design(design,Contract.model_validate(state['contract']))
                # File bytes were already hashed by snapshot(); compare native
                # text with universal-newline file text, not LF text to CRLF bytes.
                if structural(snap)!=structural(before) or netlist.replace('\r\n','\n')!=Path(before['netlist']).read_text(encoding='utf-8'):
                    raise ValueError('Fresh native read differs from the frozen snapshot')
                result={'fresh_native_verified':True}
            else:result={}
            if result.get('snapshot'):bind_snapshot(state,result)
            if req.operation in ('build','import','edit'):state['revision']+=1
            state.update(status='ready',pending=None)
            # The simulator reuses the existing durable serial RF executor.
            if req.simulation:
                from single_schematic_sim import submit
                result['simulation']=submit(req.simulation,state,root,folder)
            response=summary(state,req.operation,record,status='submitted' if result.get('simulation',{}).get('job_id') and not result['simulation'].get('cache_hit') else 'completed',elapsed_seconds=round(time.perf_counter()-started,6),
                changes=len(result.get('changes',[])),acceptance={'saved_reopened':req.operation in ('build','edit'),
                    'connectivity_verified':req.operation in ('build','edit'),
                    **({'native_read_verified':True,'original_unchanged':True} if req.operation=='import' else {})},
                simulation=result.get('simulation',{'status':'not_requested'}))
            atomic_json(record,{**result,**response,'snapshot':state.get('snapshot'),'snapshot_sha256':state.get('snapshot_sha256'),
                'timing_file':str(trace.path),'task_record':str(statefile)})
            atomic_json(statefile,state)
            return response
    except Exception as exc:
        accepted=load_snapshot(state['snapshot'],state['snapshot_sha256']) if state.get('snapshot') else None
        unchanged=accepted is not None and manifest(Path(state['target']['workspace']))==accepted['source_files']
        untouched=accepted is None and (not state.get('target') or not Path(state['target']['workspace']).exists())
        status='ready' if unchanged else 'rejected_before_creation' if untouched else 'needs_recovery'
        state.update(status=status,pending=None if unchanged or untouched else req.request_id)
        atomic_json(record,{'ok':False,'status':'failed','error':str(exc),'revision':state['revision'],'disk_unchanged':unchanged,
                           'native_backup':str(folder/'before-native'),'automatic_rollback':False,
                           'accepted_snapshot':state.get('snapshot'),'simulation':state.get('simulation')})
        atomic_json(statefile,state)
        raise
