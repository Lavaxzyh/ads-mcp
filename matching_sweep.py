"""Bounded two-port SP sweeps of frozen netlists; never edit a schematic."""
from __future__ import annotations

import itertools
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

import numpy as np
import pandas as pd

from rf_workflows import MatchingEvaluation, SweepParameter, clean, fresh_output, sha256, write_json
from sparameter_results import matrix_from_frame
from matching_targets import freeze_evaluation, runtime_evaluation, score_targets

_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_ATTR = re.compile(r"(?<!\S)([A-Za-z_]\w*(?:\[\d+\])?)\s*=\s*")


def attributes(line):
    matches = list(_ATTR.finditer(line))
    return {m[1]: (m.end(), matches[i + 1].start() if i + 1 < len(matches) else len(line)) for i, m in enumerate(matches)}


class NetlistEditor:
    def __init__(self, text):
        self.lines = re.sub(r"\\[ \t]*\r?\n[ \t]*", " ", text).splitlines()
        self.scopes = []
        scope = ""
        for line in self.lines:
            begin = re.match(r"\s*define\s+(\S+)\s*\(", line)
            if begin:
                if scope:
                    raise ValueError("Nested subcircuit definitions are unsupported")
                scope = begin[1]
            self.scopes.append(scope)
            if re.match(r"\s*end(?:\s|$)", line):
                scope = ""

    def location(self, target):
        hits = []
        for i, (line, scope) in enumerate(zip(self.lines, self.scopes)):
            if scope != target.scope:
                continue
            if target.variable:
                if re.match(r"\s*" + re.escape(target.variable) + r"\s*=", line):
                    hits.append((i, line.index("=") + 1, len(line)))
            elif target.model:
                if re.match(r"\s*model\s+" + re.escape(target.model) + r"\s+", line):
                    attrs = attributes(line)
                    if target.parameter not in attrs:
                        raise ValueError(f"Missing parameter {target.key()}")
                    hits.append((i, *attrs[target.parameter]))
            else:
                instance = re.match(r"\s*([^\s:]+):" + re.escape(target.instance) + r"\s", line)
                if instance:
                    if instance[1] in {"Port", "S_Param", "SweepPlan", "OutputPlan"}:
                        raise ValueError("Sweep circuit components, not ports or analysis controllers")
                    attrs = attributes(line)
                    if target.parameter not in attrs:
                        raise ValueError(f"Missing parameter {target.key()}")
                    hits.append((i, *attrs[target.parameter]))
        if len(hits) != 1:
            raise ValueError(f"Expected one explicit assignment for {target.key()}, found {len(hits)}")
        return hits[0]

    def validate_sp(self, reference):
        sp, ports = [], {}
        for line, scope in zip(self.lines, self.scopes):
            if re.match(r"\s*(HarmonicBalance|HB|LSSP|XDB|Envelope|Tran|Transient|AC|DC|Optim|ParamSweep):", line):
                raise ValueError("Only a single linear S_Param analysis is supported; remove other analyses")
            if scope:
                continue
            if re.match(r"\s*S_Param:", line):
                sp.append(line)
            if re.match(r"\s*Port:", line):
                attrs = attributes(line)
                try:
                    number = line[slice(*attrs["Num"])].strip()
                    z = line[slice(*attrs["Z"])].strip()
                except KeyError as exc:
                    raise ValueError("Ports require explicit Num and Z") from exc
                if number not in {"1", "2"} or int(number) in ports:
                    raise ValueError("Exactly ports 1 and 2 are required")
                match = re.fullmatch(f"({_NUMBER})(?:\\s+Ohm)?", z)
                if not match or not math.isclose(float(match[1]), reference, rel_tol=1e-12):
                    raise ValueError("Each port Z must be a literal real impedance equal to reference_ohms")
                ports[int(number)] = z
        if len(sp) != 1 or set(ports) != {1, 2}:
            raise ValueError("Expected one top-level S_Param controller and ports 1,2")

    def render(self, targets, values):
        lines = self.lines.copy()
        edits = []
        for target, value in zip(targets, values):
            i, start, end = self.location(target)
            literal = f"{value:.17g}" + (" " + target.unit if target.unit else "")
            edits.append((i, start, end, literal))
        # Right to left allows independent parameters of the same component.
        for i, start, end, literal in sorted(edits, reverse=True):
            lines[i] = lines[i][:start] + literal + " " + lines[i][end:]
        return "\n".join(lines) + "\n"


