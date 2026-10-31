"""Explicit SP power metrics and feasible-first, scaled multi-metric ranking.

The general constrained-dominance rule is also documented in Optuna's NSGA-II
constraint evaluation. No optimizer dependency or claim of a global optimum.
"""
import math
import numpy as np


def power_metrics(s, gamma_in, gamma_load, spec):
    denominator = np.abs(1 - s[:, 1, 1] * gamma_load) ** 2
    load_transfer = np.abs(s[:, 1, 0]) ** 2 * (1 - abs(gamma_load) ** 2) / denominator
    accepted = 1 - np.abs(gamma_in) ** 2
    operating = np.divide(load_transfer, accepted, out=np.full(len(s), np.nan), where=accepted > 1e-14)
    result = dict(operating_gain_db=10 * np.log10(np.maximum(operating, 1e-30)))
    if spec.source_r_ohm is not None:
        zs = complex(spec.source_r_ohm, spec.source_x_ohm)
        gs = (zs - spec.reference_ohms) / (zs + spec.reference_ohms)
        total = np.abs(1 - gamma_in * gs) ** 2
        gt = np.divide(load_transfer * (1 - abs(gs) ** 2), total, out=np.full(len(s), np.nan), where=total > 1e-28)
        result["transducer_gain_db"] = 10 * np.log10(np.maximum(gt, 1e-30))
    return result


def evaluate_criteria(points, spec, scored, s22):
    values = dict(impedance_error_ohm=points.error_ohm.to_numpy(),
                  input_return_loss_db=-points.s11_db_matched_reference.to_numpy(),
                  output_return_loss_db=-20 * np.log10(np.maximum(np.abs(s22), 1e-15)),
                  matched_s21_db=points.s21_db_matched_reference.to_numpy())
    for name in ("operating_gain_db", "transducer_gain_db"):
        if name in points:
            values[name] = points[name].to_numpy()
    records, objectives, violation = [], [], 0.0
    f = points.freq_hz.to_numpy()
    for c in spec.criteria:
        lo, hi = (spec.start_hz, spec.stop_hz) if c.start_hz is None else (c.start_hz, c.stop_hz)
        mask = (f >= lo - 1) & (f <= hi + 1)
        if c.metric == "impedance_error_ohm":
            mask &= scored
        if f[0] > lo + 1 or f[-1] < hi - 1 or not mask.any():
            raise ValueError(f"Metric {c.name} has no complete sampled band")
        data = values[c.metric][mask]
        if not np.isfinite(data).all():
            raise ValueError(f"Metric {c.name} contains undefined/nonfinite power or impedance")
        value = float({"min": np.min, "max": np.max, "mean": np.mean,
                       "rms": lambda x: np.sqrt(np.mean(x*x)), "ripple": np.ptp}[c.aggregate](data))
        deficit = (max(0., c.lower-value) if c.lower is not None else 0.) + (max(0., value-c.upper) if c.upper is not None else 0.)
        v = deficit / c.scale
        violation += v
        objective = None
        if c.objective != "none":
            objective = (abs(value-c.target) if c.objective == "target" else value if c.objective == "minimize" else -value) / c.scale
            objectives.append(objective)
        records.append(dict(name=c.name, metric=c.metric, aggregate=c.aggregate, value=value,
                            lower=c.lower, upper=c.upper, normalized_violation=v, passes=v <= 1e-10,
                            objective=objective, weight=c.weight, sampled_points=int(mask.sum()), band_hz=[lo, hi]))
    if spec.max_error_ohm is not None:
        violation += max(0., float(points.error_ohm.to_numpy()[scored].max()) - spec.max_error_ohm)
    if spec.region_file and spec.max_error_ohm is None:
        violation += float(points.error_ohm.to_numpy()[scored].max())
    return dict(criteria=records, feasible=violation <= 1e-10, constraint_violation=violation,
                objective_vector=objectives, objective_score=sum(r["objective"]*r["weight"] for r in records if r["objective"] is not None))


def rank_key(row):
    if row.get("status") != "done":
        return (2, math.inf, math.inf, math.inf, row.get("case", 0))
    return (0 if row.get("feasible", True) else 1, row.get("constraint_violation", 0.),
            row.get("objective_score", 0.), row.get("max_error_ohm", 0.), row.get("case", 0))


def pareto_cases(rows):
    eligible = [r for r in rows if r.get("status") == "done" and r.get("feasible", True)]
    front = []
    for r in eligible:
        a = np.asarray(r.get("objective_vector") or [r.get("max_error_ohm", 0.)], float)
        dominated = False
        for other in eligible:
            b = np.asarray(other.get("objective_vector") or [other.get("max_error_ohm", 0.)], float)
            if a.shape != b.shape:
                raise ValueError("Objective vector dimensions differ")
            if (b <= a).all() and (b < a).any():
                dominated = True
                break
        if not dominated:
            front.append(r["case"])
    return front
