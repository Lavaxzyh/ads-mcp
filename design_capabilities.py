"""Capability evidence keyed by the actual ADS install and loaded model libraries."""
from __future__ import annotations

import json
import os
from pathlib import Path

from schematic_flow import atomic_json, digest
from design_snapshots import file_hash

_LINKED_TREES = set()


def inventory(path):
    path=Path(path)
    if not path.exists():return {'missing':str(path)}
    result={}
    def visit(folder):
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.name=='__pycache__':continue
                if entry.is_symlink() or (hasattr(entry,'is_junction') and entry.is_junction()):
                    _LINKED_TREES.add(str(path.resolve()))
                if entry.is_dir(follow_symlinks=False):visit(entry.path)
                elif entry.is_file() and Path(entry.name).suffix not in ('.pyc','.log','.lock'):
                    stat=entry.stat();result[Path(entry.path).relative_to(path).as_posix()]=[stat.st_size,stat.st_mtime_ns]
    visit(path)
    return dict(sorted(result.items()))


def environment(library_paths):
    from ads_engine import get_ads_install_dir
    from environment_watch import memo
    ads=get_ads_install_dir()
    paths=[ads/'bin',ads/'circuit',ads/'custom',ads/'tools/python/packages/keysight/ads/de',*(Path(p) for p in library_paths)]
    return memo.get(paths,lambda:_environment(library_paths),lambda:not any(str(p.resolve()) in _LINKED_TREES for p in paths))


def _environment(library_paths):
    from ads_engine import get_ads_install_dir,get_de
    ads=get_ads_install_dir();de=get_de()
    core={str(p):file_hash(p) for p in (ads/'bin/ads.exe',ads/'bin/hpeesofsim.exe',ads/'bin/hpeesofsim_private.dll') if p.is_file()}
    trees=[ads/'bin',ads/'circuit',ads/'custom',ads/'tools/python/packages/keysight/ads/de']
    trees.extend(Path(p) for p in library_paths)
    content={str(p):inventory(p) for p in trees}
    return {'ads_install':str(ads),'product_version':de.product_version(),'python_de_version':de.__version__,
            'library_paths':sorted(library_paths),'core_hashes':core,
            'dependency_inventory_sha256':digest(json.dumps(content,sort_keys=True).encode()),
            'fingerprint_method':'Core SHA256 plus full path/size/mtime_ns inventory. Process-local reuse requires unchanged Windows recursive notification handles and root identities; unavailable/linked/remote roots or notifications force full scans. Deliberate same-identity/size/timestamp tampering is outside metadata inventory guarantees.'}


def capture(design,directory):
    from ads_engine import get_de
    de=get_de();models={};paths=set();gaps=[]
    for inst in design.instances:
        key=inst.library_name+':'+inst.cell_name
        lib=(getattr(de.Library,'get',None) or de.get_open_library)(inst.library_name)
        paths.add(str(Path(lib.path).resolve()))
        record=models.setdefault(key,{'read_verified':True,'pins':set(),'orientations':set(),
                                     'parameters':{},'observed_forms':{},'write_verification':'Not inferred from metadata; see actual build/revision receipts'})
        record['orientations'].add(inst.orient.name)
        for pin in inst.inst_pins:
            if pin.inst_term.is_numbered:record['pins'].add(pin.inst_term.get_inst_term_number())
        for p in inst.parameters:
            if p.name not in record['parameters']:
                field={'native_type':type(p).__name__,'repeated':hasattr(p,'repeats')}
                try:
                    definition=inst.model_def.parameters[p.name]
                    field['unit_type']=str(definition.unit_type)
                    field['available_forms']=[f.name for f in definition.formset.forms]
                except Exception as exc:
                    field['metadata_gap']=str(exc);gaps.append(key+'.'+p.name)
                record['parameters'][p.name]=field
            forms=[q.form_name for q in p.repeats] if hasattr(p,'repeats') else [p.form_name]
            record['observed_forms'].setdefault(p.name,set()).update(forms)
    for m in models.values():
        m['pins']=sorted(m['pins']);m['orientations']=sorted(m['orientations'])
        m['observed_forms']={k:sorted(v) for k,v in m['observed_forms'].items()}
    env=environment(paths);key=digest(json.dumps(env,sort_keys=True).encode())
    path=Path(directory)/(key+'.json')
    previous=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    # Reuse only identical native metadata, and retain native evidence rather
    # than silently trusting the registry to validate a subsequent edit.
    reused=previous is not None and previous.get('models')==models
    if not reused:
        atomic_json(path,{'schema_version':1,'kind':'ads_capabilities','key':key,'environment':env,
                         'models':models,'metadata_gaps':gaps,
                         'compatibility':['Enum choices are form_name, not arbitrary literal strings',
                             'MeasEqn is ads_simulation with repeated Meas',
                             'VAR compound table duplicates named variables; never leave VAR empty',
                             'Connectivity becomes authoritative after transaction commit',
                             'Outline.points supplies native wire geometry',
                             'Native built-in cells need not have a same-named filesystem directory',
                             'Device replacement may reorder only that instance netlist statement on save'],
                         'scope':'Native introspection verifies types/pins/forms present in this installation; available forms are not all experimentally exercised.'})
    return {'profile':str(path),'key':key,'models':len(models),'reused':reused,'product_version':env['product_version'],'metadata_gaps':gaps}


def check(path):
    profile=json.loads(Path(path).read_text(encoding='utf-8'))
    if profile.get('kind')!='ads_capabilities':raise ValueError('Expected a capability profile')
    actual=environment(profile['environment']['library_paths'])
    key=digest(json.dumps(actual,sort_keys=True).encode())
    valid=key==profile['key']
    return {'ok':True,'status':'verified_current_environment' if valid else 'invalidated_reextract_required',
            'profile':str(Path(path).resolve()),'valid':valid,'recorded_key':profile['key'],'current_key':key,
            'models':len(profile['models']),'product_version':actual['product_version']}
