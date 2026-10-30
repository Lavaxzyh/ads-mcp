"""Bridge proven matching results to native copies and simulation-backed comparisons."""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
from typing import Literal
import uuid

import numpy as np
from pydantic import Field
from schematic_flow import Model, atomic_json, digest
from design_snapshots import file_hash, manifest
from design_changes import load_snapshot, plan_patch, apply_patch, changes_between, ApplyRequest
from rf_workflows import MatchingEvaluation, ParameterTarget
from matching_sweep import NetlistEditor


def canonical_netlist(text):
    lines=re.sub(r'\\[ \t]*\r?\n[ \t]*',' ',text).splitlines()
    def spacing(line):
        result=[];quoted=False;escaped=False;space=False
        for char in line.strip():
            if quoted:
                result.append(char)
                if char=='"' and not escaped:quoted=False
                escaped=char=='\\' and not escaped
            elif char.isspace():space=True
            else:
                if space:result.append(' ');space=False
                result.append(char)
                if char=='"':quoted=True;escaped=False
        return ''.join(result)
    return '\n'.join(spacing(line) for line in lines if line.strip() and not line.lstrip().startswith((';','!')))


def snapshot_netlist(snapshot):
    path=Path(snapshot['netlist'])
    if file_hash(path)!=snapshot['netlist_sha256']:raise ValueError('Snapshot netlist artifact changed')
    if manifest(Path(snapshot['source']['workspace']))!=snapshot['source_files']:
        raise ValueError('CONCURRENT_SOURCE_EDIT: schematic changed after export')
    return path.read_text(encoding='utf-8')


def data_dependencies(text,data_directory,workspace):
    """Explicit data-directory contents plus literal file references; no guessing."""
    root=Path(data_directory).resolve(strict=True) if data_directory else Path(workspace).resolve(strict=True)
    files=set()
    if data_directory:
        for p in root.rglob('*'):
            if p.is_symlink() or p.is_junction():raise ValueError('Linked data dependency needs explicit handling')
            if p.is_file():files.add(p)
            if len(files)>25000:raise ValueError('Data dependencies exceed bounded fingerprint limit')
    for raw in re.findall(r'"([^"\r\n]+\.(?:s\d+p|mdf|mdif|dat|net|ckt|lib|mdl|csv))"',text,re.I):
        if '$' in raw:raise ValueError('Unresolved model-data path prevents same-condition evidence: '+raw)
        p=Path(raw);p=p.resolve(strict=True) if p.is_absolute() else (root/p).resolve(strict=True)
        files.add(p)
    return {str(p):file_hash(p) for p in sorted(files)}


def same_conditions(snapshot,evaluation):
    contract=snapshot.get('contract')
    if not contract:raise ValueError('Explicit reference planes, ports and frequency contract required')
    evaluation=MatchingEvaluation.model_validate(evaluation)
    ports={p['number']:p for p in contract['ports']}
    if set(ports)!={1,2}:raise ValueError('Only explicit two-port linear SP is supported')
    if ports[evaluation.input_port]['reference_plane']!=evaluation.reference_plane:
        raise ValueError('Scored input reference plane differs from schematic contract')
    if evaluation.start_hz<contract['frequency_hz'][0]-1 or evaluation.stop_hz>contract['frequency_hz'][1]+1:
        raise ValueError('Evaluation band is outside the schematic sweep')
    for port in ports.values():
        text=port['impedance'];match=re.fullmatch(r'\s*([\d.eE+\-]+)\s*(?:Ohm)?\s*',text,re.I)
        if not match or not np.isclose(float(match[1]),evaluation.reference_ohms,rtol=1e-12,atol=0):
            raise ValueError('SP reference impedances differ from matching evaluation')
    return evaluation


class SimulateRequest(Model):
    snapshot: str
    snapshot_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    authorization: Literal['run_linear_sp_on_copy']
    evaluation: MatchingEvaluation
    timeout_seconds: int = Field(default=120,ge=1,le=120)
    data_directory: str = ''


