import copy,json,os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from schematic_flow import BuildPlan,plan_digest,digest
from design_snapshots import manifest,clone_workspace
from design_changes import same_netlist_after_replacement,verify_delta,PatchRequest
from design_results import canonical_netlist,raw_difference,data_dependencies
from workflow_trace import Trace
from design_capabilities import inventory
import numpy as np


class Boundaries(unittest.TestCase):
    def test_old_v1_receipts_still_match(self):
        path=Path(__file__).resolve().parents[1]/'examples/schematic/output_match.json'
        old=json.loads(path.read_text(encoding='utf-8'))
        # v1 had no activation field; synthetic publication examples may now
        # disable controllers. Reconstruct the actual legacy all-enabled input.
        for component in old['components']:component.pop('enabled',None)
        plan=BuildPlan.model_validate(old)
        # Compare the canonical pre-activation schema (including old defaults).
        data=plan.model_dump()
        for c in data['components']:c.pop('enabled')
        expected=digest(json.dumps(data,sort_keys=True,ensure_ascii=False).encode())
        self.assertEqual(plan_digest(plan),expected)
        plan.components[0].enabled=False
        self.assertNotEqual(plan_digest(plan),expected)

    def test_native_reordering_does_not_hide_changed_values_or_other_statements(self):
        a='VAR=1\nC:C1 1 0 C=2 pF\nR:R1 1 0 R=50 Ohm\n'
        b='VAR=1\nR:R1 1 0 R=50 Ohm\nC:C1 1 0 C=2 pF\n'
        self.assertTrue(same_netlist_after_replacement(a,b,{'C1'}))
        self.assertFalse(same_netlist_after_replacement(a,b.replace('2 pF','3 pF'),{'C1'}))
        self.assertFalse(same_netlist_after_replacement(a,b.replace('VAR=1','VAR=2'),{'C1'}))

    def test_canonical_netlist_preserves_quoted_data_and_include_directives(self):
        self.assertEqual(canonical_netlist(' C:C1  1 0 C=2 pF '),'C:C1 1 0 C=2 pF')
        self.assertNotEqual(canonical_netlist('X:X1 File="a  b.dat"'),canonical_netlist('X:X1 File="a b.dat"'))
        self.assertIn('#include',canonical_netlist('#include "device.net"'))

    def test_clone_rebases_only_local_libraries_and_detects_external_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();lib=source/'test_lib';lib.mkdir()
            (source/'lib.defs').write_text('DEFINE test_lib '+lib.as_posix()+'\n',encoding='utf-8')
            (lib/'sch.oa').write_bytes(b'circuit');(lib/'model.s2p').write_bytes(b'model')
            before=manifest(source)
            with patch.dict(os.environ,HPEESOF_DIR=str(root/'ads')):
                clone_workspace(source,root/'copy',before)
                self.assertEqual((root/'copy/lib.defs').read_text(),'DEFINE test_lib test_lib\n')
                self.assertEqual(manifest(source),before)
                (lib/'model.s2p').write_bytes(b'edited')
                with self.assertRaisesRegex(ValueError,'CONCURRENT_SOURCE_EDIT'):clone_workspace(source,root/'never_created',before)
                self.assertFalse((root/'never_created').exists())

    def test_external_library_and_nested_destination_rejected_before_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);s=r/'source';s.mkdir();outside=r/'other_lib';outside.mkdir()
            (s/'lib.defs').write_text('DEFINE other '+outside.as_posix()+'\n',encoding='utf-8')
            with patch.dict(os.environ,HPEESOF_DIR=str(r/'ads')):
                with self.assertRaisesRegex(ValueError,'External user library'):clone_workspace(s,r/'copy')
                self.assertFalse((r/'copy').exists())
                with self.assertRaises(ValueError):clone_workspace(s,s/'child')

    def test_simulation_arrays_need_same_grid_and_finite_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'a.npz';b=Path(tmp)/'b.npz'
            np.savez(a,freq_hz=np.array([1.,2.]),s=np.ones((2,2,2),complex))
            np.savez(b,freq_hz=np.array([1.,3.]),s=np.ones((2,2,2),complex))
            with self.assertRaisesRegex(ValueError,'Frequency grids'):raw_difference(a,b)
            np.savez(b,freq_hz=np.array([1.,2.]),s=np.ones((2,2,2),complex)*float('nan'))
            with self.assertRaisesRegex(ValueError,'Invalid S'):raw_difference(a,b)

    def test_model_data_changes_invalidate_evidence_and_profile_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);p=r/'model.s2p';p.write_text('original',encoding='utf-8')
            before=data_dependencies('S2P:S1 File="model.s2p"',str(r),str(r));meta=inventory(r)
            p.write_text('different model data',encoding='utf-8')
            self.assertNotEqual(before,data_dependencies('S2P:S1 File="model.s2p"',str(r),str(r)))
            self.assertNotEqual(meta,inventory(r))

    def test_repeated_adapter_gap_stops_even_if_request_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=Trace(tmp);t.failure('a','code','UNADAPTED_ENUM: C1.foo')
            t.failure('b','code','UNADAPTED_ENUM: C2.foo')
            with self.assertRaisesRegex(RuntimeError,'STOP_REPEATED_INTERFACE'):t.check_retry('c','code')


if __name__=='__main__':unittest.main()
