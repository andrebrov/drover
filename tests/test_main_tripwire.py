"""A real isolated Git origin and a deterministic project-owned gate adapter."""
import importlib.machinery,importlib.util,json,os
from pathlib import Path
import subprocess,tempfile,time,unittest
ROOT=Path(__file__).resolve().parents[1]
class Tripwire(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.repo=self.root/'repo';self.repo.mkdir()
  self.env={k:v for k,v in os.environ.items() if not k.startswith(('GIT_','DROVER_','FLEET_','TRIPWIRE_'))}
  self.env.update(HOME=str(self.root),DROVER_CONFIG='/nonexistent',DROVER_REPO=str(self.repo),TRIPWIRE_STATE=str(self.root/'probe'),TRIPWIRE_ALERTS=str(self.root/'alerts'),FLEET_REPORTS=str(self.root/'reports'),DROVER_TRIPWIRE_GATE='bash gate.sh')
  (self.root/'reports').mkdir()
  self.git('init','-qb','main');self.git('config','user.name','Fixture');self.git('config','user.email','fixture@example.invalid')
  (self.repo/'gate.sh').write_text('''if [ -n "${INFRA_FAIL:-}" ]; then echo 'fixture unavailable: lease missing'; exit 1; fi
if [ -n "${DELAY_GATE:-}" ]; then touch "$HOME/gate-started"; sleep 1; fi
if [ -f BAD ]; then printf '\\033[31mFAIL\\033[0m tests/sample.test.ts\\n'; exit 1; fi
''')
  self.git('add','.');self.git('commit','-qm','good base (bean-good)');self.good=self.git('rev-parse','HEAD')
  self.git('init','--bare','-q',str(self.root/'origin'));self.git('remote','add','origin',str(self.root/'origin'));self.git('push','-q','origin','HEAD:main')
 def git(self,*args):return subprocess.check_output(['git','-C',str(self.repo),*args],env=self.env,text=True,stderr=subprocess.DEVNULL).strip()
 def invoke(self,*args):return subprocess.run([str(ROOT/'bin/fleet-main-tripwire'),*args],env=self.env,text=True,capture_output=True,timeout=15)
 def state(self):return json.loads((self.root/'probe/state.json').read_text())
 def bad(self):
  (self.repo/'BAD').touch();self.git('add','BAD');self.git('commit','-qm','broken implementation (bean-badd)');sha=self.git('rev-parse','HEAD');self.git('push','-q','origin','HEAD:main');return sha
 def test_green_then_red_names_the_real_code_commit_and_suite(self):
  p=self.invoke();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
  bad=self.bad();(self.root/'reports/coder-bean-badd.md').write_text('DONE '+bad)
  p=self.invoke();self.assertEqual(p.returncode,1,p.stdout+p.stderr)
  state=self.state();self.assertEqual(state['outcome'],'red');self.assertEqual(state['first_bad'],bad)
  alert=(self.root/'alerts/main-red.txt').read_text();self.assertIn('seat coder',alert);self.assertIn('tests/sample.test.ts',alert)
 def test_unavailable_fixture_is_not_red_and_does_not_advance_green(self):
  self.assertEqual(self.invoke().returncode,0)
  self.bad();self.env['INFRA_FAIL']='1';p=self.invoke()
  self.assertEqual(p.returncode,2,p.stdout+p.stderr);self.assertEqual(self.state()['outcome'],'unavailable');self.assertEqual(self.state()['last_green'],self.good)
 def test_changed_environment_failing_at_green_base_is_unavailable_not_blame(self):
  self.assertEqual(self.invoke().returncode,0);self.bad();self.env['DROVER_TRIPWIRE_GATE']='echo FAIL tests/shared.test.ts; exit 1'
  p=self.invoke();self.assertEqual(p.returncode,2,p.stdout+p.stderr);self.assertNotIn('first_bad',self.state())
 def test_result_is_published_for_evaluated_tip_when_main_moves(self):
  self.env['DELAY_GATE']='1';p=subprocess.Popen([str(ROOT/'bin/fleet-main-tripwire')],env=self.env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  try:
   for _ in range(300):
    if (self.root/'gate-started').exists():break
    time.sleep(.01)
   self.assertTrue((self.root/'gate-started').exists())
   (self.repo/'README.md').write_text('advance');self.git('add','README.md');self.git('commit','-qm','metadata advance');tip=self.git('rev-parse','HEAD');self.git('push','-q','origin','HEAD:main')
   out,err=p.communicate(timeout=15);self.assertEqual(p.returncode,0,out+err)
   state=self.state();self.assertEqual(state['evaluated_sha'],self.good);self.assertEqual(state['origin_main_sha'],tip);self.assertEqual(state['commits_behind'],1)
  finally:
   if p.poll() is None:p.terminate();p.communicate(timeout=5)
 def test_stale_evaluation_is_reported_without_touching_it(self):
  self.assertEqual(self.invoke().returncode,0);path=self.root/'probe/state.json';state=self.state();state['evaluated_at']=time.time()-8000;path.write_text(json.dumps(state));before=path.read_bytes();self.bad()
  p=self.invoke('--check-freshness');self.assertEqual(p.returncode,2,p.stdout+p.stderr);self.assertIn('stale',p.stdout);self.assertEqual(path.read_bytes(),before)
 def test_node_and_jest_parser_names_only_failed_files(self):
  loader=importlib.machinery.SourceFileLoader('tripwire',str(ROOT/'bin/fleet-main-tripwire'));spec=importlib.util.spec_from_loader(loader.name,loader);m=importlib.util.module_from_spec(spec);loader.exec_module(m)
  parsed=m.failed_test_files('\x1b[31mFAIL\x1b[0m tests/a.test.ts\nnot ok 1 - broken\n  location: file:///work/tests/b.test.mjs:8:2\nok 2 - good\n  location: /work/tests/good.mjs:1:2\n')
  self.assertEqual(parsed,['tests/a.test.ts','/work/tests/b.test.mjs'])

 def test_bisect_converges_with_two_code_candidates(self):
  self.assertEqual(self.invoke().returncode,0)
  (self.repo/'good-change').write_text('valid');self.git('add','good-change');self.git('commit','-qm','good implementation (bean-next)');self.git('push','-q','origin','HEAD:main')
  bad=self.bad()
  result=subprocess.run([str(ROOT/'bin/fleet-main-tripwire')],env=self.env,text=True,capture_output=True,timeout=5)
  self.assertEqual(result.returncode,1,result.stdout+result.stderr);self.assertEqual(self.state()['first_bad'],bad)
