"""A held review delivery stays queued; it cannot claim a reviewer or disappear."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]

class ReviewDelivery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)
        self.repo=self.home/'project';self.repo.mkdir(parents=True)
        self.bin=self.home/'.local/bin';self.bin.mkdir(parents=True)
        self.state=self.home/'.local/share/fleet-watch/state'
        self.tasks=self.home/'.local/share/fleet-tasks'
        self.inbox=self.home/'.local/share/fleet-inbox'
        self.queue=self.home/'.local/share/fleet-queue'
        for path in [self.state,self.tasks,self.inbox,self.queue]:path.mkdir(parents=True,exist_ok=True)
        (self.queue/'keep').touch()
        self.env={k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(HOME=str(self.home),FLEET_ROOT=str(self.repo),DROVER_REPO=str(self.repo),
                        DROVER_BIN=str(self.bin),FLEET_STATE=str(self.state),FLEET_TASKS=str(self.tasks),
                        FLEET_INBOX=str(self.inbox),FLEET_QUEUE=str(self.queue))
        for args in [('init','-q','-b','main'),('config','user.name','Fixture'),
                     ('config','user.email','fixture@example.invalid')]:
            subprocess.run(['git','-C',str(self.repo),*args],check=True,env=self.env,capture_output=True)
        (self.repo/'fixture').write_text('synthetic\n')
        subprocess.run(['git','-C',str(self.repo),'add','fixture'],check=True,env=self.env,capture_output=True)
        subprocess.run(['git','-C',str(self.repo),'commit','-qm','fixture'],check=True,env=self.env,capture_output=True)
        stub=self.bin/'fleet'
        stub.write_text('''#!/bin/sh
case "$1" in
 status) printf 'NAME KIND STATE\\ncodex-coder codex idle\\ngrok-reviewer grok idle\\n';;
 review) printf '%s\\n' "$*" >> "$HOME/review.calls"; exit "$(cat "$HOME/review.exit")";;
 *) exit 0;;
esac
''');stub.chmod(0o755)
        (self.home/'review.exit').write_text('75')
        (self.inbox/'1.msg').write_text(f'{int(time.time())}\tcodex-coder\tbean-demo\tDONE\tfixture ready\t-\n')

    def tick(self):
        result=subprocess.run(['bash',str(ROOT/'bin/fleet-watch'),'once'],env=self.env,
                              text=True,capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return result

    def test_held_review_keeps_retry_and_never_claims_reviewer(self):
        self.tick()
        self.assertIn('review codex-coder grok-reviewer bean-demo',(self.home/'review.calls').read_text())
        self.assertFalse((self.tasks/'grok-reviewer').exists())
        pending=self.state/'reviews/bean-demo'
        self.assertTrue(pending.exists(),'failed delivery lost its retry')
        self.assertEqual(pending.read_text().strip(),'codex-coder')
        before=len((self.home/'review.calls').read_text().splitlines())
        (self.home/'review.exit').write_text('0')
        self.tick()
        self.assertFalse(pending.exists())
        self.assertIn('\tbean-demo\t',(self.tasks/'grok-reviewer').read_text())
        self.assertEqual(len((self.home/'review.calls').read_text().splitlines()),before+1)

    def test_unknown_delivery_holds_for_inspection_without_resend(self):
        (self.home/'review.exit').write_text('76')
        self.tick()
        self.assertFalse((self.tasks/'grok-reviewer').exists())
        self.assertFalse((self.state/'reviews/bean-demo').exists(),'uncertain delivery must not auto-retry')
        self.assertTrue((self.state/'hold/grok-reviewer').exists())
        self.assertIn('bean-demo UNKNOWN_DELIVERY',(self.state/'blocked').read_text())
        count=len((self.home/'review.calls').read_text().splitlines())
        (self.state/'reviews').mkdir(exist_ok=True)
        (self.state/'reviews/bean-next').write_text('codex-coder')
        self.tick()
        self.assertEqual(len((self.home/'review.calls').read_text().splitlines()),count)

if __name__=='__main__':unittest.main()
