"""Draw a common-plane Smith overlay from stored load-pull regions and raw two-port SP."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from pydantic import Field
from matching_runs import raw_frame
from matching_sweep import evaluate_frame
from matching_targets import exact_index, load_regions, region_distance
from rf_workflows import StrictModel, MatchingEvaluation, ImpedanceTarget, fresh_output, sha256, write_json


class Overlay(StrictModel):
    case_directory: str
    region_file: str
    # These additional targets, if provided, are presented impedances at the case port.
    targets: list[ImpedanceTarget] = Field(default_factory=list)
    output_dir: str = ""


def reflection(z, z0):
    return (z-z0)/(z+z0)


def smith_grid(ax):
    angle = np.linspace(0, 2*np.pi, 400)
    ax.plot(np.cos(angle),np.sin(angle),color="#77838c",lw=0.8)
    ax.axhline(0,color="#b8bfc4",lw=0.6)
    x = np.r_[-np.geomspace(100,.001,250),0,np.geomspace(.001,100,250)]
    for r in (.2,.5,1,2,5):
        g = reflection(r+1j*x,1)
        ax.plot(g.real,g.imag,color="#d0d6da",lw=.55)
    for x in (.2,.5,1,2,5):
        for sign in (-1,1):
            g = reflection(np.geomspace(.0001,200,350)+1j*sign*x,1)
            ax.plot(g.real,g.imag,color="#d0d6da",lw=.55)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("Re Γ")
    ax.set_ylabel("Im Γ")


def render(payload):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib import colormaps
    from matplotlib.collections import PolyCollection
    req = Overlay.model_validate(payload)
    case = Path(req.case_directory).resolve(strict=True)
    sp_req = case/"request.json"
    raw = case/"sparameters.npz"
    region_file = Path(req.region_file).resolve(strict=True)
    spec = MatchingEvaluation.model_validate(json.loads(sp_req.read_text(encoding="utf-8"))["evaluation"])
    regions = load_regions(region_file, spec)
    if "reference_ohms" in regions:
        z0 = regions["reference_ohms"]
    else:
        source = Path(regions["source_csv"])
        if sha256(source) != regions["source_csv_sha256"]:
            raise ValueError("Region normalization cannot be verified against its source CSV")
        values = pd.read_csv(source,usecols=["Z0_ohm"]).Z0_ohm.to_numpy()
        if not np.allclose(values,values[0],rtol=1e-9,atol=1e-9):
            raise ValueError("Multiple reference impedances in region input")
        z0 = values[0]
    if not np.isclose(spec.reference_ohms,z0,rtol=1e-9,atol=1e-9):
        raise ValueError("Region and SP reference impedances differ")
    points, _ = evaluate_frame(raw_frame(raw), spec.model_dump())
    f = points.freq_hz.to_numpy()
    z = points.zin_r_ohm.to_numpy()+1j*points.zin_x_ohm.to_numpy()
    f_targets = np.asarray([p.freq_hz for p in req.targets])
    bands = [row for row in regions["frequency_regions"] if spec.start_hz-1 <= row["freq_hz"] <= spec.stop_hz+1]
    if not bands:
        raise ValueError("Region and SP have no shared frequencies in the evaluation band")
    fig = Figure(figsize=(8.6,6.4), layout="constrained")
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    smith_grid(ax)
    mask = (f>=spec.start_hz-1) & (f<=spec.stop_hz+1)
    g = reflection(z[mask],z0)
    ax.plot(g.real,g.imag,color="#232d36",lw=1.9,label="Loaded SP trajectory",zorder=4)
    rows = []
    for idx, region in enumerate(bands):
        hz = region["freq_hz"]
        i = exact_index(f,hz)
        if req.targets:
            target = req.targets[exact_index(f_targets,hz)]
            goal = complex(target.r_ohm,target.x_ohm)
        else:
            goal = complex(points.target_r_ohm.iloc[i],points.target_x_ohm.iloc[i])
        if not np.isfinite(goal):
            raise ValueError(f"Supply a presented target impedance for {hz:g} Hz")
        target_g = reflection(goal,z0)
        actual_g = reflection(z[i],z0)
        color = colormaps["viridis"](.10 + .75*idx/max(1,len(bands)-1))
        samples = np.asarray(region["sample_points"]).reshape(-1,2)
        sg = reflection(samples[:,0]+1j*samples[:,1],z0)
        ax.scatter(sg.real,sg.imag,s=6,color=color,alpha=.35)
        polygons = []
        for poly in region["polygons"]:
            v = np.asarray(poly)
            zs = v[:,0]+1j*v[:,1]
            edge = zs[:,None]+np.linspace(0,1,12,endpoint=False)*(np.roll(zs,-1)-zs)[:,None]
            curve = reflection(edge.ravel(),z0)
            polygons.append(np.c_[curve.real,curve.imag])
        if polygons:
            ax.add_collection(PolyCollection(polygons,facecolors=[color],edgecolors="none",alpha=.15))
        ax.scatter([actual_g.real],[actual_g.imag],color=color,edgecolor="#28333b",s=38,zorder=5,
                   label=f"{hz/1e9:g} GHz")
        ax.scatter([target_g.real],[target_g.imag],color=color,marker="x",s=65,zorder=6)
        distance, evidence, _ = region_distance([z[i].real,z[i].imag],region)
        rows.append(dict(freq_hz=hz,r_ohm=z[i].real,x_ohm=z[i].imag,target_r_ohm=goal.real,target_x_ohm=goal.imag,
                         target_error_ohm=abs(z[i]-goal),region_distance_ohm=distance,region_evidence=evidence))
    worst = max(rows,key=lambda r:r["target_error_ohm"])
    point = reflection(complex(worst["r_ohm"],worst["x_ohm"]),z0)
    ax.annotate(f"{worst['freq_hz']/1e9:g} GHz · max ΔZ {worst['target_error_ohm']:.3g} Ω",xy=(point.real,point.imag),xytext=(14,-26),
                textcoords="offset points",fontsize=9,arrowprops=dict(arrowstyle="-",lw=.8))
    ax.set_title(f"Port {spec.input_port} · Z₀ = {z0:g} Ω")
    ax.legend(loc="upper left",bbox_to_anchor=(1.02,1),fontsize=8,title="● Network    × Target")
    out = fresh_output(req.output_dir,"smith_overlay",[sp_req,raw,region_file])
    out.mkdir(parents=True,exist_ok=False)
    fig.savefig(out/"smith.png",dpi=160)
    pd.DataFrame(rows).to_csv(out/"impedances.csv",index=False)
    report=dict(ok=True,image=str(out/"smith.png"),table=str(out/"impedances.csv"),worst_target=worst,
                worst_region=max(rows,key=lambda r:r["region_distance_ohm"]),impedance_contract=spec.impedance_contract(),
                region_mode=regions["mode"],region_sha256=sha256(region_file),raw_sp_sha256=sha256(raw),
                target_source="explicit presented targets" if req.targets else "frozen SP evaluation")
    write_json(out/"report.json",report)
    return report
