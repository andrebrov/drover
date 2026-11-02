"""Execute fleet up against a recording transport; never type into real panes."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


FLEET = Path(__file__).resolve().parents[1] / 'bin/fleet'


def shell(pane='w1:p1'):
    return dict(pane_id=pane, shell_pid=100, foreground_process_group_id=100,
                foreground_processes=[dict(pid=100, name='zsh')])


class FleetUpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / 'fixture.json'
        self.calls = self.root / 'calls.jsonl'
        self.fixture = dict(processes=[shell()], agents=[], start_codes=[], init_code=0,
                            executable='/bin/zsh')
        stub = self.root / 'herdr'
        stub.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p=Path(os.environ['UP_FIXTURE']); cfg=json.loads(p.read_text()); a=sys.argv[1:]
with Path(os.environ['UP_CALLS']).open('a') as f: f.write(json.dumps(a)+'\\n')
def out(x): print(json.dumps({'result':x}))
if a[:2]==['agent','list']: out({'agents':cfg['agents']})
elif a[:2]==['workspace','list']: out({'workspaces':[{'workspace_id':'w1','label':'fleet'}]})
elif a[:2]==['tab','list']: out({'tabs':[{'tab_id':'t1','label':'fixture'},{'tab_id':'t2','label':'other'},{'tab_id':'t3','label':'lead'}]})
elif a[:2]==['pane','list']: out({'panes':[{'pane_id':f'w1:p{i}','tab_id':f't{i}'} for i in (1,2,3)]})
elif a[:2]==['pane','process-info']:
    count=cfg.get('inspections',0); cfg['inspections']=count+1; p.write_text(json.dumps(cfg))
    state=cfg['processes'][min(count,len(cfg['processes'])-1)]
    if state=='failed': sys.exit(1)
    if state=='malformed': print('{'); sys.exit(0)
    out({'process_info':state})
elif a[:2]==['pane','run']: sys.exit(cfg['init_code'])
elif a[:2]==['agent','start']:
    codes=cfg['start_codes']; code=codes.pop(0) if codes else 0; p.write_text(json.dumps(cfg))
    if code: print(json.dumps({'error':{'code':code}}),file=sys.stderr); sys.exit(1)
else: print('unexpected transport operation',a,file=sys.stderr); sys.exit(90)
''')
        stub.chmod(0o755)
        ps = self.root / 'ps'
        ps.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
assert sys.argv[1:]==['-p','100','-o','comm='], sys.argv[1:]
value=json.loads(Path(os.environ['UP_FIXTURE']).read_text())['executable']
if value is None: sys.exit(1)
print(value)
''')
        ps.chmod(0o755)
        # Real fleet sleeps only while waiting on the shell/native start. Keep
        # fixture retries fast without changing production retry behavior.
        (self.root / 'sleep').write_text('#!/bin/sh\nexit 0\n')
        (self.root / 'sleep').chmod(0o755)
        base = {k: v for k, v in os.environ.items() if not k.startswith(('DROVER_', 'FLEET_', 'GIT_'))}
        self.env = dict(base, HOME=str(self.root), FLEET_HERDR=str(stub), DROVER_CONFIG='/nonexistent',
                        DROVER_REPO=str(self.root), DROVER_LEAD_SEATS='lead',
                        FLEET='fixture:codex', FLEET_WORKTREES='0',
                        FLEET_ADOPT='0', FLEET_NPROC_LIMIT='1500',
                        UP_FIXTURE=str(self.config), UP_CALLS=str(self.calls),
                        PATH=str(self.root)+os.pathsep+os.environ['PATH'])

    def run_up(self, *args):
        self.config.write_text(json.dumps(self.fixture))
        result = subprocess.run(['bash', str(FLEET), 'up', *args], env=self.env,
                                text=True, capture_output=True, timeout=15)
        self.recorded = [json.loads(s) for s in self.calls.read_text().splitlines()] if self.calls.exists() else []
        return result

    def writes(self):
        return [a for a in self.recorded if a[:2] in (['pane','run'], ['agent','start'], ['agent','rename'])]

    def assert_refused(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual(self.writes(), [])
        self.assertIn('not spawning', result.stdout+result.stderr)

    def test_unregistered_live_agent_gets_no_init_or_launch(self):
        self.fixture['processes'] = [dict(shell(), foreground_process_group_id=200,
                                         foreground_processes=[dict(pid=200,name='claude',argv=['DO-NOT-PRINT'],cmdline='DO-NOT-PRINT')])]
        r=self.run_up()
        self.assert_refused(r)
        self.assertIn('occupied by',r.stdout+r.stderr)
        self.assertNotIn('DO-NOT-PRINT',r.stdout+r.stderr)

    def test_real_empty_shell_is_initialized_then_started(self):
        r=self.run_up()
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual([a[:2] for a in self.writes()],[['pane','run'],['agent','start']])
        self.assertIn('JEST_MAX_WORKERS',self.writes()[0][3])
        self.assertIn('ulimit -u 1500',self.writes()[0][3])

    def test_shell_named_foreground_child_is_not_the_pane_shell(self):
        self.fixture['processes']=[dict(shell(),foreground_process_group_id=200,
                                        foreground_processes=[dict(pid=200,name='zsh')])]
        self.assert_refused(self.run_up())

    def test_shell_group_with_an_extra_foreground_process_is_occupied(self):
        self.fixture['processes']=[dict(shell(),foreground_processes=[dict(pid=100,name='zsh'),dict(pid=200,name='vim')])]
        self.assert_refused(self.run_up())

    def test_exec_replaced_shell_pid_is_not_an_empty_shell(self):
        self.fixture['executable']='/opt/homebrew/bin/node'
        self.assert_refused(self.run_up())

    def test_unavailable_executable_fails_closed(self):
        for value in (None,''):
            with self.subTest(value=value):
                self.calls.unlink(missing_ok=True)
                self.fixture['executable']=value
                self.assert_refused(self.run_up())

    def test_unavailable_or_incomplete_inspection_fails_closed(self):
        for state in ('failed','malformed',{},dict(shell(),shell_pid=None),dict(shell(),shell_pid=0),
                      dict(shell(),shell_pid=100.5),dict(shell(),foreground_processes=[]),dict(shell(),pane_id='wrong')):
            with self.subTest(state=state):
                self.calls.unlink(missing_ok=True)
                self.fixture['processes']=[state]
                self.assert_refused(self.run_up())

    def test_disabling_resource_init_does_not_disable_occupancy_check(self):
        self.env['FLEET_NPROC_LIMIT']='0'
        self.fixture['processes']=[dict(shell(),foreground_process_group_id=200)]
        self.assert_refused(self.run_up())

    def test_init_failure_prevents_launch(self):
        self.fixture['init_code']=1
        r=self.run_up()
        self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual([a[:2] for a in self.writes()],[['pane','run']])

    def test_pane_becoming_occupied_after_init_is_not_started(self):
        self.fixture['processes']=[shell(),dict(shell(),foreground_process_group_id=200)]
        r=self.run_up()
        self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual([a[:2] for a in self.writes()],[['pane','run']])

    def test_native_busy_retry_rechecks_occupancy(self):
        self.fixture['start_codes']=['agent_pane_busy']
        self.fixture['processes']=[shell(),shell(),dict(shell(),foreground_process_group_id=200)]
        r=self.run_up()
        self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual([a[:2] for a in self.writes()],[['pane','run'],['agent','start']])

    def test_bulk_up_excludes_lead_even_if_registration_is_missing(self):
        self.env['FLEET']='fixture:codex lead:claude'
        r=self.run_up()
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual([a[2] for a in self.writes() if a[:2]==['agent','start']],['fixture'])
        self.assertIn('lead',r.stdout+r.stderr)

    def test_explicit_lead_is_refused_without_transport(self):
        self.env['FLEET']='fixture:codex lead:claude'
        r=self.run_up('lead')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(self.recorded,[])

    def test_named_form_spawns_exactly_the_named_seat(self):
        self.env['FLEET']='other:claude fixture:codex lead:claude'
        r=self.run_up('fixture')
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual([a[2] for a in self.writes() if a[:2]==['agent','start']],['fixture'])
        self.assertEqual([a[2] for a in self.writes() if a[:2]==['pane','run']],['w1:p1'])

    def test_unknown_or_extra_targets_fail_before_transport(self):
        for args in [('missing',),('',),('fixture','other')]:
            with self.subTest(args=args):
                self.calls.unlink(missing_ok=True)
                r=self.run_up(*args)
                self.assertEqual(r.returncode,2,r.stdout+r.stderr)
                self.assertEqual(self.recorded,[])

    def test_registered_seat_is_not_restarted(self):
        self.fixture['agents']=[dict(name='fixture',workspace_id='',pane_id='w1:p1',agent='codex')]
        r=self.run_up('fixture')
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(self.writes(),[])

    def test_native_busy_retry_starts_when_shell_remains_available(self):
        self.fixture['start_codes']=['agent_pane_busy',0]
        r=self.run_up()
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual([a[:2] for a in self.writes()],
                         [['pane','run'],['agent','start'],['agent','start']])

    def test_no_init_mode_can_still_start_an_empty_shell(self):
        self.env['FLEET_NPROC_LIMIT']='0'
        r=self.run_up()
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual([a[:2] for a in self.writes()],[['agent','start']])

    def test_login_shell_process_name_is_accepted(self):
        self.fixture['executable']='-zsh'
        r=self.run_up()
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual([a[:2] for a in self.writes()],[['pane','run'],['agent','start']])

    def test_bulk_continues_to_other_shell_after_one_occupied_pane(self):
        self.env['FLEET']='fixture:codex other:claude'
        self.fixture['processes']=[dict(shell(),foreground_process_group_id=200),
                                   shell('w1:p2'),shell('w1:p2')]
        r=self.run_up()
        self.assertEqual(r.returncode,1,r.stdout+r.stderr)
        self.assertEqual([a[2] for a in self.writes() if a[:2]==['agent','start']],['other'])
        self.assertEqual([a[2] for a in self.writes() if a[:2]==['pane','run']],['w1:p2'])


if __name__ == '__main__':
    unittest.main()
