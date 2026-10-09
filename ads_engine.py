"""Headless Keysight ADS DE engine adapter for ads-mcp.

All `keysight.ads.de` access lives here behind one lock (the DE engine is not
thread-safe).  The de package is imported lazily AFTER the environment
(HPEESOF_DIR et al.) is prepared; the native engine is cp314-only, so this
module must run on the ADS-bundled Python.

Verified against ADS 2027 (v6.5.0):
- microstrip family (MLIN/MSUB/MTEE/...) lives in library `ads_tlines`
- S-parameter controller / Term live in `ads_simulation`
- VAR / MeasEqn live in `ads_datacmps`; GROUND lives in `ads_rflib`
- the substrate instance must be named `MSub1` and its `Subst` references are
  left at default (string params lose their netlist quoting if assigned)
- hpeesofsim output dataset appears as `<cell>.ds` in the output directory
"""
from __future__ import annotations

import contextlib
import importlib
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

DEFAULT_HPEESOF_DIR = r"D:\Program Files\Keysight\ADS2027"

#Cells whose parameter names we know; browse_components() merges this with
#on-disk enumeration so the agent always sees authoritative parameter lists
#for the common RF parts.
KNOWN_COMPONENTS: dict[str, dict[str, Any]] = {
    "ads_tlines:MLIN": {"desc": "microstrip line", "params": ["Subst", "W", "L"]},
    "ads_tlines:MSUB": {"desc": "microstrip substrate", "params": ["H", "Er", "TanD", "T", "Cond", "Hu", "Rough"]},
    "ads_tlines:MTEE": {"desc": "microstrip tee", "params": ["Subst", "W1", "W2", "W3", "L1", "L2", "L3"]},
    "ads_tlines:MBEND": {"desc": "microstrip bend", "params": ["Subst", "W", "L1", "L2"]},
    "ads_tlines:MCROS": {"desc": "microstrip cross", "params": ["Subst", "W1", "W2", "W3", "W4", "L1", "L2", "L3", "L4"]},
    "ads_tlines:MGAP": {"desc": "microstrip gap", "params": ["Subst", "W", "S", "L"]},
    "ads_tlines:MCORN": {"desc": "microstrip corner", "params": ["Subst", "W", "L"]},
    "ads_simulation:Term": {"desc": "port terminator", "params": ["Num", "Z"]},
    "ads_simulation:S_Param": {"desc": "S-parameter controller", "params": ["Start", "Stop", "Step"]},
    "ads_datacmps:VAR": {"desc": "variable block (set vars via add_var)", "params": []},
    "ads_datacmps:MeasEqn": {"desc": "measurement equation", "params": ["Eqn"]},
    "ads_rflib:GROUND": {"desc": "ground", "params": []},
    "ads_rflib:R": {"desc": "resistor", "params": ["R"]},
    "ads_rflib:L": {"desc": "inductor", "params": ["L"]},
    "ads_rflib:C": {"desc": "capacitor", "params": ["C"]},
}

_MICROSTRIP_CELLS = {"MSUB"}
_SUBSTRATE_DEFAULT_NAME = "MSub1"
_STR_REFERENCE_PARAMS = {"Subst"}  # string params whose netlist quoting must stay default


def ensure_env() -> str:
    hpeesof_dir = os.environ.get("HPEESOF_DIR") or DEFAULT_HPEESOF_DIR
    os.environ["HPEESOF_DIR"] = hpeesof_dir
    if not Path(hpeesof_dir, "bin", "hpeesofsim.exe").exists():
        raise FileNotFoundError(f"hpeesofsim.exe not found under {hpeesof_dir}")
    return hpeesof_dir


def get_de():
    ensure_env()
    return importlib.import_module("keysight.ads.de")


