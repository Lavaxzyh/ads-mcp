"""Window operations executed inside ADS, never against other applications."""


def window_operation(a):
    import math
    import re
    from PySide6.QtWidgets import QApplication, QMainWindow, QDialog, QLabel
    from PySide6.QtGui import QAction
    from shiboken6 import isValid
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
        def mesh_count():
            return sum(isinstance(obj, db.MomentumMesh) for obj in db.AppObjectIter(d))
        before_count = mesh_count()
        # ADS 2027 uses import/clear actions, not a checkable visibility action.
        # Counting native mesh objects also avoids mistaking menu state for data.
        action_name = "Momentum Mesh" if a["visible"] else "Clear Momentum Mesh"
        if bool(before_count) != a["visible"]:
            if a["visible"]:
                from keysight.ads import de
                from pathlib import Path
                ctx = de.ael.db_find_design_context_from_name(design(w), 0)
                setup = de.ael.deem_find_em_setup_view_name_for_context(ctx)
                lib, cell, view = design(w).split(":")
                folder = Path(de.active_workspace().path) / "simulation" / lib / cell / view / (str(setup) + "_MoM")
                if not setup or not (folder / "proj.msh").is_file():
                    raise ValueError("Native saved Momentum mesh is missing for this layout/setup; run Momentum first")
            actions = [x for x in w.findChildren(QAction) if x.objectName() == action_name]
            if len(actions) != 1 or not actions[0].isEnabled():
                raise RuntimeError(action_name + " action unavailable")
            w.raise_()
            w.activateWindow()
            app.processEvents()
            actions[0].trigger()
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
                "note": "Native display objects only; no design is saved and geometry/sample correspondence needs separate validation"}
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
