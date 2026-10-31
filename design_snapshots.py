"""Read native designs through filesystem copies, with explicit missing data."""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from pydantic import Field
from schematic_flow import Model, Target, Contract, atomic_json, digest, target_lock


class ExportRequest(Model):
    source: Target
    contract: Contract | None = None


def file_hash(path): return digest(Path(path).read_bytes())


def manifest(root: Path):
    """Hash source content; only known runtime/our receipt files are excluded."""
    root = root.resolve(strict=True)
    if not root.is_dir(): raise ValueError('Source workspace must be a directory')
    out={}
    for folder, dirs, names in os.walk(root):
        dirs[:]=sorted(d for d in dirs if d not in ('.ads-mcp','.git','__pycache__'))
        for name in [*dirs,*names]:
            p=Path(folder)/name
            if p.is_symlink() or p.is_junction():
                raise ValueError('Linked/reparse source requires explicit dependency handling: '+str(p))
        for name in sorted(names):
            p=Path(folder)/name
            if p.suffix.lower() in ('.log','.lock','.cdslck','.pyc') or name.endswith('.lck'):continue
            out[str(p.relative_to(root)).replace('\\','/')]=file_hash(p)
            if len(out)>25000:raise ValueError('Source exceeds the bounded 25000-file snapshot limit')
    if 'lib.defs' not in out:raise ValueError('Source is not an ADS workspace (lib.defs missing)')
    return out


def clone_workspace(source: Path, destination: Path, expected=None):
    source=source.resolve(strict=True);destination=destination.resolve()
    if destination.exists() or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('Copy must be a new independent directory, outside the source workspace')
    before=manifest(source)
    if expected is not None and before != expected:
        raise ValueError('CONCURRENT_SOURCE_EDIT: source changed since review; export and review again')
    # Use relative library definitions in the copy. Reject paths that could
    # silently send a write back to an external user library.
    ads=Path(os.environ['HPEESOF_DIR']).resolve()
    defs={}
    for rel in before:
        if not rel.endswith('.defs'):continue
        text=(source/rel).read_text(encoding='utf-8-sig')
        rendered=[]
        for line in text.splitlines():
            m=re.match(r'\s*(DEFINE\s+\S+\s+|INCLUDE\s+)(.+?)\s*$',line,re.I)
            if not m:rendered.append(line);continue
            raw=m[2].strip().strip('"')
            expanded=raw.replace('$HPEESOF_DIR',str(ads)).replace('${HPEESOF_DIR}',str(ads))
            if '$' in expanded:raise ValueError('Unresolved library variable: '+raw)
            p=Path(expanded)
            p=(source/Path(rel).parent/p).resolve() if not p.is_absolute() else p.resolve()
            if p.is_relative_to(source):
                new=destination/p.relative_to(source)
                # ADS lib.defs does not use shell-style double quotes. Keep
                # workspace-local libraries relative so the copy is portable.
                rendered.append(m[1]+Path(os.path.relpath(new,destination/Path(rel).parent)).as_posix())
            elif p.is_relative_to(ads):rendered.append(line)
            else:raise ValueError('External user library must be explicitly copied before editing: '+str(p))
        defs[rel]='\n'.join(rendered)+'\n'
    destination.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(source,destination,ignore=shutil.ignore_patterns('.ads-mcp','.git','__pycache__','*.lock','*.cdslck','*.lck'))
    # Verify bytes before rebasing, also catching edits while copying.
    if manifest(source)!=before or manifest(destination)!=before:
        raise ValueError('CONCURRENT_SOURCE_EDIT: source changed while copying; candidate is not usable')
    for rel,text in defs.items():(destination/rel).write_text(text,encoding='utf-8')
    return before


