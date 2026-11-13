"""Semantic keys for saved top-level schematic differences and three-way merges."""
from collections import defaultdict
import json
import re

from edit_requests import QUANTITY


def wire_key(points):
    ends = sorted((points[0], points[-1]))
    return json.dumps(ends, separators=(",", ":"))


def elements(snap):
    rows = {}
    for part in snap["components"]:
        name = part["name"]
        rows["device", name] = {k: part[k] for k in ("library", "cell", "view", "at",
                                                     "orientation", "deactivated", "shorted")}
        rows["device", name]["pins"] = sorted(
            ({"ref": pin["ref"], "point": pin["point"]} for pin in part["pins"]), key=lambda p: p["ref"])
        variables = part["variables"] or {}
        for key, value in variables.items():
            rows["variable", name, key] = value
        for key, record in part["parameters"].items():
            if key == "Variable Value" and variables:
                table = record.get("value")
                if isinstance(table, list) and all(isinstance(pair, list) and len(pair) == 2 for pair in table) and dict(table) == variables:
                    continue
            rows["parameter", name, key] = record
    for net in snap["connections"]:
        pins = sorted(net["pins"])
        name = net["native_net"]
        # Native anonymous net IDs are allocated afresh when wires are edited.
        explicit = None if re.fullmatch(r"N__\d+", name) or name.startswith("unconnected:") else name
        for ref in pins:
            rows["pin_net", ref] = {"name": explicit, "pins": pins}
    wires = defaultdict(list)
    notes = []
    for shape in snap["shapes"]:
        if shape["kind"] == "Line":
            points = shape["points"]
            route = min(points, list(reversed(points)))
            wires[wire_key(route)].append({"points": route, "label": shape.get("label")})
        else:
            notes.append({k: v for k, v in shape.items() if k != "native_net"})
    for key, group in wires.items():
        rows["wire", key] = sorted(group, key=lambda item: json.dumps(item, sort_keys=True))
    rows["notes",] = sorted(notes, key=lambda item: json.dumps(item, sort_keys=True))
    contract = dict(snap.get("contract") or {})
    if "frequency_hz" in contract:
        contract["frequency_hz"] = list(contract["frequency_hz"])
    for port in contract.get("ports", []):
        rows["port", port["instance"]] = port
    rows["conditions",] = {k: v for k, v in contract.items() if k != "ports"}
    return rows


def differences(before, after):
    return [{"key": list(key), "before": before.get(key), "after": after.get(key)}
            for key in sorted(before.keys() | after.keys()) if before.get(key) != after.get(key)]


def displayed_value(entry):
    if isinstance(entry, dict):
        entry = entry.get("value")
    quantity = QUANTITY.fullmatch(entry) if isinstance(entry, str) else None
    return {"value": entry, "unit": quantity[1] if quantity else None}


def describe(before, after):
    """Return each affected instance/pin with both native net names and peer pins."""
    result = {k: [] for k in ("parameters", "devices", "connections", "ports", "wires", "other")}
    if any(not snap.get("complete",True) for snap in (before,after)):
        return dict(result,available=False,reason="Complete native snapshots required for detailed electrical differences",
                    before_gaps=before.get("unreadable",[]),after_gaps=after.get("unreadable",[]))
    native = [{p: n["native_net"] for n in snap["connections"] for p in n["pins"]}
              for snap in (before, after)]
    for row in differences(elements(before), elements(after)):
        kind, *path = row["key"]
        a, b = row["before"], row["after"]
        if kind in ("parameter", "variable"):
            result["parameters"].append(dict(instance=path[0], field=kind, key=path[1],
                                              before=displayed_value(a), after=displayed_value(b),
                                              before_form=a.get("form", a.get("forms")) if isinstance(a, dict) else None,
                                              after_form=b.get("form", b.get("forms")) if isinstance(b, dict) else None))
        elif kind == "pin_net":
            ref = path[0]
            result["connections"].append(dict(pin=ref, before_net=native[0].get(ref), after_net=native[1].get(ref),
                                               before_peers=[p for p in a["pins"] if p != ref] if a else [],
                                               after_peers=[p for p in b["pins"] if p != ref] if b else []))
        else:
            bucket = {"device": "devices", "port": "ports", "wire": "wires"}.get(kind, "other")
            result[bucket].append(row)
    def ports(snap):
        declared = {p["instance"]: p for p in (snap.get("contract") or {}).get("ports", [])}
        observed = {}
        nets = {p: sorted(n["pins"]) for n in snap["connections"] for p in n["pins"]}
        for part in snap["components"]:
            if (part["library"],part["cell"]) == ("ads_simulation","Term"):
                name = part["name"]
                params = part["parameters"]
                observed[name] = dict(number=params["Num"]["value"], impedance=params["Z"]["value"],
                                      reference_plane=declared.get(name,{}).get("reference_plane"),
                                      connected_pins=nets.get(name+":1", []))
        return observed
    a, b = ports(before), ports(after)
    result["ports"] = [dict(instance=name, before=a.get(name), after=b.get(name))
                       for name in sorted(a.keys() | b.keys()) if a.get(name) != b.get(name)]
    return result
