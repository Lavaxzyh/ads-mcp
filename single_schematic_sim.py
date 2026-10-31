"""Existing linear-SP executor, durable task receipts and conservative evidence reuse."""
import json
from pathlib import Path
import uuid

from schematic_flow import atomic_json,digest
from design_snapshots import file_hash
from design_results import SimulateRequest,same_conditions,snapshot_netlist,data_dependencies
from matching_runs import solver_fingerprint,cache_eligibility,raw_frame
from matching_sweep import prepare_sp_verification
from rf_jobs import JobStore
from workspace_activity import reserve,release


def simulation_context(request,state):
    if set(request)-{'authorization','evaluation','timeout_seconds','data_directory','use_cache'}:
        raise ValueError('Simulation takes only authorization, evaluation, timeout_seconds, data_directory and use_cache')
    if 'use_cache' in request and type(request['use_cache']) is not bool:raise ValueError('use_cache must be boolean')
    req=SimulateRequest.model_validate({**{k:v for k,v in request.items() if k!='use_cache'},
        'snapshot':state['snapshot'],'snapshot_sha256':state['snapshot_sha256']})
    from single_schematic import snapshot
    snap=snapshot(state);evaluation=same_conditions(snap,req.evaluation)
    text=snapshot_netlist(snap)
    from matching_sweep import NetlistEditor
    NetlistEditor(text).validate_sp(evaluation.reference_ohms)
    dependencies=data_dependencies(text,req.data_directory,snap['source']['workspace'])
    fingerprint=solver_fingerprint()
    reason=cache_eligibility(text,req.data_directory)
    if request.get('use_cache',True) is False:reason='Explicit fresh-solve request'
    frozen={'netlist_sha256':snap['netlist_sha256'],'workspace_files':snap['source_files'],
        'contract':snap['contract'],'evaluation':evaluation.model_dump(),'dependency_hashes':dependencies,
        'solver_fingerprint':fingerprint}
    key=digest(json.dumps(frozen,sort_keys=True).encode())
    return req,snap,frozen,key,reason


def submit(request,state,root,folder):
    req,snap,frozen,key,reason=simulation_context(request,state)
    cached=state.get('simulation_cache',{}).get(key) if not reason else None
    if cached:
        path=Path(cached['evidence'])
        try:
            if file_hash(path)!=cached['sha256']:raise ValueError('evidence digest changed')
            evidence=json.loads(path.read_text(encoding='utf-8'))
            if any(file_hash(Path(p))!=h for p,h in evidence['artifacts'].items()):raise ValueError('cached artifact changed')
            raw_frame(evidence['result']['raw_sparameters'])
        except (OSError,ValueError,KeyError) as exc:
            reason='Cache invalid; fresh solve required: '+str(exc)
        else:
            new={**evidence,'snapshot':state['snapshot'],'snapshot_sha256':state['snapshot_sha256'],
                 'cache_hit':True,'source_evidence':str(path)}
            out=folder/'evidence.json';atomic_json(out,new)
            state['simulation']={'status':'completed','cache_hit':True,'evidence':str(out),'cache_key':key,
                'job_id':evidence['job_id'],'jobs_root':str(root/'rf-jobs'),'finalized':True,'output_dir':str(folder)}
            return {'status':'completed','cache_hit':True,'job_id':evidence['job_id'],'evidence':str(out)}
    out=folder/'simulation'
    plan=prepare_sp_verification(snap['netlist'],req.evaluation.model_dump(),str(out),req.timeout_seconds,req.data_directory)
    token=uuid.uuid4().hex
    job={'status':'submitting','job_id':None,'jobs_root':str(root/'rf-jobs'),'output_dir':str(out),
        'request':request,'snapshot':state['snapshot'],'snapshot_sha256':state['snapshot_sha256'],
        'frozen':frozen,'cache_key':key,'cache_policy':reason or 'eligible','reservation_token':token,
        'evidence':str(folder/'evidence.json'),'finalized':False,'cache_hit':False}
    state['simulation']=job
    # Persist the unique output identity before submit. A broken response can
    # reconcile the existing SQLite row by output_dir, never submit again.
    atomic_json(root/'task.json',state)
    reserve(state['target']['workspace'],'single-sp:'+folder.name,token,root/'task.json',None)
    response=JobStore(job['jobs_root']).submit('sp',plan)
    job.update(job_id=response['job_id'],status=response['status'])
    atomic_json(root/'task.json',state)
    return {'status':job['status'],'job_id':job['job_id'],'cache_hit':False,
        'cache_policy':job['cache_policy'],'next_action':'run operation=status; do not resubmit'}


