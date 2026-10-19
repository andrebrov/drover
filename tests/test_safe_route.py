#!/usr/bin/env python3
"""Public-entry behavioral tests; every state/account boundary is a durable isolated fixture."""
import tempfile
import os,sys,json,hashlib,shutil,subprocess,time,unittest,base64
from pathlib import Path
D=Path(__file__).resolve().parents[1]
SOURCE=D/'bin'
OUT=Path(tempfile.mkdtemp(prefix='safe-route-')).resolve(); OUT.mkdir(parents=True,exist_ok=True)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,s,executable=False):
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s);p.chmod(0o700 if executable else 0o600)
class Routing(unittest.TestCase):
 def setUp(self):
  self.p=OUT/self._testMethodName; self.p.mkdir()
  self.home=self.p/'home';self.bin=self.home/'.local/bin';self.st=self.home/'.local/share/fleet-watch/state';self.r=self.st/'safe-route';self.tasks=self.home/'.local/share/fleet-tasks';self.inbox=self.home/'.local/share/fleet-inbox';self.reports=self.home/'.local/share/fleet-reports';self.briefs=self.home/'.local/share/fleet-briefs';self.repo=self.home/'IdeaProjects/project'
  for p in [self.bin,self.r/'queue',self.r/'bootstrap',self.tasks,self.inbox,self.reports,self.briefs,self.repo]:p.mkdir(parents=True,exist_ok=True)
  self.env=dict(os.environ,HOME=str(self.home),PATH=str(self.bin)+':/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin',SAFE_ROOT=str(self.r),FLEET_STATE=str(self.st),FLEET_TASKS=str(self.tasks),FLEET_REPORTS=str(self.reports),FLEET_INBOX=str(self.inbox),FLEET_REPO=str(self.repo),HERDR=str(self.bin/'herdr'),FLEET_TRACK=str(self.bin/'fleet-track'),SAFE_MAX_PER_TICK='2',SAFE_PROMPT_BOUND='1')
  self.env.update(DROVER_REPO=str(self.repo), DROVER_BIN=str(self.bin), DROVER_SRC=str(D), FLEET_BRIEFS=str(self.briefs))
  self.env.pop('GIT_DIR',None);self.env.pop('GIT_WORK_TREE',None)
  self.run_git('init','-q');self.run_git('config','user.email','fixture@invalid');self.run_git('config','user.name','Fixture');write(self.repo/'source.txt','baseline\n');self.run_git('add','source.txt');self.run_git('-c','core.hooksPath=/dev/null','commit','-qm','fixture baseline');self.base=self.run_git('rev-parse','HEAD').stdout.strip();self.branch=self.run_git('branch','--show-current').stdout.strip()
  for n in ['fleet-budget','fleet-track']:
   origin=D/'tests/fixtures/fleet-budget' if n=='fleet-budget' else SOURCE/n
   shutil.copy2(origin,self.bin/n)
  shutil.copy2(SOURCE/'fleet-safe-route',self.bin/'fleet-safe-route');(self.bin/'fleet-safe-route').chmod(0o700)
  shutil.copy2(SOURCE/'drover-env',self.bin/'drover-env')
  shutil.copytree(D/'lib', self.home/'.local/lib', dirs_exist_ok=True)
  shutil.copy2(SOURCE/'drover-env',self.p/'drover-env')
  # Real watcher entry with only the OS sysctl dependency replaced in its isolated copy.
  watch=Path(os.environ.get('WATCH_SOURCE',str(SOURCE/'fleet-watch'))).read_text().replace('/usr/bin/sysctl',str(self.bin/'sysctl')).replace('/usr/sbin/sysctl',str(self.bin/'sysctl'))
  write(self.bin/'watcher',watch,True)
  write(self.st/'observer','');write(self.r/'enabled','');write(self.r/'generation','gen1\n');write(self.r/'activation',str(int(time.time())-2)+'\n')
  write(self.p/'agents.json',json.dumps({'result':{'agents':[{'name':a,'agent_status':'idle'} for a in ['opencode-coder','grok-reviewer','agy-reviewer']]}}))
  write(self.p/'pressure','1\n');write(self.p/'prompt-mode','ack\n')
  for n in ['fleet-review-check','fleet-stale-worktree','fleet-landed-check','fleet-completed-audit','fleet-alert']:
   write(self.bin/n,'#!/bin/bash\nprintf "%s %s\\n" "'+n+'" "$*" >> "'+str(self.p/'calls')+'"\nexit 0\n',True)
  write(self.bin/'fleet-observe','#!/bin/bash\nprintf "observe %s\\n" "$*" >> "'+str(self.p/'calls')+'"\nexit 0\n',True)
  write(self.bin/'fleet','#!/bin/bash\ncase "$1" in status) printf "NAME KIND STATE\\nopencode-coder opencode idle\\ngrok-reviewer grok idle\\nagy-reviewer agy idle\\n";; read) [ ! -f "'+str(self.p/'budget-fail')+'" ] || exit 1; printf "Ready for assigned task\\n";; *) exit 1;; esac\n',True)
  write(self.bin/'sysctl','#!/bin/bash\nprintf "%s\\n" "$*" >> "'+str(self.p/'sysctl.calls')+'"\n[ "$*" = "-n kern.memorystatus_vm_pressure_level" ] || exit 1\n[ ! -f "'+str(self.p/'pressure-fail')+'" ] || exit 1\ncat "'+str(self.p/'pressure')+'"\n',True)
  write(self.bin/'herdr','''#!/usr/bin/env python3
import sys,json,time
from pathlib import Path
p=Path('''+repr(str(self.p))+''')
if sys.argv[1:3]==['agent','list']:print((p/'agents.json').read_text());sys.exit(0)
if sys.argv[1:3]==['agent','get']:
 print(json.dumps({'result':{'agent':dict(name=sys.argv[3],agent='codex',agent_status='idle',pane_id='w1:p'+sys.argv[3],terminal_id='term'+sys.argv[3],cwd=str(p/'repo'),state_change_seq=1,interactive_ready=True)}}));sys.exit(0)
if sys.argv[1:3]==['agent','explain']:
 print(json.dumps(dict(state='idle',visible_blocker=False,skip_state_update=False)));sys.exit(0)
if sys.argv[1:3]==['agent','read']:
 print((p/'screen').read_text() if (p/'screen').exists() else '›\\n ? for shortcuts');sys.exit(0)
if sys.argv[1:3]!=['agent','prompt']:sys.exit(2)
with (p/'prompts').open('a') as f:f.write(json.dumps(sys.argv[3:])+'\\n')
mode=(p/'prompt-mode').read_text().strip()
if mode=='timeout':print(json.dumps({'error':{'code':'timeout','message':'ack timeout'}}));sys.exit(1)
if mode=='outer':time.sleep(3);sys.exit(0)
if mode=='fail':print(json.dumps({'error':{'code':'missing','message':'unknown agent'}}));sys.exit(1)
name=sys.argv[3].removeprefix('w1:p')
print(json.dumps({'result':{'agent':dict(name=name,agent='codex',agent_status='working',pane_id=sys.argv[3],terminal_id='term'+name,cwd=str(p/'repo'),state_change_seq=2,interactive_ready=True)}}))
''',True)
 def run_git(self,*a):return subprocess.run(['/usr/bin/git','--git-dir='+str(self.repo/'.git'),'--work-tree='+str(self.repo),*a],text=True,capture_output=True,check=True,env=self.env)
 def call(self,*a,watch=False,check=True):
  cmd=['/bin/bash',str(self.bin/'watcher'),'once'] if watch else [str(self.bin/'fleet-safe-route'),*a]
  r=subprocess.run(cmd,env=self.env,text=True,capture_output=True,timeout=20)
  with (self.p/'commands.jsonl').open('a') as f:f.write(json.dumps({'argv':cmd,'rc':r.returncode,'stdout':r.stdout,'stderr':r.stderr})+'\n')
  if check:self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  return r
 def job(self,id='j1',a='opencode-coder',bean='bean-uhrt',role='coder',mode='bootstrap',after='-',ash='-'):
  brief=self.briefs/(id+'.md');write(brief,'Read this assigned fixture only.\n');report=self.reports/(id+'.md')
  fields=[a,bean,role,str(report),self.branch,str(brief),sha(brief),mode,str(after),ash,'gen1'];j=self.r/'queue'/(id+'.job');write(j,'\t'.join(fields)+'\n')
  grant={'schema':1,'job_id':id,'job_sha256':sha(j),'generation':'gen1','agent':a,'bean':bean,'role':role,'source_sha':self.base,'bean_status':'in-progress','approved_at':int(time.time()),'expires_at':int(time.time())+3600,'expected_task_sha256':None,'reviewed_harness':'opencode' if role=='reviewer' else None}
  write(self.r/'grants'/(id+'.json'),json.dumps(grant));write(self.r/'bootstrap'/(id+'.ack'),json.dumps(grant))
  return j,report
 def count(self):return len((self.p/'prompts').read_text().splitlines()) if (self.p/'prompts').exists() else 0
 def dispatch(self,**kw):
  j,r=self.job(**kw);self.call(watch=True);self.assertEqual(self.count(),1);return j,r
 def announce(self,report,ts=None,state='DONE',a='opencode-coder',bean='bean-uhrt',name=None):
  ts=int(time.time()) if ts is None else ts; m=self.inbox/(name or f'{ts}-{a}-{bean}.msg');write(m,f'{ts}\t{a}\t{bean}\t{state}\tfixture done\t{report}\n');return m
 def finish(self,report):write(report,'Verified fixture report\n');return self.announce(report)
 def test_idle_roster_with_draft_never_prompts_or_reserves(self):
  self.job();write(self.p/'screen','› protect this draft\n ? for shortcuts\n');self.call(watch=True)
  self.assertEqual(self.count(),0);self.assertFalse((self.r/'reserved/opencode-coder').exists());self.assertIn('HOLD delivery opencode-coder draft_present',(self.r/'log').read_text())
 def test_actual_watch_budget_dispatch_once(self):
  self.job();self.call(watch=True);self.call(watch=True);self.assertEqual(self.count(),1);self.assertIn('opencode-coder opencode idle OK',(self.st/'budget-seat').read_text());self.assertIn('-n kern.memorystatus_vm_pressure_level',(self.p/'sysctl.calls').read_text())
 def test_actual_watch_unknown_memory_holds(self):
  self.job();write(self.p/'pressure-fail','');self.call(watch=True);self.assertEqual(self.count(),0);self.assertTrue((self.st/'memory-pressure').exists());self.assertLessEqual(abs(int((self.st/'memory-status').read_text().splitlines()[0])-int(time.time())),3)
 def test_actual_watch_warning_and_critical_hold_reviewers(self):
  self.job(a='grok-reviewer',role='reviewer');write(self.p/'pressure','2\n');self.call(watch=True);self.assertEqual(self.count(),0);write(self.p/'pressure','4\n');self.call(watch=True);self.assertEqual(self.count(),0)
 def test_pause_holds_reviewer_keeps_alerts(self):
  self.job(a='grok-reviewer',role='reviewer');write(self.st/'land-paused','founder pause');self.call(watch=True);self.assertEqual(self.count(),0);self.assertIn('fleet-review-check',(self.p/'calls').read_text());self.assertTrue((self.st/'observer').exists())
 def test_manual_evidence_cannot_complete(self):
  j,r=self.job();write(r,'arbitrary\n');ret=self.call('complete','j1',str(r),check=False);self.assertNotEqual(ret.returncode,0);self.assertTrue(j.exists())
 def test_two_jobs_atomic_task_transition(self):
  j,r=self.dispatch();old=(self.tasks/'opencode-coder').read_bytes();j2,r2=self.job('j2',bean='bean-next',mode='completed',after=r,ash='-');self.finish(r)
  # The root-authorized successor pins the actual report bytes before dispatch.
  f=j2.read_text().rstrip('\n').split('\t');f[9]=sha(r);write(j2,'\t'.join(f)+'\n');g=json.loads((self.r/'grants/j2.json').read_text());g['job_sha256']=sha(j2);write(self.r/'grants/j2.json',json.dumps(g))
  self.call(watch=True);self.assertEqual(self.count(),2);self.assertFalse(j.exists());self.assertEqual((self.tasks/'opencode-coder').read_text().split('\t')[1],'bean-next');c=json.loads((self.r/'completed/opencode-coder').read_text());self.assertEqual(c['task_sha256'],hashlib.sha256(old).hexdigest());self.assertEqual(c['report_sha256'],sha(r));self.assertEqual(c['generation'],'gen1')
 def test_task_mismatches_preserve_completion(self):
  j,r=self.dispatch();write(r,'report');task=self.tasks/'opencode-coder';initial=task.read_text();m=self.announce(r)
  for col,value in [(0,str(int(time.time())+1)),(1,'bean-other'),(2,'0'*40),(3,str(self.reports/'other.md'))]:
   fields=initial.rstrip('\n').split('\t');fields[col]=value;write(task,'\t'.join(fields)+'\n');self.call('tick');self.assertTrue(j.exists());self.assertTrue(m.exists());self.assertFalse((self.r/'completed/opencode-coder').exists())
 def test_same_bean_newer_task_not_overwritten(self):
  j,r=self.dispatch();self.finish(r);self.call('tick');self.assertFalse(j.exists());task=self.tasks/'opencode-coder';f=task.read_text().rstrip('\n').split('\t');f[0]=str(int(f[0])+1);write(task,'\t'.join(f)+'\n');new=task.read_bytes();self.job('j2',bean='bean-uhrt');self.call(watch=True);self.assertEqual(self.count(),1);self.assertEqual(task.read_bytes(),new)
 def test_late_report_and_out_of_order_retained(self):
  j,r=self.dispatch();m=self.announce(r);unmatched=self.announce(self.reports/'other',a='unrelated-coder',name=f'{int(time.time())}-unrelated.msg');self.call('tick');self.assertTrue(m.exists());self.assertTrue(unmatched.exists());write(r,'late report');self.call('tick');self.assertFalse(j.exists());self.assertFalse(m.exists());self.assertTrue(unmatched.exists())
 def test_pre_dispatch_message_and_stale_report_retained(self):
  j,r=self.dispatch();write(r,'old');os.utime(r,(1,1));m=self.announce(r);self.call('tick');self.assertTrue(j.exists());write(r,'fresh');m.unlink();m=self.announce(r,ts=1,name=f'{int(time.time())}-stale-body.msg');self.call('tick');self.assertTrue(j.exists());self.assertTrue(m.exists())
 def test_non_done_states_hold_and_notify_once(self):
  j,r=self.dispatch();write(r,'report')
  for s in ['PARTIAL','CHANGES','BLOCKED']:
   m=self.announce(r,state=s,name=f'{int(time.time())}-{s}.msg');self.call('tick');self.call('tick');self.assertTrue(j.exists());self.assertTrue(m.exists())
  notices=list(self.inbox.glob('*lead-safe-route*.msg'));self.assertEqual(len(notices),3);self.assertEqual(self.count(),1)
 def test_history_bounded_no_repeat_logging(self):
  for i in range(4000):write(self.inbox/f'{i}-historical.msg',f'{i}\tunrelated\tother\tDONE\told\t-\n')
  j,r=self.dispatch();m=self.announce(r);self.call('tick');self.assertTrue(m.exists());write(r,'late');self.call('tick');self.assertFalse(j.exists());self.assertEqual(len(list(self.inbox.glob('*historical.msg'))),4000);self.assertLess((self.r/'log').stat().st_size,32768)
 def test_budget_unknown_failure_and_stale_fail_closed(self):
  self.job();write(self.p/'budget-fail','');self.call(watch=True);self.assertEqual(self.count(),0);(self.p/'budget-fail').unlink();self.call(watch=True);self.assertEqual(self.count(),1)
 def test_failed_producer_does_not_reuse_healthy_snapshot(self):
  self.job();write(self.st/'land-paused','');self.call(watch=True);(self.st/'land-paused').unlink();write(self.bin/'fleet-budget','#!/bin/bash\nexit 1\n',True);self.call(watch=True);self.assertEqual(self.count(),0)
 def test_held_seat_and_active_task(self):
  self.job();write(self.r/'holds/opencode-coder','held');self.call(watch=True);self.assertEqual(self.count(),0);(self.r/'holds/opencode-coder').unlink();write(self.tasks/'opencode-coder',f'{int(time.time())}\tbean-other\t{self.base}\t{self.reports}/other.md\n');self.call(watch=True);self.assertEqual(self.count(),0)
 def test_registration_failure_no_prompt_no_clear(self):
  self.job();write(self.bin/'fleet-track','#!/bin/bash\nprintf "%s\\n" "$*" >> "'+str(self.p/'track.calls')+'"\nexit 1\n',True);self.call(watch=True);self.assertEqual(self.count(),0);self.assertEqual([line.split()[0] for line in (self.p/'track.calls').read_text().splitlines()],['dispatch']);self.assertEqual(len(list(self.inbox.glob('*lead-safe-route*.msg'))),1)
 def test_ack_timeout_reserved_counts_attempt(self):
  self.job();self.job('j2',a='grok-reviewer',bean='bean-next',role='reviewer');self.env['SAFE_MAX_PER_TICK']='1';write(self.p/'prompt-mode','timeout');self.call(watch=True);self.assertEqual(self.count(),1);self.assertIn('UNKNOWN_DELIVERY',(self.r/'log').read_text());self.call('tick');self.assertEqual(self.count(),2);self.call('tick');self.assertEqual(self.count(),2)
 def test_outer_timeout_reserved_no_resend(self):
  self.job();write(self.p/'prompt-mode','outer');self.call(watch=True);self.call('tick');self.assertEqual(self.count(),1);self.assertIn('UNKNOWN_DELIVERY',(self.r/'log').read_text())
 def test_blank_fields_generation_and_traversal_refused(self):
  j,r=self.job();initial=j.read_text();(self.r/'generation').write_text('');self.call(watch=True);self.assertEqual(self.count(),0);write(self.r/'generation','gen1\n')
  for i in range(11):
   f=initial.rstrip('\n').split('\t');f[i]='';write(j,'\t'.join(f)+'\n');self.call('tick');self.assertEqual(self.count(),0)
  f=initial.rstrip('\n').split('\t');f[0]='../escape';write(j,'\t'.join(f)+'\n');self.call('tick');self.assertFalse((self.st/'escape').exists());self.assertEqual(self.count(),0)
 def test_grant_and_source_pin_required(self):
  j,r=self.job();g=self.r/'grants/j1.json';g.unlink();self.call(watch=True);self.assertEqual(self.count(),0)
 def test_observer_only_no_legacy(self):
  self.job();self.call(watch=True);calls=(self.p/'calls').read_text();self.assertEqual([s for s in calls.splitlines() if s.startswith('observe')],['observe --board','observe --board']);self.assertNotIn('keys',calls)
 def test_tick_lock_concurrent_once_only(self):
  self.job();self.call(watch=True);self.finish(self.reports/'j1.md');self.job('j2',bean='bean-next',mode='completed',after=self.reports/'j1.md',ash=sha(self.reports/'j1.md'));ps=[subprocess.Popen([str(self.bin/'fleet-safe-route'),'tick'],env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
  for p in ps:p.communicate(timeout=20);self.assertEqual(p.returncode,0)
  self.assertEqual(self.count(),2)
 def test_task_writer_compare_and_swap_refuses_changed_task(self):
  task=self.tasks/'opencode-coder';write(task,f'{int(time.time())}\tbean-new\t{self.base}\t{self.reports}/new.md\n');before=task.read_bytes()
  ret=subprocess.run([str(self.bin/'fleet-track'),'dispatch','opencode-coder','bean-uhrt',str(self.reports/'j1.md'),self.branch,'0'*64],env=self.env,capture_output=True,text=True)
  self.assertNotEqual(ret.returncode,0);self.assertEqual(task.read_bytes(),before)
 def test_reservation_mismatch_retains_message(self):
  j,r=self.dispatch();m=self.finish(r);rf=self.r/'reserved/opencode-coder/job';f=rf.read_text().splitlines();f[1]='bean-other';write(rf,'\n'.join(f)+'\n');self.call('tick');self.assertTrue(j.exists());self.assertTrue(m.exists())
 def test_current_out_of_order_inbox_messages(self):
  j,r=self.dispatch();stamp=int(time.time());m=self.announce(r,ts=stamp+1,name=f'{stamp+1}-first.msg');self.call('tick');self.assertTrue(m.exists());m.unlink();m=self.announce(r,ts=stamp,name=f'{stamp}-later.msg');write(r,'late current lower-timestamp report');self.call('tick');self.assertFalse(j.exists());self.assertFalse(m.exists())
 def test_logs_capped_and_rotated(self):
  self.job();write(self.r/'log','x'*280000);self.call(watch=True);self.assertLessEqual((self.r/'log').stat().st_size,262144);self.assertTrue((self.r/'log.1').exists())
 def test_stall_notice_per_job_and_real_source_progress(self):
  j,r=self.dispatch();self.env['SAFE_NOW']=str(int(time.time())+901);self.call('tick');self.call('tick');self.assertEqual(len(list(self.inbox.glob('*no-progress-j1*.msg'))),1)
 def test_fresh_report_is_progress_not_automatic_completion(self):
  j,r=self.dispatch();write(r,'work in progress');self.env['SAFE_NOW']=str(int(time.time())+901);self.call('tick');self.assertTrue(j.exists());self.assertFalse(list(self.inbox.glob('*no-progress-j1*.msg')))
 def test_real_source_commit_is_progress(self):
  j,r=self.dispatch();write(self.repo/'source.txt','progress\n');self.run_git('add','source.txt');self.run_git('-c','core.hooksPath=/dev/null','commit','-qm','actual fixture code progress');self.env['SAFE_NOW']=str(int(time.time())+901);self.call('tick');self.assertTrue(j.exists());self.assertFalse(list(self.inbox.glob('*no-progress-j1*.msg')))
 def test_noop_registration_keeps_reserved_no_prompt(self):
  self.job();write(self.bin/'fleet-track','#!/bin/bash\nexit 0\n',True);self.call(watch=True);self.assertEqual(self.count(),0);self.assertTrue((self.r/'reserved/opencode-coder').exists())
 def test_untrusted_grant_and_brief_hash_refused(self):
  j,r=self.job();g=self.r/'grants/j1.json';g.chmod(0o666);self.call(watch=True);self.assertEqual(self.count(),0);g.chmod(0o600);write(self.briefs/'j1.md','changed');self.call('tick');self.assertEqual(self.count(),0)
 def test_registration_public_entry_validates_exact_grant(self):
  j,r=self.job();payload=self.p/'job.input';j.rename(payload);self.call('register','j1',str(payload),str(self.r/'grants/j1.json'));self.assertEqual(j.read_bytes(),payload.read_bytes());ret=self.call('register','../escape',str(payload),str(self.r/'grants/j1.json'),check=False);self.assertNotEqual(ret.returncode,0)
 def test_snapshot_timestamp_controls_fail_closed(self):
  self.job();write(self.st/'land-paused','');self.call(watch=True);(self.st/'land-paused').unlink();budget=self.st/'budget-seat';original=budget.read_text()
  for ts in ['0',str(int(time.time())+600),'invalid']:
   write(budget,ts+'\n'+'\n'.join(original.splitlines()[1:])+'\n');self.call('tick');self.assertEqual(self.count(),0)
 def test_missing_memory_snapshot_holds(self):
  self.job();write(self.st/'land-paused','');self.call(watch=True);(self.st/'land-paused').unlink();(self.st/'memory-status').unlink(missing_ok=True);self.call('tick');self.assertEqual(self.count(),0)
 def test_coder_collision_reviewer_cross_harness(self):
  self.job();self.call(watch=True);self.assertEqual(self.count(),1);self.job('j2',a='agy-reviewer',role='reviewer');self.call(watch=True);self.assertEqual(self.count(),2)
 def test_stale_generation_and_arbitrary_after_file_refused(self):
  j,r=self.job(mode='completed',after=self.p/'arbitrary',ash='0'*64);write(self.p/'arbitrary','anything');f=j.read_text().rstrip('\n').split('\t');f[9]=sha(self.p/'arbitrary');write(j,'\t'.join(f)+'\n');g=json.loads((self.r/'grants/j1.json').read_text());g['job_sha256']=sha(j);write(self.r/'grants/j1.json',json.dumps(g));self.call(watch=True);self.assertEqual(self.count(),0)
 def test_send_failure_notices_dedup_per_job(self):
  self.job();write(self.p/'prompt-mode','fail');self.call(watch=True);self.call('tick');self.assertEqual(self.count(),1);self.assertEqual(len(list(self.inbox.glob('*send-fail-j1*.msg'))),1)
 def test_registration_attempt_counts_toward_tick_limit(self):
  self.job();self.job('j2',a='grok-reviewer',bean='bean-next',role='reviewer');self.env['SAFE_MAX_PER_TICK']='1';write(self.bin/'fleet-track','#!/bin/bash\nprintf "%s\n" "$*" >> "'+str(self.p/'track.calls')+'"\nexit 1\n',True);self.call(watch=True);self.assertEqual(self.count(),0);self.assertEqual(len((self.p/'track.calls').read_text().splitlines()),1)
 def test_existing_tick_lock_holds_completion_and_dispatch(self):
  j,r=self.dispatch();self.finish(r);self.job('j2',bean='bean-next',mode='completed',after=r,ash=sha(r));(self.r/'tick.lock').mkdir();self.call('tick');self.assertTrue(j.exists());self.assertEqual(self.count(),1);(self.r/'tick.lock').rmdir();self.call('tick');self.assertFalse(j.exists());self.assertEqual(self.count(),2)
 def test_working_done_and_blocked_native_states(self):
  self.job()
  for state in ['working','blocked','unknown','MISSING']:
   write(self.p/'agents.json',json.dumps({'result':{'agents':[{'name':'opencode-coder','agent_status':state}]}}))
   self.call(watch=True);self.assertEqual(self.count(),0);self.assertFalse((self.tasks/'opencode-coder').exists())
  write(self.p/'agents.json',json.dumps({'result':{'agents':[{'name':'opencode-coder','agent_status':'done'}]}}))
  self.call(watch=True);self.assertEqual(self.count(),1)
 def test_enabled_without_observer_marker_never_legacy(self):
  self.job();(self.st/'observer').unlink();self.call(watch=True);self.assertEqual(self.count(),1);self.assertEqual([s for s in (self.p/'calls').read_text().splitlines() if s.startswith('observe')],['observe --board','observe --board'])
 def test_root_approved_exact_inactive_task_bootstrap(self):
  prior=self.reports/'prior.md';write(prior,'root reviewed prior completion');task=self.tasks/'agy-reviewer';write(task,f'{int(time.time())-10}\tbean-we4l\tnone\t{prior}\n');raw=task.read_bytes();j,r=self.job(a='agy-reviewer',role='reviewer')
  gp=self.r/'grants/j1.json';g=json.loads(gp.read_text());g.update(expected_task_sha256=hashlib.sha256(raw).hexdigest(),free_task_b64=base64.b64encode(raw).decode(),bootstrap_report_sha256=sha(prior),bootstrap_report_state='DONE',verified_free_at=int(time.time()));write(gp,json.dumps(g))
  self.call(watch=True);self.assertEqual(self.count(),1);self.assertEqual(task.read_text().split('\t')[1],'bean-uhrt')
 def test_root_inactive_task_snapshot_does_not_clear_newer_same_bean(self):
  prior=self.reports/'prior.md';write(prior,'root reviewed prior completion');task=self.tasks/'agy-reviewer';write(task,f'{int(time.time())-10}\tbean-we4l\tnone\t{prior}\n');raw=task.read_bytes();j,r=self.job(a='agy-reviewer',role='reviewer');gp=self.r/'grants/j1.json';g=json.loads(gp.read_text());g.update(expected_task_sha256=hashlib.sha256(raw).hexdigest(),free_task_b64=base64.b64encode(raw).decode(),bootstrap_report_sha256=sha(prior),bootstrap_report_state='DONE',verified_free_at=int(time.time()));write(gp,json.dumps(g));f=task.read_text().rstrip('\n').split('\t');f[0]=str(int(f[0])+1);write(task,'\t'.join(f)+'\n');new=task.read_bytes();self.call(watch=True);self.assertEqual(self.count(),0);self.assertEqual(task.read_bytes(),new)
 def before(self,a,role,marker):
  j,r=self.job(a=a,role=role);write(marker,'root held; preserve\n');before=marker.read_bytes();self.call(watch=True)
  self.assertEqual(self.count(),0,'root-held seat received a prompt');self.assertFalse((self.r/'reserved'/a).exists());self.assertFalse((self.tasks/a).exists());self.assertTrue(j.exists());self.assertEqual(marker.read_bytes(),before)
 def test_root_hold_coder_before_reservation(self):self.before('opencode-coder','coder',self.st/'hold/opencode-coder')
 def test_root_hold_reviewer_before_reservation(self):self.before('grok-reviewer','reviewer',self.st/'hold/grok-reviewer')
 def test_root_blocked_seat_marker(self):self.before('grok-reviewer','reviewer',self.st/'blocked/grok-reviewer')
 def test_router_local_hold_retained(self):self.before('opencode-coder','coder',self.r/'holds/opencode-coder')
 def test_root_hold_after_actual_registration(self):
  a='grok-reviewer';j,r=self.job(a=a,role='reviewer');native=self.bin/'fleet-track.native';(self.bin/'fleet-track').rename(native);marker=self.st/'hold'/a
  # Execute the actual atomic tracker first. The adapter introduces a root hold only after successful registration.
  write(self.bin/'fleet-track','''#!/usr/bin/env python3
import subprocess,sys
from pathlib import Path
r=subprocess.run(['''+repr(str(native))+''',*sys.argv[1:]])
if r.returncode==0:
 p=Path('''+repr(str(marker))+''');p.parent.mkdir(parents=True,exist_ok=True);p.write_text('root hold after actual registration\\n')
sys.exit(r.returncode)
''',True)
  self.call(watch=True);self.assertEqual(self.count(),0,'new root hold was ignored after actual registration')
  self.assertTrue(marker.exists());self.assertTrue(j.exists());self.assertTrue((self.r/'reserved'/a).is_dir())
  task=(self.tasks/a).read_bytes();self.assertEqual(task.decode().split('\t')[1],'bean-uhrt');self.assertEqual(task.decode().strip().split('\t')[3],str(r))
  record=json.loads((self.r/'reserved'/a/'reservation.json').read_text());self.assertEqual(record['delivery'],'HELD');self.call('tick');self.assertEqual(self.count(),0);self.assertEqual((self.tasks/a).read_bytes(),task);self.assertTrue(marker.exists())
 def test_unrelated_historical_blocked_log_does_not_hold_all_seats(self):
  write(self.st/'blocked','other-coder bean-old historical diagnostic\n');before=(self.st/'blocked').read_bytes();self.job();self.call(watch=True);self.assertEqual(self.count(),1);self.assertEqual((self.st/'blocked').read_bytes(),before)
 def test_observer_never_queries_legacy_vendor_database(self):
  caps=self.home/'.config/herdr/fleet-caps';write(caps,'opencode_daily_usd=10\n')
  self.env['FLEET_CAPS']=str(caps)
  write(self.bin/'sqlite3','#!/bin/bash\necho forbidden >> "'+str(self.p/'vendor-db-calls')+'"\nexit 1\n',True)
  self.job();self.call(watch=True);self.assertEqual(self.count(),1)
  self.assertFalse((self.p/'vendor-db-calls').exists())
 def test_empty_queue_escalates_without_fabricating_work(self):
  self.call(watch=True);self.call('tick')
  self.assertEqual(self.count(),0);self.assertEqual(list((self.r/'queue').glob('*.job')),[])
  notices=list(self.inbox.glob('*lead-safe-route-empty-queue.msg'))
  self.assertEqual(len(notices),1);self.assertIn('finite approved queue empty',notices[0].read_text())
 def test_status_done_with_unproven_active_task_is_not_free(self):
  j,r=self.job()
  write(self.p/'agents.json',json.dumps({'result':{'agents':[{'name':'opencode-coder','agent_status':'done'}]}}))
  task=self.tasks/'opencode-coder'
  write(task,f'{int(time.time())}\tbean-uhrt\t{self.base}\t{r}\n')
  before=task.read_bytes();write(r,'DONE without a reservation is not proof\n');m=self.announce(r)
  self.call(watch=True);self.assertEqual(self.count(),0)
  self.assertEqual(task.read_bytes(),before);self.assertTrue(j.exists());self.assertTrue(m.exists())
  self.assertFalse((self.r/'reserved/opencode-coder').exists())
 def test_session_cap_snapshot_retains_job_until_healthy(self):
  j,r=self.job();write(self.st/'land-paused','');self.call(watch=True)
  (self.st/'land-paused').unlink();budget=self.st/'budget-seat';healthy=budget.read_text()
  write(budget,healthy.replace('opencode-coder opencode idle OK','opencode-coder opencode idle SESSION_CAP'))
  self.call('tick');self.assertEqual(self.count(),0);self.assertTrue(j.exists())
  self.assertFalse((self.r/'reserved/opencode-coder').exists())
  write(budget,healthy);self.call('tick');self.assertEqual(self.count(),1)
if __name__=='__main__':unittest.main(verbosity=2)
