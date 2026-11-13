"""Fundamental branch phasors, delivered load impedance, relative phase and peak turn-on."""
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import Field, FiniteFloat
from rf_workflows import StrictModel, fresh_output, sha256, write_json


class Phasor(StrictModel):
    real: str = Field(min_length=1)
    imag: str = ""  # Omit for a complex native dataset column.


class Branch(StrictModel):
    plane: str = Field(min_length=1)
    voltage_v: Phasor
    current_a: Phasor
    current_direction: Literal["into_combiner","into_device"]


class Trajectory(StrictModel):
    source: str
    varblock: str = ""
    frequency_hz: str
    drive_dbm: str
    harmonic_order: str
    group_by: list[str] = Field(default_factory=list)
    select: dict[str, str | float] = Field(default_factory=dict)
    carrier: Branch
    peak: Branch
    min_current_a: FiniteFloat = Field(gt=0)
    turn_on_current_a: FiniteFloat = Field(gt=0)
    phasor_scaling: Literal["peak","rms"]
    output_dir: str = ""


def analyze(payload):
    req = Trajectory.model_validate(payload)
    if req.turn_on_current_a < req.min_current_a:
        raise ValueError("Turn-on threshold must reach the current resolution floor")
    path = Path(req.source).resolve(strict=True)
    before = sha256(path)
    if path.suffix.lower()==".ds":
        from keysight.ads import dataset
        with dataset.open(path,mode="r") as ds:
            data = ds[req.varblock].to_dataframe().reset_index()
    elif path.suffix.lower()==".csv":
        data = pd.read_csv(path)
    else:
        raise ValueError("Select an ADS dataset or exported harmonic phasor CSV")
    for col,value in req.select.items():
        data = data[data[col] == value]
    df = data[data[req.harmonic_order]==1].copy()
    if df.empty:
        raise ValueError("No fundamental rows match the selection")
    keys = [*req.group_by,req.frequency_hz,req.drive_dbm]
    if df.duplicated(keys).any():
        raise ValueError("Ambiguous operating points; select one design and group different biases or sweeps")
    result = df[keys].copy()
    result = result.rename(columns={req.frequency_hz:"freq_hz",req.drive_dbm:"drive_dbm"})
    if not np.isfinite(result[["freq_hz","drive_dbm"]]).all().all() or (result.freq_hz<=0).any():
        raise ValueError("Invalid frequency or drive coordinates")
    currents = {}
    for name, branch in (("carrier",req.carrier),("peak",req.peak)):
        def vector(spec):
            return (df[spec.real].to_numpy(float)+1j*df[spec.imag].to_numpy(float)
                    if spec.imag else df[spec.real].to_numpy(complex))
        v = vector(branch.voltage_v)
        i = vector(branch.current_a) * (1 if branch.current_direction=="into_combiner" else -1)
        valid = np.isfinite(i) & np.isfinite(v)
        z = np.full(len(i),complex(np.nan,np.nan))
        resolved = valid & (abs(i)>=req.min_current_a)
        np.divide(v,i,out=z,where=resolved)
        result[name+"_v_real_v"],result[name+"_v_imag_v"]=v.real,v.imag
        result[name+"_i_real_a"],result[name+"_i_imag_a"]=i.real,i.imag
        result[name+"_abs_i_a"]=abs(i)
        result[name+"_r_ohm"],result[name+"_x_ohm"]=z.real,z.imag
        result[name+"_impedance_status"]=np.where(~valid,"invalid_phasor",np.where(resolved,"resolved","current_below_floor"))
        result[name+"_to_combiner_w"]=(.5 if req.phasor_scaling=="peak" else 1)*np.real(v*np.conj(i))
        currents[name] = (i,resolved)
    ic, vc = currents["carrier"]
    ip, vp = currents["peak"]
    result["peak_minus_carrier_current_deg"]=np.where(vc&vp,np.angle(ip*np.conj(ic),deg=True),np.nan)
    result = result.sort_values([*req.group_by,"freq_hz","drive_dbm"])
    onset=[]
    for key,group in result.groupby([*req.group_by,"freq_hz"],dropna=False,sort=False):
        fields=dict(zip([*req.group_by,"freq_hz"],key if isinstance(key,tuple) else [key]))
        current=group.peak_abs_i_a.to_numpy()
        drives=group.drive_dbm.to_numpy()
        hits=np.flatnonzero(np.isfinite(current)&(current>=req.turn_on_current_a))
        record=dict(**fields,status="not_reached",first_sample_dbm=None,bracket_dbm=None)
        if len(hits):
            idx=int(hits[0])
            record.update(status="above_at_first_sample" if not idx else "first_observed_above",first_sample_dbm=drives[idx])
            if idx and np.isfinite(current[idx-1]):
                record["bracket_dbm"]=[drives[idx-1],drives[idx]]
        onset.append(record)
    if sha256(path)!=before:
        raise ValueError("HB source changed during read")
    out = fresh_output(req.output_dir,"doherty_trajectory",[path])
    out.mkdir(parents=True,exist_ok=False)
    result.to_csv(out/"branch_trajectory.csv",index=False)
    report=dict(ok=True,points=len(result),trajectory=str(out/"branch_trajectory.csv"),turn_on=onset,
                source_sha256=before,contract=req.model_dump(exclude={"output_dir"}),current_report_direction="into_combiner",
                scope="Fundamental effective load at the declared branch planes. Turn-on is the specified sampled current criterion.")
    write_json(out/"report.json",report)
    return report
