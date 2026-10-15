"""Portable path configuration without an ADS installation or license."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ads_engine


class PathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ads-paths-unit-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_configured_ads_installation_takes_precedence(self):
        install = self.root / "custom ADS"
        with patch.dict(os.environ, {"HPEESOF_DIR": str(install)}):
            self.assertEqual(ads_engine.get_ads_install_dir(), install)
        self.assertFalse(install.exists())

    def test_empty_ads_setting_uses_standard_installation(self):
        install = self.root / "Program Files" / "Keysight" / "ADS2027"
        with patch.dict(os.environ, {"HPEESOF_DIR": ""}), patch.object(
            ads_engine, "DEFAULT_HPEESOF_DIR", str(install)
        ):
            self.assertEqual(ads_engine.get_ads_install_dir(), install)

    def test_ads_installation_expands_home(self):
        with patch.dict(os.environ, {"HPEESOF_DIR": "~/ads-path-unit-install"}):
            self.assertEqual(
                ads_engine.get_ads_install_dir(),
                (Path.home() / "ads-path-unit-install").resolve(),
            )

    def test_workroot_defaults_to_current_home(self):
        with patch.dict(os.environ, {"ADS_MCP_WORKROOT": ""}), patch.object(
            Path, "home", return_value=self.root
        ):
            self.assertEqual(ads_engine.get_workroot(), self.root / "ads_mcp_demo")
        self.assertFalse((self.root / "ads_mcp_demo").exists())

    def test_configured_workroot_is_resolved(self):
        workroot = self.root / "custom output"
        with patch.dict(os.environ, {"ADS_MCP_WORKROOT": str(workroot / "child" / "..")}):
            self.assertEqual(ads_engine.get_workroot(), workroot)
        self.assertFalse(workroot.exists())

    def test_workroot_expands_home(self):
        with patch.dict(os.environ, {"ADS_MCP_WORKROOT": "~/ads-path-unit-output"}):
            self.assertEqual(
                ads_engine.get_workroot(),
                (Path.home() / "ads-path-unit-output").resolve(),
            )

    def test_ensure_env_validates_and_normalizes_installation(self):
        install = self.root / "ADS"
        (install / "bin").mkdir(parents=True)
        (install / "bin" / "hpeesofsim.exe").touch()
        with patch.dict(os.environ, {"HPEESOF_DIR": str(install / "bin" / "..")}):
            self.assertEqual(ads_engine.ensure_env(), str(install))
            self.assertEqual(os.environ["HPEESOF_DIR"], str(install))

    def test_invalid_installation_has_configuration_hint(self):
        install = self.root / "missing"
        with patch.dict(os.environ, {"HPEESOF_DIR": str(install)}):
            with self.assertRaisesRegex(FileNotFoundError, "set HPEESOF_DIR"):
                ads_engine.ensure_env()
        self.assertFalse(install.exists())

    def test_component_browsing_uses_configured_installation(self):
        install = self.root / "ADS"
        (install / "oalibs" / "rf" / "test_lib" / "test_cell").mkdir(parents=True)
        with patch.dict(os.environ, {"HPEESOF_DIR": str(install)}):
            engine = ads_engine.AdsEngine()
            self.assertEqual(engine.browse_components("test_lib")["cells"], ["test_cell"])
            self.assertEqual(engine.browse_components()["libraries"]["test_lib"], ["test_cell"])

    def test_gui_uses_ads_launcher_and_workspace_working_directory(self):
        install = self.root / "ADS"
        (install / "bin").mkdir(parents=True)
        (install / "bin" / "ads.exe").touch()
        workspace = self.root / "workspace"
        workspace.mkdir()
        with patch.object(ads_engine, "ensure_env", return_value=str(install)), patch.object(
            ads_engine.subprocess, "Popen"
        ) as launch:
            launch.return_value.pid = 123
            result = ads_engine.AdsEngine().open_in_gui(str(workspace))
        self.assertEqual(launch.call_args.args[0], [str(install / "bin" / "ads.exe")])
        self.assertEqual(launch.call_args.kwargs["cwd"], str(workspace))
        self.assertEqual(result["gui_pid"], 123)


if __name__ == "__main__":
    unittest.main()
