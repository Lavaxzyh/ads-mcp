"""Fork two task-owned workspaces and prepare a review against their saved common baseline."""
from pathlib import Path
import json
import re
import uuid

from pydantic import Field
from schematic_flow import Model, Contract, Target, atomic_json, target_lock
from design_snapshots import file_hash, manifest, opened_copy
from schematic_graph import elements, differences


SIDES = ("input", "output")


class Fork(Model):
    batch: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class MergePlan(Fork):
    # Omitted sides select all their changes. An explicit empty list selects none.
    selected: dict[str, list[str]] = Field(default_factory=dict)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def live_snapshot(engine, target, contract):
    from schematic_merge_native import read_electrical
    with opened_copy(engine, target) as design:
        snap, _ = read_electrical(design, contract)
    return snap


def fork(payload, directory, engine):
    from single_schematic import run, safe_target, snapshot
    from workspace_activity import edit_guard
    req = Fork.model_validate(payload)
    root = Path(directory).resolve()
    base = root/"collaboration"/req.batch
    path = base/"baseline.json"
    with target_lock(root/"single-schematic"):
        state = _read(root/"task.json")
        target = safe_target(root, Target.model_validate(state["target"]))
        if state["task_directory"] != str(root) or state["status"] != "ready":
            raise ValueError("Select a ready integrator task")
        complete = base/"fork.json"
        if complete.exists():
            return {k:v for k,v in _read(complete).items() if k != "branch_files"}
        with edit_guard(target.workspace):
            original = snapshot(state)
            if path.exists():
                anchor = _read(path)
                if anchor["snapshot_sha256"] != state["snapshot_sha256"]:
                    raise ValueError("Integrator changed during fork creation; select a new batch")
            else:
                anchor = dict(revision=state["revision"], snapshot_sha256=state["snapshot_sha256"],
                              source_files=original["source_files"], target=state["target"],
                              contract=state["contract"], schematic=live_snapshot(engine, target, Contract.model_validate(state["contract"])))
                atomic_json(path, anchor)
            tasks = {}
            branch_files = {}
            for side in SIDES:
                task = base/side
                branch = target.model_copy(update={"workspace": str(task/"design_wrk")})
                receipt = run(dict(operation="import", request_id="fork", source=state["target"],
                                   target=branch.model_dump(), contract=state["contract"]), task, engine)
                child = _read(task/"task.json")
                if not receipt["ok"] or child["original"]["files"] != anchor["source_files"]:
                    raise ValueError("Branch was not copied from the frozen baseline")
                imported = _read(task/"operations/fork/result.json")
                branch_files[side] = _read(Path(imported["snapshot"]))["source_files"]
                tasks[side] = str(task)
            if manifest(Path(target.workspace)) != anchor["source_files"]:
                raise ValueError("Integrator changed while creating copies")
            result = dict(ok=True, batch=req.batch, revision=state["revision"], tasks=tasks,
                          baseline_sha256=file_hash(path))
            atomic_json(complete, dict(result,branch_files=branch_files))
            return result


def change_groups(base, current):
    delta = differences(base, current)
    changed_devices = {r["key"][1] for r in delta if r["key"][0] == "device"}
    groups = {}
    for row in delta:
        kind, *path = row["key"]
        group = "topology"
        if kind in ("parameter", "variable") and path[0] not in changed_devices:
            group = ".".join(row["key"])
        groups.setdefault(group, []).append(row)
    return groups


def conflicts(left, right):
    left_keys = {tuple(r["key"]): r for r in left}
    right_keys = {tuple(r["key"]): r for r in right}
    issues = []
    for key in sorted(left_keys.keys() & right_keys.keys()):
        if left_keys[key]["after"] != right_keys[key]["after"]:
            issues.append(dict(reason="different_edits", key=key, input=left_keys[key]["after"], output=right_keys[key]["after"]))
    for side, a, b in (("input", left_keys, right_keys), ("output", right_keys, left_keys)):
        devices = {key[1] for key in a if key[0] == "device"}
        variables = {key[2] for key in a if key[0] == "variable"}
        for key, row in b.items():
            if key[0] not in ("parameter", "variable", "pin_net"):
                continue
            instance = key[1].split(":")[0] if key[0] == "pin_net" else key[1]
            if instance in devices and key not in a:
                issues.append(dict(reason="device_changed_on_other_side", side=side, key=key, instance=instance))
            if key[0] in ("parameter","variable"):
                refs = {token for v in (row["before"], row["after"]) if v
                        for token in re.findall(r"\b[A-Za-z_]\w*\b", str(v.get("value","") if isinstance(v,dict) else v))}
                for name in sorted(refs & variables):
                    issues.append(dict(reason="shared_variable_reference", variable=name, side=side, key=key))
    return issues


