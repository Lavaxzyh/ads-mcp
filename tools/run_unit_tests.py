"""Run pure unit tests from this checkout, independently of the shell cwd."""
import json,sys,unittest
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
suite=unittest.defaultTestLoader.discover(str(root/'tests'),pattern='*unit.py')
result=unittest.TextTestRunner(verbosity=1).run(suite)
print(json.dumps({'tests_run':result.testsRun,'passed':result.wasSuccessful()}))
sys.exit(0 if result.wasSuccessful() else 1)
