"""Parameterized planar EM construction in the MCP-owned ADS workspace.

Geometry is supplied as data. Native EM defaults are read from the user's ADS
installation at runtime; this module does not contain a vendor XML template.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from momentum import _name, inspect_setup

Name = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128)]
Positive = Annotated[FiniteFloat, Field(gt=0, strict=True)]
Nonnegative = Annotated[FiniteFloat, Field(ge=0, strict=True)]
Coordinate = Annotated[FiniteFloat, Field(ge=-1e6, le=1e6, strict=True)]


class ModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DielectricLayer(ModelInput):
    """A finite dielectric layer, listed from bottom to top."""
    height_mm: Positive
    er: Annotated[FiniteFloat, Field(ge=1, strict=True)]
    loss_tangent: Annotated[FiniteFloat, Field(ge=0, lt=1, strict=True)] = 0


class ConductorLayer(ModelInput):
    """Metal on interface 1..N above N bottom-to-top dielectric layers."""
    layer: Name = "cond"
    interface: Annotated[int, Field(strict=True, ge=1)] = 1
    thickness_um: Nonnegative = 0
    conductivity_s_per_m: Positive | None = None  # None = perfect conductor


class ViaLayer(ModelInput):
    """Conductor via between substrate interfaces; plating dimensions in microns."""
    layer: Name = "hole"
    lower_interface: Annotated[int, Field(strict=True, ge=0)] = 0
    upper_interface: Annotated[int, Field(strict=True, ge=1)] = 1
    conductor_index: Annotated[int, Field(strict=True, ge=0)] = 0
    plating_thickness_um: Positive | None = None


class LayoutShape(ModelInput):
    """Rectangle corner pair or simple polygon vertices; coordinates in mm."""
    kind: Literal["rectangle", "polygon"]
    points_mm: Annotated[list[tuple[Coordinate, Coordinate]], Field(min_length=2, max_length=512)]
    layer: Name = "cond"

    @model_validator(mode="after")
    def geometry(self):
        pts = self.points_mm
        if self.kind == "rectangle":
            if len(pts) != 2 or pts[0][0] >= pts[1][0] or pts[0][1] >= pts[1][1]:
                raise ValueError("rectangle needs lower-left and upper-right corners with positive area")
        else:
            if len(pts) < 3 or len(set(pts)) != len(pts):
                raise ValueError("polygon needs at least three distinct vertices; omit the closing duplicate")
            area = sum(a[0]*b[1] - a[1]*b[0] for a, b in zip(pts, pts[1:]+pts[:1]))
            if abs(area) < 1e-15:
                raise ValueError("polygon has zero area")
            # Non-adjacent edges must not intersect, including touching edges.
            def cross(a, b, c):
                return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
            def on(a, b, c):
                return cross(a, b, c) == 0 and min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])
            for i in range(len(pts)):
                a, b = pts[i], pts[(i+1) % len(pts)]
                for j in range(i+1, len(pts)):
                    if j == i+1 or (i == 0 and j == len(pts)-1):
                        continue
                    c, d = pts[j], pts[(j+1) % len(pts)]
                    if (cross(a,b,c)*cross(a,b,d) < 0 and cross(c,d,a)*cross(c,d,b) < 0) or on(a,b,c) or on(a,b,d) or on(c,d,a) or on(c,d,b):
                        raise ValueError("polygon edges intersect")
        return self


class LayoutPort(ModelInput):
    """A numbered point pin on a conductor, positioned in millimeters."""
    name: Name
    number: Annotated[int, Field(strict=True, ge=1, le=64)]
    x_mm: Coordinate
    y_mm: Coordinate
    angle: Annotated[FiniteFloat, Field(strict=True)] = 0
    layer: Name = "cond"
    net: Name = "rf"


def _model_in_ads(a):
    """Build and save before the workspace is handed to ADS GUI for solving."""
    import hashlib
    import json
    import os
    import uuid
    import xml.etree.ElementTree as ET
    from pathlib import Path
    import keysight.ads.de as de
    from keysight.ads.de import db_uu as db
    from keysight.ads.de.db import LayerId, Transaction
    from keysight.ads import subst

    ws = Path(a["workspace"]).resolve()
    if not de.workspace_is_open() or Path(de.active_workspace().path).resolve() != ws:
        raise RuntimeError("Open the requested workspace in this MCP server first")
    lib = de.Library.get(a["lib"])
    lib_dir = Path(de.get_path_to_open_library(a["lib"])).resolve()
    if not lib_dir.is_relative_to(ws):
        raise RuntimeError("Modeling requires a local library inside the selected workspace")
    if any((ws / ".ads-mcp" / "locks").glob("*.json")):
        raise RuntimeError("Workspace has a Momentum run reservation; finish/recover it before modeling")
    if not lib.has_tech:
        lib.create_layout_tech_std_ads("millimeter", 1000000, True)

    def inside(path):
        path = Path(path).resolve()
        if not path.is_relative_to(ws):
            raise RuntimeError("Model output escapes the workspace")
        return path

    def layer(name):
        return LayerId.from_name(lib, name, "drawing")

    if a["operation"] == "substrate":
        name = a["name"]
        if subst.substrate_exists(lib, name):
            raise RuntimeError("Substrate already exists; choose a new name")
        # Resolve every conductor layer before changing the materials database.
        layer_ids = [layer(c["layer"]) for c in a["conductors"]]
        matfile = inside(lib_dir / "materials.matdb")
        mats = subst.load_materials(str(matfile)) if matfile.exists() else subst.copy_predefined_materials()
        dielectric_names, conductor_names = [], []
        for spec in a["dielectrics"]:
            key = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]
            material_name = "MCP_D_" + key
            existing = next((m for m in mats.dielectrics if m.name == material_name), None)
            if existing is not None:
                if float(existing.er_real) != spec["er"] or float(existing.er_loss_tangent) != spec["loss_tangent"] or existing.loss_type != subst.DielectricLossType.FREQUENCY_INDEPENDENT:
                    raise RuntimeError("Existing generated dielectric material has conflicting parameters")
            else:
                mat = subst.SubstrateDielectric(material_name)
                mat.er_real = str(spec["er"])
                mat.er_loss_tangent = str(spec["loss_tangent"])
                mat.loss_type = subst.DielectricLossType.FREQUENCY_INDEPENDENT
                mat.mur_real = "1"
                mats.dielectrics.add(mat)
            dielectric_names.append(material_name)
        for spec in a["conductors"]:
            sigma = spec["conductivity_s_per_m"]
            material_name = "PERFECT_CONDUCTOR"
            if sigma is not None:
                key = hashlib.sha256(str(sigma).encode()).hexdigest()[:16]
                material_name = "MCP_C_" + key
                existing = next((m for m in mats.conductors if m.name == material_name), None)
                if existing is not None:
                    if float(existing.real) != sigma or existing.parameter_type != subst.ConductorParamType.CONDUCTANCE:
                        raise RuntimeError("Existing generated conductor material has conflicting parameters")
                else:
                    mat = subst.SubstrateConductor(material_name)
                    mat.parameter_type = subst.ConductorParamType.CONDUCTANCE
                    mat.real = str(sigma)
                    mats.conductors.add(mat)
            conductor_names.append(material_name)
        material_tmp = inside(lib_dir / ("materials."+uuid.uuid4().hex+".matdb"))
        try:
            subst.save_materials(str(material_tmp), mats)
            os.replace(material_tmp, matfile)
        finally:
            material_tmp.unlink(missing_ok=True)
        stack = subst.create_substrate(lib, name)
        # The empty stack has infinite AIR on both sides. A ground cover
        # makes material 0 finite; an open bottom needs a separate AIR slab.
        offset = 0 if a["ground_bottom"] else 1
        for i in range(len(a["dielectrics"])-1+offset):
            stack.insert_material_and_interface_above(i)
        if a["ground_bottom"]:
            stack.interfaces[0].convert_to_cover()
            stack.interfaces[0].material_name = "PERFECT_CONDUCTOR"
            stack.interfaces[0].thickness_expr = "0"
        for i, (spec, material_name) in enumerate(zip(a["dielectrics"], dielectric_names)):
            item = stack.materials[i+offset]
            item.material_name = material_name
            item.thickness_expr = str(spec["height_mm"] * 1000)
            item.thickness_unit = subst.Unit.MICRON
        for spec, lid, material_name in zip(a["conductors"], layer_ids, conductor_names):
            item = stack.insert_layer(spec["interface"]+offset, de.ProcessRole.CONDUCTOR)
            item.layer_number = lid.layer
            item.material_name = material_name
            item.thickness_expr = str(spec["thickness_um"])
            item.thickness_unit = subst.Unit.MICRON
            item.sheet = spec["thickness_um"] == 0
        for spec in a.get("vias", []):
            item = stack.insert_conductor_via(spec["lower_interface"] + offset, spec["upper_interface"] + offset)
            item.layer_number = layer(spec["layer"]).layer
            item.material_name = conductor_names[spec["conductor_index"]]
            if spec.get("plating_thickness_um") is not None:
                item.is_plating_enabled = True
                item.plating_dielectric_material_name = "AIR"
                item.plating_thickness_expr = str(spec["plating_thickness_um"])
                item.plating_thickness_unit = subst.Unit.MICRON
        stack.save_substrate()
        if sum(not m.is_infinite_material for m in stack.materials) != len(a["dielectrics"]):
            raise RuntimeError("ADS stack finite-layer count differs from the requested dielectric count")
        return {"substrate": a["lib"]+":"+name, "materials_file": str(matfile),
                "dielectric_count": len(a["dielectrics"]), "conductor_count": len(stack.layers),
                "ground_bottom": a["ground_bottom"], "via_count": len(a.get("vias", []))}

    cell_name, view = a["cell"], a["view"]
    cell = lib.cell(cell_name) if lib.cell_exists(cell_name) else None
    if a["operation"] == "layout":
        if cell is not None and cell.view_exists(view):
            raise RuntimeError("Layout view already exists; choose a new cell/view")
        lids = [layer(shape["layer"]) for shape in a["shapes"]]
        lay = db.create_layout(a["lib"]+":"+cell_name+":"+view)
        factor = lay.meter_to_uu_factor * 0.001
        with Transaction(lay, "Create parameterized EM layout") as txn:
            for shape, lid in zip(a["shapes"], lids):
                pts = [(p[0]*factor, p[1]*factor) for p in shape["points_mm"]]
                if shape["kind"] == "rectangle":
                    lay.add_rectangle(lid, pts[0], pts[1])
                else:
                    lay.add_polygon(lid, pts)
            txn.commit()
        lay.save_design()
        return {"layout": a["lib"]+":"+cell_name+":"+view, "shape_count": len(a["shapes"]),
                "input_units": "mm", "layout_units": lay.unit_name,
                "layout_path": str(inside(lib.cell(cell_name).view(view).path))}

    if cell is None or not cell.view_exists(view):
        raise RuntimeError("Create and save the requested layout first")
    if not cell.view(view).is_layout_view:
        raise RuntimeError("Requested view is not a layout")
    # WRITE initializes a blank design in ADS. APPEND preserves saved geometry.
    lay = db.open_design(a["lib"]+":"+cell_name+":"+view, db.DesignMode.APPEND)
    factor = lay.meter_to_uu_factor * 0.001
    if a["operation"] == "ports":
        existing_names = {t.name for t in lay.terms}
        existing_numbers = {t.number for t in lay.terms}
        lids = [layer(p["layer"]) for p in a["ports"]]
        for port in a["ports"]:
            if port["name"] in existing_names or port["number"] in existing_numbers:
                raise RuntimeError("Port name or number already exists; existing terminals are preserved")
        with Transaction(lay, "Add layout ports") as txn:
            for port, lid in zip(a["ports"], lids):
                net = lay.find_or_add_net(port["net"])
                term = lay.add_term(net, port["name"])
                term.number = port["number"]
                dot = lay.add_dot(lid, (port["x_mm"]*factor, port["y_mm"]*factor))
                lay.add_pin(term, dot, angle=port["angle"])
            txn.commit()
        lay.save_design()
        return {"layout": a["lib"]+":"+cell_name+":"+view, "ports_added": a["ports"],
                "total_terminals": len(list(lay.terms))}

    if a["operation"] != "setup":
        raise ValueError("Unknown modeling operation")
    if cell.view_exists(a["setup"]):
        raise RuntimeError("EM Setup already exists; choose a new setup name to retain the previous configuration")
    if not subst.substrate_exists(lib, a["substrate"]):
        raise RuntimeError("Requested substrate does not exist in the local library")
    stack = subst.open_substrate(lib, a["substrate"])
    bottom = stack.interfaces[0]
    if not bottom.is_cover or bottom.is_termination:
        raise RuntimeError("Ground-referenced setup requires a bottom ground cover; use ground_bottom=True")
    terms = sorted(list(lay.terms), key=lambda term: term.number)
    if not terms or [t.number for t in terms] != list(range(1, len(terms)+1)):
        raise RuntimeError("Layout terminals must be consecutively numbered from 1")
    if len(terms) > 64 or any(len(list(t.pins)) != 1 for t in terms):
        raise RuntimeError("Use at most 64 terminals with one point pin per terminal")
    ports = []
    for term in terms:
        pin = list(term.pins)[0]
        bbox = pin.get_pinfig_bbox()
        if bbox.lower_left.x != bbox.upper_right.x or bbox.lower_left.y != bbox.upper_right.y:
            raise RuntimeError("Momentum setup builder requires point pins, not area pins")
        lid, point = pin.snap_layer_id, pin.snap_point
        if lid.purpose != -1:
            raise RuntimeError("Layout ports must use the drawing purpose")
        ports.append({"name": term.name, "number": term.number, "net": term.net.name,
                      "x_m": point.x / lay.meter_to_uu_factor, "y_m": point.y / lay.meter_to_uu_factor,
                      "layer": lid.layer, "purpose": lid.purpose,
                      "layer_name": lib.tech.find_layer(lid.layer).name})
    template = Path(os.environ["HPEESOF_DIR"]) / "em" / "setupTemplates" / "RFBoard.emSetupT"
    root = ET.parse(template).getroot()
    if root.tag != "EmSimSetup":
        raise RuntimeError("Installed RFBoard template has an unsupported format")
    main = root.find("MainWindow")
    if main is None:
        raise RuntimeError("Installed RFBoard template has no MainWindow")
    main.set("version", "2.2")

    def node(parent, tag, text=None, version=None):
        item = parent.find(tag)
        if item is None:
            item = ET.SubElement(parent, tag)
        if version:
            item.set("version", version)
        if text is not None:
            item.text = str(text)
        return item

    node(main, "simCalculationType", "5")
    overview = node(main, "SimSetupOverview", version="2.0")
    node(overview, "setupType", "0")
    node(overview, "simulator", "0")  # Momentum microwave; RFBoard defaults to RF.
    partition = node(main, "Partitioning", version="1.1")
    for tag, value in {"preferCircuitPartitioning":"False", "requiresCosimulation":"False",
                       "definedBy":"0", "topLibCellView":a["lib"]+":"+cell_name+":"+view,
                       "forcedEmOnly":"False"}.items():
        node(partition, tag, value)
    layout = node(main, "MomLayout", version="1.0")
    for tag, value in {"workspaceText": str(ws), "libraryText": a["lib"], "cellText": cell_name, "viewText": view}.items():
        node(layout, tag, value)
    substrate = node(main, "MomSubstrate", version="2.0")
    node(substrate, "alternateSubstrate", "False")
    node(substrate, "libSubstName", a["lib"]+":"+a["substrate"])
    node(substrate, "libLtdName", "")
    table = node(main, "MomSimFreqTable", version="1.0")
    table.clear()
    table.set("version", "1.0")
    plans = node(table, "freqPlanMap")
    node(plans, "keyName", "1")
    plan = node(plans, "MomSimFreqTableDef", version="1.0")
    step = (a["stop_ghz"]-a["start_ghz"])/(a["points"]-1)
    for tag, value in {"numFreq": "1", "typeFreq": "1", "enableFreq": "True",
                       "startFreq": a["start_ghz"], "stopFreq": a["stop_ghz"],
                       "stepFreq": format(step, ".15g"), "ptsFreq": a["points"],
                       "startFreqUnit": "GHz", "stopFreqUnit": "GHz", "stepFreqUnit": "GHz"}.items():
        node(plan, tag, value)
    reuse = node(main, "MomSimReuseVisualization", version="2.0")
    node(reuse, "reusePreviousResults", "False")
    node(reuse, "saveCurrentsInFile", "0")
    dataset = node(main, "MomSimDataset", version="2.0")
    for tag, value in {"writeDataset":"True", "openDisplay":"False", "dataset_useCellName":"True",
                       "givenDatasetName":a["dataset"], "addDecoration":"False", "useSimType":"False",
                       "dds_useCellName":"True", "givenDdsName":a["dataset"],
                       "useSimSetupViewName":"False", "useSimOptionsName":"False", "useThisText":"False",
                       "thisText":"mySuffix", "autoselectTemplate":"False", "templateName":"S_Nport_Slider.ddt"}.items():
        node(dataset, tag, value)
    output = node(main, "OutputPlan", version="1.0")
    node(output, "createDatasetForCosim", "False")
    node(output, "writeDataset", "True")
    outputs = node(output, "freqPlanForOutputMap")
    for kind, selection in (("currents", "0"), ("fields", "2")):
        ET.SubElement(outputs, "keyName").text = kind
        item = ET.SubElement(outputs, "FreqPlanForOutput", version="1.0")
        node(item, "freqPlanTypeName", kind)
        node(item, "saveFreqPlanType", selection)
    item = node(output, "FreqPlanForOutput", version="1.0")
    node(item, "freqPlanTypeName", "currents")
    node(item, "saveFreqPlanType", "0")
    ds = node(output, "SimDataset", version="1.0")
    for tag, value in {"ds_useCellName":"True", "ds_givenName":a["dataset"], "ds_addDecoration":"False",
                       "ds_useSimSetupViewName":"False", "ds_useSimType":"False", "ds_useSimOptionsName":"False", "ds_useThisText":"False",
                       "ds_thisText":"mySuffix"}.items():
        node(ds, tag, value)
    display = node(output, "SimDataDisplay", version="1.0")
    node(display, "openDisplay", "False")
    node(display, "dds_useCellName", "True")
    node(display, "dds_givenName", a["dataset"])
    node(display, "autoselectTemplate", "False")
    node(display, "templateName", "S_Nport_Slider.ddt")
    resources = node(main, "SimulationResources2", version="1.0")
    resources.clear()
    resources.set("version", "1.0")
    node(resources, "resourceType", "LocalRemote")
    local = node(resources, "DistributedResources2", version="1.1")
    node(local, "host", "Local")
    node(local, "startType", "Now")
    node(local, "parallelJobsForFreqSweep", "1")
    directory = node(local, "SimulationDirectory", version="1.0")
    node(directory, "sharedLocal", "True")
    node(directory, "customPath", "")
    editor = node(main, "PortEditorDialog", version="1.1")
    editor.clear()
    editor.set("version", "1.1")
    for tag, value in {"autoSelect":"True", "autoCenter":"True", "autoZoom":"False",
                       "hideConnectedPins":"True", "dataScope":"0"}.items():
        node(editor, tag, value)
    for viewtag, listtag in (("PinView", "Pins"), ("PortView", "Ports")):
        part = node(editor, viewtag, version="1.2" if viewtag == "PortView" else "1.1")
        node(part, "isSnapshotForInfoOnly", "True")
        entries = node(part, listtag)
        for p in ports:
            if viewtag == "PinView":
                entry = ET.SubElement(entries, "Pin", version="1.1")
                for tag, value in {"pinName":p["name"], "netName":p["net"], "pinNum":p["number"]}.items():
                    node(entry, tag, value)
                shape = node(node(entry, "shapes"), "PinShapeDef", version="1.0")
                for tag, value in {"layerNum":p["layer"], "purposeNum":p["purpose"],
                                   "layerIdName":p["layer_name"]+":drawing", "shapeType":"point"}.items():
                    node(shape, tag, value)
                node(node(shape, "points"), "stdItem", f'{p["x_m"]:.15g}:{p["y_m"]:.15g}')
            else:
                entry = ET.SubElement(entries, "Port", version="1.3")
                for tag, value in {"portNum":p["number"], "portName":p["name"], "refImpedance":f'{a["impedance_ohms"]}:0',
                                   "feedType":"Auto", "refoffset":"0", "termType":"2"}.items():
                    node(entry, tag, value)
                node(node(entry, "PlusPinNames"), "stdItem", p["name"])
                node(node(entry, "MinusPinNames"), "stdItem", "::__GND__")
    # Create the registered external OA view; paths are supplied by ADS, not
    # guessed from Windows name encoding. Existing views are never overwritten.
    setup_view = cell.create_view(a["setup"], "eesof_em_setup")
    folder = inside(setup_view.path)
    folder.mkdir(parents=True, exist_ok=True)
    xml = folder / "emStateFile.xml"
    temporary = folder / ("emStateFile."+uuid.uuid4().hex+".tmp")
    ET.indent(root)
    ET.ElementTree(root).write(temporary, encoding="utf-8", xml_declaration=True)
    os.replace(temporary, xml)
    (folder / "eesof_em_setup.file").touch(exist_ok=True)
    master = folder / "master.tag"
    if not master.exists():
        master.write_text("-- Master.tag File, Rev:1.0\neesof_em_setup.file\n", encoding="ascii")
    with Transaction(lay, "Link native Momentum setup") as txn:
        link = lay.find_prop("SIM_CONTROLLER_DESIGN")
        target = a["lib"]+":"+cell_name+":"+a["setup"]
        if link is None:
            de.db.StringProp.create(lay, "SIM_CONTROLLER_DESIGN", target)
        else:
            link.value = target
        txn.commit()
    lay.save_design()
    # The solver's GUI-only AEL link lookup is checked by ads_run_momentum
    # after handoff. The library and XML linkage are saved here.
    return {"setup_file": str(xml), "setup": a["setup"], "layout": a["lib"]+":"+cell_name+":"+view,
            "port_count": len(ports), "ports": ports, "template": str(template), "dataset_name": a["dataset"]}


class EmModeler:
    def execute(self, workspace: str, lib: str, operation: str, **data) -> dict:
        ws = Path(workspace).expanduser().resolve(strict=True)
        if not (ws / "lib.defs").is_file():
            raise ValueError("workspace must contain lib.defs")
        _name(lib)
        for key in ("cell", "view", "name", "setup", "substrate", "dataset"):
            if key in data:
                _name(data[key])
        if operation == "substrate":
            ds = [DielectricLayer.model_validate(x).model_dump(mode="json") for x in data["dielectrics"]]
            cs = [ConductorLayer.model_validate(x).model_dump(mode="json") for x in data["conductors"]]
            if not 1 <= len(ds) <= 32 or not 1 <= len(cs) <= 32:
                raise ValueError("use 1..32 dielectrics and 1..32 conductor mappings")
            if any(c["interface"] > len(ds) for c in cs) or len({c["layer"] for c in cs}) != len(cs):
                raise ValueError("conductor interfaces must be within the stack and layer names must be unique")
            data.update(dielectrics=ds, conductors=cs)
            vias = [ViaLayer.model_validate(x).model_dump(mode="json") for x in data.get("vias", [])]
            if any(not v["lower_interface"] < v["upper_interface"] <= len(ds) or v["conductor_index"] >= len(cs) for v in vias):
                raise ValueError("Via interfaces/conductor index are outside the substrate stack")
            data["vias"] = vias
        elif operation == "layout":
            data["shapes"] = [LayoutShape.model_validate(x).model_dump(mode="json") for x in data["shapes"]]
            if not 1 <= len(data["shapes"]) <= 1000 or sum(len(s["points_mm"]) for s in data["shapes"]) > 10000:
                raise ValueError("use 1..1000 shapes and at most 10000 total vertices per layout")
        elif operation == "ports":
            data["ports"] = [LayoutPort.model_validate(x).model_dump(mode="json") for x in data["ports"]]
            ports = data["ports"]
            if not 1 <= len(ports) <= 64 or len({p["name"] for p in ports}) != len(ports) or len({p["number"] for p in ports}) != len(ports):
                raise ValueError("use 1..64 ports with distinct names and numbers")
        elif operation == "setup":
            import math
            if not all(type(data[k]) in (int, float) and math.isfinite(data[k]) for k in ("start_ghz", "stop_ghz", "impedance_ohms")):
                raise ValueError("frequencies and impedance must be finite numbers")
            if not 0 < data["start_ghz"] < data["stop_ghz"] or data["impedance_ohms"] <= 0:
                raise ValueError("use positive increasing frequencies and positive impedance")
            if data["impedance_ohms"] != 50:
                raise ValueError("the current Momentum setup builder supports 50-ohm reference ports")
            if type(data["points"]) is not int or not 2 <= data["points"] <= 100_000:
                raise ValueError("points must be an integer from 2 to 100000")
            # ADS initializes a newly created setup's dataset to its cell name.
            # Use that supported convention; historical task copies stay unique.
            data["dataset"] = data["cell"]
        else:
            raise ValueError("unknown modeling operation")
        args = {"workspace": str(ws), "lib": lib, "operation": operation, **data}
        result = _model_in_ads(args)
        if operation == "setup":
            result["inspection"] = inspect_setup(str(ws), result["setup_file"], data["setup"])
        return result
