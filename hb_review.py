"""Single-tone nonlinear HB candidate recheck with an explicit measurement contract.

Each operating point is a separate native solve, eliminating ambiguous nested
dataset sweep axes. No small-signal metric is promoted to nonlinear performance.
"""
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import uuid
from typing import Literal
import numpy as np
import pandas as pd
from pydantic import Field, FiniteFloat, model_validator

from rf_workflows import StrictModel, ParameterTarget, LoadPullLimits, fresh_output, sha256, write_json
from matching_sweep import NetlistEditor, attributes
from rf_executor import record_attempt,attempt_count


class DCProbe(StrictModel):
    voltage: str = Field(min_length=1)
    current: str = Field(min_length=1)
    delivered_current_sign: Literal[-1, 1] = 1


class HBContract(StrictModel):
    frequency_target: ParameterTarget
    drive_target: ParameterTarget
    bias_targets: list[ParameterTarget] = Field(default_factory=list, max_length=4)
    harmonic_order: int = Field(ge=2, le=31)
    input_voltage: str = Field(min_length=1)
    input_current: str = Field(min_length=1)
    input_current_sign: Literal[-1, 1] = 1
    output_voltage: str = Field(min_length=1)
    output_current: str = Field(min_length=1)
    output_current_sign: Literal[-1, 1] = 1
    supplies: list[DCProbe] = Field(min_length=1, max_length=8)
    input_reference_plane: str = Field(min_length=1)
    output_reference_plane: str = Field(min_length=1)
    network_boundary: str = Field(min_length=1)
    model_conditions: str = Field(min_length=1)
    source_termination: str = Field(min_length=1)
    load_termination: str = Field(min_length=1)
    harmonic_terminations: str = Field(min_length=1)
    limits: LoadPullLimits = Field(default_factory=LoadPullLimits)
    stress_voltage: str = ""
    stress_current: str = ""

    @model_validator(mode="after")
    def targets(self):
        if not self.frequency_target.variable or self.frequency_target.scope or self.frequency_target.unit != "Hz":
            raise ValueError("HB frequency_target must be a top-level variable in Hz")
        if not self.drive_target.variable or self.drive_target.unit or self.drive_target.scope:
            raise ValueError("HB drive_target must be a top-level, unitless numeric dBm variable used by dbmtow")
        targets = [self.frequency_target,self.drive_target]+self.bias_targets
        if len({p.key() for p in targets}) != len(targets):
            raise ValueError("HB operating-condition targets must be distinct")
        for name in ("input_reference_plane","output_reference_plane","network_boundary","model_conditions","source_termination","load_termination","harmonic_terminations"):
            if not getattr(self,name).strip():
                raise ValueError("HB reference conditions cannot be blank")
        return self


class HBPoint(StrictModel):
    freq_hz: FiniteFloat = Field(gt=0)
    pavs_dbm: FiniteFloat = Field(ge=-200, le=100)
    bias_values: list[FiniteFloat] = Field(default_factory=list, max_length=4)


class HBMapping(StrictModel):
    source_key: str = Field(min_length=1)
    target: ParameterTarget
    scale: FiniteFloat = 1


