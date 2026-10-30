import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from pydantic import ValidationError

from matching_sweep import NetlistEditor, evaluate_frame, prepare_sweep
from rf_workflows import MatchingEvaluation, SweepParameter, analyze_loadpull, common_loads, fresh_output, sha256


SPEC = dict(start_hz=1e9, stop_hz=2e9, reference_ohms=50, load_r_ohm=35, load_x_ohm=12,
            target_r_ohm=35, target_x_ohm=12, input_port=1, reference_plane="port 1 of an ideal through")
NET = '''Options UseNutmegFormat=no
Lvalue=2 nH
define DUT (a b)
Lvalue=4 nH
L:L1 a b L=Lvalue Noise=yes
end DUT
L:L1 a b L=Lvalue Noise=yes
MLIN:TL1 b c W=1 mm L=10 mm Subst="MSub1"
Port:P1 a 0 Num=1 Z=50 Ohm Noise=yes
Port:P2 c 0 Num=2 Z=50 Ohm Noise=yes
S_Param:SP1 SweepVar="freq"
'''


class WorkflowTests(unittest.TestCase):
    def test_loaded_through_uses_complex_termination_not_s11_only(self):
        frame = pd.DataFrame({"S[1,1]": [0,0], "S[1,2]": [1,1], "S[2,1]": [1,1], "S[2,2]": [0,0]}, index=[1e9,2e9])
        points, result = evaluate_frame(frame, SPEC)
        np.testing.assert_allclose(points.zin_r_ohm, 35, atol=1e-12)
        np.testing.assert_allclose(points.zin_x_ohm, 12, atol=1e-12)
        self.assertLess(result["max_error_ohm"], 1e-12)

    def test_explicit_port_orientation(self):
        frame = pd.DataFrame({"S[1,1]": [0.1], "S[1,2]": [0.2], "S[2,1]": [0.3], "S[2,2]": [0.4]}, index=[1e9])
        points, _ = evaluate_frame(frame, {**SPEC, "stop_hz":1e9, "input_port":2, "load_r_ohm":50, "load_x_ohm":0})
        self.assertAlmostEqual(points.zin_r_ohm.iloc[0], 50*1.4/.6)

    def test_band_coverage_and_multidimensional_rejected(self):
        frame = pd.DataFrame({"S[1,1]": [0], "S[1,2]": [1], "S[2,1]": [1], "S[2,2]": [0]}, index=[1.5e9])
        with self.assertRaisesRegex(ValueError, "cover"):
            evaluate_frame(frame, SPEC)

    def test_nonfinite_and_invalid_contract_rejected(self):
        for change in ({"reference_ohms":float("nan")}, {"load_r_ohm":0}, {"start_hz":3e9}, {"reference_plane":" "}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                MatchingEvaluation(**{**SPEC, **change})

    def test_scoped_variable_does_not_change_subcircuit(self):
        editor = NetlistEditor(NET)
        value = editor.render([SweepParameter(variable="Lvalue", values=[3], unit="nH")], [3])
        self.assertIn("Lvalue=3 nH", value)
        self.assertIn("Lvalue=4 nH", value)

    def test_two_parameters_same_instance_preserve_string_and_other_values(self):
        targets = [SweepParameter(instance="TL1", parameter=p, values=[v], unit="mm") for p,v in [("W",2),("L",20)]]
        text = NetlistEditor(NET).render(targets, [2,20])
        self.assertIn('W=2 mm L=20 mm Subst="MSub1"', text)
        self.assertIn("L:L1 a b L=Lvalue Noise=yes", text)

    def test_missing_ambiguous_targets_and_port_edits_rejected(self):
        for source, target in [(NET, dict(variable="missing",values=[1])),
                               (NET+"Lvalue=6\n",dict(variable="Lvalue",values=[1])),
                               (NET,dict(instance="P1",parameter="Z",values=[75]))]:
            with self.subTest(target=target), self.assertRaises(ValueError):
                NetlistEditor(source).location(SweepParameter(**target))

    def test_hb_multiple_controllers_and_wrong_reference_rejected(self):
        for source in (NET+"HB:HB1 Freq[1]=1 GHz\n", NET+"S_Param:SP2\n", NET.replace("Z=50", "Z=75")):
            with self.assertRaises(ValueError):
                NetlistEditor(source).validate_sp(50)

    def test_case_limit_rejected_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/"base.net"; source.write_text(NET, encoding="utf-8")
            target=Path(tmp)/"out"
            with self.assertRaisesRegex(ValueError, "exceeding"):
                prepare_sweep(str(source), [dict(variable="Lvalue",values=[1,2,3])], SPEC, str(target), max_cases=2)
            self.assertFalse(target.exists())

    def test_output_overwrite_and_source_workspace_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/"lib.defs").touch(); source=root/"source.ds"; source.touch()
            with self.assertRaises(FileExistsError): fresh_output(str(root), "x", [source])
            with self.assertRaisesRegex(ValueError,"outside"): fresh_output(str(root/"output"), "x", [source])

    def frame(self):
        return pd.DataFrame(dict(R_ohm=[10.,20.,30.],X_ohm=[1.,2.,3.],Pout_dBm=[30.,40.,50.],
                                 PAE_pct=[40.,50.,60.],Gt_dB=[10.,20.,30.],numeric_valid=[True,True,False],
                                 Pavs_dBm=[20.]*3,VDD_V=[28.]*3,VGG_V=[2.]*3,Z0_ohm=[50.]*3,
                                 meets_limits=[False,True,False]))

    def test_common_loads_align_coordinates_not_row_order(self):
        left=self.frame(); right=left.iloc[::-1].copy()
        right.loc[right.R_ohm==20,"PAE_pct"]=45
        result=common_loads([left,right],[{"harmonic_order":5}]*2,"external drain","same model and harmonic terminations")
        self.assertEqual(len(result),2)
        self.assertEqual(result.iloc[0].R_ohm,20)
        self.assertEqual(result.iloc[0].worst_PAE_pct,45)

    def test_mixed_drive_and_missing_context_rejected(self):
        left=self.frame(); right=self.frame(); right.Pavs_dBm=21
        with self.assertRaisesRegex(ValueError,"Pavs"):
            common_loads([left,right],[{"harmonic_order":5}]*2,"drain","same model")
        with self.assertRaisesRegex(ValueError,"requires"):
            common_loads([left,left],[{"harmonic_order":5}]*2,"","")

    def test_all_points_retained_even_when_no_points_qualify(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/"source.ds"; source.write_bytes(b"fixture")
            data=self.frame(); data["hb_convergence_verified"]=False
            summary=dict(sha256=sha256(source),harmonic_order=5)
            with patch("loadpull_exporter.extract", return_value=(data,summary)):
                result=analyze_loadpull([str(source)], str(root/"out"), {"min_pae_pct":99})
            self.assertEqual(result["points"],3)
            self.assertEqual(result["qualifying_points"],0)
            self.assertFalse(result["hb_convergence_verified"])
            self.assertEqual(len(pd.read_csv(result["all_points_csv"])),3)
            json.loads((root/"out/manifest.json").read_text(encoding="utf-8"))


if __name__ == "__main__": unittest.main()
