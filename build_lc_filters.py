"""Build + simulate the 8-cell LC lumped topology library (v0.2 first tier).

Topologies: LPF / HPF / BPF / BSF, each doubly terminated, fc(f0) = 1 GHz,
in two responses: Chebyshev 0.1 dB N=5 and Butterworth N=5 (BPF/BSF use
fractional bandwidth d = 0.4). All cells are built headlessly with the
standard 23-tool primitives (L/C/GROUND via ads_rflib) and simulated with
hpeesofsim; key response metrics are verified per topology.

布局规范(v3,防文字重叠,参照 KiCad autoplace_fields):
- 元件本体宽 1.0,串联节距 PITCH=2.5(留 1.5 空隙给标签文字)
- shunt 列放在空隙中点垂直向下;垂直堆叠每层 1.0,GND 在末层中心下 1.0
- Term 距首/末元件本体 >= 1.25(实例标签向右延伸约 1.06 不压本体)
- 构建完成后跑 engine.autolabel() 清残余文字碰撞
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
OUT = WORKROOT / "sim_out_lc"
FC = 1e9
DELTA = 0.4
PITCH = 2.5


def fmt_L(v):
    return f"{v * 1e9:.4g} nH"


def fmt_C(v):
    return f"{v * 1e12:.4g} pF"


def place_ladder(engine: AdsEngine, elements: list[dict]):
    """generic ladder: series single/inline-LC on main line, shunt stacks below."""
    x = 0.5
    idx = {"L": 0, "C": 0}
    tap_after = []
    for el in elements:
        if el["kind"] == "series":
            if el["type"] == "LCparallel":
                # horizontal L || C between nodes (x,0) and (x+1,0)
                idx["L"] += 1
                engine.place_component("ads_rflib", "L", x, 0.0,
                                       instance_name=f"L{idx['L']}", params={"L": fmt_L(el["L"])})
                idx["C"] += 1
                engine.place_component("ads_rflib", "C", x, -0.75,
                                       instance_name=f"C{idx['C']}", params={"C": fmt_C(el["C"])})
                engine.add_wire([[x, 0.0], [x, -0.75]])
                engine.add_wire([[x + 1.0, -0.75], [x + 1.0, 0.0]])
                x += 1.0 + PITCH
                engine.add_wire([[x - PITCH, 0.0], [x, 0.0]])
            elif el["type"] == "LCseries":
                idx["L"] += 1
                engine.place_component("ads_rflib", "L", x, 0.0,
                                       instance_name=f"L{idx['L']}", params={"L": fmt_L(el["L"])})
                idx["C"] += 1
                engine.place_component("ads_rflib", "C", x + 1.0, 0.0,
                                       instance_name=f"C{idx['C']}", params={"C": fmt_C(el["C"])})
                x += 2.0 + PITCH
                engine.add_wire([[x - PITCH, 0.0], [x, 0.0]])
            else:
                idx[el["type"]] += 1
                name = f"{el['type']}{idx[el['type']]}"
                val = fmt_L(el["val"]) if el["type"] == "L" else fmt_C(el["val"])
                engine.place_component("ads_rflib", el["type"], x, 0.0,
                                       instance_name=name, params={el["type"]: val})
                x += 1.0 + PITCH
                engine.add_wire([[x - PITCH, 0.0], [x, 0.0]])
        else:
            tap_after.append((x, el))
    # Term1:信号引脚=(0,0);接地片引脚在 +1.0(悬于 L1 本体上方,无害)。
    # 引入线 0.5 长,绝不越过 +1.0 处的地片引脚。
    engine.add_term("T1", 0.0, 0.0)
    engine.add_wire([[0.0, 0.0], [0.5, 0.0]])
    # shunt 列:空隙中点垂直向下
    for tx, el in tap_after:
        engine.add_wire([[tx, 0.0], [tx, -1.25]])
        y = -1.25
        if el["type"] in ("C", "L"):  # 单元件 shunt
            idx[el["type"]] += 1
            val = fmt_L(el["val"]) if el["type"] == "L" else fmt_C(el["val"])
            engine.place_component("ads_rflib", el["type"], tx, y, instance_name=f"{el['type']}{idx[el['type']]}",
                                   angle=-90.0, params={el["type"]: val})
            engine.place_component("ads_rflib", "GROUND", tx, y - 1.0,
                                   instance_name=f"GND_{el['type']}_{tx:.2f}", angle=-90.0)
        else:  # LCseries: C、L 依次堆叠,GND 在 L 下 1.0
            idx["C"] += 1
            engine.place_component("ads_rflib", "C", tx, y, instance_name=f"C{idx['C']}",
                                   angle=-90.0, params={"C": fmt_C(el["C"])})
            idx["L"] += 1
            engine.place_component("ads_rflib", "L", tx, y - 1.0, instance_name=f"L{idx['L']}",
                                   angle=-90.0, params={"L": fmt_L(el["L"])})
            engine.place_component("ads_rflib", "GROUND", tx, y - 2.0,
                                   instance_name=f"GND_LC_{tx:.2f}", angle=-90.0)
    # Term2 让位(+1.25)
    engine.add_term("T2", x + 1.25, 0.0)
    engine.add_wire([[x, 0.0], [x + 1.25, 0.0]])
    return x + 1.25


def place_bpf(engine: AdsEngine, elements: list[dict]):
    """BPF: 串联 LC 臂在主线上;shunt 并联臂 = C、L 各自独立支路到地。

    注意:shunt 臂必须是并联(在 f0 开路)。若把 C、L 串成一堆接地,
    会在 f0 短路主线,带通变陷波。"""
    x = 0.5
    idx = {"L": 0, "C": 0, "T": 0}
    # Term1:信号引脚=(0,0);接地片引脚在 +1.0(悬于 L1 本体上方,无害)。
    # 引入线 0.5 长,绝不越过 +1.0 处的地片引脚。
    engine.add_term("T1", 0.0, 0.0)
    engine.add_wire([[0.0, 0.0], [0.5, 0.0]])
    shunts = []
    for el in elements:
        if el["kind"] == "series":  # 串联 LC 臂,占 x..x+2
            idx["L"] += 1
            engine.place_component("ads_rflib", "L", x, 0.0,
                                   instance_name=f"L{idx['L']}", params={"L": fmt_L(el["L"])})
            idx["C"] += 1
            engine.place_component("ads_rflib", "C", x + 1.0, 0.0,
                                   instance_name=f"C{idx['C']}", params={"C": fmt_C(el["C"])})
            x += 2.0
        else:  # 并联臂:C、L 两支路放空隙 x..x+1.5,主线桥接
            shunts.append((x + 0.25, el["C"], "C"))
            shunts.append((x + 1.25, el["L"], "L"))
            engine.add_wire([[x, 0.0], [x + 1.5, 0.0]])
            x += 1.5
    # 放置 shunt 并联支路(C、L 各自独立到地)
    for bx, val, kind in shunts:
        idx["T"] += 1
        engine.add_wire([[bx, 0.0], [bx, -1.25]])
        if kind == "C":
            idx["C"] += 1
            engine.place_component("ads_rflib", "C", bx, -1.25, instance_name=f"C{idx['C']}",
                                   angle=-90.0, params={"C": fmt_C(val)})
            engine.place_component("ads_rflib", "GROUND", bx, -2.25,
                                   instance_name=f"GND_C{idx['T']}", angle=-90.0)
        else:
            idx["L"] += 1
            engine.place_component("ads_rflib", "L", bx, -1.25, instance_name=f"L{idx['L']}",
                                   angle=-90.0, params={"L": fmt_L(val)})
            engine.place_component("ads_rflib", "GROUND", bx, -2.25,
                                   instance_name=f"GND_L{idx['T']}", angle=-90.0)
    engine.add_wire([[x, 0.0], [x + 1.0, 0.0]])
    engine.add_term("T2", x + 1.0, 0.0)
    return x + 1.0


def build_filter(engine: AdsEngine, lib: str, cell: str, topo: str, g: list[float]):
    engine.create_schematic(lib, cell)
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
        end_x = place_bpf(engine, elements)
    else:
        end_x = place_ladder(engine, elements)
    engine.place_component("ads_simulation", "S_Param", end_x, -3.0, instance_name="SP1",
                           params={"Start": sweep[0], "Stop": sweep[1], "Step": sweep[2]})
    # KiCad 式文字避让:迭代移动标注直到无 text 级碰撞
    label_fix = engine.autolabel(max_pass=10)
    engine.save_schematic()
    return sweep, label_fix


def metrics(engine: AdsEngine, ds_path: str) -> dict:
    import keysight.ads.dataset as dataset
    df = dataset.open(ds_path, mode="r")["SP1.SP"].to_dataframe().reset_index()
    f = df["freq"].to_numpy(dtype=float)
    db21 = 20 * np.log10(np.maximum(np.abs(df["S[1,2]"].to_numpy(dtype=complex)), 1e-15))
    db11 = 20 * np.log10(np.maximum(np.abs(df["S[1,1]"].to_numpy(dtype=complex)), 1e-15))

    def at(fq):
        return db21[int(np.argmin(np.abs(f - fq)))]

    if "lpf" in ds_path:
        m = (f >= 0.1e9) & (f <= 0.9e9)
        return {"带内纹波": f"{db21[m].max()-db21[m].min():.2f} dB",
                "截止@-3dB": f"{f[np.where(db21 < -3)[0][0]]/1e9:.2f} GHz"}
    if "hpf" in ds_path:
        m = (f >= 1.1e9) & (f <= 2.9e9)
        return {"通带(1.1-2.9G)最差": f"{db21[m].min():.2f} dB",
                "抑制@0.2G": f"{at(0.2):.1f} dB"}
    if "bpf" in ds_path:
        m = (f >= 0.85e9) & (f <= 1.15e9)
        return {"中心增益@1G": f"{db21[m].max():.2f} dB",
                "抑制@0.4G": f"{at(0.4):.1f} dB", "抑制@2G": f"{at(2.0):.1f} dB"}
    i = int(db21.argmin())
    return {"陷波深度": f"{db21.min():.1f} dB @ {f[i]/1e9:.2f} GHz"}


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
            sweep, label_fix = build_filter(engine, "lc_lib", cell, topo, g)
            od = OUT / cell
            od.mkdir(parents=True, exist_ok=True)
            nl = engine.generate_netlist()["netlist"]
            res = engine.run_simulation(str(od), nl)
            m = metrics(engine, res["dataset"])
            results.append((f"{resp} {topo}", m))
            print(f"{resp} {topo:4s} | 标注位移 {len(label_fix['moved'])} 次 | "
                  f"残留碰撞 {label_fix['collision_count']} | "
                  f"{'; '.join(f'{k} {v}' for k, v in m.items())}", flush=True)

    print("\n=== 全部完成 ===")
    for label, m in results:
        print(f"{label:22s}", m)


import shutil  # noqa: E402

if __name__ == "__main__":
    main()
    import os

    # de 引擎的 Qt 线程会阻止进程正常退出(demo 脚本无 mcp_server 的兜底)
    os._exit(0)
