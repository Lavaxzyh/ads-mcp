"""Optimization closed loop: Chebyshev-synthesized SI-LPF tuned by ADS's
gradient optimizer (hpVMO) against 3 goals, then before/after comparison.

Variables (5, with opt ranges): Wh, Wl, Lh1, Lm, Ll
Goals:
  1. dB(S(1,2)) >= -1 dB   in 0.1-0.9 GHz  (passband flatness)
  2. dB(S(1,1)) <= -20 dB  in 0.1-0.9 GHz  (input match)
  3. dB(S(1,2)) <= -25 dB  in 1.5-3 GHz    (stopband rejection)
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ads_engine import AdsEngine  # noqa: E402

WORKROOT = Path(r"D:\ADS_Project\ads_mcp_demo")
WS = WORKROOT / "si_lpf_synth_wrk"
OUT = WORKROOT / "sim_out_opt"

NOMINAL = {"Wh": "0.953 mm", "Wl": "11.118 mm", "Lh1": "17.35 mm",
           "Lm": "29.89 mm", "Ll": "13.55 mm"}
RANGES = {
    "Wh": ("0.6 mm", "1.3 mm"),
    "Wl": ("8 mm", "14 mm"),
    "Lh1": ("12 mm", "24 mm"),
    "Lm": ("22 mm", "38 mm"),
    "Ll": ("9 mm", "18 mm"),
}
SEG = [("Wh", "Lh1"), ("Wl", "Ll"), ("Wh", "Lm"), ("Wl", "Ll"), ("Wh", "Lh1")]


def build(engine: AdsEngine) -> str:
    # NOTE: create_schematic on an EXISTING cell returns a blank design
    # (wipes it), so always start from a fresh workspace
    if WS.exists():
        shutil.rmtree(WS, ignore_errors=True)
    engine.create_workspace(str(WS))
    engine.create_library("si_lpf_lib")
    engine.create_schematic("si_lpf_lib", "lpf5o")
    xs = [0.5, 2.0, 3.5, 5.0, 6.5]
    for x, (w, l), name in zip(xs, SEG, ["TL1", "TL2", "TL3", "TL4", "TL5"]):
        engine.place_component("ads_tlines", "MLIN", x, 0.0,
                               instance_name=name, params={"W": w, "L": l})
    engine.add_term("Term1", 0.0, 0.0)
    engine.add_term("Term2", 8.0, 0.0)
    edges = [(0.0, xs[0])] + [(xs[i] + 1.0, xs[i + 1]) for i in range(4)] + [(xs[-1] + 1.0, 8.0)]
    for a, b in edges:
        engine.add_wire([[a, 0.0], [b, 0.0]])
    engine.place_component("ads_tlines", "MSUB", 4.0, -2.5,
                           params={"Er": "4.4", "H": "1.6 mm", "TanD": "0.02"})
    for n, v in NOMINAL.items():
        lo, hi = RANGES[n]
        engine.add_var(n, v, opt_min=lo, opt_max=hi)
    engine.place_component("ads_simulation", "S_Param", 8.0, -2.5, instance_name="SP1",
                           params={"Start": "0.05 GHz", "Stop": "4 GHz", "Step": "10 MHz"})
    engine.add_goal("dB(S(1,2))", "SP1", "min", "-1.0", "0.1 GHz", "0.9 GHz", weight=1)
    engine.add_goal("dB(S(1,1))", "SP1", "max", "-20.0", "0.1 GHz", "0.9 GHz", weight=1)
    engine.add_goal("dB(S(1,2))", "SP1", "max", "-25.0", "1.5 GHz", "3.0 GHz", weight=1)
    engine.add_optim("gradient", 60)
    engine.save_schematic()
    nl = engine.generate_netlist()["netlist"]
    assert "opt{" in nl and "OptimGoal" in nl and "Optim:" in nl, "optim netlist incomplete"
    print("[1] optimization schematic ready (5 opt-vars, 3 goals, gradient/60 iters)")
    return nl


def metrics(engine: AdsEngine, ds_path: str) -> dict:
    import keysight.ads.dataset as dataset
    df = dataset.open(ds_path, mode="r")["SP1.SP"].to_dataframe()
    f = df.index.to_numpy(dtype=float)
    db21 = 20 * np.log10(np.maximum(np.abs(df["S[1,2]"].to_numpy(dtype=complex)), 1e-15))
    db11 = 20 * np.log10(np.maximum(np.abs(df["S[1,1]"].to_numpy(dtype=complex)), 1e-15))
    below = np.where(db21 < -3)[0]
    cutoff = f[below[0]] if len(below) else None
    m = f <= 0.9e9
    stop = (f >= 1.5e9) & (f <= 3e9)
    return {
        "cutoff_ghz": round(cutoff / 1e9, 3) if cutoff else None,
        "s21_worst_inband": round(float(db21[m].min()), 2),
        "s21_mean_inband": round(float(db21[m].mean()), 2),
        "s11_worst_inband": round(float(db11[m].max()), 2),
        "s21_worst_stopband": round(float(db21[stop].min()), 2),
        "s21_max_stopband": round(float(db21[stop].max()), 2),
    }


def main() -> None:
    engine = AdsEngine()
    netlist = build(engine)
    if OUT.exists():
        shutil.rmtree(OUT, ignore_errors=True)
    print("[2] running optimization (gradient, up to 60 iterations)...")
    result = engine.run_simulation(str(OUT), netlist)
    ds = result["dataset"]
    print("[3] dataset:", ds)

    log = Path(OUT) / "lpf5o.log"
    if log.exists():
        tail = log.read_text(errors="ignore")
        import re
        vals = re.findall(r"(Wh|Wl|Lh1|Lm|Ll)\s*=\s*([0-9.]+\s*(?:mm|mil))", tail)
        if vals:
            print("[4] final variable values (from log):", vals[-5:])

    m = metrics(engine, ds)
    print("[5] AFTER optimization:")
    for k, v in m.items():
        print(f"    {k} = {v}")
    print("\n    BEFORE (synthesis nominal): cutoff 1.030 | s21_worst -0.80 | mean -0.22 |"
          " s11_worst -11.88 | stopband max -13.9(约)")
    engine.plot_sparams(ds, str(WORKROOT / "lpf5_optimized.png"), ["S[1,1]", "S[1,2]"])
    print("[6] plot:", WORKROOT / "lpf5_optimized.png")


if __name__ == "__main__":
    main()
