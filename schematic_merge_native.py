"""Apply a reviewed electrical delta to exactly one ADS design and verify the reopened graph."""
from pathlib import Path

from design_snapshots import manifest, opened_copy, read_design
from schematic_graph import differences, elements, wire_key


def read_electrical(design, contract):
    snap, netlist = read_design(design, contract)
    for native, shape in zip(design.shapes, snap["shapes"], strict=True):
        if shape["kind"] == "Line" and native.find_first_wire_label() is not None:
            shape["label"] = shape["native_net"]
    if not snap["complete"]:
        raise ValueError(f"Unreadable native fields: {snap['unreadable']}")
    return snap, netlist


def set_parameter(inst, name, entry):
    from design_snapshots import json_value
    param = inst.parameters[name]
    value = entry["value"]
    if hasattr(param, "repeats"):
        while len(param.repeats) < len(value):
            param.repeats.clone()
        while len(param.repeats) > len(value):
            param.repeats.remove(len(param.repeats) - 1)
        for item, form in zip(param.repeats, entry["forms"], strict=True):
            if item.form_name != form:
                item.form_name = form
    elif param.form_name != entry["form"]:
        param.form_name = entry["form"]
    if json_value(param.value) != value:
        param.value = value
    inst.invoke_item_parameter_changed_callback(name)


def write_elements(design, original, expected, changes):
    wire_keys = {row["key"][1] for row in changes if row["key"][0] == "wire"}
    for shape in list(design.shapes):
        if type(shape).__name__ == "Line":
            points = [[float(p.x), float(p.y)] for p in shape.get_outline().points]
            if wire_key(points) in wire_keys:
                shape.delete_object()

    device_names = [row["key"][1] for row in changes if row["key"][0] == "device"]
    created = set()
    for name in device_names:
        key = ("device", name)
        before, after = original.get(key), expected.get(key)
        same_model = before and after and all(before[k] == after[k] for k in ("library", "cell", "view"))
        if before and not same_model:
            design.find_instance(name).delete_object()
        if not after:
            continue
        if same_model:
            inst = design.find_instance(name)
            if before["at"] != after["at"]:
                inst.origin = tuple(after["at"])
        else:
            inst = design.add_instance(f"{after['library']}:{after['cell']}:{after['view']}", tuple(after["at"]), name=name)
            created.add(name)
        if inst.orient.name != after["orientation"]:
            inst.orient = type(inst.orient)[after["orientation"]]
        if after["shorted"]:
            inst.deactivate_and_short()
        elif after["deactivated"]:
            inst.deactivate()
        elif inst.is_deactivated:
            inst.activate()

    changed = {tuple(row["key"]) for row in changes}
    touched = set(created)
    for key, value in expected.items():
        if key[0] not in ("parameter", "variable") or (key not in changed and key[1] not in created):
            continue
        kind, name, field = key
        inst = design.find_instance(name)
        if kind == "variable":
            inst.vars[field] = value
        else:
            set_parameter(inst, field, value)
        touched.add(name)
    for name in touched:
        inst = design.find_instance(name)
        if inst.is_var_instance:
            for var in list(inst.vars):
                if ("variable", name, var) not in expected:
                    del inst.vars[var]
        inst.update_item_annotation()
    # A removal can be the sole edit to a surviving VAR block.
    for key in changed:
        if key[0] == "variable" and key not in expected and ("device", key[1]) in expected and key[1] not in touched:
            inst = design.find_instance(key[1])
            del inst.vars[key[2]]
            inst.update_item_annotation()
    for key in sorted(wire_keys):
        for route in expected.get(("wire", key), []):
            wire = design.add_wire([tuple(point) for point in route["points"]])
            if route["label"]:
                wire.add_wire_label(route["label"])


def apply_changes(engine, target, contract, baseline, changes, baseline_files):
    from ads_engine import get_de
    original = elements(baseline)
    expected = original.copy()
    for row in changes:
        key = tuple(row["key"])
        if original.get(key) != row["before"]:
            raise ValueError(f"Review does not match baseline at {key}")
        if row["after"] is None:
            expected.pop(key)
        else:
            expected[key] = row["after"]

    def verify(design):
        saved, netlist = read_electrical(design, contract)
        diff = differences(expected, elements(saved))
        if diff or not netlist.strip():
            raise ValueError(f"Native merge differs from the selected changes: {diff[:2]}")
        return saved, netlist

    with opened_copy(engine, target, writable=True) as design:
        with get_de().db.Transaction(design, "Reviewed input/output matching merge") as transaction:
            write_elements(design, original, expected, changes)
            transaction.commit()
        verify(design)
        if manifest(Path(target.workspace)) != baseline_files:
            raise ValueError("Integrator changed before native save")
        design.save_design()
    with opened_copy(engine, target) as design:
        saved, netlist = verify(design)
    # Other workflows use their original snapshot representation.
    for shape in saved["shapes"]:
        shape.pop("label", None)
    return saved, netlist
