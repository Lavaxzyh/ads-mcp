"""Chebyshev LPF synthesis: g-parameters -> stepped-impedance microstrip (SIF).

Design targets:
  N=5, 0.1 dB ripple, fc = 1 GHz, R0 = 50 ohm, FR4 (Er=4.4, H=1.6 mm)
  Series L  -> high-Z line (Zh)
  Shunt  C  -> low-Z  line (Zl)

SIF formulas (at cutoff, small-angle Tan approximation valid because
theta stays well under 90 deg for practical Zh/Zl):
  theta_Lk = g_k * R0 / Zh      (series inductors)
  theta_Ck = g_k * Zl / R0      (shunt capacitors)
  l_k = theta_k / (2*pi) * lambda_g(fc, that line)

Microstrip synthesis/analysis: Wheeler-style closed forms with bisection.
"""
from __future__ import annotations
import math

# ---------- constants ----------
Er, H, FC, R0 = 4.4, 1.6, 1e9, 50.0
ZH, ZL = 90.0, 20.0
# Chebyshev 0.1 dB, N=5 g-parameters (textbook values)
G = [1.0, 1.1468, 1.3712, 1.9755, 1.3712, 1.1468, 1.0]


# ---------- microstrip analysis (W in mm -> Z0, Eeff) ----------
def microstrip_z0(w_mm: float) -> tuple[float, float]:
    u = w_mm / H
    if u <= 1:
        eeff = (Er + 1) / 2 + (Er - 1) / 2 / math.sqrt(1 + 12 / u + 0.04 * u * u)
        z0 = 60 / math.sqrt(eeff) * math.log(8 / u + 0.25 / u)
    else:
        eeff = (Er + 1) / 2 + (Er - 1) / 2 / math.sqrt(1 + 12 / u)
        z0 = 120 * math.pi / (math.sqrt(eeff) * (u + 1.393 + 0.667 * math.log(u + 1.444)))
    return z0, eeff


def synth_width(z_target: float) -> float:
    """Bisection: find W (mm) whose microstrip Z0 matches z_target."""
    lo, hi = 0.01, 30.0
    for _ in range(80):
        mid = (lo + hi) / 2
        z, _ = microstrip_z0(mid)
        lo, hi = (mid, hi) if z > z_target else (lo, mid)
    return (lo + hi) / 2


# ---------- synthesis ----------
def line_for_z(z_target: float) -> tuple[float, float, float]:
    """Return (W mm, Eeff, lambda_g mm at FC)."""
    w = synth_width(z_target)
    _, eeff = microstrip_z0(w)
    lam_g = 299.792458 / math.sqrt(eeff)  # mm at 1 GHz
    return w, eeff, lam_g


wh, eeff_h, lam_h = line_for_z(ZH)
wl, eeff_l, lam_l = line_for_z(ZL)
print(f"[line synthesis] Zh={ZH} ohm -> W={wh:.3f} mm (Eeff={eeff_h:.3f}, lg={lam_h:.2f} mm)")
print(f"[line synthesis] Zl={ZL} ohm -> W={wl:.3f} mm (Eeff={eeff_l:.3f}, lg={lam_l:.2f} mm)")

# lumped prototype values (for reference)
print("\n[lumped prototype] L1=L5, C2=C4, L3:")
for k, kind in [(1, "L"), (2, "C"), (3, "L"), (4, "C"), (5, "L")]:
    if kind == "L":
        val = G[k] * R0 / (2 * math.pi * FC)
        print(f"  g{G[k]:.4f} -> L = {val*1e9:.3f} nH")
    else:
        val = G[k] / (2 * math.pi * FC * R0)
        print(f"  g{G[k]:.4f} -> C = {val*1e12:.3f} pF")

# SIF line lengths
print("\n[SIF stepped-impedance realization] (L=series/hi-Z, C=shunt/lo-Z):")
sections = []
for k, kind in [(1, "L"), (2, "C"), (3, "L"), (4, "C"), (5, "L")]:
    if kind == "L":
        theta = G[k] * R0 / ZH
        length = theta / (2 * math.pi) * lam_h
        sections.append(("Wh", "Lh", length, math.degrees(theta)))
    else:
        theta = G[k] * Zl if False else G[k] * ZL / R0
        length = theta / (2 * math.pi) * lam_l
        sections.append(("Wl", "Ll", length, math.degrees(theta)))
    print(f"  element {k} ({kind}): theta = {theta:.4f} rad = {math.degrees(theta):.2f} deg"
          f" -> L = {length:.2f} mm")

print("\n[VAR values for the schematic]")
wh_s, wl_s = f"{wh:.3f} mm", f"{wl:.3f} mm"
lh_s = f"{sections[0][2]:.2f} mm"
ll_s = f"{sections[1][2]:.2f} mm"
print(f'Wh="{wh_s}"  Wl="{wl_s}"  Lh="{lh_s}"  Ll="{ll_s}"')
