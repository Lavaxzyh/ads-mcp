"""Read ADS HB1Tone_LoadPull datasets with Keysight's installed dataset API.

Read-only .ds access. No simulator, network, package installation or GUI automation.
Supports the template's two swept coordinates, not arbitrary tuner topologies.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from keysight.ads import dataset

COORDS = ["imag_indexs11", "real_indexs11"]
HB_COLS = {"V_In", "I_In.i", "Vs_high", "Vs_low", "Is_high.i", "Is_low.i", "Mix[1]"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def select_block(blocks: dict, required: set, label: str):
    matches = [(name, frame) for name, frame in blocks.items()
               if required.issubset(frame.columns)]
    if len(matches) != 1:
        raise ValueError(f"Expected one {label} block; found {len(matches)}. "
                         f"Blocks: {list(blocks)}")
    return matches[0]


def same_coordinates(frame, reference, label):
    if list(frame.index.names) != COORDS or not frame.index.is_unique:
        raise ValueError(f"{label}: unsupported/duplicate coordinate indices")
    if (len(frame) != len(reference) or
            not frame.index.difference(reference.index).empty or
            not reference.index.difference(frame.index).empty):
        raise ValueError(f"{label}: coordinates do not match metadata; refusing positional alignment")
    return frame.reindex(reference.index)


def real_values(series, label):
    values = series.to_numpy(dtype=complex)
    finite = np.isfinite(values)
    if np.any(np.abs(values.imag[finite]) > 1e-9):
        raise ValueError(f"{label}: expected real values")
    return values.real


def extract(path: Path):
    path = path.resolve(strict=True)
    if path.suffix.lower() != ".ds":
        raise ValueError(f"Expected .ds file: {path}")
    before = digest(path)
    with dataset.open(path, mode="r") as source:
        blocks = {name: source[name].to_dataframe() for name in source.varblock_names}
    if digest(path) != before:
        raise ValueError(f"Dataset changed while reading: {path}; finish simulation first")
    hb_name, hb = select_block(blocks, HB_COLS, "HB voltage/current")
    power_name, power = select_block(blocks, {"P_Load.p"}, "load power")
    meta_name, meta = select_block(blocks, {"Z0", "Pavs"}, "Z0/Pavs")
    if list(meta.index.names) != COORDS or not meta.index.is_unique or meta.empty:
        raise ValueError("Only a nonempty two-coordinate load-pull sweep is supported")
    for name, frame in [(hb_name, hb), (power_name, power)]:
        if list(frame.index.names) != COORDS + ["freq"] or not frame.index.is_unique:
            raise ValueError(f"{name}: expected unique (imag_indexs11, real_indexs11, freq)")
    freqs = np.sort(hb.index.get_level_values("freq").unique().to_numpy(dtype=float))
    if not np.all(np.isfinite(freqs)) or freqs[0] != 0 or len(freqs) < 2:
        raise ValueError("Expected DC and positive harmonic frequencies")
    f0 = float(freqs[1])
    orders = freqs / f0
    if f0 <= 0 or not np.allclose(orders, np.arange(len(freqs)), rtol=0, atol=1e-8):
        raise ValueError("Only single-tone, consecutive harmonic frequencies are supported")
    if not np.array_equal(freqs, np.sort(power.index.get_level_values("freq").unique())):
        raise ValueError("Load-power and HB frequency coordinates differ")
    # Check all harmonic grids; do not infer a rectangular grid from scan bounds.
    for freq in freqs:
        same_coordinates(hb.xs(freq, level="freq"), meta, f"HB {freq:g} Hz")
        same_coordinates(power.xs(freq, level="freq"), meta, f"P_Load {freq:g} Hz")
    dc = same_coordinates(hb.xs(0, level="freq"), meta, "DC")
    ac = same_coordinates(hb.xs(f0, level="freq"), meta, "fundamental")
    if not np.allclose(real_values(ac["Mix[1]"], "Mix[1]"), 1):
        raise ValueError("Fundamental Mix[1] is not 1")
    z0 = real_values(meta["Z0"], "Z0")
    if not np.all(np.isfinite(z0) & (z0 > 0)):
        raise ValueError("This exporter requires a finite positive real Z0")
    pavs = real_values(meta["Pavs"], "Pavs")
    gre = meta.index.get_level_values("real_indexs11").to_numpy(dtype=float)
    gim = meta.index.get_level_values("imag_indexs11").to_numpy(dtype=float)
    gamma = gre + 1j * gim
    p_out = real_values(same_coordinates(power.xs(f0, level="freq"), meta, "power")["P_Load.p"], "P_Load.p")
    p_in = 0.5 * np.real(ac["V_In"].to_numpy() * np.conj(ac["I_In.i"].to_numpy()))
    vdd = real_values(dc["Vs_high"], "Vs_high DC")
    vgg = real_values(dc["Vs_low"], "Vs_low DC")
    idc = real_values(dc["Is_high.i"], "Is_high DC")
    igdc = real_values(dc["Is_low.i"], "Is_low DC")
    pdc = vdd * idc + vgg * igdc
    with np.errstate(divide="ignore", invalid="ignore"):
        impedance = z0 * (1 + gamma) / (1 - gamma)
        pout_dbm = np.where(p_out > 0, 10 * np.log10(p_out) + 30, np.nan)
        pae = np.where(pdc > 0, 100 * (p_out - p_in) / pdc, np.nan)
        drain_eff = np.where(vdd * idc > 0, 100 * p_out / (vdd * idc), np.nan)
    out = pd.DataFrame({
        "source_dataset": path.name, "source_path": str(path), "source_sha256": before,
        "point_id": np.arange(1, len(meta) + 1), "freq_Hz": f0, "Pavs_dBm": pavs,
        "Z0_ohm": z0, "Gamma_real": gre, "Gamma_imag": gim,
        "R_ohm": impedance.real, "X_ohm": impedance.imag,
        "VDD_V": vdd, "VGG_V": vgg, "Idc_A": idc, "Igate_dc_A": igdc,
        "Pin_accepted_W": p_in, "Pout_W": p_out, "Pout_dBm": pout_dbm,
        "Pdc_W": pdc, "PAE_pct": pae, "Gt_dB": pout_dbm - pavs,
        "drain_efficiency_pct": drain_eff,
    })
    numeric = out.select_dtypes(include=[np.number])
    finite = np.isfinite(numeric.to_numpy()).all(axis=1)
    issues = [[] for _ in range(len(out))]
    checks = [
        (~finite, "nonfinite_numeric"), (p_out <= 0, "nonpositive_output"),
        (pdc <= 0, "nonpositive_dc_power"), (p_in < -1e-12, "negative_input_power"),
        (np.abs(gamma) >= 1, "nonpassive_gamma"),
        (impedance.real <= 0, "nonpositive_load_resistance"),
        (pae > 100 + 1e-6, "pae_above_100"),
    ]
    for mask, reason in checks:
        for i in np.flatnonzero(mask):
            issues[i].append(reason)
    out["numeric_valid"] = [not row for row in issues]
    out["issue"] = [";".join(row) for row in issues]
    out["hb_convergence_verified"] = False
    valid = out[out["numeric_valid"]]
    peak_cols = ["point_id", "freq_Hz", "Pavs_dBm", "R_ohm", "X_ohm", "Pout_dBm", "PAE_pct"]
    summary = {
        "source_path": str(path), "sha256": before, "points": len(out),
        "numeric_valid_points": len(valid), "freq_Hz": f0,
        "pavs_dBm_values": np.unique(pavs[np.isfinite(pavs)]).tolist(),
        "harmonic_order": int(orders[-1]),
        "blocks": {"hb": hb_name, "power": power_name, "metadata": meta_name},
        "max_pout": None if valid.empty else valid.loc[valid.Pout_dBm.idxmax(), peak_cols].to_dict(),
        "max_pae": None if valid.empty else valid.loc[valid.PAE_pct.idxmax(), peak_cols].to_dict(),
    }
    return out, summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("datasets", nargs="+", type=Path, help="One or more ADS load-pull .ds files")
    parser.add_argument("--out", type=Path, help="New output directory; existing directories are refused")
    args = parser.parse_args(argv)
    sources = [p.resolve(strict=True) for p in args.datasets]
    if len(set(sources)) != len(sources):
        parser.error("Duplicate input dataset paths")
    output = args.out or (Path(__file__).resolve().parents[1] / "data" / "exports" /
                          ("loadpull_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")))
    if output.exists():
        parser.error(f"Output already exists; refusing overwrite: {output}")
    results = [extract(path) for path in sources]
    # No output directory is created until all sources pass structural checks.
    output.mkdir(parents=True, exist_ok=False)
    summaries = []
    for number, (table, summary) in enumerate(results, 1):
        name = f"{number:02d}_{sources[number-1].stem}_points.csv"
        table.to_csv(output / name, index=False, encoding="utf-8-sig", float_format="%.12g")
        summary["csv"] = name
        summaries.append(summary)
        print(f"{sources[number-1].name}: {len(table)} points, "
              f"{summary['numeric_valid_points']} numeric-valid")
    pd.concat([r[0] for r in results], ignore_index=True).to_csv(
        output / "all_points.csv", index=False, encoding="utf-8-sig", float_format="%.12g")
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "exporter": "ADS HB1Tone_LoadPull read-only exporter v1",
        "datasets": summaries,
        "scope": "All stored sweep points; no interpolation, no automatic deduplication or global optimum claim.",
        "assumptions": [
            "real_indexs11+j*imag_indexs11 is the fundamental LoadTuner reflection coefficient.",
            "Z0 is real and positive; Z=Z0*(1+Gamma)/(1-Gamma).",
            "P_Load.p fundamental is net load output power in W.",
            "I_In points into the DUT; Is_high and Is_low point from supply to DUT.",
            "P_in=0.5*real(V_In*conj(I_In.i)); Pdc=VDD*Idc+VGG*Igate_dc.",
            "Reference plane and harmonic/source terminations must be checked in the schematic/netlist.",
        ],
        "limitations": "Numeric validity is not HB convergence/stability/model-safety verification. "
                       "The dataset does not supply a per-point convergence verdict here. "
                       "Unavailable or failed sweep points absent from the input cannot be reconstructed.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Output: {output.resolve()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
