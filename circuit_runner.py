"""Dispatch native GUI circuit simulation/optimization; poll fresh artifacts."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
import uuid
from ads_gui import call_native


def dispatch_circuit(a):
    import json
    from pathlib import Path
    from keysight.ads import de
    from PySide6.QtWidgets import QApplication, QMainWindow
    from PySide6.QtGui import QAction
    from PySide6.QtCore import QTimer
    name = a["design"]
    ctx = de.ael.db_find_design_context_from_name(name, 0)
    app = QApplication.instance()
    from shiboken6 import isValid
    if any(isValid(w) and w.isVisible() and w.windowTitle() == "Optimization Cockpit" for w in app.topLevelWidgets()):
        raise RuntimeError("Close the Optimization Cockpit before starting another run; release the completed optimizer task first")
    matches = [w for w in app.topLevelWidgets() if isValid(w) and isinstance(w, QMainWindow) and "[" + name + "]" in w.windowTitle()]
    if not matches:
        de.ael.de_show_context_in_new_window(ctx)
    app.processEvents()
    matches = [w for w in app.topLevelWidgets() if isValid(w) and isinstance(w, QMainWindow) and "[" + name + "]" in w.windowTitle()]
    if not matches:
        raise ValueError("Target schematic window not found")
    w = app.activeWindow() if app.activeWindow() in matches else matches[-1]
    text = "Optimize..." if a["mode"] == "optimize" else "Simulate"
    actions = [x for x in w.findChildren(QAction) if x.objectName() == text]
    if len(actions) != 1 or not actions[0].isEnabled():
        raise RuntimeError("Requested simulation action unavailable: " + text)
    w.raise_()
    w.activateWindow()
    # Return to ADS's event loop before triggering a potentially lengthy action.
    marker = Path(a["marker"])
    marker.write_text(json.dumps({"queued": True, "design": name, "mode": a["mode"]}), encoding="utf-8")
    # A bridge operation's local scope disappears before Qt runs the timer.
    # Retain both the callback and its QAction wrapper on the application.
    retained = getattr(app, "_ads_mcp_dispatches", {})
    token = str(marker)
    action = actions[0]
    def trigger():
        try:
            if not isValid(w) or not isValid(action):
                raise RuntimeError("Target simulation window/action was deleted before dispatch")
            w.raise_()
            w.activateWindow()
            marker.write_text(json.dumps({"dispatched": True, "design": name, "mode": a["mode"]}), encoding="utf-8")
            if a["mode"] == "simulate":
                de.ael.de_analyze()
            else:
                action.trigger()
        except Exception as exc:
            marker.write_text(json.dumps({"error": str(exc)}), encoding="utf-8")
        finally:
            retained.pop(token, None)
    retained[token] = trigger
    app._ads_mcp_dispatches = retained
    QTimer.singleShot(0, trigger)
    return {"queued": True, "design": name, "mode": a["mode"]}


def signature(p):
    try:
        s = p.stat()
        return [s.st_mtime_ns, s.st_size]
    except FileNotFoundError:
        return None


def release_simulator(a):
    from keysight.ads import de
    from PySide6.QtWidgets import QApplication
    from shiboken6 import isValid
    de.ael.de_release_simulator()
    # Leaving the finished cockpit open blocks the next native Analyze call
    # with a modal "Close the Optimization Cockpit?" question.
    closed = []
    for w in QApplication.topLevelWidgets():
        if isValid(w) and w.isVisible() and w.windowTitle() == "Optimization Cockpit":
            closed.append(bool(w.close()))
    return {"simulator_released": True, "cockpit_closed": closed, "schematic_parameters_modified": False}


def optimizer_completion(a):
    from PySide6.QtWidgets import QApplication, QLabel, QAbstractButton
    from shiboken6 import isValid
    cockpits = [w for w in QApplication.topLevelWidgets() if isValid(w) and w.isVisible() and w.windowTitle() == "Optimization Cockpit"]
    if len(cockpits) != 1:
        return {"complete": False, "reason": "A unique visible native cockpit was not found"}
    w = cockpits[0]
    labels = [x.text() for x in w.findChildren(QLabel) if x.isVisible()]
    buttons = {x.text(): x.isEnabled() for x in w.findChildren(QAbstractButton) if x.isVisible()}
    stopped = "Stopping reason:" in labels and buttons.get("Continue", False) and buttons.get("Simulate", False)
    return {"complete": bool(stopped), "visible_labels": labels, "buttons": buttons}


class CircuitRunner:
    def release(self, state, task_id):
        task = state.task_status(task_id)
        if task["kind"] != "gui_circuit" or task["status"] != "done" or task["monitor"]["mode"] != "optimize":
            raise ValueError("Select a completed native GUI optimization task")
        if any(t["status"] == "running" for t in state.snapshot()["tasks"].values()):
            raise RuntimeError("Wait for active simulations before releasing the simulator")
        m = task["monitor"]
        result = dict(task["result"])
        if result.get("simulator_released"):
            return result
        released = call_native(release_simulator, {}, m["session_id"], m["workspace"])
        result.update(released, simulator_release_required=False)
        state.record_optimizer_release(task_id, released)
        return result

    def start(self, state, session_id, workspace, design, mode="simulate"):
        ws = Path(workspace).resolve(strict=True)
        if not (ws / "lib.defs").is_file():
            raise ValueError("Not an ADS workspace")
        parts = design.split(":")
        if len(parts) != 3 or parts[2] != "schematic":
            raise ValueError("design must identify a schematic")
        cell = parts[1]
        token = uuid.uuid4().hex
        folder = ws / ".ads-mcp" / "circuit_runs" / token
        folder.mkdir(parents=True)
        datasets = {str(p.resolve()): signature(p) for p in (ws / "data").rglob("*.ds")}
        logs = {str(p.resolve()): signature(p) for p in (ws / "data").rglob("logFile.txt")}
        monitor = {"workspace": str(ws), "session_id": session_id, "design": design,
                   "mode": mode, "cell": cell, "folder": str(folder), "marker": str(folder / "dispatch.json"),
                   "datasets_before": datasets, "logs_before": logs, "started": time.time()}
        tid = state.create_external_task("gui_circuit", monitor)
        try:
            call_native(dispatch_circuit, monitor, session_id, str(ws))
        except Exception as exc:
            # A bridge timeout may mean dispatch happened. Never retry implicitly.
            if any(s in str(exc) for s in ("unavailable", "not found", "another workspace", "Close the Optimization Cockpit")):
                state.update_external_task(tid, status="error", error=str(exc))
            else:
                state.update_external_task(tid, progress={"dispatch_uncertain": str(exc), "attention_required": "Inspect ADS and poll this task; do not redispatch"})
        return {"task_id": tid, "mode": mode, "state": "simulating"}

    def poll(self, state, task_id, read_dataframe):
        st = state.task_status(task_id)
        if st["status"] != "running":
            return st
        m, progress = st["monitor"], dict(st.get("progress", {}))
        ws = Path(m["workspace"])
        marker = Path(m["marker"])
        if marker.exists():
            report = json.loads(marker.read_text(encoding="utf-8"))
            if "error" in report:
                state.update_external_task(task_id, status="error", error=report["error"])
                return state.task_status(task_id)
        candidates = [p for p in (ws / "data").rglob(m["cell"] + ".ds") if signature(p) != m["datasets_before"].get(str(p.resolve()))]
        fresh_logs = [p for p in (ws / "data").rglob("logFile.txt") if signature(p) != m["logs_before"].get(str(p.resolve()))]
        progress.update(fresh_datasets=[str(p) for p in candidates], fresh_logs=[str(p) for p in fresh_logs])
        if candidates:
            p = max(candidates, key=lambda x: x.stat().st_mtime_ns)
            sig = signature(p)
            stable = progress.get("last_dataset") == [str(p), sig]
            progress["last_dataset"] = [str(p), sig]
            if stable:
                try:
                    if m["mode"] == "optimize":
                        import keysight.ads.dataset as dataset
                        ds = dataset.open(str(p), mode="r")
                        summaries = [k for k in ds.keys() if k.endswith(".SUMMARY")]
                        solutions = [k for k in ds.keys() if k.endswith(".OPTSOLNVALS")]
                        if len(summaries) != 1 or len(solutions) != 1:
                            raise ValueError("Optimization summary/solution not ready")
                        summary = ds[summaries[0]].to_dataframe().iloc[0].to_dict()
                        values = ds[solutions[0]].to_dataframe().iloc[0].to_dict()
                        result = {"summary": {k: float(v) for k, v in summary.items()},
                                  "optimal_parameters": {k: float(v) for k, v in values.items()},
                                  "simulator_release_required": True}
                    else:
                        _, frame = read_dataframe(str(p))
                        result = {"rows": len(frame), "columns": list(frame.columns)}
                    # Native Optim with an interactive cockpit may not write
                    # logFile.txt. Require its stopped UI state as independent
                    # completion evidence alongside the fresh final dataset.
                    log = None
                    if m["mode"] == "optimize":
                        evidence = call_native(optimizer_completion, {}, m["session_id"], str(ws))
                        if not evidence["complete"]:
                            raise ValueError("Native optimization cockpit has not stopped")
                        (Path(m["folder"]) / "completion.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
                        result["completion_evidence"] = evidence
                    else:
                        if not fresh_logs:
                            raise ValueError("Fresh dataset exists but a fresh simulator log has not been found")
                        log = max(fresh_logs, key=lambda x: x.stat().st_mtime_ns)
                        text = log.read_text(encoding="utf-8", errors="replace")
                        if "Total stopwatch time" not in text and "Simulation finished" not in text:
                            raise ValueError("Simulator completion marker not ready")
                    target = Path(m["folder"])
                    shutil.copy2(p, target / p.name)
                    if log:
                        shutil.copy2(log, target / "logFile.txt")
                    state.update_external_task(task_id, status="done", result={"dataset": str(target / p.name), "source_dataset": str(p), "log": str(target / "logFile.txt") if log else None, **result}, progress=progress)
                    return state.task_status(task_id)
                except Exception as exc:
                    progress["pending_validation"] = str(exc)
        for log in fresh_logs:
            text = log.read_text(encoding="utf-8", errors="replace")
            import re
            error = re.search(r"(?im)^\s*(?:Error detected|FATAL|Simulation (?:failed|aborted)).*$", text)
            if error:
                state.update_external_task(task_id, status="error", error=error[0], progress=progress)
                return state.task_status(task_id)
        if time.time() - m["started"] > 180:
            progress["attention_required"] = "Check ADS if progress stops; this task will not automatically redispatch"
        state.update_external_task(task_id, progress=progress)
        return state.task_status(task_id)
