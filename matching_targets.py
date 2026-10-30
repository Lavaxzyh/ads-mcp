"""Frequency-specific targets and provenance-preserving load-pull regions.

Regions are unions of clipped local triangles, never a convex hull of good
points. Interpolation is explicit in R/X and in exported dBm/percent/dB values.
No inference is made between different load-pull frequencies.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from rf_workflows import LoadPullLimits, fresh_output, sha256, write_json

LIMIT_COLUMNS = {"min_pout_dbm": "Pout_dBm", "min_pae_pct": "PAE_pct", "min_gt_db": "Gt_dB"}
FREQUENCY_ATOL = 1.0
GEOMETRY_ATOL = 1e-9


def clip_minimum(vertices, column, minimum):
    """Clip a convex polygon carrying affine scalar fields against one limit."""
    result = []
    for a, b in zip(vertices, np.roll(vertices, -1, axis=0)):
        ia, ib = a[column] >= minimum, b[column] >= minimum
        if ia:
            result.append(a)
        if ia != ib:
            result.append(a + (minimum - a[column]) / (b[column] - a[column]) * (b - a))
    return np.asarray(result).reshape(-1, vertices.shape[1])


def polygon_area(vertices):
    return float(abs(np.sum(vertices[:, 0] * np.roll(vertices[:, 1], -1) - vertices[:, 1] * np.roll(vertices[:, 0], -1))) / 2)


def build_regions(analysis_manifest, limits, output_dir="", mode="samples", max_edge_ohm=None,
                  reference_plane="", conditions=""):
    from scipy.spatial import Delaunay, cKDTree, QhullError
    limits = LoadPullLimits.model_validate(limits)
    if not any(v is not None for v in limits.model_dump().values()):
        raise ValueError("At least one explicit Pout/PAE/Gt threshold is required")
    if mode not in {"samples", "triangulated"}:
        raise ValueError("mode must be samples or triangulated")
    if max_edge_ohm is not None and (not np.isfinite(max_edge_ohm) or max_edge_ohm <= 0):
        raise ValueError("max_edge_ohm must be positive and finite")
    manifest_path = Path(analysis_manifest).expanduser().resolve(strict=True)
    manifest_hash = sha256(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    csv_path = Path(manifest["all_points_csv"]).resolve(strict=True)
    if sha256(csv_path) != manifest.get("all_points_sha256"):
        raise ValueError("CSV integrity check failed; rerun ads_analyze_loadpull with this version to obtain a hashed manifest")
    frame = pd.read_csv(csv_path)
    if len(frame) > 200000:
        raise ValueError("Region construction is capped at 200000 points")
    plane = reference_plane.strip() or manifest.get("reference_plane", "").strip()
    contract = conditions.strip() or manifest.get("conditions", "").strip()
    if not plane or not contract:
        raise ValueError("Specify reference_plane and model/source/harmonic conditions")
    for col in ("Pavs_dBm", "VDD_V", "VGG_V", "Z0_ohm"):
        vals = frame[col].to_numpy(float)
        if not len(vals) or not np.isfinite(vals).all() or not np.allclose(vals, vals[0], rtol=1e-9, atol=1e-9):
            raise ValueError(f"Different or invalid {col}; build separate region sets")
    if len({d["harmonic_order"] for d in manifest["datasets"]}) != 1:
        raise ValueError("Different harmonic orders; build separate region sets")
    if not frame.numeric_valid.isin([True, False]).all():
        raise ValueError("numeric_valid must contain booleans")
    if not np.isfinite(frame.freq_Hz).all() or (frame.freq_Hz <= 0).any():
        raise ValueError("Invalid load-pull frequencies")
    frequency_regions = []
    for frequency, group in frame.groupby("freq_Hz", sort=True):
        if group.duplicated(["R_ohm", "X_ohm"]).any():
            raise ValueError("Repeated impedance at one frequency is ambiguous; choose one consistent dataset")
        columns = ["R_ohm", "X_ohm", *LIMIT_COLUMNS.values()]
        values = group[columns].to_numpy(float)
        finite_xy = np.isfinite(values[:, :2]).all(axis=1)
        valid = group.numeric_valid.to_numpy(bool) & np.isfinite(values).all(axis=1) & (values[:, 0] > 0)
        qualifies = valid.copy()
        for key, col in LIMIT_COLUMNS.items():
            threshold = getattr(limits, key)
            if threshold is not None:
                qualifies &= values[:, columns.index(col)] >= threshold
        region = dict(freq_hz=float(frequency), sample_points=values[qualifies, :2].tolist(), polygons=[],
                      stored_points=len(group), qualified_samples=int(qualifies.sum()),
                      rejected_invalid_triangles=0, rejected_long_triangles=0, max_edge_ohm=None)
        if mode == "triangulated":
            xy = values[finite_xy, :2]
            v, ok = values[finite_xy], valid[finite_xy]
            if len(xy) < 3 or len(xy) > 50000:
                raise ValueError("Triangulation needs 3..50000 finite coordinates per frequency")
            try:
                triangles = Delaunay(xy).simplices
            except QhullError as exc:
                raise ValueError("Load-pull coordinates must span a 2D region") from exc
            edge_limit = max_edge_ohm or 2.5 * float(np.median(cKDTree(xy).query(xy, k=2)[0][:, 1]))
            region["max_edge_ohm"] = edge_limit
            region["edge_limit_source"] = "caller" if max_edge_ohm is not None else "2.5 times median nearest-neighbor spacing"
            for tri in triangles:
                vertices = v[tri].copy()
                if not ok[tri].all():
                    region["rejected_invalid_triangles"] += 1
                    continue
                if np.linalg.norm(vertices[:, :2] - np.roll(vertices[:, :2], -1, axis=0), axis=1).max() > edge_limit:
                    region["rejected_long_triangles"] += 1
                    continue
                for key, col in LIMIT_COLUMNS.items():
                    threshold = getattr(limits, key)
                    if threshold is not None and len(vertices):
                        vertices = clip_minimum(vertices, columns.index(col), threshold)
                if len(vertices) >= 3 and polygon_area(vertices[:, :2]) > 1e-16:
                    region["polygons"].append(vertices[:, :2].tolist())
        frequency_regions.append(region)
    if sha256(manifest_path) != manifest_hash or sha256(csv_path) != manifest["all_points_sha256"]:
        raise ValueError("Analysis inputs changed during region construction")
    out = fresh_output(output_dir, "loadpull_regions", [manifest_path, csv_path, *[d["source_path"] for d in manifest["datasets"]]])
    out.mkdir(parents=True, exist_ok=False)
    result = dict(schema="ads-mcp-loadpull-regions-v1", mode=mode, reference_plane=plane, conditions=contract,
                  conditions_source="caller; not independently verified", limits=limits.model_dump(),
                  source_manifest=str(manifest_path), source_manifest_sha256=manifest_hash,
                  source_csv=str(csv_path), source_csv_sha256=manifest["all_points_sha256"],
                  frequency_regions=frequency_regions, frequency_interpolation="none", hb_convergence_verified=False,
                  interpolation=("none" if mode == "samples" else "piecewise linear in R/X; Pout dBm, PAE percent, Gt dB"),
                  scope="Stored HB results are not measurements or proof of HB convergence. Interpolated polygons are estimates; excluded triangles and unsampled gaps are not verified feasible.")
    write_json(out / "regions.json", result)
    return dict(region_file=str(out / "regions.json"), region_sha256=sha256(out / "regions.json"), mode=mode,
                frequencies=[{k: v for k, v in r.items() if k not in {"sample_points", "polygons"}} | {"polygons": len(r["polygons"])} for r in frequency_regions],
                reference_plane=plane, hb_convergence_verified=False)


def load_regions(path, spec):
    source = Path(path).expanduser().resolve(strict=True)
    if source.stat().st_size > 40_000_000:
        raise ValueError("Region artifact exceeds 40 MB")
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    if data.get("schema") != "ads-mcp-loadpull-regions-v1" or data.get("mode") not in {"samples", "triangulated"}:
        raise ValueError("Unsupported region artifact")
    if data["reference_plane"].strip() != spec.reference_plane.strip():
        raise ValueError("Region and matching reference_plane must agree exactly; no automatic de-embedding")
    regions = data["frequency_regions"]
    fs = np.asarray([r["freq_hz"] for r in regions], float)
    if not len(fs) or not np.isfinite(fs).all() or (fs <= 0).any() or (np.diff(fs) <= 2).any():
        raise ValueError("Region frequencies must be finite, ordered and distinct")
    for r in regions:
        arrays = [(r["sample_points"], False)] + [(p, True) for p in r["polygons"]]
        if data["mode"] == "samples" and r["polygons"]:
            raise ValueError("Sample-only artifact cannot contain polygons")
        for v, polygon in arrays:
            a = np.asarray(v, float)
            if not len(a) and not polygon:
                continue
            if a.ndim != 2 or a.shape[1] != 2 or not np.isfinite(a).all() or (a[:, 0] <= 0).any():
                raise ValueError("Region coordinates must be finite R+jX with R>0")
            if polygon:
                edges = np.roll(a, -1, axis=0) - a
                cross = edges[:, 0] * np.roll(edges[:, 1], -1) - edges[:, 1] * np.roll(edges[:, 0], -1)
                if len(a) < 3 or polygon_area(a) <= 1e-16 or (np.any(cross > 1e-10) and np.any(cross < -1e-10)):
                    raise ValueError("Region polygons must be nondegenerate and convex")
    return data


def freeze_evaluation(spec):
    """Validate targets before dispatch; later workers use this immutable snapshot."""
    if spec.region_file:
        source = Path(spec.region_file).expanduser().resolve(strict=True)
        before = sha256(source)
        data = load_regions(source, spec)
        selected = [r for r in data["frequency_regions"] if spec.start_hz - 1 <= r["freq_hz"] <= spec.stop_hz + 1]
        if not selected or any(not r["sample_points"] and not r["polygons"] for r in selected):
            raise ValueError("No feasible region at one or more requested frequencies")
        if sha256(source) != before:
            raise ValueError("Region artifact changed while reading")
        return dict(region_snapshot=data, region_source=str(source), region_source_sha256=before)
    if spec.target_table and spec.target_interpolation == "exact" and not any(spec.start_hz - 1 <= p.freq_hz <= spec.stop_hz + 1 for p in spec.target_table):
        raise ValueError("No target_table frequencies in the evaluation band")
    return {}


def runtime_evaluation(plan, out):
    spec = dict(plan["evaluation"])
    if "region_snapshot" in plan:
        write_json(out / "region_snapshot.json", plan["region_snapshot"])
        spec["region_file"] = str(out / "region_snapshot.json")
    return spec


def exact_index(frequencies, target):
    hits = np.flatnonzero(np.abs(frequencies - target) <= FREQUENCY_ATOL)
    if len(hits) != 1:
        raise ValueError(f"Expected exactly one SP sample at {target:g} Hz (tolerance 1 Hz); no frequency interpolation")
    return int(hits[0])


def region_distance(point, region):
    p = np.asarray(point, float)
    samples = np.asarray(region["sample_points"], float).reshape(-1, 2)
    sample_distance = float(np.linalg.norm(samples - p, axis=1).min()) if len(samples) else float("inf")
    if sample_distance <= GEOMETRY_ATOL:
        return 0.0, "stored_sample", sample_distance
    best = sample_distance
    for vertices in region["polygons"]:
        a = np.asarray(vertices)
        b = np.roll(a, -1, axis=0)
        edge, rel = b - a, p - a
        lengths2 = (edge ** 2).sum(axis=1)
        cross = edge[:, 0] * rel[:, 1] - edge[:, 1] * rel[:, 0]
        tol = GEOMETRY_ATOL * np.sqrt(lengths2)
        if (cross >= -tol).all() or (cross <= tol).all():
            return 0.0, "interpolated_region", sample_distance
        t = np.divide((rel * edge).sum(axis=1), lengths2, out=np.zeros(len(a)), where=lengths2 > 0).clip(0, 1)
        best = min(best, float(np.linalg.norm(p - (a + t[:, None] * edge), axis=1).min()))
    if not np.isfinite(best):
        raise ValueError("No feasible samples or polygons at this frequency")
    return best, "outside_region", sample_distance


def score_targets(f, zin, band, spec):
    error = np.full(len(f), np.nan)
    target_r, target_x, sample_distance = (np.full(len(f), np.nan) for _ in range(3))
    evidence = np.full(len(f), "unscored", dtype=object)
    scored = band.copy()
    coverage = "all_SP_samples_in_band"
    if spec.region_file:
        data = load_regions(spec.region_file, spec)
        scored[:] = False
        coverage = "loadpull_frequencies_only; no claims between frequencies"
        for r in data["frequency_regions"]:
            if spec.start_hz - 1 <= r["freq_hz"] <= spec.stop_hz + 1:
                i = exact_index(f, r["freq_hz"])
                scored[i] = True
                error[i], evidence[i], sample_distance[i] = region_distance([zin[i].real, zin[i].imag], r)
    elif spec.target_table:
        tf = np.asarray([p.freq_hz for p in spec.target_table])
        tr = np.asarray([p.r_ohm for p in spec.target_table])
        tx = np.asarray([p.x_ohm for p in spec.target_table])
        if spec.target_interpolation == "linear":
            # Clamp only the 1 Hz grid-matching tolerance; never extrapolate a target.
            query = f[band].clip(tf[0], tf[-1])
            target_r[band], target_x[band] = np.interp(query, tf, tr), np.interp(query, tf, tx)
            evidence[band] = "interpolated_target"
            for freq in tf:
                evidence[band & (np.abs(f - freq) <= 1)] = "supplied_target"
        else:
            scored[:] = False
            coverage = "target_table_frequencies_only; no claims between frequencies"
            for freq, r, x in zip(tf, tr, tx):
                if spec.start_hz - 1 <= freq <= spec.stop_hz + 1:
                    i = exact_index(f, freq)
                    scored[i], target_r[i], target_x[i], evidence[i] = True, r, x, "supplied_target"
        error[scored] = abs(zin[scored] - (target_r[scored] + 1j * target_x[scored]))
    else:
        target_r[band], target_x[band] = spec.target_r_ohm, spec.target_x_ohm
        error[band] = abs(zin[band] - complex(spec.target_r_ohm, spec.target_x_ohm))
        evidence[band] = "constant_target"
    if not scored.any() or not np.isfinite(error[scored]).all():
        raise ValueError("No finite scored target frequencies")
    extra = dict(target_r_ohm=target_r, target_x_ohm=target_x, target_evidence=evidence,
                 nearest_stored_sample_ohm=sample_distance, is_scored=scored)
    metrics = dict(evaluation_coverage=coverage, score_definition="Euclidean R/X distance in ohms to target or feasible set")
    if spec.region_file:
        metrics.update(inside_all_regions=bool((error[scored] <= GEOMETRY_ATOL).all()),
                       stored_sample_hits=int((evidence[scored] == "stored_sample").sum()),
                       interpolated_region_hits=int((evidence[scored] == "interpolated_region").sum()))
    return error, scored, extra, metrics