def preview(payload, directory, engine):
    from single_schematic import safe_target, snapshot
    from workspace_activity import edit_guard
    req = MergePlan.model_validate(payload)
    if set(req.selected) - set(SIDES):
        raise ValueError("Select only input or output changes")
    root = Path(directory).resolve()
    base = root/"collaboration"/req.batch
    fork_record = _read(base/"fork.json")
    if file_hash(base/"baseline.json") != fork_record["baseline_sha256"]:
        raise ValueError("Fork baseline fingerprint differs")
    anchor = _read(base/"baseline.json")
    original = elements(anchor["schematic"])
    contract = Contract.model_validate(anchor["contract"])
    options, taken, fingerprints = {}, {}, {}
    with target_lock(root/"single-schematic"):
        state = _read(root/"task.json")
        if state["status"] != "ready" or state["snapshot_sha256"] != anchor["snapshot_sha256"]:
            raise ValueError("Integrator changed since the common baseline; create a new fork")
        snapshot(state)
        for side in SIDES:
            task = base/side
            with target_lock(task/"single-schematic"):
                child = _read(task/"task.json")
                target = safe_target(task, Target.model_validate(child["target"]))
                if child["status"] != "ready" or child["task_directory"] != str(task):
                    raise ValueError(f"Branch {side} has an unresolved operation")
                with edit_guard(target.workspace):
                    current_files = manifest(Path(target.workspace))
                    prefix = f"{target.library}/{target.cell}/schematic/"
                    prior = fork_record["branch_files"][side]
                    foreign = [p for p in sorted(prior.keys() | current_files.keys())
                               if not p.startswith(prefix) and prior.get(p) != current_files.get(p)]
                    if foreign:
                        raise ValueError(f"{side} changed files outside the selected schematic: {foreign}")
                    current = live_snapshot(engine, target, contract)
                    if manifest(Path(target.workspace)) != current_files:
                        raise ValueError(f"{side} changed while reading the saved design")
                    fingerprints[side] = current_files
                groups = change_groups(original, elements(current))
                chosen = req.selected.get(side, list(groups))
                if set(chosen) - groups.keys():
                    raise ValueError(f"Unknown changes in {side}: {set(chosen) - groups.keys()}")
                options[side] = groups
                taken[side] = [row for key in dict.fromkeys(chosen) for row in groups[key]]
        issues = conflicts(taken["input"], taken["output"])
        for side, rows in taken.items():
            for row in rows:
                if row["key"][0] in ("notes", "conditions", "port"):
                    issues.append(dict(reason="outside_electrical_network_merge", side=side, key=row["key"]))
        if issues:
            return dict(ok=False, status="conflict", conflicts=issues, choices=options)
        merged = {tuple(row["key"]): row for rows in taken.values() for row in rows}
        if not merged:
            return dict(ok=True, status="no_changes", choices=options)
        review = dict(kind="schematic_merge", batch=req.batch, target=state["target"], baseline_sha256=fork_record["baseline_sha256"],
                      revision=state["revision"], branch_files=fingerprints, changes=list(merged.values()))
        path = base/"reviews"/(uuid.uuid4().hex+".json")
        atomic_json(path, review)
        request = dict(operation="merge", request_id="merge-"+path.stem, expected_revision=state["revision"],
                       review=str(path), review_sha256=file_hash(path))
        request_path = base/"requests"/(path.stem+".json")
        atomic_json(request_path, request)
        return dict(ok=True, status="review_ready", choices=options, selected_changes=review["changes"],
                    next_call=dict(action="run", task_directory=str(root), request_file=str(request_path)))


def apply_review(req, root, state, engine, folder, trace):
    from single_schematic import save_snapshot
    from schematic_merge_native import apply_changes
    path = Path(req.review).resolve(strict=True)
    if not path.is_relative_to(root/"collaboration") or file_hash(path) != req.review_sha256:
        raise ValueError("Merge review location or fingerprint differs")
    review = _read(path)
    base = path.parent.parent
    if (review["kind"] != "schematic_merge" or review["target"] != state["target"] or
            review["revision"] != state["revision"] or file_hash(base/"baseline.json") != review["baseline_sha256"]):
        raise ValueError("Merge review no longer matches the integrator")
    anchor = _read(base/"baseline.json")
    for side in SIDES:
        if manifest(base/side/"design_wrk") != review["branch_files"][side]:
            raise ValueError(f"Branch {side} changed after review; prepare a new merge")
    target = Target.model_validate(state["target"])
    if manifest(Path(target.workspace)) != anchor["source_files"]:
        raise ValueError("Integrator workspace changed since review")
    saved, netlist = apply_changes(engine, target, Contract.model_validate(state["contract"]),
                                   anchor["schematic"], review["changes"], anchor["source_files"])
    return {**save_snapshot(saved, netlist, target, folder, trace),
            "changes": review["changes"], "review": str(path), "save_reopen_verified": True}
