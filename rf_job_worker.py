"""Detached serial queue dispatcher; never opens a GUI or a source workspace."""
import itertools
import json
from pathlib import Path
import os
import sys
import time
import subprocess

import psutil
from rf_jobs import JobStore
from rf_executor import Executor, JobCancelled
from rf_workflows import SweepParameter, sha256, write_json


class JobPaused(BaseException):
    pass


def run_grid(plan, progress):
    from matching_runs import CaseRunner, SimulationBudgetReached
    targets=[SweepParameter.model_validate(p) for p in plan["parameters"]]
    runner=CaseRunner(plan,targets,plan["total_cases"],progress,plan.get("cache_dir",""),plan.get("use_cache",False))
    for values in itertools.product(*(p.values for p in targets)):
        try:
            row=runner.evaluate(values,"sweep")
        except SimulationBudgetReached:
            break
        if row["status"]=="error" and plan.get("stop_on_error",True):
            break
    return runner.finish(total_cases=plan["total_cases"],attempted_cases=len(runner.rows),
                         skipped_cases=plan["total_cases"]-len(runner.rows),
                         incomplete=len(runner.rows)!=plan["total_cases"])


def run_job(store, row):
    job_id=row["id"]
    plan_path=Path(row["plan_path"])
    if sha256(plan_path)!=row["plan_hash"]:
        raise ValueError("Frozen job plan was modified")
    plan=json.loads(plan_path.read_text(encoding="utf-8"))
    os.environ.update(plan.get("execution_environment",{}))
    out=Path(plan["output_dir"])
    marker=out/"job-owner.json"
    if out.exists():
        if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")).get("job_id")!=job_id:
            raise ValueError("Output exists without this job's ownership marker")
        plan["_resume"]=True
    else:
        out.mkdir(parents=True,exist_ok=False)
        write_json(marker,dict(job_id=job_id,plan_hash=row["plan_hash"]))
        plan["_resume"]=True
    # Pin implementation version; a resumed job must not silently change algorithms.
    code_hashes={p.name:sha256(p) for p in Path(__file__).parent.glob("*.py")}
    code_file=out/"code-fingerprint.json"
    if code_file.exists() and json.loads(code_file.read_text(encoding="utf-8"))!=code_hashes:
        raise ValueError("RF code changed since the checkpoint; keep this result and submit a new job")
    write_json(code_file,code_hashes)
    install=Path(os.environ["HPEESOF_DIR"])
    runtime_hashes={str(p):sha256(p) for p in [install/"bin/hpeesofsim.exe",install/"bin/hpeesofsim_private.dll",install/"bin/hpeesofsim.cfg"]}
    config=Path(os.environ.get("HOME",str(Path.home())))/"hpeesof/config"
    runtime_hashes.update({str(p):sha256(p) for p in config.rglob("*") if p.is_file() and p.suffix in {".cfg",".defs"}})
    runtime_file=out/"runtime-fingerprint.json"
    if runtime_file.exists() and json.loads(runtime_file.read_text(encoding="utf-8"))!=runtime_hashes:
        raise ValueError("ADS runtime/config changed since the checkpoint; submit a new job")
    write_json(runtime_file,runtime_hashes)
    if sha256(plan["source_netlist"])!=plan["source_sha256"]:
        raise ValueError("Source netlist changed since submission")
    last_check=[0.,None]
    def cancel_check(force=False):
        now=time.monotonic()
        if force or now-last_check[0]>0.2:
            last_check[:]=[now,store.row(job_id)["control"]]
        if last_check[1]=="cancel":
            raise JobCancelled()
    def progress(data):
        cancel_check(True)
        store.update(job_id,progress=data)
        if last_check[1]=="pause":
            raise JobPaused()
    executor=Executor(store.root/job_id, cancel_check, lambda p,b:store.update(job_id,worker_pid=p,worker_born=b))
    plan["_executor"]=executor
    try:
        if row["kind"]=="hb":
            from hb_review import run_hb
            result=run_hb(plan,progress)
        elif row["kind"]=="optimize":
            from rf_optimizer import run_optimization
            result=run_optimization(plan,progress)
        elif row["kind"] in ("sweep","sp"):
            result=run_grid(plan,progress)
        else:
            from matching_advanced import run_search,run_tolerance
            result={"search":run_search,"tolerance":run_tolerance}[row["kind"]](plan,progress)
        result["python_worker_starts"]=executor.starts
        write_json(out/"summary.json",result)
        store.update(job_id,status="completed" if result["completed"] else "failed",result=result,control=None,
                     error=None if result["completed"] else "Some cases failed or the requested experiment is incomplete; inspect case details")
    except JobCancelled:
        store.update(job_id,status="cancelled",control=None,error="Cancelled by caller; completed checkpoints retained")
    except JobPaused:
        store.update(job_id,status="paused",control=None)
    finally:
        executor.close()


def main():
    store=JobStore(sys.argv[1])
    me=psutil.Process()
    while True:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            owner=db.execute("SELECT * FROM dispatcher WHERE id=1").fetchone()
            # Windows venv python.exe can be a launcher with a separate Python
            # child. The recorded Popen PID is its ancestor, not necessarily us.
            owners=[me]+me.parents()
            if not any(p.pid==owner["pid"] and abs((owner["born"] or 0)-p.create_time())<0.01 for p in owners):
                return
            row=db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if row is None:
                db.execute("UPDATE dispatcher SET pid=NULL,born=NULL WHERE id=1")
                return
            db.execute("UPDATE jobs SET status='running',revision=revision+1,updated=?,error=NULL WHERE id=?",(time.time(),row["id"]))
        try:
            run_job(store,dict(row))
        except Exception as exc:
            store.update(row["id"],status="failed",error=f"{type(exc).__name__}: {exc}")


if __name__=="__main__":
    if len(sys.argv)>1 and sys.argv[1]=="--broker":
        import contextlib
        root=Path(sys.argv[2]).resolve(strict=True)
        sys.argv=[sys.argv[0],str(root)]
        with (root/"dispatcher.log").open("a",encoding="utf-8",buffering=1) as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
            try:
                main()
            except BaseException:
                import traceback
                traceback.print_exc()
                raise
    elif len(sys.argv)>1 and sys.argv[1]=="--detach":
        root=Path(sys.argv[2]).resolve(strict=True)
        with (root/"dispatcher.log").open("ab") as log:
            process=subprocess.Popen([sys.executable,"-B",str(Path(__file__).resolve()),str(root)],cwd=str(root),
                       stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,close_fds=True,
                       creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)|getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0))
        print(json.dumps(dict(pid=process.pid,born=psutil.Process(process.pid).create_time())),flush=True)
    else:
        main()
