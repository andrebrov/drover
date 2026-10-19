"""bean-2qw1: fleet-mignum must allocate above four-digit migration prefixes.

Real Git in an isolated HOME/repo. The tool is exercised as the fleet invokes it
(`bash bin/fleet-mignum <bean>`), so the assertions cover the prefix scan, the
numeric high-water mark and the reservation file together.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]
TOOL = SOURCE/'bin/fleet-mignum'


class Mignum(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("FLEET_PROOF_SCRATCH"))
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home = root/'home'; self.home.mkdir()
        self.repo = root/'repo'
        (self.repo/'backend/src/db/migrations').mkdir(parents=True)
        self.res = self.home/'state/migration-reservations.tsv'
        self.res.parent.mkdir(parents=True)
        self.res.write_text('')
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(HOME=str(self.home), FLEET_ROOT=str(self.repo), DROVER_REPO=str(self.repo), DROVER_HOME=str(self.home/'state'), DROVER_MIGRATIONS_DIR='backend/src/db/migrations', GIT_CONFIG_NOSYSTEM='1')
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'core.hooksPath', '/dev/null')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], env=self.env, text=True)

    def land(self, numbers):
        """Commit migration files and publish them as origin/main (the tool's scan target)."""
        for number in numbers:
            (self.repo/f'backend/src/db/migrations/{number}_fixture.sql').write_text('select 1;\n')
        self.git('add', '-A')
        self.git('commit', '-qm', 'migrations')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')

    def reserve_by_hand(self, number, bean):
        with self.res.open('a') as handle:
            handle.write(f'{number}\t{bean}\t2026-10-19T00:00:00Z\n')

    def allocate(self, bean):
        return subprocess.run(['bash', str(TOOL), bean], env=self.env, capture_output=True, text=True, timeout=30)

    def assert_allocates(self, bean, expected):
        result = self.allocate(bean)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual(result.stdout.strip(), expected, result.stdout+result.stderr)

    def test_allocates_above_four_digit_prefixes(self):
        self.land(['1024', '1025'])
        self.assert_allocates('bean-fixture', '1026')

    def test_mixed_width_prefixes_sort_numerically(self):
        self.land(['099', '1000', '1025'])
        self.assert_allocates('bean-fixture', '1026')

    def test_numeric_max_beats_lexicographic_max(self):
        # '999' is the lexicographically largest prefix but 1000 is the highest number.
        self.land(['999', '1000'])
        self.assert_allocates('bean-fixture', '1001')

    def test_reserved_but_unlanded_number_is_never_handed_out_again(self):
        self.land(['1024'])
        self.reserve_by_hand('1025', 'bean-other')  # reserved, never landed as a file
        self.assert_allocates('bean-newcomer', '1026')
        self.assert_allocates('bean-other', '1025')  # the reservation itself is idempotent

    def test_same_bean_reuses_its_reservation(self):
        self.land(['1024', '1025'])
        self.assert_allocates('bean-fixture', '1026')
        self.assert_allocates('bean-fixture', '1026')
        self.assertEqual(self.res.read_text().count('bean-fixture'), 1)


if __name__ == '__main__':
    unittest.main()
