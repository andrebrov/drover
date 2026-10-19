"""Run the canonical assign command; no generated fleet script or source-token checks."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

BIN = Path(__file__).resolve().parents[1] / 'bin'


class BriefFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('FLEET_PROOF_SCRATCH'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        (self.home / '.local/bin').mkdir(parents=True)
        (self.home / '.config/drover').mkdir(parents=True)
        self.peers = self.home / '.config/drover/PEERS.md'
        self.peers.write_text('# Peers\n\n## Rule A\nRULE-BODY\n')
        self.repo = self.root / 'repo'
        (self.repo / '.beans').mkdir(parents=True)
        (self.repo / '.beans/bean-abcd--fixture.md').write_text('---\ntitle: Fixture\nstatus: todo\n---\n')
        self.env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(HOME=str(self.home), FLEET_ROOT=str(self.repo), DROVER_REPO=str(self.repo), FLEET='fixture:codex',
                        FLEET_DISPATCH_FIXTURE=str(self.root / 'board'), TMPDIR=str(self.root),
                        FLEET_PRECEDENT='on', DROVER_RULEBOOK_TOOL=str(self.home/'.local/bin/fleet-rulebook'), DROVER_PRECEDENT_TOOL=str(self.home/'.local/bin/fleet-precedent'))
        for args in [('init','-q','-b','main'), ('config','user.name','Fixture'),
                     ('config','user.email','fixture@example.invalid'), ('add','.beans'),
                     ('commit','-qm','fixture'), ('update-ref','refs/remotes/origin/main','HEAD')]:
            subprocess.run(['git','-C',str(self.repo),*args],env=self.env,check=True,capture_output=True)
        (self.root / 'board/config').mkdir(parents=True)
        transport = self.root / 'herdr-stub'
        transport.write_text("""#!/usr/bin/env python3
import json,sys
row=dict(name='fixture',cwd='WORKTREE',agent='codex',agent_status='done',pane_id='w1:p1',terminal_id='t1',state_change_seq=1,interactive_ready=True)
if sys.argv[2]=='explain': print(json.dumps(dict(state='done',visible_blocker=False,skip_state_update=False)))
elif sys.argv[2]=='read': print('›\\n ? for shortcuts')
else: print(json.dumps({'result':{'agent':dict(row,agent_status='working') if sys.argv[2]=='prompt' else row}}))
""")
        transport.chmod(0o755)
        self.env['FLEET_HERDR'] = str(transport)
        self.tool('fleet-track', '', 0)
        self.tool('fleet-rulebook', 'RULE-SELECTED', 0)
        self.tool('fleet-precedent', 'PRECEDENT-SELECTED', 0)

    def tool(self, name, output, code=0):
        path = self.home / '.local/bin' / name
        path.write_text('#!/usr/bin/env python3\nimport os, pathlib, sys\n'
                        f'pathlib.Path(os.environ["HOME"], "{name}.calls").write_text(" ".join(sys.argv[1:]))\n'
                        f'print({output!r})\nsys.exit({code})\n')
        path.chmod(0o755)

    def assign(self):
        result = subprocess.run(['bash',str(BIN/'fleet'),'assign','bean-abcd','fixture'],
                                env=self.env,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return (self.home/'.local/share/drover/briefs/assign-fixture-bean-abcd.txt').read_text()


class RulebookTests(BriefFixture):
    def test_direct_brief_owns_publication_and_uses_remote_main(self):
        mode=self.root/'publish-mode';mode.write_text('direct\n');self.env['DROVER_PUBLISH_MODE_FILE']=str(mode)
        brief=self.assign()
        self.assertIn('Land your own tested commits',brief)
        self.assertNotIn('never push',brief)
        self.assertNotIn('(local main, not origin/main)',brief)


    def test_selected_rules_follow_prompt(self):
        brief = self.assign()
        self.assertTrue(brief.startswith('You are fixture,'))
        self.assertIn('RULE-SELECTED',brief)
        self.assertLess(brief.index('Your job: bean bean-abcd'),brief.index('RULE-SELECTED'))
        self.assertEqual((self.home/'fleet-rulebook.calls').read_text(),'bean-abcd')

    def test_missing_failing_and_silent_rules_keep_whole_file(self):
        for mode in ('missing','failing','silent'):
            with self.subTest(mode=mode):
                path=self.home/'.local/bin/fleet-rulebook'
                if mode=='missing': path.unlink()
                else: self.tool('fleet-rulebook','PARTIAL' if mode=='failing' else '',2 if mode=='failing' else 0)
                brief=self.assign()
                self.assertIn('RULE-BODY',brief)
                self.assertIn('fleet-rulebook failed or is missing',brief)
                self.assertNotIn('PARTIAL',brief)

    def test_unreadable_peers_are_disclosed(self):
        (self.home/'.local/bin/fleet-rulebook').unlink()
        self.peers.unlink()
        brief=self.assign()
        self.assertIn('could not be read',brief)
        self.assertIn(str(self.peers),brief)


class PrecedentTests(BriefFixture):
    def test_precedent_follows_rules(self):
        brief=self.assign()
        self.assertIn('RULE-SELECTED',brief)
        self.assertIn('PRECEDENT-SELECTED',brief)
        self.assertLess(brief.index('RULE-SELECTED'),brief.index('PRECEDENT-SELECTED'))
        self.assertEqual((self.home/'fleet-precedent.calls').read_text(),'bean-abcd')

    def test_off_does_not_call_tool(self):
        self.env['FLEET_PRECEDENT']='off'
        brief=self.assign()
        self.assertNotIn('PRECEDENT-SELECTED',brief)
        self.assertFalse((self.home/'fleet-precedent.calls').exists())

    def test_missing_failing_or_silent_appends_nothing(self):
        for mode in ('missing','failing','silent'):
            with self.subTest(mode=mode):
                path=self.home/'.local/bin/fleet-precedent'
                if mode=='missing': path.unlink()
                else: self.tool('fleet-precedent','PARTIAL' if mode=='failing' else '',2 if mode=='failing' else 0)
                brief=self.assign()
                self.assertIn('RULE-SELECTED',brief)
                self.assertNotIn('PRECEDENT-SELECTED',brief)
                self.assertNotIn('PARTIAL',brief)


if __name__=='__main__':
    unittest.main()