def prepare_hb(netlist_path, contract, operating_points, output_dir="", candidates_json="", candidate_ids=None,
               mappings=None, data_directory="", dependency_files=None, timeout_seconds=120, stop_on_error=True):
    contract = HBContract.model_validate(contract)
    points = [HBPoint.model_validate(p) for p in operating_points]
    maps = [HBMapping.model_validate(m) for m in (mappings or [])]
    if not 1 <= len(points) <= 256 or not 1 <= timeout_seconds <= 600:
        raise ValueError("Use 1..256 operating points and timeout_seconds=1..600")
    if any(len(p.bias_values) != len(contract.bias_targets) for p in points):
        raise ValueError("Each point must provide exactly one value for each declared bias target")
    if len({(p.freq_hz,p.pavs_dbm,tuple(p.bias_values)) for p in points}) != len(points):
        raise ValueError("Duplicate HB operating points")
    source = Path(netlist_path).expanduser().resolve(strict=True)
    if source.stat().st_size > 20_000_000:
        raise ValueError("HB netlist exceeds 20 MB")
    before = sha256(source)
    text = source.read_text(encoding="utf-8-sig")
    if sha256(source) != before:
        raise ValueError("HB source changed while reading")
    editor = NetlistEditor(text)
    controllers = [l for l,s in zip(editor.lines,editor.scopes) if not s and re.match(r'\s*HB:',l)]
    if len(controllers) != 1 or any(re.match(r'\s*(S_Param|DC|Tran|Transient|Envelope|Optim|ParamSweep|LSSP|XDB):',l) for l in editor.lines):
        raise ValueError("Use exactly one single-tone HB controller and no other analyses or optimizer")
    hb = controllers[0]
    attrs = {k:hb[a:b].strip() for k,(a,b) in attributes(hb).items()}
    if attrs.get("Freq[1]") != contract.frequency_target.variable or attrs.get("Order[1]") != str(contract.harmonic_order):
        raise ValueError("HB Freq[1]/Order[1] must match the contract; harmonic order is never silently changed")
    if any(k.startswith(("Sweep", "Freq[2]", "Order[2]")) for k in attrs):
        raise ValueError("Use an unswept single-tone template; the workflow owns operating-point sweeps")
    all_targets = [contract.frequency_target,contract.drive_target]+contract.bias_targets+[m.target for m in maps]
    if len({t.key() for t in all_targets}) != len(all_targets):
        raise ValueError("Candidate mappings must not overwrite another target or an operating condition")
    for target in all_targets:
        editor.location(target)
    candidates = [dict(case=0,parameters={})]
    source_hashes = {str(source):before}
    if candidates_json:
        file = Path(candidates_json).resolve(strict=True)
        content = json.loads(file.read_text(encoding="utf-8-sig"))
        if not isinstance(content,list) or not candidate_ids or not maps:
            raise ValueError("Supply cases.json, explicit candidate_ids and parameter mappings")
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("Duplicate candidate ids")
        candidates = []
        for number in candidate_ids:
            hits = [c for c in content if c.get("case") == number and c.get("status") == "done"]
            if len(hits) != 1:
                raise ValueError(f"Candidate {number} is missing, duplicated or not successful")
            c = hits[0]
            if any(m.source_key not in c["parameters"] or not math.isfinite(float(c["parameters"][m.source_key])*m.scale) for m in maps):
                raise ValueError("Candidate mapping source is missing/nonfinite")
            candidates.append(dict(case=number,parameters={m.source_key:float(c["parameters"][m.source_key]) for m in maps}))
        source_hashes[str(file)] = sha256(file)
    elif maps or candidate_ids:
        raise ValueError("Candidate mapping requires candidates_json")
    total = len(candidates)*len(points)
    if total > 256:
        raise ValueError("HB verification is capped at 256 native cases")
    data = str(Path(data_directory).resolve(strict=True)) if data_directory else ""
    if data and not Path(data).is_dir():
        raise ValueError("data_directory must be a local directory")
    for file in dependency_files or []:
        path = Path(file).resolve(strict=True)
        source_hashes[str(path)] = sha256(path)
    out = fresh_output(output_dir,"hb_review",source_hashes)
    return dict(workflow="hb",source_netlist=str(source),source_sha256=before,text=text,source_hashes=source_hashes,
                output_dir=str(out),total_cases=total,contract=contract.model_dump(),operating_points=[p.model_dump() for p in points],
                candidates=candidates,mappings=[m.model_dump() for m in maps],data_directory=data,
                timeout_seconds=timeout_seconds,stop_on_error=stop_on_error,
                dependency_scope="Explicit files hashed; no HB cache. Model and termination descriptions are caller assertions.")


