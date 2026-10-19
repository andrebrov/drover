"""Exercise real fleet entry points with a synthetic native Herdr transport."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class DeliveryCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.transport = self.root / 'herdr'
        self.transport.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ['FIXTURE'])
row = dict(name='tester', agent='codex', agent_status='idle', pane_id='w1:p1', terminal_id='term1', state_change_seq=10, cwd=str(p/'checkout'), interactive_ready=True)
row.update(json.loads((p/'row.json').read_text()))
a = sys.argv[1:]
with (p/'calls').open('a') as f: f.write(json.dumps(a)+'\\n')
if a[:2] == ['agent','get']: print(json.dumps({'result':{'agent':row}}))
elif a[:2] == ['agent','explain']: print((p/'explain.json').read_text())
elif a[:2] == ['agent','read']: print((p/'screen').read_text())
elif a[:2] == ['agent','prompt']:
    with (p/'prompts').open('a') as f: f.write(json.dumps(a)+'\\n')
    status=(p/'reply-status').read_text() if (p/'reply-status').exists() else 'working'
    print(json.dumps({'result':{'agent':dict(row, agent_status=status)}}))
else: sys.exit(2)
''')
        self.transport.chmod(0o755)
        self.env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(HOME=str(self.root), FIXTURE=str(self.root), FLEET='tester:codex',
                        FLEET_HERDR=str(self.transport), FLEET_LEDGER=str(self.root/'ledger'),
                        FLEET_ROOT=str(self.root/'checkout'))
        self.configure()

    def configure(self, row=None, explain=None, screen='Done.\n› Ask Codex to do anything\n  ? for shortcuts\n'):
        (self.root/'row.json').write_text(json.dumps(row or {}))
        (self.root/'explain.json').write_text(json.dumps(explain or dict(state='idle', visible_blocker=False, skip_state_update=False)))
        (self.root/'screen').write_text(screen)

    def call(self, verb='send'):
        return subprocess.run(['bash', str(ROOT/'bin/fleet'), verb, 'tester', 'Synthetic assignment'],
                              env=self.env, text=True, capture_output=True, timeout=15)

    def assert_held(self, reason, verb='send'):
        result=self.call(verb)
        self.assertEqual(result.returncode, 75, result.stdout+result.stderr)
        self.assertIn(reason, result.stderr)
        self.assertFalse((self.root/'prompts').exists(), result.stdout+result.stderr)
        self.assertFalse((self.root/'ledger').exists(), 'held delivery must not create an assignment event')

    def test_native_idle_with_draft_never_receives_prompt(self):
        self.configure(screen='Done.\n› keep my draft\n  ? for shortcuts\n')
        self.assert_held('draft_present')

    def test_working_turn_never_receives_followup(self):
        self.configure(row={'agent_status':'working'})
        self.assert_held('not_idle', 'followup')

    def test_empty_stable_composer_receives_once(self):
        result=self.call()
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual(len((self.root/'prompts').read_text().splitlines()), 1)
        calls=[json.loads(s) for s in (self.root/'calls').read_text().splitlines()]
        self.assertGreaterEqual(sum(c[:2]==['agent','explain'] for c in calls), 2)
        sent=next(c for c in calls if c[:2]==['agent','prompt'])
        self.assertEqual(sent[2:4], ['w1:p1', 'Synthetic assignment'])
        self.assertIn('--wait', sent)

    def test_zero_exit_with_unsubmitted_prompt_is_unknown_not_success(self):
        (self.root/'reply-status').write_text('idle')
        result=self.call()
        self.assertEqual(result.returncode,76,result.stdout+result.stderr)
        self.assertIn('UNKNOWN_DELIVERY',result.stderr)
        self.assertEqual(len((self.root/'prompts').read_text().splitlines()),1)
        retry=self.call()
        self.assertEqual(retry.returncode,75,retry.stdout+retry.stderr)
        self.assertIn('seat_held',retry.stderr)
        self.assertEqual(len((self.root/'prompts').read_text().splitlines()),1)


class DeliveryGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('fleet_delivery', ROOT/'lib/fleet_delivery.py')
        cls.gate=importlib.util.module_from_spec(spec); spec.loader.exec_module(cls.gate)

    def setUp(self):
        self.row=dict(name='tester', agent='codex', agent_status='idle', pane_id='w1:p1',
                      terminal_id='term1', state_change_seq=10, cwd='/fixture/project', interactive_ready=True,
                      agent_session={'source':'startup','value':'conversation-1'})
        self.screen='Done.\n› Ask Codex to do anything\n\n  GPT-6.1-Sol high · ~/project\n  ? for shortcuts\n'
        self.explain=dict(state='idle', visible_blocker=False, skip_state_update=False)
        self.clock=0.0; self.calls=[]; self.change=None; self.gets=0
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        from unittest.mock import patch
        self.patch=patch.dict(os.environ, FLEET_STATE=self.temp.name); self.patch.start(); self.addCleanup(self.patch.stop)

    def read(self, command, args):
        self.calls.append(args)
        if args[0]=='get':
            self.gets+=1
            return json.dumps({'result':{'agent':self.row}})
        if args[0]=='explain': return json.dumps(self.explain)
        if args[0]=='read': return self.screen
        self.fail(args)

    def sleep(self, seconds):
        self.clock+=seconds
        if self.change: self.change()

    def enter(self):
        return self.gate.delivery_gate('tester', read=self.read, sleep=self.sleep, clock=lambda:self.clock)

    def test_real_empty_codex_footer_is_eligible(self):
        with self.enter() as target: self.assertEqual(target['pane_id'], 'w1:p1')
        self.assertEqual(self.clock,2)
        self.assertEqual(self.gets,4)

    def test_native_done_still_requires_same_empty_composer(self):
        self.row['agent_status']='done'; self.explain['state']='done'
        with self.enter() as target: self.assertEqual(target['pane_id'],'w1:p1')

    def test_both_samples_must_have_same_screen(self):
        self.change=lambda:setattr(self,'screen',self.screen.replace('Done.','New output.'))
        with self.assertRaisesRegex(self.gate.Held, 'unstable'):
            with self.enter(): self.fail('sent')

    def test_identity_or_conversation_change_holds_without_retry(self):
        for key,value in [('terminal_id','term2'),('pane_id','w2:p2'),('cwd','/fixture/other'),
                          ('state_change_seq',11),('agent_session',{'source':'resume','value':'conversation-2'})]:
            with self.subTest(key=key):
                before=self.row[key]
                self.change=lambda:setattr_row(key,value)
                def setattr_row(k,v): self.row[k]=v
                with self.assertRaisesRegex(self.gate.Held,'identity_changed'):
                    with self.enter(): self.fail('sent')
                self.row[key]=before

    def test_compaction_phase_does_not_change_conversation(self):
        self.change=lambda:self.row.update(agent_session={'source':'compact','value':'conversation-1'})
        with self.enter() as target: self.assertEqual(target['pane_id'],'w1:p1')

    def test_explain_disagreement_skip_and_blocker_hold(self):
        for bad in [dict(state='working'),dict(state='blocked'),dict(visible_blocker=True),
                    dict(skip_state_update=True),dict(skipped_update_reason='overlay'),dict(visible_working=True)]:
            with self.subTest(bad=bad):
                prior=dict(self.explain); self.explain.update(bad)
                with self.assertRaises(self.gate.Held):
                    with self.enter(): self.fail('sent')
                self.explain=prior

    def test_missing_identity_fields_and_readiness_hold(self):
        for field in ['terminal_id','pane_id','cwd','state_change_seq','interactive_ready']:
            with self.subTest(field=field):
                prior=self.row.pop(field)
                with self.assertRaises(self.gate.Held):
                    with self.enter(): self.fail('sent')
                self.row[field]=prior

    def test_composer_layouts_and_drafts(self):
        cases=[
            ('codex','Done\n›\n ? for shortcuts',None),
            ('codex','Done\n› ⠋Ask Codex to do anything⠴\n ? for shortcuts',None),
            ('codex','Done\n› Ask Codex to do anything else\n ? for shortcuts','draft_present'),
            ('codex','Done\n›\n  wrapped draft\n ? for shortcuts','draft_present'),
            ('codex','› old transcript prompt\nDone','composer_unavailable'),
            ('claude','Old transcript approval?\n──────\n❯\n──────\n ? for shortcuts',None),
            ('claude','❯\n do not overwrite\n──────\n ? for shortcuts','draft_present'),
            ('claude','❯ 1. Allow once\n Enter to select\n──────','dialog_present'),
            ('opencode','Done\n→ Add a follow-up\n ctrl+t variants',None),
            ('opencode','Done\n→ continue my draft\n ctrl+t variants','draft_present'),
            ('codex','Question 1 of 2\n❯ 1. Yes\n Enter to select','composer_unavailable'),
            ('unknown','›\n ? for shortcuts','composer_unavailable'),
        ]
        for kind,screen,expected in cases:
            with self.subTest(kind=kind,screen=screen): self.assertEqual(self.gate.composer_reason(screen,kind),expected)

    def test_same_seat_concurrent_gate_refused(self):
        with self.enter():
            with self.assertRaisesRegex(self.gate.Held,'delivery_in_flight'):
                with self.enter(): self.fail('second send')
        # Closing the owner releases the lock without deleting its inode.
        with self.enter() as target: self.assertEqual(target['pane_id'],'w1:p1')

if __name__ == '__main__': unittest.main()