class AdsEngine:
    """One headless DE engine per server process; every op takes the global lock."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._design = None  # current open db_uu design (schematic)

    @contextlib.contextmanager
    def _op(self):
        """Serialize DE access and keep the de engine's chatter off stdout:
        the MCP stdio transport owns stdout; any stray print from the engine
        would corrupt the JSON-RPC stream."""
        with self._lock, contextlib.redirect_stdout(sys.stderr):
            yield

    # ---- environment / install ----
    def check_installed(self) -> dict[str, Any]:
        hpeesof_dir = ensure_env()
        de = get_de()
        sim_bin = Path(hpeesof_dir, "bin", "hpeesofsim.exe")
        return {
            "ok": True,
            "hpeesof_dir": hpeesof_dir,
            "de_automation_mode": bool(de.running_automation()),
            "simulator": str(sim_bin),
            "bundled_python": str(Path(hpeesof_dir, "tools", "python", "python.exe")),
        }

    # ---- workspace / library ----
    def create_workspace(self, path: str) -> dict[str, Any]:
        with self._op():
            de = get_de()
            wp = Path(path).resolve()
            if de.workspace_is_open():
                de.close_workspace()
            created = True
            if wp.exists():
                # existing workspace: just open it instead of failing
                created = False
            else:
                wp.parent.mkdir(parents=True, exist_ok=True)
                de.create_workspace(str(wp))
            ws = de.open_workspace(str(wp))
            return {
                "workspace": str(wp),
                "created": created,
                "opened": ws is not None,
            }

    def open_workspace(self, path: str) -> dict[str, Any]:
        with self._op():
            de = get_de()
            wp = Path(path).resolve()
            if not wp.exists():
                raise FileNotFoundError(f"workspace not found: {wp}")
            if de.workspace_is_open():
                de.close_workspace()
            ws = de.open_workspace(str(wp))
            return {"workspace": str(wp), "opened": ws is not None}

    def close_workspace(self) -> dict[str, Any]:
        with self._op():
            de = get_de()
            if de.workspace_is_open():
                de.close_workspace()
                self._design = None
                return {"closed": True}
            return {"closed": False, "note": "no workspace was open"}

    # ---- schematic ----
    def create_library(self, name: str) -> dict[str, Any]:
        with self._op():
            de = get_de()
            if not de.workspace_is_open():
                raise RuntimeError("no workspace open; call ads_create_workspace first")
            ws = de.active_workspace()
            lib_dir = Path(ws.path) / name if hasattr(ws, "path") else None
            if lib_dir is None:
                raise RuntimeError("cannot resolve workspace path from active workspace")
            de.create_new_library(name, str(lib_dir))
            ws.add_library(name, str(lib_dir), de.LibraryMode.NON_SHARED)
            return {"library": name, "directory": str(lib_dir)}

    def create_schematic(self, lib: str, cell: str) -> dict[str, Any]:
        with self._op():
            de = get_de()
            self._require_workspace(de)
            design = de.db_uu.create_schematic(f"{lib}:{cell}:schematic")
            if design is None:
                raise RuntimeError(f"could not create schematic {lib}:{cell}:schematic")
            self._design = design
            return {"schematic": f"{lib}:{cell}:schematic"}

    def _current_design(self):
        de = get_de()
        self._require_workspace(de)
        if self._design is None:
            raise RuntimeError("no schematic open; call ads_create_schematic first")
        return self._design

    @staticmethod
    def _require_workspace(de) -> None:
        if not de.workspace_is_open():
            raise RuntimeError("no workspace open; call ads_create_workspace first")

    def place_component(
        self,
        lib: str,
        cell: str,
        x: float,
        y: float,
        instance_name: str | None = None,
        angle: float = 0.0,
        params: dict[str, str] | None = None,
        show_annotations: bool = True,
    ) -> dict[str, Any]:
        with self._op():
            de = get_de()
            design = self._current_design()
            if cell in _MICROSTRIP_CELLS and not instance_name:
                instance_name = _SUBSTRATE_DEFAULT_NAME
            inst = design.add_instance(
                de.LCVName(lib, cell, "symbol"),
                (float(x), float(y)),
                name=instance_name,
                angle=float(angle),
                ads_annot=show_annotations,
            )
            applied: dict[str, str] = {}
            rejected: dict[str, str] = {}
            for pname, value in (params or {}).items():
                if pname in _STR_REFERENCE_PARAMS:
                    rejected[pname] = (
                        "string-reference parameter (e.g. Subst) must keep its default; "
                        "rename the referenced instance instead"
                    )
                    continue
                try:
                    inst.parameters[pname].value = str(value)
                    applied[pname] = str(value)
                except Exception as exc:  # noqa: BLE001
                    rejected[pname] = str(exc)
            return {
                "instance": inst.name,
                "cell": f"{lib}:{cell}",
                "at": [x, y],
                "params_applied": applied,
                "params_rejected": rejected,
            }

    def add_wire(self, points: list[list[float]], label: str | None = None) -> dict[str, Any]:
        with self._op():
            de = get_de()
            design = self._current_design()
            pts = [(float(px), float(py)) for px, py in points]
            wire = design.add_wire(pts)
            if label:
                wire.add_wire_label(label)
            return {"wire": True, "points": pts, "label": label}

    def add_var(self, name: str, value: str, opt_min: str | None = None,
                opt_max: str | None = None) -> dict[str, Any]:
        with self._op():
            de = get_de()
            design = self._current_design()
            var_inst = design.find_instance("VAR1")
            if var_inst is None:
                var_inst = design.add_instance(
                    de.LCVName("ads_datacmps", "VAR", "symbol"),
                    (0.5, -2.5),
                    name="VAR1",
                    angle=0.0,
                )
            stored = str(value)
            if opt_min and opt_max:
                stored = f"{value} opt{{{opt_min} to {opt_max}}}"
            var_inst.vars[name] = stored
            return {"var": name, "value": str(value),
                    "opt": f"{opt_min}..{opt_max}" if opt_min and opt_max else None,
                    "block": var_inst.name}

    def add_goal(self, expr: str, sim_name: str, goal_type: str, bound: str,
                 fmin: str, fmax: str, weight: float = 1.0) -> dict[str, Any]:
        """Add an optimization goal.

        goal_type 'min' means "keep Expr at or above bound" (netlist
        GreaterThan/Min); 'max' means "keep Expr at or below bound" (Max).
        fmin/fmax bracket the evaluation frequency range; values with units
        ("0.9 GHz") are converted to plain Hz numbers because the de layer
        renders repeated-param strings unquoted (spaces break the netlist).
        """

        def to_hz(v: str) -> str:
            s = str(v).strip()
            try:
                hz = float(s)
            except ValueError:
                m = re.match(r"^([0-9.eE+-]+)\s*(GHz|MHz|kHz|Hz)?$", s, re.IGNORECASE)
                if not m:
                    raise ValueError(f"cannot parse frequency {v!r}; use e.g. '0.9 GHz' or '9e8'")
                mult = {"ghz": 1e9, "mhz": 1e6, "khz": 1e3, "hz": 1.0}
                hz = float(m.group(1)) * mult[m.group(2).lower()]
            # repeated params only accept strings, and strings with spaces
            # render unquoted (netlist syntax error) -> plain Hz, no spaces
            return f"{hz:.12g}"

        with self._op():
            de = get_de()
            design = self._current_design()
            existing = [i.name for i in design.instances if i.name.startswith("GOAL")]
            name = f"GOAL{len(existing) + 1}"
            inst = design.add_instance(
                de.LCVName("ads_simulation", "Goal", "symbol"),
                (0.5, -4.5 - 1.0 * len(existing)),
                name=name,
                angle=0.0,
            )
            inst.parameters["Expr"].value = str(expr)
            inst.parameters["SimInstanceName"].value = str(sim_name)
            inst.parameters["Min" if goal_type == "min" else "Max"].value = str(bound)
            inst.parameters["Weight"].value = str(weight)
            # repeated params take LISTS; strings with spaces render unquoted
            # and break the netlist, so frequencies become plain Hz numbers
            inst.parameters["RangeVar"].value = ["freq"]
            inst.parameters["RangeMin"].value = [to_hz(fmin)]
            inst.parameters["RangeMax"].value = [to_hz(fmax)]
            return {"goal": name, "expr": str(expr), "type": goal_type,
                    "bound": str(bound), "range": [str(fmin), str(fmax)], "weight": weight}

    def add_optim(self, optim_type: str = "gradient", max_iters: int = 60) -> dict[str, Any]:
        """Place the optimization controller (auto-collects all opt-vars and goals)."""
        with self._op():
            de = get_de()
            design = self._current_design()
            if design.find_instance("OPT1") is None:
                design.add_instance(
                    de.LCVName("ads_simulation", "Optim", "symbol"),
                    (2.5, -4.5),
                    name="OPT1",
                    angle=0.0,
                )
            opt = design.find_instance("OPT1")
            opt.parameters["OptimType"].value = str(optim_type)
            opt.parameters["MaxIters"].value = str(max_iters)
            return {"optim": "OPT1", "type": str(optim_type), "max_iters": max_iters}

    def audit_schematic(self, lib: str | None = None, cell: str | None = None) -> dict[str, Any]:
        """KiCad-style schematic overlap audit (eeschema autoplace_fields 的思路):

        对原理图每个实例取三种包围盒——本体 (body)、纯文字 (annotation)、
        本体+文字 (full)——两两做内积重叠检测,按严重度分级:
          body-body   元件本体互相重叠(最严重)
          body-text   某元件的文字压到另一元件本体
          text-text   文字互相重叠(较轻,常为标签挤压)
        返回全部碰撞对及重叠矩形,布局修复以清零 body 级为目标。
        若给 lib/cell 则只读打开磁盘上已保存的设计审计;否则审计当前内存设计。
        """
        with self._op():
            de = get_de()
            if lib and cell:
                design = de.db_uu.open_design(f"{lib}:{cell}:schematic")  # READ_ONLY
            else:
                design = self._current_design()
            EPS = 1e-9

            def rect(b):
                ll, ur = b.lower_left, b.upper_right
                return (ll.x, ll.y, ur.x, ur.y)

            # 分级阈值(KiCad 严重度思路):不同碰撞类型不同灵敏度,
            # 避免全局一刀切——本体重叠要显著,文字擦碰即算,深压才算
            SEV_EPS = {"body-body": 0.02, "body-text": 0.005, "text-body": 0.005,
                       "text-text": 0.01, "text-wire": 0.005, "body-wire": 0.05}

            def inter(a, b, eps=EPS):
                x0, y0 = max(a[0], b[0]), max(a[1], b[1])
                x1, y1 = min(a[2], b[2]), min(a[3], b[3])
                if x1 - x0 > eps and y1 - y0 > eps:
                    return (round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3))
                return None

            # 导线(de API:design.shapes -> Line,带 bbox)
            wires = []
            try:
                for sh in design.shapes:
                    bb = sh.bbox
                    if bb is not None:
                        wires.append((bb.lower_left.x, bb.lower_left.y,
                                      bb.upper_right.x, bb.upper_right.y))
            except Exception:
                pass
            items = []
            for inst in design.instances:
                try:
                    body = rect(inst.bbox)
                    ann = rect(inst.bbox_annotation_only)
                except Exception:
                    continue
                pins = []
                try:
                    for ip in inst.inst_pins:
                        sp = ip.snap_point
                        pins.append((sp.x, sp.y))
                except Exception:
                    pass
                items.append({"name": inst.name, "cell": str(getattr(inst, "cell_name", "")),
                              "body": body, "text": ann, "pins": pins})

            # 悬空线端点(ERC 式):端点不触任何引脚、不触其他线段
            dangling = []
            all_pins = [p for it in items for p in it["pins"]]
            def _near_pt(a, b, tol=0.02):
                return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol
            def _pt_seg_dist(p, s):
                x0, y0, x1, y1 = s
                dx, dy = x1 - x0, y1 - y0
                L2 = dx * dx + dy * dy
                if L2 == 0:
                    return ((p[0] - x0) ** 2 + (p[1] - y0) ** 2) ** 0.5
                tt = max(0, min(1, ((p[0] - x0) * dx + (p[1] - y0) * dy) / L2))
                return ((p[0] - x0 - tt * dx) ** 2 + (p[1] - y0 - tt * dy) ** 2) ** 0.5
            for si, s in enumerate(wires):
                for ep in ((s[0], s[1]), (s[2], s[3])):
                    if any(_near_pt(ep, p) for p in all_pins):
                        continue
                    if any(_pt_seg_dist(ep, o) <= 0.02
                           for oj, o in enumerate(wires) if oj != si):
                        continue
                    dangling.append((round(ep[0], 2), round(ep[1], 2)))
            collisions = []
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    A, B = items[i], items[j]
                    sev = []
                    pin_connected = any(
                        abs(pa[0] - pb[0]) <= 0.02 and abs(pa[1] - pb[1]) <= 0.02
                        for pa in A["pins"] for pb in B["pins"])
                    for tag, box in (("body-body", (A["body"], B["body"])),
                                     ("body-text", (A["body"], B["text"])),
                                     ("text-body", (A["text"], B["body"])),
                                     ("text-text", (A["text"], B["text"]))):
                        ov = inter(*box, SEV_EPS.get(tag, EPS))
                        if ov:
                            if tag == "body-body" and pin_connected:
                                continue  # 引脚相接的嵌套(GND 偎在电容底)是 ADS 原生画法
                            sev.append({"kind": tag, "rect": ov})
                    for tag, box in (("text-wire", A["text"]), ("body-wire", A["body"])):
                        for w in wires:
                            ov = inter(box, w, SEV_EPS.get(tag, EPS))
                            if ov:
                                sev.append({"kind": tag, "rect": ov})
                    if sev:
                        collisions.append({"a": A["name"], "b": B["name"], "collisions": sev})
            for dp in dangling:
                collisions.append({"a": f"wire@{dp}", "b": "(悬空)",
                                   "collisions": [{"kind": "dangling-wire",
                                                   "rect": [dp[0], dp[1], dp[0], dp[1]]}]})
            return {"instances": len(items), "collisions": collisions,
                    "collision_count": len(collisions), "dangling_wires": dangling}

    def autolabel(self, max_pass: int = 4, step: float = 0.75) -> dict[str, Any]:
        """KiCad eeschema autoplace_fields 的移植:文字级碰撞自动避让。

        每一轮先审计,再对每个 text 级碰撞把"后放置实例"的标注按四个候选
        方位(右/左/下/上,距离 step)试移——取第一个与对方 body/text 都
        不相交的方位(对应 KiCad 的 chooseSideForFields 严重度过滤)。
        body-body 碰撞不在此处理(属于布局间距问题),只报告。
        """
        with self._op():
            de = get_de()
            design = self._current_design()
            EPS = 1e-9

            def rect(b):
                ll, ur = b.lower_left, b.upper_right
                return (ll.x, ll.y, ur.x, ur.y)

            def inter(a, b):
                x0, y0 = max(a[0], b[0]), max(a[1], b[1])
                x1, y1 = min(a[2], b[2]), min(a[3], b[3])
                return (x1 - x0) > EPS and (y1 - y0) > EPS

            def snapshot():
                items = []
                for inst in design.instances:
                    try:
                        items.append({"name": inst.name,
                                      "body": rect(inst.bbox),
                                      "text": rect(inst.bbox_annotation_only)})
                    except Exception:
                        continue
                return items

            moved = []
            passes = []
            for _ in range(max_pass):
                items = snapshot()
                by_name = {i["name"]: i for i in items}
                collisions = []
                for i in range(len(items)):
                    for j in range(i + 1, len(items)):
                        A, B = items[i], items[j]
                        kinds = []
                        if inter(A["body"], B["body"]):
                            kinds.append("body-body")
                        if inter(A["body"], B["text"]):
                            kinds.append("body-text")
                        if inter(A["text"], B["body"]):
                            kinds.append("text-body")
                        if inter(A["text"], B["text"]):
                            kinds.append("text-text")
                        if kinds:
                            collisions.append({"a": A["name"], "b": B["name"],
                                               "kinds": kinds})
                text_collisions = [c for c in collisions
                                   if any(k != "body-body" for k in c["kinds"])]
                passes.append({"remaining": len(collisions),
                               "text": len(text_collisions),
                               "body": sum(1 for c in collisions
                                           if "body-body" in c["kinds"])})
                if not text_collisions:
                    break
                # fix the first text collision: move b's annotation away from a
                c0 = text_collisions[0]
                inst = design.find_instance(c0["b"])
                if inst is None:
                    break
                A = by_name[c0["a"]]
                B = by_name[c0["b"]]
                others = [i for i in items if i["name"] not in (c0["a"], c0["b"])]
                acx = (A["body"][0] + A["body"][2]) / 2
                acy = (A["body"][1] + A["body"][3]) / 2
                bcx = (B["text"][0] + B["text"][2]) / 2
                bcy = (B["text"][1] + B["text"][3]) / 2
                # 候选方位:远离 a 的方向优先(右/左/上/下)
                sx = 1.0 if bcx >= acx else -1.0
                sy = 1.0 if bcy >= acy else -1.0
                candidates = [(sx * step, 0.0), (-sx * step, 0.0),
                              (0.0, sy * step), (0.0, -sy * step),
                              (sx * step, sy * step)]
                chosen = None
                for off in candidates:
                    trial = (B["text"][0] + off[0], B["text"][1] + off[1],
                             B["text"][2] + off[0], B["text"][3] + off[1])
                    if not inter(trial, A["body"]) and not inter(trial, A["text"]):
                        if not any(inter(trial, o["body"]) or inter(trial, o["text"])
                                   for o in others):
                            chosen = off
                            break
                if chosen is None:
                    chosen = (sx * step, sy * step)  # fallback: move farther away
                try:
                    inst.move_annotation(chosen)
                    moved.append({"inst": c0["b"], "offset": list(chosen),
                                  "vs": c0["a"]})
                except Exception:
                    break
            final = self.audit_schematic()
            return {"moved": moved, "passes": passes,
                    "remaining_collisions": final["collisions"],
                    "collision_count": final["collision_count"]}

    def add_term(self, name: str, x: float, y: float, z: str = "50 Ohm") -> dict[str, Any]:
        with self._op():
            de = get_de()
            design = self._current_design()
            term = design.add_instance(
                de.LCVName("ads_simulation", "Term", "symbol"),
                (float(x), float(y)),
                name=name,
                angle=-90.0,
            )
            term.parameters["Num"].value = re.sub(r"\D", "", name) or "1"
            term.parameters["Z"].value = z
            design.add_instance(
                de.LCVName("ads_rflib", "GROUND", "symbol"),
                (float(x), float(y) - 1.0),
                name=f"GND_{name}",
            )
            return {"term": name, "at": [x, y], "z": z}

    def add_ground(self, x: float, y: float) -> dict[str, Any]:
        with self._op():
            de = get_de()
            design = self._current_design()
            gnd = design.add_instance(
                de.LCVName("ads_rflib", "GROUND", "symbol"),
                (float(x), float(y)),
                angle=-90.0,
            )
            return {"ground": gnd.name, "at": [x, y]}

    def save_schematic(self) -> dict[str, Any]:
        with self._op():
            design = self._current_design()
            design.save_design()
            return {"saved": True}

    def generate_netlist(self) -> dict[str, Any]:
        with self._op():
            design = self._current_design()
            netlist = design.generate_netlist()
            text = netlist if isinstance(netlist, str) else str(netlist)
            return {"netlist": text}

    def list_instances(self) -> dict[str, Any]:
        with self._op():
            design = self._current_design()
            out = []
            for inst in design.instances:
                out.append({
                    "name": inst.name,
                    "is_var": bool(inst.is_var_instance),
                    "cell": str(getattr(inst, "cell_name", "")) or None,
                })
            return {"instances": out}

    @staticmethod
    def _instances(design):
        return list(design.instances)

    # ---- simulation ----
    def run_simulation(self, output_dir: str, netlist: str | None = None) -> dict[str, Any]:
        """Blocking sim; call from a task thread. Returns dataset path + log tail."""
        with self._op():
            if netlist is None:
                design = self._current_design()
                raw = design.generate_netlist()
                netlist = raw if isinstance(raw, str) else str(raw)
            out = Path(output_dir).resolve()
            out.mkdir(parents=True, exist_ok=True)
            from keysight.edatoolbox import ads as eda_ads

            sim = eda_ads.CircuitSimulator()
            sim.run_netlist(netlist, output_dir=str(out))  # raises RuntimeError with hpeesofsim msg
            ds_files = sorted(out.glob("*.ds"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not ds_files:
                raise RuntimeError(f"simulation produced no dataset in {out}")
            return {
                "dataset": str(ds_files[0]),
                "output_dir": str(out),
            }

    # ---- results ----
    @staticmethod
    def _read_dataframe(ds_path: str, varblock: str | None = None):
        import keysight.ads.dataset as dataset

        ds = dataset.open(str(ds_path), mode="r")
        keys = list(ds.keys())
        if varblock is None:
            varblock = keys[0]
        if varblock not in keys:
            raise KeyError(f"varblock {varblock!r} not in dataset keys {keys}")
        return keys, ds[varblock].to_dataframe()

    def get_results(self, ds_path: str, varblock: str | None = None) -> dict[str, Any]:
        with self._op():
            keys, df = self._read_dataframe(ds_path, varblock)
            s_cols = [c for c in df.columns if c.startswith("S[")]
            out: dict[str, Any] = {
                "dataset": str(ds_path),
                "varblocks": keys,
                "rows": int(len(df)),
                "freq_hz": [float(df.index.min()), float(df.index.max())],
                "s_columns": s_cols,
            }
            if s_cols:
                out["summary"] = self._s_summary(df, s_cols)
                out["preview"] = self._s_preview(df, s_cols, max_points=101)
            return out

    @staticmethod
    def _s_summary(df, s_cols) -> dict[str, Any]:
        import numpy as np

        freq = df.index.to_numpy(dtype=float)
        summary: dict[str, Any] = {}
        for col in s_cols:
            s = df[col].to_numpy(dtype=complex)
            db = 20 * np.log10(np.maximum(np.abs(s), 1e-15))
            tag = re.sub(r"[^\d,]", "", col)
            entry = {
                "db_min": round(float(db.min()), 2),
                "db_max": round(float(db.max()), 2),
                "db_at_fmin": round(float(db[0]), 2),
                "db_at_fmax": round(float(db[-1]), 2),
            }
            # first upward crossing below -3 dB (useful LPF cutoff hint)
            below = np.where(db < -3.0)[0]
            if len(below) and below[0] > 0:
                entry["first_below_-3dB_hz"] = float(freq[below[0]])
            summary[f"S{tag}"] = entry
        return summary

    @staticmethod
    def _s_preview(df, s_cols, max_points: int = 101) -> list[dict[str, Any]]:
        import numpy as np

        step = max(1, len(df) // max_points)
        sub = df.iloc[::step]
        rows = []
        for f, r in sub.iterrows():
            row: dict[str, Any] = {"freq_hz": float(f)}
            for col in s_cols:
                v = complex(r[col])
                row[col] = {
                    "db": round(20 * __import__("math").log10(max(abs(v), 1e-15)), 2),
                    "deg": round(__import__("math").degrees(__import__("cmath").phase(v)), 1),
                }
            rows.append(row)
        return rows

    def export_touchstone(self, ds_path: str, out_path: str, ports: int = 2) -> dict[str, Any]:
        import math

        with self._op():
            keys, df = self._read_dataframe(ds_path)
            s_cols = sorted(
                (c for c in df.columns if c.startswith("S[")),
                key=lambda c: tuple(int(x) for x in re.findall(r"\d+", c)),
            )
            out = Path(out_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("w", encoding="ascii") as fh:
                fh.write(f"! Touchstone S2P written by ads-mcp from {Path(ds_path).name}\n")
                fh.write(f"! varblocks: {','.join(keys)}\n")
                fh.write("# HZ S RI R 50\n")
                for f, r in df.iterrows():
                    parts = [f"{float(f):.6e}"]
                    for col in s_cols:
                        v = complex(r[col])
                        parts.append(f"{v.real:.6e} {v.imag:.6e}")
                    fh.write(" ".join(parts) + "\n")
            return {"touchstone": str(out), "rows": int(len(df)), "format": "HZ S RI R 50"}

    def plot_sparams(self, ds_path: str, out_png: str, columns: list[str] | None = None) -> dict[str, Any]:
        import math

        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        with self._op():
            _, df = self._read_dataframe(ds_path)
            s_cols = columns or [c for c in df.columns if c.startswith("S[")]
            freq_ghz = df.index.to_numpy(dtype=float) / 1e9
            fig, ax = plt.subplots(figsize=(9, 5.5), dpi=130)
            for col in s_cols:
                s = df[col].to_numpy(dtype=complex)
                db = 20 * np.log10(np.maximum(np.abs(s), 1e-15))
                ax.plot(freq_ghz, db, linewidth=1.6, label=col)
            ax.set_xlabel("Frequency (GHz)")
            ax.set_ylabel("Magnitude (dB)")
            ax.set_title("S-Parameters")
            ax.set_xlim(left=0)
            ax.grid(True, which="both", alpha=0.35)
            ax.legend(loc="best")
            fig.tight_layout()
            out = Path(out_png)
            out.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(out)
            plt.close(fig)
            return {"plot": str(out)}

    # ---- browse ----
    def browse_components(self, lib: str | None = None) -> dict[str, Any]:
        libs: dict[str, list[str]] = {}
        if lib is None:
            rf_root = Path(os.environ.get("HPEESOF_DIR", DEFAULT_HPEESOF_DIR)) / "oalibs" / "rf"
            if rf_root.exists():
                for entry in sorted(rf_root.iterdir()):
                    if entry.is_dir() and not entry.name.startswith("%"):
                        cells = self._cells_in_oa_lib(entry)
                        if cells:
                            libs[entry.name] = cells
            return {"libraries": libs, "known_components": KNOWN_COMPONENTS}
        known = {
            key: info
            for key, info in KNOWN_COMPONENTS.items()
            if key.split(":")[0] == lib
        }
        lib_dir = Path(os.environ.get("HPEESOF_DIR", DEFAULT_HPEESOF_DIR)) / "oalibs" / "rf" / lib
        cells = self._cells_in_oa_lib(lib_dir) if lib_dir.exists() else []
        return {"library": lib, "cells": cells, "known_components": known}

    @staticmethod
    def _cells_in_oa_lib(lib_dir: Path) -> list[str]:
        def decode(name: str) -> str:
            return re.sub(r"%([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), name)

        return sorted(decode(e.name) for e in lib_dir.iterdir() if e.is_dir())

    # ---- GUI ----
    def open_in_gui(self, workspace_path: str) -> dict[str, Any]:
        hpeesof_dir = ensure_env()
        exe = Path(hpeesof_dir, "bin", "hpeesofde.exe")
        if not exe.exists():
            raise FileNotFoundError(exe)
        flags = 0
        if os.name == "nt":
            # break away from the MCP process tree: a client-side tree kill
            # must never take the user's ADS GUI with it
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            [str(exe), "-w", str(Path(workspace_path).resolve())],
            cwd=str(exe.parent),
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"gui_pid": proc.pid, "workspace": str(workspace_path)}