def simulate(payload,task_directory):
    from matching_sweep import prepare_sp_verification
    from matching_runs import solver_fingerprint
    from rf_jobs import JobStore
    req=SimulateRequest.model_validate(payload)
    snap=load_snapshot(req.snapshot,req.snapshot_sha256)
    if not snap['complete']:raise ValueError('Complete native snapshot required before simulation')
    evaluation=same_conditions(snap,req.evaluation)
    netlist=snapshot_netlist(snap);NetlistEditor(netlist).validate_sp(evaluation.reference_ohms)
    dependencies=data_dependencies(netlist,req.data_directory,snap['source']['workspace'])
    out=Path(task_directory)/'simulations'/uuid.uuid4().hex
    model_fingerprint=solver_fingerprint()
    plan=prepare_sp_verification(snap['netlist'],evaluation.model_dump(),str(out),req.timeout_seconds,req.data_directory)
    store=JobStore();job=store.submit('sp',plan);revision=-1;deadline=time.monotonic()+req.timeout_seconds+60
    while True:
        status=store.wait(job['job_id'],revision,10);revision=status.get('revision',revision)
        if status['status'] not in ('queued','running'):break
        if time.monotonic()>deadline:
            store.control_job(job['job_id'],'cancel')
            raise TimeoutError('SP verification exceeded its dispatch budget; only its owned job was cancelled: '+job['job_id'])
    if status['status']!='completed':raise RuntimeError(f'SP verification job {job["job_id"]}: {status.get("error",status["status"])}')
    summary=json.loads((out/'summary.json').read_text(encoding='utf-8'))
    result=summary['best_case']
    snapshot_netlist(snap)  # Abort evidence promotion on a concurrent saved edit.
    if dependencies!=data_dependencies(netlist,req.data_directory,snap['source']['workspace']):
        raise ValueError('Model-data dependencies changed during simulation')
    evidence={'schema_version':1,'kind':'linear_sp_evidence','snapshot':str(Path(req.snapshot).resolve()),
              'snapshot_sha256':req.snapshot_sha256,'netlist_sha256':snap['netlist_sha256'],
              'contract':snap['contract'],'evaluation':evaluation.model_dump(),
              'solver_fingerprint':model_fingerprint,'dependency_hashes':dependencies,'result':result,'job_id':job['job_id'],
              'artifacts':{result[k]:file_hash(result[k]) for k in ('dataset','raw_sparameters','points_csv')},
              'scope':'Linear SP simulation of this saved copy. No nonlinear HB, PAE, compression or measurement claim.'}
    path=out/'evidence.json';atomic_json(path,evidence)
    return {'ok':True,'status':'simulated_linear_sp','evidence':str(path),'evidence_sha256':file_hash(path),
            'raw_sparameters':result['raw_sparameters'],'points_csv':result['points_csv'],
            'simulation_run':True,'nonlinear_verified':False}


class CandidateRequest(Model):
    snapshot: str
    snapshot_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    search_directory: str
    case: int = Field(ge=1)
    destination: str


