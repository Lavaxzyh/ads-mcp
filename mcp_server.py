#!/usr/bin/env python3
"""ads-mcp: an open, headless-first MCP server for Keysight ADS.

Built on keysight.ads.de (the official Python API shipped with ADS 2027).
v0.1 focuses on the microstrip schematic -> S-parameter simulation loop.

Run:  <ads-mcp>/.venv/Scripts/python.exe mcp_server.py   (stdio transport)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ads_engine import AdsEngine, ensure_env
from session_state import SessionState, StateError

_HERE = Path(__file__).resolve().parent
DEFAULT_WORKROOT = r"D:\ADS_Project\ads_mcp_demo"

INSTRUCTIONS = """Drive Keysight ADS (Advanced Design System) headlessly for
RF/microwave schematic work.  Typical loop: ads_check_installed ->
ads_create_workspace -> ads_create_library -> ads_create_schematic ->
ads_place_component / ads_add_wire / ads_add_var / ads_add_term (build the
schematic; the substrate instance is auto-named MSub1) -> ads_generate_netlist
-> ads_run_simulation (returns a task id; poll with ads_get_sim_status) ->
ads_get_results / ads_plot_sparams / ads_export_touchstone.  Use
ads_browse_components to discover library/cell/parameter names before placing
unfamiliar parts.  Microstrip cells (MLIN, MSUB, MTEE, ...) live in
`ads_tlines`; controllers and ports in `ads_simulation`; VAR/MeasEqn in
`ads_datacmps`.  Simulation requires the schematic on file: the tools save
automatically before netlisting."""

mcp = FastMCP("ads-mcp", instructions=INSTRUCTIONS)
# FastMCP does not expose the server version; report ours instead of the SDK's
mcp._mcp_server.version = "0.1.0"
engine = AdsEngine()
state = SessionState(_HERE / "state" / "session_state.json")


def _ok(**kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("ok", True)
    return kwargs


def _err(exc: Exception, **context: Any) -> dict[str, Any]:
    out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    out.update(context)
    return out


@mcp.tool()
def ads_check_installed() -> dict[str, Any]:
    """Check ADS install, simulator binary, and the headless DE engine."""
    try:
        return _ok(**engine.check_installed())
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_create_workspace(path: str = "") -> dict[str, Any]:
    """Create (or open if existing) an ADS workspace at `path` and open it."""
    try:
        target = path.strip() or str(Path(DEFAULT_WORKROOT) / "work_wrk")
        result = engine.create_workspace(target)
        state.set_field("workspace_path", result["workspace"])
        state.set_field("current_schematic", None)
        state.transition("idle")
        return _ok(**result)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_open_workspace(path: str) -> dict[str, Any]:
    """Open an existing ADS workspace."""
    try:
        result = engine.open_workspace(path)
        state.set_field("workspace_path", result["workspace"])
        state.set_field("current_schematic", None)
        state.transition("idle")
        return _ok(**result)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_close_workspace() -> dict[str, Any]:
    """Close the currently open workspace."""
    try:
        result = engine.close_workspace()
        state.set_field("workspace_path", None)
        state.set_field("current_schematic", None)
        state.transition("idle")
        return _ok(**result)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_session_status() -> dict[str, Any]:
    """Return session state: open workspace, current schematic, task history."""
    return _ok(**state.snapshot())


@mcp.tool()
def ads_create_library(name: str) -> dict[str, Any]:
    """Create a design library inside the open workspace and attach it."""
    try:
        state.transition("building")
        return _ok(**engine.create_library(name))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_create_schematic(lib: str, cell: str) -> dict[str, Any]:
    """Create (or open) a schematic cell `lib:cell` as the current design."""
    try:
        state.transition("building")
        result = engine.create_schematic(lib, cell)
        state.set_field("current_schematic", result["schematic"])
        return _ok(**result)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_browse_components(lib: str = "") -> dict[str, Any]:
    """List component libraries and cells (with known parameter names).

    Call without `lib` to enumerate all standard libraries; with `lib` to list
    that library's cells.  Microstrip parts live in `ads_tlines`, controllers
    and ports in `ads_simulation`, VAR/MeasEqn in `ads_datacmps`.
    """
    try:
        return _ok(**engine.browse_components(lib.strip() or None))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_place_component(
    lib: str,
    cell: str,
    x: float,
    y: float,
    instance_name: str = "",
    angle: float = 0.0,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Place a component instance on the current schematic.

    `params` maps parameter names to value strings with units, e.g.
    {"W": "0.3 mm", "L": "20 mm"} for ads_tlines:MLIN or {"Er": "4.4"} for
    MSUB.  The substrate (MSUB) is auto-named MSub1; never set its `Subst`
    references on lines - the default already points at MSub1.
    """
    try:
        state.transition("building")
        result = engine.place_component(
            lib, cell, x, y,
            instance_name=instance_name.strip() or None,
            angle=angle, params=params,
        )
        return _ok(**result)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_add_wire(points: list[list[float]], label: str = "") -> dict[str, Any]:
    """Add a polyline wire through `points` [[x1,y1],[x2,y2],...]; optional net label."""
    try:
        state.transition("building")
        return _ok(**engine.add_wire(points, label.strip() or None))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_add_var(name: str, value: str, opt_min: str = "", opt_max: str = "") -> dict[str, Any]:
    """Define a schematic variable in the VAR block, e.g. ("W_hi", "0.3 mm").

    Component parameters can then reference the name (e.g. W="W_hi").
    Optional opt_min/opt_max turn it into an optimization variable
    (e.g. opt_min="0.5 mm", opt_max="1.4 mm").
    """
    try:
        state.transition("building")
        return _ok(**engine.add_var(name, value, opt_min.strip() or None, opt_max.strip() or None))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_add_goal(expr: str, sim_name: str, goal_type: str, bound: str,
                 fmin: str, fmax: str, weight: float = 1.0) -> dict[str, Any]:
    """Add an optimization goal (ads_simulation:Goal).

    goal_type 'min' keeps `expr` at or above `bound` (e.g. passband S21);
    'max' keeps it at or below `bound` (e.g. stopband rejection, return loss
    limit). `expr` like "dB(S(1,2))", `sim_name` is the analysis controller
    name (e.g. "SP1"), fmin/fmax bracket the evaluation band.
    """
    try:
        state.transition("building")
        return _ok(**engine.add_goal(expr, sim_name, goal_type, bound, fmin, fmax, weight))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_add_optim(optim_type: str = "gradient", max_iters: int = 60) -> dict[str, Any]:
    """Place the optimization controller (ads_simulation:Optim).

    It automatically collects every variable that has an opt range and every
    goal on the schematic. optim_type: gradient / random / quasinewton.
    """
    try:
        state.transition("building")
        return _ok(**engine.add_optim(optim_type, int(max_iters)))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_add_term(name: str, x: float, y: float, z: str = "50 Ohm") -> dict[str, Any]:
    """Place an S-parameter port (Term) with its ground; name like Term1/Term2."""
    try:
        state.transition("building")
        return _ok(**engine.add_term(name, x, y, z))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_add_ground(x: float, y: float) -> dict[str, Any]:
    """Place a ground symbol at (x, y)."""
    try:
        state.transition("building")
        return _ok(**engine.add_ground(x, y))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_list_instances() -> dict[str, Any]:
    """List instances placed on the current schematic."""
    try:
        return _ok(**engine.list_instances())
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_generate_netlist(save: bool = True) -> dict[str, Any]:
    """Generate the netlist of the current schematic and return its text."""
    try:
        if save:
            engine.save_schematic()
        result = engine.generate_netlist()
        return _ok(netlist_chars=len(result["netlist"]), **result)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_run_simulation(output_dir: str = "") -> dict[str, Any]:
    """Save the schematic, netlist it, and run hpeesofsim in the background.

    Returns a `task_id`; poll with ads_get_sim_status.  The result carries the
    dataset path for ads_get_results / ads_plot_sparams / ads_export_touchstone.
    """
    try:
        ws = state.get("workspace_path")
        engine.save_schematic()
        netlist_result = engine.generate_netlist()
        state.transition("simulating")
        out_dir = output_dir.strip() or str(
            Path(str(ws or DEFAULT_WORKROOT)).parent / "sim_out"
        )

        def _run() -> dict[str, Any]:
            try:
                result = engine.run_simulation(out_dir, netlist_result["netlist"])
                state.transition("idle")
                return result
            except Exception:
                try:
                    state.transition("error")
                except StateError:
                    pass
                raise

        task_id = state.submit("simulation", _run)
        return _ok(task_id=task_id, output_dir=out_dir, state="simulating")
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_get_sim_status(task_id: str) -> dict[str, Any]:
    """Poll a simulation task submitted with ads_run_simulation."""
    try:
        st = state.task_status(task_id)
        return _ok(**st)
    except KeyError as exc:
        return _err(exc)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_get_results(dataset: str = "", varblock: str = "") -> dict[str, Any]:
    """Read a simulation dataset (.ds): S-parameter summary and data preview."""
    try:
        ds_path = dataset.strip()
        if not ds_path:
            last = _last_dataset_from_tasks()
            if not last:
                raise RuntimeError("no dataset path given and no finished simulation found")
            ds_path = last
        state.transition("analyzing")
        return _ok(**engine.get_results(ds_path, varblock.strip() or None))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def _last_dataset_from_tasks() -> str:
    for st in state.snapshot()["tasks"].values():
        if st["status"] == "done" and st.get("result", {}).get("dataset"):
            return st["result"]["dataset"]
    return ""


