"""Regression cases for collaboration, restored content and RF evidence boundaries."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import numpy as np
from matching_advanced import ToleranceParameter, ToleranceSettings, tolerance_design
from matching_comparison import ComponentModel, substitute
from pa_sweep import failure, group_metrics, operating_grid
from rf_workflows import MatchingEvaluation
from rf_workflows import write_json
from rf_executor import Executor
from schematic_backup import changes, checkpoint, hash_stream, resolve_checkpoint
from schematic_connections import Expected,check
from schematic_graph import describe
from schematic_merge import conflicts
from twoport_review import factors, sample_indices


class CollaborationTests(unittest.TestCase):
    def test_two_sides_change_dependent_variables(self):
        left=[dict(key=["variable","VAR1","width"],before="1 mm",after="2 mm")]
        right=[dict(key=["variable","VAR2","stub_width"],before="3 mm",after="2*width")]
        self.assertEqual(conflicts(left,right)[0]["reason"],"shared_variable_reference")
        self.assertEqual(conflicts(left,left),[])

    def test_different_routes_on_the_same_pin_reject(self):
        def route(peer):
            return dict(key=["pin_net","R1:1"],before={"pins":["R1:1","T1:1"]},after={"pins":["R1:1",peer]})
        self.assertEqual(conflicts([route("A:1")],[route("B:1")])[0]["reason"],"different_edits")

    def test_non_port_short_identifies_native_pin_locations(self):
        names=["A:1","A:2","B:1","B:2"]
        expected=Expected(expected_nets=[{"name":"one","pins":names[:2]},{"name":"two","pins":names[2:]}])
        parts=[dict(name=n,library="test",cell="R",pins=[dict(ref=p,point=[i,0],native_net="N__0")
                     for i,p in enumerate(names) if p.startswith(n+":")]) for n in ("A","B")]
        result=check(dict(components=parts,connections=[dict(native_net="N__0",pins=names)]),expected)
        self.assertFalse(result["ok"])
        self.assertEqual(result["issues"][0]["kind"],"joined_nets")
        self.assertEqual(result["issues"][0]["pins"][2]["xy"],[2,0])

    def test_incomplete_snapshot_identifies_unreadable_fields(self):
        gap=[{"field":"TL1.orientation","error":"unreadable"}]
        result=describe(dict(complete=False,unreadable=gap),dict(complete=False,unreadable=[]))
        self.assertFalse(result["available"])
        self.assertEqual(result["before_gaps"],gap)

    def test_incremental_backup_reconstructs_add_change_delete_and_deduplicates(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            ws=root/"design_wrk";ws.mkdir()
            for name,value in (("cell","one"),("remove","old")):
                (ws/name).write_text(value,encoding="utf-8")
            digest=lambda:{p.name:hash_stream(p) for p in ws.iterdir()}
            state=dict(target={"workspace":str(ws)},revision=1,snapshot="native",snapshot_sha256="a"*64)
            original=digest()
            first=checkpoint(root,state,original)
            (ws/"remove").unlink()
            (ws/"cell").write_text("two",encoding="utf-8")
            (ws/"alias").write_text("one",encoding="utf-8")
            # Corruption of a blob being reused must be repaired before checkpoint acceptance.
            h=original["cell"]
            (root/"backups/objects"/h[:2]/h).write_text("bad",encoding="utf-8")
            second=checkpoint(root,dict(state,revision=2),digest())
            self.assertEqual(hash_stream(root/"backups/objects"/h[:2]/h),h)
            self.assertEqual(second["files_stored"],2)
            self.assertEqual(resolve_checkpoint(root/"backups",second["checkpoint"])[1],digest())
            self.assertEqual(resolve_checkpoint(root/"backups",first["checkpoint"])[1],original)
            self.assertEqual(changes(digest(),original),dict(restore=["remove"],remove=["alias"],overwrite=["cell"]))


class RFTests(unittest.TestCase):
    def test_worker_completion_is_private_to_each_case(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            executor=Executor(root)
            executor.process=Mock()
            executor.process.poll.return_value=None
            # A previous case's marker is deliberately unreadable.
            (executor.folder/"done.json").write_text("old",encoding="utf-8")
            def accept(path,data):
                write_json(path,data)
                if path.name=="next.json":
                    case=Path(data["request_file"]).parent
                    write_json(case/"result.json",dict(ok=True,owner=case.name))
                    path.unlink()
            with patch("rf_executor.write_json",side_effect=accept):
                for name in ("first","second"):
                    receipt=executor.execute(root/name,"deck",{},"",1)
                    self.assertEqual(receipt["owner"],name)

    def test_opposite_loadings_share_only_one_reproducible_random_draw(self):
        parts=[ToleranceParameter(variable=f"x{i}",nominal=10,absolute=d,correlation_group="board" if i<2 else "",
                                  correlation_sign=-1 if i==1 else 1) for i,d in enumerate((1,2,1))]
        config=ToleranceSettings(method="monte_carlo",samples=10,seed=17)
        design=tolerance_design(parts,config)
        self.assertEqual(design,tolerance_design(parts,config))
        self.assertEqual(len(design),15)
        trial=np.array([v for phase,v in design if phase=="trial"])-10
        np.testing.assert_allclose(trial[:,1],-2*trial[:,0])
        self.assertFalse(np.allclose(trial[:,0],trial[:,2]))

    def test_loadpull_regions_have_presented_impedances(self):
        with self.assertRaisesRegex(ValueError,"presented load"):
            MatchingEvaluation(start_hz=1e9,stop_hz=2e9,reference_ohms=50,load_r_ohm=50,load_x_ohm=0,
                               input_port=1,reference_plane="drain",region_file="r.json",target_convention="conjugate_of_device_impedance")

    def test_supplier_two_terminal_model_pin_order(self):
        with TemporaryDirectory() as directory:
            model=Path(directory)/"cap.net"
            model.write_text("define Chip (p n)\nC:Cx p n C=1 pF\nend Chip\n",encoding="utf-8")
            sub=ComponentModel(instance="C1",subcircuit="Chip",subcircuit_file=str(model),reverse_pins=True)
            deck,hashes=substitute("C:C1 L R C=1 pF\n",[sub])
            self.assertIn("Chip:C1 R L",deck)
            self.assertEqual(hashes[str(model)],hash_stream(model))

    def test_missing_hb_points_never_bridge_p1db_or_backoff(self):
        rows=[dict(case=k+1,candidate_id=0,freq_hz=1e9,bias_values=[1.0],pavs_dbm=p,
                   status=status,pout_dbm=p+g if g is not None else None,
                   gain_db=g,pae_pct=20 if g is not None else None,drain_eff_pct=30 if g is not None else None)
              for k,(p,g,status) in enumerate([(-10,10,"accepted"),(-5,10,"accepted"),(0,None,"not_converged"),(5,8,"accepted")])]
        result=group_metrics(rows,[3],None)
        self.assertIsNone(result["p1db"])
        self.assertEqual(result["backoff"][0]["status"],"unbracketed_or_gap")
        self.assertIsNone(rows[2]["compression_db"])

    def test_hb_convergence_error_takes_precedence_over_a_probe_hint(self):
        contract=dict(input_voltage="Vi",input_current="Ii",output_voltage="Vo",output_current="Io",supplies=[])
        with TemporaryDirectory() as directory:
            (Path(directory)/"simulator.log").write_text("HB started\nFailed to converge\n",encoding="utf-8")
            record=failure(dict(directory=directory,error="check probes"),contract)
            self.assertEqual((record["status"],record["line"]),("not_converged",2))
            self.assertTrue(record["next_check"])
            (Path(directory)/"simulator.log").write_text("Simulation finished\n",encoding="utf-8")
            record=failure(dict(directory=directory,error="Nonpositive PA powers; check probe directions"),contract)
            self.assertEqual(record["status"],"power_check_failed")

    def test_missing_operating_point_and_duplicate_case_are_distinct(self):
        point=dict(freq_hz=1e9,pavs_dbm=0,bias_values=[])
        plan=dict(candidates=[{"case":0}],operating_points=[point],contract={})
        self.assertEqual(operating_grid(plan,[])[0]["status"],"not_run")
        with self.assertRaisesRegex(ValueError,"Duplicate"):
            operating_grid(plan,[{"case":1},{"case":1}])

    def test_unilateral_stability_is_defined_without_a_finite_k(self):
        s=np.array([[[.2,0],[2,.25]],[[1.01,0],[2,.25]]],complex)
        data=factors(s)
        np.testing.assert_array_equal(data["unconditional"],[True,False])
        self.assertTrue(data["unilateral"].all())

    def test_comparison_rejects_unsolved_frequency_or_ambiguous_axis(self):
        np.testing.assert_equal(sample_indices(np.array([1e9,1.5e9,2e9]),[1e9,2e9]),[0,2])
        with self.assertRaisesRegex(ValueError,"solved sample"):
            sample_indices(np.array([1e9,2e9]),[1.5e9])
        with self.assertRaisesRegex(ValueError,"solved sample"):
            sample_indices(np.array([1e9,1e9]),[1e9])
