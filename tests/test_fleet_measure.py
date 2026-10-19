#!/usr/bin/env python3
"""Execute the real scoreboard with disposable Git history and alert files."""
import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from fleet_stamp import stamp as write_stamped  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / 'bin/fleet-measure'
SCRATCH = Path(tempfile.gettempdir())


class FleetMeasureTest(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='8r79-test-', dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = self.home / 'IdeaProjects/project'
        self.root.mkdir(parents=True)
        self.alerts = self.home / '.local/share/fleet-watch/state/alerts'
        self.alerts.mkdir(parents=True)
        self.now = int(time.time())
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Fixture')
        self.commit('base', 'base', age=86400)
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.remote = self.home / 'origin.git'
        self.git('init', '-q', '--bare', str(self.remote))
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', '-q', 'origin', 'main')
        self.alert('stuck.txt', 0)

    def git(self, *args, env=None):
        clean = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(self.root), *args],
                              env=dict(clean, **(env or {})), text=True, capture_output=True, check=True).stdout

    def commit(self, filename, subject, age=7200):
        (self.root / filename).write_text(subject + '\n')
        self.git('add', filename)
        date = dt.datetime.fromtimestamp(self.now - age, dt.timezone.utc).isoformat()
        self.git('commit', '-qm', subject, env={'GIT_AUTHOR_DATE': date, 'GIT_COMMITTER_DATE': date})

    def alert(self, name, count, age=0):
        path = self.alerts / name
        path.write_text(''.join(f'fixture-{i}: pending\n' for i in range(count)))
        os.utime(path, (self.now - age, self.now - age))
        if name == 'unlanded.txt':  # fleet-measure certifies its own snapshot with a stamped receipt (bean-90dj)
            write_stamped(self.alerts / 'unlanded.txt.ok', SCRIPT, {'rows': count}, now=self.now - age)

    def measure(self, git_failure=False, commands=None):
        original_run = subprocess.run

        def external(args, **kwargs):
            if commands is not None and args[0] == 'git':
                commands.append(list(args))
            if args[0] == 'herdr':
                return subprocess.CompletedProcess(args, 0, '{"result":{"agents":[]}}', '')
            if git_failure and args[0] == 'git' and (git_failure is True or args[1] == git_failure):
                if kwargs.get('check'):
                    raise subprocess.CalledProcessError(1, args, '', 'fixture Git unavailable')
                return subprocess.CompletedProcess(args, 1, '', 'fixture Git unavailable')
            return original_run(args, **kwargs)

        output = io.StringIO()
        with mock.patch.dict(os.environ, HOME=str(self.home), FLEET_ROOT=str(self.root), DROVER_REPO=str(self.root), FLEET_STATE=str(self.home / '.local/share/fleet-watch/state'), FLEET_REPORTS=str(self.home / '.local/share/fleet-reports')), \
                mock.patch.object(sys, 'argv', [str(SCRIPT), '--no-render']), \
                mock.patch('subprocess.run', side_effect=external), \
                mock.patch('urllib.request.urlopen', side_effect=OSError('fixture offline')), \
                contextlib.redirect_stdout(output):
            runpy.run_path(str(SCRIPT), run_name='__main__')
        snapshots = [p for p in (self.home / '.local/share/fleet-reports/measure').glob('*.json')
                     if p.name != '20000101-0000.json']
        self.assertTrue(snapshots, 'writer produced no complete measurement')
        current = max(snapshots, key=lambda p: p.stat().st_mtime_ns)
        return output.getvalue(), json.loads(current.read_text())['payload']

    @staticmethod
    def patch_computations(commands):
        return [args for args in commands if args[1] in ('cherry', 'diff-tree', 'patch-id')
                or (args[1] == 'log' and '--stdin' in args)]

    def test_many_distinct_tips_use_batched_git_not_one_walk_per_branch(self):
        for i in range(24):
            self.git('checkout', '-qB', f'fleet/coder-bean-b{i:03}', self.base)
            self.commit(f'pending-{i}', f'feat(bean-b{i:03}): pending')
        calls = []
        text, metrics = self.measure(commands=calls)
        self.assertEqual(metrics['unlanded_branches_60m'], 24, text)
        self.assertFalse(any(args[1] == 'cherry' for args in calls), calls)
        self.assertLessEqual(len(self.patch_computations(calls)), 3, calls)

    def test_unchanged_tip_reuses_cache_but_changed_tip_recomputes(self):
        self.git('checkout', '-qb', 'fleet/coder-bean-old1')
        self.commit('one', 'feat(bean-old1): first pending')
        self.measure()
        unchanged = []
        _, metrics = self.measure(commands=unchanged)
        self.assertEqual(metrics['unlanded_branches_60m'], 1)
        self.assertEqual(self.patch_computations(unchanged), [])
        self.commit('two', 'feat(bean-old1): another pending')
        changed = []
        text, metrics = self.measure(commands=changed)
        self.assertEqual(metrics['unlanded_branches_60m'], 1, text)
        self.assertIn('2 unlanded bean commit(s)', (self.alerts / 'unlanded.txt').read_text())
        self.assertTrue(self.patch_computations(changed))
        repeated = []
        self.measure(commands=repeated)
        self.assertEqual(self.patch_computations(repeated), [])

    def test_cached_tip_is_rechecked_when_main_lands_an_equivalent_patch(self):
        self.git('checkout', '-qb', 'fleet/coder-bean-old1')
        self.commit('one', 'feat(bean-old1): pending')
        tip = self.git('rev-parse', 'HEAD').strip()
        self.assertEqual(self.measure()[1]['unlanded_branches_60m'], 1)
        self.git('checkout', '-q', 'main')
        self.git('cherry-pick', tip)
        self.git('commit', '--amend', '-qm', 'fix(bean-old1): accepted under another subject')
        self.git('push', '-q', 'origin', 'main')
        text, metrics = self.measure()
        self.assertEqual(metrics['unlanded_branches_60m'], 0, text)

    def test_old_archive_candidate_stays_counted_without_repeated_patch_work(self):
        self.git('checkout', '-qb', 'fleet/coder-bean-old1')
        self.commit('old', 'feat(bean-old1): old pending work', age=30 * 86400)
        self.assertEqual(self.measure()[1]['unlanded_branches_60m'], 1)
        repeated = []
        text, metrics = self.measure(commands=repeated)
        self.assertEqual(metrics['unlanded_branches_60m'], 1, text)
        self.assertEqual(self.patch_computations(repeated), [])
        self.commit('older', 'fix(bean-old1): changed backdated tip', age=29 * 86400)
        self.measure()
        self.assertIn('2 unlanded bean commit(s)', (self.alerts / 'unlanded.txt').read_text())

    def test_patch_in_branch_ancestry_is_not_a_landed_equivalent(self):
        # A revert/reapply must remain pending: its matching old patch is already
        # in this branch's ancestry, not on the other side of git cherry.
        self.commit('same', 'feat(bean-old1): original landed change', age=86400)
        self.git('push', '-q', 'origin', 'main')
        self.git('checkout', '-qb', 'fleet/coder-bean-old1')
        self.git('rm', '-q', 'same')
        self.git('commit', '-qm', 'fix(bean-old1): remove', env={
            'GIT_AUTHOR_DATE': dt.datetime.fromtimestamp(self.now - 7200, dt.timezone.utc).isoformat(),
            'GIT_COMMITTER_DATE': dt.datetime.fromtimestamp(self.now - 7200, dt.timezone.utc).isoformat()})
        self.commit('same', 'feat(bean-old1): original landed change')
        self.assertEqual(sum(row.startswith('+ ') for row in self.git('cherry', 'origin/main', 'HEAD').splitlines()), 2)
        text, metrics = self.measure()
        self.assertEqual(metrics['unlanded_branches_60m'], 1, text)
        self.assertIn('2 unlanded bean commit(s)', (self.alerts / 'unlanded.txt').read_text())

    def test_stale_unlanded_source_is_unmeasured_without_opportunity(self):
        self.alert('unlanded.txt', 78, age=7201)
        text, metrics = self.measure(git_failure=True)
        self.assertIn('unmeasured (UNAVAILABLE(stale))', text)
        self.assertIsNone(metrics['unlanded_branches_60m'])
        self.assertNotIn('78 branches hold', text)

    def test_touched_frozen_file_with_an_old_stamp_is_still_unavailable(self):
        # bean-90dj: the 10-13 count was republished for 5 days; a fresh mtime must not certify it.
        self.alert('unlanded.txt', 78, age=5 * 86400)
        os.utime(self.alerts / 'unlanded.txt', (self.now, self.now))
        text, metrics = self.measure(git_failure=True)
        self.assertIn('UNAVAILABLE(stale)', text)
        self.assertIsNone(metrics['unlanded_branches_60m'])

    def test_unstamped_receipt_is_unavailable_missing_marker(self):
        self.alert('unlanded.txt', 78)
        (self.alerts / 'unlanded.txt.ok').write_text(str(self.now))   # the legacy bare-integer receipt
        text, metrics = self.measure(git_failure=True)
        self.assertIn('UNAVAILABLE(missing_marker)', text)
        self.assertIsNone(metrics['unlanded_branches_60m'])

    def test_fresh_unlanded_snapshot_keeps_count_when_refresh_unavailable(self):
        self.alert('unlanded.txt', 3, age=60)
        text, metrics = self.measure(git_failure=True)
        self.assertEqual(metrics['unlanded_branches_60m'], 3)
        self.assertIn('unlanded > 60 min: 3', text)
        self.assertIn('refresh unavailable', text)

    def test_stale_stuck_source_is_unmeasured_without_restart_opportunity(self):
        self.alert('stuck.txt', 4, age=7201)
        text, metrics = self.measure()
        self.assertIsNone(metrics['stuck_seats'])
        self.assertIn('stuck seats: unmeasured (source stale since ', text)
        self.assertNotIn('stuck seats: restart', text)

    def test_fresh_stuck_source_retains_count_and_opportunity(self):
        self.alert('stuck.txt', 2, age=60)
        text, metrics = self.measure()
        self.assertEqual(metrics['stuck_seats'], 2)
        self.assertIn('stuck seats: restart', text)

    def test_fresh_unlanded_opportunity_uses_current_snapshot(self):
        self.alert('unlanded.txt', 21)
        text, _ = self.measure(git_failure=True)
        self.assertIn('21 branches hold unlanded bean commits', text)
        self.assertIn('current unlanded.txt snapshot', text)
        self.assertNotIn('unlanded-triage-20261013', text)

    def test_failed_branch_observation_preserves_complete_snapshot_and_age(self):
        self.git('checkout', '-qb', 'fleet/coder-bean-old1')
        self.commit('old', 'feat(bean-old1): pending')
        self.alert('unlanded.txt', 3, age=60)
        path = self.alerts / 'unlanded.txt'
        original = (path.read_bytes(), path.stat().st_mtime)
        text, metrics = self.measure(git_failure='diff-tree')
        self.assertEqual(metrics['unlanded_branches_60m'], 3)
        self.assertIn('Git refresh unavailable', text)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime), original)
        self.assertEqual(json.loads((self.alerts / 'unlanded.txt.ok').read_text())['stamp']['computed_at'], self.now - 60)  # receipt not re-certified

    def test_missing_sources_are_unmeasured_not_observed_zero(self):
        (self.alerts / 'stuck.txt').unlink()
        text, metrics = self.measure(git_failure=True)
        self.assertIsNone(metrics['unlanded_branches_60m'])
        self.assertIsNone(metrics['stuck_seats'])
        self.assertIn('source missing', text)

    def test_empty_fresh_source_is_measured_zero(self):
        self.alert('unlanded.txt', 0)
        _, metrics = self.measure(git_failure=True)
        self.assertEqual(metrics['unlanded_branches_60m'], 0)
        self.assertEqual(metrics['stuck_seats'], 0)

    def test_legacy_frozen_count_is_not_used_as_a_delta_baseline(self):
        self.alert('unlanded.txt', 3)
        history = self.home / '.local/share/fleet-reports/measure'
        history.mkdir(parents=True)
        (history / '20000101-0000.json').write_text(json.dumps({'unlanded_branches_60m': 78}))
        text, _ = self.measure(git_failure=True)
        self.assertIn('unlanded > 60 min: 3;', text)
        self.assertNotIn('-75 vs last', text)

    def test_prior_measured_count_still_supports_a_delta(self):
        self.alert('unlanded.txt', 3)
        history = self.home / '.local/share/fleet-reports/measure'
        history.mkdir(parents=True)
        write_stamped(history / '20000101-0000.json', SCRIPT, {
            'unlanded_branches_60m': 1,
            'alert_sources': {'unlanded_branches_60m': 'observed 2026-10-18 08:00 EDT'},
        }, now=self.now - 3600)
        text, _ = self.measure(git_failure=True)
        self.assertIn('unlanded > 60 min: 3 (+2 vs last)', text)

    def test_refill_no_longer_overwrites_measure_owned_snapshot(self):
        self.alert('unlanded.txt', 3)
        snapshot = (self.alerts / 'unlanded.txt').read_bytes()
        with mock.patch.dict(os.environ, HOME=str(self.home), FLEET_ROOT=str(self.root), DROVER_REPO=str(self.root), FLEET_STATE=str(self.home / '.local/share/fleet-watch/state'), FLEET_REPORTS=str(self.home / '.local/share/fleet-reports')):
            ns = runpy.run_path(str(SCRIPT.with_name('fleet-refill')))
        audit = ns['landing_audit']
        with mock.patch.dict(audit.__globals__, run=lambda *a, **k: subprocess.CompletedProcess(a, 0, '', '')):
            audit({}, self.now)
        self.assertEqual((self.alerts / 'unlanded.txt').read_bytes(), snapshot)

    def test_future_dated_source_is_unmeasured(self):
        self.alert('stuck.txt', 4, age=-3600)
        text, metrics = self.measure()
        self.assertIsNone(metrics['stuck_seats'])
        self.assertIn('source timestamp is in the future', text)

    def test_deleted_remote_branch_is_pruned_before_counting(self):
        self.git('checkout', '-qb', 'fixture-history')
        self.commit('deleted', 'feat(bean-gone): no longer a remote branch')
        tip = self.git('rev-parse', 'HEAD').strip()
        self.git('update-ref', 'refs/remotes/origin/fleet/removed-bean-gone', tip)
        self.git('checkout', '-q', 'main')
        _, metrics = self.measure()
        self.assertEqual(metrics['unlanded_branches_60m'], 0)

    def test_live_git_replaces_frozen_count_and_ignores_patch_equivalent_commit(self):
        self.alert('unlanded.txt', 78, age=5 * 86400)
        self.git('checkout', '-qb', 'fleet/coder-bean-old1', self.base)
        self.commit('old', 'feat(bean-old1): still pending')
        self.git('push', '-q', 'origin', 'HEAD:refs/heads/fleet/coder-bean-old1')
        # Remote-only work is included; the matching local+remote old1 counts once.
        self.git('checkout', '-qb', 'fleet/coder-bean-old2', self.base)
        self.commit('remote', 'feat(bean-old2): remote pending')
        self.git('push', '-q', 'origin', 'HEAD:refs/heads/fleet/coder-bean-old2')
        self.git('checkout', '-q', 'main')
        self.git('branch', '-D', 'fleet/coder-bean-old2')
        self.git('checkout', '-qb', 'fleet/coder-bean-new1', self.base)
        self.commit('new', 'feat(bean-new1): recent work', age=1800)
        self.git('checkout', '-qb', 'fleet/no-bean', self.base)
        self.commit('unowned', 'scratch without a bean')
        self.git('checkout', '-qb', 'fleet/coder-bean-eqvl', self.base)
        self.commit('equivalent', 'feat(bean-eqvl): original')
        queued = self.git('rev-parse', 'HEAD').strip()
        self.git('checkout', '-q', 'main')
        self.git('cherry-pick', queued)
        self.git('commit', '--amend', '-qm', 'feat(bean-eqvl): landed with a new subject')
        self.git('push', '-q', 'origin', 'main')
        self.assertTrue(self.git('cherry', 'origin/main', queued, queued + '^').startswith('- '))
        text, metrics = self.measure()
        self.assertEqual(metrics['unlanded_branches_60m'], 2, text)
        self.assertIn('unlanded > 60 min: 2', text)
        self.assertNotIn('78 branches hold', text)
        rows = (self.alerts / 'unlanded.txt').read_text()
        self.assertIn('fleet/coder-bean-old1', rows)
        self.assertIn('fleet/coder-bean-old2', rows)
        self.assertNotIn('eqvl', rows)
        self.assertNotIn('new1', rows)
        completed = self.alerts / 'unlanded.txt.ok'
        self.assertTrue(completed.exists(), 'the live owner must also update the preflight completion receipt')
        self.assertGreaterEqual(json.loads(completed.read_text())['stamp']['computed_at'], self.now)


if __name__ == '__main__':
    unittest.main()
