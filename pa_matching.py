"""Four-way comparison on one active HB testbench with explicit matching-network edits."""
import itertools
import re
from pathlib import Path

import pandas as pd
from pydantic import Field, FiniteFloat
from hb_review import prepare_hb
from matching_sweep import NetlistEditor
from rf_workflows import StrictModel, ParameterTarget, sha256, write_json


class ValueChange(StrictModel):
    target: ParameterTarget
    value: FiniteFloat


class NetworkChange(StrictModel):
    reference_plane: str = Field(min_length=1)
    parameters: list[ValueChange] = Field(default_factory=list)
    subcircuit: str = Field(default="", pattern=r"^(?:[A-Za-z_]\w*)?$")
    replacement_file: str = ""


def block(text, name):
    pattern = re.compile(r"(?ims)^[ \t]*define[ \t]+"+re.escape(name)+r"\s*\(([^)]*)\)[^\n]*\n.*?^[ \t]*end[ \t]+"+
                         re.escape(name)+r"[ \t]*(?=\r?$)")
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError("Expected exactly one matching-network definition: "+name)
    return matches[0]


def prepare(netlist_path, contract, operating_points, input_network, output_network, output_dir="",
            data_directory="", dependency_files=None, timeout_seconds=120, stop_on_error=False):
    plan = prepare_hb(netlist_path,contract,operating_points,output_dir=output_dir,data_directory=data_directory,
                      dependency_files=dependency_files,timeout_seconds=timeout_seconds,stop_on_error=stop_on_error)
    sides = [NetworkChange.model_validate(n) for n in (input_network,output_network)]
    protected = {ParameterTarget.model_validate(v).key() for v in
                 [plan["contract"]["frequency_target"],plan["contract"]["drive_target"],*plan["contract"]["bias_targets"]]}
    used, replaced = set(), {}
    for n in sides:
        if bool(n.subcircuit)!=bool(n.replacement_file) or not (n.parameters or n.subcircuit):
            raise ValueError("Each side needs its explicit matching changes")
        for c in n.parameters:
            key = c.target.key()
            if key in used or key in protected:
                raise ValueError("Overlapping or protected testbench parameter: "+key)
            NetlistEditor(plan["text"]).location(c.target)
            used.add(key)
        if n.subcircuit:
            if n.subcircuit in replaced or any(c.target.scope==n.subcircuit for s in sides for c in s.parameters):
                raise ValueError("Matching subcircuit replacements overlap another edit")
            old = block(plan["text"],n.subcircuit)
            path = Path(n.replacement_file).resolve(strict=True)
            text = path.read_text(encoding="utf-8-sig").strip()
            new = block(text,n.subcircuit)
            if new.group(0)!=text or new[1].split()!=old[1].split():
                raise ValueError("Supply only the matching subcircuit definition with identical port order")
            replaced[n.subcircuit] = text
            plan["source_hashes"][str(path)] = sha256(path)
    scenarios = []
    for i,(label, active) in enumerate((("baseline",[]),("input_only",sides[:1]),("output_only",sides[1:]),("combined",sides))):
        text = plan["text"]
        changes = [c for side in active for c in side.parameters]
        for network in active:
            if network.subcircuit:
                old = block(text,network.subcircuit)
                text = text[:old.start()]+replaced[network.subcircuit]+text[old.end():]
        if changes:
            text = NetlistEditor(text).render([c.target for c in changes],[c.value for c in changes])
        scenarios.append(dict(case=i,label=label,parameters={c.target.key():c.value for c in changes},network_text=text))
    if 4*len(operating_points)>256:
        raise ValueError("Four-way HB comparison supports at most 64 operating points per scenario")
    plan.update(workflow="hb_matching",candidates=scenarios,total_cases=4*len(operating_points),
                matching_planes=[n.reference_plane for n in sides])
    return plan


def comparison(out, plan, grid):
    table = {}
    metrics = ("gain_db","pout_dbm","pae_pct")
    for r in grid:
        key = (r["freq_hz"],r["pavs_dbm"],tuple(r["bias_values"]))
        row = table.setdefault(key,dict(freq_hz=key[0],pavs_dbm=key[1],bias_values=key[2]))
        label = plan["candidates"][r["candidate_id"]]["label"]
        row[label+"_status"] = r["status"]
        for metric in metrics:
            row[label+"_"+metric] = r[metric]
    for row in table.values():
        for label,metric in itertools.product(("input_only","output_only","combined"),metrics):
            value,base = row[label+"_"+metric],row["baseline_"+metric]
            row[label+"_delta_"+metric] = value-base if value is not None and base is not None else None
    path = out/"matching_comparison.csv"
    pd.DataFrame(table.values()).to_csv(path,index=False)
    return str(path)