def plan_candidate(payload,task_directory):
    req=CandidateRequest.model_validate(payload)
    snapshot=load_snapshot(req.snapshot,req.snapshot_sha256)
    directory=Path(req.search_directory).resolve(strict=True)
    request_file=directory/'request.json';cases_file=directory/'cases.json';source_file=directory/'source.net'
    job=json.loads(request_file.read_text(encoding='utf-8'))
    if job.get('region_source') and file_hash(job['region_source'])!=job['region_source_sha256']:
        raise ValueError('Load-pull region source changed since the frozen search')
    cases=json.loads(cases_file.read_text(encoding='utf-8'))
    selected=[c for c in cases if c['case']==req.case]
    if len(selected)!=1 or selected[0]['status']!='done':raise ValueError('Select exactly one successfully simulated case')
    row=selected[0];evaluation=same_conditions(snapshot,job['evaluation'])
    if file_hash(source_file)!=job['source_sha256'] or file_hash(job['source_netlist'])!=job['source_sha256']:
        raise ValueError('Matching source netlist changed since the search')
    source=source_file.read_text(encoding='utf-8')
    if canonical_netlist(source)!=canonical_netlist(snapshot_netlist(snapshot)):
        raise ValueError('Search source and native schematic do not describe the same circuit/conditions')
    parameters=job.get('advanced_parameters',job['parameters'])
    targets=[ParameterTarget.model_validate({k:v for k,v in p.items() if k in ParameterTarget.model_fields}) for p in parameters]
    if any(p.scope for p in targets):raise ValueError('Hierarchical parameter writeback needs an explicit subdesign adapter')
    if set(row['parameters'])!={p.key() for p in targets}:raise ValueError('Selected parameter set differs from the frozen search')
    parts={c['name']:c for c in snapshot['components']};changes=[];mapping=[]
    for target in targets:
        value=float(row['parameters'][target.key()])
        if not np.isfinite(value):raise ValueError('Nonfinite selected parameter')
        literal=f'{value:.17g}'+(' '+target.unit if target.unit else '')
        if target.variable:
            owners=[c['name'] for c in parts.values() if target.variable in (c.get('variables') or {})]
            if len(owners)!=1:raise ValueError('Variable owner is ambiguous: '+target.variable)
            instance=owners[0];field='variables';key=target.variable
        else:
            instance=target.instance or target.model;field='parameters';key=target.parameter
            if instance not in parts or key not in parts[instance]['parameters']:
                raise ValueError('No exact native parameter mapping for '+target.key())
        changes.append(dict(instance=instance,field=field,key=key,value=literal))
        mapping.append({'search_key':target.key(),'native':f'{instance}.{field}.{key}','value':literal})
    rendered=NetlistEditor(source).render(targets,[row['parameters'][p.key()] for p in targets])
    actual_input=Path(row['directory'])/'input.net'
    if canonical_netlist(rendered)!=canonical_netlist(actual_input.read_text(encoding='utf-8')):
        raise ValueError('Selected simulator input does not match the declared parameters')
    artifacts=[request_file,cases_file,source_file,actual_input,*[Path(row[k]) for k in ('dataset','raw_sparameters','points_csv')]]
    if job.get('region_source'):artifacts.append(Path(job['region_source']))
    proof={'case':req.case,'evaluation':evaluation.model_dump(),'data_directory':job.get('data_directory',''),
           'hashes':{str(p):file_hash(p) for p in artifacts},'expected_netlist':rendered,
           'raw_sparameters':row['raw_sparameters'],'mapping':mapping}
    review=plan_patch({'snapshot':req.snapshot,'snapshot_sha256':req.snapshot_sha256,
                       'destination':req.destination,'changes':changes},task_directory)
    path=Path(review['review']);data=json.loads(path.read_text(encoding='utf-8'));data['candidate']=proof
    atomic_json(path,data);review.update(review_sha256=file_hash(path),parameter_mapping=mapping,case=req.case)
    return review


def verify_candidate(proof,netlist=None):
    for p,expected in proof['hashes'].items():
        if file_hash(p)!=expected:raise ValueError('Selected search artifact changed: '+p)
    if netlist is not None and canonical_netlist(netlist)!=canonical_netlist(proof['expected_netlist']):
        raise ValueError('Materialized native netlist differs from the selected simulated case')


class MaterializeRequest(ApplyRequest):
    simulation: Literal['none','verify_same_conditions']
    timeout_seconds: int = Field(default=120,ge=1,le=120)


def raw_difference(left,right):
    with np.load(left,allow_pickle=False) as a,np.load(right,allow_pickle=False) as b:
        fa,sa=a['freq_hz'],a['s'];fb,sb=b['freq_hz'],b['s']
        if fa.shape!=fb.shape or not np.array_equal(fa,fb):raise ValueError('Frequency grids differ; no implicit interpolation')
        if sa.shape!=sb.shape or not np.isfinite(sa).all() or not np.isfinite(sb).all():raise ValueError('Invalid S-parameter matrices')
        return {'frequency_points':len(fa),'max_complex_s_difference':float(np.max(np.abs(sa-sb)))}


def materialize(engine,payload,task_directory):
    req=MaterializeRequest.model_validate(payload)
    path=Path(req.review)
    if file_hash(path)!=req.review_sha256:raise ValueError('Candidate review changed')
    review=json.loads(path.read_text(encoding='utf-8'))
    if 'candidate' not in review:raise ValueError('Use plan_candidate first')
    proof=review['candidate'];verify_candidate(proof)
    result=apply_patch(engine,req.model_dump(include={'review','review_sha256'}),task_directory)
    if req.simulation=='verify_same_conditions':
        check=simulate({'snapshot':result['snapshot'],'snapshot_sha256':result['snapshot_sha256'],
                        'authorization':'run_linear_sp_on_copy','evaluation':proof['evaluation'],
                        'data_directory':proof['data_directory'],'timeout_seconds':req.timeout_seconds},task_directory)
        verify_candidate(proof)
        difference=raw_difference(proof['raw_sparameters'],check['raw_sparameters'])
        passed=difference['max_complex_s_difference']<=1e-9
        result.update(simulation=check,simulation_run=True,nonlinear_verified=False,
                      same_condition_comparison={**difference,'absolute_s_tolerance':1e-9,'passed':passed})
        if not passed:result.update(ok=False,status='copy_built_but_simulation_inconsistent')
    result['selected_case']=proof['case']
    atomic_json(Path(result['workspace'])/'.ads-mcp/materialization.json',result)
    return result


