"""Operating-point diagnostics and gain, compression and back-off tables for validated HB jobs."""
from collections import defaultdict
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd
from pydantic import Field, FiniteFloat
from rf_workflows import StrictModel, fresh_output, sha256, write_json
from workspace_diagnostics import first_solver_error


class SweepReview(StrictModel):
    job_directory: str
    backoff_db: list[FiniteFloat] = Field(default_factory=list)
    reference_pout_dbm: FiniteFloat | None = None
    output_dir: str = ""


def failure(row, contract):
    directory = Path(row["directory"])
    log = directory/"simulator.log"
    error = row.get("error","")
    issue = first_solver_error(log)
    line = None
    message = error
    status = "execution_failed"
    if log.is_file():
        match = re.compile(r"failed to converge|not converg|no convergence|non[- ]?converg|singular matrix|tolerance violations?",re.I)
        for n,text in enumerate(log.read_text(encoding="utf-8",errors="replace").splitlines(),1):
            if match.search(text):
                status, line, message = "not_converged", n, text.strip()
                break
        else:
            if issue:
                line, message = issue["line"], issue["message"]+" "+issue["detail"]
            elif "native log failed acceptance" in error:
                status = "log_not_accepted"
    if status != "not_converged":
        lower = error.lower()
        if "power balance" in lower or "nonpositive pa powers" in lower or "negative harmonic power" in lower:
            status = "power_check_failed"
        elif "probe" in lower:
            status = "probe_error"
    probes = [contract[k] for k in ("input_voltage","input_current","output_voltage","output_current")]
    probes.extend(s[k] for s in contract["supplies"] for k in ("voltage","current"))
    next_check = {"power_check_failed":"Check probe orientation, drive normalization and all DC supplies.",
                  "probe_error":"Read the named voltage/current probes from this operating point's dataset.",
                  "not_converged":"Review the first convergence message and adjacent accepted drive/bias points."}.get(
                      status,"Inspect the first native error and dataset for this operating point.")
    return dict(status=status,log=str(log) if log.is_file() else None,line=line,message=message,probes=probes,next_check=next_check)


def operating_grid(plan, rows):
    by_case = {r["case"]:r for r in rows}
    count = len(plan["candidates"])*len(plan["operating_points"])
    if len(by_case) != len(rows) or any(not 1 <= n <= count for n in by_case):
        raise ValueError("Duplicate or out-of-grid HB case number")
    result = []
    for candidate in plan["candidates"]:
        for point in plan["operating_points"]:
            number = len(result)+1
            row = by_case.get(number)
            obs = dict(case=number,candidate_id=candidate["case"],freq_hz=point["freq_hz"],pavs_dbm=point["pavs_dbm"],
                       bias_values=point["bias_values"],status="not_run", gain_db=None,pout_dbm=None,pae_pct=None,drain_eff_pct=None)
            if row:
                if row["candidate_id"] != candidate["case"] or row["conditions"] != point:
                    raise ValueError("HB case and requested operating point differ")
                if row["status"]=="done":
                    obs.update(status="accepted",gain_db=row["gain_db"],pout_dbm=row["pout_dbm"],pae_pct=row["pae_pct"],
                               drain_eff_pct=100*row["pout_w"]/row["pdc_w"],log=row.get("simulator_log"))
                else:
                    obs.update(failure(row,plan["contract"]))
            result.append(obs)
    return result


def interpolate(a,b,field,t):
    return a[field]+t*(b[field]-a[field])


