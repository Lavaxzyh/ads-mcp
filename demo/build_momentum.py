"""Build planar EM geometry/substrate/ports/setup through MCP, optionally solve.

Use a new workspace directory. Existing workspaces and design views are
preserved. Input geometry is explicit JSON; this is not a schematic converter.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parent.parent


async def call(session, name, arguments):
    response = await asyncio.wait_for(session.call_tool(name, arguments), timeout=300)
    result = json.loads(response.content[0].text)
    if response.isError or not result.get("ok"):
        raise RuntimeError(f"{name}: {result}")
    return result


async def run(args):
    ws = Path(args.workspace).expanduser().resolve()
    if ws.exists():
        raise ValueError("Use a new workspace directory; existing directories are not overwritten")
    model = json.loads(Path(args.model).read_text(encoding="utf-8"))
    env = dict(os.environ)
    env["ADS_MCP_STATE_FILE"] = str(ws / ".ads-mcp" / "pipeline_state.json")
    params = StdioServerParameters(command=sys.executable, args=[str(REPO / "mcp_server.py")], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Registered tools:", len((await session.list_tools()).tools), flush=True)
            await call(session, "ads_create_workspace", {"path": str(ws)})
            await call(session, "ads_create_library", {"name": model["lib"]})
            print("Substrate:", await call(session, "ads_create_substrate", {
                "lib": model["lib"], "name": model["substrate"],
                "dielectrics": model["dielectrics"], "conductors": model["conductors"],
                "ground_bottom": model.get("ground_bottom", True)}), flush=True)
            print("Layout:", await call(session, "ads_create_layout", {
                "lib": model["lib"], "cell": model["cell"], "shapes": model["shapes"]}), flush=True)
            print("Ports:", await call(session, "ads_add_layout_ports", {
                "lib": model["lib"], "cell": model["cell"], "ports": model["ports"]}), flush=True)
            setup = await call(session, "ads_configure_momentum_setup", {
                "lib": model["lib"], "cell": model["cell"], "substrate": model["substrate"],
                **model["sweep"], "setup": model.get("setup", "emSetup")})
            print("Setup:", json.dumps(setup, ensure_ascii=False), flush=True)
            await call(session, "ads_close_workspace", {})
            print("Build complete:", ws, flush=True)
            if not (args.open_gui or args.run):
                return
            print("GUI:", await call(session, "ads_open_in_gui", {"workspace": str(ws)}), flush=True)
            if not args.run:
                return
            deadline = time.monotonic() + 90
            session_id = None
            while time.monotonic() < deadline:
                discovered = await call(session, "ads_list_gui_sessions", {})
                matching = [s for s in discovered["sessions"]
                            if s.get("workspace_path") and Path(s["workspace_path"]).resolve() == ws]
                if matching:
                    session_id = matching[-1]["id"]
                    break
                await asyncio.sleep(2)
            if session_id is None:
                raise TimeoutError("ADS GUI did not announce this workspace within 90 seconds; no solver launch was sent")
            started = await call(session, "ads_run_momentum", {
                "session_id": session_id, "workspace": str(ws),
                "setup_file": setup["setup_file"], "setup": setup["setup"]})
            print("Task:", started, flush=True)
            previous = None
            while True:
                status = await call(session, "ads_get_sim_status", {"task_id": started["task_id"]})
                progress = status.get("progress", {})
                current = (status["status"], progress.get("current_point"), progress.get("attention_required"))
                if current != previous:
                    print("Progress:", current, progress.get("frequency", ""), flush=True)
                    previous = current
                if status["status"] == "error":
                    raise RuntimeError(status.get("error"))
                if status["status"] == "done":
                    print("Completed:", json.dumps(status["result"]), flush=True)
                    break
                await asyncio.sleep(5)
            results = await call(session, "ads_get_results", {})
            print("Results:", {k: v for k, v in results.items() if k != "preview"}, flush=True)
            print("Plot:", await call(session, "ads_plot_sparams", {}), flush=True)
            if len(model["ports"]) == 2:
                print("Touchstone:", await call(session, "ads_export_touchstone", {}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--model", default=str(REPO / "demo" / "models" / "stepped_lpf.json"))
    parser.add_argument("--open-gui", action="store_true")
    parser.add_argument("--run", action="store_true")
    asyncio.run(run(parser.parse_args()))
