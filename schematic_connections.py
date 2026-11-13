"""Compare an intended pin partition with the saved native electrical graph before SP."""
import json
from pathlib import Path
import uuid

from pydantic import Field, model_validator
from schematic_flow import Model, Net, Pin, atomic_json, target_lock


class Expected(Model):
    expected_nets: list[Net]
    open_pins: list[Pin] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique(self):
        names = [net.name for net in self.expected_nets]
        pins = [pin for net in self.expected_nets for pin in net.pins] + self.open_pins
        if not pins or len(names) != len(set(names)) or len(pins) != len(set(pins)):
            raise ValueError("Declare each pin exactly once, using unique expected net names")
        return self


class Check(Model):
    expected: Expected | None = None
    bind: bool = False


def check(snap, expected):
    desired = {p: ("net", n.name) for n in expected.expected_nets for p in n.pins}
    desired.update({p: ("open", p) for p in expected.open_pins})
    details = {pin["ref"]: dict(instance=c["name"], pin=pin["ref"], xy=pin["point"], native_net=pin["native_net"])
               for c in snap["components"] for pin in c["pins"]}
    groups = {p: tuple(n["pins"]) for n in snap["connections"] for p in n["pins"]}
    ports = {c["name"]+":1" for c in snap["components"] if (c["library"],c["cell"]) == ("ads_simulation","Term")}
    issues = []

    def issue(kind, refs, **fields):
        issues.append(dict(kind=kind, pins=[details.get(ref, {"pin":ref}) for ref in sorted(refs)], **fields))

    for p in sorted(desired.keys()-details.keys()):
        issue("missing_pin", [p], expected=desired[p][1])
    for p in sorted(details.keys()-desired.keys()):
        issue("undeclared_pin", [p])
    for net in expected.expected_nets:
        present = [p for p in net.pins if p in groups]
        if len({groups[p] for p in present}) > 1:
            isolated = [p for p in present if len(groups[p]) == 1]
            issue("open_pin" if isolated else "split_net", present, expected=net.name, isolated=isolated)
    for n in snap["connections"]:
        refs = n["pins"]
        intended = {desired[p] for p in refs if p in desired}
        if len(intended) > 1:
            shorted_ports = [p for p in refs if p in ports and p in desired]
            kind = "shorted_ports" if len({desired[p] for p in shorted_ports}) > 1 else "joined_nets"
            issue(kind, refs, native_net=n["native_net"], expected=sorted(k[1] for k in intended))
    if snap.get("unreadable"):
        issues.append(dict(kind="unreadable_fields", fields=snap["unreadable"]))
    return dict(ok=not issues, checked_pins=len(details), issues=issues, scope="Saved top-level pin connectivity")


def binding(root, state):
    path = root/"connections.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["target"] != state["target"]:
            raise ValueError("Connection expectation belongs to another target")
        return Expected.model_validate(data["expected"])
    # A build plan already supplies the intended independent graph.
    if state.get("template") or isinstance(state.get("input_source"), str):
        for path in root.glob("operations/*/expanded.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("target") == state["target"] and "expected_nets" in data:
                return Expected(expected_nets=data["expected_nets"], open_pins=data.get("open_pins", []))
    return None


def inspect(payload, root):
    from single_schematic import snapshot, safe_target
    from schematic_flow import Target
    req = Check.model_validate(payload)
    with target_lock(root/"single-schematic"):
        state = json.loads((root/"task.json").read_text(encoding="utf-8"))
        if state["task_directory"] != str(root) or state["status"] != "ready":
            raise ValueError("Select a ready task")
        safe_target(root, Target.model_validate(state["target"]))
        saved = snapshot(state)
        expected = req.expected or binding(root,state)
        if expected is None:
            raise ValueError("Supply expected_nets and intentional open_pins for this imported schematic")
        report = check(saved, expected)
        if req.bind:
            atomic_json(root/"connections.json", dict(target=state["target"],expected=expected.model_dump()))
        report.update(target=state["target"], revision=state["revision"], bound=req.bind or req.expected is None)
        path = root/"connection-checks"/(uuid.uuid4().hex+".json")
        atomic_json(path, report)
        return {**report, "report": str(path)}
