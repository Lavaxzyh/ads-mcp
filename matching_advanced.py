"""Bounded coarse/fine search and explicit tolerance experiments for SP netlists."""
import itertools
import math

import numpy as np
import pandas as pd
from pydantic import Field, FiniteFloat, model_validator
from typing import Literal

from rf_workflows import ParameterTarget, StrictModel, fresh_output, write_json
from matching_sweep import prepare_sweep
from matching_runs import CaseRunner, SimulationBudgetReached
from rf_metrics import rank_key

E_SERIES = {
    "E12": [1, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2],
    "E24": [1, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2, 2.2, 2.4, 2.7, 3, 3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1],
}


class SearchParameter(ParameterTarget):
    minimum: FiniteFloat
    maximum: FiniteFloat
    step: FiniteFloat | None = Field(default=None, gt=0)
    allowed_values: list[FiniteFloat] = Field(default_factory=list, max_length=2048)
    e_series: Literal["E12", "E24"] | None = None

    @model_validator(mode="after")
    def domain(self):
        if self.maximum <= self.minimum or not math.isfinite(self.maximum - self.minimum):
            raise ValueError("minimum must be below maximum with a finite span")
        if sum((self.step is not None, bool(self.allowed_values), self.e_series is not None)) > 1:
            raise ValueError("Choose step, allowed_values OR e_series; bounds always apply")
        if self.step is not None and (self.maximum - self.minimum) / self.step > 1e12:
            raise ValueError("Manufacturing grid exceeds numerical resolution")
        if self.allowed_values and (len(set(self.allowed_values)) != len(self.allowed_values) or any(v < self.minimum or v > self.maximum for v in self.allowed_values)):
            raise ValueError("allowed_values must be distinct and inside the bounds")
        if self.e_series and self.minimum <= 0:
            raise ValueError("E-series values require positive bounds")
        return self


class SearchSettings(StrictModel):
    budget: int = Field(default=32, ge=1, le=256)
    coarse_fraction: FiniteFloat = Field(default=0.5, gt=0, lt=1)
    coarse_points: int = Field(default=3, ge=2, le=11)
    fine_points: int = Field(default=5, ge=3, le=21)
    refine_top: int = Field(default=2, ge=1, le=8)
    seed: int = Field(default=0, ge=0, le=2147483647)


class ToleranceParameter(ParameterTarget):
    nominal: FiniteFloat
    relative_pct: FiniteFloat | None = Field(default=None, gt=0)
    absolute: FiniteFloat | None = Field(default=None, gt=0)
    minimum: FiniteFloat | None = None
    maximum: FiniteFloat | None = None

    def delta(self):
        return self.absolute if self.absolute is not None else abs(self.nominal) * self.relative_pct / 100

    @model_validator(mode="after")
    def interval(self):
        if (self.relative_pct is None) == (self.absolute is None):
            raise ValueError("Specify exactly one relative_pct or absolute tolerance")
        d = self.delta()
        if not math.isfinite(d) or d <= 0 or not math.isfinite(self.nominal + d) or not math.isfinite(self.nominal - d):
            raise ValueError("Tolerance interval must be finite and nonzero")
        if self.minimum is not None and self.nominal - d < self.minimum or self.maximum is not None and self.nominal + d > self.maximum:
            raise ValueError("Tolerance interval violates a physical bound; values are never silently clipped")
        return self


class ToleranceSettings(StrictModel):
    method: Literal["oat", "corners", "monte_carlo"] = "monte_carlo"
    samples: int = Field(default=32, ge=1, le=239)
    distribution: Literal["uniform", "truncated_normal"] = "uniform"
    seed: int = Field(default=0, ge=0, le=2147483647)
    budget: int = Field(default=128, ge=1, le=256)


def identity(parameter):
    return parameter.model_dump(include=set(ParameterTarget.model_fields))


