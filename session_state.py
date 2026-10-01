"""Session state + task model for ads-mcp.

A tiny state machine persisted to disk (atomic tmp+replace writes) so the
server can be restarted without forgetting what it was doing, and so every
mutation records which state it came from / went to.  Long operations
(simulation) run as background tasks and are tracked by task id; callers
poll instead of blocking the tool call.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

#States: idle -> building -> simulating -> analyzing -> back to idle; error is a side-state.
VALID_STATES = {"idle", "building", "simulating", "analyzing", "error"}

_TRANSITIONS: dict[str, set[str]] = {
    "idle": {"building", "simulating", "analyzing", "error"},
    "building": {"idle", "building", "simulating", "analyzing", "error"},
    "simulating": {"idle", "analyzing", "error"},
    "analyzing": {"idle", "building", "simulating", "analyzing", "error"},
    "error": set(VALID_STATES),
}


class StateError(RuntimeError):
    pass


class SessionState:
    """One JSON file per server process; all writes atomic, all mutations locked."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {
            "state": "idle",
            "workspace_path": None,
            "current_schematic": None,
            "gui_pid": None,
            "updated_at": None,
            "tasks": {},
        }
        self._load()

    # ---- persistence ----
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._data.update(data)
        except (OSError, json.JSONDecodeError):
            # corrupt state file: keep defaults, never block startup
            pass

    def _save(self) -> None:
        self._data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---- state machine ----
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            out = dict(self._data)
            out["tasks"] = {
                tid: {k: v for k, v in t.items() if k != "thread"}
                for tid, t in self._data["tasks"].items()
            }
            return out

    def transition(self, new_state: str) -> None:
        with self._lock:
            old = self._data["state"]
            if new_state not in VALID_STATES:
                raise StateError(f"unknown state {new_state!r}")
            if new_state == old:
                return  # no-op transition is always legal
            if new_state not in _TRANSITIONS.get(old, set()):
                raise StateError(f"illegal transition {old} -> {new_state}")
            self._data["state"] = new_state
            self._save()

    def set_field(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._save()

    # ---- tasks ----
    def submit(self, kind: str, fn: Callable[[], Any]) -> str:
        """Run fn in a background thread; track by task id. fn returns a JSON-safe dict."""
        task_id = f"{kind}_{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._data["tasks"][task_id] = {
                "kind": kind,
                "status": "running",
                "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "finished_at": None,
                "result": None,
                "error": None,
            }
            self._save()

        def _run() -> None:
            try:
                result = fn()
                with self._lock:
                    self._data["tasks"][task_id]["result"] = result
                    self._data["tasks"][task_id]["status"] = "done"
                    self._data["tasks"][task_id]["finished_at"] = time.strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    )
                    self._save()
            except Exception as exc:  # noqa: BLE001 - surface everything to the caller
                with self._lock:
                    self._data["tasks"][task_id]["error"] = f"{type(exc).__name__}: {exc}"
                    self._data["tasks"][task_id]["status"] = "error"
                    self._data["tasks"][task_id]["finished_at"] = time.strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    )
                    self._save()

        threading.Thread(target=_run, name=f"ads-task-{task_id}", daemon=True).start()
        return task_id

    def task_status(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._data["tasks"].get(task_id)
            if task is None:
                raise KeyError(f"unknown task id {task_id!r}")
            return {k: v for k, v in task.items()}

    def wait_task(self, task_id: str, timeout: float = 300.0, poll: float = 0.2) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            st = self.task_status(task_id)
            if st["status"] in ("done", "error"):
                return st
            time.sleep(poll)
        raise TimeoutError(f"task {task_id} still running after {timeout:g}s")
