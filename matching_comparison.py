"""Compare supplied matching designs and model substitutions under one native SP contract."""
from collections import Counter
from pathlib import Path
import re

import numpy as np
import pandas as pd
from pydantic import Field, FiniteFloat, model_validator
from rf_workflows import MatchingEvaluation, StrictModel, fresh_output, sha256, write_json
from matching_sweep import NetlistEditor, prepare_sp_verification
from matching_runs import CaseRunner


class LayoutSize(StrictModel):
    length_mm: FiniteFloat = Field(gt=0)
    width_mm: FiniteFloat = Field(gt=0)
    minimum_trace_mm: FiniteFloat = Field(gt=0)
    minimum_gap_mm: FiniteFloat = Field(gt=0)
    source: str = Field(min_length=1)


class Alternative(StrictModel):
    name: str = Field(min_length=1)
    topology: str = Field(min_length=1)
    netlist_path: str
    layout: LayoutSize


class Manufacturing(StrictModel):
    board: str = Field(min_length=1)
    minimum_trace_mm: FiniteFloat = Field(gt=0)
    minimum_gap_mm: FiniteFloat = Field(gt=0)
    maximum_length_mm: FiniteFloat = Field(gt=0)
    maximum_width_mm: FiniteFloat = Field(gt=0)


class Topologies(StrictModel):
    alternatives: list[Alternative] = Field(min_length=2,max_length=256)
    manufacturing: Manufacturing
    evaluation: MatchingEvaluation
    simulation_budget: int = Field(ge=2,le=256)
    output_dir: str = ""
    timeout_seconds: int = Field(default=120,ge=1,le=120)
    data_directory: str = ""


def topology_plan(**payload):
    req = Topologies.model_validate(payload)
    items = req.alternatives
    counts = Counter(a.topology for a in items)
    if len(counts) < 2 or len(set(counts.values())) != 1 or len(items) > req.simulation_budget:
        raise ValueError("Supply the same number of candidates per topology within the shared solve budget")
    if len({a.name for a in items}) != len(items) or req.evaluation.max_error_ohm is None:
        raise ValueError("Use distinct design names and a common max_error_ohm for sampled matching bandwidth")
    limits = req.manufacturing
    variants = []
    for a in items:
        size = a.layout
        if (size.minimum_trace_mm < limits.minimum_trace_mm or size.minimum_gap_mm < limits.minimum_gap_mm or
                size.length_mm > limits.maximum_length_mm or size.width_mm > limits.maximum_width_mm):
            raise ValueError(f"Declared geometry for {a.name} violates the shared fabrication limits")
        child = prepare_sp_verification(a.netlist_path, req.evaluation, timeout_seconds=req.timeout_seconds,
                                        data_directory=req.data_directory)
        variants.append(dict(name=a.name, topology=a.topology, layout=size.model_dump(), plan=child))
    return batch_plan("topologies", variants, req, manufacturing=limits.model_dump())


class ComponentModel(StrictModel):
    instance: str = Field(pattern=r"^[A-Za-z_]\w*$")
    scope: str = ""
    esr_ohm: FiniteFloat = Field(default=0,ge=0)
    esl_nh: FiniteFloat = Field(default=0,ge=0)
    parallel_c_pf: FiniteFloat = Field(default=0,ge=0)
    subcircuit_file: str = ""
    subcircuit: str = Field(default="",pattern=r"^(?:[A-Za-z_]\w*)?$")
    reverse_pins: bool = False

    @model_validator(mode="after")
    def model(self):
        numeric = any((self.esr_ohm,self.esl_nh,self.parallel_c_pf))
        if bool(self.subcircuit_file) != bool(self.subcircuit) or bool(self.subcircuit) == numeric:
            raise ValueError("Select explicit series ESR/ESL or parallel C, or a two-terminal ADS vendor subcircuit")
        return self


class Parasitics(StrictModel):
    netlist_path: str
    replacements: list[ComponentModel] = Field(min_length=1)
    evaluation: MatchingEvaluation
    resonance: str = Field(default="input_reactance_zero", pattern="^input_reactance_zero$")
    output_dir: str = ""
    timeout_seconds: int = Field(default=120,ge=1,le=120)
    data_directory: str = ""


