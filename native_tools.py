"""Register the v0.4 native workflow tools without an arbitrary-code endpoint."""
from typing import Literal
from pathlib import Path
from ads_gui import call_native
from design_operations import CellParameter, design_operation
from em_model import MeshSettings, FrequencyPlan, PortSettings, em_operation
from gui_windows import window_operation
from em_circuit import EMVariable, EMSGoal


def register(mcp, engine, state):
    def invoke(fn, data, session_id=None, workspace="", readonly=False):
        if not readonly and any(t["status"] == "running" for t in state.snapshot()["tasks"].values()):
            raise RuntimeError("Wait for the active simulation before editing this session")
        ws = workspace or engine.workspace_path or ""
        if session_id is None:
            with engine._op():
                result = call_native(fn, data, workspace=ws)
        else:
            if engine.workspace_path is not None:
                raise RuntimeError("Close the MCP-owned workspace before using the GUI path")
            if readonly:
                result = call_native(fn, data, session_id, ws)
            else:
                from workspace_activity import edit_guard
                if not ws:
                    from gui_bridge_2025 import discover
                    matches=[s for s in discover() if s['session_id']==session_id]
                    if len(matches)!=1:raise RuntimeError('Select an identified owned session and workspace')
                    ws=matches[0]['workspace']
                with edit_guard(ws):
                    result = call_native(fn, data, session_id, ws)
        return {"ok": True, **result}

    @mcp.tool()
    def ads_open_design(lib: str, cell: str, view: str = "schematic", session_id: int | None = None,
                        workspace: str = "", show: bool = False) -> dict:
        """Inspect an existing local design; optionally show it in its ADS GUI session."""
        if show and session_id is None:
            raise ValueError("Showing a window requires an ADS GUI session_id")
        if session_id is None and view == "schematic":
            # Keep inspection, selection and state in the same engine lock.
            # A successful inspection must select the same target for edits.
            with engine._op():
                result = invoke(design_operation, dict(operation="open", lib=lib, cell=cell, view=view, show=show), session_id, workspace, readonly=True)
                from keysight.ads.de import db_uu as db
                engine._design = db.open_design(f"{lib}:{cell}:{view}", db.DesignMode.APPEND)
                state.set_field("current_schematic", f"{lib}:{cell}:{view}")
                return result
        return invoke(design_operation, dict(operation="open", lib=lib, cell=cell, view=view, show=show), session_id, workspace, readonly=True)

    @mcp.tool()
    def ads_add_design_port(lib: str, cell: str, name: str, number: int, instance: str,
                            pin_number: int, angle: float = 0, view: str = "schematic",
                            session_id: int | None = None, workspace: str = "") -> dict:
        """Add a Subnetwork terminal on a specified instance pin and its actual net (not a simulation TermG)."""
        from momentum import _name
        import math
        _name(name)
        if number < 1 or pin_number < 1 or not math.isfinite(angle):
            raise ValueError("Positive terminal/pin numbers and a finite angle are required")
        return invoke(design_operation, dict(operation="design_port",lib=lib,cell=cell,view=view,name=name,number=number,instance=instance,pin_number=pin_number,angle=angle),session_id,workspace)

    @mcp.tool()
    def ads_set_instance_parameters(lib: str, cell: str, instance: str, parameters: dict[str, str],
                                    simulation_view: str = "", view: str = "schematic",
                                    session_id: int | None = None, workspace: str = "") -> dict:
        """Edit existing parameters and explicitly select an existing DUT simulation view."""
        return invoke(design_operation, dict(operation="edit_instance", lib=lib, cell=cell, view=view, instance=instance, parameters=parameters, simulation_view=simulation_view), session_id, workspace)

    @mcp.tool()
    def ads_define_cell_parameters(lib: str, cell: str, parameters: list[CellParameter], replace: bool = False,
                                   session_id: int | None = None, workspace: str = "") -> dict:
        """Persist REAL, NO_UNIT Subnetwork parameters in itemdef.py. Empty list defines a fixed subnetwork. Use W*1 mm in artwork."""
        if len({p.name for p in parameters}) != len(parameters):
            raise ValueError("Use distinct parameter names")
        return invoke(design_operation, dict(operation="formal_parameters", lib=lib, cell=cell, parameters=[p.model_dump() for p in parameters], replace=replace), session_id, workspace)

    @mcp.tool()
    def ads_generate_layout(lib: str, cell: str, source_view: str = "schematic", target_view: str = "layout",
                            parameterized: bool = False, update: bool = False,
                            session_id: int | None = None, workspace: str = "") -> dict:
        """Generate native device artwork and optional PSN. ADS 2025 requires an owned GUI session_id; reports omitted artwork for review."""
        return invoke(design_operation, dict(operation="layout", lib=lib, cell=cell, view=source_view, target_view=target_view, parameterized=parameterized, update=update), session_id, workspace)

    @mcp.tool()
    def ads_generate_symbol(lib: str, cell: str, source_view: str = "schematic", target_view: str = "symbol",
                            style: Literal["dual", "quad", "lookalike"] = "dual", replace: bool = False,
                            lead_length: float = .25, lead_spacing: float = .25, lookalike_size: float = 16,
                            session_id: int | None = None, workspace: str = "") -> dict:
        """Generate a rectangular Dual/Quad or native Layout-lookalike symbol. Does not generate EM data."""
        import math
        if not all(math.isfinite(v) and v > 0 for v in (lead_length, lead_spacing, lookalike_size)):
            raise ValueError("Symbol dimensions must be finite and positive")
        return invoke(design_operation, dict(operation="symbol", lib=lib, cell=cell, view=source_view, target_view=target_view, style=style, replace=replace, lead_length=lead_length, lead_spacing=lead_spacing, lookalike_size=lookalike_size), session_id, workspace)

    @mcp.tool()
    def ads_import_library(lib: str, definition_file: str, session_id: int | None = None, workspace: str = "") -> dict:
        """Register a local vendor lib.defs. Reports reload required; does not download or modify vendor models."""
        if Path(definition_file).name != "lib.defs":
            raise ValueError("Select the vendor lib.defs file")
        return invoke(design_operation, dict(operation="import_library", lib=lib, cell="", definition_file=definition_file), session_id, workspace)

    @mcp.tool()
    def ads_create_em_testbench(lib: str, cell: str, testbench: str,
                                start_ghz: float, stop_ghz: float, points: int,
                                parameters: dict[str, str] | None = None,
                                model: str = "emModel", symbol: str = "symbol",
                                series_resistance_ohms: float | None = None,
                                shunt_capacitance_pf: float | None = None,
                                session_id: int | None = None, workspace: str = "") -> dict:
        """Create a NEW native two-port emModel SP bench with explicit frequency grid and 50-ohm ports; optional series R/shunt C. Saves netlist, does not simulate."""
        import math
        from momentum import _name
        from em_circuit import build_em_testbench
        for name in (lib,cell,testbench,model,symbol):_name(name)
        if not all(math.isfinite(v) for v in (start_ghz,stop_ghz)) or not 0 < start_ghz < stop_ghz:
            raise ValueError('Use positive increasing frequencies')
        if type(points) is not int or not 2 <= points <= 100000:
            raise ValueError('Use 2..100000 frequency points')
        if any(v is not None and (not math.isfinite(v) or v <= 0) for v in (series_resistance_ohms,shunt_capacitance_pf)):
            raise ValueError('Optional R/C values must be finite and positive')
        return invoke(build_em_testbench,dict(lib=lib,cell=cell,testbench=testbench,
            start_ghz=start_ghz,stop_ghz=stop_ghz,points=points,parameters=parameters or {},model=model,symbol=symbol,
            series_resistance_ohms=series_resistance_ohms,shunt_capacitance_pf=shunt_capacitance_pf),session_id,workspace)

    @mcp.tool()
    def ads_create_em_optimization(lib: str, cell: str, testbench: str,
                                   start_ghz: float, stop_ghz: float, points: int,
                                   variables: list[EMVariable], goals: list[EMSGoal],
                                   max_iterations: int,
                                   parameters: dict[str, str] | None = None,
                                   model: str = 'emModel', symbol: str = 'symbol',
                                   series_resistance_ohms: float | None = None,
                                   shunt_capacitance_pf: float | None = None,
                                   session_id: int | None = None, workspace: str = '') -> dict:
        """Create a NEW native two-port SP/EM Gradient optimization bench with explicit bounds/goals and MaxIters. Does not launch. Iteration budget is NOT a hard EM-solve-count cap."""
        import math
        from momentum import _name
        from em_circuit import build_em_optimization
        for name in (lib,cell,testbench,model,symbol):_name(name)
        if not all(math.isfinite(v) for v in (start_ghz,stop_ghz)) or not 0 < start_ghz < stop_ghz:
            raise ValueError('Use positive increasing frequencies')
        if type(points) is not int or not 2 <= points <= 100000:
            raise ValueError('Use 2..100000 frequency points')
        if type(max_iterations) is not int or not 1 <= max_iterations <= 100:
            raise ValueError('Use an explicit native iteration budget from 1 to 100')
        if not 1 <= len(variables) <= 6 or len({v.parameter for v in variables}) != len(variables):
            raise ValueError('Use 1..6 distinct native model parameters')
        if set(parameters or {}) & {v.parameter for v in variables}:
            raise ValueError('A model parameter cannot be both fixed and optimized')
        if not 1 <= len(goals) <= 8:
            raise ValueError('Use 1..8 explicit S-parameter goals')
        if any(v is not None and (not math.isfinite(v) or v <= 0) for v in (series_resistance_ohms,shunt_capacitance_pf)):
            raise ValueError('Optional R/C values must be finite and positive')
        return invoke(build_em_optimization,dict(lib=lib,cell=cell,testbench=testbench,
            start_ghz=start_ghz,stop_ghz=stop_ghz,points=points,parameters=parameters or {},
            variables=[v.model_dump() for v in variables],goals=[g.model_dump() for g in goals],
            max_iterations=max_iterations,model=model,symbol=symbol,
            series_resistance_ohms=series_resistance_ohms,shunt_capacitance_pf=shunt_capacitance_pf),session_id,workspace)

    @mcp.tool()
    def ads_create_em_candidate(lib: str, cell: str, candidate: str,
                                parameters: dict[str,float], symbol: str = 'symbol',
                                session_id: int | None = None, workspace: str = '') -> dict:
        """Create a NEW native two-port candidate subnetwork with ALL numeric source parameters explicitly fixed. References the source schematic/layout; never copies/reuses source EM data. Layout and simulation are separate steps."""
        import math
        from momentum import _name
        from em_circuit import build_em_candidate
        for name in (lib,cell,candidate,symbol):_name(name)
        if not parameters or any(not math.isfinite(v) for v in parameters.values()):
            raise ValueError('Explicit finite native parameter values are required')
        return invoke(build_em_candidate,dict(lib=lib,cell=cell,candidate=candidate,
            parameters=parameters,symbol=symbol),session_id,workspace)

    @mcp.tool()
    def ads_configure_em_model(lib: str, cell: str, setup: str = "emSetup", model: str = "emModel",
                               symbol: str = "symbol", session_id: int | None = None, workspace: str = "") -> dict:
        """Configure native auto-create/update output. Reload workspace then run Momentum to create model data."""
        return invoke(em_operation, dict(operation="model", lib=lib, cell=cell, setup=setup, model=model, symbol=symbol), session_id, workspace)

    @mcp.tool()
    def ads_configure_em_model_storage(lib: str, cell: str, retain_full_solver_data: bool,
                                       setup: str = 'emSetup', model: str = 'emModel',
                                       session_id: int | None = None, workspace: str = '') -> dict:
        """Set native storage flags in a local EM Setup and existing emModel; full data preserves geometry/mesh for NEW samples. Backs up settings, never deletes samples. Reload if reported."""
        if setup==model:
            raise ValueError('EM Setup and model views must be distinct')
        return invoke(em_operation,dict(operation='model_storage',lib=lib,cell=cell,setup=setup,
            model=model,retain_full_solver_data=retain_full_solver_data),session_id,workspace)

    @mcp.tool()
    def ads_inspect_em_model(lib: str, cell: str, model: str = "emModel", session_id: int | None = None, workspace: str = "") -> dict:
        """Inspect native files, geometry parameter names, sample cache and interpolation policy."""
        return invoke(em_operation, dict(operation="inspect_model", lib=lib, cell=cell, model=model), session_id, workspace, readonly=True)

    @mcp.tool()
    def ads_configure_em_parameters(lib: str, cell: str, model: str = "emModel", interpolate_geometry: bool = False,
                                    resolutions: dict[str, float] | None = None,
                                    session_id: int | None = None, workspace: str = "") -> dict:
        """Set native model geometry cache interpolation/resolution; does not clear samples or simulate."""
        import math
        if resolutions and any(not math.isfinite(v) or v <= 0 for v in resolutions.values()):
            raise ValueError("Parameter resolutions must be finite and positive")
        return invoke(em_operation, dict(operation="model_parameters", lib=lib, cell=cell, model=model,
                      interpolate_geometry=interpolate_geometry, resolutions=resolutions or {}), session_id, workspace)

    @mcp.tool()
    def ads_release_optimizer(task_id: str) -> dict:
        """Release the simulator of a completed GUI Optim task, retaining its recorded best values without applying them."""
        from circuit_runner import CircuitRunner
        return {"ok": True, **CircuitRunner().release(state, task_id)}

    @mcp.tool()
    def ads_configure_momentum_mesh(lib: str, cell: str, mesh: MeshSettings, setup: str = "emSetup",
                                    session_id: int | None = None, workspace: str = "") -> dict:
        """Configure global cells/wavelength, edge mesh and reference frequency. Requires reload and a new solve."""
        return invoke(em_operation, dict(operation="mesh", lib=lib, cell=cell, setup=setup, mesh=mesh.model_dump()), session_id, workspace)

    @mcp.tool()
    def ads_configure_momentum_options(lib: str, cell: str, plans: list[FrequencyPlan] | None = None,
                                       ports: list[PortSettings] | None = None, setup: str = "emSetup",
                                       session_id: int | None = None, workspace: str = "") -> dict:
        """Extend an existing Setup with direct linear RF/DC plans and Auto/TML/uncalibrated internal ports."""
        if not plans and not ports:
            raise ValueError("Specify frequency plans or port settings")
        if plans and (len(plans) > 2 or sum(p.kind == "dc" for p in plans) > 1 or sum(p.kind == "linear" for p in plans) != 1):
            raise ValueError("Use one RF linear plan and optionally one DC plan")
        if ports and len({p.number for p in ports}) != len(ports):
            raise ValueError("Duplicate port numbers")
        return invoke(em_operation, dict(operation="setup_options", lib=lib, cell=cell, setup=setup, plans=[p.model_dump() for p in plans] if plans else None, ports=[p.model_dump() for p in ports] if ports else []), session_id, workspace)

    @mcp.tool()
    def ads_list_windows(session_id: int) -> dict:
        """List ADS process windows, design identities, screen bounds and Qt logical-pixel geometry."""
        return invoke(window_operation, dict(operation="list"), session_id, readonly=True)

    @mcp.tool()
    def ads_arrange_windows(session_id: int, targets: list[str], mode: Literal["tile", "maximize_each", "restore"] = "tile",
                            columns: int | None = None, rectangles: list[list[int]] | None = None,
                            screen: str = "", front: str = "") -> dict:
        """Tile via setGeometry, maximize each selected window, or restore tool-saved states; never closes windows."""
        if not targets or (columns is not None and columns < 1):
            raise ValueError("Select windows and a positive column count")
        if rectangles and (len(rectangles) != len(targets) or any(len(r) != 4 or r[2] <= 0 or r[3] <= 0 for r in rectangles)):
            raise ValueError("One [x,y,width,height] rectangle per window is required")
        return invoke(window_operation, dict(operation="arrange", targets=targets, mode=mode, columns=columns, rectangles=rectangles, screen=screen, front=front), session_id, readonly=True)

    @mcp.tool()
    def ads_set_mesh_visibility(session_id: int, target: str, visible: bool = True) -> dict:
        """Load/clear native Momentum mesh display objects in one Layout window; does not save the design."""
        return invoke(window_operation, dict(operation="mesh", targets=[target], visible=visible), session_id, readonly=True)

    @mcp.tool()
    def ads_compare_sparameters(left_dataset: str, right_dataset: str, tolerance: float = 1e-10) -> dict:
        """Compare all complex N-port S terms on equal frequency grids without interpolation."""
        import math
        from sparameter_results import compare_frames
        if tolerance < 0 or not math.isfinite(tolerance):
            raise ValueError("Tolerance must be finite and nonnegative")
        with engine._op():
            _, left = engine._read_dataframe(left_dataset)
            _, right = engine._read_dataframe(right_dataset)
            return {"ok": True, **compare_frames(left, right, tolerance)}
