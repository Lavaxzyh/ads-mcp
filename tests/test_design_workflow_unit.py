import copy
import json
from pathlib import Path
import tempfile
import unittest

from schematic_templates import expand, catalog
from workflow_trace import Trace


class Templates(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]
        self.short=json.loads((root/'examples/schematic/output_match_short.json').read_text(encoding='utf-8'))
        self.full=json.loads((root/'examples/schematic/output_match.json').read_text(encoding='utf-8'))

    def test_template_preserves_every_electrical_choice_and_pin_partition(self):
        plan, info=expand(self.short)
        self.assertLess(info['input_bytes'], info['expanded_bytes']/3)
        self.assertEqual(plan.model_dump()['expected_nets'],self.full['expected_nets'])
        self.assertEqual(plan.model_dump(mode='json')['wires'],self.full['wires'])
        a={c['name']:c for c in self.full['components']}
        for c in plan.components:
            if c.name in ('SP1','Term1','Term2','GOAL1','GOAL2','VAR_TARGET'):continue
            self.assertEqual(c.parameters,a[c.name].get('parameters',{}))
            self.assertEqual(c.variables,a[c.name].get('variables',{}))

    def test_no_default_board_target_or_dimensions(self):
        for field in ('MSub1.Er','MSub1.H','Rtarget','W_TL1','VDD'):
            q=copy.deepcopy(self.short);q['inputs'].pop(field)
            with self.assertRaisesRegex(ValueError,'missing'): expand(q)

    def test_override_is_bounded_and_does_not_change_contract(self):
        self.short['overrides']=[dict(instance='VAR1',field='variables',key='L_TL1',value='4 mm')]
        p,meta=expand(self.short)
        self.assertEqual(next(c for c in p.components if c.name=='VAR1').variables['L_TL1'],'4 mm')
        self.assertEqual(len(meta['overrides']),1)
        for instance,field,key in [('Term1','parameters','Z'),('MSub1','parameters','Er'),('VAR_TARGET','variables','Rtarget')]:
            self.short['overrides']=[dict(instance=instance,field=field,key=key,value='9')]
            with self.assertRaisesRegex(ValueError,'protected'):expand(self.short)

    def test_unsupported_input_and_complex_reference_fail(self):
        self.short['inputs']['unknown']='1'
        with self.assertRaises(ValueError):expand(self.short)
        del self.short['inputs']['unknown']
        self.short['contract']['ports'][0]['impedance']='50+j*5'
        with self.assertRaisesRegex(ValueError,'real SP'):expand(self.short)


class Timing(unittest.TestCase):
    def test_unobserved_is_not_claimed_measured(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=Trace(tmp)
            with t.span('configuration','expansion'):pass
            t.native_timings({'reopen_verification':.02})
            s=t.summary()
            self.assertIn('preparation',s['unobserved'])
            self.assertNotIn('acceptance',s['unobserved'])
            self.assertEqual(s['open_spans'],[])

    def test_failures_stop_unchanged_retry_but_implementation_change_can_be_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=Trace(tmp)
            for _ in range(2):t.failure('request','code','unsupported')
            with self.assertRaisesRegex(RuntimeError,'STOP_REPEATED'):t.check_retry('request','code')
            t.check_retry('request','fixed-code')

    def test_failed_and_incomplete_spans_remain_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=Trace(tmp)
            with self.assertRaises(ValueError):
                with t.span('build'):raise ValueError('fail')
            token=t.begin('preparation',observer='caller_boundary')
            self.assertEqual(t.summary()['open_spans'],[token])
            t.end(token)
            with self.assertRaises(ValueError):t.end(token)


if __name__=='__main__':unittest.main()
