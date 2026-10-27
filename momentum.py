"""Run saved native Momentum setups through an existing ADS GUI session.

Only the optional GUI bridge starts Keysight's installed ads-mcp.exe. This
module contains no vendor templates and never edits a setup or closes ADS.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from typing import Any
from version import __version__

_MARKER = "ADS_MOMENTUM_JSON:"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_UNITS = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}


def _inside(path: Path, root: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"path escapes workspace: {path}")
    return path


def _name(value: str) -> str:
    if not _NAME.fullmatch(value):
        raise ValueError(f"unsupported ADS name: {value!r}; use letters, digits and underscores")
    return value


def _signature(path: Path) -> list[int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return [stat.st_mtime_ns, stat.st_size]


def inspect_setup(workspace: str, setup_file: str, setup: str = "emSetup") -> dict[str, Any]:
    """Inspect a local-library native setup, without writing any project files.

    The first release supports the saved calculation type and single enabled
    linear sweep verified on ADS 2027. RFPro and remote queues are not adapters
    for this native Momentum workflow.
    """
    ws = Path(workspace).resolve(strict=True)
    if not (ws / "lib.defs").is_file():
        raise ValueError(f"not an ADS workspace: {ws}")
    xml = _inside(Path(setup_file), ws)
    _name(setup)
    if xml.name != "emStateFile.xml" or not (xml.parent / "eesof_em_setup.file").is_file():
        raise ValueError("setup_file must be emStateFile.xml in a native EM Setup cellview")
    if xml.parent.name.replace("%", "") != setup:
        raise ValueError("setup name does not match the native cellview directory")
    root = ET.parse(xml).getroot()
    if root.tag != "EmSimSetup":
        raise ValueError("not a native EM Setup XML file")
    main = root.find("MainWindow")
    if main is None or main.findtext("simCalculationType") != "5":
        raise ValueError("only native calculation type 5 has been validated on ADS 2027")
    mode = main.findtext("SimSetupOverview/simulator")
    if mode != "0":
        raise ValueError("only Momentum microwave mode (SimSetupOverview/simulator=0) is supported")
    layout = main.find("MomLayout")
    if layout is None:
        raise ValueError("setup has no Momentum layout")
    saved_ws = Path(layout.findtext("workspaceText", "")).resolve()
    if saved_ws != ws:
        raise ValueError("setup refers to a different workspace; rebind the copied setup in ADS first")
    lib = _name(layout.findtext("libraryText", ""))
    cell = _name(layout.findtext("cellText", ""))
    view = _name(layout.findtext("viewText", ""))
    cell_dir, lib_dir = xml.parent.parent, xml.parent.parent.parent
    if cell_dir.name.replace("%", "") != cell or lib_dir.name.replace("%", "") != lib:
        raise ValueError("setup and layout must belong to the same local library and cell")
    views = [p for p in cell_dir.iterdir() if p.is_dir() and p.name.replace("%", "") == view]
    if len(views) != 1 or not (views[0] / "layout.oa").is_file():
        raise ValueError("saved layout view not found")
    for resource in main.findall("SimulationResources2/*"):
        host = resource.findtext("host", resource.findtext("simulationHost", "Local"))
        if host != "Local" or resource.findtext("startType", "Now") != "Now":
            raise ValueError("only local, immediate Momentum runs are supported")
        directory = resource.find("SimulationDirectory")
        if directory is not None and (directory.findtext("customPath", "").strip() or directory.findtext("sharedLocal", "True") != "True"):
            raise ValueError("custom simulation directories are not supported; use the workspace default")
    if main.findtext("MomSimDataset/writeDataset") != "True" or main.findtext("OutputPlan/writeDataset", "True") != "True":
        raise ValueError("enable Momentum dataset output in ADS before running")
    if main.findtext("MomSimReuseVisualization/reusePreviousResults") != "False":
        raise ValueError("disable reusePreviousResults for a fresh Momentum solve")
    plans = [p for p in main.findall("MomSimFreqTable/freqPlanMap/MomSimFreqTableDef")
             if p.findtext("enableFreq") == "True"]
    rf = [p for p in plans if p.findtext("typeFreq") == "1"]
    dc = [p for p in plans if p.findtext("typeFreq") == "3" and float(p.findtext("startFreq", "nan")) == 0]
    if len(rf) != 1 or len(dc) > 1 or len(rf) + len(dc) != len(plans):
        raise ValueError("exactly one enabled linear frequency plan and optional discrete DC plan are required")
    plan = rf[0]

    def frequency(tag: str) -> float:
        unit = plan.findtext(tag + "Unit", "").lower()
        if unit not in _UNITS:
            raise ValueError(f"unsupported frequency unit: {unit!r}")
        return float(plan.findtext(tag, "nan")) * _UNITS[unit]

    start, stop, step = frequency("startFreq"), frequency("stopFreq"), frequency("stepFreq")
    points = int(plan.findtext("ptsFreq", "0"))
    if not all(math.isfinite(v) for v in (start, stop, step)) or not 0 < start < stop or step <= 0:
        raise ValueError("frequency limits and step must be finite and positive")
    if not 2 <= points <= 100_000 or not math.isclose(start + step * (points - 1), stop, rel_tol=1e-10, abs_tol=1e-3):
        raise ValueError("linear sweep limits, step and point count are inconsistent")
    sim_dir = _inside(ws / "simulation" / lib_dir.name / cell_dir.name / views[0].name / (setup + "_MoM"), ws)
    lock_name = hashlib.sha256(f"{lib}:{cell}:{view}:{setup}".encode("utf-8")).hexdigest()[:24] + ".json"
    run_lock = _inside(ws / ".ads-mcp" / "locks" / lock_name, ws)
    frequency_info = {"start_hz": start, "stop_hz": stop, "step_hz": step, "points": points}
    if dc:
        frequency_info.update(points=points + 1, rf_points=points, include_dc=True)
    return {"workspace": str(ws), "setup_file": str(xml), "library_dir": str(lib_dir),
            "lib": lib, "cell": cell, "view": view, "setup": setup,
            "simulation_dir": str(sim_dir), "log": str(sim_dir / "proj.log"),
            "frequency": frequency_info,
            "simulation_mode": "microwave",
            "requires_gui": True, "run_lock": str(run_lock), "existing_run_lock": run_lock.exists()}


class OfficialAdsClient:
    """Small synchronous stdio client for the installed Keysight GUI bridge."""

    def __init__(self, executable: str | Path, timeout: float = 60) -> None:
        self.executable = Path(executable)
        self.timeout = timeout
        self._messages: queue.Queue = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=30)
        self._serial = 0
        self.process: subprocess.Popen | None = None
        self._readers: list[threading.Thread] = []

    def __enter__(self):
        if not self.executable.is_file():
            raise FileNotFoundError(f"Keysight GUI bridge not found: {self.executable}")
        self.process = subprocess.Popen([str(self.executable)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding="utf-8", errors="replace", bufsize=1,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

        def read_stdout():
            assert self.process is not None and self.process.stdout is not None
            for line in self.process.stdout:
                try:
                    self._messages.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
            self._messages.put(None)

        def read_stderr():
            assert self.process is not None and self.process.stderr is not None
            for line in self.process.stderr:
                self._stderr.append(line.rstrip())

        self._readers = [threading.Thread(target=read_stdout, daemon=True),
                         threading.Thread(target=read_stderr, daemon=True)]
        for reader in self._readers:
            reader.start()
        try:
            self._request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                        "clientInfo": {"name": "ads-mcp-momentum", "version": __version__}})
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_):
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()  # only this helper; never terminate the ADS GUI or solver
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        for reader in self._readers:
            reader.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream:
                stream.close()

    def _send(self, message: dict) -> None:
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def _request(self, method: str, params: dict) -> dict:
        self._serial += 1
        request_id = self._serial
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Keysight GUI bridge timed out in {method}; no retry was sent")
            try:
                message = self._messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(f"Keysight GUI bridge timed out in {method}; no retry was sent") from exc
            if message is None:
                raise RuntimeError("Keysight GUI bridge exited: " + "\n".join(self._stderr))
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"Keysight GUI bridge error: {message['error']}")
            return message["result"]

    def call(self, name: str, arguments: dict) -> dict:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError(f"Keysight {name}: {result.get('content')}")
        texts = [c["text"] for c in result.get("content", []) if c.get("type") == "text"]
        if len(texts) != 1:
            raise RuntimeError(f"unexpected response from Keysight {name}")
        payload = json.loads(texts[0])
        if not isinstance(payload, dict):
            raise RuntimeError(f"unexpected payload from Keysight {name}")
        if payload.get("ok") is False or payload.get("success") is False:
            raise RuntimeError(f"Keysight {name}: {payload.get('stderr') or payload}")
        return payload

    def execute(self, code: str) -> dict:
        payload = self.call("execute_python", {"code": code})
        if payload.get("mode") != "remote":
            raise RuntimeError("Momentum requires a remote ADS GUI session")
        lines = payload.get("stdout", "").splitlines()
        for line in reversed(lines):
            if line.startswith(_MARKER):
                return json.loads(line[len(_MARKER):])
        raise RuntimeError("ADS GUI did not return the expected Momentum response")


def _remote_code(info: dict, launch: bool) -> str:
    # JSON is data, never executable input. Names and paths are checked again
    # inside ADS to avoid acting on another workspace after a user switches it.
    data = repr(json.dumps({k: info[k] for k in ("workspace", "library_dir", "lib", "cell", "view", "setup")}))
    return f'''import json
from pathlib import Path
import keysight.ads.de as de
args = json.loads({data})
if not de.workspace_is_open():
    raise RuntimeError("Open the requested workspace in this ADS GUI session first")
if Path(de.active_workspace().path).resolve() != Path(args["workspace"]).resolve():
    raise RuntimeError("ADS GUI is using another workspace; no workspace was switched")
if Path(de.get_path_to_open_library(args["lib"])).resolve() != Path(args["library_dir"]).resolve():
    raise RuntimeError("GUI library path differs from the inspected local library")
ctx = de.ael.db_find_design_context_from_name(args["lib"]+":"+args["cell"]+":"+args["view"], 0)
linked = de.ael.deem_find_em_setup_view_name_for_context(ctx)
if linked != args["setup"]:
    raise RuntimeError("Layout is not linked to the inspected native EM Setup")
result = {{"validated": True, "setup": linked}}
if {launch!r}:
    result["accepted"] = bool(de.ael.deem_run_em_setup(ctx, linked))
print({_MARKER!r} + json.dumps(result))
'''


class MomentumRunner:
    """Dispatch once, then poll fresh disk outputs (also after MCP restart)."""

    def __init__(self, executable: str | Path, client_factory=OfficialAdsClient) -> None:
        self.executable = executable
        self.client_factory = client_factory
        self._start_lock = threading.Lock()
        self._poll_lock = threading.Lock()

    def list_sessions(self) -> dict:
        with self.client_factory(self.executable) as client:
            payload = client.call("list_sessions", {})
        return {"sessions": payload.get("sessions", [])}

    def start(self, state, session_id: int, workspace: str, setup_file: str, setup: str = "emSetup") -> dict:
        if type(session_id) is not int or session_id <= 0:
            raise ValueError("session_id must be a positive ADS GUI session id")
        with self._start_lock:
            info = inspect_setup(workspace, setup_file, setup)
            sim_dir = Path(info["simulation_dir"])
            sim_dir.mkdir(parents=True, exist_ok=True)
            # ADS may delete/rebuild simulation/ during dispatch. Reservations
            # must live outside that generated directory.
            lock = Path(info["run_lock"])
            lock.parent.mkdir(parents=True, exist_ok=True)
            if lock.exists():
                raise RuntimeError(f"Momentum run is already reserved: {lock}; poll its existing task")
            with self.client_factory(self.executable) as client:
                connected = client.call("connect_session", {"session_id": session_id})
                if not connected.get("connected") or connected.get("mode") != "remote":
                    raise RuntimeError("could not attach to the requested ADS GUI session")
                client.execute(_remote_code(info, launch=False))
                monitor = {**info, "token": uuid.uuid4().hex, "session_id": session_id,
                           "started_at_epoch": time.time(), "baseline_log": _signature(Path(info["log"])),
                           "baseline_datasets": {str(p.resolve()): _signature(p)
                                                 for p in (Path(info["workspace"]) / "data").glob("*.ds")}}
                task_id = state.create_external_task("momentum", monitor)
                try:
                    with lock.open("x", encoding="utf-8") as fh:
                        json.dump({"task_id": task_id, "token": monitor["token"]}, fh)
                except Exception as exc:
                    state.update_external_task(task_id, status="error", error=str(exc))
                    raise
                try:
                    result = client.execute(_remote_code(info, launch=True))
                except Exception as exc:
                    # Dispatch can outlive a bridge timeout. Keep the reservation;
                    # the solver is not cancelled and must never be retried blindly.
                    state.update_external_task(task_id, progress={"dispatch_uncertain": str(exc), "log": info["log"]})
                    return {"task_id": task_id, "state": "simulating", "dispatch_uncertain": True,
                            "note": "Poll this task; no automatic launch retry or cancellation was sent."}
                if not result.get("accepted"):
                    state.update_external_task(task_id, status="error", error="ADS declined the Momentum run")
                    self._release(monitor)
                    raise RuntimeError("ADS declined the Momentum run")
                return {"task_id": task_id, "state": "simulating", "log": info["log"], "frequency": info["frequency"]}

    @staticmethod
    def _release(monitor: dict) -> None:
        path = Path(monitor["run_lock"])
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("token") == monitor["token"]:
                path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _snapshot(monitor: dict, task_id: str, dataset: Path, log: Path) -> dict:
        """Keep task results stable when ADS reuses its native output name."""
        ws = Path(monitor["workspace"])
        target = _inside(ws / ".ads-mcp" / "results" / _name(task_id), ws)
        target.mkdir(parents=True, exist_ok=True)
        source_signature = _signature(dataset)
        for source in (dataset, log):
            destination = target / source.name
            temporary = destination.with_name(destination.name + ".tmp." + monitor["token"])
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        if _signature(dataset) != source_signature:
            raise RuntimeError("Momentum dataset changed while capturing task results; inspect the ADS run")
        return {"dataset": str(target / dataset.name), "log": str(target / log.name),
                "source_dataset": str(dataset), "source_log": str(log)}

    def poll(self, state, task_id: str, validate_dataset) -> dict:
        with self._poll_lock:
            return self._poll(state, task_id, validate_dataset)

    def _poll(self, state, task_id: str, validate_dataset) -> dict:
        task = state.task_status(task_id)
        if task["kind"] != "momentum" or task["status"] != "running":
            return task
        monitor = task["monitor"]
        lock = Path(monitor["run_lock"])
        if not lock.exists():
            state.update_external_task(task_id, status="error", error="run reservation was removed; no solver cancellation was sent")
            return state.task_status(task_id)
        if json.loads(lock.read_text(encoding="utf-8")).get("token") != monitor["token"]:
            state.update_external_task(task_id, status="error", error="run reservation belongs to another task")
            return state.task_status(task_id)
        log = Path(monitor["log"])
        signature = _signature(log)
        fresh = signature != monitor["baseline_log"] and signature is not None
        text, header = "", ""
        if fresh:
            with log.open("rb") as fh:
                header = fh.read(4096).decode("utf-8", errors="replace")
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 512_000))
                text = fh.read(512_000).decode("utf-8", errors="replace")
        progress = {**task.get("progress", {}), "log": str(log), "fresh_log": fresh,
                    "total_points": monitor["frequency"]["points"]}
        if signature != progress.get("last_log_signature"):
            progress.update(last_log_signature=signature, last_activity_epoch=time.time())
            progress.pop("attention_required", None)
        frequencies = re.findall(r"Simulation frequency \[(\d+)\] = ([^\r\n]+)", text)
        if frequencies:
            progress.update(current_point=int(frequencies[-1][0]), frequency=frequencies[-1][1])
        if text:
            progress["log_tail"] = text.splitlines()[-8:]
        if time.time() - monitor["started_at_epoch"] > 180 and not fresh:
            progress["attention_required"] = "No fresh log yet. Check ADS; the launch has not been retried."
        elif time.time() - progress.get("last_activity_epoch", monitor["started_at_epoch"]) > 300:
            progress["attention_required"] = "The solver log has not changed for 5 minutes. Check ADS; no cancellation was sent."
        state.update_external_task(task_id, progress=progress)
        failure = re.search(r"(?im)^\s*(?:-+\s*)?(?:ERROR\b|FATAL\b|Simulation (?:failed|aborted|terminated)\b).*$", text)
        if failure:
            state.update_external_task(task_id, status="error", error=failure[0], progress=progress)
            # Leave the reservation: an error line alone does not prove that
            # every solver process has exited. Inspect ADS before manual recovery.
        elif "S-parameter simulation finished" in text and "Simulation finished on:" in text:
            names = re.findall(r'The simulation results have been saved in dataset "([^"]+)"', text)
            if names:
                if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", names[-1]):
                    state.update_external_task(task_id, status="error", error="unsafe dataset name in solver log", progress=progress)
                    self._release(monitor)
                    return state.task_status(task_id)
                dataset = _inside(Path(monitor["workspace"]) / "data" / (names[-1] + ".ds"), Path(monitor["workspace"]))
                if dataset.is_file() and _signature(dataset) != monitor["baseline_datasets"].get(str(dataset)):
                    try:
                        if "Momentum MomEngine" not in header:
                            raise ValueError("fresh log does not identify the Momentum solver")
                        if not re.search(r"Simulation Mode:\s*(?:uW|MW|Microwave)\b", header, re.I):
                            raise ValueError("fresh log does not identify Momentum microwave mode")
                        grid = validate_dataset(str(dataset), monitor["frequency"])
                        captured = self._snapshot(monitor, task_id, dataset, log)
                    except Exception as exc:
                        state.update_external_task(task_id, status="error", error=str(exc), progress=progress)
                    else:
                        progress["current_point"] = monitor["frequency"]["points"]
                        state.update_external_task(task_id, status="done", progress=progress,
                                                   result={**captured, **grid, "simulation_mode": "microwave"})
                    self._release(monitor)
        return state.task_status(task_id)
