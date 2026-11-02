"""A bean that names pre-land commits names their landed equivalents after a rebase/cherry-pick, and nothing else."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import fleet_beans  # noqa: E402

ENV = dict(os.environ, GIT_AUTHOR_NAME='Seat', GIT_AUTHOR_EMAIL='seat@example.com', GIT_AUTHOR_DATE='2026-01-01T00:00:00Z',
           GIT_COMMITTER_NAME='Seat', GIT_COMMITTER_EMAIL='seat@example.com')


class Remap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.git('init', '-q', '-b', 'main')
        self.commit({'README': 'x\n'}, 'init')
        self.git('checkout', '-qb', 'work')
        self.work = self.commit({'a.py': 'def f():\n    return 1\n'}, 'feat: f')
        self.git('checkout', '-q', 'main')
        self.commit({'other.txt': 'main moved\n'}, 'main moved')

    def git(self, *args, env=None):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True, text=True,
                              env=env or ENV).stdout.strip()

    def commit(self, files, msg, env=None):
        for name, body in files.items():
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(body)
            self.git('add', name)
        self.git('commit', '-qm', msg, env=env)
        return self.git('rev-parse', 'HEAD')

    def land(self, *shas):
        """What fleet-land-bean does: cherry-pick -x onto main in a land branch, carrying a bean naming the work."""
        self.git('checkout', '-qb', 'land', 'main')
        for sha in shas:
            self.git('cherry-pick', '-x', sha)
        self.commit({'.beans/bean-abcd--fix.md': f'---\ntitle: fix\nstatus: in-progress\n---\nFixed in {shas[0][:10]}.\n'}, 'bean')

    def test_a_cherry_picked_commit_is_renamed_and_landed_commits_recorded(self):
        self.land(self.work)
        landed = self.git('rev-parse', 'HEAD~1')
        mapping = fleet_beans.remap_rebased_shas(self.repo, 'main', 'bean-abcd')
        self.assertEqual(mapping, {self.work[:10]: landed[:10]})
        text = (self.repo / '.beans/bean-abcd--fix.md').read_text()
        self.assertIn(f'Fixed in {landed[:10]}.', text)
        self.assertIn('LANDED_COMMITS: ' + landed, text)
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_an_indentation_only_twin_is_not_an_equivalent(self):
        self.git('checkout', '-q', 'work')
        self.git('checkout', '-qb', 'side', 'main~1')
        twin = self.commit({'a.py': 'def f():\n  return 1\n'}, 'feat: f')   # same author, second and message
        self.git('checkout', '-q', 'main')
        self.land(self.work)
        (self.repo / '.beans/bean-abcd--fix.md').write_text(f'---\nstatus: in-progress\n---\nFixed in {twin[:10]}.\n')
        self.git('commit', '-qam', 'bean names the twin')
        self.assertEqual(fleet_beans.remap_rebased_shas(self.repo, 'main'), {})

    def test_the_same_bytes_by_another_author_are_not_an_equivalent(self):
        other = dict(ENV, GIT_AUTHOR_NAME='Other', GIT_AUTHOR_EMAIL='other@example.com')
        self.git('checkout', '-qb', 'other', 'main~1')
        foreign = self.commit({'a.py': 'def f():\n    return 1\n'}, 'feat: f', env=other)
        self.git('checkout', '-q', 'main')
        self.land(self.work)
        (self.repo / '.beans/bean-abcd--fix.md').write_text(f'---\nstatus: in-progress\n---\nFixed in {foreign[:10]}.\n')
        self.git('commit', '-qam', 'bean names a foreign commit')
        self.assertEqual(fleet_beans.remap_rebased_shas(self.repo, 'main'), {})

    def test_an_uncommitted_bean_edit_is_never_overwritten(self):
        self.land(self.work)
        (self.repo / '.beans/bean-abcd--fix.md').write_text(f'Fixed in {self.work[:10]}. local edit\n')
        with self.assertRaises(ValueError):
            fleet_beans.remap_rebased_shas(self.repo, 'main')


if __name__ == '__main__':
    unittest.main()