def prepare_sweep(netlist_path, parameters, evaluation, output_dir="", max_cases=64, timeout_seconds=120, data_directory=""):
    if not 1 <= max_cases <= 256 or not 1 <= timeout_seconds <= 600:
        raise ValueError("max_cases must be 1..256; timeout_seconds must be 1..600")
    source = Path(netlist_path).expanduser().resolve(strict=True)
    if source.suffix.lower() not in {".net", ".ckt", ".log", ".txt"}:
        raise ValueError("Supply a saved ADS text netlist")
    if source.stat().st_size > 20_000_000:
        raise ValueError("Netlist is too large for this bounded workflow")
    before = sha256(source)
    text = source.read_text(encoding="utf-8-sig")
    if sha256(source) != before:
        raise ValueError("Source netlist changed while reading")
    targets = [SweepParameter.model_validate(p) for p in parameters]
    spec = MatchingEvaluation.model_validate(evaluation)
    snapshot = freeze_evaluation(spec)
    if not targets or len({p.key() for p in targets}) != len(targets):
        raise ValueError("Supply distinct, nonempty sweep targets")
    count = math.prod(len(p.values) for p in targets)
    if count > max_cases:
        raise ValueError(f"Sweep has {count} cases, exceeding max_cases={max_cases}")
    editor = NetlistEditor(text)
    editor.validate_sp(spec.reference_ohms)
    for target in targets:
        editor.location(target)
    data = str(Path(data_directory).expanduser().resolve(strict=True)) if data_directory else ""
    if data and not Path(data).is_dir():
        raise ValueError("data_directory must be a directory")
    out = fresh_output(output_dir, "matching_sweep", [source])
    return dict(source_netlist=str(source), source_sha256=before, text=text,
                parameters=[p.model_dump() for p in targets], evaluation=spec.model_dump(),
                output_dir=str(out), total_cases=count, timeout_seconds=timeout_seconds, data_directory=data, **snapshot)


def prepare_sp_verification(netlist_path, evaluation, output_dir="", timeout_seconds=120, data_directory=""):
    """One unchanged SP netlist through the existing durable serial executor."""
    if not 1<=timeout_seconds<=120:raise ValueError('SP verification timeout must be 1..120 seconds')
    source=Path(netlist_path).resolve(strict=True)
    if source.stat().st_size>20_000_000:raise ValueError('Netlist exceeds 20 MB')
    before=sha256(source);text=source.read_text(encoding='utf-8-sig')
    if sha256(source)!=before:raise ValueError('Netlist changed while reading')
    spec=MatchingEvaluation.model_validate(evaluation);frozen=freeze_evaluation(spec)
    NetlistEditor(text).validate_sp(spec.reference_ohms)
    data=str(Path(data_directory).resolve(strict=True)) if data_directory else ''
    if data and not Path(data).is_dir():raise ValueError('data_directory must be a directory')
    return dict(workflow='sp',source_netlist=str(source),source_sha256=before,text=text,
                parameters=[],evaluation=spec.model_dump(),output_dir=str(fresh_output(output_dir,'sp_verification',[source])),
                total_cases=1,simulation_budget=1,timeout_seconds=timeout_seconds,data_directory=data,
                use_cache=False,stop_on_error=True,**frozen)


def evaluate_frame(frame, evaluation):
    spec = MatchingEvaluation.model_validate(evaluation)
    f, s = matrix_from_frame(frame)
    if s.shape[1:] != (2, 2) or not np.isfinite(f).all() or len(f) < 1 or (np.diff(f) <= 0).any():
        raise ValueError("Expected a finite, increasing single-frequency-axis two-port dataset")
    mask = (f >= spec.start_hz - 1) & (f <= spec.stop_hz + 1)
    if f[0] > spec.start_hz + 1 or f[-1] < spec.stop_hz - 1 or not mask.any():
        raise ValueError("Dataset does not cover the requested evaluation band")
    # Port order is explicit. The other port is terminated by load_r+j*load_x.
    if spec.input_port == 2:
        s = s[:, ::-1, ::-1]
    z0 = spec.reference_ohms
    load = complex(spec.load_r_ohm, spec.load_x_ohm)
    gamma_load = (load - z0) / (load + z0)
    denom = 1 - s[:, 1, 1] * gamma_load
    if (np.abs(denom) < 1e-14).any():
        raise ValueError("Singular loaded two-port response")
    gamma_in = s[:, 0, 0] + s[:, 0, 1] * s[:, 1, 0] * gamma_load / denom
    if (np.abs(1 - gamma_in) < 1e-14).any():
        raise ValueError("Input impedance is singular")
    zin = z0 * (1 + gamma_in) / (1 - gamma_in)
    if not np.isfinite(zin).all():
        raise ValueError("Non-finite impedance results")
    error, scored, extra_points, extra_metrics = score_targets(f, zin, mask, spec)
    s11_db = 20 * np.log10(np.maximum(np.abs(s[:, 0, 0]), 1e-15))
    s21_db = 20 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-15))
    points = pd.DataFrame(dict(freq_hz=f, zin_r_ohm=zin.real, zin_x_ohm=zin.imag,
                               error_ohm=error, in_evaluation_band=mask,
                               s11_db_matched_reference=s11_db, s21_db_matched_reference=s21_db, **extra_points))
    from rf_metrics import power_metrics, evaluate_criteria
    for name, value in power_metrics(s, gamma_in, gamma_load, spec).items():
        points[name] = value
    multi = evaluate_criteria(points, spec, scored, s[:, 1, 1])
    maximum = float(error[scored].max())
    return points, dict(max_error_ohm=maximum, rms_error_ohm=float(np.sqrt(np.mean(error[scored] ** 2))),
                        worst_s11_db_matched_reference=float(s11_db[scored].max()),
                        min_s21_db_matched_reference=float(s21_db[scored].min()),
                        evaluated_points=int(scored.sum()), total_frequency_points=len(f), **extra_metrics, **multi,
                        meets_error_limit=(maximum <= spec.max_error_ohm) if spec.max_error_ohm is not None else extra_metrics.get("inside_all_regions"))


