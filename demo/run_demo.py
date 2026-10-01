"""Demo: step-impedance microstrip low-pass filter, built end-to-end headless.

This is the ads-mcp v0.1 showcase and smoke test.  It drives the exact same
ads_engine API the MCP tools expose, then verifies the simulated response:
passband insertion loss near 0 dB at low frequency and clear rejection above
the design cutoff.

Circuit (5-element step-impedance LPF on FR4, fc ~ 1 GHz):

  Term1 -[hi-Z L1]-[lo-Z C1]-[hi-Z L2]-[lo-Z C2]-[hi-Z L3]- Term2
         series inductors = narrow (high-Z) lines
         shunt  capacitors = wide (low-Z) lines

Values are first-cut synthesized (not tuned): Wh=0.2 mm (~90 ohm),
Wl=8 mm (~20 ohm) on Er=4.4 / H=1.6 mm.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ads_engine import AdsEngine  # noqa: E402

WORKROOT = Path(r"D:\ADS_Project\ads_mcp_demo")
WS = WORKROOT / "si_lpf_wrk"
OUT = WORKROOT / "sim_out"


def build(engine: AdsEngine) -> None:
    if WS.exists():
        shutil.rmtree(WS, ignore_errors=True)
    print("[1] workspace:", engine.create_workspace(str(WS))["workspace"])
    print("[2] library:", engine.create_library("si_lpf_lib")["library"])
    print("[3] schematic:", engine.create_schematic("si_lpf_lib", "lpf5")["schematic"])

    # main line: each MLIN spans x..x+1.0 at y=0 (grid 0.25)
    xs = [0.5, 2.0, 3.5, 5.0, 6.5, 8.0]
    hi = {"W": "Wh", "L": "Lh"}   # series inductor ~ narrow high-Z line
    lo = {"W": "Wl", "L": "Ll"}   # shunt capacitor ~ wide low-Z line
    kinds = [hi, lo, hi, lo, hi, lo]
    names = ["TL1", "TL2", "TL3", "TL4", "TL5", "TL6"]
    for x, kind, name in zip(xs, kinds, names):
        r = engine.place_component("ads_tlines", "MLIN", x, 0.0, instance_name=name, params=kind)
        assert r["params_rejected"] == {}, r
    print("[4] placed 6 MLIN segments")

    engine.add_term("Term1", 0.0, 0.0)
    engine.add_term("Term2", 9.5, 0.0)
    print("[5] placed terms")

    # wires between adjacent edges
    edges = [(0.0, 0.5)]
    for i in range(len(xs) - 1):
        edges.append((xs[i] + 1.0, xs[i + 1]))
    edges.append((xs[-1] + 1.0, 9.5))
    for x1, x2 in edges:
        engine.add_wire([[x1, 0.0], [x2, 0.0]])
    print(f"[6] wired {len(edges)} joints")

    engine.place_component("ads_tlines", "MSUB", 5.0, -2.5)  # auto-named MSub1
    for var, val in (("Wh", "0.2 mm"), ("Wl", "8 mm"), ("Lh", "11 mm"), ("Ll", "3 mm")):
        engine.add_var(var, val)
    sp = engine.place_component(
        "ads_simulation", "S_Param", 9.5, -2.5,
        instance_name="SP1",
        params={"Start": "0.05 GHz", "Stop": "6 GHz", "Step": "25 MHz"},
    )
    assert sp["params_rejected"] == {}, sp
    engine.save_schematic()
    print("[7] substrate + vars + S-param controller placed, design saved")


def simulate_and_verify(engine: AdsEngine) -> None:
    if OUT.exists():
        shutil.rmtree(OUT, ignore_errors=True)
    print("[8] netlisting...")
    netlist = engine.generate_netlist()["netlist"]
    assert "MLIN" in netlist and "MSub1" in netlist
    print("[9] simulating (hpeesofsim)...")
    result = engine.run_simulation(str(OUT), netlist)
    print("    dataset:", result["dataset"])

    print("[10] reading results...")
    res = engine.get_results(result["dataset"])
    s11, s21 = res["summary"]["S1,1"], res["summary"]["S1,2"]
    print(f"    S21 passband max: {s21['db_max']} dB at low freq ({s21['db_at_fmin']} dB @ first point)")
    print(f"    S21 at band edge: {s21['db_at_fmax']} dB @ {res['freq_hz'][1]/1e9:.1f} GHz")
    cutoff = s21.get("first_below_-3dB_hz")
    print(f"    S21 first below -3 dB: {cutoff/1e9:.3f} GHz" if cutoff else "    no -3 dB crossing")

    # assertions: passband passes, stopband rejects
    assert s21["db_at_fmin"] > -2.0, f"passband insertion loss too high: {s21['db_at_fmin']} dB"
    assert cutoff is not None and cutoff < 6e9, "expected an LPF rolloff inside the sweep"
    assert s21["db_at_fmax"] < s21["db_at_fmin"] - 10, "expected stopband rejection at sweep end"
    print("[11] response checks passed (LPF behaviour confirmed)")

    s2p = engine.export_touchstone(result["dataset"], str(WORKROOT / "lpf5.s2p"))
    png = engine.plot_sparams(
        result["dataset"], str(WORKROOT / "lpf5_sparams.png"),
        columns=["S[1,1]", "S[1,2]"],
    )
    print("[12] exports:", s2p["touchstone"], "|", png["plot"])


def main() -> None:
    engine = AdsEngine()
    build(engine)
    simulate_and_verify(engine)
    print("\nDEMO OK — full headless loop: schematic -> netlist -> hpeesofsim -> results/exports")


if __name__ == "__main__":
    main()