def substitute(text, replacements):
    editor = NetlistEditor(text)
    used, vendor, dependencies = set(), {}, {}
    for r in replacements:
        identity = (r.scope,r.instance)
        if identity in used:
            raise ValueError(f"Repeated model substitution: {identity}")
        used.add(identity)
        pattern = re.compile(r"^(\s*)([LC]):"+re.escape(r.instance)+r"\s+(\S+)\s+(\S+)(.*)$")
        hits = [(i,m) for i,(line,scope) in enumerate(zip(editor.lines,editor.scopes))
                if scope == r.scope and (m:=pattern.match(line))]
        if len(hits) != 1:
            raise ValueError(f"Expected one ideal two-terminal L/C instance at {identity}")
        index, m = hits[0]
        a,b = m[3],m[4]
        if r.subcircuit:
            path = Path(r.subcircuit_file).resolve(strict=True)
            model = path.read_text(encoding="utf-8-sig")
            sig = re.findall(r"(?im)^\s*define\s+"+re.escape(r.subcircuit)+r"\s*\(([^)]*)\)",model)
            if len(sig) != 1 or len(sig[0].split()) != 2:
                raise ValueError("Vendor model needs an explicit two-terminal ADS subcircuit definition")
            if re.search(r"(?im)^\s*(?:#include|\.include|include)\b",model):
                raise ValueError("Supply a self-contained ADS subcircuit with nested definitions in the same file")
            if path not in vendor:
                own = set(re.findall(r"(?im)^\s*define\s+(\S+)\s*\(",model))
                others = set(re.findall(r"(?im)^\s*define\s+(\S+)\s*\(",text+"\n"+"\n".join(vendor.values())))
                if own & others:
                    raise ValueError("Vendor subcircuit name collision")
                vendor[path] = model
                dependencies[str(path)] = sha256(path)
            p,q = (b,a) if r.reverse_pins else (a,b)
            editor.lines[index] = f"{r.subcircuit}:{r.instance} {p} {q}"
            continue
        prefix = "MCPpar_"+r.instance+"_"
        if re.search(r"\b"+re.escape(prefix),text):
            raise ValueError("Generated parasitic node or instance name already exists")
        lines = []
        node = a
        for kind, value, unit in (("R",r.esr_ohm,"Ohm"),("L",r.esl_nh,"nH")):
            if value:
                internal = prefix+kind+"node"
                lines.append(f"{kind}:{prefix}{kind} {node} {internal} {kind}={value:.17g} {unit}")
                node = internal
        lines.append(f"{m[2]}:{r.instance} {node} {b}{m[5]}")
        if r.parallel_c_pf:
            lines.append(f"C:{prefix}Cp {a} {b} C={r.parallel_c_pf:.17g} pF")
        editor.lines[index] = "\n".join(lines)
    return "\n".join([*vendor.values(),*editor.lines])+"\n", dependencies


def parasitic_plan(**payload):
    req = Parasitics.model_validate(payload)
    source = prepare_sp_verification(req.netlist_path,req.evaluation,timeout_seconds=req.timeout_seconds,
                                     data_directory=req.data_directory)
    text, deps = substitute(source["text"],req.replacements)
    variants = [dict(name="ideal",plan=source),dict(name="realistic",plan={**source,"text":text})]
    return batch_plan("parasitics",variants,req,model_hashes=deps, replacements=[r.model_dump() for r in req.replacements])


def batch_plan(kind, variants, req, **metadata):
    seeds = [v["plan"]["source_netlist"] for v in variants]
    out = fresh_output(req.output_dir,kind,seeds)
    return dict(workflow=kind,source_netlist=seeds[0],source_sha256=variants[0]["plan"]["source_sha256"],
                output_dir=str(out),variants=variants,evaluation=req.evaluation.model_dump(),total_cases=len(variants),
                simulation_budget=getattr(req,"simulation_budget",len(variants)),**metadata)


