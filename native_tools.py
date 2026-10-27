"""Register the v0.4 native workflow tools without an arbitrary-code endpoint."""
from typing import Literal
from pathlib import Path
from ads_gui import call_native
from design_operations import CellParameter, design_operation
from em_model import MeshSettings, FrequencyPlan, PortSettings, em_operation
from gui_windows import window_operation


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
            result = call_native(fn, data, session_id, ws)
        return {"ok": True, **result}

    @mcp.tool()
    def ads_open_design(lib: str, cell: str, view: str = "schematic", session_id: int | None = None,
                        workspace: str = "", show: bool = False) -> dict:
        """Inspect an existing local design; optionally show it in its ADS GUI session."""
        if show and session_id is None:
            raise ValueError("Showing a window requires an ADS GUI session_id")
        result = invoke(design_operation, dict(operation="open", lib=lib, cell=cell, view=view, show=show), session_id, workspace, readonly=True)
        if session_id is None and view == "schematic":
            from keysight.ads.de import db_uu as db
            engine._design = db.open_design(f"{lib}:{cell}:{view}", db.DesignMode.APPEND)
        return result

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
        """Persist REAL, NO_UNIT Subnetwork formal parameters in itemdef.py. Use W*1 mm in artwork."""
        if not parameters or len({p.name for p in parameters}) != len(parameters):
            raise ValueError("Use distinct nonempty parameters")
        return invoke(design_operation, dict(operation="formal_parameters", lib=lib, cell=cell, parameters=[p.model_dump() for p in parameters], replace=replace), session_id, workspace)

    @mcp.tool()
    def ads_generate_layout(lib: str, cell: str, source_view: str = "schematic", target_view: str = "layout",
                            parameterized: bool = False, update: bool = False,
                            session_id: int | None = None, workspace: str = "") -> dict:
        """Generate native artwork from a saved schematic via update_design_from; optionally make a PSN PCell."""
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
    def ads_configure_em_model(lib: str, cell: str, setup: str = "emSetup", model: str = "emModel",
                               symbol: str = "symbol", session_id: int | None = None, workspace: str = "") -> dict:
        """Configure native auto-create/update output. Reload workspace then run Momentum to create model data."""
        return invoke(em_operation, dict(operation="model", lib=lib, cell=cell, setup=setup, model=model, symbol=symbol), session_id, workspace)

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
