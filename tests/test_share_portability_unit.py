"""The share branch never needs a contributor's machine-specific install path."""
import ast,os,unittest
from pathlib import Path
from unittest.mock import patch
import ads_engine

class SharePortability(unittest.TestCase):
    def test_default_branch_targets_ads2025(self):
        self.assertEqual(Path(ads_engine.DEFAULT_HPEESOF_DIR).name,'ADS2025')
    def test_configured_installation_still_accepts_other_versions(self):
        expected=Path.cwd()/'explicit ADS2027'
        with patch.dict(os.environ,{'HPEESOF_DIR':str(expected)}):
            self.assertEqual(ads_engine.get_ads_install_dir(),expected)
    def test_clients_use_the_shared_installation_resolver(self):
        root=Path(__file__).resolve().parents[1]
        for name in ('design_workflow_client.py','rf_workflow_client.py','schematic_workflow_client.py'):
            tree=ast.parse((root/name).read_text(encoding='utf-8'))
            calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
                   and n.func.attr=='add_argument' and any(isinstance(a,ast.Constant) and a.value=='--ads-root' for a in n.args)]
            self.assertEqual(len(calls),1,name)
            default=next(k.value for k in calls[0].keywords if k.arg=='default')
            self.assertIn('get_ads_install_dir()',ast.unparse(default),name)