@contextmanager
def opened_copy(engine, target, writable=False):
    from ads_engine import get_de
    de=get_de()
    with engine._op():
        if engine.workspace_path is not None or de.workspace_is_open():
            raise RuntimeError('Session owns another workspace; use a private session without closing it')
        owned=False
        try:
            engine.open_workspace(target.workspace);owned=True
            lib=(getattr(de.Library,'get',None) or de.get_open_library)(target.library)
            if not Path(lib.path).resolve().is_relative_to(Path(target.workspace).resolve()):
                raise ValueError('Target library resolves outside the independent copy')
            design=de.db_uu.open_design(f'{target.library}:{target.cell}:schematic',
                       de.db_uu.DesignMode.APPEND if writable else de.db_uu.DesignMode.READ_ONLY)
            yield design
        finally:
            if owned:engine.close_workspace()


def json_value(value):
    # Do not stringify compound values into something that appears writable.
    if value is None or isinstance(value,(str,int,float,bool)):return value
    if isinstance(value,(tuple,list)):return [json_value(v) for v in value]
    raise TypeError('Unsupported native value class: '+type(value).__name__)


def read_design(design, contract=None):
    components=[];unreadable=[];groups=defaultdict(list);shapes=[]
    def read(label,fn):
        try:return fn()
        except Exception as exc:
            unreadable.append({'field':label,'error':f'{type(exc).__name__}: {exc}'})
            return None
    for inst in sorted(design.instances,key=lambda i:i.name):
        row={'name':inst.name,'library':inst.library_name,'cell':inst.cell_name,
             'view':inst.view_name,'at':[float(inst.origin.x),float(inst.origin.y)],
             'orientation':read(inst.name+'.orientation',lambda:inst.orient.name),
             'deactivated':inst.is_deactivated,'shorted':inst.is_deactivated_and_shorted,
             'parameters':{},'variables':{},'pins':[]}
        for p in inst.parameters:
            path=inst.name+'.parameters.'+p.name
            entry={'native_type':type(p).__name__}
            entry['value']=read(path+'.value',lambda:json_value(p.value))
            # A repeated parameter owns per-repeat forms, not one form.
            if hasattr(p,'repeats'):
                entry['forms']=read(path+'.forms',lambda:[q.form_name for q in p.repeats])
            else:entry['form']=read(path+'.form',lambda:p.form_name)
            row['parameters'][p.name]=entry
        if inst.is_var_instance:
            row['variables']=read(inst.name+'.variables',lambda:dict(inst.vars))
        for i,pin in enumerate(inst.inst_pins):
            term=pin.inst_term
            number=read(f'{inst.name}.pins[{i}].number',lambda:(term.term_number if hasattr(term, "term_number") else term.get_inst_term_number()) if term.is_numbered else None)
            ref=inst.name+':'+str(number) if number is not None else inst.name+':unnumbered_'+str(i)
            xy=read(ref+'.point',lambda:[float(pin.snap_point.x),float(pin.snap_point.y)])
            net=term.net.name if term.net is not None else None
            row['pins'].append({'ref':ref,'number':number,'point':xy,'native_net':net})
            groups[net if net is not None else 'unconnected:'+ref].append(ref)
            if number is None:unreadable.append({'field':ref,'error':'Unnumbered pin; cannot address through incremental edit API'})
        components.append(row)
    for i,shape in enumerate(design.shapes):
        kind=type(shape).__name__
        item={'kind':kind,'native_net':shape.net.name if shape.net else None}
        if kind=='Line':
            item['points']=read(f'shapes[{i}].points',lambda:[[float(p.x),float(p.y)] for p in shape.get_outline().points])
        elif hasattr(shape,'text_string'):
            item['text']=read(f'shapes[{i}].text',lambda:shape.text_string)
        else:
            item['geometry_status']='preserved_in_native_copy; not decoded'
            unreadable.append({'field':f'shapes[{i}].geometry','error':'Native '+kind+' geometry is preserved but not decoded'})
        shapes.append(item)
    netlist=read('netlist',lambda:str(design.generate_netlist()))
    result={'schema_version':1,'kind':'schematic_snapshot','components':components,
            'connections':[{'native_net':k,'pins':sorted(v)} for k,v in sorted(groups.items())],
            'shapes':shapes,'simulation_conditions':[c for c in components if c['library']=='ads_simulation'],
            'contract':contract.model_dump() if contract is not None else None,
            'unreadable':unreadable,'complete':not unreadable,
            'semantic_unknowns':[] if contract else ['reference_planes','network_boundary','intended_testbench'],
            'scope':'Saved top-level native schematic; hierarchy internals are retained in the copy, not flattened. Unsaved GUI edits are not observable.'}
    if contract:check_contract(result,contract)
    return result,netlist


