"""A coder routes its own review: a free reviewer on a different harness, never a same-harness or usage-limited one."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

FLEET = Path(__file__).resolve().parents[1] / 'bin/fleet'


class ReviewRequest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        stub = self.root / 'herdr'
        stub.write_text('#!/usr/bin/env python3\nimport json,sys\na=sys.argv[1:]\n'
                        "print(json.dumps({'result':{'agent':{'name':a[2] if len(a)>2 else '','agent_status':'idle','cwd':'/w'}}}))\n")
        stub.chmod(0o755)
        self.state = self.root / 'state'
        base = {k: v for k, v in os.environ.items() if not k.startswith(('DROVER_', 'FLEET_', 'GIT_'))}
        self.env = dict(base, HOME=str(self.root), DROVER_CONFIG='/nonexistent', DROVER_REPO=str(self.root),
                        FLEET_HERDR=str(stub), FLEET_STATE=str(self.state),
                        FLEET='c-coder:claude x-reviewer:codex y-reviewer:claude')
        (self.root / '.beans').mkdir()
        (self.root / '.beans' / 'bean-abcd--fix.md').write_text('---\ntitle: fix\n---\n')
        for args in (['init', '-q', '-b', 'main'], ['add', '.beans'],
                     ['-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-qm', 'bean']):
            subprocess.run(['git', '-C', str(self.root), *args], check=True)

    def run_fleet(self, *args):
        return subprocess.run(['bash', str(FLEET), '--dry-run', 'review-request', *args], env=self.env,
                              text=True, capture_output=True, timeout=30)

    def test_picks_a_reviewer_on_another_harness(self):
        r = self.run_fleet('c-coder', 'bean-abcd')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('agent=x-reviewer', r.stdout)

    def test_a_non_coder_cannot_request(self):
        self.assertEqual(self.run_fleet('x-reviewer', 'bean-abcd').returncode, 2)

    def test_a_usage_limited_reviewer_is_skipped_and_named(self):
        (self.state / 'credit-dead').mkdir(parents=True)
        (self.state / 'credit-dead' / 'x-reviewer').write_text(json.dumps({'reset_at': time.time() + 3600, 'at': time.time()}))
        r = self.run_fleet('c-coder', 'bean-abcd')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('usage-limited: x-reviewer (usage limit until', r.stderr)

    def test_codex_seats_launch_without_the_blocking_question_box(self):
        out = subprocess.run(['bash', '-c', f'eval "$(sed -n "/^agent_args()/,/^}}/p" {FLEET})"; FLEET_YOLO=1; seat_model(){{ :; }}; '
                              'agent_args codex x; echo; agent_args opencode x'],
                             text=True, capture_output=True, env=self.env).stdout.splitlines()
        self.assertIn('tools.experimental_request_user_input.enabled=false', out[0])
        self.assertEqual(out[1], '--auto')


if __name__ == '__main__':
    unittest.main()
