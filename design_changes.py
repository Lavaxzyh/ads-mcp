"""Review a frozen change set, apply only on a new copy, compare and reopen."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import time
import uuid

from pydantic import Field, model_validator
from schematic_flow import Model, Name, Contract, Target, atomic_json, digest, target_lock
from schematic_templates import Override
from design_snapshots import file_hash, manifest, clone_workspace, opened_copy, read_design, structural


class Replacement(Model):
    instance: Name
    library: Name
    cell: Name
    parameters: dict[str,str|list[str]] = Field(default_factory=dict)
    forms: dict[str,str] = Field(default_factory=dict)


class PatchRequest(Model):
    snapshot: str
    snapshot_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    destination: str
    changes: list[Override] = Field(default_factory=list,max_length=150)
    replacements: list[Replacement] = Field(default_factory=list,max_length=30)
    contract_after: Contract | None = None

    @model_validator(mode='after')
    def check(self):
        if not self.changes and not self.replacements:raise ValueError('Empty change set')
        if not Path(self.destination).is_absolute():raise ValueError('Destination must be absolute')
        keys=[(c.instance,c.field,c.key) for c in self.changes]
        names=[r.instance for r in self.replacements]
        if len(set(keys))!=len(keys) or len(set(names))!=len(names):raise ValueError('Duplicate edits')
        if set(names)&{c.instance for c in self.changes}:raise ValueError('Use replacement parameters for replaced devices')
        return self


class ApplyRequest(Model):
    review: str
    review_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


def load_snapshot(path,expected):
    p=Path(path).resolve(strict=True)
    if file_hash(p)!=expected:raise ValueError('Snapshot artifact changed; review the new export')
    d=json.loads(p.read_text(encoding='utf-8'))
    if d.get('kind')!='schematic_snapshot':raise ValueError('Expected a standard native schematic snapshot')
    return d


def review_changes(req,snapshot):
    parts={c['name']:c for c in snapshot['components']};changes=[]
    for c in req.changes:
        if c.instance not in parts:raise ValueError('Unknown instance: '+c.instance)
        row=parts[c.instance]
        field='parameters' if c.field=='forms' else c.field
        if c.key not in row[field]:raise ValueError('Unknown field: '+c.instance+'.'+c.key)
        old=row[field][c.key]
        if c.field=='forms':old=old.get('form')
        elif c.field=='parameters':old=old['value']
        if c.field in ('forms','variables') and not isinstance(c.value,str):raise ValueError('Form/variable needs a string')
        values=c.value if isinstance(c.value,list) else [c.value]
        if not values or len(values)>128 or any(not v or len(v)>2000 or '\n' in v or '\r' in v for v in values):
            raise ValueError('Edit values must be bounded nonempty single lines')
        changes.append({'instance':c.instance,'field':c.field,'key':c.key,'before':old,'after':c.value})
    for r in req.replacements:
        if r.instance not in parts:raise ValueError('Unknown replacement instance: '+r.instance)
        old=parts[r.instance]
        if old['variables'] or old['library']=='ads_simulation':
            raise ValueError('Controller/VAR replacement is outside bounded device replacement')
        changes.append({'instance':r.instance,'field':'device','before':{k:old[k] for k in ('library','cell')},
                        'after':r.model_dump(exclude={'instance'}),
                        'condition':'Must retain exactly the same pin numbers and native pin locations; no automatic rewiring'})
    return changes


def plan_patch(payload,task_directory, *, owned_in_place=False):
    req=PatchRequest.model_validate(payload)
    snapshot=load_snapshot(req.snapshot,req.snapshot_sha256)
    if not snapshot['complete']:
        raise ValueError('Unreadable native fields prevent complete delta verification; inspect snapshot.unreadable')
    if manifest(Path(snapshot['source']['workspace']))!=snapshot['source_files']:
        raise ValueError('CONCURRENT_SOURCE_EDIT: source changed since export')
    target=Path(req.destination).resolve();source=Path(snapshot['source']['workspace']).resolve()
    if owned_in_place and target!=source:
        raise ValueError('Owned task patch must keep the identical workspace')
    if not owned_in_place and (target.exists() or target.is_relative_to(source) or source.is_relative_to(target)):
        raise ValueError('Select a new independent destination')
    changes=review_changes(req,snapshot)
    review={'schema_version':1,'kind':'owned_patch_review' if owned_in_place else 'copy_patch_review','request':req.model_dump(),
            'source':snapshot['source'],'source_files':snapshot['source_files'],'changes':changes,
            'simulation_authorized':False,'scope':'Task-owned independent schematic only' if owned_in_place else 'New native workspace copy; originals are never opened or saved by ADS'}
    path=Path(task_directory)/'reviews'/(uuid.uuid4().hex+'.json');atomic_json(path,review)
    return {'ok':True,'status':'review_ready','review':str(path),'review_sha256':file_hash(path),
            'changes':changes,'destination':str(target),'source_unchanged':True}


def set_values(inst,parameters,forms):
    known={p.name for p in inst.parameters}
    unknown=(set(parameters)|set(forms))-known
    if unknown:raise ValueError(f'UNADAPTED_PARAMETER: {inst.name}: {sorted(unknown)}')
    if set(parameters)&set(forms):raise ValueError('Parameter and enum form overlap')
    for key,value in parameters.items():
        param=inst.parameters[key]
        if isinstance(value,list):
            if not hasattr(param,'repeats'):raise ValueError('Not a repeated parameter: '+key)
            while len(param.repeats)<len(value):param.repeats.clone()
            while len(param.repeats)>len(value):param.repeats.remove(len(param.repeats)-1)
        param.value=value;inst.invoke_item_parameter_changed_callback(key)
    for key,form in forms.items():
        choices={f.name for f in inst.model_def.parameters[key].formset.forms}
        if form not in choices:raise ValueError(f'UNADAPTED_ENUM: {inst.name}.{key}={form}; available={sorted(choices)}')
        inst.parameters[key].form_name=form;inst.invoke_item_parameter_changed_callback(key)
    inst.update_item_annotation()


def changes_between(before,after):
    a=structural(before);b=structural(after)
    result=[]
    aa={c['name']:c for c in a.pop('components')};bb={c['name']:c for c in b.pop('components')}
    def walk(x,y,path):
        if x==y:return
        if isinstance(x,dict) and isinstance(y,dict):
            for k in sorted(set(x)|set(y)):walk(x.get(k),y.get(k),path+[k])
        else:result.append({'path':path,'before':x,'after':y})
    walk(aa,bb,['components']);walk(a,b,[])
    return result


def verify_delta(before,after,req):
    if not after['complete']:raise ValueError('Native readback has unreadable fields after edit')
    b={c['name']:c for c in before['components']};a={c['name']:c for c in after['components']}
    norm=lambda v:[norm(x) for x in v] if isinstance(v,list) else str(v).strip().strip('"')
    allowed=set()
    for c in req.changes:
        if c.field=='variables':
            actual=a[c.instance]['variables'][c.key]
            allowed.add(('components',c.instance,'variables',c.key))
        else:
            record=a[c.instance]['parameters'][c.key]
            actual=record['form' if c.field=='forms' else 'value']
            allowed.add(('components',c.instance,'parameters',c.key,'value'))
            if c.field=='forms':allowed.add(('components',c.instance,'parameters',c.key,'form'))
        if norm(actual)!=norm(c.value):raise ValueError('Requested value did not read back: '+c.instance+'.'+c.key)
    replaced={r.instance for r in req.replacements}
    for r in req.replacements:
        row=a[r.instance]
        if (row['library'],row['cell'])!=(r.library,r.cell):raise ValueError('Replacement model mismatch')
        # Verify requested values rather than treating the whole instance as unchecked.
        for k,v in r.parameters.items():
            if norm(row['parameters'][k]['value'])!=norm(v):raise ValueError('Replacement parameter mismatch: '+k)
        for k,v in r.forms.items():
            if row['parameters'][k]['form']!=v:raise ValueError('Replacement enum mismatch: '+k)
    delta=changes_between(before,after)
    for change in delta:
        path=tuple(change['path'])
        permitted=path in allowed
        if len(path)>=3 and path[0]=='components' and path[1] in replaced and path[2] in ('library','cell','view','parameters'):
            permitted=True
        if path and path[0]=='contract' and req.contract_after is not None:permitted=True
        if not permitted:raise ValueError('Unexpected structural change: '+'.'.join(path))
    return delta


def same_netlist_after_replacement(before,after,names):
    if before==after:return True
    # ADS puts a newly inserted device at the end in memory, then restores
    # stable instance ordering on reopen. Permit only those exact statements
    # to move; every other statement and every replacement value must match.
    def separate(text):
        statements=text.replace('\\\n',' ').splitlines();moved={};rest=[]
        for line in statements:
            found=re.match(r'^\s*[^\s:]+:([A-Za-z_][A-Za-z0-9_]*)\s',line)
            name=found[1] if found else None
            if name in names:
                if name in moved:raise ValueError('Ambiguous replacement statement in hierarchical netlist')
                moved[name]=line.strip()
            elif line.strip():rest.append(line.strip())
        return rest,moved
    a,am=separate(before);b,bm=separate(after)
    return a==b and am==bm and set(am)==set(names)


def mutate_design(design, req):
    """Shared native edits; callers own isolation, transaction and delta acceptance."""
    from ads_engine import get_ads_install_dir
    for r in req.replacements:
        if not (get_ads_install_dir()/'oalibs/rf'/r.library).is_dir():
            raise ValueError('Replacement requires an installed and discoverable ADS RF model')
        old=design.find_instance(r.instance);origin=(old.origin.x,old.origin.y);orient=old.orient
        old.delete_object()
        inst=design.add_instance(f'{r.library}:{r.cell}:symbol',origin,name=r.instance)
        inst.orient=orient
        set_values(inst,r.parameters,r.forms)
    for c in req.changes:
        inst=design.find_instance(c.instance)
        if c.field=='variables':inst.vars[c.key]=c.value;inst.update_item_annotation()
        else:set_values(inst,{c.key:c.value} if c.field=='parameters' else {},{c.key:c.value} if c.field=='forms' else {})


def apply_patch(engine,payload,task_directory):
    from ads_engine import get_de,get_ads_install_dir
    arg=ApplyRequest.model_validate(payload)
    path=Path(arg.review).resolve(strict=True)
    if file_hash(path)!=arg.review_sha256:raise ValueError('Review artifact changed; generate a new review')
    review=json.loads(path.read_text(encoding='utf-8'));req=PatchRequest.model_validate(review['request'])
    if review.get('candidate'):
        from design_results import verify_candidate
        verify_candidate(review['candidate'])
    before=load_snapshot(req.snapshot,req.snapshot_sha256)
    target=Target.model_validate(review['source']).model_copy(update={'workspace':str(Path(req.destination).resolve())})
    source=Path(before['source']['workspace']);destination=Path(target.workspace)
    contract=req.contract_after or (Contract.model_validate(before['contract']) if before.get('contract') else None)
    report=destination/'.ads-mcp/revision.json'
    from workflow_trace import Trace
    trace=Trace(task_directory)
    with target_lock(destination):
        if destination.exists():raise ValueError('Existing destination is never overwritten')
        clone_workspace(source,destination,review['source_files'])
        try:
            with opened_copy(engine,target,writable=True) as design:
                actual,_=read_design(design,Contract.model_validate(before['contract']) if before.get('contract') else None)
                if structural(actual)!=structural(before):raise ValueError('Native source copy differs from the reviewed snapshot')
                disk_before=manifest(destination)
                de=get_de()
                with de.db.Transaction(design,'Apply reviewed copy changes') as tx:
                    mutate_design(design,req)
                    tx.commit()
                after,netlist=read_design(design,contract)
                delta=verify_delta(before,after,req)
                if review.get('candidate'):verify_candidate(review['candidate'],netlist)
                if manifest(source)!=review['source_files']:raise ValueError('CONCURRENT_SOURCE_EDIT: changed before saving copy')
                if manifest(destination)!=disk_before:raise ValueError('CONCURRENT_COPY_EDIT: destination changed on disk before save')
                design.save_design()
            with trace.span('acceptance','reopen copied schematic and verify persisted delta'):
                with opened_copy(engine,target) as design:
                    saved,saved_netlist=read_design(design,contract)
            if structural(saved)!=structural(after) or not same_netlist_after_replacement(netlist,saved_netlist,{r.instance for r in req.replacements}):
                atomic_json(report.parent/'reopen-diagnostic.json',{'structural_changes':changes_between(after,saved),
                            'before_netlist':netlist,'saved_netlist':saved_netlist})
                raise ValueError('Saved/reopened copy differs from the accepted change; see reopen-diagnostic.json')
            netlist=saved_netlist
            if review.get('candidate'):verify_candidate(review['candidate'],netlist)
            if manifest(source)!=review['source_files']:raise ValueError('CONCURRENT_SOURCE_EDIT: changed during copy verification')
            saved.update(source=target.model_dump(),source_files=manifest(destination),source_unchanged=True,
                         netlist=str(report.parent/'verified.net'),netlist_sha256=digest(netlist.encode()))
            report.parent.mkdir(parents=True,exist_ok=True)
            (report.parent/'verified.net').write_text(netlist,encoding='utf-8')
            saved['netlist_sha256']=file_hash(report.parent/'verified.net')
            atomic_json(report.parent/'snapshot.json',saved)
            result={'ok':True,'status':'completed','workspace':str(destination),'review':str(path),
                    'review_sha256':arg.review_sha256,'snapshot':str(report.parent/'snapshot.json'),
                    'snapshot_sha256':file_hash(report.parent/'snapshot.json'),'changes':delta,
                    'netlist':str(report.parent/'verified.net'),'source_unchanged':True,
                    'connectivity_unchanged':True,'save_reopen_verified':True,'simulation_run':False}
            atomic_json(report,result)
            return result
        except Exception as exc:
            atomic_json(report,{'status':'failed','error':str(exc),'usable':False})
            raise
