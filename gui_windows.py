"""Window operations executed inside ADS, never against other applications."""
from native_geometry import layout_geometry_snapshot


def window_operation(a):
    import math
    import re
    try:
        from PySide6.QtWidgets import QApplication, QMainWindow, QDialog, QLabel
        from PySide6.QtGui import QAction
        from shiboken6 import isValid
    except ImportError:
        from PySide2.QtWidgets import QApplication, QMainWindow, QDialog, QLabel, QAction
        from shiboken2 import isValid
    app = QApplication.instance()
    if app is None:
        raise RuntimeError("ADS GUI QApplication not found")
    windows = [w for w in app.topLevelWidgets() if isValid(w) and isinstance(w, QMainWindow)]
    def key(w):
        return str(int(w.winId()))
    def design(w):
        m = re.search(r"\[([^\]]+:[^\]]+:[^\]]+)\]", w.windowTitle())
        return m.group(1) if m else None
    def describe(w):
        g = w.geometry()
        return {"id": key(w), "title": w.windowTitle(), "design": design(w),
                "geometry": [g.x(), g.y(), g.width(), g.height()], "visible": w.isVisible(),
                "maximized": w.isMaximized(), "screen": w.screen().name()}
    screens = [{"name": s.name(), "available_geometry": [s.availableGeometry().x(), s.availableGeometry().y(), s.availableGeometry().width(), s.availableGeometry().height()]} for s in app.screens()]
    if a["operation"] == "list":
        return {"windows": [describe(w) for w in windows], "screens": screens}
    chosen = []
    for target in a["targets"]:
        matches = [w for w in windows if key(w) == target or design(w) == target]
        if len(matches) != 1:
            raise ValueError("Window target must match exactly one window: " + target)
        chosen.append(matches[0])
    if len({key(w) for w in chosen}) != len(chosen):
        raise ValueError("Duplicate window targets")
    before = [describe(w) for w in chosen]
    if a["operation"] == "mesh":
        if len(chosen) != 1 or not (design(chosen[0]) or "").endswith(":layout"):
            raise ValueError("Select one Layout window")
        w = chosen[0]
        from keysight.ads.de import db_uu as db
        d = db.open_design(design(w), db.DesignMode.READ_ONLY)
        modern_mesh = getattr(db, 'MomentumMesh', None)
        tracked = getattr(app, '_ads_mcp_legacy_mesh', {})
        def objects():return list(db.AppObjectIter(d))
        def mesh_count():
            if modern_mesh is not None:
                return sum(isinstance(obj, modern_mesh) for obj in objects())
            current = objects()
            return sum(any(obj == saved for saved in tracked.get(design(w), [])) for obj in current)
        before_count = mesh_count()
        # ADS 2027 uses import/clear actions, not a checkable visibility action.
        # Counting native mesh objects also avoids mistaking menu state for data.
        action_name = "Momentum Mesh" if a["visible"] else "Clear Momentum Mesh"
        proof = None
        if a["visible"]:
            import hashlib
            import json
            from keysight.ads import de
            from pathlib import Path
            ctx = de.ael.db_find_design_context_from_name(design(w), 0)
            setup = de.ael.deem_find_em_setup_view_name_for_context(ctx)
            lib, cell, view = design(w).split(":")
            ws = Path(de.active_workspace().path).resolve()
            folder = ws / "simulation" / lib / cell / view / (str(setup) + "_MoM")
            proof_file = folder / 'ads-mcp-mesh-proof.json'
            if not setup or not (folder / "proj.msh").is_file() or not proof_file.is_file():
                raise ValueError("A completed MCP Momentum solve with mesh/geometry evidence is required for this layout")
            proof = json.loads(proof_file.read_text(encoding='utf-8'))
            for field in ('mesh_file','setup_file'):
                if not Path(proof[field]).resolve().is_relative_to(ws):
                    raise ValueError('Mesh evidence points outside the selected workspace')
            if Path(proof['mesh_file']).resolve() != (folder/'proj.msh').resolve():
                raise ValueError('Mesh evidence belongs to another simulation directory')
            if hashlib.sha256((folder/'proj.msh').read_bytes()).hexdigest()!=proof['mesh_sha256']:
                raise ValueError('Saved mesh has changed since its verified solve')
            if hashlib.sha256(Path(proof['setup_file']).read_bytes()).hexdigest()!=proof['setup_sha256']:
                raise ValueError('EM setup changed since mesh generation')
            geometry = layout_geometry_snapshot({'lib':lib,'cell':cell,'view':view})
            if geometry['sha256']!=proof['geometry']['sha256']:
                raise ValueError('Current native geometry does not match the saved Momentum mesh')
        def trigger(action_name):
            actions = [x for x in w.findChildren(QAction) if x.objectName() == action_name]
            if len(actions) != 1 or not actions[0].isEnabled():
                raise RuntimeError(action_name + " action unavailable")
            w.raise_()
            w.activateWindow()
            app.processEvents()
            actions[0].trigger()
            app.processEvents()
        if modern_mesh is None:
            # ADS 2025 exposes generic AppObjects. Identify only objects
            # introduced by the native Mesh action after its own Clear action;
            # unrelated application objects are neither counted nor removed.
            trigger('Clear Momentum Mesh')
            baseline = objects()
            tracked[design(w)] = []
            if a['visible']:
                trigger('Momentum Mesh')
                tracked[design(w)] = [obj for obj in objects() if not any(obj==old for old in baseline)]
            app._ads_mcp_legacy_mesh = tracked
        elif bool(before_count) != a['visible']:
            trigger(action_name)
        app.processEvents()
        errors = []
        for dialog in app.topLevelWidgets():
            if isValid(dialog) and isinstance(dialog, QDialog) and dialog.isVisible():
                text = " ".join(l.text() for l in dialog.findChildren(QLabel))
                if "momentum mesh" in text.lower():
                    errors.append(text)
        count = mesh_count()
        return {"window": describe(w), "errors": errors, "before_mesh_objects": before_count,
                "visibility_requested": a["visible"], "mesh_objects": count,
                "mesh_loaded_verified": count > 0, "visibility_applied": bool(count) == a["visible"],
                "geometry_correspondence_verified": bool(proof), "source_task_id": proof['task_id'] if proof else None,
                "note": "Native mesh display only; geometry/setup/file hashes are checked before loading; no design is saved"}
    snapshots = getattr(app, "_ads_mcp_window_states", {})
    mode = a["mode"]
    screen = next((s for s in app.screens() if s.name() == a.get("screen")), None) if a.get("screen") else chosen[0].screen()
    if screen is None:
        raise ValueError("Requested screen not found")
    area = screen.availableGeometry()
    cols = a.get("columns") or math.ceil(math.sqrt(len(chosen)))
    rows = math.ceil(len(chosen) / cols)
    for n, w in enumerate(chosen):
        identity = key(w)
        if mode == "restore":
            saved = snapshots.get(identity)
            if saved is None:
                raise ValueError("No saved state for window " + identity)
            w.showNormal()
            w.setGeometry(*saved["geometry"])
            if saved["maximized"]:
                w.showMaximized()
        else:
            snapshots.setdefault(identity, describe(w))
            if mode == "maximize_each":
                w.showMaximized()
            else:
                rects = a.get("rectangles")
                if rects:
                    rect = rects[n]
                else:
                    c, r = n % cols, n // cols
                    x0, x1 = c * area.width() // cols, (c + 1) * area.width() // cols
                    y0, y1 = r * area.height() // rows, (r + 1) * area.height() // rows
                    rect = [area.x() + x0, area.y() + y0, x1 - x0, y1 - y0]
                w.showNormal()
                w.setGeometry(*rect)
        app.processEvents()
    app._ads_mcp_window_states = snapshots
    if a.get("front"):
        front = [w for w in chosen if key(w) == a["front"] or design(w) == a["front"]]
        if len(front) != 1:
            raise ValueError("front must identify one selected window")
        front[0].raise_()
        front[0].activateWindow()
    app.processEvents()
    return {"before": before, "after": [describe(w) for w in chosen], "mode": mode,
            "screens": screens}


window_operation.__ads_mcp_dependencies__ = (layout_geometry_snapshot,)