def group_metrics(group, backoffs, ref):
    seq = sorted(group,key=lambda r:r["pavs_dbm"])
    valid = [r for r in seq if r["status"]=="accepted"]
    first_bad = next((r for r in seq if r["status"]!="accepted"),None)
    performance = dict(candidate_id=seq[0]["candidate_id"],freq_hz=seq[0]["freq_hz"],bias_values=seq[0]["bias_values"],
                       accepted=len(valid),requested=len(seq),first_unaccepted=first_bad,
                       accepted_pavs_range_dbm=[valid[0]["pavs_dbm"],valid[-1]["pavs_dbm"]] if valid else None,
                       peak_sampled_pae_pct=max((r["pae_pct"] for r in valid),default=None),
                       peak_pae_point=max(valid,key=lambda r:r["pae_pct"]) if valid else None,
                       low_drive_gain_db=None,p1db=None,backoff=[])
    reference = None
    if len(seq)>=2 and all(r["status"]=="accepted" for r in seq[:2]) and abs(seq[0]["gain_db"]-seq[1]["gain_db"]) <= .2:
        reference = (seq[0]["gain_db"]+seq[1]["gain_db"])/2
    performance["low_drive_gain_db"]=reference
    for row in seq:
        row["compression_db"] = reference-row["gain_db"] if reference is not None and row["status"]=="accepted" else None
    performance["p1db_status"] = "low_drive_baseline_unavailable" if reference is None else "not_bracketed"
    for a,b in zip(seq,seq[1:]):
        if a["compression_db"] is None or b["compression_db"] is None:
            continue
        if a["compression_db"] < 1 <= b["compression_db"]:
            t = (1-a["compression_db"])/(b["compression_db"]-a["compression_db"])
            performance.update(p1db_status="linear_dB_interpolation",
                               p1db=dict(pavs_dbm=interpolate(a,b,"pavs_dbm",t),pout_dbm=interpolate(a,b,"pout_dbm",t),
                                         bracket_cases=[a["case"],b["case"]]))
            break
    peak = ref if ref is not None else max((r["pout_dbm"] for r in valid),default=None)
    performance.update(reference_pout_dbm=peak,backoff_reference="specified_output" if ref is not None else "highest_accepted_sampled_output")
    for offset in backoffs:
        target = peak-offset if peak is not None else None
        crossings = []
        if target is not None:
            for r in valid:
                if abs(r["pout_dbm"]-target)<1e-10:
                    crossings.append(dict(pae_pct=r["pae_pct"],drain_eff_pct=r["drain_eff_pct"],bracket_cases=[r["case"]]))
            for a,b in zip(seq,seq[1:]):
                if a["status"]!="accepted" or b["status"]!="accepted":
                    continue
                da,db = a["pout_dbm"]-target,b["pout_dbm"]-target
                if da*db < 0 and min(abs(da),abs(db))>=1e-10:
                    t = -da/(db-da)
                    crossings.append(dict(pae_pct=interpolate(a,b,"pae_pct",t),drain_eff_pct=interpolate(a,b,"drain_eff_pct",t),
                                          bracket_cases=[a["case"],b["case"]]))
        status = "unbracketed_or_gap" if not crossings else "multiple_crossings" if len(crossings)>1 else (
                 "sample" if len(crossings[0]["bracket_cases"])==1 else "linear_in_output_dBm")
        performance["backoff"].append(dict(backoff_db=offset,target_pout_dbm=target,status=status,
                                            **(crossings[0] if len(crossings)==1 else {"pae_pct":None,"drain_eff_pct":None})))
    return performance


def review(payload):
    req = SweepReview.model_validate(payload)
    if any(d < 0 for d in req.backoff_db):
        raise ValueError("Output back-off must be nonnegative")
    job = Path(req.job_directory).resolve(strict=True)
    plan = json.loads((job/"request.json").read_text(encoding="utf-8"))
    source = job/"cases.json"
    rows = json.loads(source.read_text(encoding="utf-8"))
    grid = operating_grid(plan,rows)
    groups = defaultdict(list)
    for r in grid:
        groups[r["candidate_id"],r["freq_hz"],tuple(r["bias_values"])].append(r)
    metrics = [group_metrics(group,req.backoff_db,req.reference_pout_dbm) for group in groups.values()]
    out = fresh_output(req.output_dir,"hb_power_sweep",[source])
    out.mkdir(parents=True,exist_ok=False)
    pd.DataFrame(grid).to_csv(out/"power_sweep.csv",index=False)
    pd.DataFrame([{k:v for k,v in m.items() if k not in ("backoff","peak_pae_point","first_unaccepted","p1db")} |
                  {"p1db_input_dbm":(m["p1db"] or {}).get("pavs_dbm"),"p1db_output_dbm":(m["p1db"] or {}).get("pout_dbm")}
                  for m in metrics]).to_csv(out/"performance.csv",index=False)
    write_json(out/"performance.json",metrics)
    issues = [r for r in grid if r["status"]!="accepted"]
    write_json(out/"diagnostics.json",issues)
    return dict(ok=True, performance=str(out/"performance.json"), table=str(out/"performance.csv"), curve=str(out/"power_sweep.csv"),
                diagnostics=str(out/"diagnostics.json"), points=len(grid), accepted=len(grid)-len(issues),
                first_unaccepted=next(iter(issues),None),first_failure=next((r for r in issues if r["status"]!="not_run"),None),
                source_sha256=sha256(source))
