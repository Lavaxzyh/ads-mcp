"""Run a prepared native example through the public MCP stdio tools.

Set HPEESOF_DIR and use an interpreter with ADS/MCP dependencies installed.
Open the example workspace in ADS GUI first. Never opens the source workspace.
"""
import argparse,asyncio,json,os,sys,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
REPO=Path(__file__).resolve().parent.parent

async def call(session,name,args):
    response=await session.call_tool(name,args)
    if response.isError:raise RuntimeError(str(response.content))
    result=response.structuredContent or json.loads(response.content[0].text)
    if result.get("ok") is False:raise RuntimeError(str(result))
    return result

async def run(args):
    folder=REPO/"examples"/args.example;ws=folder/"workspace"
    config=json.loads((folder/"example.json").read_text())
    env=dict(os.environ);env["ADS_MCP_STATE_FILE"]=str(ws/".ads-mcp/example_state.json")
    async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(REPO/"mcp_server.py")],env=env)) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            sessions=await call(session,"ads_list_gui_sessions",{})
            matches=[x for x in sessions["sessions"] if x.get("workspace_path") and Path(x["workspace_path"]).resolve()==ws.resolve()]
            if args.session_id:
                matches=[x for x in matches if x["id"]==args.session_id]
            if len(matches)!=1:raise RuntimeError("Open this example in exactly one ADS GUI session, or select --session-id")
            sid=matches[0]["id"]
            if args.action=="inspect":
                print(json.dumps(await call(session,"ads_inspect_em_model",dict(lib=config["lib"],cell=config["metal_cell"],session_id=sid,workspace=str(ws))),indent=2));return
            if args.action=="optimize":
                if "optimizer" not in config:raise ValueError("This example is not an optimization case")
                designs=[config["optimizer"]]
            else:designs=config["testbenches"]
            for design in designs:
                result=await call(session,"ads_run_simulation",dict(backend="gui",session_id=sid,workspace=str(ws),design=design,mode="optimize" if args.action=="optimize" else "simulate"))
                tid=result["task_id"];deadline=time.monotonic()+args.timeout
                while time.monotonic()<deadline:
                    result=await call(session,"ads_get_sim_status",dict(task_id=tid))
                    if result["status"]!="running":break
                    await asyncio.sleep(2)
                else:raise TimeoutError("Task still running; poll its task_id, do not redispatch")
                print(json.dumps(result,indent=2))
                if result["status"]!="done":raise RuntimeError(result.get("error"))
                if args.action=="optimize":
                    print(json.dumps(await call(session,"ads_release_optimizer",dict(task_id=tid)),indent=2))

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("example",choices=["pin_phase_shifter_1p4ghz","microstrip_patch_em_optimization"])
    p.add_argument("--action",choices=["inspect","simulate","optimize"],default="inspect")
    p.add_argument("--session-id",type=int);p.add_argument("--timeout",type=float,default=1800)
    asyncio.run(run(p.parse_args()))