class ComparisonRequest(Model):
    left: str
    left_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    right: str
    right_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    left_evidence: str = ''
    right_evidence: str = ''
    left_evidence_sha256: str = ''
    right_evidence_sha256: str = ''


def evidence(path,expected_hash,snapshot_path,snapshot_hash):
    if not expected_hash or file_hash(path)!=expected_hash:raise ValueError('Simulation evidence hash missing or changed')
    d=json.loads(Path(path).read_text(encoding='utf-8'))
    if d.get('kind')!='linear_sp_evidence' or d['snapshot_sha256']!=snapshot_hash or Path(d['snapshot']).resolve()!=Path(snapshot_path).resolve():
        raise ValueError('Simulation evidence does not belong to this exact schematic snapshot')
    for p,h in d['artifacts'].items():
        if file_hash(p)!=h:raise ValueError('Simulation evidence artifact changed: '+p)
    snapshot=load_snapshot(snapshot_path,snapshot_hash)
    if d['netlist_sha256']!=snapshot['netlist_sha256']:raise ValueError('Evidence netlist does not match this snapshot')
    return d


def compare(payload,task_directory):
    import pandas as pd
    req=ComparisonRequest.model_validate(payload)
    a=load_snapshot(req.left,req.left_sha256);b=load_snapshot(req.right,req.right_sha256)
    result={'ok':True,'status':'structural_comparison','changes':changes_between(a,b),
            'left_gaps':a['unreadable'],'right_gaps':b['unreadable'],'performance':'No corresponding compatible simulation evidence supplied'}
    if req.left_evidence and req.right_evidence:
        try:
            ea=evidence(req.left_evidence,req.left_evidence_sha256,req.left,req.left_sha256)
            eb=evidence(req.right_evidence,req.right_evidence_sha256,req.right,req.right_sha256)
            if any(ea.get(k)!=eb.get(k) for k in ('evaluation','contract','solver_fingerprint','dependency_hashes')):
                raise ValueError('Simulation conditions, reference planes, ports, evaluation or solver environment differ')
            diff=raw_difference(ea['result']['raw_sparameters'],eb['result']['raw_sparameters'])
            pa=pd.read_csv(ea['result']['points_csv']);pb=pd.read_csv(eb['result']['points_csv'])
            if not np.array_equal(pa.freq_hz,pb.freq_hz):raise ValueError('Point-table frequency grids differ')
            columns=[c for c in pa.columns if c in pb.columns and c!='freq_hz' and pd.api.types.is_numeric_dtype(pa[c]) and not pd.api.types.is_bool_dtype(pa[c])]
            metrics={c:{'max_absolute_change':float(np.max(np.abs(pb[c].to_numpy()-pa[c].to_numpy()))),
                        'left_min':float(pa[c].min()),'left_max':float(pa[c].max()),
                        'right_min':float(pb[c].min()),'right_max':float(pb[c].max())} for c in columns
                     if np.isfinite(pa[c]).all() and np.isfinite(pb[c]).all()}
            table=pd.DataFrame({'freq_hz':pa.freq_hz})
            for c in columns:
                table[c+'_left']=pa[c];table[c+'_right']=pb[c];table[c+'_delta']=pb[c]-pa[c]
            for side,e in [('left',ea),('right',eb)]:
                with np.load(e['result']['raw_sparameters'],allow_pickle=False) as arrays:
                    for i in range(2):
                        for j in range(2):
                            s=arrays['s'][:,i,j]
                            table[f'S{i+1}{j+1}_real_{side}']=s.real;table[f'S{i+1}{j+1}_imag_{side}']=s.imag
            csv=Path(task_directory)/'comparisons'/(uuid.uuid4().hex+'.csv');csv.parent.mkdir(parents=True,exist_ok=True)
            table.to_csv(csv,index=False,encoding='utf-8-sig')
            result.update(status='structure_and_linear_sp_comparison',performance={**diff,'metrics':metrics,'per_frequency_csv':str(csv)},
                          scope='Compatible saved linear SP evidence only; no HB/nonlinear/measurement inference')
        except (ValueError,KeyError,OSError,TypeError) as exc:
            result['performance']='Structural comparison only: '+str(exc)
    path=Path(task_directory)/'comparisons'/(uuid.uuid4().hex+'.json');atomic_json(path,result)
    return {**result,'report':str(path)}
