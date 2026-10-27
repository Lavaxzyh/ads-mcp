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
    lib = de.Library.get(a["lib"])
    if not Path(lib.path).resolve().is_relative_to(ws):
        raise RuntimeError("Editing requires a local library in this workspace")
    op = a["operation"]
    name = a["lib"] + ":" + a["cell"] + ":" + a.get("view", "schematic")
    if op == "formal_parameters":
        cell = lib.cell(a["cell"])
        path = Path(cell.path) / "itemdef.py"
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
        return {"item_definition": str(path), "parameters": a["parameters"],
                "is_sub_design": True, "parameter_type": "REAL", "unit_type": "NO_UNIT"}
    if op == "import_library":
        path = Path(a["definition_file"]).resolve(strict=True)
        defs = ws / "lib.defs"
        before = defs.read_text(encoding="utf-8")
        de.active_workspace().add_library_definition_file(str(path))
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
        pin = inst.first_inst_pin_numbered(a["pin_number"])
        with de.db.Transaction(source, "Add connected Subnetwork terminal") as tx:
            net = pin.inst_term.net or source.find_or_add_net(a["name"])
            pin.inst_term.net = net
            term = source.add_term(net, a["name"])
            term.number = a["number"]
            lid = db.LayerId(231) if not source.is_layout else pin.snap_layer_id
            source.add_pin(term, source.add_dot(lid, pin.snap_point), angle=a["angle"])
            tx.commit()
        source.save_design()
        return {"design": name, "terminal": term.name, "number": term.number,
                "net": term.net.name, "connected_to": [inst.name, a["pin_number"]]}
    if op in ("open", "inspect"):
        if a.get("show", False):
            from PySide6.QtWidgets import QApplication, QMainWindow
            from shiboken6 import isValid
            app = QApplication.instance()
            matches = [w for w in app.topLevelWidgets() if isValid(w) and isinstance(w, QMainWindow) and "["+name+"]" in w.windowTitle()]
            if matches:
                matches[-1].show();matches[-1].raise_();matches[-1].activateWindow()
            else:
                de.ael.de_show_context_in_new_window(de.ael.db_find_design_context_from_name(name, 0))
        return {"design": name, "instances": [{"name": i.name, "cell": i.cell_name,
                "parameters": {p.name: str(p.value) for p in i.parameters}} for i in source.instances],
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
        with de.db.Transaction(source, "Update instance parameters") as tx:
            for key, value in a["parameters"].items():
                inst.parameters[key].value = value
            if a.get("simulation_view"):
                target = de.Library.get(inst.library_name).cell(inst.cell_name)
                if not target.view_exists(a["simulation_view"]):
                    raise ValueError("Simulation view does not exist")
                inst.specialized_subview = a["simulation_view"]
            tx.commit()
        if source.is_layout:
            with de.db.ExpressionContext() as context:
                context.setup_hierarchy_for_layout_only(source)
                context.update_pcell_params(inst)
        source.save_design()
        return {"design": name, "instance": inst.name, "parameters": a["parameters"],
                "simulation_view": str(inst.specialized_subview)}
    if op == "layout":
        target_view = a.get("target_view", "layout")
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
        for d in lay.edit.update_design_from(source):
            d.save_design()
            changed.append(str(d))
        if a.get("parameterized", False):
            db.PCellInfo(db.PCellType.PSN).make_pcell(lay)
        lay.save_design()
        return {"layout": layout_name, "layout_units": lay.unit_name,
                "parameterized": a.get("parameterized", False), "saved_designs": changed,
                "instance_count": len(list(lay.instances)),
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
