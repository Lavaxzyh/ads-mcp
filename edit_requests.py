"""Prepare a revision-bound edit from the current native snapshot."""
import json
import os
import re
import uuid
from pathlib import Path

from schematic_flow import Target, atomic_json, target_lock
from schematic_templates import Override

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}")
QUANTITY = re.compile(r"\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\s*([a-zA-ZµΩ°][\wµΩ°/^-]*)?\s*")

def editable_fields(snap, models, instance):
    for part in snap["components"]:
        name = part["name"]
        if (instance and name != instance) or not NAME.fullmatch(name):
            continue
        model = models.get(part["library"] + ":" + part["cell"], {}).get("parameters", {})
        for field in ("variables", "parameters"):
            for key, entry in (part[field] or {}).items():
                if not NAME.fullmatch(key):
                    continue
                value = entry if field == "variables" else entry["value"]
                if not isinstance(value, str):
                    continue
                # Contract-bound controls need a coordinated contract_after edit.
                if field == "parameters" and part["library"] == "ads_simulation":
                    if (part["cell"], key) in {("Term","Num"),("Term","Z"),("S_Param","Start"),
                                               ("S_Param","Stop"),("S_Param","Step")}:
                        continue
                if field == "parameters" and entry.get("form") not in (None, "StdForm"):
                    continue
                match = QUANTITY.fullmatch(value)
                yield dict(instance=name, field=field, key=key, value=value,
                           unit=match[1] if match else None,
                           unit_type=model.get(key, {}).get("unit_type") if field == "parameters" else None)

def prepare(directory, instance="", key="", value=None, field="parameters"):
    from single_schematic import TaskRequest, safe_target, snapshot
    from design_changes import review_changes, PatchRequest
    root = Path(directory).resolve()
    if not Path(directory).is_absolute() or not (root/"task.json").is_file():
        raise ValueError("Select an existing imported or built task by absolute task_directory")
    assigned = os.environ.get("ADS_MCP_TASK_DIR")
    if assigned and Path(assigned).resolve() != root:
        raise ValueError("This session belongs to another task directory")
    with target_lock(root/"single-schematic"):
        state = json.loads((root/"task.json").read_text(encoding="utf-8"))
        if state["task_directory"] != str(root) or state["status"] != "ready":
            raise ValueError("Task identity or status does not allow preparing edits")
        target = safe_target(root, Target.model_validate(state["target"]))
        snap = snapshot(state)
        profile = snap.get("capability_profile")
        models = json.loads(Path(profile["profile"]).read_text(encoding="utf-8"))["models"] if profile else {}
        rows = list(editable_fields(snap, models, instance))
        if key:
            rows = [r for r in rows if r["key"] == key and r["field"] == field]
        if value is None:
            return dict(ok=True, target=state["target"], revision=state["revision"], fields=rows)
        if not instance or not key or len(rows) != 1:
            raise ValueError("Select one listed instance, field and key before supplying value")
        change = Override(instance=instance, field=field, key=key, value=value)
        patch = PatchRequest(snapshot=state["snapshot"],snapshot_sha256=state["snapshot_sha256"],
                             destination=target.workspace,changes=[change])
        preview = review_changes(patch, snap)
        request = TaskRequest(operation="edit",request_id="edit-"+uuid.uuid4().hex,
                              target=target,expected_revision=state["revision"],changes=[change])
        path = root/"requests"/(request.request_id+".json")
        path.parent.mkdir(exist_ok=True)
        atomic_json(path, request.model_dump(exclude_unset=True))
        return dict(ok=True, revision=state["revision"], preview=preview,
                    next_call=dict(action="run",task_directory=str(root),request_file=str(path)))
