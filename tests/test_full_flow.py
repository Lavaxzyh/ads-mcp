"""Full acceptance test: design + simulate an SI-LPF purely through ads-mcp tools.

Exercises the complete tool surface including the background-task simulation
path (de calls from a worker thread), result reading, plotting, and Touchstone
export — everything a conversational session would do.
"""
import asyncio
import json
import os
import shutil

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON = str(REPO / ".venv" / "Scripts" / "python.exe")
SERVER = str(REPO / "mcp_server.py")
WS = str(Path.home() / "ads_mcp_demo" / "acceptance_wrk")

X = [0.5, 2.0, 3.5, 5.0, 6.5, 8.0]
SEG = [("Wh", "Lh"), ("Wl", "Ll"), ("Wh", "Lh"), ("Wl", "Ll"), ("Wh", "Lh"), ("Wl", "Ll")]


def payload(res):
    return json.loads(res.content[0].text)


async def call(session, tool, args):
    res = await asyncio.wait_for(session.call_tool(tool, args), timeout=120)
    out = payload(res)
    assert out.get("ok"), f"{tool} failed: {out}"
    return out


async def main() -> None:
    params = StdioServerParameters(
        command=PYTHON, args=[SERVER],
        env={**os.environ, "HPEESOF_DIR": r"D:\Program Files\Keysight\ADS2027"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            shutil.rmtree(WS, ignore_errors=True)

            await call(session, "ads_create_workspace", {"path": WS})
            await call(session, "ads_create_library", {"name": "si_lpf_lib"})
            await call(session, "ads_create_schematic", {"lib": "si_lpf_lib", "cell": "lpf5"})

            for x, (w, l), name in zip(X, SEG, ["TL1", "TL2", "TL3", "TL4", "TL5", "TL6"]):
                await call(session, "ads_place_component", {
                    "lib": "ads_tlines", "cell": "MLIN", "x": x, "y": 0.0,
                    "instance_name": name, "params": {"W": w, "L": l},
                })
            await call(session, "ads_add_term", {"name": "Term1", "x": 0.0, "y": 0.0})
            await call(session, "ads_add_term", {"name": "Term2", "x": 9.5, "y": 0.0})
            edges = [(0.0, X[0])] + [(X[i] + 1.0, X[i + 1]) for i in range(5)] + [(X[-1] + 1.0, 9.5)]
            for x1, x2 in edges:
                await call(session, "ads_add_wire", {"points": [[x1, 0.0], [x2, 0.0]]})
            await call(session, "ads_place_component", {"lib": "ads_tlines", "cell": "MSUB", "x": 5.0, "y": -2.5})
            for n, v in (("Wh", "0.2 mm"), ("Wl", "8 mm"), ("Lh", "11 mm"), ("Ll", "3 mm")):
                await call(session, "ads_add_var", {"name": n, "value": v})
            await call(session, "ads_place_component", {
                "lib": "ads_simulation", "cell": "S_Param", "x": 9.5, "y": -2.5,
                "instance_name": "SP1",
                "params": {"Start": "0.05 GHz", "Stop": "6 GHz", "Step": "25 MHz"},
            })

            task = await call(session, "ads_run_simulation", {})
            tid = task["task_id"]
            print("sim task:", tid, flush=True)
            for _ in range(120):
                st = await call(session, "ads_get_sim_status", {"task_id": tid})
                if st["status"] in ("done", "error"):
                    break
                await asyncio.sleep(0.5)
            assert st["status"] == "done", st
            print("sim done, dataset:", st["result"]["dataset"], flush=True)

            results = await call(session, "ads_get_results", {})
            s21 = results["summary"]["S1,2"]
            print(f"S21: passband {s21['db_at_fmin']} dB | cutoff {s21.get('first_below_-3dB_hz', 0)/1e9:.2f} GHz | 6GHz {s21['db_at_fmax']} dB", flush=True)
            assert s21["db_at_fmin"] > -2.0 and s21["db_at_fmax"] < -20

            ts = await call(session, "ads_export_touchstone", {})
            plot = await call(session, "ads_plot_sparams", {"columns": ["S[1,1]", "S[1,2]"]})
            print("touchstone:", ts["touchstone"], flush=True)
            print("plot:", plot["plot"], flush=True)
            print("\nFULL ACCEPTANCE TEST PASSED", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
