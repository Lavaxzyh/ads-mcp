import json,os,tempfile,time
from pathlib import Path
import unittest
from unittest.mock import patch
from single_schematic import TaskRequest,safe_target,snapshot,run,bind_snapshot
from schematic_flow import Target,atomic_json
from design_snapshots import file_hash,manifest
from environment_watch import Memo,Watch
from single_schematic_sim import simulation_context


class TaskBoundaries(unittest.TestCase):
    def test_mutations_need_stable_id_and_operation_specific_fields(self):
        for doc in [dict(operation='edit',changes=[]),dict(operation='build',request_id='a',build_request='x',changes=[]),
                    dict(operation='inspect',simulation={}),dict(operation='simulate',request_id='a')]:
            with self.assertRaises(ValueError):TaskRequest.model_validate(doc)
        self.assertEqual(TaskRequest(operation='status').wait_seconds,0)
    def test_only_independent_descendant_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for p in [root,root.parent/'original',Path('relative')]:
                with self.assertRaises(ValueError):safe_target(root,Target(workspace=str(p),library='lib',cell='cell'))
    def test_snapshot_detects_any_native_file_or_evidence_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ws=root/'design';ws.mkdir();(ws/'lib.defs').write_text('')
            native=ws/'cell.oa';native.write_bytes(b'original')
            net=root/'verified.net';net.write_bytes(b'netlist')
            path=root/'snapshot.json';target=dict(workspace=str(ws),library='lib',cell='cell')
            atomic_json(path,{'kind':'schematic_snapshot','source':target,'source_files':manifest(ws),'netlist':str(net),'netlist_sha256':file_hash(net)})
            state={'snapshot':str(path),'snapshot_sha256':file_hash(path),'target':target}
            self.assertEqual(snapshot(state)['source'],target)
            native.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'CONCURRENT_TASK_EDIT'):snapshot(state)
            native.write_bytes(b'original');net.write_bytes(b'wrong')
            with self.assertRaisesRegex(ValueError,'netlist evidence'):snapshot(state)
    def test_simulation_cannot_supply_other_snapshot_or_missing_authorization(self):
        with self.assertRaisesRegex(ValueError,'Simulation takes only'):simulation_context({'snapshot':'outside'}, {})
        state={'snapshot':'dummy','snapshot_sha256':'0'*64}
        with self.assertRaises(ValueError):simulation_context({'evaluation':{}},state)

    def test_new_snapshot_invalidates_current_simulation_but_retains_evidence(self):
        state={'revision':2,'simulation':{'finalized':True,'evidence':'old.json','status':'completed','job_id':'old-job'},
               'simulation_cache':{'old-key':{'evidence':'old.json'}}}
        with patch('single_schematic.load_snapshot',return_value={'contract':{'version':'new'}}):
            bind_snapshot(state,{'snapshot':'new.json','snapshot_sha256':'a'*64})
        self.assertNotIn('simulation',state)
        self.assertEqual(state['previous_simulation']['revision'],2)
        self.assertEqual(state['previous_simulation']['evidence'],'old.json')
        self.assertIn('old-key',state['simulation_cache'])

    def test_precreation_failure_can_be_corrected_without_replacing_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bad=root/'bad.json';bad.write_text('{broken',encoding='utf-8')
            for request_id in ('first','corrected'):
                with self.assertRaises(json.JSONDecodeError):
                    run(dict(operation='build',request_id=request_id,build_request=str(bad)),root,None)
                state=json.loads((root/'task.json').read_text(encoding='utf-8'))
                self.assertEqual(state['status'],'rejected_before_creation')
            self.assertEqual(len(list((root/'operations').iterdir())),2)
            with self.assertRaisesRegex(ValueError,'PREVIOUS_REQUEST_FAILED'):
                run(dict(operation='build',request_id='first',build_request=str(bad)),root,None)

    def test_status_respects_explicit_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target=dict(workspace=str(root/'design'),library='lib',cell='cell')
            atomic_json(root/'task.json',{'kind':'single_schematic_task','task_directory':str(root),
                'target':target,'revision':1,'status':'ready'})
            with self.assertRaisesRegex(ValueError,'TASK_IDENTITY_MISMATCH'):
                run(dict(operation='status',target={**target,'cell':'wrong'}),root,None)


@unittest.skipUnless(os.name=='nt','Native Windows notifications')
class NativeWatchTests(unittest.TestCase):
    def test_same_process_reuse_then_write_create_rename_delete_invalidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);p=r/'model.dat';p.write_text('one');memo=Memo();calls=[]
            def compute():calls.append(1);return sorted((x.name,x.read_text()) for x in r.iterdir() if x.is_file())
            try:
                original=memo.get([r],compute);self.assertEqual(memo.get([r],compute),original);self.assertEqual(len(calls),1)
                operations=[lambda:p.write_text('two'),lambda:(r/'new.dat').write_text('new'),
                            lambda:(r/'new.dat').rename(r/'renamed.dat'),lambda:(r/'renamed.dat').unlink()]
                for operation in operations:
                    old=len(calls);operation();memo.get([r],compute);self.assertEqual(len(calls),old+1)
                self.assertNotEqual(memo.get([r],compute),original)
            finally:memo.close()
    def test_changes_during_fingerprint_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);memo=Memo()
            def race():(r/'new').write_text('changed');return 'stale'
            try:
                with self.assertRaisesRegex(RuntimeError,'ENVIRONMENT_CHANGED_DURING_READ'):memo.get([r],race)
                self.assertFalse(memo.entries)
            finally:memo.close()
    def test_unavailable_handle_and_unwatchable_tree_do_not_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            memo=Memo();calls=[]
            def calc():calls.append(1);return 42
            with patch('environment_watch.Watch',side_effect=OSError('unavailable')):
                self.assertEqual(memo.get([tmp],calc),42);memo.get([tmp],calc)
            self.assertEqual(len(calls),2)
            memo.get([tmp],calc,lambda:False);memo.get([tmp],calc,lambda:False)
            self.assertEqual(len(calls),4);self.assertFalse(memo.entries)
    def test_root_replacement_never_reuses_old_directory_handle(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp);root=parent/'models';root.mkdir();memo=Memo()
            try:
                memo.get([root],lambda:1)
                root.rename(parent/'old');root.mkdir()
                self.assertEqual(memo.get([root],lambda:2),2)
            finally:memo.close()


if __name__=='__main__':unittest.main()
