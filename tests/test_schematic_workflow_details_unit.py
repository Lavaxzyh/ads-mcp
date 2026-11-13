import tempfile
import unittest
from pathlib import Path

from edit_requests import editable_fields
from workspace_diagnostics import inspect, first_solver_error


class SchematicWorkflowDetails(unittest.TestCase):
    def test_scientific_notation_and_variable_expressions(self):
        snapshot = {"components": [{"name": "VAR1", "cell": "VAR", "library": "ads_simulation",
                                    "parameters": {}, "variables": {"gain": "1e3", "length": "2.5e-3 mm",
                                                                   "derived": "2*length"}}]}
        fields = {row["key"]: row for row in editable_fields(snapshot, {}, "VAR1")}
        self.assertIsNone(fields["gain"]["unit"])
        self.assertEqual(fields["length"]["unit"], "mm")
        self.assertIsNone(fields["derived"]["unit"])

    def test_nested_library_and_literal_data_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root/"lib.defs").write_text("INCLUDE nested.defs\n", encoding="utf-8")
            (root/"nested.defs").write_text("INCLUDE lib.defs\nDEFINE p missing_lib\n", encoding="utf-8")
            net = root/"design.net"
            net.write_text('; .include "ignore.lib"\nS2P:X1 n1 n2 File="absent.s2p"\n', encoding="utf-8")
            result = inspect(str(root), str(net))
            first, second = result["issues"]
            self.assertEqual((first["source"], first["line"], first["library"]), (str(root/"nested.defs"), 2, "p"))
            self.assertEqual((second["line"], second["instance"]), (2, "X1"))
            self.assertIn("next_check", result["first_error"])

    def test_solver_error_skips_zero_error_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory)/"simulator.log"
            log.write_text("Error count: 0\nError detected by hpeesofsim\nR1 is undefined\nFatal: later\n", encoding="utf-8")
            issue = first_solver_error(log)
            self.assertEqual((issue["line"], issue["detail"]), (2, "R1 is undefined"))
