"""Small-schema MCP facade for persisted RF jobs and paginated evidence."""
import json
from pathlib import Path
from typing import Literal
from rf_jobs import JobStore


def prepare_request(workflow, arguments):
    if workflow=="sp":
        from matching_sweep import prepare_sp_verification
        return prepare_sp_verification(**arguments)
    if workflow=="optimize":
        from rf_optimizer import prepare_optimization
        return prepare_optimization(**arguments)
    if workflow=="hb":
        from hb_review import prepare_hb
        return prepare_hb(**arguments)
    if workflow=="sweep":
        from matching_sweep import prepare_sweep
        arguments=dict(arguments)
        stop=arguments.pop("stop_on_error",True)
        return dict(**prepare_sweep(**arguments),stop_on_error=stop)
    if workflow in {"search","tolerance"}:
        from matching_advanced import prepare_advanced
        return prepare_advanced(workflow,**arguments)
    raise ValueError("workflow must be sp, optimize, hb, sweep, search or tolerance")


def register(mcp):
    @mcp.tool()
    def ads_submit_rf_job(workflow:Literal["sp","optimize","hb","sweep","search","tolerance"], request_file:str)->dict:
        """Submit an immutable RF request JSON as a durable serial job. Survives client disconnect.

        Read RF-JOBS.zh-CN.md and examples/rf_jobs for contracts. JSON contains
        the workflow arguments, including source netlist, fresh output directory,
        conditions and budgets. Original workspaces are never modified. Returns
        job_id immediately. Use ads_rf_job(wait) for compact progress/results.
        """
        try:
            source=Path(request_file).expanduser().resolve(strict=True)
            if source.stat().st_size>2_000_000:
                raise ValueError("Request JSON must not exceed 2 MB")
            arguments=json.loads(source.read_text(encoding="utf-8-sig"))
            if not isinstance(arguments,dict):
                raise ValueError("Request must be a JSON object")
            return JobStore().submit(workflow,prepare_request(workflow,arguments))
        except Exception as exc:
            return dict(ok=False,error=f"{type(exc).__name__}: {exc}")

    @mcp.tool()
    def ads_rf_job(action:Literal["status","wait","pause","resume","cancel","list","detail"], job_id:str="",
                   after_revision:int=-1, wait_seconds:int=20, offset:int=0, limit:int=10,
                   fields:list[str]|None=None, failures_only:bool=False)->dict:
        """Manage durable RF jobs without retransmitting full results.

        wait returns on revision change, terminal state or <=25 seconds; a wait
        timeout is not job failure. Pause finishes the current case then stops;
        resume verifies/reuses saved cases; cancel stops only owned processes.
        detail pages <=50 cases with selected fields or failures_only. Full
        precision results and logs remain in output_dir. The workroot identifies
        the shared queue; list retrieves its recent jobs across MCP sessions.
        """
        try:
            store=JobStore()
            if action=="list":
                return store.list_jobs(limit)
            if action=="status":
                return store.status(job_id)
            if action=="wait":
                return store.wait(job_id,after_revision,wait_seconds)
            if action=="detail":
                return store.detail(job_id,offset,limit,fields,failures_only)
            return store.control_job(job_id,action)
        except Exception as exc:
            return dict(ok=False,error=f"{type(exc).__name__}: {exc}")
