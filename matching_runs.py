"""Shared case bookkeeping and conservative, content-checked linear-SP cache."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

import numpy as np
import pandas as pd

from matching_sweep import NetlistEditor, evaluate_frame, execute_case
from matching_targets import runtime_evaluation
from rf_workflows import ParameterTarget, clean, sha256, write_json
from sparameter_results import export_touchstone
from rf_metrics import rank_key, pareto_cases
from rf_executor import record_attempt, attempt_count


class SimulationBudgetReached(BaseException):
    pass


def raw_frame(path):
    with np.load(path, allow_pickle=False) as raw:
        f, s = raw["freq_hz"], raw["s"]
    if f.ndim != 1 or s.shape != (len(f), 2, 2) or not len(f) or not np.isfinite(f).all() or not np.isfinite(s).all() or (np.diff(f) <= 0).any():
        raise ValueError("Invalid cached S-parameter grid/matrix")
    return pd.DataFrame({f"S[{i+1},{j+1}]": s[:, i, j] for i in range(2) for j in range(2)}, index=pd.Index(f, name="freq"))


def cache_eligibility(text, data_directory):
    if data_directory:
        return "data_directory has external dependencies; cache disabled"
    if re.search(r"\b(?:File|FileName|DataFile|Include|FilePath|ModelFile)\s*=", text, re.I):
        return "file-valued model dependency; cache disabled"
    known = {"R", "L", "C", "MLIN", "MLIN2", "TLIN", "Port", "S_Param", "SweepPlan", "OutputPlan"}
    known |= set(re.findall(r"^\s*define\s+(\w+)\s*\(", text, re.M))
    for line in NetlistEditor(text).lines:
        line = line.strip()
        if not line or line.startswith((";", "//")):
            continue
        if re.match(r"(?:define\s|end\b|Options\s)", line):
            continue
        if re.fullmatch(r'#load\s+"python"\s*,\s*"LinearCollapse"', line):
            continue
        if re.fullmatch(r'Component\s+Module="LinearCollapse"\s+Type="ModelExtractor"\s+NetworkRepresentation=2', line):
            continue
        functions = re.findall(r"\b(\w+)\s*\(", line)
        if any(f not in {"sin", "cos", "tan", "sqrt", "exp", "log", "ln", "abs", "pow"} for f in functions):
            return "unclassified function may read external data; cache disabled"
        model = re.match(r"model\s+\w+\s+(\w+)", line)
        component = re.match(r"(\w+):", line)
        assignment = re.match(r"\w+\s*=", line)
        if model and model[1] == "MSUB" or component and component[1] in known:
            continue
        if assignment and not re.search(r"[\"']", line):
            continue
        return "unclassified model, directive or expression; cache disabled"
    return ""


def solver_fingerprint():
    root = Path(os.environ["HPEESOF_DIR"])
    required = [root / "bin" / n for n in ("hpeesofsim.exe", "hpeesofsim_private.dll", "hpeesofsim.cfg")]
    required += [Path(__file__).with_name(n) for n in ("matching_worker.py", "sparameter_results.py")]
    # Conservative install fingerprint: changes anywhere in the installation
    # (including a model/parser/DLL update) invalidate the cache. Hash the core
    # solver contents as well; large vendor trees need not be read in full.
    inventory = []
    for directory in (root / "bin", root / "circuit", root / "custom", root / "tools" / "python"):
        if directory.exists():
            for path in sorted(directory.rglob("*")):
                if path.is_file() and path.suffix not in {".pyc", ".log"} and "__pycache__" not in path.parts:
                    stat = path.stat()
                    inventory.append((str(path.relative_to(root)), stat.st_size, stat.st_mtime_ns))
    configs = Path(os.environ.get("HOME", str(Path.home()))) / "hpeesof" / "config"
    home_hashes = {str(p.relative_to(configs)): sha256(p) for p in sorted(configs.rglob("*")) if p.is_file() and p.suffix in {".cfg", ".defs"}}
    payload = dict(core={str(p): sha256(p) for p in required}, install_inventory=inventory, home_config=home_hashes)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class SimulationCache:
    def __init__(self, plan, directory, enabled):
        self.reason = cache_eligibility(plan["text"], plan["data_directory"]) if enabled else "disabled by caller"
        self.directory, self.fingerprint = None, None
        if self.reason:
            return
        from ads_engine import get_workroot
        path = (Path(directory).expanduser() if directory else get_workroot() / "matching_cache_v1").resolve()
        for parent in Path(plan["source_netlist"]).parents:
            if (parent / "lib.defs").is_file() and path.is_relative_to(parent):
                raise ValueError("Cache must be outside the source ADS workspace")
        if path.exists() and not path.is_dir():
            raise ValueError("cache_dir must be a directory")
        self.fingerprint = solver_fingerprint()
        path.mkdir(parents=True, exist_ok=True)
        self.directory = path

    def key(self, rendered):
        return hashlib.sha256(("raw-linear-SP-v1\n" + str(self.fingerprint) + "\n" + rendered).encode("utf-8")).hexdigest()

    def get(self, key):
        if self.directory is None:
            return None, self.reason
        entry = self.directory / f"{key}.json"
        if not entry.is_file():
            return None, "miss"
        try:
            meta = json.loads(entry.read_text(encoding="utf-8"))
            if meta["key"] != key or meta["solver_fingerprint"] != self.fingerprint:
                raise ValueError("cache key mismatch")
            for record in meta["files"].values():
                path = self.directory / record["file"]
                if path.resolve().parent != self.directory or sha256(path) != record["sha256"]:
                    raise ValueError("cache artifact integrity mismatch")
            raw_frame(self.directory / meta["files"]["raw"]["file"])
            return meta, "hit"
        except Exception as exc:
            return None, f"invalid entry ignored: {type(exc).__name__}"

    def put(self, key, result, case):
        if self.directory is None:
            return
        # Unique immutable blobs plus atomic manifest replacement allow safe
        # concurrent writers. Existing result files are never deleted.
        meta = dict(key=key, solver_fingerprint=self.fingerprint, source_case=str(case), files={})
        token = uuid.uuid4().hex
        for kind, field, suffix in (("raw", "raw_sparameters", ".npz"), ("dataset", "dataset", ".ds")):
            dest = self.directory / f"{key}.{token}{suffix}"
            shutil.copy2(result[field], dest)
            meta["files"][kind] = dict(file=dest.name, sha256=sha256(dest))
        write_json(self.directory / f"{key}.json", meta)

    def materialize(self, meta, case, rendered, evaluation):
        case.mkdir(parents=True, exist_ok=False)
        (case / "input.net").write_text(rendered, encoding="utf-8")
        write_json(case / "request.json", dict(evaluation=evaluation, cache_key=meta["key"], cache_source=meta["source_case"]))
        paths = {"raw": case / "sparameters.npz", "dataset": case / "matching_case.ds"}
        for kind, target in paths.items():
            record = meta["files"][kind]
            shutil.copy2(self.directory / record["file"], target)
            if sha256(target) != record["sha256"]:
                raise ValueError("Cache changed during copy; refusing an unverified result")
        frame = raw_frame(paths["raw"])
        points, metrics = evaluate_frame(frame, evaluation)
        points.to_csv(case / "points.csv", index=False, encoding="utf-8-sig")
        touchstone = export_touchstone(frame, case / "result.s2p", reference_ohms=evaluation["reference_ohms"])
        result = dict(dataset=str(paths["dataset"]), raw_sparameters=str(paths["raw"]), points_csv=str(case / "points.csv"),
                      touchstone=touchstone["touchstone"], **metrics)
        write_json(case / "result.json", dict(ok=True, cache_hit=True, **result))
        (case / "worker.log").write_text("Cache hit: no ADS invocation. Scores recomputed from raw complex S parameters.\n", encoding="utf-8")
        return result


class CaseRunner:
    def __init__(self, plan, targets, budget, progress, cache_dir, use_cache):
        self.plan, self.targets, self.budget, self.progress = plan, targets, budget, progress
        self.rows, self.frequencies, self.simulations, self.hits = [], None, 0, 0
        self.new_simulations, self.resumed = 0, 0
        self.cache = SimulationCache(plan, cache_dir, use_cache)
        self.out = Path(plan["output_dir"])
        resume = plan.get("_resume", False)
        self.out.mkdir(parents=True, exist_ok=resume)
        if resume and (self.out / "source.net").exists() and (self.out / "source.net").read_text(encoding="utf-8") != plan["text"]:
            raise ValueError("Checkpoint source differs from the frozen plan")
        (self.out / "source.net").write_text(plan["text"], encoding="utf-8")
        write_json(self.out / "request.json", {k: v for k, v in plan.items() if k != "text" and not k.startswith("_")})
        self.evaluation = runtime_evaluation(plan, self.out)
        self.editor = NetlistEditor(plan["text"])
        self.checkpoints = self.out / "checkpoints"
        self.checkpoints.mkdir(exist_ok=resume)
        self.simulations = attempt_count(self.out)

    def evaluate(self, values, phase):
        if len(self.rows) >= self.budget:
            raise RuntimeError("Internal evaluation budget reached before dispatch")
        number = len(self.rows) + 1
        case = self.out / f"case_{number:04d}"
        rendered = self.editor.render(self.targets, values)
        key = self.cache.key(rendered)
        row = dict(case=number, phase=phase, status="error", parameters={p.key(): v for p, v in zip(self.targets, values)},
                   directory=str(case), cache_key=key, cache_hit=False)
        self.progress(dict(completed=len(self.rows), budget=self.budget, phase=phase, simulation_attempts=self.simulations, cache_hits=self.hits))
        checkpoint = self.checkpoints / f"{number:04d}.json"
        if self.plan.get("_resume") and checkpoint.exists():
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            if saved["row"]["parameters"] != row["parameters"] or saved["row"]["phase"] != phase or saved["rendered_sha256"] != hashlib.sha256(rendered.encode()).hexdigest():
                raise ValueError("Resume request differs from the deterministic checkpoint")
            for path, digest in saved["hashes"].items():
                if not Path(path).is_file() or sha256(path) != digest:
                    raise ValueError("Checkpoint artifact is missing or changed; refusing silent reuse")
            row = dict(saved["row"], resumed=True)
            self.rows.append(row)
            self.hits += int(row.get("cache_hit", False))
            self.resumed += 1
            if row["status"] == "done":
                self.frequencies = pd.read_csv(row["points_csv"]).freq_hz.to_numpy()
            return row
        if case.exists() and self.plan.get("_resume"):
            # Preserve an incomplete attempt in the owned output; never overwrite it.
            if case.resolve().parent != self.out.resolve():
                raise ValueError("Unsafe case output path")
            case.rename(case.with_name(case.name + ".interrupted_" + uuid.uuid4().hex[:8]))
        try:
            entry, note = self.cache.get(key)
            row["cache_note"] = note
            if entry:
                result = self.cache.materialize(entry, case, rendered, self.evaluation)
                self.hits += 1
                row["cache_hit"] = True
            else:
                maximum = self.plan.get("simulation_budget", self.budget)
                if self.simulations >= maximum:
                    raise SimulationBudgetReached()
                if sha256(self.plan["source_netlist"]) != self.plan["source_sha256"]:
                    raise ValueError("Source netlist changed; stopping before dispatch")
                record_attempt(self.out, number, hashlib.sha256(rendered.encode()).hexdigest())
                self.simulations += 1
                self.new_simulations += 1
                execute = self.plan["_executor"].execute if "_executor" in self.plan else execute_case
                result = execute(case, rendered, self.evaluation, self.plan["data_directory"], self.plan["timeout_seconds"])
            f = pd.read_csv(result["points_csv"]).freq_hz.to_numpy()
            if self.frequencies is not None and (f.shape != self.frequencies.shape or not np.allclose(f, self.frequencies, rtol=0, atol=1)):
                raise ValueError("Frequency grid changed; no implicit interpolation")
            self.frequencies = f
            row.update(status="done", **result)
            if not entry:
                try:
                    self.cache.put(key, result, case)
                except Exception as exc:
                    row["cache_write_warning"] = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        self.rows.append(row)
        artifacts = [row[k] for k in ("dataset", "raw_sparameters", "points_csv") if k in row]
        write_json(checkpoint, dict(row=row, rendered_sha256=hashlib.sha256(rendered.encode()).hexdigest(),
                                   hashes={p:sha256(p) for p in artifacts}))
        self.progress(dict(completed=len(self.rows), budget=self.budget, phase=phase, last_status=row["status"], simulation_attempts=self.simulations, cache_hits=self.hits))
        return row

    def finish(self, **extra):
        write_json(self.out / "cases.json", self.rows)
        good = sorted((r for r in self.rows if r["status"] == "done"), key=rank_key)
        ranked = [{**{k: v for k, v in r.items() if k != "parameters"}, "rank": n, **r["parameters"]} for n, r in enumerate(good, 1)]
        pd.DataFrame(ranked or [], columns=list(ranked[0]) if ranked else ["rank", "case", "status", "max_error_ohm"]).to_csv(self.out / "ranking.csv", index=False, encoding="utf-8-sig")
        unchanged = Path(self.plan["source_netlist"]).is_file() and sha256(self.plan["source_netlist"]) == self.plan["source_sha256"]
        completed = bool(good) and len(good) == len(self.rows) and unchanged and not extra.pop("incomplete", False)
        result = dict(status="completed" if completed else "partially_completed", completed=completed,
                      output_dir=str(self.out), evaluated_cases=len(self.rows), successful_cases=len(good), failed_cases=len(self.rows)-len(good),
                      simulation_attempts=self.simulations, new_simulation_attempts=self.new_simulations, resumed_cases=self.resumed,
                      cache_hits=self.hits, cache_disabled_reason=self.cache.reason or None,
                      cache_dir=str(self.cache.directory) if self.cache.directory else None, budget=self.budget,
                      budget_definition="budget caps evaluated parameter cases including cache hits; simulation_budget (default=budget) caps native attempts, including interrupted attempts across resumes",
                      source_netlist_unchanged=unchanged, best_case=good[0] if good else None,
                      feasible_cases=sum(bool(r.get("feasible", True)) for r in good), pareto_cases=pareto_cases(good),
                      ranking_csv=str(self.out / "ranking.csv"), cases_json=str(self.out / "cases.json"), evaluation=self.plan["evaluation"],
                      scope="Linear two-port SP at explicitly scored frequencies. No schematic writeback, HB prediction, measured yield or global optimum guarantee.", **extra)
        write_json(self.out / "summary.json", result)
        return clean(result)
