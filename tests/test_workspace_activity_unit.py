import tempfile,unittest
from pathlib import Path
from workspace_activity import edit_guard,reserve,release,assert_idle

class WorkspaceActivityTests(unittest.TestCase):
    def test_second_client_cannot_edit_or_dispatch_while_native_task_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws=Path(tmp)
            with edit_guard(ws):reserve(ws,'original','secret',ws/'state.json',123)
            with self.assertRaisesRegex(RuntimeError,'original'):
                with edit_guard(ws):self.fail('Must not enter')
            with self.assertRaisesRegex(RuntimeError,'another native task'):release(ws,'wrong')
            with self.assertRaises(RuntimeError):assert_idle(ws)
            release(ws,'secret')
            with edit_guard(ws):pass