def check_contract(snapshot,contract):
    from schematic_flow import hz
    parts={c['name']:c for c in snapshot['components']}
    terms={c['name'] for c in parts.values() if (c['library'],c['cell'])==('ads_simulation','Term')}
    if terms!={p.instance for p in contract.ports}:raise ValueError('Contract does not cover actual ports')
    norm=lambda x:str(x).strip().strip('"')
    for p in contract.ports:
        params=parts[p.instance]['parameters']
        if norm(params['Num']['value'])!=str(p.number) or norm(params['Z']['value'])!=norm(p.impedance):
            raise ValueError('Contract port differs from native design: '+p.instance)
    controllers=[c for c in parts.values() if (c['library'],c['cell'])==('ads_simulation','S_Param')]
    if len(controllers)!=1:raise ValueError('Contract requires exactly one linear SP controller')
    actual=[hz(norm(controllers[0]['parameters'][k]['value'])) for k in ('Start','Stop','Step')]
    if any(abs(a-b)>max(1e-6,abs(b)*1e-12) for a,b in zip(actual,contract.frequency_hz)):
        raise ValueError('Contract frequency grid differs from native design')


def structural(snapshot):
    # Native net names may be renumbered while the electrical graph stays equal.
    data={k:v for k,v in snapshot.items() if k in ('components','connections','contract','shapes')}
    data=json.loads(json.dumps(data))
    for c in data['components']:
        if (c['library'],c['cell'])==('ads_datacmps','VAR'):
            repeated=c['parameters'].get('Variable Value',{})
            # ADS exposes each variable twice. Deduplicate only after proving
            # that the native compound table equals the named variable map.
            values=repeated.get('value')
            if isinstance(values,list) and all(isinstance(v,list) and len(v)==2 for v in values) and dict(values)==c['variables']:
                repeated.pop('value');repeated['value_source']='variables'
        for pin in c['pins']:pin.pop('native_net',None)
        c['pins'].sort(key=lambda p:p['ref'])
    for shape in data.get('shapes',[]):shape.pop('native_net',None)
    data['shapes']=sorted(data.get('shapes',[]),key=lambda s:json.dumps(s,sort_keys=True))
    data['connections']=sorted(sorted(n['pins']) for n in data['connections'])
    return data


def export(engine,payload,task_directory):
    req=ExportRequest.model_validate(payload)
    source=Path(req.source.workspace).resolve(strict=True)
    output=Path(task_directory)/'exports'/uuid.uuid4().hex
    target=req.source.model_copy(update={'workspace':str(output/'readback_wrk')})
    before=clone_workspace(source,Path(target.workspace))
    with opened_copy(engine,target) as design:
        snapshot,netlist=read_design(design,req.contract)
        from design_capabilities import capture
        profile=capture(design,Path(task_directory)/'profiles')
    if manifest(source)!=before:raise ValueError('CONCURRENT_SOURCE_EDIT: source changed during extraction')
    path=output/'snapshot.json'
    snapshot.update(source=req.source.model_dump(),source_files=before,source_unchanged=True,capability_profile=profile)
    if netlist is not None:
        netpath=output/'source.net';netpath.write_text(netlist,encoding='utf-8')
        snapshot.update(netlist=str(netpath),netlist_sha256=file_hash(netpath))
    atomic_json(path,snapshot)
    return {'ok':True,'status':'exported' if snapshot['complete'] else 'exported_with_gaps',
            'snapshot':str(path),'snapshot_sha256':file_hash(path),'instances':len(snapshot['components']),
            'unreadable':snapshot['unreadable'],'semantic_unknowns':snapshot['semantic_unknowns'],
            'source_unchanged':True,'capabilities':profile}
