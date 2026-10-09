"""LC ladder filter synthesis: Butterworth + Chebyshev, doubly terminated.

Chebyshev g-values come from published prototype tables (Matthaei/Young/Jones;
values below match the standard 0.1/0.5/1.0 dB tables for N=3/5/7). Butterworth
uses the exact closed form g_k = 2 sin((2k-1)pi/(2N)). Even-N Chebyshev tables
are not included (add tables before using even N).

Transformations (Pozar ch.8; d = fractional bandwidth, w0 = center):
  LP->HP: series L -> series C = 1/(g R0 wc); shunt C -> shunt L = R0/(g wc)
  LP->BP: series L -> series LC: L = g R0/(w0 d), C = d/(g R0 w0)
          shunt C -> shunt parallel LC: C = g/(R0 w0 d), L = d R0/(g w0)
  LP->BS: series L -> parallel LC (series arm): L = d g R0/w0, C = 1/(d g R0 w0)
          shunt C -> series LC (shunt arm): C = d g/(R0 w0), L = R0/(d g w0)
"""
from __future__ import annotations
import math

R0 = 50.0

# Chebyshev prototype tables: g1..gN (g0 = gN+1 = 1)
CHEBYSHEV_TABLES: dict[float, dict[int, list[float]]] = {
    0.1: {
        3: [1.0316, 1.1474, 1.0316],
        5: [1.1468, 1.3712, 1.9755, 1.3712, 1.1468],
        7: [1.1812, 1.4228, 2.0967, 1.5734, 2.0967, 1.4228, 1.1812],
    },
    0.5: {
        3: [1.5963, 1.0967, 1.5963],
        5: [1.7058, 1.2296, 2.5408, 1.2296, 1.7058],
    },
    1.0: {
        3: [2.0236, 0.9941, 2.0236],
        5: [2.1349, 1.0911, 3.0009, 1.0911, 2.1349],
    },
}


def butterworth_g(n: int) -> list[float]:
    return [1.0] + [2 * math.sin((2 * k - 1) * math.pi / (2 * n)) for k in range(1, n + 1)] + [1.0]


def chebyshev_g(n: int, ripple_db: float) -> list[float]:
    tab = CHEBYSHEV_TABLES.get(ripple_db, {}).get(n)
    if tab is None:
        raise ValueError(
            f"no Chebyshev table for N={n}, {ripple_db} dB "
            f"(available: N=3/5/7 @ 0.1 dB; N=3/5 @ 0.5/1.0 dB)"
        )
    return [1.0] + list(tab) + [1.0]


# ---------- ladder realizations (values in H / F) ----------

def lp_ladder(g: list[float], wc: float) -> list[dict]:
    out = []
    for k, gk in enumerate(g[1:-1], start=1):
        if k % 2 == 1:
            out.append({"kind": "series", "type": "L", "val": gk * R0 / wc})
        else:
            out.append({"kind": "shunt", "type": "C", "val": gk / (R0 * wc)})
    return out


def hp_ladder(g: list[float], wc: float) -> list[dict]:
    out = []
    for k, gk in enumerate(g[1:-1], start=1):
        if k % 2 == 1:
            out.append({"kind": "series", "type": "C", "val": 1 / (gk * R0 * wc)})
        else:
            out.append({"kind": "shunt", "type": "L", "val": R0 / (gk * wc)})
    return out


def bp_ladder(g: list[float], w0: float, d: float) -> list[dict]:
    out = []
    for k, gk in enumerate(g[1:-1], start=1):
        if k % 2 == 1:
            out.append({"kind": "series", "type": "LCseries",
                        "L": gk * R0 / (w0 * d), "C": d / (gk * R0 * w0)})
        else:
            out.append({"kind": "shunt", "type": "LCshunt",
                        "C": gk / (R0 * w0 * d), "L": d * R0 / (gk * w0)})
    return out


def bs_ladder(g: list[float], w0: float, d: float) -> list[dict]:
    out = []
    for k, gk in enumerate(g[1:-1], start=1):
        if k % 2 == 1:
            out.append({"kind": "series", "type": "LCparallel",
                        "L": d * gk * R0 / w0, "C": 1 / (d * gk * R0 * w0)})
        else:
            out.append({"kind": "shunt", "type": "LCseries",
                        "C": d * gk / (R0 * w0), "L": R0 / (d * gk * w0)})
    return out


if __name__ == "__main__":
    print("chebyshev 0.1dB N=5:", CHEBYSHEV_TABLES[0.1][5])
    print("butterworth N=5    :", [round(v, 4) for v in butterworth_g(5)])
    lp = lp_ladder([1.0] + CHEBYSHEV_TABLES[0.1][5] + [1.0], 2 * math.pi * 1e9)
    print("LPF N=5 1GHz ladder:")
    for el in lp:
        print("  ", el["kind"], el["type"],
              f"{el['val']*1e9:.3f} nH" if el["type"] == "L" else f"{el['val']*1e12:.3f} pF")
