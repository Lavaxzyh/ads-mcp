"""Build + simulate the 8-cell LC lumped topology library (v0.2 first tier).

Topologies: LPF / HPF / BPF / BSF, each doubly terminated, fc(f0) = 1 GHz,
in two responses: Chebyshev 0.1 dB N=5 and Butterworth N=5 (BPF/BSF use
fractional bandwidth d = 0.4). All cells are built headlessly with the
standard tool primitives (L/C/GROUND via ads_rflib) and simulated with
hpeesofsim; key response metrics are verified per topology.

布局引擎(v3,KiCad autoplace 本意——放置时评分选位,而非事后补丁):
- Slot 登记簿:每次放置后实测该实例的 body/annotation 包围盒并登记,
  每段导线登记线段;新元素放置前先用登记簿评分候选槽位,取第一个
  footprint(本体+标注+连线)完全清晰的槽位——对应 KiCad 的
  getPreferredSides + filterCollisions;
- 标注即时避让:放置后若 annotation 与登记簿(含导线)相撞,立即按
  右/左/上/下 选无碰撞方位 move_annotation;
- 硬布线规则(实测教训):Term 必须经 add_term 旋转放置、引入线 0.5 长
  不越地片、桥线端点 = x-PITCH、GND 在堆叠末层下 1.0、同坐标引脚自动合并。
"""
from __future__ import annotations
import math
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ads_engine import AdsEngine  # noqa: E402
import lc_synth as S  # noqa: E402

WORKROOT = Path(r"D:\ADS_Project\ads_mcp_demo")
WS = WORKROOT / "lc_filters_wrk"
if len(sys.argv) > 1:  # 可选:python build_lc_filters.py <workspace-name>
    WS = WORKROOT / sys.argv[1]
OUT = WORKROOT / "sim_out_lc"
FC = 1e9
DELTA = 0.4
PITCH = 3.0


def fmt_L(v):
    return f"{v * 1e9:.4g} nH"


def fmt_C(v):
    return f"{v * 1e12:.4g} pF"


def _inter(a, b):
    """矩形相交面积;> 0.001 即算(与审计口径一致:擦碰即重叠;
    引脚贴合是零面积接触,不会误伤)"""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return (x1 - x0) if (x1 - x0) > 0.001 and (y1 - y0) > 0.001 else 0.0


