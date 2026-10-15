"""Momentum dispatch/monitor regression tests; no ADS or license required."""
import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from momentum import MomentumRunner, OfficialAdsClient, inspect_setup
from session_state import SessionState, StateError


class SetupFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ads-mcp-unit-")
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name).resolve()
        (self.ws / "lib.defs").write_text("DEFINE lib lib\n")
        self.cell = self.ws / "lib" / "cell"
        (self.cell / "layout").mkdir(parents=True)
        (self.cell / "layout" / "layout.oa").write_bytes(b"fixture")
        setup = self.cell / "em%Setup"
        setup.mkdir()
        (setup / "eesof_em_setup.file").touch()
        self.xml = setup / "emStateFile.xml"
        self.root = ET.Element("EmSimSetup")
        self.main = ET.SubElement(self.root, "MainWindow")
        ET.SubElement(self.main, "simCalculationType").text = "5"
        layout = ET.SubElement(self.main, "MomLayout")
        for tag, value in {"workspaceText": str(self.ws), "libraryText": "lib", "cellText": "cell", "viewText": "layout"}.items():
            ET.SubElement(layout, tag).text = value
        ET.SubElement(ET.SubElement(self.main, "MomSimDataset"), "writeDataset").text = "True"
        ET.SubElement(ET.SubElement(self.main, "MomSimReuseVisualization"), "reusePreviousResults").text = "False"
        plans = ET.SubElement(ET.SubElement(self.main, "MomSimFreqTable"), "freqPlanMap")
        self.plan = ET.SubElement(plans, "MomSimFreqTableDef")
        for tag, value in {"enableFreq": "True", "typeFreq": "1", "startFreq": "0.05", "stopFreq": "4", "stepFreq": "0.01", "ptsFreq": "396",
                           "startFreqUnit": "GHz", "stopFreqUnit": "GHz", "stepFreqUnit": "GHz"}.items():
            ET.SubElement(self.plan, tag).text = value
        self.save()
        self.state = SessionState(self.ws / "state.json")
        self.info = inspect_setup(str(self.ws), str(self.xml))
        self.sim = Path(self.info["simulation_dir"])
        self.sim.mkdir(parents=True)
        self.lock = Path(self.info["run_lock"])
        self.log = self.sim / "proj.log"
        self.ds = self.ws / "data" / "cell_MomUW.ds"
        self.ds.parent.mkdir()
        self.client = Mock()
        self.client.__enter__ = Mock(return_value=self.client)
        self.client.__exit__ = Mock(return_value=False)
        self.client.call.return_value = {"connected": True, "mode": "remote"}
        self.client.execute.side_effect = [{"validated": True}, {"accepted": True}]
        self.runner = MomentumRunner("unused", client_factory=lambda _: self.client)
        self.validate = Mock(return_value={"rows": 396, "frequency_grid_verified": True})

    def save(self):
        ET.ElementTree(self.root).write(self.xml, encoding="utf-8")

    def start(self):
        return self.runner.start(self.state, 123, str(self.ws), str(self.xml))["task_id"]

    def finished_log(self, name="cell_MomUW"):
        self.log.write_text('Momentum MomEngine_64 2027\nSimulation frequency [396] = 4 GHz\n'
                            'S-parameter simulation finished\nSimulation finished on: today\n'
                            f'The simulation results have been saved in dataset "{name}".\n')

    def test_inspect_396_grid(self):
        self.assertEqual(self.info["frequency"], {"start_hz": 5e7, "stop_hz": 4e9, "step_hz": 1e7, "points": 396})

    def test_copied_workspace_is_rejected(self):
        self.main.find("MomLayout/workspaceText").text = str(self.ws / "old")
        self.save()
        with self.assertRaisesRegex(ValueError, "different workspace"):
            inspect_setup(str(self.ws), str(self.xml))

    def test_generated_xml_is_not_native_cellview(self):
        generated = self.sim / "emStateFile.xml"
        generated.write_bytes(self.xml.read_bytes())
        with self.assertRaisesRegex(ValueError, "native EM Setup"):
            inspect_setup(str(self.ws), str(generated))

    def test_bad_sweeps_are_rejected(self):
        for tag, value in [("ptsFreq", "101"), ("startFreq", "nan"), ("stepFreqUnit", "invalid"), ("typeFreq", "0")]:
            node = self.plan.find(tag)
            previous = node.text
            node.text = value
            self.save()
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                inspect_setup(str(self.ws), str(self.xml))
            node.text = previous

    def test_unsafe_setup_name(self):
        with self.assertRaises(ValueError):
            inspect_setup(str(self.ws), str(self.xml), "../emSetup")

    def test_reuse_results_is_rejected(self):
        self.main.find("MomSimReuseVisualization/reusePreviousResults").text = "True"
        self.save()
        with self.assertRaisesRegex(ValueError, "fresh Momentum"):
            inspect_setup(str(self.ws), str(self.xml))

    def test_remote_resources_are_rejected(self):
        resources = ET.SubElement(self.main, "SimulationResources2")
        remote = ET.SubElement(resources, "EEsofPvmResources2")
        ET.SubElement(remote, "simulationHost").text = "elsewhere"
        self.save()
        with self.assertRaisesRegex(ValueError, "local, immediate"):
            inspect_setup(str(self.ws), str(self.xml))

    def test_custom_output_directory_is_rejected(self):
        resources = ET.SubElement(self.main, "SimulationResources2")
        local = ET.SubElement(resources, "DistributedResources2")
        directory = ET.SubElement(local, "SimulationDirectory")
        ET.SubElement(directory, "customPath").text = "C:/another-project"
        self.save()
        with self.assertRaisesRegex(ValueError, "custom simulation"):
            inspect_setup(str(self.ws), str(self.xml))

    def test_disabled_output_plan_is_rejected(self):
        ET.SubElement(ET.SubElement(self.main, "OutputPlan"), "writeDataset").text = "False"
        self.save()
        with self.assertRaisesRegex(ValueError, "dataset output"):
            inspect_setup(str(self.ws), str(self.xml))

    def test_stale_complete_log_does_not_complete_task(self):
        self.finished_log()
        self.ds.write_bytes(b"old")
        tid = self.start()
        self.assertEqual(self.runner.poll(self.state, tid, self.validate)["status"], "running")
        self.validate.assert_not_called()

    def test_fresh_log_without_fresh_dataset_stays_running(self):
        self.ds.write_bytes(b"old")
        tid = self.start()
        self.finished_log()
        result = self.runner.poll(self.state, tid, self.validate)
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["progress"]["current_point"], 396)

    def test_fresh_outputs_finish_and_release_lock(self):
        tid = self.start()
        self.finished_log()
        self.ds.write_bytes(b"new")
        result = self.runner.poll(self.state, tid, self.validate)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["result"]["source_dataset"], str(self.ds))
        self.assertTrue(Path(result["result"]["dataset"]).is_file())
        self.assertFalse(self.lock.exists())

    def test_ads_can_rebuild_simulation_directory_without_losing_reservation(self):
        tid = self.start()
        self.sim.rmdir()
        self.sim.mkdir()
        self.assertTrue(self.lock.exists())
        self.finished_log()
        self.ds.write_bytes(b"new")
        self.assertEqual(self.runner.poll(self.state, tid, self.validate)["status"], "done")

    def test_completed_task_keeps_its_own_dataset_when_native_output_is_overwritten(self):
        tid = self.start()
        self.finished_log()
        self.ds.write_bytes(b"verified run")
        result = self.runner.poll(self.state, tid, self.validate)
        self.ds.write_bytes(b"later unrelated run")
        captured = Path(result["result"]["dataset"])
        self.assertEqual(captured.read_bytes(), b"verified run")

    def test_manual_reservation_release_makes_running_task_error(self):
        tid = self.start()
        self.lock.unlink()
        self.assertEqual(self.runner.poll(self.state, tid, self.validate)["status"], "error")

    def test_incorrect_dataset_grid_is_error(self):
        tid = self.start()
        self.finished_log()
        self.ds.write_bytes(b"new")
        self.validate.side_effect = ValueError("frequency grid mismatch")
        self.assertEqual(self.runner.poll(self.state, tid, self.validate)["status"], "error")

    def test_long_log_keeps_solver_header(self):
        tid = self.start()
        self.finished_log()
        ending = self.log.read_text().split("\n", 1)[1]
        self.log.write_text("Momentum MomEngine_64 2027\n" + "line\n" * 110_000 + ending)
        self.ds.write_bytes(b"new")
        self.assertEqual(self.runner.poll(self.state, tid, self.validate)["status"], "done")

    def test_unsafe_dataset_name_is_error(self):
        tid = self.start()
        self.finished_log("../../outside")
        self.assertEqual(self.runner.poll(self.state, tid, self.validate)["status"], "error")
        self.validate.assert_not_called()

    def test_solver_error_keeps_reservation(self):
        tid = self.start()
        self.log.write_text("Momentum MomEngine_64\nERROR: license unavailable\n")
        self.assertEqual(self.runner.poll(self.state, tid, self.validate)["status"], "error")
        self.assertTrue(self.lock.exists())

    def test_duplicate_dispatch_is_rejected(self):
        self.start()
        with self.assertRaisesRegex(RuntimeError, "reserved"):
            self.start()
        self.assertEqual(self.client.execute.call_count, 2)

    def test_restart_can_finish_external_task_without_relaunch(self):
        tid = self.start()
        restarted = SessionState(self.state.path)
        self.finished_log()
        self.ds.write_bytes(b"new")
        self.assertEqual(self.runner.poll(restarted, tid, self.validate)["status"], "done")
        self.assertEqual(self.client.execute.call_count, 2)

    def test_uncertain_dispatch_not_retried(self):
        self.client.execute.side_effect = [{"validated": True}, TimeoutError("disconnected")]
        result = self.runner.start(self.state, 123, str(self.ws), str(self.xml))
        self.assertTrue(result["dispatch_uncertain"])
        self.assertEqual(self.client.execute.call_count, 2)
        self.assertTrue(self.lock.exists())

    def test_wrong_gui_workspace_fails_before_registration(self):
        self.client.execute.side_effect = RuntimeError("different GUI workspace")
        with self.assertRaises(RuntimeError):
            self.start()
        self.assertEqual(self.state.snapshot()["tasks"], {})

    def test_declined_run_records_error(self):
        self.client.execute.side_effect = [{"validated": True}, {"accepted": False}]
        with self.assertRaisesRegex(RuntimeError, "declined"):
            self.start()
        self.assertFalse(self.lock.exists())
        self.assertEqual(self.state.snapshot()["state"], "error")