def evaluate_hb_frame(frame, contract, point, log_text):
    c, point = HBContract.model_validate(contract), HBPoint.model_validate(point)
    if frame.index.nlevels != 1 or frame.index.name != "freq":
        raise ValueError("Expected one single-tone HB frequency axis")
    f = frame.index.to_numpy(float)
    expected = np.arange(c.harmonic_order+1)*point.freq_hz
    if len(f) != len(expected) or not np.allclose(f,expected,rtol=1e-9,atol=1):
        raise ValueError("Missing/extra HB harmonics or incorrect fundamental frequency")
    if "Mix[1]" in frame and not np.array_equal(frame["Mix[1]"].to_numpy().real,np.arange(c.harmonic_order+1)):
        raise ValueError("HB harmonic indices do not agree with frequencies")
    bad = re.findall(r'[^\n]*(?:error detected|warning detected|failed to converge|not converge|no convergence|non[- ]?convergen|tolerance violations?|singular matrix|maximum.*iterations)[^\n]*',log_text,re.I)
    if bad or "Simulation finished" not in log_text or not re.search(r'\bHB\s+\w+\[',log_text):
        raise ValueError("HB native log failed acceptance: "+str(bad[:4]))
    def column(name):
        if name not in frame:
            raise ValueError(f"Missing declared HB probe: {name}")
        value = frame[name].to_numpy(complex)
        if not np.isfinite(value).all():
            raise ValueError(f"Nonfinite HB probe: {name}")
        if abs(value[0].imag) > 1e-8*max(1,abs(value[0].real)):
            raise ValueError("DC phasor has a significant imaginary part")
        return value
    vin, iin = column(c.input_voltage), column(c.input_current)*c.input_current_sign
    vout, iout = column(c.output_voltage), column(c.output_current)*c.output_current_sign
    power = 0.5*np.real(vout*np.conj(iout))
    pin = float(0.5*np.real(vin[1]*np.conj(iin[1])))
    pout = float(power[1])
    pdc = sum(float(column(s.voltage)[0].real*column(s.current)[0].real*s.delivered_current_sign) for s in c.supplies)
    pavs = 1e-3*10**(point.pavs_dbm/10)
    if min(pin,pout,pdc) <= 0 or pin > pavs*(1+1e-5):
        raise ValueError("Nonpositive PA powers or delivered input exceeds available source power; check probe directions and drive contract")
    if np.any(power[1:] < -1e-10):
        raise ValueError("Output absorbs negative harmonic power; check direction/load contract")
    pout_dbm = 10*math.log10(pout/1e-3)
    pae = 100*(pout-pin)/pdc
    gain = pout_dbm-point.pavs_dbm
    if pae > 100+1e-5 or sum(power[1:]) > pin+pdc+max(1e-6,abs(pdc)*1e-4):
        raise ValueError("Power balance failed; check supply probes and model")
    limits = c.limits
    feasible = all(v is None or measured >= v for v,measured in ((limits.min_pout_dbm,pout_dbm),(limits.min_pae_pct,pae),(limits.min_gt_db,gain)))
    harmonics = [dict(order=n,power_w=float(power[n]),dbc=10*math.log10(max(float(power[n]),1e-300)/pout)) for n in range(2,len(power))]
    result = dict(pout_w=pout,pout_dbm=pout_dbm,pin_delivered_w=pin,pdc_w=pdc,pae_pct=pae,gain_db=gain,
                  delivered_gain_db=10*math.log10(pout/pin),feasible=feasible,harmonics=harmonics,
                  convergence=dict(solver_accepted=True,complete_harmonic_grid=True,log_reject_patterns=[],
                                   evidence="Native solver returned without error; completed HB log and finite full harmonic grid. Not a harmonic-order convergence study."))
    # ADS complex spectra are peak phasors, DC + Re(sum(Vn exp(j n wt))).
    theta = np.linspace(0,2*np.pi,4096,endpoint=False)
    for name,field in ((c.stress_voltage,"voltage"),(c.stress_current,"current")):
        if name:
            values=column(name)
            wave=values[0].real+np.real(values[1:]@np.exp(1j*np.arange(1,len(values))[:,None]*theta))
            result[field+"_sampled_min"]=float(wave.min())
            result[field+"_sampled_max"]=float(wave.max())
    return result


