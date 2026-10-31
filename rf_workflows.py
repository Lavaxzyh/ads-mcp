"""Read-only load-pull analysis and shared, explicit RF workflow contracts."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoadPullLimits(StrictModel):
    min_pout_dbm: FiniteFloat | None = None
    min_pae_pct: FiniteFloat | None = None
    min_gt_db: FiniteFloat | None = None


class ParameterTarget(StrictModel):
    variable: str = Field(default="", pattern=r"^$|^[A-Za-z_]\w*$")
    instance: str = Field(default="", pattern=r"^$|^[A-Za-z_]\w*$")
    model: str = Field(default="", pattern=r"^$|^[A-Za-z_]\w*$")
    parameter: str = Field(default="", pattern=r"^$|^[A-Za-z_]\w*$")
    scope: str = Field(default="", pattern=r"^$|^[A-Za-z_]\w*$")
    unit: Literal["", "Ohm", "H", "nH", "pH", "F", "uF", "nF", "pF", "fF", "m", "mm", "um", "mil", "Hz", "MHz", "GHz", "V", "mV", "A", "mA"] = ""

    @model_validator(mode="after")
    def target(self):
        if sum(map(bool, (self.variable, self.instance, self.model))) != 1 or bool(self.parameter) == bool(self.variable):
            raise ValueError("Select variable OR instance+parameter OR model+parameter")
        return self

    def key(self):
        name = self.variable or (self.instance or 'model:' + self.model) + '.' + self.parameter
        return f"{self.scope or 'top'}/{name}"


class SweepParameter(ParameterTarget):
    values: list[FiniteFloat] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def unique_values(self):
        if len(set(self.values)) != len(self.values):
            raise ValueError("Duplicate sweep values")
        return self


class ImpedanceTarget(StrictModel):
    freq_hz: FiniteFloat = Field(gt=0)
    r_ohm: FiniteFloat = Field(gt=0)
    x_ohm: FiniteFloat


class MetricCriterion(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    metric: Literal["impedance_error_ohm", "input_return_loss_db", "output_return_loss_db", "matched_s21_db", "transducer_gain_db", "operating_gain_db"]
    aggregate: Literal["min", "max", "mean", "rms", "ripple"] = "min"
    start_hz: FiniteFloat | None = Field(default=None, gt=0)
    stop_hz: FiniteFloat | None = Field(default=None, gt=0)
    lower: FiniteFloat | None = None
    upper: FiniteFloat | None = None
    objective: Literal["minimize", "maximize", "target", "none"] = "none"
    target: FiniteFloat | None = None
    scale: FiniteFloat = Field(default=1, gt=0)
    weight: FiniteFloat = Field(default=1, gt=0)

    @model_validator(mode="after")
    def valid_criterion(self):
        if (self.start_hz is None) != (self.stop_hz is None) or self.start_hz is not None and self.start_hz > self.stop_hz:
            raise ValueError("Metric band requires both ordered start_hz and stop_hz")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("Metric lower must not exceed upper")
        if (self.objective == "target") != (self.target is not None):
            raise ValueError("Only target objectives require a target")
        if self.objective == "none" and self.lower is None and self.upper is None:
            raise ValueError("A criterion needs a bound or an objective")
        return self


class MatchingEvaluation(StrictModel):
    start_hz: FiniteFloat = Field(gt=0)
    stop_hz: FiniteFloat = Field(gt=0)
    reference_ohms: FiniteFloat = Field(gt=0)
    load_r_ohm: FiniteFloat = Field(gt=0)
    load_x_ohm: FiniteFloat
    target_r_ohm: FiniteFloat | None = Field(default=None, gt=0)
    target_x_ohm: FiniteFloat | None = None
    target_table: list[ImpedanceTarget] = Field(default_factory=list, max_length=10001)
    target_interpolation: Literal["exact", "linear"] = "exact"
    region_file: str = ""
    input_port: Literal[1, 2]
    reference_plane: str = Field(min_length=1)
    max_error_ohm: FiniteFloat | None = Field(default=None, ge=0)
    source_r_ohm: FiniteFloat | None = Field(default=None, gt=0)
    source_x_ohm: FiniteFloat | None = None
    criteria: list[MetricCriterion] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def band(self):
        if (self.source_r_ohm is None) != (self.source_x_ohm is None):
            raise ValueError("Supply both source_r_ohm and source_x_ohm")
        if any(c.metric == "transducer_gain_db" for c in self.criteria) and self.source_r_ohm is None:
            raise ValueError("Loaded transducer gain requires an explicit source impedance")
        if len({c.name for c in self.criteria}) != len(self.criteria):
            raise ValueError("Metric names must be unique")
        if self.stop_hz < self.start_hz or not self.reference_plane.strip():
            raise ValueError("Specify an ordered band and a meaningful reference plane")
        constant = self.target_r_ohm is not None or self.target_x_ohm is not None
        if sum((constant, bool(self.target_table), bool(self.region_file))) != 1:
            raise ValueError("Choose exactly one constant target, target_table, or region_file")
        if constant and (self.target_r_ohm is None or self.target_x_ohm is None):
            raise ValueError("Constant target needs both target_r_ohm and target_x_ohm")
        if self.target_table:
            freqs = [p.freq_hz for p in self.target_table]
            if any(b - a <= 2 for a, b in zip(freqs, freqs[1:])):
                raise ValueError("Target frequencies must be increasing and separated by more than 2 Hz")
            if self.target_interpolation == "linear" and (len(freqs) < 2 or freqs[0] > self.start_hz or freqs[-1] < self.stop_hz):
                raise ValueError("Linear target interpolation needs >=2 points covering the band; no extrapolation")
        elif self.target_interpolation != "exact":
            raise ValueError("Frequency interpolation is available only for target_table, not load-pull regions")
        return self


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def fresh_output(output_dir, prefix, sources):
    from ads_engine import get_workroot
    out = (Path(output_dir).expanduser() if output_dir else get_workroot() / f"{prefix}_{uuid.uuid4().hex[:12]}").resolve()
    if out.exists():
        raise FileExistsError(f"Use a new output directory; refusing to overwrite {out}")
    for source in sources:
        for parent in Path(source).resolve().parents:
            if (parent / "lib.defs").is_file() and out.is_relative_to(parent):
                raise ValueError("Output must be outside the source ADS workspace")
    # Creating a new sibling is fine; existing directories are never cleared.
    return out


def common_loads(frames, summaries, reference_plane, conditions):
    if len(frames) < 2:
        raise ValueError("Common-load analysis needs at least two datasets")
    if not reference_plane.strip() or not conditions.strip():
        raise ValueError("Common-load analysis requires reference_plane and conditions (model, source/harmonic terminations)")
    # These conditions are measurable in the files. Remaining conditions are
    # caller-supplied provenance, never reported as independently verified.
    for col in ("Pavs_dBm", "VDD_V", "VGG_V", "Z0_ohm"):
        values = np.concatenate([f[col].to_numpy(float) for f in frames])
        if not np.isfinite(values).all() or not np.allclose(values, values[0], rtol=1e-9, atol=1e-9):
            raise ValueError(f"Cannot combine different or invalid {col}; analyze these datasets separately")
    if len({s["harmonic_order"] for s in summaries}) != 1:
        raise ValueError("Cannot combine different harmonic orders")
    joined = None
    for i, frame in enumerate(frames):
        valid = frame.loc[frame.numeric_valid, ["R_ohm", "X_ohm", "Pout_dBm", "PAE_pct", "Gt_dB", "meets_limits"]].copy()
        if valid.duplicated(["R_ohm", "X_ohm"]).any():
            raise ValueError("Ambiguous repeated impedance in a dataset")
        valid = valid.rename(columns={c: f"{c}_{i}" for c in valid.columns if c not in ("R_ohm", "X_ohm")})
        joined = valid if joined is None else joined.merge(valid, on=["R_ohm", "X_ohm"], how="inner", validate="one_to_one")
    result = joined[["R_ohm", "X_ohm"]].copy()
    for column in ("Pout_dBm", "PAE_pct", "Gt_dB"):
        result["worst_" + column] = joined[[f"{column}_{i}" for i in range(len(frames))]].min(axis=1)
    result["meets_all_limits"] = joined[[f"meets_limits_{i}" for i in range(len(frames))]].all(axis=1)
    return result.sort_values(["meets_all_limits", "worst_PAE_pct", "worst_Pout_dBm"], ascending=[False, False, False], kind="stable")


def analyze_loadpull(datasets, output_dir="", limits=None, compare_common=False, reference_plane="", conditions=""):
    from loadpull_exporter import extract
    limits = LoadPullLimits.model_validate(limits or {})
    if not 1 <= len(datasets) <= 32:
        raise ValueError("Supply 1..32 .ds datasets")
    paths = [Path(p).expanduser().resolve(strict=True) for p in datasets]
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate source dataset")
    out = fresh_output(output_dir, "loadpull", paths)
    frames, summaries = [], []
    for i, path in enumerate(paths, 1):
        frame, summary = extract(path)
        frame.insert(0, "dataset_id", f"dataset_{i:03d}")
        frame["meets_limits"] = frame.numeric_valid.copy()
        for key, column in [("min_pout_dbm", "Pout_dBm"), ("min_pae_pct", "PAE_pct"), ("min_gt_db", "Gt_dB")]:
            limit = getattr(limits, key)
            if limit is not None:
                frame["meets_limits"] &= frame[column] >= limit
        summary.update(dataset_id=f"dataset_{i:03d}", qualifying_points=int(frame.meets_limits.sum()))
        frames.append(frame)
        summaries.append(summary)
    common = common_loads(frames, summaries, reference_plane, conditions) if compare_common else None
    all_points = pd.concat(frames, ignore_index=True)
    # Validate everything before reserving the output. Inputs remain read-only.
    if any(sha256(p) != s["sha256"] for p, s in zip(paths, summaries)):
        raise ValueError("Source dataset changed during analysis")
    out.mkdir(parents=True, exist_ok=False)
    all_points.to_csv(out / "all_points.csv", index=False, encoding="utf-8-sig")
    all_points.loc[all_points.meets_limits].to_csv(out / "qualified_points.csv", index=False, encoding="utf-8-sig")
    for frame, summary in zip(frames, summaries):
        frame.to_csv(out / f"{summary['dataset_id']}.csv", index=False, encoding="utf-8-sig")
    if common is not None:
        common.to_csv(out / "common_loads.csv", index=False, encoding="utf-8-sig")
    result = dict(output_dir=str(out), points=len(all_points), numeric_valid_points=int(all_points.numeric_valid.sum()),
                  qualifying_points=int(all_points.meets_limits.sum()), datasets=summaries,
                  all_points_csv=str(out / "all_points.csv"), all_points_sha256=sha256(out / "all_points.csv"),
                  qualified_csv=str(out / "qualified_points.csv"),
                  common_loads_csv=str(out / "common_loads.csv") if common is not None else None,
                  common_points=len(common) if common is not None else None,
                  common_qualified_points=int(common.meets_all_limits.sum()) if common is not None else None,
                  top_common_loads=common.loc[common.meets_all_limits].head(10).to_dict("records") if common is not None else [],
                  limits=limits.model_dump(), reference_plane=reference_plane, conditions=conditions,
                  conditions_source="caller; model/source/harmonic conditions are not independently verified",
                  source_hashes_unchanged=True, hb_convergence_verified=False,
                  comparison="Exact R/X intersection; no interpolation or deduplication; rank qualified common loads by worst PAE then worst Pout",
                  scope="HB1Tone_LoadPull template with original probe names/directions only. All stored points are retained; numeric validity is not HB convergence or device safety.",
                  created_at=datetime.now(timezone.utc).isoformat())
    write_json(out / "manifest.json", result)
    return clean(result)
