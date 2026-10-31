"""Workspace ownership and existing GUI-call compatibility, without ADS."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ads_gui
import native_tools
from session_state import SessionState
from workspace_activity import activity_path, edit_guard, release, reserve


def edit_probe(arguments):
    """Stand in for a native edit after real bridge preflight checks."""
    from keysight.ads import de
    de.edits.append(arguments)
    return {"saved": True}


class Registry:
    def __init__(self):
        self.functions = {}

    def tool(self):
        def register(fn):
            self.functions[fn.__name__] = fn
            return fn
        return register


class NativeGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ws = self.root / "work_wrk"
        self.ws.mkdir()
        self.engine = SimpleNamespace(workspace_path=None, _op=contextlib.nullcontext)
        self.state = SessionState(self.root / "client_b.json")
        registry = Registry()
        native_tools.register(registry, self.engine, self.state)
        self.tools = registry.functions

    def edit(self, **kwargs):
        return self.tools["ads_set_instance_parameters"](
            "lib", "cell", "R1", {"R": "50 Ohm"}, **kwargs)

    def reservation(self):
        reserve(self.ws, "client_a_solver", "owner", self.root / "client_a.json", 123)

    def test_other_client_reservation_blocks_headless_and_explicit_gui_edits(self):
        self.reservation()
        for sid in (None, 2027):
            with self.subTest(session_id=sid), patch.object(native_tools, "call_native") as native:
                with self.assertRaisesRegex(RuntimeError, "client_a_solver"):
                    self.edit(session_id=sid, workspace=str(self.ws))
                native.assert_not_called()
        self.assertTrue(activity_path(self.ws).is_file())

    def test_engine_workspace_is_also_guarded(self):
        self.engine.workspace_path = str(self.ws)
        self.reservation()
        with patch.object(native_tools, "call_native") as native:
            with self.assertRaisesRegex(RuntimeError, "client_a_solver"):
                self.edit()
            native.assert_not_called()

    def test_discovered_workspace_is_guarded_for_headless_and_gui(self):
        self.reservation()
        for sid in (None, 2027):
            with self.subTest(session_id=sid), patch.object(
                native_tools, "call_native", return_value={"workspace": str(self.ws)}
            ) as native:
                with self.assertRaisesRegex(RuntimeError, "client_a_solver"):
                    self.edit(session_id=sid)
                native.assert_called_once_with(ads_gui.active_workspace, {}, sid)

    def test_guard_is_held_during_dispatch_and_released_afterward(self):
        for sid in (None, 2027):
            def dispatch(*args, **kwargs):
                with self.assertRaisesRegex(RuntimeError, "Another MCP operation"):
                    with edit_guard(self.ws):
                        self.fail("Concurrent edit entered the workspace")
                return {"saved": True}
            with self.subTest(session_id=sid), patch.object(native_tools, "call_native", side_effect=dispatch):
                self.assertTrue(self.edit(session_id=sid, workspace=str(self.ws))["saved"])
            with edit_guard(self.ws):
                pass

    def test_edit_is_allowed_after_original_owner_releases_reservation(self):
        self.reservation()
        release(self.ws, "owner")
        with patch.object(native_tools, "call_native", return_value={"saved": True}) as native:
            self.assertTrue(self.edit(workspace=str(self.ws))["saved"])
            native.assert_called_once()

    def test_exception_releases_edit_lock(self):
        with patch.object(native_tools, "call_native", side_effect=RuntimeError("native failed")):
            with self.assertRaisesRegex(RuntimeError, "native failed"):
                self.edit(workspace=str(self.ws))
        with edit_guard(self.ws):
            pass

    def test_headless_resolution_and_dispatch_both_hold_engine_lock(self):
        inside = []
        @contextlib.contextmanager
        def engine_lock():
            inside.append(True)
            try:
                yield
            finally:
                inside.pop()
        self.engine._op = engine_lock
        def dispatch(operation, arguments, session_id, workspace=""):
            self.assertTrue(inside)
            return {"workspace": str(self.ws)} if operation is ads_gui.active_workspace else {"saved": True}
        with patch.object(native_tools, "call_native", side_effect=dispatch):
            self.assertTrue(self.edit()["saved"])
        self.assertFalse(inside)

    def test_readonly_inspection_remains_available_during_reserved_task(self):
        self.reservation()
        with patch.object(native_tools, "call_native", return_value={"windows": []}) as native:
            self.assertTrue(self.tools["ads_list_windows"](2027)["ok"])
            self.assertEqual(native.call_count, 1)

    def test_gui_rejects_an_already_owned_headless_workspace(self):
        self.engine.workspace_path = str(self.ws)
        with patch.object(native_tools, "call_native") as native:
            with self.assertRaisesRegex(RuntimeError, "Close the MCP-owned workspace"):
                self.edit(session_id=2027)
            native.assert_not_called()


class GuiWorkspaceResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name) / "work_wrk"
        self.ws.mkdir()
        self.de = SimpleNamespace(
            edits=[],
            workspace_is_open=Mock(return_value=True),
            active_workspace=Mock(return_value=SimpleNamespace(path=self.ws)))
        modules = {"keysight": SimpleNamespace(), "keysight.ads": SimpleNamespace(de=self.de)}
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)

    def bridge(self, after_execute=lambda: None):
        def call(name, arguments):
            if name == "connect_session":
                return {"connected": True, "mode": "remote"}
            self.assertEqual(name, "execute_python")
            with contextlib.redirect_stdout(io.StringIO()) as output:
                exec(compile(arguments["code"], "<bridge-test>", "exec"), {})
            after_execute()
            return {"mode": "remote", "stdout": output.getvalue()}
        client = Mock()
        client.call.side_effect = call
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        return client

    def tools(self):
        registry = Registry()
        native_tools.register(registry, SimpleNamespace(workspace_path=None, _op=contextlib.nullcontext),
                              SimpleNamespace(snapshot=lambda: {"tasks": {}}))
        return registry.functions

    def test_active_workspace_local_and_no_open_workspace(self):
        self.assertEqual(ads_gui.call_native(ads_gui.active_workspace, {}), {"workspace": str(self.ws.resolve())})
        self.de.workspace_is_open.return_value = False
        with self.assertRaisesRegex(RuntimeError, "Open a workspace"):
            ads_gui.call_native(ads_gui.active_workspace, {})

    def test_omitted_workspace_works_with_official_and_managed_transports(self):
        # Execute the actual serialized read and workspace-check code, replacing
        # only transport I/O and the design-edit operation.
        for transport in ("official", "managed"):
            client = self.bridge()
            if transport == "official":
                transport_patch = patch("momentum.OfficialAdsClient", return_value=client)
            else:
                transport_patch = patch("gui_bridge_2025.ManagedAdsClient", return_value=client)
            with self.subTest(transport=transport), transport_patch, patch(
                "ads_engine.get_ads_install_dir", return_value=Path(self.tmp.name) / "no_official_bridge"
            ), patch.object(native_tools, "design_operation", edit_probe):
                result = self.tools()["ads_set_instance_parameters"]("lib", "cell", "R1", {}, session_id=2027)
                self.assertTrue(result["saved"])
                self.assertEqual(len(self.de.edits), 1)
                self.de.edits.clear()
                executes = [c for c in client.call.call_args_list if c.args[0] == "execute_python"]
                self.assertEqual(len(executes), 2)
                self.assertIn('if _request["workspace"]:', executes[1].args[1]["code"])

    def test_workspace_switch_between_discovery_and_edit_is_rejected(self):
        other = self.ws.parent / "other_wrk"
        def switch():
            self.de.active_workspace.return_value = SimpleNamespace(path=other)
        client = self.bridge(switch)
        with patch("momentum.OfficialAdsClient", return_value=client), patch.object(
            native_tools, "design_operation", edit_probe
        ), patch.object(self.de, "workspace_is_open", wraps=self.de.workspace_is_open) as opened:
            with self.assertRaisesRegex(RuntimeError, "another workspace"):
                self.tools()["ads_set_instance_parameters"]("lib", "cell", "R1", {}, session_id=2027)
            # Only discovery and the second request's preflight reach DE; the
            # operation body in the second request is never entered.
            self.assertEqual(opened.call_count, 2)
            self.assertEqual(self.de.edits, [])
        with edit_guard(self.ws):
            pass

    def test_no_open_gui_workspace_never_creates_a_lock_in_the_cwd(self):
        self.de.workspace_is_open.return_value = False
        client = self.bridge()
        with patch("momentum.OfficialAdsClient", return_value=client), patch("workspace_activity.edit_guard") as guard:
            with self.assertRaisesRegex(RuntimeError, "Open a workspace"):
                self.tools()["ads_set_instance_parameters"]("lib", "cell", "R1", {}, session_id=2027)
            guard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
