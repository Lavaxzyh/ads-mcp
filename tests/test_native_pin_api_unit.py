"""ADS 2025 method and ADS 2027 property pin-number API compatibility."""
from types import SimpleNamespace
import unittest
from schematic_flow import native_pins
from design_snapshots import read_design


class PinNumberCompatibilityTests(unittest.TestCase):
    def test_native_pin_mapping_accepts_both_supported_apis(self):
        for term in (SimpleNamespace(is_numbered=True,term_number=2),
                     SimpleNamespace(is_numbered=True,get_inst_term_number=lambda:2)):
            pin=SimpleNamespace(inst_term=term,snap_point=SimpleNamespace(x=0,y=0))
            design=SimpleNamespace(instances=[SimpleNamespace(name='R1',inst_pins=[pin])])
            self.assertEqual(native_pins(design),{'R1:2':pin})

    def test_snapshot_remains_complete_and_addressable_with_both_apis(self):
        for term in (SimpleNamespace(is_numbered=True,term_number=3,net=None),
                     SimpleNamespace(is_numbered=True,get_inst_term_number=lambda:3,net=None)):
            pin=SimpleNamespace(inst_term=term,snap_point=SimpleNamespace(x=1,y=2))
            instance=SimpleNamespace(name='R1',library_name='ads_rflib',cell_name='R',view_name='symbol',
                origin=SimpleNamespace(x=0,y=0),orient=SimpleNamespace(name='R0'),
                is_deactivated=False,is_deactivated_and_shorted=False,is_var_instance=False,
                parameters=[],inst_pins=[pin])
            design=SimpleNamespace(instances=[instance],shapes=[],generate_netlist=lambda:'R:R1 a b R=50 Ohm')
            snapshot,netlist=read_design(design)
            self.assertTrue(snapshot['complete'],snapshot['unreadable'])
            self.assertEqual(snapshot['components'][0]['pins'][0]['ref'],'R1:3')
            self.assertEqual(snapshot['connections'][0]['pins'],['R1:3'])
            self.assertIn('R:R1',netlist)


if __name__=='__main__':unittest.main()
