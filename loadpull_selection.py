"""Rank loads stored at every requested frequency by their worst threshold margin."""
from pathlib import Path
import json

import numpy as np
import pandas as pd
from pydantic import Field, FiniteFloat
from rf_workflows import StrictModel, LoadPullLimits, common_loads, fresh_output, sha256, write_json, clean
from matching_targets import LIMIT_COLUMNS


class CommonSelection(StrictModel):
    analysis_manifest: str
    limits: LoadPullLimits
    margin_scales: dict[str, FiniteFloat] = Field(default_factory=dict)
    output_dir: str = ""


def select(payload, *, compact=False):
    req = CommonSelection.model_validate(payload)
    source = Path(req.analysis_manifest).resolve(strict=True)
    doc = json.loads(source.read_text(encoding="utf-8-sig"))
    csv = Path(doc["all_points_csv"])
    if sha256(csv) != doc["all_points_sha256"]:
        raise ValueError("Stored load-pull CSV fingerprint changed")
    scales = req.margin_scales
    limits = {col: getattr(req.limits,k) for k,col in LIMIT_COLUMNS.items() if getattr(req.limits,k) is not None}
    if not limits or scales.keys()-limits.keys() or any(v <= 0 for v in scales.values()):
        raise ValueError("Specify thresholds and positive margin scales for enabled Pout_dBm, PAE_pct or Gt_dB")
    data = pd.read_csv(csv)
    if not data.numeric_valid.isin([True,False]).all() or not np.isfinite(data.freq_Hz).all():
        raise ValueError("Invalid frequency or validity flag in stored load-pull data")
    groups = []
    details = []
    for hz, frame in data.groupby("freq_Hz", sort=True):
        frame = frame.copy()
        frame["numeric_valid"] &= np.isfinite(frame[list(limits)+["R_ohm","X_ohm"]]).all(axis=1) & (frame.R_ohm>0)
        frame["meets_limits"] = frame.numeric_valid.copy()
        count = {}
        for col, limit in limits.items():
            frame["meets_limits"] &= frame[col] >= limit
            count[col] = int((frame.numeric_valid & (frame[col] < limit)).sum())
        groups.append(frame)
        details.append(dict(freq_hz=float(hz), qualifying=int(frame.meets_limits.sum()), failed_by_metric=count))
    common = common_loads(groups, doc["datasets"], doc["reference_plane"], doc["conditions"])
    # Reindex the per-frequency observations by impedance, independent of dataset row order.
    common = common.set_index(["R_ohm","X_ohm"])
    labels, margins = [], []
    for frame in groups:
        valid = frame[frame.numeric_valid].set_index(["R_ohm","X_ohm"]).reindex(common.index)
        for col, limit in limits.items():
            margins.append((valid[col].to_numpy()-limit)/scales.get(col,1.0))
            labels.append((float(frame.freq_Hz.iloc[0]),col))
    if len(common):
        matrix = np.array(margins)
        critical = matrix.argmin(axis=0)
        common["worst_normalized_margin"] = matrix.min(axis=0)
        common["limiting_freq_hz"] = [labels[i][0] for i in critical]
        common["limiting_metric"] = [labels[i][1] for i in critical]
        common["limiting_margin_native_units"] = [m * scales.get(col,1.0) for m,col in
                zip(common.worst_normalized_margin,common.limiting_metric)]
        common = common.sort_values("worst_normalized_margin", ascending=False, kind="stable")
    common = common.reset_index()
    out = fresh_output(req.output_dir,"common_load_selection",[source,csv])
    out.mkdir(parents=True,exist_ok=False)
    common.to_csv(out/"common_loads.csv",index=False,encoding="utf-8-sig")
    passing = common[common.meets_all_limits]
    result = dict(ok=True, status="qualified" if len(passing) else "no_qualified_common_load" if len(common) else "no_shared_valid_sample",
                  basis="Exact stored R/X pairs at every frequency; no interpolation.", conditions=doc["conditions"],
                  reference_plane=doc["reference_plane"], source_manifest=str(source), source_csv_sha256=doc["all_points_sha256"],
                  thresholds=limits, margin_scales={col:scales.get(col,1.0) for col in limits},
                  qualified=len(passing), shared_valid=len(common), frequency_diagnostics=details,
                  best=common.head(1).to_dict("records"), ranking=str(out/"common_loads.csv"), report=str(out/"selection.json"))
    write_json(result["report"], result)
    if compact:
        result["frequencies"] = len(details)
        result["fewest_qualifying"] = min(details, key=lambda row: row["qualifying"])
        del result["frequency_diagnostics"], result["source_manifest"], result["source_csv_sha256"]
    return clean(result)