def run_hb_case(case, request):
    from keysight.edatoolbox.ads import CircuitSimulator
    from keysight.ads import dataset
    options = dict(rel_data_dir=request["data_directory"]) if request["data_directory"] else {}
    CircuitSimulator().run_netlist((case/"input.net").read_text(encoding="utf-8"),output_dir=str(case),working_dir=str(case),
            output_file=str(case/"simulator.log"),netlist_file=str(case/"simulator.ckt"),dataset_name="hb_case",**options)
    files=list(case.glob("*.ds"))
    if len(files)!=1:
        raise ValueError("Expected exactly one fresh HB dataset")
    with dataset.open(files[0], mode="r") as ds:
        frames=[ds[k].to_dataframe() for k in ds.keys() if str(k).endswith(".HB")]
    if len(frames)!=1:
        raise ValueError("Expected exactly one HB varblock")
    frame=frames[0].sort_index()
    result=evaluate_hb_frame(frame, request["contract"],request["point"],(case/"simulator.log").read_text(encoding="utf-8",errors="replace"))
    columns={"freq_hz":frame.index.to_numpy()}
    for name in frame.columns:
        values=frame[name].to_numpy(complex)
        columns[name+".real"]=values.real; columns[name+".imag"]=values.imag
    pd.DataFrame(columns).to_csv(case/"harmonics.csv",index=False,encoding="utf-8-sig")
    return dict(dataset=str(files[0]),harmonics_csv=str(case/"harmonics.csv"),simulator_log=str(case/"simulator.log"),**result)


def compression_summary(rows):
    groups={}
    for row in rows:
        if row["status"]=="done":
            p=row["conditions"]
            groups.setdefault((row["candidate_id"],p["freq_hz"],tuple(p["bias_values"])),[]).append(row)
    records=[]
    for (candidate,freq,bias),group in groups.items():
        ordered=sorted(group,key=lambda r:r["conditions"]["pavs_dbm"])
        record=dict(candidate_id=candidate,freq_hz=freq,bias_values=list(bias),p1db_input_dbm=None,
                    interpretation="Relative to the lowest two sampled-drive gains, only if these agree within 0.2 dB; no extrapolation")
        if len(ordered)<3 or abs(ordered[0]["gain_db"]-ordered[1]["gain_db"])>0.2:
            record["status"]="small_signal_baseline_not_established"
        else:
            reference=(ordered[0]["gain_db"]+ordered[1]["gain_db"])/2
            record.update(status="not_bracketed",reference_gain_db=reference)
            for a,b in zip(ordered,ordered[1:]):
                ga,gb=reference-a["gain_db"],reference-b["gain_db"]
                if ga<1<=gb:
                    pa,pb=a["conditions"]["pavs_dbm"],b["conditions"]["pavs_dbm"]
                    record.update(status="interpolated_crossing",p1db_input_dbm=pa+(pb-pa)*(1-ga)/(gb-ga),bracket_dbm=[pa,pb])
                    break
        records.append(record)
    return records