def poll(state,root,after_revision=-1,wait_seconds=0):
    from single_schematic import summary,snapshot
    job=state['simulation'];record=Path(job['evidence'])
    if job.get('finalized'):
        snapshot(state)
        return summary(state,'status',record,status=job['status'],job_id=job['job_id'],cache_hit=job['cache_hit'],
            ok=job['status']=='completed',**({'error':job['error']} if job.get('error') else {}))
    store=JobStore(job['jobs_root'])
    if job['job_id'] is None:
        with store.connect() as db:
            found=db.execute('SELECT id FROM jobs WHERE output_dir=?',(job['output_dir'],)).fetchall()
        if len(found)!=1:raise RuntimeError('SUBMISSION_UNRESOLVED: no unique original job; inspect persisted receipt, do not resubmit')
        job['job_id']=found[0]['id'];atomic_json(root/'task.json',state)
    status=store.wait(job['job_id'],after_revision,wait_seconds)
    if status['status'] in ('queued','running','paused'):
        return summary(state,'status',root/'task.json',status=status['status'],job_id=job['job_id'],
            job_revision=status['revision'],changed=status['revision']!=after_revision)
    try:
        if status['status']!='completed':
            raise RuntimeError('Original SP job stopped: '+str(status.get('error') or status['status']))
        snap=snapshot(state)
        if state['snapshot_sha256']!=job['snapshot_sha256']:raise ValueError('Task revision changed while solver was active')
        text=snapshot_netlist(snap)
        if job['frozen']['dependency_hashes']!=data_dependencies(text,job['request'].get('data_directory',''),state['target']['workspace']):
            raise ValueError('Model dependencies changed during simulation')
        if job['frozen']['solver_fingerprint']!=solver_fingerprint():raise ValueError('Solver installation changed during simulation')
        result=json.loads((Path(job['output_dir'])/'summary.json').read_text(encoding='utf-8'))['best_case']
        raw_frame(result['raw_sparameters'])
        evidence={'schema_version':1,'kind':'linear_sp_evidence','snapshot':state['snapshot'],
            'snapshot_sha256':state['snapshot_sha256'],**{k:v for k,v in job['frozen'].items() if k!='workspace_files'},
            'result':result,'job_id':job['job_id'],'cache_hit':False,
            'artifacts':{result[k]:file_hash(result[k]) for k in ('dataset','raw_sparameters','points_csv')},
            'scope':'Saved independent linear-SP only; no HB, PAE or measured-performance claim'}
        atomic_json(record,evidence)
        if job['cache_policy']=='eligible':
            state.setdefault('simulation_cache',{})[job['cache_key']]={'evidence':str(record),'sha256':file_hash(record)}
        job.update(status='completed',finalized=True)
    except Exception as exc:
        atomic_json(record,{'ok':False,'status':'failed_acceptance','error':str(exc),'job_id':job['job_id']})
        job.update(status='failed_acceptance',finalized=True,error=str(exc))
        raise
    finally:
        # The authoritative queue is terminal. Observation timeout never gets here.
        release(state['target']['workspace'],job['reservation_token'])
        atomic_json(root/'task.json',state)
    return summary(state,'status',record,status='completed',job_id=job['job_id'],job_revision=status['revision'],
        changed=True,cache_hit=False,metrics={k:result[k] for k in ('max_error_ohm','rms_error_ohm') if k in result})