def axis(parameter, count, lower=None, upper=None):
    p = parameter
    lo, hi = max(p.minimum, lower if lower is not None else p.minimum), min(p.maximum, upper if upper is not None else p.maximum)
    allowed = p.allowed_values
    if p.e_series:
        allowed = [float(f"{m * 10.0 ** power:.14g}") for power in range(max(-307, math.floor(math.log10(p.minimum)) - 1), min(307, math.ceil(math.log10(p.maximum))) + 1) for m in E_SERIES[p.e_series]]
    if allowed:
        choices = sorted(v for v in allowed if lo <= v <= hi)
        if not choices:
            raise ValueError(f"No permitted nominal values in bounds for {p.key()}")
        return [choices[i] for i in sorted(set(np.rint(np.linspace(0, len(choices)-1, min(count, len(choices)))).astype(int)))]
    if p.e_series:
        raise ValueError("No representable E-series values in bounds")
    if p.step is not None:
        first = math.ceil((lo - p.minimum) / p.step - 1e-9)
        last = math.floor((hi - p.minimum) / p.step + 1e-9)
        if first > last:
            raise ValueError(f"No manufacturing-grid value for {p.key()}")
        ticks = sorted(set(np.rint(np.linspace(first, last, min(count, last-first+1))).astype(np.int64)))
        return sorted(set(float(f"{p.minimum + n*p.step:.14g}") for n in ticks))
    return sorted(set(float(v) for v in np.linspace(lo, hi, count)))


