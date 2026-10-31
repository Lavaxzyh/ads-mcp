"""Budgeted multi-metric search with constraints checked before expensive ADS solves."""
import itertools
import math
import time
from typing import Literal
import numpy as np
from pydantic import Field, FiniteFloat, model_validator

from rf_workflows import StrictModel, write_json
from matching_advanced import SearchParameter, axis, prepare_advanced, bounded_product
from matching_runs import CaseRunner, SimulationBudgetReached
from rf_metrics import rank_key


class LinearConstraint(StrictModel):
    name: str = Field(min_length=1)
    coefficients: dict[str, FiniteFloat]
    lower: FiniteFloat | None = None
    upper: FiniteFloat | None = None
    unit: str = Field(min_length=1)

    @model_validator(mode="after")
    def bounds(self):
        if not self.coefficients or self.lower is None and self.upper is None:
            raise ValueError("A geometric constraint needs coefficients and at least one bound")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("Invalid geometry bounds")
        return self


class OptimizationSettings(StrictModel):
    evaluation_budget: int = Field(default=48, ge=1, le=256)
    simulation_budget: int = Field(default=32, ge=1, le=256)
    seed: int = Field(default=0, ge=0, le=2147483647)
    initial_fraction: FiniteFloat = Field(default=0.4, gt=0, lt=1)
    refine_rounds: int = Field(default=4, ge=1, le=12)
    refine_top: int = Field(default=3, ge=1, le=8)
    stop_when_feasible: bool = False
    wall_seconds: FiniteFloat = Field(default=1800, gt=0, le=86400)
    geometry_constraints: list[LinearConstraint] = Field(default_factory=list, max_length=32)


def prepare_optimization(netlist_path, parameters, evaluation, settings=None, **kwargs):
    config = OptimizationSettings.model_validate(settings or {})
    targets = [SearchParameter.model_validate(p) for p in parameters]
    keys = {p.key() for p in targets}
    if any(not set(c.coefficients) <= keys for c in config.geometry_constraints):
        raise ValueError("Geometry constraints must reference the exact parameter keys; coefficients operate on the declared numeric units")
    plan = prepare_advanced("search", netlist_path, targets, evaluation,
                            dict(budget=config.evaluation_budget), **kwargs)
    plan.update(workflow="optimize", optimization=config.model_dump(), simulation_budget=config.simulation_budget)
    return plan


def geometry_failures(targets, values, constraints):
    parameters = {p.key():v for p,v in zip(targets, values)}
    failures = []
    for c in constraints:
        value = sum(a*parameters[k] for k,a in c.coefficients.items())
        if c.lower is not None and value < c.lower-1e-12 or c.upper is not None and value > c.upper+1e-12:
            failures.append(dict(name=c.name, value=value, lower=c.lower, upper=c.upper, unit=c.unit))
    return failures


def run_optimization(plan, progress=lambda p: None):
    config = OptimizationSettings.model_validate(plan["optimization"])
    targets = [SearchParameter.model_validate(p) for p in plan["advanced_parameters"]]
    runner = CaseRunner(plan, targets, config.evaluation_budget, progress, plan["cache_dir"], plan["use_cache"])
    rng = np.random.default_rng(config.seed)
    seen, rejected, stop_reason = set(), [], "candidate_pool_exhausted"
    started = time.monotonic()

    def evaluate(values, phase):
        nonlocal stop_reason
        key = tuple(float(f"{v:.14g}") for v in values)
        if key in seen:
            return True
        seen.add(key)
        if len(runner.rows) >= config.evaluation_budget:
            stop_reason = "evaluation_budget_reached"
            return False
        if time.monotonic()-started >= config.wall_seconds:
            stop_reason = "wall_budget_reached"
            return False
        failures = geometry_failures(targets, values, config.geometry_constraints)
        if failures:
            rejected.append(dict(parameters=dict(zip((p.key() for p in targets), values)), violations=failures))
            return True
        try:
            row = runner.evaluate(values, phase)
        except SimulationBudgetReached:
            stop_reason = "simulation_budget_reached"
            return False
        if row["status"] == "error" and plan["stop_on_error"]:
            stop_reason = "case_error"
            return False
        if config.stop_when_feasible and row.get("status") == "done" and row.get("feasible"):
            stop_reason = "feasible_target_reached"
            return False
        return True

    n_initial = max(3, int(config.evaluation_budget*config.initial_fraction))
    initial_axes = [axis(p, min(17, n_initial)) for p in targets]
    proceed = True
    for values in bounded_product(initial_axes, min(1024, n_initial*4), rng):
        proceed = evaluate(values, "initial")
        if not proceed or len(runner.rows) >= n_initial:
            break
    for round_no in range(config.refine_rounds):
        if not proceed:
            break
        best = sorted((r for r in runner.rows if r["status"] == "done"), key=rank_key)[:config.refine_top]
        pools = []
        for row in best:
            axes = []
            for p in targets:
                center = row["parameters"][p.key()]
                radius = (p.maximum-p.minimum)/(2**(round_no+2))
                axes.append(axis(p, 5, center-radius, center+radius))
            pools.append(iter(bounded_product(axes, min(1024, config.evaluation_budget*4), rng)))
        round_quota = min(config.evaluation_budget, n_initial+math.ceil((config.evaluation_budget-n_initial)*(round_no+1)/config.refine_rounds))
        while pools and proceed and len(runner.rows) < round_quota:
            for pool in pools.copy():
                values = next(pool, None)
                if values is None:
                    pools.remove(pool)
                    continue
                proceed = evaluate(values, f"refine_{round_no+1}")
                if not proceed or len(runner.rows) >= round_quota:
                    break
    if len(runner.rows) >= config.evaluation_budget:
        stop_reason = "evaluation_budget_reached"
    write_json(runner.out/"geometry_rejections.json", rejected)
    return runner.finish(stop_reason=stop_reason, geometry_rejected=len(rejected),
                         geometry_rejections=str(runner.out/"geometry_rejections.json"),
                         simulation_budget=config.simulation_budget, global_optimum_proven=False,
                         method="Feasible-first scaled metrics, bounded stratified initial points, then shrinking local grids",
                         audit_passed=not any(r["status"] == "error" for r in runner.rows),
                         geometry_scope="Declared numeric linear constraints, not layout DRC or EM validation")
