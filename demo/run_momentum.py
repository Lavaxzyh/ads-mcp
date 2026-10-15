"""Run a prepared native Momentum setup through the structured MCP tools.

Open the workspace in ADS first. This demo never creates or edits designs.
Use --resume-task with the same state file after a client/server restart.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def call(session, name: str, arguments: dict) -> dict:
    response = await asyncio.wait_for(session.call_tool(name, arguments), timeout=300)
    payload = json.loads(response.content[0].text)
    if response.isError or not payload.get("ok"):
        raise RuntimeError(f"{name}: {payload}")
    return payload


async def run(args):
    repo = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    if args.state_file:
        env["ADS_MCP_STATE_FILE"] = str(Path(args.state_file).resolve())
    params = StdioServerParameters(command=sys.executable, args=[str(repo / "mcp_server.py")], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            print("Server:", initialized.serverInfo.version, flush=True)
            tools = await session.list_tools()
            print("Registered tools:", len(tools.tools), flush=True)
            info = await call(session, "ads_inspect_momentum_setup", {
                "workspace": args.workspace, "setup_file": args.setup_file})
            print("Sweep:", info["frequency"], flush=True)
            if args.resume_task:
                task_id = args.resume_task
            else:
                sessions = await call(session, "ads_list_gui_sessions", {})
                print("GUI sessions:", sessions["sessions"], flush=True)
                started = await call(session, "ads_run_momentum", {
                    "session_id": args.session_id, "workspace": args.workspace, "setup_file": args.setup_file})
                task_id = started["task_id"]
                print("Task:", json.dumps(started), flush=True)
            previous = None
            while True:
                status = await call(session, "ads_get_sim_status", {"task_id": task_id})
                progress = status.get("progress", {})
                current = (status["status"], progress.get("current_point"), progress.get("attention_required"))
                if current != previous:
                    print("Progress:", current, progress.get("frequency", ""), flush=True)
                    previous = current
                if status["status"] != "running":
                    print("Final task:", json.dumps(status, ensure_ascii=False), flush=True)
                    if status["status"] != "done":
                        raise RuntimeError(status.get("error") or "Momentum did not complete")
                    break
                await asyncio.sleep(5)
            results = await call(session, "ads_get_results", {})
            print("Results:", json.dumps({k: v for k, v in results.items() if k != "preview"}), flush=True)
            print("Plot:", await call(session, "ads_plot_sparams", {}), flush=True)
            print("Touchstone:", await call(session, "ads_export_touchstone", {}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--setup-file", required=True)
    parser.add_argument("--session-id", type=int, required=True)
    parser.add_argument("--state-file", default="")
    parser.add_argument("--resume-task", default="")
    asyncio.run(run(parser.parse_args()))
