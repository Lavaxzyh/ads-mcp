"""Build + simulate the 8-cell LC lumped topology library (v0.2 first tier).

Topologies: LPF / HPF / BPF / BSF, each doubly terminated, fc(f0) = 1 GHz,
in two responses: Chebyshev 0.1 dB N=5 and Butterworth N=5 (BPF/BSF use
fractional bandwidth d = 0.4). All cells are built headlessly with the
standard 23-tool primitives (L/C/GROUND via ads_rflib) and simulated with
hpeesofsim; key response metrics are verified per topology.
"""
from __future__ import annotations
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from ads_engine import AdsEngine  # noqa: E402
import lc_synth as S  # noqa: E402

WORKROOT = Path(r"D:\ADS_Project\ads_mcp_demo")
WS = WORKROOT / "lc_filters_wrk"
OUT = WORKROOT / "sim_out_lc"
FC = 1e9
DELTA = 0.4


def fmt_L(v):
    return f"{v * 1e9:.4g} nH"


def fmt_C(v):
    return f"{v * 1e12:.4g} pF"


# ---------- layout builders (one per topology) ----------

def place_bpf(engine: AdsEngine, elements: list[dict]):
    """BPF: series LC arms on the main line; shunt arms are PARALLEL C-branch +
    L-branch (each its own vertical path to ground) hanging off the main line.

    NOTE: the shunt arm must be parallel (open at f0). Stacking C and L in
    series to ground would short the through line at f0 (learned the hard way).
    """
    x = 0.5
    idx = {"L": 0, "C": 0, "G": 0, "T": 0}
    engine.add_wire([[0.0, 0.0], [x, 0.0]])
    shunts = []
    for el in elements:
        if el["kind"] == "series":  # series LC arm spans x..x+2
            idx["L"] += 1
            engine.place_component("ads_rflib", "L", x, 0.0,
                                   instance_name=f"L{idx['L']}", params={"L": fmt_L(el["L"])})
            idx["C"] += 1
            engine.place_component("ads_rflib", "C", x + 1.0, 0.0,
                                   instance_name=f"C{idx['C']}", params={"C": fmt_C(el["C"])})
            x += 2.0
        else:  # shunt parallel pair in the gap x..x+1
            shunts.append((x + 0.25, el["C"], "C"))
            shunts.append((x + 0.75, el["L"], "L"))
            engine.add_wire([[x, 0.0], [x + 1.0, 0.0]])
            x += 1.0
    engine.add_wire([[x, 0.0], [x + 0.5, 0.0]])
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
    return x + 0.5


def place_ladder(engine: AdsEngine, elements: list[dict]):
    """generic ladder: series single/inline-LC on main line, shunt stacks below.

    Layout rules (from the ex_lpf probe): inline components span 1.0 in x and
    are pin-to-pin when adjacent centers are 1.0 apart; vertical shunt stacks
    use 1.0 y-spacing pin-to-pin, tapped with a wire from the main line to the
    first stack center (-1.25)."""
    x = 0.5
    idx = {"L": 0, "C": 0, "G": 0}
    tap_after = []
    for el in elements:
        if el["kind"] == "series":
            if el["type"] == "LCparallel":
                # horizontal L || C between nodes (x,0) and (x+1,0)
                idx["L"] += 1
                engine.place_component("ads_rflib", "L", x, 0.0,
                                       instance_name=f"L{idx['L']}", params={"L": fmt_L(el["L"])})
                idx["C"] += 1
                engine.place_component("ads_rflib", "C", x, -0.6,
                                       instance_name=f"C{idx['C']}", params={"C": fmt_C(el["C"])})
                engine.add_wire([[x, 0.0], [x, -0.6]])
                engine.add_wire([[x + 1.0, -0.6], [x + 1.0, 0.0]])
                x += 1.0
            elif el["type"] == "LCseries":
                # series L + series C inline, pin-to-pin, spans x..x+2
                idx["L"] += 1
                engine.place_component("ads_rflib", "L", x, 0.0,
                                       instance_name=f"L{idx['L']}", params={"L": fmt_L(el["L"])})
                idx["C"] += 1
                engine.place_component("ads_rflib", "C", x + 1.0, 0.0,
                                       instance_name=f"C{idx['C']}", params={"C": fmt_C(el["C"])})
                x += 2.0
            else:
                idx[el["type"]] += 1
                name = f"{el['type']}{idx[el['type']]}"
                val = fmt_L(el["val"]) if el["type"] == "L" else fmt_C(el["val"])
                engine.place_component("ads_rflib", el["type"], x, 0.0,
                                       instance_name=name, params={el["type"]: val})
                x += 1.0
        else:
            tap_after.append((x, el))
    # Term1 lead-in
    engine.add_wire([[0.0, 0.0], [0.5, 0.0]])
    # shunt taps + vertical stacks
    for tx, el in tap_after:
        engine.add_wire([[tx, 0.0], [tx, -1.25]])
        y = -1.25
        if el["type"] in ("C", "L"):  # single shunt element
            idx[el["type"]] += 1
            val = fmt_L(el["val"]) if el["type"] == "L" else fmt_C(el["val"])
            engine.place_component("ads_rflib", el["type"], tx, y, instance_name=f"{el['type']}{idx[el['type']]}",
                                   angle=-90.0, params={el["type"]: val})
            engine.place_component("ads_rflib", "GROUND", tx, y - 1.0,
                                   instance_name=f"GND_{el['type']}_{tx:.2f}", angle=-90.0)
        else:  # LCseries: C then L continuing the stack, GND 1.0 below L
            idx["C"] += 1
            engine.place_component("ads_rflib", "C", tx, y, instance_name=f"C{idx['C']}",
                                   angle=-90.0, params={"C": fmt_C(el["C"])})
            idx["L"] += 1
            engine.place_component("ads_rflib", "L", tx, y - 1.0, instance_name=f"L{idx['L']}",
                                   angle=-90.0, params={"L": fmt_L(el["L"])})
            engine.place_component("ads_rflib", "GROUND", tx, y - 2.0,
                                   instance_name=f"GND_LC_{tx:.2f}", angle=-90.0)
    return x


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
    engine.add_term("Term1", 0.0, 0.0)
    engine.add_term("Term2", end_x, 0.0)
    # main-line wiring: done inside place_ladder via taps; series gaps need wires:
    # (placed components with adjacent centers are pin-to-pin; non-adjacent are
    #  separated by the 1.0 gap we leave at shunt taps -> add connecting wires)
    engine.place_component("ads_simulation", "S_Param", end_x, -2.5, instance_name="SP1",
                           params={"Start": sweep[0], "Stop": sweep[1], "Step": sweep[2]})
    engine.save_schematic()
    return sweep


