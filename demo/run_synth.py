"""v0.2: Chebyshev-synthesized stepped-impedance LPF — build, simulate, verify.

Values come from demo/chebyshev_synth.py (g-parameter synthesis, 0.1 dB
ripple, N=5, fc=1 GHz, FR4 Er=4.4 H=1.6 mm, Zh=90 / Zl=20 ohm).
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
OUT = WORKROOT / "sim_out_synth"

# synthesized values (see chebyshev_synth.py output)
VARS = {
    "Wh": "0.953 mm",    # high-Z line width  (90 ohm)
    "Wl": "11.118 mm",   # low-Z  line width  (20 ohm)
    "Lh1": "17.35 mm",   # end series sections  (theta 36.5 deg)
    "Lm": "29.89 mm",    # middle series section (theta 62.9 deg, g3)
    "Ll": "13.55 mm",    # shunt sections       (theta 31.4 deg)
}
SEG = [("Wh", "Lh1"), ("Wl", "Ll"), ("Wh", "Lm"), ("Wl", "Ll"), ("Wh", "Lh1")]


def build(engine: AdsEngine) -> None:
    if WS.exists():
        shutil.rmtree(WS, ignore_errors=True)
    print("[1] workspace:", engine.create_workspace(str(WS))["workspace"])
    engine.create_library("si_lpf_lib")
    engine.create_schematic("si_lpf_lib", "lpf5s")
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
    for n, v in VARS.items():
        engine.add_var(n, v)
    engine.place_component("ads_simulation", "S_Param", 8.0, -2.5, instance_name="SP1",
                           params={"Start": "0.05 GHz", "Stop": "4 GHz", "Step": "10 MHz"})
    engine.save_schematic()
    print("[2] 5 synthesized sections + terms + MSub1 + SP1 (0.05-4 GHz @10MHz)")


def report(engine: AdsEngine) -> dict:
    if OUT.exists():
        shutil.rmtree(OUT, ignore_errors=True)
    netlist = engine.generate_netlist()["netlist"]
    print("[3] simulating...")
    result = engine.run_simulation(str(OUT), netlist)
    import keysight.ads.dataset as dataset
    df = dataset.open(result["dataset"], mode="r")["SP1.SP"].to_dataframe()
    f = df.index.to_numpy(dtype=float)
    db21 = 20 * np.log10(np.maximum(np.abs(df["S[1,2]"].to_numpy(dtype=complex)), 1e-15))
    db11 = 20 * np.log10(np.maximum(np.abs(df["S[1,1]"].to_numpy(dtype=complex)), 1e-15))
    below = np.where(db21 < -3)[0]
    cutoff = f[below[0]] if len(below) else None
    m = (f <= 0.9e9)
    print(f"[4] cutoff(-3dB): {cutoff / 1e9:.3f} GHz")
    print(f"    带内(0.05-0.9GHz) S21 最好 {db21[m].max():.2f} / 最差 {db21[m].min():.2f} / 均值 {db21[m].mean():.2f} dB")
    ripples = db21[m]
    print(f"    带内纹波(最大-最小): {ripples.max() - ripples.min():.2f} dB")
    for fq in (0.1, 0.3, 0.5, 0.7, 0.9):
        i = int(np.argmin(np.abs(f - fq * 1e9)))
        print(f"    S21@{fq:.1f} = {db21[i]:.2f} dB | S11@{fq:.1f} = {db11[i]:.2f} dB")
    print(f"    S21@2GHz = {db21[int(np.argmin(np.abs(f-2e9)))]:.2f} dB (阻带)")
    return {"dataset": result["dataset"], "cutoff": cutoff}


if __name__ == "__main__":
    engine = AdsEngine()
    build(engine)
    report(engine)
