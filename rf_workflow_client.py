"""Call the new MCP tools immediately, even if a desktop connection is cached.

Usage: ADS-venv-python rf_workflow_client.py request.json [--ads-root <ADS_INSTALL>]
request.json: {"tool": "<RF workflow tool name>", "arguments": {...}}
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
from ads_engine import get_ads_install_dir
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(request, ads_root):
    if request.get("tool") not in {"ads_analyze_loadpull", "ads_run_matching_sweep", "ads_build_loadpull_regions", "ads_search_matching_network", "ads_analyze_matching_tolerance", "ads_submit_rf_job", "ads_rf_job", "ads_build_schematic"} or not isinstance(request.get("arguments"),dict):
        raise ValueError("Use a supported RF workflow/job tool with an arguments object")
    if not (Path(ads_root)/"bin/hpeesofsim.exe").is_file():
        raise ValueError("Specify the ADS installation with --ads-root")
    runtime=Path(tempfile.mkdtemp(prefix="ads-mcp-rf-call-"))
    config=runtime/"home/hpeesof/config/SystemVue"
    config.mkdir(parents=True)
    (config/"lib.defs").write_text("# Isolated RF client session\n",encoding="utf-8")
    source=Path(__file__).resolve().parent
    env=dict(os.environ,HPEESOF_DIR=str(Path(ads_root).resolve()),HOME=str(runtime/"home"),
             ADS_MCP_STATE_FILE=str(runtime/"state.json"),PYTHONUTF8="1",PYTHONIOENCODING="utf-8",PYTHONDONTWRITEBYTECODE="1")
    env.setdefault("ADS_MCP_WORKROOT",str(source.parent/"outputs"))
    with (runtime/"server.log").open("w",encoding="utf-8") as errors:
      async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(source/"mcp_server.py")],cwd=str(runtime),env=env),errlog=errors) as (rd,wr):
       async with ClientSession(rd,wr) as session:
        await asyncio.wait_for(session.initialize(),timeout=60)
        async def call(name,arguments):
            response=await asyncio.wait_for(session.call_tool(name,arguments),timeout=120)
            payload="\n".join(c.text for c in response.content if c.type=="text")
            if response.isError:
                # MCP schema/validation failures are ordinary text, not JSON.
                raise RuntimeError(payload)
            result=json.loads(payload)
            if not result.get("ok",True):
                raise RuntimeError(result.get("error",str(result)))
            return result
        result=await call(request["tool"],request["arguments"])
        if "task_id" in result and request["tool"]!="ads_rf_job":
            task_id=result["task_id"]
            last_progress=None
            revision=-1
            while True:
                if task_id.startswith("rf_"):
                    result=await call("ads_rf_job",{"action":"wait","job_id":task_id,"after_revision":revision,"wait_seconds":20})
                    revision=result["revision"]
                else:
                    result=await call("ads_get_sim_status",{"task_id":task_id})
                progress=result.get("progress")
                if progress!=last_progress:
                    print(json.dumps({"task_id":task_id,"progress":progress},ensure_ascii=False),file=sys.stderr,flush=True)
                    last_progress=progress
                if result["status"] not in {"running","queued"}: break
                if not task_id.startswith("rf_"):
                    await asyncio.sleep(1)
            if task_id.startswith("rf_") and request["tool"] not in {"ads_submit_rf_job","ads_rf_job"}:
                # Keep the historical client's full result contract; the new
                # file-based submission entry defaults to compact results.
                result=await call("ads_get_sim_status",{"task_id":task_id})
        result["client_runtime_dir"]=str(runtime)
        print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
        return 1 if result.get("status") in {"error","failed","interrupted","cancelled"} else 0


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("--ads-root",default=str(get_ads_install_dir()))
    args=parser.parse_args()
    payload=json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
    raise SystemExit(asyncio.run(run(payload,args.ads_root)))
