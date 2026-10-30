"""Durable local RF queue. PID plus creation time, never heartbeat age, proves liveness."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager

import psutil
from rf_executor import process_alive
from rf_workflows import clean, sha256, write_json

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def start_windows_dispatcher(root):
    """Use the local Windows process broker for a job independent of MCP's
    kill-on-close Job Object. No service/task is installed and no elevation is
    requested. An access denial is reported, not bypassed with another account.
    """
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    service=startup=process=arguments=result=None
    try:
        service=win32com.client.GetObject(r"winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2")
        startup=service.Get("Win32_ProcessStartup").SpawnInstance_()
        startup.ShowWindow=0
        startup.CreateFlags=0x01000000 | 0x08000000 | 0x00000400 | 0x00000200
        startup.EnvironmentVariables=[f"{k}={v}" for k,v in os.environ.items()]
        process=service.Get("Win32_Process")
        arguments=process.Methods_("Create").InParameters.SpawnInstance_()
        arguments.CommandLine=subprocess.list2cmdline([sys.executable,"-B",str(Path(__file__).with_name("rf_job_worker.py")),"--broker",str(root)])
        arguments.CurrentDirectory=str(root)
        arguments.ProcessStartupInformation=startup
        result=process.ExecMethod_("Create",arguments)
        if result.ReturnValue:
            raise RuntimeError(f"Windows background process creation failed with WMI code {result.ReturnValue}")
        pid=int(result.ProcessId)
        return dict(pid=pid,born=psutil.Process(pid).create_time())
    finally:
        # Release COM wrappers while the apartment is still initialized.
        result=arguments=process=startup=service=None
        pythoncom.CoUninitialize()


def root_path():
    from ads_engine import get_workroot
    return (get_workroot()/"rf_jobs_v2").resolve()


class JobStore:
    def __init__(self, root=None):
        self.root = Path(root or root_path()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, control TEXT,
                  revision INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL,
                  plan_path TEXT NOT NULL, plan_hash TEXT NOT NULL, output_dir TEXT UNIQUE NOT NULL,
                  progress TEXT, result TEXT, error TEXT, worker_pid INTEGER, worker_born REAL);
                CREATE TABLE IF NOT EXISTS dispatcher (id INTEGER PRIMARY KEY CHECK(id=1), pid INTEGER, born REAL);
                INSERT OR IGNORE INTO dispatcher(id) VALUES(1);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root/"jobs.sqlite3", timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def submit(self, kind, plan):
        job_id = "rf_"+uuid.uuid4().hex
        folder = self.root/job_id
        folder.mkdir()
        plan = dict(plan, execution_environment={k:os.environ[k] for k in ("HPEESOF_DIR","HOME","MPLCONFIGDIR") if k in os.environ})
        write_json(folder/"plan.json", plan)
        now = time.time()
        with self.connect() as db:
            db.execute("INSERT INTO jobs(id,kind,status,created,updated,plan_path,plan_hash,output_dir) VALUES(?,?,?,?,?,?,?,?)",
                       (job_id, kind, "queued", now, now, str(folder/"plan.json"), sha256(folder/"plan.json"), plan["output_dir"]))
        self.ensure_dispatch()
        return dict(ok=True, task_id=job_id, job_id=job_id, status="queued", total_cases=plan.get("total_cases"),
                    output_dir=plan["output_dir"], next_tool="ads_rf_job", jobs_root=str(self.root))

    def ensure_dispatch(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            owner = db.execute("SELECT * FROM dispatcher WHERE id=1").fetchone()
            if process_alive(owner["pid"], owner["born"]):
                return
            for job in db.execute("SELECT * FROM jobs WHERE status='running'").fetchall():
                if process_alive(job["worker_pid"], job["worker_born"]):
                    # The native host may still be finishing. Never dispatch a duplicate.
                    return
                db.execute("UPDATE jobs SET status='interrupted', error=?, revision=revision+1, updated=? WHERE id=?",
                           ("Dispatcher exited; owned worker is confirmed gone. Explicit resume reuses verified checkpoints.", time.time(), job["id"]))
            if not db.execute("SELECT 1 FROM jobs WHERE status='queued' LIMIT 1").fetchone():
                db.execute("UPDATE dispatcher SET pid=NULL,born=NULL WHERE id=1")
                return
            # A completed bootstrap parent disconnects the dispatcher from MCP
            # clients that terminate the entire server process tree on EOF.
            if os.name=="nt":
                owner=start_windows_dispatcher(self.root)
            else:
                with (self.root/"dispatcher.log").open("ab") as log:
                    started = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("rf_job_worker.py")), "--detach", str(self.root)],
                                     cwd=str(self.root), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log,
                                     creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0), timeout=12, check=True)
                owner=json.loads(started.stdout.decode("utf-8"))
            db.execute("UPDATE dispatcher SET pid=?,born=? WHERE id=1", (owner["pid"], owner["born"]))

    def row(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown RF job_id in this workroot")
        return dict(row)

    def update(self, job_id, **changes):
        allowed = {"status", "control", "progress", "result", "error", "worker_pid", "worker_born"}
        if not changes.keys() <= allowed:
            raise ValueError("Unsupported job update field")
        for key in ("progress", "result"):
            if key in changes:
                changes[key] = json.dumps(clean(changes[key]), ensure_ascii=False, allow_nan=False)
        changes["updated"] = time.time()
        with self.connect() as db:
            db.execute("UPDATE jobs SET "+",".join(k+"=?" for k in changes)+",revision=revision+1 WHERE id=?", (*changes.values(),job_id))

    def status(self, job_id, full=False):
        self.ensure_dispatch()
        row = self.row(job_id)
        result = json.loads(row["result"]) if row["result"] else None
        answer = {k:row[k] for k in ("id","kind","status","revision","created","updated","output_dir","error")}
        answer.update(ok=True, job_id=row["id"], task_id=row["id"], progress=json.loads(row["progress"]) if row["progress"] else {},
                      control=row["control"], summary_file=str(Path(row["output_dir"])/"summary.json"))
        if result:
            if full:
                answer["result"] = result
            else:
                keys = ("completed", "successful_cases", "failed_cases", "evaluated_cases", "simulation_attempts", "new_simulation_attempts", "cache_hits", "resumed_cases", "feasible_cases", "pareto_cases", "stop_reason", "source_netlist_unchanged", "audit_passed")
                brief = {k:result[k] for k in keys if k in result}
                best = result.get("best_case")
                if best:
                    brief["best"] = {k:best[k] for k in ("case","parameters","feasible","max_error_ohm","objective_score","pout_dbm","pae_pct","gain_db") if k in best}
                    brief["best"]["criteria"] = best.get("criteria", [])
                if result.get("candidate_review"):
                    brief["candidate_review"] = result["candidate_review"][:3]
                    brief["best_point_is_not_whole_band_acceptance"] = True
                answer["result"] = brief
        return answer

    def wait(self, job_id, after_revision=-1, timeout_seconds=20):
        if not 0 <= timeout_seconds <= 25:
            raise ValueError("wait_seconds must be 0..25")
        self.ensure_dispatch()
        end = time.monotonic()+timeout_seconds
        while True:
            row = self.row(job_id)
            if row["revision"] > after_revision or row["status"] in TERMINAL|{"paused"} or time.monotonic() >= end:
                return self.status(job_id)
            time.sleep(0.15)

    def control_job(self, job_id, action):
        self.ensure_dispatch()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown RF job_id")
            status, control = row["status"], row["control"]
            if action == "pause":
                if status == "queued":
                    status = "paused"
                elif status == "running":
                    control = "pause"
                elif status != "paused":
                    raise ValueError("Only queued or running jobs can be paused")
            elif action == "cancel":
                if status == "running":
                    control = "cancel"
                elif status in {"queued","paused","interrupted"}:
                    if process_alive(row["worker_pid"], row["worker_born"]):
                        raise RuntimeError("Owned native worker is still alive; wait for confirmed exit")
                    status, control = "cancelled", None
                elif status != "cancelled":
                    raise ValueError("Job is already terminal")
            elif action == "resume":
                if status not in {"paused","interrupted"}:
                    raise ValueError("Only paused or interrupted jobs can be resumed")
                if process_alive(row["worker_pid"], row["worker_born"]):
                    raise RuntimeError("Owned worker is still alive; resume must not duplicate it")
                status, control = "queued", None
            else:
                raise ValueError("action must be pause, cancel or resume")
            db.execute("UPDATE jobs SET status=?,control=?,revision=revision+1,updated=? WHERE id=?", (status,control,time.time(),job_id))
        self.ensure_dispatch()
        return self.status(job_id)

    def list_jobs(self, limit=10):
        if not 1 <= limit <= 50:
            raise ValueError("limit must be 1..50")
        self.ensure_dispatch()
        with self.connect() as db:
            rows = db.execute("SELECT id,kind,status,revision,updated,output_dir FROM jobs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return dict(ok=True, jobs=[dict(r) for r in rows])

    def detail(self, job_id, offset=0, limit=10, fields=None, failures_only=False):
        if offset < 0 or not 1 <= limit <= 50:
            raise ValueError("Use offset>=0 and limit=1..50")
        row = self.row(job_id)
        path = Path(row["output_dir"])/"cases.json"
        if path.is_file():
            rows = json.loads(path.read_text(encoding="utf-8"))
        else:
            rows = [json.loads(p.read_text(encoding="utf-8"))["row"] for p in sorted((Path(row["output_dir"])/"checkpoints").glob("*.json"))]
        allowed = {"case","phase","status","parameters","error","feasible","constraint_violation","criteria","max_error_ohm","rms_error_ohm","objective_score","objective_vector","cache_hit","resumed","pout_dbm","pae_pct","gain_db","convergence","harmonics","conditions","directory","dataset","points_csv"}
        fields = fields or ["case","status","parameters","feasible","max_error_ohm","pout_dbm","pae_pct","gain_db","error"]
        if not set(fields) <= allowed:
            raise ValueError("Unknown detail field")
        if failures_only:
            rows = [r for r in rows if r.get("status") != "done" or r.get("feasible") is False]
        page = rows[offset:offset+limit]
        return dict(ok=True, job_id=job_id, total=len(rows), next_offset=offset+len(page) if offset+len(page)<len(rows) else None,
                    rows=[{k:r[k] for k in fields if k in r} for r in page])
