"""fleet assign refuses a seat in the shared checkout, and a seat whose HEAD holds unlanded work for another bean."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

FLEET = Path(__file__).resolve().parents[1] / 'bin/fleet'


class AssignGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        (self.repo / '.beans').mkdir(parents=True)
        for b in ('bean-abcd', 'bean-wxyz'):
            (self.repo / f'.beans/{b}--x.md').write_text('---\ntitle: x\nstatus: todo\n---\n')
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('GIT_', 'DROVER_', 'FLEET_'))}
        self.env.update(HOME=str(self.root), DROVER_CONFIG='/nonexistent', DROVER_REPO=str(self.repo),
                        FLEET='fixture:codex', DROVER_BEAN_PREFIX='bean-')
        self.git(self.repo, 'init', '-q', '-b', 'main')
        self.git(self.repo, 'add', '.beans')
        self.git(self.repo, 'commit', '-qm', 'beans')
        self.git(self.repo, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.wt = self.root / 'seat-wt'
        self.git(self.repo, 'worktree', 'add', '-q', '-b', 'fleet/fixture-bean-wxyz', str(self.wt))

    def git(self, where, *args):
        subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', '-C', str(where), *args],
                       check=True, capture_output=True, env=self.env)

    def assign(self, cwd, *extra):
        stub = self.root / 'herdr'
        stub.write_text('#!/usr/bin/env python3\nimport json\n'
                        f"print(json.dumps({{'result':{{'agent':{{'name':'fixture','cwd':{str(cwd)!r},'agent_status':'idle'}}}}}}))\n")
        stub.chmod(0o755)
        return subprocess.run(['bash', str(FLEET), 'assign', 'bean-abcd', 'fixture', *extra], text=True,
                              capture_output=True, env=dict(self.env, FLEET_HERDR=str(stub)), timeout=30)

    def test_a_seat_in_the_shared_checkout_gets_no_dispatch(self):
        r = self.assign(self.repo)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn('runs in the shared checkout', r.stderr)

    def test_unlanded_work_for_another_bean_refuses_until_forced(self):
        (self.wt / 'f.txt').write_text('x\n')
        self.git(self.wt, 'add', 'f.txt')
        self.git(self.wt, 'commit', '-qm', 'feat: half-done work')          # no bean in the subject: the branch's bean owns it
        r = self.assign(self.wt)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn('unlanded work for another bean', r.stderr)
        self.assertIn('bean-wxyz', r.stderr)
        r = self.assign(self.wt, '--force-after-land')
        self.assertIn('--force-after-land overrides unlanded work', r.stderr)

    def test_work_already_landed_under_another_sha_does_not_refuse(self):
        (self.wt / 'f.txt').write_text('x\n')
        self.git(self.wt, 'add', 'f.txt')
        self.git(self.wt, 'commit', '-qm', 'feat: landed elsewhere (bean-wxyz)')
        self.git(self.repo, 'cherry-pick', 'fleet/fixture-bean-wxyz')
        self.git(self.repo, 'update-ref', 'refs/remotes/origin/main', 'main')
        r = self.assign(self.wt)
        self.assertNotIn('unlanded work', r.stderr)


if __name__ == '__main__':
    unittest.main()
