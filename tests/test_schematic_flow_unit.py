"""Engineering contracts and retry/ownership boundaries, without native ADS."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from pydantic import ValidationError
from schematic_flow import BuildPlan, hz, target_lock, snapshot_fingerprint, point_on_segment


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.plan=json.loads((Path(__file__).resolve().parents[1]/'examples/schematic/output_match.json').read_text(encoding='utf-8'))

    def bad(self, change):
        p=copy.deepcopy(self.plan);change(p)
        with self.assertRaises(ValidationError):BuildPlan.model_validate(p)

    def test_reference_fixture_has_explicit_execution_boundary(self):
        p=BuildPlan.model_validate(self.plan)
        self.assertEqual(p.contract.execution,'build_only')
        self.assertEqual(len(p.contract.ports),2)

    def test_cannot_request_simulation_or_unknown_operations(self):
        self.bad(lambda p:p['contract'].update(execution='simulate'))
        self.bad(lambda p:p.update(code='print(1)'))

    def test_port_mismatch_and_duplicate_port_numbers_fail(self):
        self.bad(lambda p:p['contract']['ports'][0].update(impedance='25 Ohm'))
        self.bad(lambda p:p['contract']['ports'][1].update(number=1))

    def test_frequency_mismatch_negative_and_nonfinite_fail(self):
        for f in [[2e9,3e9,1e7],[-1,2,1],[2e9,3e9,float('nan')]]:
            self.bad(lambda p:p['contract'].update(frequency_hz=f))

    def test_missing_reference_plane_fails(self):
        self.bad(lambda p:p['contract']['ports'][0].pop('reference_plane'))

    def test_cross_net_wire_and_duplicate_pin_fail(self):
        self.bad(lambda p:p['wires'][0].update(end='Term2:1'))
        self.bad(lambda p:p['expected_nets'][0]['pins'].append(p['expected_nets'][1]['pins'][0]))

    def test_wrong_measeq_library_and_optimizer_literal_fail(self):
        self.bad(lambda p:next(c for c in p['components'] if c['cell']=='MeasEqn').update(library='ads_datacmps'))
        self.bad(lambda p:next(c for c in p['components'] if c['cell']=='Optim')['parameters'].update(OptimType='gradient'))

    def test_names_and_variables_cannot_be_ambiguous(self):
        self.bad(lambda p:p['components'].append(p['components'][0]))
        self.bad(lambda p:next(c for c in p['components'] if c['name']=='VAR_FEED')['variables'].update(W_TL1='1 mm'))

    def test_paths_and_nonfinite_coordinates_fail(self):
        self.bad(lambda p:p['target'].update(workspace='../existing'))
        self.bad(lambda p:p['components'][0].update(at=[float('inf'),0]))
        self.bad(lambda p:p['target'].update(cell='../original'))

    def test_nonlinear_controller_and_wrong_analysis_fail(self):
        self.bad(lambda p:p['components'].append(dict(name='HB1',library='ads_simulation',cell='HarmonicBalance',at=[0,0])))
        self.bad(lambda p:next(c for c in p['components'] if c['cell']=='Optim')['parameters'].update(FinalAnalysis='All'))

    def test_fingerprint_changes_on_circuit_edit_but_not_log(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);(p/'sch.oa').write_bytes(b'circuit')
            before=snapshot_fingerprint(p)
            (p/'run.log').write_text('progress',encoding='utf-8')
            self.assertEqual(before,snapshot_fingerprint(p))
            (p/'sch.oa').write_bytes(b'modified')
            self.assertNotEqual(before,snapshot_fingerprint(p))

    def test_target_lock_is_exclusive_and_released(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'new_wrk'
            with target_lock(p):
                with self.assertRaises(RuntimeError):
                    with target_lock(p):pass
            with target_lock(p):pass

    def test_units_are_parsed_without_expression_evaluation(self):
        self.assertEqual(hz('2.4 GHz'),2.4e9)
        with self.assertRaises(ValueError):hz('start+1e9')

    def test_wire_geometry_uses_segments_not_a_polyline_bounding_box(self):
        self.assertTrue(point_on_segment((1,0),(0,0),(2,0)))
        self.assertTrue(point_on_segment((1,1),(0,0),(2,2)))
        self.assertFalse(point_on_segment((1,1),(0,0),(2,0)))
        self.assertFalse(point_on_segment((3,0),(0,0),(2,0)))


if __name__=='__main__':unittest.main()
