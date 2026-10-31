"""One owned, reusable Python simulator host per durable job; solvers stay serial."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager

import psutil
from rf_workflows import write_json


class JobCancelled(BaseException):
    pass


@contextmanager
def native_children_job():
    """On Windows, a crashed simulator host must not orphan hpeesofsim.
    The private job contains only this host and its future descendants.
    """
    if os.name!="nt":
        yield
        return
    import win32api,win32job
    handle=win32job.CreateJobObject(None,"")
    info=win32job.QueryInformationJobObject(handle,win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"]|=win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(handle,win32job.JobObjectExtendedLimitInformation,info)
    win32job.AssignProcessToJobObject(handle,win32api.GetCurrentProcess())
    try:
        yield
    finally:
        if not psutil.Process().children(recursive=True):
            info["BasicLimitInformation"]["LimitFlags"]&=~win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(handle,win32job.JobObjectExtendedLimitInformation,info)
        handle.Close()


def record_attempt(out, number, digest):
    # Reserve budget durably before dispatch, including attempts interrupted
    # before a case checkpoint exists. Restarting must not reset this counter.
    with (Path(out)/"attempts.jsonl").open("a",encoding="utf-8") as stream:
        stream.write(json.dumps(dict(case=number,netlist_sha256=digest,time=time.time()))+"\n")
        stream.flush()
        os.fsync(stream.fileno())


def attempt_count(out):
    path=Path(out)/"attempts.jsonl"
    if not path.exists():
        return 0
    records=[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return len(records)


def process_alive(pid, born):
    if not pid or born is None:
        return False
    try:
        return abs(psutil.Process(pid).create_time() - born) < 0.01
    except psutil.NoSuchProcess:
        return False
    # AccessDenied deliberately propagates: uncertain ownership must not restart.


def terminate_owned(pid, born):
    if not process_alive(pid, born):
        return
    parent = psutil.Process(pid)
    children = parent.children(recursive=True)
    for proc in reversed(children):
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
    try:
        parent.kill()
    except psutil.NoSuchProcess:
        pass
    psutil.wait_procs(children+[parent], timeout=10)


class Executor:
    def __init__(self, out, control=lambda: None, register=lambda p,b: None):
        self.folder = Path(out) / ("worker_" + uuid.uuid4().hex[:10])
        self.folder.mkdir(parents=True)
        self.control, self.register = control, register
        self.process = None
        self.sequence = 0
        self.starts = 0

    def start(self):
        self.log = (self.folder/"host.log").open("a", encoding="utf-8")
        me = psutil.Process()
        self.process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).with_name("matching_worker.py")),
                          "--serve", str(self.folder), str(me.pid), str(me.create_time())],
                          cwd=str(self.folder), stdout=self.log, stderr=subprocess.STDOUT,
                          creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        self.born = psutil.Process(self.process.pid).create_time()
        self.register(self.process.pid, self.born)
        self.starts += 1

    def execute(self, case, rendered, evaluation, data_directory, timeout_seconds, extra=None):
        self.control()
        if self.process is None or self.process.poll() is not None:
            self.start()
        case = Path(case)
        case.mkdir(parents=True, exist_ok=False)
        (case/"input.net").write_text(rendered, encoding="utf-8")
        write_json(case/"request.json", dict(evaluation=evaluation, data_directory=data_directory, **(extra or {})))
        self.sequence += 1
        write_json(self.folder/"next.json", dict(sequence=self.sequence, request_file=str(case/"request.json")))
        started = time.monotonic()
        try:
            while True:
                self.control()
                done = self.folder/"done.json"
                if done.exists() and json.loads(done.read_text(encoding="utf-8"))["sequence"] == self.sequence:
                    break
                if self.process.poll() is not None:
                    raise RuntimeError(f"Simulator host exited ({self.process.returncode}); see {self.folder/'host.log'}")
                if time.monotonic()-started > timeout_seconds:
                    raise TimeoutError("Case exceeded timeout_seconds; only the owned worker tree is stopped")
                time.sleep(0.05)
        except BaseException:
            self.close(force=True)
            raise
        result = json.loads((case/"result.json").read_text(encoding="utf-8"))
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "Native worker failed"))
        return {k:v for k,v in result.items() if k != "ok"}

    def close(self, force=False):
        if self.process is None:
            return
        if force:
            terminate_owned(self.process.pid, self.born)
        else:
            (self.folder/"stop").write_text("stop", encoding="utf-8")
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                terminate_owned(self.process.pid, self.born)
        self.log.close()
        self.register(None, None)
        self.process = None