class Slot:
    """KiCad 式放置登记簿:实例包围盒 + 导线段,支持候选槽位评分"""

    def __init__(self, engine: AdsEngine):
        self.e = engine
        self.boxes: list[tuple[str, tuple, tuple]] = []  # (name, body, annot)
        self.wires: list[tuple] = []                      # (x0,y0,x1,y1)

    # ---- 登记 ----
    def reg_instance(self, name: str) -> tuple:
        inst = self.e._design.find_instance(name)
        b, a = inst.bbox, inst.bbox_annotation_only
        body = (b.lower_left.x, b.lower_left.y, b.upper_right.x, b.upper_right.y)
        ann = (a.lower_left.x, a.lower_left.y, a.upper_right.x, a.upper_right.y)
        self.boxes.append((name, body, ann))
        return body, ann

    def reg_wire(self, pts) -> None:
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            self.wires.append((x0, y0, x1, y1))

    # ---- 放置(带登记) ----
    def place(self, lib, cell, x, y, name=None, angle=0.0, params=None):
        r = self.e.place_component(lib, cell, x, y, instance_name=name,
                                   angle=angle, params=params)
        return self.reg_instance(r["instance"])

    def wire(self, pts):
        self.e.add_wire(pts)
        self.reg_wire(pts)

    # ---- 碰撞查询 ----
    def hits(self, box) -> list[str]:
        out = []
        for name, body, ann in self.boxes:
            if _inter(box, body):
                out.append(f"body:{name}")
            if _inter(box, ann):
                out.append(f"text:{name}")
        for (x0, y0, x1, y1) in self.wires:
            if _inter(box, (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))):
                out.append("wire")
        return out

    def clear(self, box) -> bool:
        return not self.hits(box)

    # ---- 候选槽位搜索:从 base 向两侧以 step 扫,取第一个清晰槽位 ----
    def find_x(self, base_x, box_fn, span=1.5, step=0.25, lo=None, hi=None) -> float:
        for k in range(0, int(span / step) + 1):
            for sx in ((0,) if k == 0 else (k * step, -k * step)):
                x = base_x + sx
                if lo is not None and x < lo - 1e-9:
                    continue
                if hi is not None and x > hi + 1e-9:
                    continue
                if self.clear(box_fn(x)):
                    return x
        return base_x

    # ---- 标注即时避让(放置后调用) ----
    def fix_annotation(self, name: str, step: float = 0.75) -> bool:
        inst = self.e._design.find_instance(name)
        b, a = inst.bbox, inst.bbox_annotation_only
        body = (b.lower_left.x, b.lower_left.y, b.upper_right.x, b.upper_right.y)
        ann = (a.lower_left.x, a.lower_left.y, a.upper_right.x, a.upper_right.y)
        idx = next(i for i, (n, _, _) in enumerate(self.boxes) if n == name)
        others = [bx for n, bx, ax in self.boxes]
        texts = [ax for n2, bx, ax in self.boxes if n2 != name]  # 排除自身原标注(否则位移后被自己挡住)
        wires = [(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                 for (x0, y0, x1, y1) in self.wires]

        def clash(box):
            return any(_inter(box, o) for o in others + texts + wires)

        if not clash(ann):
            return False
        for st in (step, step * 2, step * 3):  # 步长递增:小位移优先
          for dx, dy in ((st, 0), (-st, 0), (0, st), (0, -st),
                         (st, st), (-st, st), (st, -st), (-st, -st)):
            trial = (ann[0] + dx, ann[1] + dy, ann[2] + dx, ann[3] + dy)
            if not clash(trial):
                inst.move_annotation((dx, dy))
                new = inst.bbox_annotation_only
                self.boxes[idx] = (name, body,
                                   (new.lower_left.x, new.lower_left.y,
                                    new.upper_right.x, new.upper_right.y))
                return True
        return False


# ---------------- 拓扑布局 ----------------

def place_series(slot: Slot, kind: str, x: float, val: str, name: str) -> float:
    """串联元件(占 1.0);返回下一游标"""
    if kind == "L":
        slot.place("ads_rflib", "L", x, 0.0, name=name, params={"L": val})
    else:
        slot.place("ads_rflib", "C", x, 0.0, name=name, params={"C": val})
    slot.fix_annotation(name)
    return x + 1.0


def place_shunt_stack(slot: Slot, el: dict, x_hint: float, idx: dict,
                      template: dict | None):
    """shunt 列:在 x_hint 附近评分选槽;template 为首列实测 footprint,
    后续列平移后预扫,避免实测-移动循环。"""
    kind = el["type"]
    if kind == "C":
        stack = [("C", fmt_C(el["val"])), ("GND", None)]
    elif kind == "L":
        stack = [("L", fmt_L(el["val"])), ("GND", None)]
    else:  # LCseries
        stack = [("C", fmt_C(el["C"])), ("L", fmt_L(el["L"])), ("GND", None)]
    tpl = template["boxes"] if template else None

    def col_box(x):
        if tpl is None:  # 首列:无模板,保守估计盒(列 ±0.6,深 3.4)
            return (x - 0.6, -3.4, x + 0.6, 0.15)
        flat = [v for pair in tpl for v in pair]
        xs = [v[0] for v in flat] + [v[2] for v in flat]
        ys = [v[1] for v in flat] + [v[3] for v in flat]
        dx = x - template["x"]
        return (min(xs) + dx, min(ys), max(xs) + dx, max(ys))

    x = slot.find_x(x_hint, col_box, lo=x_hint - 0.1, hi=x_hint + PITCH / 2 - 0.1)
    slot.wire([[x, 0.0], [x, -1.25]])
    boxes = []
    for i, (t, val) in enumerate(stack):
        y = -1.25 - i * 1.0
        if t == "GND":
            idx["G"] += 1
            slot.place("ads_rflib", "GROUND", x, y, name=f"GND_{idx['G']}")
        else:
            idx[t] += 1
            nm = f"{t}{idx[t]}"
            slot.place("ads_rflib", t, x, y, name=nm, angle=-90.0,
                       params={t: val})
            slot.fix_annotation(nm)
        inst = slot.e._design.find_instance(
            f"GND_{idx['G']}" if t == "GND" else f"{t}{idx[t]}")
        b, a = inst.bbox, inst.bbox_annotation_only
        boxes.append(((b.lower_left.x, b.lower_left.y,
                       b.upper_right.x, b.upper_right.y),
                      (a.lower_left.x, a.lower_left.y,
                       a.upper_right.x, a.upper_right.y)))
    if template is None:
        return {"x": x, "boxes": boxes}
    return None


def build_ladder(slot: Slot, elements: list[dict]) -> float:
    """LPF/HPF/BSF 通用梯形(串联 + shunt 列评分选槽)"""
    idx = {"L": 0, "C": 0, "G": 0}
    x = 0.5
    shunt_pending = []
    for el in elements:
        if el["kind"] == "series":
            if el["type"] == "LCparallel":  # BSF 串联臂:L 主线 + C 并联线下
                idx["L"] += 1
                slot.place("ads_rflib", "L", x, 0.0, name=f"L{idx['L']}",
                           params={"L": fmt_L(el["L"])})
                slot.fix_annotation(f"L{idx['L']}")
                idx["C"] += 1
                slot.place("ads_rflib", "C", x, -1.25, name=f"C{idx['C']}",
                           params={"C": fmt_C(el["C"])})
                slot.fix_annotation(f"C{idx['C']}")
                slot.wire([[x, 0.0], [x, -1.25]])
                slot.wire([[x + 1.0, -1.25], [x + 1.0, 0.0]])
                x += 1.0 + PITCH
                slot.wire([[x - PITCH, 0.0], [x, 0.0]])
            else:
                x = place_series(slot, el["type"], x, fmt_L(el["val"])
                                 if el["type"] == "L" else fmt_C(el["val"]),
                                 f"{el['type']}{idx[el['type']] + 1}")
                idx[el["type"]] += 1
                x += PITCH
                slot.wire([[x - PITCH, 0.0], [x, 0.0]])
        else:
            shunt_pending.append((x, el))
    # shunt 列:逐个评分选槽(首列实测 footprint 作模板)
    template = None
    for hx, el in shunt_pending:
        r = place_shunt_stack(slot, el, hx - PITCH / 2, idx, template)
        if template is None:
            template = r
    return x


def build_bpf(slot: Slot, elements: list[dict]) -> float:
    """BPF:串联 LC 臂主线 + 并联臂(C/L 各自独立支路,支路槽位评分)"""
    idx = {"L": 0, "C": 0, "G": 0, "T": 0}
    x = 0.5
    slot.wire([[0.0, 0.0], [x, 0.0]])
    branches = []
    for el in elements:
        if el["kind"] == "series":  # 串联 LC 臂,占 2.0
            idx["L"] += 1
            slot.place("ads_rflib", "L", x, 0.0, name=f"L{idx['L']}",
                       params={"L": fmt_L(el["L"])})
            slot.fix_annotation(f"L{idx['L']}")
            slot.wire([[x + 1.0, 0.0], [x + 1.5, 0.0]])
            idx["C"] += 1
            slot.place("ads_rflib", "C", x + 1.5, 0.0, name=f"C{idx['C']}",
                       params={"C": fmt_C(el["C"])})
            slot.fix_annotation(f"C{idx['C']}")
            x += 2.5
        else:  # 并联臂:空隙 x..x+2.0,C/L 支路各自评分选槽
            branches.append((x, el))
            slot.wire([[x, 0.0], [x + 2.0, 0.0]])
            x += 2.0
    for gx, el in branches:
        for kind, val in (("C", el["C"]), ("L", el["L"])):
            bx = slot.find_x(gx + (0.25 if kind == "C" else 1.75),
                             lambda b, k=kind: (b - 0.5, -3.0, b + 0.5, 0.15),
                             span=0.75, step=0.25, lo=gx + 0.1, hi=gx + 1.9)
            idx["T"] += 1
            slot.wire([[bx, 0.0], [bx, -1.25]])
            idx[kind] += 1
            slot.place("ads_rflib", kind, bx, -1.25, name=f"{kind}{idx[kind]}",
                       angle=-90.0,
                       params={kind: fmt_C(val) if kind == "C" else fmt_L(val)})
            slot.fix_annotation(f"{kind}{idx[kind]}")
            idx["G"] += 1
            slot.place("ads_rflib", "GROUND", bx, -2.25,
                       name=f"GND_{kind}{idx['T']}")
    slot.wire([[x, 0.0], [x + 0.5, 0.0]])
    return x + 0.5


def build_filter(engine: AdsEngine, lib: str, cell: str, topo: str, g: list[float]):
    engine.create_schematic(lib, cell)
    slot = Slot(engine)
    if topo == "LPF":
        elements = S.lp_ladder(g, 2 * math.pi * FC)
        sweep = ("0.05 GHz", "3 GHz", "10 MHz")
    elif topo == "HPF":
        elements = S.hp_ladder(g, 2 * math.pi * FC)
        sweep = ("0.05 GHz", "3 GHz", "10 MHz")
    elif topo == "BPF":
        elements = S.bp_ladder(g, 2 * math.pi * FC, DELTA)
        sweep = ("0.2 GHz", "2 GHz", "5 MHz")
    elif topo == "BSF":
        elements = S.bs_ladder(g, 2 * math.pi * FC, DELTA)
        sweep = ("0.2 GHz", "2 GHz", "5 MHz")
    else:
        raise ValueError(topo)
    if topo == "BPF":
        end_x = build_bpf(slot, elements)
    else:
        end_x = build_ladder(slot, elements)
    place_terms(slot, end_x)
    slot.place("ads_simulation", "S_Param", end_x + 2.0, -3.0, name="SP1",
               params={"Start": sweep[0], "Stop": sweep[1], "Step": sweep[2]})
    slot.fix_annotation("SP1")
    # 最终清扫:登记簿已完整,逐个复查标注 vs 全部盒/导线(KiCad 末轮 sweep)
    for name, _, _ in list(slot.boxes):
        slot.fix_annotation(name)
    engine.save_schematic()
    return sweep


def place_terms(slot: Slot, x_end: float):
    """T1/T2 均经 add_term(旋转+GND);放置后立即避让标注"""
    slot.e.add_term("T1", 0.0, 0.0)
    slot.reg_instance("T1")
    slot.wire([[0.0, 0.0], [0.5, 0.0]])
    slot.fix_annotation("T1")
    slot.e.add_term("T2", x_end, 0.0)
    slot.reg_instance("T2")
    slot.fix_annotation("T2")


def metrics(engine: AdsEngine, ds_path: str) -> dict:
    import keysight.ads.dataset as dataset
    df = dataset.open(ds_path, mode="r")["SP1.SP"].to_dataframe().reset_index()
    f = df["freq"].to_numpy(dtype=float)
    db21 = 20 * np.log10(np.maximum(np.abs(df["S[1,2]"].to_numpy(dtype=complex)), 1e-15))

    def at(fq):
        return db21[int(np.argmin(np.abs(f - fq * 1e9)))]

    if "lpf" in ds_path:
        m = (f >= 0.1e9) & (f <= 0.9e9)
        return {"带内纹波": f"{db21[m].max()-db21[m].min():.2f} dB",
                "截止@-3dB": f"{f[np.where(db21 < -3)[0][0]]/1e9:.2f} GHz"}
    if "hpf" in ds_path:
        m = (f >= 1.1e9) & (f <= 2.9e9)
        return {"通带最差": f"{db21[m].min():.2f} dB", "0.2G抑制": f"{at(0.2):.1f} dB"}
    if "bpf" in ds_path:
        return {"中心@1G": f"{at(1.0):.2f} dB", "0.4G抑制": f"{at(0.4):.1f} dB",
                "2G抑制": f"{at(2.0):.1f} dB"}
    i = int(db21.argmin())
    return {"陷波": f"{db21.min():.1f} dB @ {f[i]/1e9:.2f} GHz"}


def main() -> None:
    engine = AdsEngine()
    if WS.exists():
        shutil.rmtree(WS, ignore_errors=True)
    engine.create_workspace(str(WS))
    engine.create_library("lc_lib")

    g_cheb = S.chebyshev_g(5, 0.1)
    g_butt = S.butterworth_g(5)

    results = []
    for resp, g in (("Chebyshev", g_cheb), ("Butterworth", g_butt)):
        for topo in ("LPF", "HPF", "BPF", "BSF"):
            cell = f"{resp[:4].lower()}_{topo.lower()}"
            sweep = build_filter(engine, "lc_lib", cell, topo, g)
            audit = engine.audit_schematic("lc_lib", cell)
            od = OUT / cell
            od.mkdir(parents=True, exist_ok=True)
            nl = engine.generate_netlist()["netlist"]
            res = engine.run_simulation(str(od), nl)
            m = metrics(engine, res["dataset"])
            results.append((f"{resp} {topo}", m))
            print(f"{resp} {topo:4s} | 碰撞 {audit['collision_count']} 处 | "
                  f"{'; '.join(f'{k} {v}' for k, v in m.items())}", flush=True)

    print("\n=== 全部完成 ===")
    for label, m in results:
        print(f"{label:22s}", m)


import shutil  # noqa: E402

if __name__ == "__main__":
    main()
    import os

    os._exit(0)
