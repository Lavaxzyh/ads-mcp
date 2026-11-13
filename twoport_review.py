"""Sampled two-port stability and same-plane circuit/Momentum complex-S comparisons."""
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import FiniteFloat, Field
from rf_workflows import StrictModel, fresh_output, sha256, write_json
from sparameter_results import matrix_from_frame


class TwoPort(StrictModel):
    file: str
    reference_ohms: FiniteFloat = Field(gt=0)
    planes: tuple[str,str]
    varblock: str = ""


def load(req):
    path = Path(req.file).resolve(strict=True)
    if any(not label.strip() for label in req.planes):
        raise ValueError("Specify both port reference planes in matrix order")
    if path.suffix.lower()==".s2p":
        import skrf
        network = skrf.Network(str(path))
        f,s = network.f,network.s
        if not np.allclose(network.z0,req.reference_ohms,rtol=1e-9,atol=1e-9):
            raise ValueError("Declared reference impedance differs from Touchstone")
    elif path.suffix.lower()==".ds":
        from keysight.ads import dataset
        with dataset.open(path,mode="r") as ds:
            f,s=matrix_from_frame(ds[req.varblock].to_dataframe())
    elif path.suffix.lower()==".npz":
        with np.load(path,allow_pickle=False) as raw:
            f,s = raw["freq_hz"],raw["s"]
    else:
        raise ValueError("Select .s2p, .ds with varblock, or saved sparameters.npz")
    if f.ndim!=1 or not len(f) or s.shape!=(len(f),2,2) or not np.isfinite(f).all() or not np.isfinite(s).all() or (np.diff(f)<=0).any():
        raise ValueError("Expected one ordered frequency axis with a finite complex 2×2 matrix")
    return f,s


class BiasPoint(TwoPort):
    bias: dict[str,FiniteFloat] = Field(min_length=1)


class Stability(StrictModel):
    before: BiasPoint
    after: BiasPoint | None = None
    start_hz: FiniteFloat = Field(gt=0)
    stop_hz: FiniteFloat = Field(gt=0)
    frequencies_hz: list[FiniteFloat] = Field(default_factory=list)
    output_dir: str = ""


def sample_indices(axis, selected):
    """Accept measured frequencies only, allowing floating-point representation error."""
    indices = []
    for value in selected:
        match = np.flatnonzero(np.isclose(axis, value, rtol=1e-12, atol=1e-6))
        if len(match) != 1:
            raise ValueError(f"Expected one solved sample at {value:g} Hz")
        indices.append(int(match[0]))
    return indices


def ratios(numerator, denominator):
    with np.errstate(divide="ignore",invalid="ignore"):
        return numerator/denominator


def factors(s):
    a,b,c,d=s[:,0,0],s[:,0,1],s[:,1,0],s[:,1,1]
    delta=a*d-b*c
    prod=np.abs(b*c)
    k=ratios(1-abs(a)**2-abs(d)**2+abs(delta)**2,2*prod)
    mu=ratios(1-abs(a)**2,abs(d-delta*a.conj())+prod)
    mu_rev=ratios(1-abs(d)**2,abs(a-delta*d.conj())+prod)
    stable=np.where(prod==0,(abs(a)<1)&(abs(d)<1),(k>1)&(abs(delta)<1))
    return dict(K=k,abs_delta=abs(delta),mu=mu,mu_reverse=mu_rev,unilateral=(prod==0),unconditional=stable)


def stability(payload):
    req = Stability.model_validate(payload)
    if req.after and (req.before.bias!=req.after.bias or req.before.planes!=req.after.planes or req.before.reference_ohms!=req.after.reference_ohms):
        raise ValueError("Damping comparison requires the same declared bias, port order, planes and reference impedance")
    conditions = {"before":req.before}
    if req.after: conditions["after"]=req.after
    selected = req.frequencies_hz
    if selected and (selected != sorted(set(selected)) or selected[0] != req.start_hz or selected[-1] != req.stop_hz):
        raise ValueError("Explicit sample grid must be ascending, distinct and include both requested band edges")
    records, summary, common = {}, {}, None
    for label,cond in conditions.items():
        f,s=load(cond)
        if req.stop_hz<req.start_hz or f[0]>req.start_hz+1 or f[-1]<req.stop_hz-1:
            raise ValueError("Requested frequency band is not covered")
        mask = sample_indices(f,selected) if selected else (f>=req.start_hz-1)&(f<=req.stop_hz+1)
        f,s=f[mask],s[mask]
        if selected:
            f=np.asarray(selected)
        if not len(f) or (common is not None and not np.array_equal(f,common)):
            raise ValueError("Both networks must have identical nonempty in-band samples")
        common=f
        data=factors(s)
        bad=~data["unconditional"]
        summary[label]=dict(all_samples_unconditionally_stable=not bool(bad.any()),
                            first_failing_hz=float(f[bad][0]) if bad.any() else None,
                            source_sha256=sha256(cond.file),worst={})
        for key in ("K","abs_delta","mu","mu_reverse"):
            values=data[key]
            # K has no finite definition for a unilateral network; retain the explicit flag.
            defined=~np.isnan(values) & (~data["unilateral"] if key=="K" else True)
            if defined.any():
                extreme=np.max(values[defined]) if key=="abs_delta" else np.min(values[defined])
                summary[label]["worst"][key]=dict(value=float(extreme) if np.isfinite(extreme) else str(extreme),
                                                   freq_hz=float(f[np.flatnonzero(defined&(values==extreme))[0]]))
            else:
                summary[label]["worst"][key]=None
        records.update({label+"_"+k:a for k,a in data.items()})
    out=fresh_output(req.output_dir,"twoport_stability",[c.file for c in conditions.values()])
    out.mkdir(parents=True,exist_ok=False)
    pd.DataFrame(dict(freq_hz=common,**records)).to_csv(out/"stability.csv",index=False)
    report=dict(ok=True,band_hz=[req.start_hz,req.stop_hz],points=len(common),bias=req.before.bias,planes=req.before.planes,
                reference_ohms=req.before.reference_ohms,
                results=summary,table=str(out/"stability.csv"),interpolation_used=False,
                scope="Small-signal two-port, specified bias and sampled frequencies")
    write_json(out/"report.json",report)
    return report


