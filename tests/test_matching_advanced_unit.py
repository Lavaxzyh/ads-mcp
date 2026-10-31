import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from pydantic import ValidationError

from matching_advanced import (SearchParameter, SearchSettings, ToleranceParameter, ToleranceSettings,
                               axis, bounded_product, prepare_advanced, run_search, run_tolerance, tolerance_design)
from matching_runs import SimulationCache, cache_eligibility
from matching_sweep import NetlistEditor, evaluate_frame
from matching_targets import build_regions, load_regions, region_distance
from rf_workflows import MatchingEvaluation, SweepParameter, sha256, write_json
from test_workflows_unit import NET, SPEC


def through(f=(1e9, 1.5e9, 2e9)):
    return pd.DataFrame({"S[1,1]": np.zeros(len(f)), "S[1,2]": np.ones(len(f)),
                         "S[2,1]": np.ones(len(f)), "S[2,2]": np.zeros(len(f))}, index=f)


def table_spec(interpolation="exact"):
    return {k: v for k, v in SPEC.items() if not k.startswith("target_")} | dict(
        target_table=[dict(freq_hz=1e9, r_ohm=30, x_ohm=10), dict(freq_hz=2e9, r_ohm=40, x_ohm=20)],
        target_interpolation=interpolation)


class AdvancedTests(unittest.TestCase):
    def test_frequency_table_exact_and_explicit_linear(self):
        points, result = evaluate_frame(through(), table_spec())
        self.assertEqual(result["evaluated_points"], 2)
        self.assertFalse(points.is_scored.iloc[1])
        self.assertTrue(np.isnan(points.error_ohm.iloc[1]))
        points, result = evaluate_frame(through(), table_spec("linear"))
        self.assertEqual(result["evaluated_points"], 3)
        self.assertEqual(points.target_evidence.iloc[1], "interpolated_target")
        self.assertAlmostEqual(points.error_ohm.iloc[1], 3)
        self.assertAlmostEqual(result["max_error_ohm"], np.sqrt(89))

    def test_missing_exact_frequency_not_nearest_or_interpolated(self):
        s = table_spec(); s["target_table"][1]["freq_hz"] = 1.9e9
        with self.assertRaisesRegex(ValueError, "exactly one"):
            evaluate_frame(through(), s)

    def test_target_modes_and_extrapolation_rejected(self):
        for spec in (SPEC | {"target_table": table_spec()["target_table"]}, table_spec("linear") | {"stop_hz": 3e9},
                     SPEC | {"target_interpolation": "linear"}):
            with self.assertRaises(ValidationError):
                MatchingEvaluation.model_validate(spec)

    def test_model_parameters_and_same_line_edits(self):
        editor = NetlistEditor(NET + "model MSub1 MSUB H=0.8 mm Er=3.48 TanD=0.0037\n")
        targets = [SweepParameter(model="MSub1", parameter="Er", values=[4]), SweepParameter(model="MSub1", parameter="H", values=[1], unit="mm")]
        text = editor.render(targets, [4, 1])
        self.assertIn("H=1 mm Er=4 TanD=0.0037", text)
        self.assertEqual(targets[0].key(), "top/model:MSub1.Er")

    def region_fixture(self, root):
        rows = []
        for r, x in itertools.product((1., 2., 3.), (-1., 0., 1.)):
            rows.append(dict(dataset_id="dataset_001", freq_Hz=1e9, R_ohm=r, X_ohm=x,
                             PAE_pct=0 if (r, x) == (2, 0) else 50, Pout_dBm=40, Gt_dB=10,
                             numeric_valid=True, Pavs_dBm=30, VDD_V=28, VGG_V=2.7, Z0_ohm=50))
        csv = root / "all_points.csv"
        pd.DataFrame(rows).to_csv(csv, index=False)
        manifest = root / "manifest.json"
        write_json(manifest, dict(all_points_csv=str(csv), all_points_sha256=sha256(csv),
                   reference_plane=SPEC["reference_plane"], conditions="synthetic test, fixed bias/source/harmonics",
                   datasets=[dict(source_path=str(root / "fixture.ds"), harmonic_order=3)]))
        return manifest

    def test_region_hole_is_not_convex_hull_and_evidence_separated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest = self.region_fixture(root)
            result = build_regions(manifest, {"min_pae_pct": 25}, str(root / "region"), "triangulated", 1.5)
            data = load_regions(result["region_file"], MatchingEvaluation(**SPEC))
            region = data["frequency_regions"][0]
            self.assertGreater(region_distance((2, 0), region)[0], 0.1)
            self.assertEqual(region_distance((3, 1), region)[1], "stored_sample")
            self.assertEqual(region_distance((2, 0.75), region)[1], "interpolated_region")
            self.assertAlmostEqual(region_distance((5, 0), region)[0], 2)
            self.assertEqual(region["qualified_samples"], 8)
            self.assertFalse(data["hb_convergence_verified"])

    def test_samples_do_not_create_regions_and_hash_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest = self.region_fixture(root)
            result = build_regions(manifest, {"min_pae_pct": 25}, str(root / "region"))
            data = load_regions(result["region_file"], MatchingEvaluation(**SPEC))
            self.assertEqual(data["frequency_regions"][0]["polygons"], [])
            self.assertGreater(region_distance((2, 0.75), data["frequency_regions"][0])[0], 0)
            with (root / "all_points.csv").open("a") as f:
                f.write("\n")
            with self.assertRaisesRegex(ValueError, "integrity"):
                build_regions(manifest, {"min_pae_pct": 25}, str(root / "bad"))
            self.assertFalse((root / "bad").exists())

    def test_region_plane_and_sparse_frequency_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest = self.region_fixture(root)
            region = build_regions(manifest, {"min_pae_pct": 25}, str(root / "region"))["region_file"]
            with self.assertRaisesRegex(ValueError, "reference_plane"):
                load_regions(region, MatchingEvaluation(**(SPEC | {"reference_plane": "different plane"})))
            spec = {k: v for k, v in SPEC.items() if not k.startswith("target_")} | {"region_file": region}
            points, result = evaluate_frame(through(), spec)
            self.assertEqual(result["evaluated_points"], 1)
            self.assertIn("no claims between", result["evaluation_coverage"])
            self.assertEqual(points.target_evidence.iloc[0], "outside_region")

    def test_preferred_values_and_manufacturing_grid(self):
        p = SearchParameter(variable="Lvalue", unit="nH", minimum=1, maximum=3, e_series="E12")
        self.assertEqual(axis(p, 20), [1, 1.2, 1.5, 1.8, 2.2, 2.7])
        p = SearchParameter(instance="TL1", parameter="W", unit="mm", minimum=.8, maximum=1.23, step=.1)
        self.assertEqual(axis(p, 20), [.8, .9, 1., 1.1, 1.2])
        for bad in ({"step": .1, "allowed_values": [1, 2]}, {"allowed_values": [4]}, {"e_series": "E24", "minimum": 0}):
            with self.assertRaises(ValidationError):
                SearchParameter.model_validate(dict(variable="Lvalue", minimum=1, maximum=3) | bad)

    def test_high_dimensional_sampling_is_bounded_and_repeatable(self):
        axes = [list(range(11))]*8
        a = bounded_product(axes, 31, np.random.default_rng(4))
        self.assertEqual(a, bounded_product(axes, 31, np.random.default_rng(4)))
        self.assertEqual(len(set(a)), 31)
        self.assertIn((0,)*8, a); self.assertIn((10,)*8, a)

    def test_tolerances_reject_clipping_and_corner_budget(self):
        with self.assertRaises(ValidationError):
            ToleranceParameter(variable="Lvalue", nominal=1, relative_pct=20, minimum=.9)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"base.net"; path.write_text(NET)
            with self.assertRaisesRegex(ValueError, "exceeding budget"):
                prepare_advanced("tolerance", path, [dict(variable="Lvalue", nominal=2, relative_pct=10)], SPEC,
                                 settings={"method": "corners", "budget": 4}, output_dir=str(Path(tmp)/"out"))
            self.assertFalse((Path(tmp)/"out").exists())

    def test_monte_carlo_reproducible_bounded_and_unclipped(self):
        targets = [ToleranceParameter(variable="Lvalue", nominal=2, relative_pct=10)]
        settings = ToleranceSettings(method="monte_carlo", samples=239, distribution="truncated_normal", seed=7, budget=256)
        a = tolerance_design(targets, settings)
        self.assertEqual(a, tolerance_design(targets, settings))
        draws = np.asarray([v[0] for tag, v in a if tag == "trial"])
        self.assertTrue(((draws > 1.8) & (draws < 2.2)).all())
        self.assertEqual(len(draws), 239)

    def test_external_model_cache_fails_closed(self):
        self.assertEqual(cache_eligibility(NET, ""), "")
        for text, directory in ((NET+"#include device.net\n", ""), (NET+"S2P:D1 a b File=\"data.s2p\"\n", ""),
                                (NET+'R:R1 a b R=read_external("data.txt")\n', ""), (NET, "models")):
            self.assertIn("disabled", cache_eligibility(text, directory))

    def test_cache_integrity_and_new_objective_rescoring(self):
        with tempfile.TemporaryDirectory() as tmp, patch("matching_runs.solver_fingerprint", return_value="solver-v1"):
            root = Path(tmp); source = root/"base.net"; source.write_text(NET)
            cache = SimulationCache(dict(text=NET, source_netlist=str(source), data_directory=""), str(root/"cache"), True)
            raw = root/"raw.npz"; s = np.zeros((2,2,2), complex); s[:,0,1]=1; s[:,1,0]=1
            np.savez(raw, freq_hz=[1e9,2e9], s=s)
            dataset = root/"test.ds"; dataset.write_bytes(b"synthetic cache integrity fixture")
            key = cache.key(NET); cache.put(key, dict(raw_sparameters=str(raw),dataset=str(dataset)), root)
            meta, status = cache.get(key); self.assertEqual(status, "hit")
            result = cache.materialize(meta, root/"hit", NET, SPEC | {"target_r_ohm": 45})
            self.assertAlmostEqual(result["max_error_ohm"], 10)
            self.assertNotEqual(cache.key(NET+"\n"), key)
            blob = cache.directory/meta["files"]["raw"]["file"]
            with blob.open("ab") as stream:
                stream.write(b"corruption")
            meta, reason = cache.get(key)
            self.assertIsNone(meta); self.assertIn("invalid", reason)

    def fake_solver(self, case, rendered, evaluation, data_directory, timeout_seconds):
        case.mkdir(parents=True)
        # Deliberately simple independent oracle: through network, varied scalar
        # Lvalue perturbs reflection through a real input impedance.
        import re
        value = float(re.search(r"Lvalue=([\d.e+-]+)", rendered)[1])
        z = 30 + value*5
        gamma = (z-50)/(z+50)
        frame = through(); frame["S[1,1]"] = gamma; frame["S[1,2]"] = 0; frame["S[2,1]"] = 0
        points, metrics = evaluate_frame(frame, evaluation)
        points.to_csv(case/"points.csv", index=False)
        return dict(points_csv=str(case/"points.csv"), **metrics)

    def test_search_budget_refinement_and_no_writeback(self):
        with tempfile.TemporaryDirectory() as tmp, patch("matching_runs.execute_case", side_effect=self.fake_solver):
            root=Path(tmp); source=root/"base.net"; source.write_text(NET); before=sha256(source)
            plan=prepare_advanced("search", source, [dict(variable="Lvalue",minimum=1,maximum=3)], SPEC,
                                  settings={"budget":5,"coarse_points":2},output_dir=str(root/"out"),use_cache=False)
            result=run_search(plan)
            self.assertTrue(result["completed"])
            self.assertEqual(result["evaluated_cases"],5)
            self.assertGreater(result["fine_cases"],0)
            self.assertEqual(result["simulation_attempts"],5)
            self.assertEqual(sha256(source),before)

    def test_tolerance_sensitivity_not_zero_at_optimum_and_failure_denominator(self):
        with tempfile.TemporaryDirectory() as tmp, patch("matching_runs.execute_case", side_effect=self.fake_solver):
            root=Path(tmp); source=root/"base.net"; source.write_text(NET)
            spec=SPEC | dict(load_r_ohm=50,load_x_ohm=0,target_r_ohm=40,target_x_ohm=0,max_error_ohm=.5)
            plan=prepare_advanced("tolerance",source,[dict(variable="Lvalue",nominal=2,relative_pct=10)],spec,
                                  settings={"method":"corners","budget":5},output_dir=str(root/"out"),use_cache=False,stop_on_error=False)
            result=run_tolerance(plan)
            self.assertTrue(result["completed"])
            self.assertAlmostEqual(result["sensitivity"][0]["max_delta_z_ohm"],1)
            self.assertAlmostEqual(result["sensitivity"][0]["max_abs_dz_dparameter"],5)
            self.assertAlmostEqual(result["sensitivity"][0]["score_derivative"],0)
            self.assertEqual(result["trial_statistics"]["verified_pass_fraction"],0)
            calls=[0]
            def sometimes_fail(*args):
                calls[0]+=1
                if calls[0]==4:
                    raise RuntimeError("intentional trial failure")
                return self.fake_solver(*args)
            plan["output_dir"]=str(root/"failed")
            with patch("matching_runs.execute_case",side_effect=sometimes_fail):
                failed=run_tolerance(plan)
            self.assertFalse(failed["completed"])
            self.assertEqual(failed["trial_statistics"]["simulation_failures"],1)
            self.assertEqual(failed["trial_statistics"]["requested"],2)


if __name__ == "__main__":
    unittest.main()
