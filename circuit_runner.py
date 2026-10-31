"""Dispatch native GUI circuit simulation/optimization; poll fresh artifacts."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
import uuid
import hashlib
from ads_gui import call_native


def dispatch_circuit(a):
    import json
    import re
    from pathlib import Path
    from keysight.ads import de, ael
    try:
        from PySide6.QtWidgets import QApplication, QMainWindow
        from PySide6.QtGui import QAction
        from PySide6.QtCore import QTimer
        from shiboken6 import isValid
    except ImportError:
        from PySide2.QtWidgets import QApplication, QMainWindow, QAction
        from PySide2.QtCore import QTimer
        from shiboken2 import isValid
    name = a["design"]
    mode = ael.decl.APPEND_DSN_MODE if a['mode']=='optimize' else ael.decl.READ_ONLY_DSN_MODE
    ctx = de.ael.db_get_design_context_from_name(name, mode)
    app = QApplication.instance()
    if any(isValid(w) and w.isVisible() and w.windowTitle() == "Optimization Cockpit" for w in app.topLevelWidgets()):
        raise RuntimeError("Close the Optimization Cockpit before starting another run; release the completed optimizer task first")
    matches = [w for w in app.topLevelWidgets() if isValid(w) and w.isVisible() and isinstance(w, QMainWindow) and "[" + name + "]" in w.windowTitle()]
    if not matches:
        de.ael.de_show_context_in_new_window(ctx)
    app.processEvents()
    matches = [w for w in app.topLevelWidgets() if isValid(w) and w.isVisible() and isinstance(w, QMainWindow) and "[" + name + "]" in w.windowTitle()]
    if not matches:
        raise ValueError("Target schematic window not found")
    w = app.activeWindow() if app.activeWindow() in matches else matches[-1]
    sequence = re.search(r':(\d+)$', w.windowTitle())
    if sequence is None:
        raise RuntimeError('The target window has no native ADS sequence number')
    sequence = int(sequence.group(1))
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
            # Qt focus alone does not select ADS's AEL graphics context.
            # Select the documented native sequence before Analyze/Optimize.
            de.ael.api_set_current_window_by_seq_num(sequence)
            actual = str(de.ael.db_get_design_name(de.ael.de_get_current_design_context()))
            if actual != name:
                raise RuntimeError('Native Analyze context differs from requested design: '+actual)
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
    import json
    from pathlib import Path
    from keysight.ads import de
    try:
        from PySide6.QtWidgets import QApplication,QDialog,QLabel,QAbstractButton
        from PySide6.QtCore import QTimer
        from shiboken6 import isValid
    except ImportError:
        from PySide2.QtWidgets import QApplication,QDialog,QLabel,QAbstractButton
        from PySide2.QtCore import QTimer
        from shiboken2 import isValid
    # Recheck immediately inside the selected native process. A stale client
    # record is not enough if someone resumed the cockpit after polling.
    evidence=optimizer_completion(a)
    already_released=a.get('already_released',False)
    if already_released:
        if ('Stopping reason:' not in evidence.get('visible_labels',[]) or
            evidence.get('buttons',{}).get('Continue',True) or evidence.get('buttons',{}).get('Simulate',True)):
            raise RuntimeError('The saved release receipt no longer matches the stopped native cockpit')
    elif not evidence.get('complete'):
        raise RuntimeError('Native optimizer is not stopped; no simulator was released')
    current=str(de.ael.db_get_design_name(de.ael.de_get_current_design_context()))
    if current!=a['design']:
        raise RuntimeError('Current native design does not identify the completed optimizer task')
    receipt=Path(a['receipt_file']).resolve()
    ws=Path(de.active_workspace().path).resolve()
    if not receipt.is_relative_to(ws/'.ads-mcp'/'circuit_runs'):
        raise ValueError('Optimizer release receipt must belong to its local task')
    if not already_released:
        de.ael.de_release_simulator()
    report={'simulator_released':True,'design':a['design'],'cockpit_closed':[],
            'schematic_parameters_modified':False,'confirmation_actions':[]}
    receipt.write_text(json.dumps(report,indent=2),encoding='utf-8')
    # Leaving the finished cockpit open blocks the next native Analyze call
    # with a modal "Close the Optimization Cockpit?" question.
    app=QApplication.instance()
    for w in app.topLevelWidgets():
        if not (isValid(w) and w.isVisible() and w.metaObject().className()=='OptimCockpitDialog'):
            continue
        timer=QTimer(app)
        def decline_update():
            for box in app.topLevelWidgets():
                if not(isValid(box) and box is not w and box.isVisible() and isinstance(box,QDialog)
                       and box.windowTitle()=='Optimization Cockpit'):
                    continue
                labels=[label.text() for label in box.findChildren(QLabel)]
                buttons=[b for b in box.findChildren(QAbstractButton) if b.isVisible()]
                choices=[b for b in buttons if b.text().strip()=="Don't Update the Design" and b.isEnabled()]
                if box.metaObject().className()=='OptionsDialog' and 'Before closing the cockpit:' in labels and len(choices)==1:
                    report['confirmation_actions'].append('Dont Update the Design')
                    choices[0].click()
                else:
                    report['confirmation_actions'].append('Unexpected close dialog cancelled')
                    box.reject()
                timer.stop()
                return
        timer.timeout.connect(decline_update)
        timer.start(50)
        try:
            report['cockpit_closed'].append(bool(w.close()))
        finally:
            timer.stop();timer.deleteLater()
    report['cockpit_close_required']=any(isValid(w) and w.isVisible() and w.metaObject().className()=='OptimCockpitDialog' for w in app.topLevelWidgets())
    receipt.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def optimizer_completion(a):
    try:
        from PySide6.QtWidgets import QApplication, QLabel, QAbstractButton
        from shiboken6 import isValid
    except ImportError:
        from PySide2.QtWidgets import QApplication, QLabel, QAbstractButton
        from shiboken2 import isValid
    cockpits = [w for w in QApplication.topLevelWidgets() if isValid(w) and w.isVisible() and w.windowTitle() == "Optimization Cockpit"]
    if len(cockpits) != 1:
        return {"complete": False, "reason": "A unique visible native cockpit was not found"}
    w = cockpits[0]
    labels = [x.text() for x in w.findChildren(QLabel) if x.isVisible()]
    buttons = {x.text(): x.isEnabled() for x in w.findChildren(QAbstractButton) if x.isVisible()}
    stopped = "Stopping reason:" in labels and buttons.get("Continue", False) and buttons.get("Simulate", False)
    return {"complete": bool(stopped), "visible_labels": labels, "buttons": buttons}


release_simulator.__ads_mcp_dependencies__=(optimizer_completion,)


class CircuitRunner:
    def release(self, state, task_id):
        task = state.task_status(task_id)
        if task["kind"] != "gui_circuit" or task["status"] != "done" or task["monitor"]["mode"] != "optimize":
            raise ValueError("Select a completed native GUI optimization task")
        if any(t["status"] == "running" for t in state.snapshot()["tasks"].values()):
            raise RuntimeError("Wait for active simulations before releasing the simulator")
        m = task["monitor"]
        result = dict(task["result"])
        receipt=Path(m['folder'])/'optimizer-release.json'
        if receipt.is_file():
            saved=json.loads(receipt.read_text(encoding='utf-8'))
            if saved.get('design')!=m['design']:
                raise RuntimeError('Optimizer release receipt identifies another design')
            result.update(saved)
        if result.get("simulator_released") and result.get('cockpit_closed') and all(result['cockpit_closed']) and not result.get('cockpit_close_required'):
            return result
        from workspace_activity import assert_idle
        assert_idle(m['workspace'])
        saved_dataset=Path(result.get('dataset',''))
        if not saved_dataset.is_file() or not result.get('dataset_sha256'):
            raise RuntimeError('Optimizer release requires a preserved dataset fingerprint from the completed task')
        if hashlib.sha256(saved_dataset.read_bytes()).hexdigest()!=result['dataset_sha256']:
            raise RuntimeError('Saved optimizer dataset changed; refusing to release against stale evidence')
        released = call_native(release_simulator, {'design':m['design'],'receipt_file':str(receipt),
            'already_released':bool(result.get('simulator_released'))}, m["session_id"], m["workspace"])
        result.update(released, simulator_release_required=False)
        state.record_optimizer_release(task_id, released)
        return result

    def start(self, state, session_id, workspace, design, mode="simulate"):
        from workspace_activity import edit_guard
        with edit_guard(workspace):
            return self._start(state,session_id,workspace,design,mode)

    def _start(self, state, session_id, workspace, design, mode="simulate"):
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
                   "activity_token":token,
                   "mode": mode, "cell": cell, "folder": str(folder), "marker": str(folder / "dispatch.json"),
                   "datasets_before": datasets, "logs_before": logs, "started": time.time()}
        tid = state.create_external_task("gui_circuit", monitor)
        from workspace_activity import reserve
        reserve(ws,tid,token,state.path,session_id)
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
                    state.update_external_task(task_id, status="done", result={"dataset": str(target / p.name), "dataset_sha256":hashlib.sha256((target/p.name).read_bytes()).hexdigest(), "source_dataset": str(p), "log": str(target / "logFile.txt") if log else None, **result}, progress=progress)
                    if m.get('activity_token'):
                        from workspace_activity import release
                        release(ws,m['activity_token'])
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