def main() -> None:
    engine = AdsEngine()
    if WS.exists():
        shutil.rmtree(WS, ignore_errors=True)
    engine.create_workspace(str(WS))
    engine.create_library("lc_lib")

    g_cheb = S.chebyshev_g(5, 0.1)
    g_butt = S.butterworth_g(5)

    # NOTE: main-line series gaps at shunt taps must be wired; build_filter
    # collects tap nodes and wires them inside place_ladder, so series runs
    # are continuous by construction (components every 1.0 at taps).
    results = []
    for resp, g in (("Chebyshev", g_cheb), ("Butterworth", g_butt)):
        for topo in ("LPF", "HPF", "BPF", "BSF"):
            cell = f"{resp[:4].lower()}_{topo.lower()}"
            sweep = build_filter(engine, "lc_lib", cell, topo, g)
            od = OUT / cell
            od.mkdir(parents=True, exist_ok=True)
            nl = engine.generate_netlist()["netlist"]
            res = engine.run_simulation(str(od), nl)
            results.append((f"{resp} {topo}", cell, sweep, res["dataset"]))
            print(f"simulated: {resp} {topo} -> {res['dataset']}", flush=True)

    print("\n=== 指标汇总 ===")
    for label, cell, sweep, ds in results:
        import keysight.ads.dataset as dataset
        df = dataset.open(ds, mode="r")["SP1.SP"].to_dataframe().reset_index()
        f = df["freq"].to_numpy(dtype=float)
        db21 = 20 * np.log10(np.maximum(np.abs(df["S[1,2]"].to_numpy(dtype=complex)), 1e-15))
        db11 = 20 * np.log10(np.maximum(np.abs(df["S[1,1]"].to_numpy(dtype=complex)), 1e-15))

        def at(fq):
            return db21[int(np.argmin(np.abs(f - fq)))]

        if cell.endswith("lpf"):
            m = (f >= 0.1e9) & (f <= 0.9e9)
            extra = f"带内纹波 {db21[m].max()-db21[m].min():.2f} dB"
        elif cell.endswith("hpf"):
            m = (f >= 1.1e9) & (f <= 2.9e9)
            extra = f"通带(1.1-2.9G)最差 {db21[m].min():.2f} dB"
        elif cell.endswith("bpf"):
            m = (f >= 0.85e9) & (f <= 1.15e9)
            extra = f"中心增益 {db21[m].max():.2f} dB @1G | 抑制@0.4G {at(0.4):.1f} / @2G {at(2.0):.1f} dB"
        else:
            notch = db21.min()
            extra = f"陷波深度 {notch:.1f} dB @ {f[db21.argmin()]/1e9:.2f} GHz"
        print(f"{label:22s} | {extra}")


import shutil  # noqa: E402

if __name__ == "__main__":
    main()
