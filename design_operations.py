"""Native schematic, PSN layout, subnetwork and symbol operations."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CellParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    default: str


def design_operation(a):
    import json
    from pathlib import Path
    from keysight.ads import de
    from keysight.ads.de import db_uu as db
    if not de.workspace_is_open():
        raise RuntimeError("No workspace open")
    ws = Path(de.active_workspace().path).resolve()
    lib = (getattr(de.Library, "get", None) or de.get_open_library)(a["lib"])
    if not Path(lib.path).resolve().is_relative_to(ws):
        raise RuntimeError("Editing requires a local library in this workspace")
    op = a["operation"]
    name = a["lib"] + ":" + a["cell"] + ":" + a.get("view", "schematic")
    def native_window(context):
        try:
            return de.ael.de_bring_context_to_top_or_open_new_window(context)
        except RuntimeError as exc:
            # ADS 2025 can retain a window registration across workspace
            # reload although that window no longer has a design attached.
            if "specified window does not contain a design" not in str(exc):
                raise
            return de.ael.de_show_context_in_new_window(context)
    if op == "formal_parameters":
        cell = lib.cell(a["cell"])
        # ADS 2025 exposes the native OA path resolver, not Cell.path.
        folder = getattr(cell, "path", None)
        if folder is None:
            folder = de._pde.get_path_to_cell(a["lib"], a["cell"])
        folder = Path(folder).resolve()
        if not folder.is_relative_to(ws):
            raise RuntimeError("Cell output escapes the workspace")
        path = folder / "itemdef.py"
        if path.exists() and not a.get("replace", False):
            raise RuntimeError("itemdef.py already exists; explicitly request replace")
        params = [(p["name"], p["default"]) for p in a["parameters"]]
        code = ('from keysight.ads import de\n'
                'def create_itemdef(cell):\n'
                '    fs = de.db.model_lib.formsets["StdFormSet"]\n'
                '    params = []\n'
                f'    for name, value in {params!r}:\n'
                '        p = de.db.ModelParam(name, name, fs, de.db.ModelUnitType.NO_UNIT, de.db.ModelParamType.REAL)\n'
                '        p.default_value = de.db.std_string_param(value)\n'
                '        params.append(p)\n'
                '    model = de.db.ModelDef(cell.name, "Parameterized subnetwork")\n'
                '    model.is_sub_design = True\n'
                '    model.inst_name_prefix = "X"\n'
                '    model.parameters = params\n'
                '    de.add_model_definition(cell.library, model)\n')
        ns = {}
        exec(compile(code, "itemdef.py", "exec"), ns)
        ns["create_itemdef"](cell)
        path.write_text(code, encoding="utf-8")
        # Persist discovery of itemdef.py after the workspace/server is reopened.
        config = Path(lib.path) / "eesof_lib.cfg"
        previous = config.read_text(encoding="utf-8") if config.exists() else ""
        import re
        if re.search(r"(?mi)^\s*PYTHON_ENABLED\s*=", previous):
            enabled = re.sub(r"(?mi)^\s*PYTHON_ENABLED\s*=.*$", "PYTHON_ENABLED=TRUE", previous)
        else:
            enabled = previous.rstrip("\r\n") + "\nPYTHON_ENABLED=TRUE\n"
        if enabled != previous:
            import hashlib
            history = ws / ".ads-mcp" / "configuration_history"
            history.mkdir(parents=True, exist_ok=True)
            backup = history / ("libcfg_" + hashlib.sha256((str(config) + previous).encode()).hexdigest()[:16] + ".txt")
            if not backup.exists():
                backup.write_text(previous, encoding="utf-8")
            config.write_text(enabled, encoding="utf-8")
        return {"item_definition": str(path), "parameters": a["parameters"],
                "is_sub_design": True, "parameter_type": "REAL", "unit_type": "NO_UNIT"}
    if op == "import_library":
        path = Path(a["definition_file"]).resolve(strict=True)
        defs = ws / "lib.defs"
        before = defs.read_text(encoding="utf-8")
        if path == defs.resolve():
            raise ValueError("Cannot include the workspace's own lib.defs")
        if "\n" in str(path) or "\r" in str(path):
            raise ValueError("Library definition path contains a newline")
        native_add = getattr(de.active_workspace(), "add_library_definition_file", None)
        if native_add is not None:
            native_add(str(path))
        else:
            # The ADS 2025 native library mapping supports INCLUDE directly.
            # Keep vendor files untouched and defer loading until workspace reopen.
            import os
            includes = []
            for line in before.splitlines():
                fields = line.strip().split(None, 1)
                if len(fields) == 2 and fields[0] == "INCLUDE":
                    candidate = Path(os.path.expandvars(fields[1]))
                    includes.append((candidate if candidate.is_absolute() else ws / candidate).resolve())
            if path not in includes:
                after = before.rstrip("\r\n") + "\nINCLUDE " + path.as_posix() + "\n"
                temporary = defs.with_suffix(".defs.tmp")
                temporary.write_text(after, encoding="utf-8")
                os.replace(temporary, defs)
        after = defs.read_text(encoding="utf-8")
        # ADS may shorten an absolute path to $HOME using its preferences even
        # when HOME is not defined for the standalone process. Keep just the
        # newly added INCLUDE explicit; never rewrite existing library entries.
        new_lines = [line for line in after.splitlines() if line not in before.splitlines() and line.strip().startswith("INCLUDE ")]
        if len(new_lines) == 1:
            import hashlib
            backup = ws / ".ads-mcp" / "configuration_history" / ("libdefs_" + hashlib.sha256(before.encode()).hexdigest()[:16] + ".txt")
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():backup.write_text(before, encoding="utf-8")
            # ADS lib.defs consumes the remaining line as the path; quotes
            # become literal filename characters in this native parser.
            explicit = 'INCLUDE ' + path.as_posix()
            defs.write_text(after.replace(new_lines[0], explicit), encoding="utf-8")
        return {"definition_file": str(path), "requires_workspace_reload": True, "explicit_include": len(new_lines) == 1}
    mode = db.DesignMode.READ_ONLY if op in ("open", "inspect") else db.DesignMode.APPEND
    source = db.open_design(name, mode)
    if op == "design_port":
        if any(t.name == a["name"] or t.number == a["number"] for t in source.terms):
            raise ValueError("Terminal name or number already exists")
        inst = source.find_instance(a["instance"])
        if inst is None:
            raise ValueError("Instance not found")
        finder = getattr(inst, "first_inst_pin_numbered", None)
        if finder is not None:
            pin = finder(a["pin_number"])
        else:
            pin = next((p for p in inst.inst_pins if p.inst_term.is_numbered
                        and (p.inst_term.term_number if hasattr(p.inst_term, "term_number") else p.inst_term.get_inst_term_number()) == a["pin_number"]), None)
        if pin is None or not pin.is_valid or pin.snap_point is None:
            raise ValueError("Instance has no valid pin numbered " + str(a["pin_number"]))
        with de.db.Transaction(source, "Add connected Subnetwork terminal") as tx:
            net = pin.inst_term.net or source.find_or_add_net(a["name"])
            pin.inst_term.net = net
            term = source.add_term(net, a["name"])
            term.number = a["number"]
            lid = db.LayerId(231) if not source.is_layout else (
                pin.snap_layer_id if hasattr(pin, "snap_layer_id") else pin.get_snap_layer_id())
            source.add_pin(term, source.add_dot(lid, pin.snap_point), angle=a["angle"])
            tx.commit()
        source.save_design()
        return {"design": name, "terminal": term.name, "number": term.number,
                "net": term.net.name, "connected_to": [inst.name, a["pin_number"]]}
    if op in ("open", "inspect"):
        if a.get("show", False):
            try:
                from PySide6.QtWidgets import QApplication, QMainWindow
                from shiboken6 import isValid
            except ImportError:
                from PySide2.QtWidgets import QApplication, QMainWindow
                from shiboken2 import isValid
            app = QApplication.instance()
            matches = [w for w in app.topLevelWidgets() if isValid(w) and isinstance(w, QMainWindow) and "["+name+"]" in w.windowTitle()]
            if matches:
                matches[-1].show();matches[-1].raise_();matches[-1].activateWindow()
            else:
                de.ael.de_show_context_in_new_window(de.ael.db_find_design_context_from_name(name, 0))
        # ADS 2025 may fail formatting an inherited/default value (e.g. Noise)
        # although the schematic/netlist is valid. Preserve per-field errors;
        # one unreadable optional value must not hide the entire design.
        instances = []
        for inst in source.instances:
            values, errors, pins = {}, {}, []
            for param in inst.parameters:
                try:
                    values[param.name] = str(param.value)
                except Exception as exc:
                    errors[param.name] = str(exc)
            for pin in inst.inst_pins:
                term = pin.inst_term
                pins.append({"number": (term.term_number if hasattr(term, "term_number") else term.get_inst_term_number()) if term.is_numbered else None,
                             "point": [float(pin.snap_point.x), float(pin.snap_point.y)] if pin.snap_point is not None else None,
                             "net": term.net.name if term.net else None})
            row = {"name": inst.name, "library": inst.library_name, "cell": inst.cell_name,
                   "parameters": values, "parameter_read_errors": errors,
                   "origin": [float(inst.origin.x), float(inst.origin.y)], "pins": pins}
            if source.is_layout:
                b = inst.bbox
                row["bbox_uu"] = [b.lower_left.x, b.lower_left.y, b.upper_right.x, b.upper_right.y]
            if inst.is_var_instance:
                row["variables"] = dict(inst.vars)
            instances.append(row)
        return {"design": name, "units": source.unit_name, "instances": instances,
                "terminals": [{"name": t.name, "number": t.number} for t in source.terms]}
    if op == "edit_instance":
        inst = source.find_instance(a["instance"])
        if inst is None:
            raise ValueError("Instance not found: " + a["instance"])
        # Validate all parameter names before starting a transaction.
        known = {p.name for p in inst.parameters}
        missing = set(a["parameters"]) - known
        if missing:
            raise ValueError("Unknown parameters: " + str(sorted(missing)))
        legacy_layout = source.is_layout and not hasattr(de.db.ExpressionContext, "update_pcell_params")
        if legacy_layout and de.running_automation():
            raise NotImplementedError("ADS 2025 layout parameter updates require an owned GUI session to regenerate native geometry; no parameter was changed")
        with de.db.Transaction(source, "Update instance parameters") as tx:
            for key, value in a["parameters"].items():
                inst.parameters[key].value = value
            if a.get("simulation_view"):
                target = (getattr(de.Library, "get", None) or de.get_open_library)(inst.library_name).cell(inst.cell_name)
                if not target.view_exists(a["simulation_view"]):
                    raise ValueError("Simulation view does not exist")
                inst.specialized_subview = a["simulation_view"]
            tx.commit()
        if source.is_layout:
            context = de.db.ExpressionContext()
            if hasattr(context, "__enter__"):
                with context:
                    context.setup_hierarchy_for_layout_only(source)
                    context.update_pcell_params(inst)
            else:
                # The 2025 Python PCell updater changes OA parameter values but
                # can leave PSN geometry stale. The native item editor also
                # refreshes expression evaluation. Preserve raw parameter
                # objects (including forms, repeats and compound values).
                ctx = de.ael.db_find_design_context_from_name(name, 0)
                native_window(ctx)
                handle = de.ael.db_find_instance_ex(ctx, inst.name)
                parameters = []
                parameter = de.ael.db_first_parm(handle)
                while parameter:
                    parameters.append(parameter)
                    parameter = de.ael.db_next_parm(parameter)
                item = de.ael.de_edit_item_ex(ctx, inst.name)
                de.ael.de_set_item_parameters(item, parameters)
                de.ael.de_end_edit_item(item)
        source.save_design()
        if legacy_layout:
            de.ael.db_save_design_without_prompting(de.ael.db_get_design_context_from_name(name,2))
        return {"design": name, "instance": inst.name, "parameters": a["parameters"],
                "simulation_view": str(inst.specialized_subview)}
    if op == "layout":
        if not source.is_schematic:
            raise ValueError("Native layout generation requires a schematic source")
        modern = hasattr(source, "edit")
        if not modern and de.running_automation():
            raise NotImplementedError(
                "ADS 2025 native artwork synchronization requires an owned GUI session_id. "
                "Its headless engine has no Design.edit.update_design_from. "
                "No target layout was created or updated."
            )
        target_view = a.get("target_view", "layout")
        terminals = sorted(source.terms, key=lambda t: t.number)
        if not modern and not terminals:
            raise ValueError("Native synchronization currently requires a numbered source terminal")
        cell = lib.cell(a["cell"])
        exists = cell.view_exists(target_view)
        if exists and not a.get("update", False):
            raise ValueError("Target layout exists; explicitly request update")
        if not lib.has_tech:
            lib.create_layout_tech_std_ads("millimeter", 1000000, True)
        source.save_design()
        layout_name = a["lib"] + ":" + a["cell"] + ":" + target_view
        lay = db.open_design(layout_name, db.DesignMode.APPEND) if exists else db.create_layout(layout_name)
        changed = []
        pcell_type = None
        if modern:
            for d in lay.edit.update_design_from(source):
                d.save_design()
                changed.append(str(d))
            if a.get("parameterized", False):
                db.PCellInfo(db.PCellType.PSN).make_pcell(lay)
        else:
            # ADS 2025 documented native synchronization uses each device's
            # artwork. No geometric approximation or manual redraw is used.
            lay_context = de.ael.db_find_design_context_from_name(layout_name, 0)
            source_context = de.ael.db_find_design_context_from_name(name, 0)
            lay_window = native_window(lay_context)
            native_window(source_context)
            de.ael.de_dse_s2l(terminals[0].name, 0., 0., 0., 0., 0., lay_window)
            if a.get("parameterized", False):
                de.ael.de_setup_psn_pcell(lay_context)
                pcell_type = int(de.ael.de_get_pcell_type(lay_context))
                if pcell_type != 2:
                    raise RuntimeError("ADS did not assign the requested native PSN type")
            changed.append(layout_name)
        lay.save_design()
        if not modern:
            de.ael.db_save_design_without_prompting(de.ael.db_get_design_context_from_name(layout_name,2))
        layout_instances = {i.name: i for i in lay.instances}
        omissions = []
        for inst in source.instances:
            if inst.name not in layout_instances:
                omissions.append({"name": inst.name, "library": inst.library_name,
                                  "cell": inst.cell_name,
                                  "has_pins": bool(list(inst.inst_pins)),
                                  "reason": "Native synchronization emitted no artwork instance; substrate/controllers/variables are non-geometric, other devices require an artwork review"})
        source_ports = sorted((t.number, t.name) for t in source.terms)
        layout_ports = sorted((t.number, t.name) for t in lay.terms)
        if source_ports != layout_ports:
            raise RuntimeError("Native layout terminal mismatch after generation: " + str({"source": source_ports, "layout": layout_ports}))
        return {"layout": layout_name, "layout_units": lay.unit_name,
                "parameterized": a.get("parameterized", False), "saved_designs": changed,
                "native_method": "Design.edit.update_design_from" if modern else "de_dse_s2l",
                "pcell_type": pcell_type, "omitted_instances": omissions,
                "artwork_review_required": any(i["has_pins"] for i in omissions),
                "instance_count": len(layout_instances),
                "terminals": [{"name": t.name, "number": t.number} for t in lay.terms]}
    if op == "symbol":
        target_view = a.get("target_view", "symbol")
        if lib.cell(a["cell"]).view_exists(target_view) and not a.get("replace", False):
            raise ValueError("Symbol exists; explicitly request replace")
        symbol_name = a["lib"] + ":" + a["cell"] + ":" + target_view
        if a["style"] == "lookalike":
            if not source.is_layout:
                raise ValueError("Lookalike symbol needs a Layout source")
            ctx = de.ael.db_find_design_context_from_name(name, 0)
            de.ael.momSymbol_createLayoutLookAlikeSymbol(ctx, target_view, 0, a["lookalike_size"], False, True, True)
            db.open_design(symbol_name,db.DesignMode.APPEND).save_design()
            de.ael.db_save_design_without_prompting(de.ael.db_get_design_context_from_name(symbol_name,2))
        else:
            from keysight.ads.de.experimental.generate_symbol import SymbolGenerator
            sym = db.open_design(symbol_name, db.DesignMode.APPEND) if lib.cell(a["cell"]).view_exists(target_view) else db.create_symbol(symbol_name)
            generator = SymbolGenerator(sym, source, a["lead_length"], a["lead_spacing"])
            generator.is_dual_symbol_type = a["style"] == "dual"
            generator.should_replace = a.get("replace", False)
            generator.pin_shape = "square"
            generator.generate_symbol()
            sym.save_design()
        return {"symbol": symbol_name, "style": a["style"]}
    raise ValueError("Unknown native design operation")
