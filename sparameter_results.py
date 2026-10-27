"""Full precision N-port exports and model comparisons."""
from pathlib import Path
import re
import numpy as np


def matrix_from_frame(frame):
    columns = {}
    for col in frame.columns:
        m = re.fullmatch(r"S\[(\d+),(\d+)\]", str(col))
        if m:
            columns[(int(m[1]), int(m[2]))] = col
    if not columns:
        raise ValueError("No S parameter matrix")
    n = max(max(ij) for ij in columns)
    if set(columns) != {(i, j) for i in range(1, n + 1) for j in range(1, n + 1)}:
        raise ValueError("Incomplete S parameter matrix")
    s = np.empty((len(frame), n, n), dtype=complex)
    for (i, j), col in columns.items():
        s[:, i - 1, j - 1] = frame[col].to_numpy(dtype=complex)
    if not np.isfinite(s).all():
        raise ValueError("Non-finite S parameter data")
    return frame.index.to_numpy(dtype=float), s


def export_touchstone(frame, out_path, reference_ohms=50.0):
    f, s = matrix_from_frame(frame)
    if reference_ohms <= 0 or not np.isfinite(reference_ohms):
        raise ValueError("Reference impedance must be positive and finite")
    n = s.shape[1]
    out = Path(out_path).resolve()
    if out.suffix.lower() != f".s{n}p":
        raise ValueError(f"Expected .s{n}p output extension")
    out.parent.mkdir(parents=True, exist_ok=True)
    # Touchstone 1.x: two-port order is 11,21,12,22; N>=3 is row-major.
    order = [(0, 0), (1, 0), (0, 1), (1, 1)] if n == 2 else [(i, j) for i in range(n) for j in range(n)]
    with out.open("w", encoding="ascii") as fh:
        fh.write(f"! ads-mcp N-port export; reference impedance is caller supplied\n# HZ S RI R {reference_ohms:.16g}\n")
        for freq, sample in zip(f, s):
            pairs = [f"{sample[i,j].real:.17g} {sample[i,j].imag:.17g}" for i, j in order]
            # N-port rows are wrapped at each matrix row for N>=3.
            if n >= 3:
                for row in range(n):
                    fh.write((f"{freq:.17g} " if row == 0 else "  ") + " ".join(pairs[row*n:(row+1)*n]) + "\n")
            else:
                fh.write(f"{freq:.17g} " + " ".join(pairs) + "\n")
    return {"touchstone": str(out), "ports": n, "rows": len(f), "reference_ohms": reference_ohms}


def compare_frames(left, right, atol=1e-10):
    f1, s1 = matrix_from_frame(left)
    f2, s2 = matrix_from_frame(right)
    if f1.shape != f2.shape or not np.allclose(f1, f2, rtol=0, atol=1.0) or s1.shape != s2.shape:
        raise ValueError("Frequency grid or port matrix differs; no interpolation was applied")
    error = float(np.max(np.abs(s1 - s2)))
    return {"points": len(f1), "ports": s1.shape[1], "max_complex_error": error,
            "tolerance": atol, "passed": error <= atol, "interpolation_used": False}
