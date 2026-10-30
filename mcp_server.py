#!/usr/bin/env python3
"""ads-mcp: structured design and simulation tools for Keysight ADS.

Based on upstream ads-mcp v0.4.0; this branch adapts the native API to ADS 2025.
Circuit simulation and native Momentum simulation with an ADS GUI session.

Run:  <ads-mcp>/.venv/Scripts/python.exe mcp_server.py   (stdio transport)
"""
from __future__ import annotations

import json
import os
import functools
import threading
from pathlib import Path
from typing import Any

from runtime_identity import RuntimeIdentity
_RUNTIME_IDENTITY = RuntimeIdentity(Path(__file__).resolve().parent)

from mcp.server.fastmcp import FastMCP

from ads_engine import AdsEngine, ensure_env, get_ads_install_dir, get_workroot, get_state_file
from momentum import MomentumRunner, inspect_setup
from em_modeling import ConductorLayer, DielectricLayer, EmModeler, LayoutPort, LayoutShape, ViaLayer
from session_state import SessionState, StateError
from version import __version__
from circuit_runner import CircuitRunner

_HERE = Path(__file__).resolve().parent

INSTRUCTIONS = """Drive Keysight ADS using verified local workflows. Default:
ads_design_workflow(action=run), one independent task directory and one target
schematic. Read SINGLE-SCHEMATIC.zh-CN.md beside this server. Reuse a private
design_workflow_client.py session; build/import once, edit only requested fields,
save/reopen and verify. Supply explicit topology, models, board, ports, reference
planes and units. No historical engineering defaults. Originals remain read-only.
Return compact receipts; full snapshots, netlists, logs and evidence stay in files.
After a timeout inspect the same request/job ID; do not redispatch or cancel by
assumption. Use operation=status for async SP. Never infer tokens from byte counts.

Existing routes: DESIGN-WORKFLOW.zh-CN.md covers extraction, reviewed copies,
capabilities, matching-result materialization and evidence comparisons;
SCHEMATIC-WORKFLOW.zh-CN.md covers full BuildPlan. EM-WORKFLOW.zh-CN.md covers
native ADS 2025 artwork/PSN/lookalike, Momentum, emModel and bounded Gradient
optimization. Native EM needs an identified task-owned GUI; never attach to or
stop arbitrary user sessions. Native MaxIters is not a hard EM-solve-count budget.
RF-WORKFLOWS.zh-CN.md and RF-ADVANCED.zh-CN.md cover Load-pull analysis, matching
search, frequency targets and tolerances. RF-JOBS.zh-CN.md covers durable jobs,
compact wait/status and paginated detail. Keep task/workspace ownership isolated.

Run ads_runtime_selfcheck when starting a task or after deployment. Report stale
code/catalog, unsupported inputs and repeated adapter gaps; do not turn routine
design into unlimited tool development. Simulation requires explicit scope and
authorization. Structural acceptance, SP, EM, nonlinear HB and measurements are
different evidence. Metadata, initial values or SP do not establish PA power,
PAE or stability. No Computer Use, global ADS changes or other-session restarts.
"""

mcp = FastMCP("ads-mcp", instructions=INSTRUCTIONS)
# FastMCP does not expose the server version; report ours instead of the SDK's
mcp._mcp_server.version = __version__
engine = AdsEngine()
state = SessionState(get_state_file())
momentum = MomentumRunner(get_ads_install_dir() / "bin" / "ads-mcp.exe")
em_modeler = EmModeler()
circuit_runner = CircuitRunner()
_workspace_dispatch_lock = threading.RLock()


