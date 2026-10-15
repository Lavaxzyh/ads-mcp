"""Tool-surface and dispatch guards, with no ADS process or workspace."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import mcp_server as server
from session_state import SessionState


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ads-server-unit-")
        self.addCleanup(self.tmp.cleanup)
        self.state = SessionState(Path(self.tmp.name) / "state.json")
        self.engine = Mock(workspace_path=None)
        self.runner = Mock()
        for name, value in [("state", self.state), ("engine", self.engine), ("momentum", self.runner)]:
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_latest_completed_dataset_is_selected(self):
        self.state._data["tasks"] = {
            "old": {"status": "done", "finished_at": "2026-01-01", "result": {"dataset": "old.ds"}},
            "new": {"status": "done", "finished_at": "2026-01-02", "result": {"dataset": "new.ds"}},
        }
        self.assertEqual(server._last_dataset_from_tasks(), "new.ds")

    def test_same_second_completion_prefers_later_task(self):
        self.state._data["tasks"] = {
            "old": {"status": "done", "finished_at": "same", "result": {"dataset": "old.ds"}},
            "new": {"status": "done", "finished_at": "same", "result": {"dataset": "new.ds"}},
        }
        self.assertEqual(server._last_dataset_from_tasks(), "new.ds")

    def test_workspace_cannot_switch_during_external_run(self):
        self.state.create_external_task("momentum", {})
        self.assertFalse(server.ads_open_workspace("other")["ok"])
        self.engine.open_workspace.assert_not_called()

    def test_circuit_cannot_start_during_external_run(self):
        self.state.create_external_task("momentum", {})
        self.assertFalse(server.ads_run_simulation()["ok"])
        self.engine.save_schematic.assert_not_called()

    def test_momentum_rejects_server_owned_workspace(self):
        self.engine.workspace_path = "in-use"
        self.assertFalse(server.ads_run_momentum(1, "workspace", "setup.xml")["ok"])
        self.runner.start.assert_not_called()

    def test_poll_does_not_expose_monitor_baselines(self):
        tid = self.state.create_external_task("momentum", {"baseline": "private"})
        self.runner.poll.return_value = self.state.task_status(tid)
        result = server.ads_get_sim_status(tid)
        self.assertTrue(result["ok"])
        self.assertNotIn("monitor", result)


class SchemaTests(unittest.IsolatedAsyncioTestCase):
    async def test_28_tools_and_momentum_signature(self):
        tools = await server.mcp.list_tools()
        names = {tool.name for tool in tools}
        self.assertEqual(len(names), 28)
        self.assertTrue({"ads_list_gui_sessions", "ads_inspect_momentum_setup", "ads_run_momentum"} <= names)
        tool = next(tool for tool in tools if tool.name == "ads_run_momentum")
        self.assertEqual(set(tool.inputSchema["required"]), {"session_id", "workspace", "setup_file"})
        self.assertEqual(server.mcp._mcp_server.version, "0.3.0")


if __name__ == "__main__":
    unittest.main()
