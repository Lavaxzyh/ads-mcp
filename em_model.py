"""Native EM setup configuration and cache inspection (ADS 2027)."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator


class MeshSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cells_per_wavelength: FiniteFloat = Field(gt=0, le=1000)
    edge_mesh_enabled: bool = True
    reference_frequency_ghz: FiniteFloat | None = Field(default=None, gt=0)


class FrequencyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["linear", "dc"] = "linear"
    start_ghz: FiniteFloat = 0
    stop_ghz: FiniteFloat = 0
    points: int = Field(default=1, strict=True, ge=1, le=100000)

    @model_validator(mode="after")
    def limits(self):
        if self.kind == "dc":
            if self.start_ghz or self.stop_ghz or self.points != 1:
                raise ValueError("DC plan has start=stop=0 and points=1")
        elif not (0 < self.start_ghz < self.stop_ghz and self.points >= 2):
            raise ValueError("Linear RF plan needs 0 < start < stop and at least two points")
        return self


class PortSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: int = Field(strict=True, ge=1, le=64)
    feed_type: Literal["Auto", "TML", "None"] = "Auto"


def em_operation(a):
    import copy
    import hashlib
    import json
    import os
    import xml.etree.ElementTree as ET
    from pathlib import Path
    from keysight.ads import de
    if not de.workspace_is_open():
        raise RuntimeError("No ADS workspace open")
    ws = Path(de.active_workspace().path).resolve()
    lib = de.Library.get(a["lib"])
    if not Path(lib.path).resolve().is_relative_to(ws):
        raise ValueError("EM operations require a local library")
    cell = lib.cell(a["cell"])
    op = a["operation"]
    if op == "model_parameters":
        options = Path(cell.view(a["model"]).path) / "emDataOptions.xml"
        tree = ET.parse(options)
        cp = tree.getroot().find("cellParameters")
        if cp is None:
            raise ValueError("Model has no native geometry parameter options")
        parameters = {p.get("name"): p for p in cp.findall("parameters/cellParameter")}
        unknown = set(a["resolutions"]) - parameters.keys()
        if unknown:
            raise ValueError("Unknown native geometry parameters: " + str(sorted(unknown)))
        before = options.read_bytes()
        cp.set("useInterpolation", "yes" if a["interpolate_geometry"] else "no")
        for name, value in a["resolutions"].items():
            parameters[name].set("resolution", format(value, ".15g"))
        history = ws / ".ads-mcp" / "configuration_history"
        history.mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(str(options).encode()).hexdigest()[:12]
        backup = history / (identity + "_" + hashlib.sha256(before).hexdigest()[:16] + ".xml")
        if not backup.exists():
            backup.write_bytes(before)
        temporary = options.with_suffix(".xml.tmp")
        tree.write(temporary, encoding="utf-8", xml_declaration=True)
        os.replace(temporary, options)
        return {"model_options": str(options), "backup": str(backup), "requires_workspace_reload": True,
                "geometry_interpolation": cp.get("useInterpolation"), "resolutions": a["resolutions"],
                "cache_deleted": False}
    if op == "inspect_model":
        folder = Path(cell.view(a["model"]).path)
        required = ["eesof_em_model.file", "model.ndx", "emDataIndex.xml", "emStateFile.xml"]
        present = {n: (folder / n).is_file() for n in required}
        samples, names = [], []
        index = folder / "emDataIndex.xml"
        if index.exists():
            root = ET.parse(index).getroot()
            names = [n.text for n in root.findall(".//ParameterNames/parameterName")]
            # ADS writes cache samples as <entry><parameterValues>... .
            for elem in root.iter():
                values = elem.find("parameterValues")
                if values is not None:
                    samples.append({"parameters": dict(zip(names, [v.text for v in values])),
                                    "metadata": ET.tostring(elem, encoding="unicode")})
        options = folder / "emDataOptions.xml"
        interpolation = None
        if options.exists():
            cp = ET.parse(options).getroot().find("cellParameters")
            interpolation = cp.get("useInterpolation") if cp is not None else None
        return {"model": a["lib"] + ":" + a["cell"] + ":" + a["model"],
                "folder": str(folder), "files": present, "complete_files": all(present.values()),
                "parameter_names": names, "samples": samples, "sample_count": len(samples),
                "geometry_interpolation": interpolation,
                "data_files": [p.name for p in folder.glob("data.*")],
                "note": "File presence is not a numerical validation or proof of current mesh correspondence"}
    folder = Path(cell.view(a["setup"]).path)
    xml = folder / "emStateFile.xml"
    tree = ET.parse(xml)
    main = tree.getroot().find("MainWindow")
    if main is None:
        raise ValueError("Invalid native EM Setup")
    def node(parent, name, value=None, version=None):
        e = parent.find(name)
        if e is None:
            e = ET.SubElement(parent, name)
        if version:
            e.set("version", version)
        if value is not None:
            e.text = str(value)
        return e
    before = hashlib.sha256(xml.read_bytes()).hexdigest()
    if op == "mesh":
        meshes = main.findall(".//MomSimOptMeshGlobal")
        if not meshes:
            raise ValueError("Setup contains no native global mesh configuration")
        previous = [{e.tag: e.text for e in m} for m in meshes]
        s = a["mesh"]
        for m in meshes:
            node(m, "MeshDensityCpW", s["cells_per_wavelength"])
            node(m, "EdgeMeshEnabled", str(s["edge_mesh_enabled"]))
            freq = s.get("reference_frequency_ghz")
            node(m, "MeshDensityCpWHighestSimFreq", str(freq is None))
            if freq is not None:
                node(m, "MeshDensityCpWFreq", freq)
                node(m, "MeshDensityCpWFreqUnit", "3")
        details = {"before": previous, "mesh": s, "requires_new_solve": True}
    elif op == "model":
        blocks = main.findall("EMComponent")
        component = copy.deepcopy(blocks[0]) if blocks else ET.Element("EMComponent", version="1.0")
        for b in blocks:
            main.remove(b)
        output = node(component, "OutputEmModel", version="2.1")
        for tag, value in {"modelName": a["model"], "appendText": "False",
                           "useLayoutViewName": "False", "useSimSetupViewName": "False",
                           "useSimType": "False", "useThisText": "False", "thisText": "mySuffix",
                           "autoCreateEMModel": "True", "autoUpdateEMModel": "True",
                           "includeSParamDataOnly": "True"}.items():
            node(output, tag, value)
        node(node(output, "EmModelContent", version="1.0"), "reducedToDecreaseSize", "1")
        symbol = node(component, "OutputEmSymbol", version="2.0")
        for tag, value in {"symbolName": a.get("symbol", "symbol"), "symbolIsBlackBox": "False",
                           "sizeDefinitionType": "0", "sizeDefinitionNbrOfUnits": "1",
                           "addRefPin": "False", "autoCreateSymbol": "False"}.items():
            node(symbol, tag, value)
        main.append(component)
        details = {"model": a["model"], "auto_create": True, "auto_update": True,
                   "symbol_created_separately": True}
    elif op == "setup_options":
        if a.get("plans"):
            freqmap = main.find("MomSimFreqTable/freqPlanMap")
            if freqmap is None:
                raise ValueError("No frequency plan map")
            freqmap.clear()
            for n, spec in enumerate(a["plans"], 1):
                ET.SubElement(freqmap, "keyName").text = str(n)
                plan = ET.SubElement(freqmap, "MomSimFreqTableDef", version="1.0")
                dc = spec["kind"] == "dc"
                step = 0 if dc else (spec["stop_ghz"] - spec["start_ghz"]) / (spec["points"] - 1)
                # Discrete DC type and field names match the native ADS setup.
                for tag, value in {"numFreq": n, "typeFreq": "3" if dc else "1", "enableFreq": "True",
                                   "startFreq": spec["start_ghz"], "stopFreq": -1 if dc else spec["stop_ghz"],
                                   "stepFreq": " - " if dc else format(step, ".15g"), "ptsFreq": " - " if dc else spec["points"],
                                   "startFreqUnit": "GHz", "stopFreqUnit": "GHz", "stepFreqUnit": "GHz"}.items():
                    node(plan, tag, value)
        ports = main.findall("PortEditorDialog/PortView/Ports/Port")
        requested = {p["number"]: p["feed_type"] for p in a.get("ports", [])}
        if set(requested) - {int(p.findtext("portNum")) for p in ports}:
            raise ValueError("Port setting refers to an unknown EM port")
        for p in ports:
            number = int(p.findtext("portNum"))
            if number in requested:
                node(p, "feedType", requested[number])
        if requested:
            node(main.find("PortEditorDialog/PortView"), "isSnapshotForInfoOnly", "False")
        details = {"plans": a.get("plans"), "ports": a.get("ports")}
    else:
        raise ValueError("Unknown EM operation")
    history = ws / ".ads-mcp" / "configuration_history"
    history.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256((a["lib"] + ":" + a["cell"] + ":" + a["setup"]).encode()).hexdigest()[:12]
    backup = history / (identity + "_" + before[:16] + ".xml")
    if not backup.exists():
        backup.write_bytes(xml.read_bytes())
    temporary = xml.with_suffix(".xml.tmp")
    ET.indent(tree)
    tree.write(temporary, encoding="utf-8", xml_declaration=True)
    os.replace(temporary, xml)
    after = hashlib.sha256(xml.read_bytes()).hexdigest()
    state = {"setup_file": str(xml), "operation": op, "before_sha256": before,
             "after_sha256": after, "backup": str(backup), "requires_workspace_reload": True,
             "existing_results_current": False, **details}
    (history / (identity + "_latest.json")).write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state