def sampled_metrics(points, spec):
    mask = points.in_evaluation_band.to_numpy(bool)
    p = points.loc[mask]
    f = p.freq_hz.to_numpy()
    if not np.isclose(f[0],spec.start_hz,rtol=0,atol=1) or not np.isclose(f[-1],spec.stop_hz,rtol=0,atol=1):
        raise ValueError("Both band edges must be solved")
    passed = p.is_scored.to_numpy(bool,copy=True)
    if spec.max_error_ohm is not None:
        passed = passed & (p.error_ohm.to_numpy() <= spec.max_error_ohm)
    else:
        passed[:] = False
    windows = []
    for group in np.split(np.flatnonzero(passed),np.where(np.diff(np.flatnonzero(passed))>1)[0]+1):
        if len(group):
            windows.append([float(f[group[0]]),float(f[group[-1]])])
    x = p.zin_x_ohm.to_numpy()
    flat = bool((x==0).all())
    zeros = [] if flat else [float(v) for v in f[x==0]]
    for i in np.flatnonzero(x[:-1]*x[1:] < 0):
        zeros.append(float(f[i] - x[i]*(f[i+1]-f[i])/(x[i+1]-x[i])))
    return dict(sampled_passbands_hz=windows, widest_sampled_bandwidth_hz=max((b-a for a,b in windows),default=0),
                matched_reference_insertion_loss_db_edges=(-p.s21_db_matched_reference.iloc[[0,-1]]).tolist(),
                worst_matched_reference_insertion_loss_db=float(-p.s21_db_matched_reference.min()),
                input_reactance_zeros_hz=sorted(zeros), resonance_basis="Zero across the sampled band" if flat else
                "Sampled zeros and linear interpolation of sign crossings")


def run_comparison(plan, progress):
    root = Path(plan["output_dir"])
    spec = MatchingEvaluation.model_validate(plan["evaluation"])
    rows, grid, attempts = [], None, 0
    for i,variant in enumerate(plan["variants"],1):
        if any(sha256(p) != h for p,h in plan.get("model_hashes",{}).items()):
            raise ValueError("Vendor model changed since submission")
        child = dict(variant["plan"],output_dir=str(root/f"variant_{i:03d}"),simulation_budget=1,
                     _executor=plan["_executor"],_resume=plan.get("_resume",False))
        runner = CaseRunner(child,[],1,progress,"",False)
        from matching_runs import SimulationBudgetReached
        try:
            case = runner.evaluate([],variant["name"])
        except SimulationBudgetReached:
            case = dict(status="error",error="Interrupted native attempt exhausted this variant's allocation")
        attempts += runner.simulations
        row = dict(case,case=i,name=variant["name"])
        if variant.get("layout"):
            row.update(topology=variant["topology"],layout=variant["layout"],
                       area_mm2=variant["layout"]["length_mm"]*variant["layout"]["width_mm"])
        if row["status"] == "done":
            points = pd.read_csv(row["points_csv"])
            f = points.freq_hz.to_numpy()
            if grid is not None and not np.array_equal(f,grid):
                raise ValueError("SP frequency grids differ between alternatives")
            grid = f
            row.update(sampled_metrics(points,spec))
        rows.append(row)
    write_json(root/"cases.json",rows)
    columns=("name","topology","status","area_mm2","widest_sampled_bandwidth_hz",
             "worst_matched_reference_insertion_loss_db","matched_reference_insertion_loss_db_edges","error")
    pd.DataFrame([{k:r[k] for k in columns if k in r} for r in rows]).to_csv(root/"comparison.csv",index=False)
    complete = all(r["status"]=="done" for r in rows)
    result = dict(completed=complete,status="completed" if complete else "partially_completed",evaluated_cases=len(rows),
                  successful_cases=sum(r["status"]=="done" for r in rows),simulation_attempts=attempts,
                  simulation_budget=plan["simulation_budget"],evaluation=spec.model_dump(),impedance_contract=spec.impedance_contract(),
                  manufacturing=plan.get("manufacturing"),cases_json=str(root/"cases.json"),comparison_csv=str(root/"comparison.csv"),
                  size_basis="Declared candidate layout extents and fabrication dimensions; no layout DRC or EM extraction")
    if plan["workflow"] == "parasitics" and complete:
        a,b = rows
        za,zb = a["input_reactance_zeros_hz"],b["input_reactance_zeros_hz"]
        result["difference"] = dict(ideal_zeros_hz=za,realistic_zeros_hz=zb,
            resonance_shift_hz=zb[0]-za[0] if len(za)==len(zb)==1 else None,
            ideal_passbands_hz=a["sampled_passbands_hz"],realistic_passbands_hz=b["sampled_passbands_hz"],
            insertion_loss_change_db_edges=(np.array(b["matched_reference_insertion_loss_db_edges"])-
                                             a["matched_reference_insertion_loss_db_edges"]).tolist())
    return result
