"""Deduplicated pre-edit checkpoints and fingerprinted, resumable native file restores."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from pydantic import Field
from schematic_flow import Model, Target, atomic_json, target_lock
from design_snapshots import file_hash, manifest
from workspace_activity import edit_guard

HASH = r"^[0-9a-f]{64}$"


class Checkpoint(Model):
    checkpoint: str = Field(pattern=HASH)


class Restore(Model):
    review: str
    review_sha256: str = Field(pattern=HASH)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def hash_stream(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def store_blob(store, path, digest):
    dest = store/"objects"/digest[:2]/digest
    if dest.is_file() and hash_stream(dest)==digest:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_name(uuid.uuid4().hex + ".tmp")
    try:
        shutil.copyfile(path, temp)
        if hash_stream(temp) != digest:
            raise ValueError(f"File changed while checkpointing: {path}")
        temp.replace(dest)
    finally:
        temp.unlink(missing_ok=True)


def resolve_checkpoint(store, checkpoint):
    chain = []
    while checkpoint:
        if not re.fullmatch(HASH, checkpoint):
            raise ValueError("Invalid checkpoint fingerprint")
        path = store/"records"/(checkpoint + ".json")
        if file_hash(path) != checkpoint:
            raise ValueError("Backup record fingerprint differs")
        entry = read(path)
        chain.append(entry)
        checkpoint = entry["parent"]
    files = {}
    for entry in reversed(chain):
        for name in entry["removed"]:
            del files[name]
        files.update(entry["files"])
    return chain[0], files


def checkpoint(root, state, files):
    """Called while the task and workspace are exclusively owned."""
    store = root/"backups"
    head = store/"head.json"
    prior = read(head)["checkpoint"] if head.exists() else None
    previous = resolve_checkpoint(store, prior)[1] if prior else {}
    ws = Path(state["target"]["workspace"])
    changed = {p: h for p, h in files.items() if previous.get(p) != h}
    for name, sha in changed.items():
        store_blob(store, ws/name, sha)
    record = dict(parent=prior, kind="incremental" if prior else "baseline", target=state["target"],
                  revision=state["revision"], snapshot=state["snapshot"], snapshot_sha256=state["snapshot_sha256"],
                  files=changed, removed=sorted(previous.keys()-files.keys()))
    # Write once. Point head at a complete, content-addressed record.
    raw = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
    key = hashlib.sha256(raw).hexdigest()
    path = store/"records"/(key+".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temp = path.with_suffix(".tmp")
        temp.write_bytes(raw)
        temp.replace(path)
    result = dict(checkpoint=key, kind=record["kind"], files_stored=len(changed), paths_removed=len(record["removed"]),
                  revision=state["revision"], record=str(path))
    atomic_json(head, result)
    return result


def changes(current, wanted):
    return dict(restore=sorted(wanted.keys()-current.keys()), remove=sorted(current.keys()-wanted.keys()),
                overwrite=sorted(p for p in current.keys() & wanted.keys() if current[p] != wanted[p]))


def bound_task(root):
    from single_schematic import safe_target
    state = read(root/"task.json")
    if state["task_directory"] != str(root):
        raise ValueError("Task record does not belong to this directory")
    target = safe_target(root, Target.model_validate(state["target"]))
    if state.get("simulation") and not state["simulation"].get("finalized"):
        raise ValueError("Poll the existing simulation before restoring")
    return state, Path(target.workspace)


def preview(payload, root):
    req = Checkpoint.model_validate(payload)
    with target_lock(root/"single-schematic"):
        state, ws = bound_task(root)
        entry, wanted = resolve_checkpoint(root/"backups", req.checkpoint)
        if entry["target"] != state["target"]:
            raise ValueError("Checkpoint target differs")
        with edit_guard(ws):
            current = manifest(ws, require_workspace=False)
        review = dict(checkpoint=req.checkpoint, revision=state["revision"], target=state["target"],
                      current=current, changes=changes(current, wanted))
        path = root/"backups/reviews"/(uuid.uuid4().hex+".json")
        atomic_json(path, review)
        return dict(ok=True, changes=review["changes"], source_revision=entry["revision"],
                    review=str(path), review_sha256=file_hash(path))


def apply(payload, root, engine):
    from schematic_flow import Contract
    from design_changes import load_snapshot
    from design_snapshots import opened_copy, read_design, structural
    from single_schematic import bind_snapshot, save_snapshot
    from workflow_trace import Trace
    req = Restore.model_validate(payload)
    path = Path(req.review).resolve(strict=True)
    store = root/"backups"
    if path.parent != store/"reviews" or file_hash(path) != req.review_sha256:
        raise ValueError("Restore review location or fingerprint differs")
    review = read(path)
    folder = root/"restores"/path.stem
    receipt = folder/"result.json"
    with target_lock(root/"single-schematic"):
        state, ws = bound_task(root)
        if receipt.is_file():
            return {**read(receipt), "replayed": True}
        if (state.get("last_restore", {}).get("review_sha256") == req.review_sha256 and
                state["revision"] == review["revision"] + 1):
            result = dict(ok=True, revision=state["revision"], changes=review["changes"], native_reopened=True,
                          snapshot=state["snapshot"], snapshot_sha256=state["snapshot_sha256"])
            atomic_json(receipt, result)
            return {**result, "replayed": True}
        if state["target"] != review["target"] or state["revision"] != review["revision"]:
            raise ValueError("Task revision changed since preview")
        entry, wanted = resolve_checkpoint(store, review["checkpoint"])
        accepted = load_snapshot(entry["snapshot"], entry["snapshot_sha256"])
        if accepted["source_files"] != wanted or accepted["source"] != state["target"]:
            raise ValueError("Backup does not match its saved native snapshot")
        before = review["current"]
        with edit_guard(ws):
            current = manifest(ws, require_workspace=False)
            journal = folder/"pending.json"
            if journal.exists():
                if read(journal)["review_sha256"] != req.review_sha256:
                    raise ValueError("A different restore is pending")
                # On interruption only the old or reviewed content is acceptable.
                if any(current.get(p) not in (before.get(p), wanted.get(p)) for p in current.keys() | before.keys() | wanted.keys()):
                    raise ValueError("Workspace changed during interrupted restore")
            elif current != before or state["status"] not in ("ready", "needs_recovery"):
                raise ValueError("Workspace or task changed after preview")
            diff = changes(current, wanted)
            for name in diff["restore"] + diff["overwrite"]:
                sha = wanted[name]
                obj = store/"objects"/sha[:2]/sha
                if hash_stream(obj) != sha:
                    raise ValueError(f"Damaged backup object for {name}")
            for name in diff["restore"] + diff["overwrite"] + diff["remove"]:
                dest = (ws/name).resolve()
                if not dest.is_relative_to(ws) or dest == ws:
                    raise ValueError("File outside bound workspace in restore manifest")
            # Preserve every file this operation can overwrite, including external edits
            # explicitly accepted in the preview. Never discard its pre-restore state.
            if not journal.exists():
                for name in diff["remove"] + diff["overwrite"]:
                    store_blob(store, ws/name, current[name])
                atomic_json(journal, dict(review_sha256=req.review_sha256, pre_restore_manifest=before))
            state.update(status="needs_recovery", pending="restore-"+path.stem)
            atomic_json(root/"task.json", state)
            for name in diff["remove"]:
                (ws/name).unlink()
            for name in diff["restore"] + diff["overwrite"]:
                dest = ws/name
                dest.parent.mkdir(parents=True, exist_ok=True)
                sha = wanted[name]
                # Staging in the operation folder keeps partial files out of the manifest.
                temp = folder/"restore-file.tmp"
                shutil.copyfile(store/"objects"/sha[:2]/sha, temp)
                os.replace(temp, dest)
            if manifest(ws) != wanted:
                raise ValueError("Restored workspace differs from checkpoint")
            target = Target.model_validate(state["target"])
            with opened_copy(engine, target) as design:
                saved, netlist = read_design(design, Contract.model_validate(accepted["contract"]))
            if structural(saved) != structural(accepted):
                raise ValueError("Restored ADS graph differs from checkpoint")
            result = save_snapshot(saved, netlist, target, folder, Trace(root))
            bind_snapshot(state, result)
            state.update(status="ready", pending=None, revision=review["revision"]+1)
            state["last_restore"] = dict(review_sha256=req.review_sha256, checkpoint=review["checkpoint"])
            atomic_json(root/"task.json", state)
            result = dict(ok=True, revision=state["revision"], changes=review["changes"], native_reopened=True, **result)
            atomic_json(receipt, result)
            return result