class StateTests(unittest.TestCase):
    def test_malformed_task_container_does_not_break_startup(self):
        with tempfile.TemporaryDirectory(prefix="ads-state-unit-") as tmp:
            p = Path(tmp) / "state.json"
            p.write_text(json.dumps({"state": "invalid", "tasks": ["broken"]}))
            state = SessionState(p)
            self.assertEqual(state.snapshot()["tasks"], {})
            self.assertEqual(state.snapshot()["state"], "idle")

    def test_invalid_phase_and_incomplete_task_are_ignored(self):
        with tempfile.TemporaryDirectory(prefix="ads-state-unit-") as tmp:
            p = Path(tmp) / "state.json"
            p.write_text(json.dumps({"state": {}, "tasks": {"broken": {}}}))
            state = SessionState(p)
            self.assertEqual(state.snapshot()["tasks"], {})
            self.assertEqual(state.snapshot()["state"], "idle")

    def test_interrupted_circuit_task_is_not_left_running(self):
        with tempfile.TemporaryDirectory(prefix="ads-state-unit-") as tmp:
            p = Path(tmp) / "state.json"
            p.write_text(json.dumps({"state": "simulating", "tasks": {"old": {"kind": "simulation", "status": "running"}}}))
            state = SessionState(p)
            self.assertEqual(state.task_status("old")["status"], "error")
            self.assertEqual(state.snapshot()["state"], "error")

    def test_external_task_rejects_other_running_task(self):
        with tempfile.TemporaryDirectory(prefix="ads-state-unit-") as tmp:
            state = SessionState(Path(tmp) / "state.json")
            state.create_external_task("momentum", {})
            with self.assertRaises(StateError):
                state.create_external_task("momentum", {})
            with self.assertRaises(StateError):
                state.submit("simulation", lambda: {})