def run_hb(plan, progress=lambda p:None):
    out=Path(plan["output_dir"]); out.mkdir(parents=True,exist_ok=plan.get("_resume",False))
    (out/"source.net").write_text(plan["text"],encoding="utf-8")
    write_json(out/"request.json",{k:v for k,v in plan.items() if k!="text" and not k.startswith("_")})
    checkpoint_dir=out/"checkpoints"; checkpoint_dir.mkdir(exist_ok=plan.get("_resume",False))
    c=HBContract.model_validate(plan["contract"])
    maps=[HBMapping.model_validate(m) for m in plan["mappings"]]
    editor=NetlistEditor(plan["text"])
    rows=[]; reused=0; attempts=0
    for number,(candidate,point) in enumerate(itertools.product(plan["candidates"],plan["operating_points"]),1):
        progress(dict(completed=len(rows),total=plan["total_cases"],phase="hb",current_case=number))
        if any(not Path(p).is_file() or sha256(p)!=digest for p,digest in plan["source_hashes"].items()):
            raise ValueError("HB source/dependency changed; no further simulation dispatched")
        targets=[c.frequency_target,c.drive_target]+c.bias_targets+[m.target for m in maps]
        values=[point["freq_hz"],point["pavs_dbm"]]+point["bias_values"]+[candidate["parameters"][m.source_key]*m.scale for m in maps]
        rendered=editor.render(targets,values)
        digest=hashlib.sha256(rendered.encode()).hexdigest()
        saved=checkpoint_dir/f"{number:04d}.json"
        case=out/f"case_{number:04d}"
        if plan.get("_resume") and saved.exists():
            checkpoint=json.loads(saved.read_text(encoding="utf-8"))
            if checkpoint["rendered_sha256"]!=digest or any(not Path(p).is_file() or sha256(p)!=h for p,h in checkpoint["hashes"].items()):
                raise ValueError("HB checkpoint failed integrity validation")
            row=dict(checkpoint["row"],resumed=True); reused+=1
        else:
            if case.exists() and plan.get("_resume"):
                if case.resolve().parent!=out.resolve():
                    raise ValueError("Unsafe HB case path")
                case.rename(case.with_name(case.name+".interrupted_"+uuid.uuid4().hex[:8]))
            row=dict(case=number,status="error",candidate_id=candidate["case"],parameters=candidate["parameters"],conditions=point,directory=str(case))
            record_attempt(out,number,digest)
            attempts+=1
            try:
                result=plan["_executor"].execute(case,rendered,{},plan["data_directory"],plan["timeout_seconds"],
                                               extra=dict(analysis="hb",contract=plan["contract"],point=point))
                row.update(status="done",**result)
            except Exception as exc:
                row["error"]=f"{type(exc).__name__}: {exc}"
            artifacts=[row[k] for k in ("dataset","harmonics_csv","simulator_log") if k in row]
            write_json(saved,dict(row=row,rendered_sha256=digest,hashes={p:sha256(p) for p in artifacts}))
        rows.append(row)
        progress(dict(completed=len(rows),total=plan["total_cases"],phase="hb",last_status=row["status"]))
        if row["status"]=="error" and plan["stop_on_error"]:
            break
    write_json(out/"cases.json",rows)
    good=sorted((r for r in rows if r["status"]=="done"),key=lambda r:(not r["feasible"],-r["pae_pct"],-r["pout_dbm"]))
    unchanged=all(Path(p).is_file() and sha256(p)==h for p,h in plan["source_hashes"].items())
    complete=len(good)==plan["total_cases"] and unchanged
    candidate_summary=[]
    for candidate in plan["candidates"]:
        selected=[r for r in rows if r["candidate_id"]==candidate["case"]]
        accepted=len(selected)==len(plan["operating_points"]) and all(r["status"]=="done" and r["feasible"] for r in selected)
        valid=[r for r in selected if r["status"]=="done"]
        candidate_summary.append(dict(candidate_id=candidate["case"],passes_all_points=accepted,
            worst_pout_dbm=min((r["pout_dbm"] for r in valid),default=None),worst_pae_pct=min((r["pae_pct"] for r in valid),default=None),
            worst_gain_db=min((r["gain_db"] for r in valid),default=None)))
    candidate_summary.sort(key=lambda r:(not r["passes_all_points"],-(r["worst_pae_pct"] if r["worst_pae_pct"] is not None else -1e99)))
    summary=dict(status="completed" if complete else "partially_completed",completed=complete,output_dir=str(out),
        evaluated_cases=len(rows),successful_cases=len(good),failed_cases=len(rows)-len(good),simulation_attempts=attempt_count(out),
        new_simulation_attempts=attempts,resumed_cases=reused,feasible_cases=sum(r["feasible"] for r in good),
        best_case=good[0] if good else None,candidate_review=candidate_summary,compression=compression_summary(rows),
        source_netlist_unchanged=unchanged,audit_passed=complete,cases_json=str(out/"cases.json"),contract=plan["contract"],
        scope="Single-tone nonlinear HB at sampled operating points; source/model/reference conditions are caller asserted; no measured performance or harmonic-order convergence guarantee")
    write_json(out/"summary.json",summary)
    return summary
