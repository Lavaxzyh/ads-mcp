import tempfile
import unittest
from pathlib import Path

from runtime_identity import RuntimeIdentity


class RuntimeIdentityTests(unittest.TestCase):
    def test_loaded_identity_survives_update_but_reports_stale_process(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'version.py'
            source.write_text('old', encoding='utf-8')
            running = RuntimeIdentity(root)
            before = running.report(['ads_a'])
            source.write_text('new', encoding='utf-8')
            report = running.report(['ads_a'])
            self.assertEqual(before['build_id'], report['build_id'])
            self.assertTrue(report['fresh_server_required'])
            self.assertEqual(report['files_changed_since_start'], ['version.py'])
            self.assertNotEqual(report['loaded_code_sha256'], report['disk_code_sha256'])

    def test_unknown_catalog_is_not_claimed_current(self):
        with tempfile.TemporaryDirectory() as d:
            report = RuntimeIdentity(Path(d)).report(['ads_a'])
            self.assertIsNone(report['tool_catalog']['client_refresh_required'])

    def test_catalog_filter_is_distinguished_from_missing_new_tool(self):
        with tempfile.TemporaryDirectory() as d:
            runtime = RuntimeIdentity(Path(d))
            report = runtime.report(['ads_a', 'ads_gui', 'ads_new'],
                client_tool_names=['mcp__ads_mcp__ads_a'], disabled_tools=['ads_gui'])
            self.assertEqual(report['tool_catalog']['missing_from_client'], ['ads_new'])
            self.assertTrue(report['tool_catalog']['client_refresh_required'])


if __name__ == '__main__':
    unittest.main()