class TransportTests(unittest.TestCase):
    def test_rpc_ignores_notifications_and_matches_id(self):
        client = OfficialAdsClient("unused")
        client.process = SimpleNamespace(stdin=io.StringIO())
        client._messages.put({"method": "notification"})
        client._messages.put({"id": 1, "result": {"good": True}})
        self.assertEqual(client._request("test", {}), {"good": True})

    def test_rpc_timeout_is_explicit(self):
        client = OfficialAdsClient("unused", timeout=0.001)
        client.process = SimpleNamespace(stdin=io.StringIO())
        with self.assertRaisesRegex(TimeoutError, "no retry"):
            client._request("test", {})

    def test_nested_execution_error_is_not_success(self):
        client = OfficialAdsClient("unused")
        client._request = Mock(return_value={"content": [{"type": "text", "text": json.dumps({"ok": False, "stderr": "AEL error"})}]})
        with self.assertRaisesRegex(RuntimeError, "AEL error"):
            client.call("execute_python", {})

    def test_remote_execution_requires_response_marker(self):
        client = OfficialAdsClient("unused")
        client.call = Mock(return_value={"mode": "remote", "stdout": "unrelated chatter"})
        with self.assertRaisesRegex(RuntimeError, "expected Momentum response"):
            client.execute("unused")


if __name__ == "__main__":
    unittest.main()