@mcp.tool()
def ads_export_touchstone(dataset: str = "", out_path: str = "") -> dict[str, Any]:
    """Export the simulated S-parameters as a Touchstone .s2p file."""
    try:
        ds_path = dataset.strip() or _last_dataset_from_tasks()
        if not ds_path:
            raise RuntimeError("no dataset available; run a simulation first")
        out = out_path.strip() or str(Path(ds_path).with_suffix(".s2p"))
        return _ok(**engine.export_touchstone(ds_path, out))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_plot_sparams(dataset: str = "", out_png: str = "", columns: list[str] | None = None) -> dict[str, Any]:
    """Plot |S| in dB vs frequency to a PNG (e.g. columns ["S[1,1]", "S[1,2]"])."""
    try:
        ds_path = dataset.strip() or _last_dataset_from_tasks()
        if not ds_path:
            raise RuntimeError("no dataset available; run a simulation first")
        out = out_png.strip() or str(Path(ds_path).with_suffix(".png"))
        return _ok(**engine.plot_sparams(ds_path, out, columns))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_open_in_gui() -> dict[str, Any]:
    """Open the current workspace in the ADS GUI for visual inspection.

    The GUI process is detached from this server, so it survives MCP restarts.
    """
    try:
        ws = state.get("workspace_path")
        if not ws:
            raise RuntimeError("no workspace open in this session")
        result = engine.open_in_gui(ws)
        state.set_field("gui_pid", result["gui_pid"])
        return _ok(**result)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


if __name__ == "__main__":
    ensure_env()
    # import the native de engine on the MAIN thread: _pde/Qt must not be
    # first-initialized from a worker thread (silent native abort)
    from ads_engine import get_de

    get_de()
    mcp.run()
    # the de engine leaves Qt threads behind; without a hard exit the process
    # would hang after stdin EOF and block every MCP client's shutdown
    import os

    os._exit(0)