def execute_case(case, rendered, evaluation, data_directory, timeout_seconds):
    """Common isolated worker for grid scans, adaptive searches and tolerance runs."""
    case.mkdir(parents=True, exist_ok=False)
    (case / "input.net").write_text(rendered, encoding="utf-8")
    write_json(case / "request.json", dict(evaluation=evaluation, data_directory=data_directory))
    with (case / "worker.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).with_name("matching_worker.py")), str(case / "request.json")],
                                   cwd=str(case), stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            returncode = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                process.kill()
            process.wait(timeout=10)
            raise TimeoutError("This case exceeded timeout_seconds; its worker was terminated")
    result_path = case / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    if returncode or not result.get("ok"):
        raise RuntimeError(result.get("error", f"Worker exited with code {returncode}; see worker.log"))
    return {k: v for k, v in result.items() if k != "ok"}


def run_sweep(plan, progress=lambda value: None, stop_on_error=True):
    out = Path(plan["output_dir"])
    out.mkdir(parents=True, exist_ok=False)
    (out / "source.net").write_text(plan["text"], encoding="utf-8")
    public = {k: v for k, v in plan.items() if k != "text"}
    write_json(out / "request.json", public)
    evaluation = runtime_evaluation(plan, out)
    editor = NetlistEditor(plan["text"])
    targets = [SweepParameter.model_validate(p) for p in plan["parameters"]]
    rows, expected_frequencies = [], None
    for number, values in enumerate(itertools.product(*(p.values for p in targets)), 1):
        case = out / f"case_{number:04d}"
        rendered = editor.render(targets, values)
        row = dict(case=number, status="error", parameters={p.key(): v for p, v in zip(targets, values)}, directory=str(case))
        progress(dict(completed=number - 1, total=plan["total_cases"], current_case=number, output_dir=str(out)))
        try:
            result = execute_case(case, rendered, evaluation, plan["data_directory"], plan["timeout_seconds"])
            frequencies = pd.read_csv(case / "points.csv")["freq_hz"].to_numpy()
            if expected_frequencies is not None and (frequencies.shape != expected_frequencies.shape or not np.allclose(frequencies, expected_frequencies, rtol=0, atol=1)):
                raise ValueError("Frequency grids changed between cases; no interpolation is applied")
            expected_frequencies = frequencies
            row.update(status="done", **result)
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
        write_json(out / "cases.json", rows)
        progress(dict(completed=len(rows), total=plan["total_cases"], last_status=row["status"], output_dir=str(out)))
        if row["status"] == "error" and stop_on_error:
            break
    good = sorted((r for r in rows if r["status"] == "done"), key=lambda r: (r["max_error_ohm"], r["rms_error_ohm"], r["case"]))
    ranked = []
    for rank, row in enumerate(good, 1):
        ranked.append({**{k: v for k, v in row.items() if k != "parameters"}, "rank": rank, **row["parameters"]})
    pd.DataFrame(ranked).to_csv(out / "ranking.csv", index=False, encoding="utf-8-sig")
    unchanged = sha256(plan["source_netlist"]) == plan["source_sha256"]
    complete = len(good) == plan["total_cases"] and unchanged
    summary = dict(status="completed" if complete else "partially_completed", completed=complete,
                   output_dir=str(out), total_cases=plan["total_cases"], attempted_cases=len(rows),
                   successful_cases=len(good), failed_cases=sum(r["status"] == "error" for r in rows),
                   skipped_cases=plan["total_cases"] - len(rows), source_netlist_unchanged=unchanged,
                   best_case=good[0] if good else None, ranking_csv=str(out / "ranking.csv"),
                   cases_json=str(out / "cases.json"), evaluation=plan["evaluation"],
                   ranking="Minimize worst scored-frequency impedance distance, then RMS; only evaluated parameter/frequency samples count",
                   scope="Two-port linear SP; S21 is referenced to matched port impedances, not loaded transducer gain. No HB, native optimizer, GUI, or schematic writeback.")
    write_json(out / "summary.json", summary)
    return clean(summary)
