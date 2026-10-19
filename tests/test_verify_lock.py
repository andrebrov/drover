"""A verifier never steals an incompletely published legacy lock or overlaps a live gate."""
from pathlib import Path
import os,subprocess,tempfile,time,unittest
ROOT=Path(__file__).resolve().parents[1]
class VerifyLock(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.repo=self.root/'repo';self.repo.mkdir()
  self.env={k:v for k,v in os.environ.items() if not k.startswith(('GIT_','FLEET_','DROVER_'))}
  self.env.update(HOME=str(self.root),DROVER_CONFIG='/nonexistent',DROVER_REPO=str(self.repo),FLEET_STATE=str(self.root/'state'),DROVER_BUILD='touch "$HOME/started"; sleep 2',DROVER_CLONE_DIRS='')
  for args in [('init','-qb','main'),('config','user.name','Fixture'),('config','user.email','fixture@example.invalid'),('commit','--allow-empty','-qm','fixture')]: subprocess.run(['git','-C',str(self.repo),*args],env=self.env,check=True,capture_output=True)
 def invoke(self):return subprocess.run([str(ROOT/'bin/fleet-verify-main')],env=self.env,text=True,capture_output=True,timeout=10)
 def test_unknown_legacy_owner_refuses_before_build(self):
  lock=self.root/'state/verify-wt/.gate-lock';lock.mkdir(parents=True)
  p=self.invoke();self.assertEqual(p.returncode,3,p.stdout+p.stderr);self.assertFalse((self.root/'started').exists())
 def test_live_gate_refuses_sibling_and_exit_releases(self):
  first=subprocess.Popen([str(ROOT/'bin/fleet-verify-main')],env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  try:
   for _ in range(200):
    if (self.root/'started').exists():break
    time.sleep(.02)
   self.assertTrue((self.root/'started').exists())
   second=self.invoke();self.assertEqual(second.returncode,3,second.stdout+second.stderr)
   out,err=first.communicate(timeout=10);self.assertEqual(first.returncode,0,out+err)
   third=self.invoke();self.assertEqual(third.returncode,0,third.stdout+third.stderr)
  finally:
   if first.poll() is None:first.terminate();first.communicate(timeout=5)