def _serialize_workspace(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        with _workspace_dispatch_lock:
            return fn(*args, **kwargs)
    return wrapped


def _ok(**kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("ok", True)
    return kwargs


def _err(exc: Exception, **context: Any) -> dict[str, Any]:
    out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    out.update(context)
    return out


def _ensure_no_running_tasks() -> None:
    if any(t["status"] == "running" for t in state.snapshot()["tasks"].values()):
        raise StateError("a simulation task is running; poll ads_get_sim_status before changing workspaces or starting another")


@mcp.tool()
def ads_check_installed() -> dict[str, Any]:
    """Check ADS install, simulator binary, and the headless DE engine."""
    try:
        return _ok(**engine.check_installed())
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_runtime_selfcheck(client_tool_names: list[str] | None = None,
                          disabled_tools: list[str] | None = None,
                          expected_build_id: str | None = None) -> dict[str, Any]:
    """Identify this running build, disk changes, ADS environment and optional client catalog.

    Read-only. Pass actual client tool names to detect an old/filtered catalog;
    declare intentional exclusions with disabled_tools. Never restarts sessions.
    """
    report = _RUNTIME_IDENTITY.report(mcp._tool_manager._tools.keys(),
        client_tool_names=client_tool_names, disabled_tools=disabled_tools,
        expected_build_id=expected_build_id)
    install = get_ads_install_dir()
    try:
        report['ads'] = engine.check_installed()
        from ads_engine import get_de
        de = get_de()
        report['ads']['native_product_version'] = str(de.product_version())
        report['ads']['python_de_version'] = str(getattr(de, '__version__', 'unknown'))
    except Exception as exc:
        report['ads'] = {'ok': False, 'install': str(install), 'error': str(exc)}
    import importlib.util
    modules={name:importlib.util.find_spec(name) is not None for name in ('mcp','pydantic','numpy','pandas','psutil','win32com','pythoncom')}
    report['dependencies'] = {'official_gui_bridge': (install/'bin/ads-mcp.exe').is_file(),
        'managed_ads2025_bridge': (Path(__file__).parent/'gui_bridge_bootstrap.py').is_file(),
        'mcp_python_packages':modules,'missing_mcp_packages':[n for n,present in modules.items() if not present],
        'note':'The MCP virtualenv and native ADS GUI Python are separate interpreters; managed GUI code uses native ADS/PySide2 plus stdlib.'}
    from gui_bridge_2025 import discover
    report['owned_gui_sessions']=discover()
    report['known_limits'] = [
        'ADS 2025 native paths require a task-owned managed GUI and workspace; arbitrary existing ADS GUIs are not attached or stopped.',
        'Native emModel testbench/candidate/Gradient optimizer constructors currently support two-port, real 50-ohm linear SP; one-variable microstrip optimization is end-to-end verified.',
        'Native MaxIters limits optimizer iterations, not EM solve count. Cache parameter resolution is distinct from geometric grid precision.',
        'Layout generation reports omitted artwork. Package footprints, nonlinear PA HB, custom PDK and external substrate dependencies need case-specific validation.',
        'Existing loaded EM Setup changes may require workspace reopen; native pre-save rejects unexpected explicit-setting changes.',
        'A timed-out or failed native task may retain a workspace reservation; inspect the original job before recovery. No automatic redispatch or forced cancellation.',
    ]
    return _ok(**report)


@mcp.tool()
def ads_manage_gui_session(action: str, task_directory: str = '', workspace: str = '',
                           session_id: int | None = None) -> dict[str, Any]:
    """Start/list/stop only task-owned ADS 2025 GUI sessions in independent workspaces.

    start requires a workspace contained inside task_directory. Never attaches
    to arbitrary pre-existing ADS GUIs. Stop only after all owned tasks finish.
    """
    from gui_bridge_2025 import start_session, discover, ManagedAdsClient
    if action == 'list':
        return _ok(sessions=discover())
    if action == 'start':
        if engine.workspace_path is not None:
            raise RuntimeError('Close the MCP-owned workspace before opening a dedicated GUI')
        if not task_directory or not workspace:
            raise ValueError('task_directory and workspace are required')
        return _ok(**start_session(task_directory,workspace))
    if action == 'stop':
        _ensure_no_running_tasks()
        if session_id is None:
            raise ValueError('session_id is required')
        with ManagedAdsClient() as client:
            client.call('connect_session',{'session_id':session_id})
            from workspace_activity import assert_idle
            assert_idle(client.record['workspace'])
            return _ok(**client.call('shutdown_owned',{}))
    raise ValueError('action must be start, list or stop')


@mcp.tool()
@_serialize_workspace
def ads_create_workspace(path: str = "") -> dict[str, Any]:
    """Create (or open if existing) an ADS workspace at `path` and open it."""
    try:
        _ensure_no_running_tasks()
        target = path.strip() or str(get_workroot() / "work_wrk")
        result = engine.create_workspace(target)
        state.set_field("workspace_path", result["workspace"])
        state.set_field("current_schematic", None)
        state.transition("idle")
        return _ok(**result)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
@_serialize_workspace
def ads_open_workspace(path: str) -> dict[str, Any]:
    """Open an existing ADS workspace."""
    try:
        _ensure_no_running_tasks()
        result = engine.open_workspace(path)
        state.set_field("workspace_path", result["workspace"])
        state.set_field("current_schematic", None)
        state.transition("idle")
        return _ok(**result)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
@_serialize_workspace
def ads_close_workspace() -> dict[str, Any]:
    """Close the currently open workspace."""
    try:
        _ensure_no_running_tasks()
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
    ports and MeasEqn in `ads_simulation`, VAR in `ads_datacmps`.
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
@_serialize_workspace
def ads_run_simulation(output_dir: str = "", backend: str = "standalone", session_id: int | None = None,
                       workspace: str = "", design: str = "", mode: str = "simulate", data_directory: str = "") -> dict[str, Any]:
    """Save the schematic, netlist it, and run hpeesofsim in the background.

    Returns a `task_id`; poll with ads_get_sim_status.  The result carries the
    dataset path for ads_get_results / ads_plot_sparams / ads_export_touchstone.
    """
    try:
        _ensure_no_running_tasks()
        if backend == "gui":
            if data_directory:
                raise ValueError("GUI uses workspace vendor-library search paths; data_directory is for standalone runs")
            if engine.workspace_path is not None:
                raise StateError("Close MCP-owned workspace before running in ADS GUI")
            if mode not in {"simulate", "optimize"} or not session_id or not workspace or not design:
                raise ValueError("GUI run requires session_id, workspace, schematic design, and simulate/optimize mode")
            return _ok(**circuit_runner.start(state, session_id, workspace, design, mode))
        if backend != "standalone" or mode != "simulate":
            raise ValueError("Use backend=gui for native optimization; standalone defaults remain unchanged")
        ws = state.get("workspace_path")
        engine.save_schematic()
        netlist_result = engine.generate_netlist()
        state.transition("simulating")
        out_dir = output_dir.strip() or str(
            (Path(ws).parent if ws else get_workroot()) / "sim_out"
        )

        def _run() -> dict[str, Any]:
            try:
                result = engine.run_simulation(out_dir, netlist_result["netlist"], data_directory=data_directory) if data_directory else engine.run_simulation(out_dir, netlist_result["netlist"])
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
    """Poll circuit or Momentum tasks. Momentum reports live frequency progress.

    Momentum monitoring survives server restarts; polling does not relaunch
    the solver. A bridge timeout is uncertain dispatch, never a retry signal.
    """
    try:
        if task_id.startswith("rf_"):
            from rf_jobs import JobStore
            result=JobStore().status(task_id,full=True)
            native_status=result["status"]
            result["job_status"]=native_status
            result["status"]="done" if native_status=="completed" else "running" if native_status in {"queued","running","paused"} else "error"
            return result
        st = state.task_status(task_id)
        if st["kind"] == "momentum":
            st = momentum.poll(state, task_id, engine.validate_momentum_dataset)
        elif st["kind"] == "gui_circuit":
            st = circuit_runner.poll(state, task_id, engine._read_dataframe)
        st.pop("monitor", None)
        return _ok(**st)
    except KeyError as exc:
        return _err(exc)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_list_gui_sessions() -> dict[str, Any]:
    """Discover GUI sessions through the official bridge or the owned ADS 2025 registry.

    Returns session IDs and workspace paths. Does not start or close ADS.
    """
    try:
        return _ok(**momentum.list_sessions())
    except Exception as exc:
        return _err(exc)


def _model_operation(lib: str, operation: str, *, session_id: int | None = None, workspace: str = "", **data) -> dict[str, Any]:
    try:
        _ensure_no_running_tasks()
        if session_id is not None:
            if engine.workspace_path is not None:
                raise StateError("close this server's workspace before modeling in the selected GUI")
            if not workspace:
                raise ValueError("An explicit workspace is required for GUI modeling")
            return _ok(**em_modeler.execute(workspace, lib, operation, session_id=session_id, **data))
        if engine.workspace_path is None:
            raise StateError("open a workspace with ads_create_workspace or ads_open_workspace before modeling")
        with engine._op():
            return _ok(**em_modeler.execute(engine.workspace_path, lib, operation, **data))
    except Exception as exc:
        return _err(exc)


@mcp.tool()
@_serialize_workspace
def ads_create_substrate(lib: str, name: str,
                         dielectrics: list[DielectricLayer], conductors: list[ConductorLayer],
                          ground_bottom: bool = True, vias: list[ViaLayer] | None = None) -> dict[str, Any]:
    """Create a parameterized planar substrate in the current local library.

    dielectrics are bottom-to-top, with height_mm, er and loss_tangent.
    conductors map existing drawing layer names to interface 1..N above N
    dielectrics, with thickness_um and optional conductivity_s_per_m (null =
    perfect conductor). Bottom ground is an ideal cover when enabled; top is
    open air. A new substrate name is required; existing substrates/materials
    are preserved. New libraries receive standard ADS layout technology.
    """
    return _model_operation(lib, "substrate", name=name,
                             dielectrics=dielectrics, conductors=conductors, ground_bottom=ground_bottom,
                             vias=vias or [])


@mcp.tool()
@_serialize_workspace
def ads_create_layout(lib: str, cell: str,
                      shapes: list[LayoutShape], view: str = "layout") -> dict[str, Any]:
    """Create and save a planar layout from parameterized rectangles/polygons.

    Each shape specifies kind, points_mm and an existing drawing layer name.
    A rectangle has lower-left/upper-right corners; polygons have distinct
    vertices without a repeated closing point. Coordinates are millimeters
    and converted to the library's layout units. Existing views are rejected.
    This accepts explicit geometry; it does not infer arbitrary schematic or
    PDK artwork. Use ads_add_layout_ports after creation.
    """
    return _model_operation(lib, "layout", cell=cell, view=view, shapes=shapes)


@mcp.tool()
@_serialize_workspace
def ads_add_layout_ports(lib: str, cell: str,
                         ports: list[LayoutPort], view: str = "layout") -> dict[str, Any]:
    """Add numbered point pins to an existing saved layout in this workspace.

    Each pin specifies name, number, x_mm, y_mm, optional angle, layer and
    net. Names and numbers must be distinct, including existing terminals.
    For Momentum, use consecutive terminal numbers starting at 1. Port
    impedance/reference mapping is created by ads_configure_momentum_setup.
    """
    return _model_operation(lib, "ports", cell=cell, view=view, ports=ports)


@mcp.tool()
@_serialize_workspace
def ads_configure_momentum_setup(lib: str, cell: str,
                                  substrate: str, start_ghz: float, stop_ghz: float, points: int,
                                  view: str = "layout", setup: str = "emSetup",
                                  session_id: int | None = None, workspace: str = "") -> dict[str, Any]:
    """Create/link a native Momentum setup from the local ADS RFBoard template.

    Uses existing local layout and substrate, a single linear frequency sweep,
    microwave mode, local execution, fresh results and numbered single-pin 50-ohm ground-
    referenced ports with a bottom ground cover. The installed vendor template
    is read at runtime; none is distributed. Existing setup names are rejected
    to retain prior setups.
    The native dataset uses the cell name; completed tasks retain unique
    copies. The returned setup_file can be passed to ads_run_momentum after
    closing the server workspace and opening the saved workspace in ADS GUI.
    """
    return _model_operation(lib, "setup", cell=cell, view=view,
                            session_id=session_id, workspace=workspace,
                            substrate=substrate, start_ghz=start_ghz, stop_ghz=stop_ghz,
                            points=points, setup=setup, dataset=cell,
                            impedance_ohms=50.0)


@mcp.tool()
def ads_inspect_momentum_setup(workspace: str, setup_file: str, setup: str = "emSetup") -> dict[str, Any]:
    """Inspect a saved native Momentum setup, its layout, paths and linear sweep.

    setup_file is the canonical emStateFile.xml inside the EM Setup cellview,
    not the generated copy under simulation/. Requires an existing local
    library and saved layout, native calculation type 5 and one linear sweep
    (also verified locally on ADS 2025). Reports stale copied workspace references; no edits.
    """
    try:
        return _ok(**inspect_setup(workspace, setup_file, setup))
    except Exception as exc:
        return _err(exc)


@mcp.tool()
@_serialize_workspace
def ads_run_momentum(session_id: int, workspace: str, setup_file: str, setup: str = "emSetup") -> dict[str, Any]:
    """Run an existing native Momentum setup in the selected ADS GUI session.

    Open the requested workspace in that GUI first. Close any workspace
    owned by this server with ads_close_workspace. The tool verifies the GUI
    workspace/library/setup link, returns task_id, and never closes ADS or
    silently switches workspaces. Poll ads_get_sim_status for fresh outputs
    and configured frequency-grid validation. Saved setups can be created by
    ads_configure_momentum_setup. A reservation prevents duplicate dispatches.
    """
    try:
        _ensure_no_running_tasks()
        if engine.workspace_path is not None:
            raise StateError("close this server's workspace with ads_close_workspace before running Momentum in ADS GUI")
        return _ok(**momentum.start(state, session_id, workspace, setup_file, setup))
    except Exception as exc:
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
    tasks = list(state.snapshot()["tasks"].values())
    for st in sorted(reversed(tasks), key=lambda t: t.get("finished_at") or "", reverse=True):
        if st["status"] == "done" and st.get("result", {}).get("dataset"):
            return st["result"]["dataset"]
    return ""


@mcp.tool()
def ads_export_touchstone(dataset: str = "", out_path: str = "") -> dict[str, Any]:
    """Export the full 1/N-port matrix to a matching .sNp file (50-ohm reference)."""
    try:
        ds_path = dataset.strip() or _last_dataset_from_tasks()
        if not ds_path:
            raise RuntimeError("no dataset available; run a simulation first")
        if out_path.strip():
            out = out_path.strip()
        else:
            from sparameter_results import matrix_from_frame
            with engine._op():
                _, frame = engine._read_dataframe(ds_path)
                _, matrix = matrix_from_frame(frame)
            out = str(Path(ds_path).with_suffix(f".s{matrix.shape[1]}p"))
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
def ads_autolabel(max_pass: int = 4, step: float = 0.75) -> dict[str, Any]:
    """Auto-place instance labels to clear text-level collisions (KiCad-style).

    For each text collision, tries four candidate sides for the later
    instance's annotation and moves it to the first collision-free spot.
    Body-body collisions are reported only — fix those in the layout.
    """
    try:
        return _ok(**engine.autolabel(max_pass=int(max_pass), step=float(step)))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def ads_audit_schematic(lib: str = "", cell: str = "") -> dict[str, Any]:
    """Audit a schematic for overlapping elements (KiCad-style).

    Compares every pair of instance bounding boxes (body vs annotation text)
    and reports collisions by severity: body-body / body-text / text-text.
    With lib+cell, audits the SAVED design read-only; without, audits the
    current in-memory design. Aim for zero body-level collisions before
    delivering a schematic.
    """
    try:
        return _ok(**engine.audit_schematic(lib.strip() or None, cell.strip() or None))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
@_serialize_workspace
def ads_open_in_gui(workspace: str = "") -> dict[str, Any]:
    """Open the current or explicitly specified saved workspace in ADS GUI.

    The GUI process is detached from this server, so it survives MCP restarts.
    For EM handoff, close the server workspace first, then pass its saved path
    explicitly; the GUI loads the newly created substrate materials on open.
    """
    try:
        _ensure_no_running_tasks()
        ws = workspace.strip() or state.get("workspace_path")
        if not ws:
            raise RuntimeError("no workspace open in this session")
        target = Path(ws).expanduser().resolve(strict=True)
        if not (target / "lib.defs").is_file():
            raise ValueError("workspace must contain lib.defs")
        result = engine.open_in_gui(str(target))
        state.set_field("gui_pid", result["gui_pid"])
        return _ok(**result)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


from native_tools import register as _register_native_tools
_register_native_tools(mcp, engine, state)

from workflow_tools import register as _register_workflow_tools
_register_workflow_tools(mcp, engine, state, _workspace_dispatch_lock)

from schematic_flow import register as _register_schematic_flow
_register_schematic_flow(mcp, engine, state, _workspace_dispatch_lock)

from design_workflow import register as _register_design_workflow
_register_design_workflow(mcp, engine, state, _workspace_dispatch_lock)

if __name__ == "__main__":
    ensure_env()
    # import the native de engine on the MAIN thread: _pde/Qt must not be
    # first-initialized from a worker thread (silent native abort)
    from ads_engine import get_de

    get_de()
    # ADS 2025's bundled SciPy BLAS extension can block on first import once
    # the stdio transport has reader threads. Load it on the main thread
    # before starting MCP, as with the native DE engine above.
    import scipy.spatial

    mcp.run()
    # the de engine leaves Qt threads behind; without a hard exit the process
    # would hang after stdin EOF and block every MCP client's shutdown
    import os

    os._exit(0)
