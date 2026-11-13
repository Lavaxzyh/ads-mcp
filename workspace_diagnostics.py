"""Resolve ADS library definitions and explicit model/data file references."""
import os
import re
from pathlib import Path

DEFINITION = re.compile(r'^\s*(DEFINE\s+(\S+)\s+|INCLUDE\s+)(.+?)\s*$', re.I)
DATA_FILE = re.compile(r'"([^"\r\n]+\.(?:s\d+p|mdf|mdif|dat|net|ckt|lib|mdl|csv|ibs))"',re.I)
INCLUDE = re.compile(r'^\s*(?:#include|\.include|include)\s+(?:"([^"]+)"|\'([^\']+)\'|(\S+))',re.I)
FAILURE = re.compile(r'^\s*(?:\*+\s*)?(?:fatal\b|error\b(?!\s*(?:count|summary|[:=]?\s*0\b))|\[error\])',re.I)

def file_references(text):
    for number,line in enumerate(text.splitlines(),1):
        if line.lstrip().startswith((";", "//", "*")):
            continue
        inc = INCLUDE.match(line)
        refs = {next(g for g in inc.groups() if g is not None)} if inc else set()
        refs.update(DATA_FILE.findall(line))
        device = re.match(r'\s*\w+:([^\s]+)', line)
        for raw in sorted(refs):
            yield number, device[1] if device else None, raw

def library_path(raw, parent, ads):
    raw = raw.strip().strip('"')
    expanded = raw.replace("${HPEESOF_DIR}",str(ads)).replace("$HPEESOF_DIR",str(ads))
    if "$" in expanded:
        raise ValueError("Unresolved library variable: "+raw)
    return (parent/expanded).resolve()

def first_solver_error(path):
    if not path.is_file():
        return None
    with path.open(encoding="utf-8",errors="replace") as lines:
        for n,line in enumerate(lines,1):
            if FAILURE.search(line):
                detail = next(lines,"").strip()
                return dict(source=str(path),line=n,message=line.strip(),detail=detail,
                            next_check="Inspect this log line and the corresponding instance in the exported netlist.")
    return None

def inspect(workspace,netlist_path="",data_directory="",solver_log=""):
    from ads_engine import get_ads_install_dir
    ws=Path(workspace).resolve()
    ads=get_ads_install_dir()
    rows=[]
    issues=[]
    seen=set()
    def record(kind,path,source,n,valid,next_check,**context):
        row=dict(kind=kind,path=str(path),source=str(source),line=n,exists=valid,**context)
        rows.append(row)
        if not valid:
            issues.append(dict(**row,next_check=next_check))
    def definitions(source):
        if source in seen or not source.is_file():return
        seen.add(source)
        for n,line in enumerate(source.read_text(encoding="utf-8-sig").splitlines(),1):
            m=DEFINITION.match(line)
            if not m:continue
            try:
                path=library_path(m[3],source.parent,ads)
            except ValueError as exc:
                issues.append(dict(source=str(source),line=n,path=m[3],message=str(exc),
                                   next_check="Resolve the variable in this definition before importing the workspace."))
                continue
            is_library=m[2] is not None
            valid=path.is_dir() if is_library else path.is_file()
            record("library" if is_library else "include",path,source,n,valid,
                   "Check the definition relative to its containing file and locate the referenced ADS library or definition.",
                   library=m[2],location="workspace" if path.is_relative_to(ws) else "ADS" if path.is_relative_to(ads) else "external")
            if valid and not is_library and path.is_relative_to(ws):definitions(path)
    defs=ws/"lib.defs"
    record("workspace_definitions",defs,ws,None,defs.is_file(),"Select the workspace that contains lib.defs.")
    definitions(defs)
    data=Path(data_directory).resolve() if data_directory else ws
    if data_directory:
        record("data_directory",data,ws,None,data.is_dir(),"Locate the model/data directory used by this testbench.")
    if netlist_path:
        net=Path(netlist_path).resolve()
        record("netlist",net,ws,None,net.is_file(),"Export the selected schematic's saved netlist.")
        if net.is_file():
            for n,instance,raw in file_references(net.read_text(encoding="utf-8-sig")):
                path=(data/raw).resolve()
                record("model_or_data",path,net,n,path.is_file(),"Check the filename and data_directory used by this instance.",instance=instance)
    if solver_log:
        log=Path(solver_log).resolve()
        record("solver_log",log,ws,None,log.is_file(),"Select the simulator log for the failed case.")
        if issue:=first_solver_error(log):issues.append(issue)
    return dict(ok=not issues,workspace=str(ws),references=rows,issues=issues,first_error=issues[0] if issues else None,
                scope="Workspace-local DEFINE/INCLUDE chains and literal references in the supplied netlist.")
