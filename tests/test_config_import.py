"""Importing an observer for probes must not poison the caller's later fixture environments."""
import os
from pathlib import Path
import subprocess,sys,unittest
ROOT=Path(__file__).resolve().parents[1]
class ConfigImport(unittest.TestCase):
 def test_library_import_leaves_environment_unchanged(self):
  env={k:v for k,v in os.environ.items() if not k.startswith(('DROVER_','FLEET_','GIT_'))}
  env['DROVER_CONFIG']='/nonexistent'
  script='''import importlib.machinery, importlib.util, os, sys
before=dict(os.environ)
loader=importlib.machinery.SourceFileLoader('observer',sys.argv[1]);spec=importlib.util.spec_from_loader(loader.name,loader);m=importlib.util.module_from_spec(spec);loader.exec_module(m)
assert dict(os.environ)==before, sorted(set(os.environ)-set(before))
'''
  result=subprocess.run([sys.executable,'-c',script,str(ROOT/'bin/fleet-observe')],env=env,capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stdout+result.stderr)