class Board(StrictModel):
    stackup: str = Field(min_length=1)
    er: FiniteFloat = Field(gt=0)
    height_mm: FiniteFloat = Field(gt=0)
    copper_um: FiniteFloat = Field(gt=0)
    conductivity_s_per_m: FiniteFloat = Field(gt=0)
    loss_tangent: FiniteFloat = Field(ge=0)


class Network(TwoPort):
    substrate: Board
    geometry: str = Field(min_length=1)
    model_source: str


class MomentumComparison(StrictModel):
    circuit: Network
    momentum: Network
    features: list[Literal["bend","tee","open_stub"]] = Field(min_length=1)
    frequencies_hz: tuple[FiniteFloat,FiniteFloat,FiniteFloat]
    momentum_log: str
    output_dir: str = ""


def em_comparison(payload, *, compact=False):
    req = MomentumComparison.model_validate(payload)
    circuit, em = req.circuit, req.momentum
    fields = ("planes","reference_ohms","substrate","geometry")
    if any(getattr(circuit,k) != getattr(em,k) for k in fields):
        raise ValueError("Port order, reference planes, impedance, board and physical geometry declarations must match")
    selected = req.frequencies_hz
    if not 0 < selected[0] < selected[1] < selected[2]:
        raise ValueError("Specify lower edge, center and upper edge in increasing order")
    log = Path(req.momentum_log).resolve(strict=True)
    evidence = [circuit.file, em.file, circuit.model_source, em.model_source, log]
    hashes = {str(Path(p).resolve()):sha256(p) for p in evidence}
    log_text = log.read_text(encoding="utf-8",errors="replace")
    if "Momentum MomEngine" not in log_text or "S-parameter simulation finished" not in log_text:
        raise ValueError("A completed native Momentum S-parameter solver log is required")
    if Path(circuit.file).resolve() == Path(em.file).resolve():
        raise ValueError("Select separate circuit and EM result files")
    f, sc = load(circuit)
    fe, se = load(em)
    if len(f)!=len(fe) or not np.allclose(f,fe,rtol=1e-12,atol=1e-6):
        raise ValueError("Circuit and Momentum must have the same solved frequency grid")
    indices = sample_indices(f, selected)
    lo, hi = indices[0], indices[-1]
    rows, anchors, worst = [], {}, {}
    for i,j in ((0,0),(1,0),(0,1),(1,1)):
        name = f"S{i+1}{j+1}"
        a, b = sc[:,i,j], se[:,i,j]
        errors = abs(b-a)
        peak = lo+int(np.argmax(errors[lo:hi+1]))
        worst[name] = dict(abs_delta=float(errors[peak]),freq_hz=float(f[peak]))
        for k in range(lo,hi+1):
            row = dict(parameter=name,freq_hz=float(f[k]),
                       circuit_real=float(a[k].real),circuit_imag=float(a[k].imag),
                       momentum_real=float(b[k].real),momentum_imag=float(b[k].imag),
                       abs_delta=float(errors[k]))
            if a[k] != 0 and b[k] != 0:
                row["momentum_minus_circuit_db"]=float(20*np.log10(abs(b[k])/abs(a[k])))
                row["phase_delta_deg"]=float(np.angle(b[k]*a[k].conj(),deg=True))
            rows.append(row)
            if k in indices:
                anchors.setdefault(("lower","center","upper")[indices.index(k)],{})[name]=row
    if any(sha256(p)!=h for p,h in hashes.items()):
        raise ValueError("Input changed during comparison")
    out = fresh_output(req.output_dir,"momentum_comparison",evidence)
    out.mkdir(parents=True,exist_ok=False)
    pd.DataFrame(rows).to_csv(out/"complex_s.csv",index=False)
    report=dict(ok=True,points=hi-lo+1,anchors=anchors,worst=worst,table=str(out/"complex_s.csv"),
                contract=req.model_dump(exclude={"output_dir"}),source_hashes=hashes,interpolation_used=False,
                scope="Sampled passive two-port comparison; stackup, geometry and plane correspondence are supplied by the caller")
    write_json(out/"report.json",report)
    if compact:
        return dict(ok=True, points=report["points"], frequencies_hz=selected, planes=circuit.planes,
                    reference_ohms=circuit.reference_ohms, worst=worst,
                    anchor_abs_delta={label:{name:row["abs_delta"] for name,row in values.items()}
                                      for label,values in anchors.items()},
                    interpolation_used=False, scope=report["scope"], table=report["table"],
                    report=str(out/"report.json"))
    return report