def bounded_product(axes, count, rng):
    """Deterministic coverage without constructing an exponential Cartesian grid."""
    total = math.prod(map(len, axes))
    count = min(count, total)
    indices = {}
    def add(index):
        if len(indices) < count:
            indices.setdefault(tuple(map(int, index)), None)
    add([len(a)//2 for a in axes])
    add([0]*len(axes))
    add([len(a)-1 for a in axes])
    if total <= count:
        for index in itertools.product(*(range(len(a)) for a in axes)):
            add(index)
    else:
        strata = [rng.permutation(count) for _ in axes]
        for row in range(count):
            add([min(len(a)-1, int((strata[j][row]+0.5)*len(a)/count)) for j, a in enumerate(axes)])
        for _ in range(10000):
            if len(indices) >= count:
                break
            add([rng.integers(len(a)) for a in axes])
        if len(indices) < count:
            for index in itertools.islice(itertools.product(*(range(len(a)) for a in axes)), 10000):
                add(index)
                if len(indices) >= count:
                    break
    return [tuple(a[i] for a, i in zip(axes, index)) for index in indices]


def prepare_advanced(kind, netlist_path, parameters, evaluation, settings=None, output_dir="", timeout_seconds=120,
                     data_directory="", cache_dir="", use_cache=True, stop_on_error=True):
    model, config = (SearchParameter, SearchSettings) if kind == "search" else (ToleranceParameter, ToleranceSettings)
    targets = [model.model_validate(p) for p in parameters]
    config = config.model_validate(settings or {})
    if not 1 <= len(targets) <= 8:
        raise ValueError("Supply 1..8 distinct parameters")
    base = [dict(**identity(p), values=[p.minimum if kind == "search" else p.nominal]) for p in targets]
    if kind == "search":
        for p in targets:
            axis(p, config.coarse_points)
        count = config.budget
    else:
        count = 1 + 2*len(targets) + (2**len(targets) if config.method == "corners" else config.samples if config.method == "monte_carlo" else 0)
        if count > config.budget:
            raise ValueError(f"Tolerance experiment needs {count} evaluations, exceeding budget={config.budget}; reduce samples or use Monte Carlo")
    output = output_dir or str(fresh_output("", "matching_" + kind, [netlist_path]))
    plan = prepare_sweep(netlist_path, base, evaluation, output, max_cases=1, timeout_seconds=timeout_seconds, data_directory=data_directory)
    plan.update(workflow=kind, advanced_parameters=[p.model_dump() for p in targets], settings=config.model_dump(),
                total_cases=count, cache_dir=cache_dir, use_cache=use_cache, stop_on_error=stop_on_error)
    return plan


def run_search(plan, progress=lambda p: None):
    targets = [SearchParameter.model_validate(p) for p in plan["advanced_parameters"]]
    settings = SearchSettings.model_validate(plan["settings"])
    runner = CaseRunner(plan, targets, settings.budget, progress, plan["cache_dir"], plan["use_cache"])
    rng, seen = np.random.default_rng(settings.seed), set()
    coarse_axes = [axis(p, settings.coarse_points) for p in targets]
    coarse_budget = max(1, int(settings.budget * settings.coarse_fraction))
    stop_reason = "candidate_pool_exhausted"
    def evaluate(values, phase):
        nonlocal stop_reason
        key = tuple(float(f"{v:.14g}") for v in values)
        if key in seen:
            return True
        seen.add(key)
        try:
            row = runner.evaluate(values, phase)
        except SimulationBudgetReached:
            stop_reason = "simulation_budget_reached"
            return False
        return not (row["status"] == "error" and plan["stop_on_error"])
    proceed = True
    for values in bounded_product(coarse_axes, coarse_budget, rng):
        if not evaluate(values, "coarse"):
            proceed = False
            if stop_reason != "simulation_budget_reached":
                stop_reason = "case_error"
            break
    coarse = sorted((r for r in runner.rows if r["status"] == "done"), key=rank_key)
    pools = []
    for best in coarse[:settings.refine_top] if proceed else []:
        fine_axes = []
        for p, a in zip(targets, coarse_axes):
            i = int(np.argmin(np.abs(np.asarray(a) - best["parameters"][p.key()])))
            fine_axes.append(axis(p, settings.fine_points, a[max(0, i-1)], a[min(len(a)-1, i+1)]))
        pools.append(iter(bounded_product(fine_axes, min(1024, settings.budget*4), rng)))
    while pools and len(runner.rows) < settings.budget and proceed:
        for pool in pools.copy():
            values = next(pool, None)
            if values is None:
                pools.remove(pool)
                continue
            if not evaluate(values, "fine"):
                proceed = False
                if stop_reason != "simulation_budget_reached":
                    stop_reason = "case_error"
                break
            if len(runner.rows) >= settings.budget:
                break
    if len(runner.rows) == settings.budget:
        stop_reason = "budget_reached"
    return runner.finish(stop_reason=stop_reason, global_optimum_proven=False,
                         coarse_cases=sum(r["phase"] == "coarse" for r in runner.rows),
                         fine_cases=sum(r["phase"] == "fine" for r in runner.rows),
                         coarse_grid_size=math.prod(map(len, coarse_axes)),
                         best_coarse_error_ohm=coarse[0]["max_error_ohm"] if coarse else None,
                         method="Bounded coarse grid/stratified subset, then local fine grids around best coarse cases")


def tolerance_design(targets, settings):
    nominal = np.asarray([p.nominal for p in targets], float)
    delta = np.asarray([p.delta() for p in targets], float)
    design = [("nominal", nominal.tolist())]
    for i in range(len(targets)):
        for sign, label in ((-1, "minus"), (1, "plus")):
            v = nominal.copy(); v[i] += sign*delta[i]
            design.append((f"oat_{i}_{label}", v.tolist()))
    if settings.method == "corners":
        for signs in itertools.product((-1, 1), repeat=len(targets)):
            design.append(("trial", (nominal + np.asarray(signs)*delta).tolist()))
    elif settings.method == "monte_carlo":
        rng = np.random.default_rng(settings.seed)
        if settings.distribution == "uniform":
            draws = rng.uniform(-1, 1, size=(settings.samples, len(targets)))
        else:
            draws = rng.normal(0, 1/3, size=(settings.samples, len(targets)))
            invalid = np.abs(draws) > 1
            while invalid.any():
                draws[invalid] = rng.normal(0, 1/3, size=int(invalid.sum()))
                invalid = np.abs(draws) > 1
        design.extend(("trial", (nominal + d*delta).tolist()) for d in draws)
    return design


def run_tolerance(plan, progress=lambda p: None):
    targets = [ToleranceParameter.model_validate(p) for p in plan["advanced_parameters"]]
    settings = ToleranceSettings.model_validate(plan["settings"])
    design = tolerance_design(targets, settings)
    runner = CaseRunner(plan, targets, settings.budget, progress, plan["cache_dir"], plan["use_cache"])
    for phase, values in design:
        try:
            row = runner.evaluate(values, phase)
        except SimulationBudgetReached:
            break
        if row["status"] == "error" and plan["stop_on_error"]:
            break
    good = {r["phase"]: r for r in runner.rows if r["status"] == "done" and r["phase"] != "trial"}
    sensitivity = []
    if "nominal" in good:
        nominal_points = pd.read_csv(good["nominal"]["points_csv"])
        mask = nominal_points.is_scored.to_numpy(bool)
        def impedance(row):
            points = pd.read_csv(row["points_csv"])
            return (points.zin_r_ohm.to_numpy() + 1j*points.zin_x_ohm.to_numpy())[mask]
        base = impedance(good["nominal"])
        for i, p in enumerate(targets):
            minus, plus = good.get(f"oat_{i}_minus"), good.get(f"oat_{i}_plus")
            if minus is None or plus is None:
                continue
            low, high = impedance(minus), impedance(plus)
            impact = float(max(abs(high-base).max(), abs(low-base).max()))
            sensitivity.append(dict(parameter=p.key(), unit=p.unit, nominal=p.nominal, tolerance=p.delta(),
                                    max_delta_z_ohm=impact, max_abs_dz_dparameter=float(abs((high-low)/(2*p.delta())).max()),
                                    score_derivative=(plus["max_error_ohm"]-minus["max_error_ohm"])/(2*p.delta()),
                                    minus_error_ohm=minus["max_error_ohm"], plus_error_ohm=plus["max_error_ohm"],
                                    nominal_error_ohm=good["nominal"]["max_error_ohm"]))
    sensitivity.sort(key=lambda r: (-r["max_delta_z_ohm"], r["parameter"]))
    for rank, row in enumerate(sensitivity, 1):
        row["rank"] = rank
    pd.DataFrame(sensitivity or [], columns=list(sensitivity[0]) if sensitivity else ["rank", "parameter", "max_delta_z_ohm"]).to_csv(runner.out / "sensitivity.csv", index=False, encoding="utf-8-sig")
    phase_filter = (lambda p: p.startswith("oat_")) if settings.method == "oat" else (lambda p: p == "trial")
    expected = sum(phase_filter(p) for p, _ in design)
    trials = [r for r in runner.rows if phase_filter(r["phase"])]
    successful = [r for r in trials if r["status"] == "done"]
    has_metric_bounds = any(c.get("lower") is not None or c.get("upper") is not None for c in plan["evaluation"].get("criteria", []))
    passed = sum(r.get("feasible", False) is True for r in successful) if has_metric_bounds else sum(r["meets_error_limit"] is True for r in successful)
    threshold_available = bool(successful) and (has_metric_bounds or all(r["meets_error_limit"] is not None for r in successful))
    errors = np.asarray([r["max_error_ohm"] for r in successful])
    stats = dict(method=settings.method, requested=expected, evaluated=len(trials), successful=len(successful),
                 simulation_failures=len(trials)-len(successful), not_run=expected-len(trials),
                 passed=passed if threshold_available else None,
                 verified_pass_fraction=passed/expected if threshold_available and expected else None,
                 pass_fraction_denominator="All requested trial cases, including failures and unrun cases; these are never counted as passes",
                 error_quantiles_ohm=dict(zip(("p05", "p50", "p95"), np.quantile(errors, [0.05, 0.5, 0.95]).tolist())) if len(errors) else None,
                 worst_error_ohm=float(errors.max()) if len(errors) else None,
                 quantile_population="Successful trial simulations only; inspect failures separately",
                 distribution=(settings.distribution if settings.method == "monte_carlo" else None),
                 distribution_definition="Independent parameters; uniform within ±tolerance, or Gaussian sigma=tolerance/3 conditioned on ±tolerance. OAT and corners are deterministic, not statistical yield.",
                 seed=settings.seed, manufacturing_yield_verified=False)
    write_json(runner.out / "tolerance_statistics.json", stats)
    return runner.finish(incomplete=len(runner.rows) != len(design) or len(sensitivity) != len(targets),
                         requested_cases=len(design), skipped_cases=len(design)-len(runner.rows),
                         nominal_case=good.get("nominal"), sensitivity=sensitivity, trial_statistics=stats,
                         sensitivity_csv=str(runner.out / "sensitivity.csv"),
                         sensitivity_definition="Rank by largest |Zperturbed-Znominal| at scored frequencies under each specified tolerance; local OAT, not a global sensitivity index")
