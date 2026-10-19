"""bean-fo07: current task ownership, never advisory file claims or inferred releases."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BIN = ROOT / 'bin/fleet-claims-audit'
FIXTURES = HERE / 'fixtures/claims-audit'


class ClaimsAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('FLEET_PROOF_SCRATCH'))
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'fixture'

    def fixture(self, name='clean'):
        shutil.copytree(FIXTURES / name, self.path)
        (self.path / 'seat-state').mkdir(exist_ok=True)
        repo = self.path / 'repo'
        repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (repo / '.beans').mkdir()
        for bid, status in json.loads((self.path / 'beans.json').read_text()).items():
            (repo / '.beans' / f'{bid}--fixture.md').write_text(f'---\nstatus: {status}\n---\n')
        self.git('add', '.beans')
        self.git('commit', '-qm', 'fixture board')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        return self.path

    def git(self, *args):
        return subprocess.check_output(['/usr/bin/git', '-C', str(self.path / 'repo'), *args], stderr=subprocess.DEVNULL)

    def run_audit(self):
        p = subprocess.run([sys.executable, str(BIN), '--fixture', str(self.path), '--json'],
                           capture_output=True, text=True, timeout=15)
        self.assertTrue(p.stdout.strip(), p.stderr)
        return p, json.loads(p.stdout)

    def test_duplicate_current_seats_flagged_before_other_findings(self):
        self.fixture('duplicate')
        (self.path / 'tasks/malformed').write_text('not a task snapshot\n')
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 2, doc)
        self.assertEqual(doc['status'], 'unknown')
        self.assertEqual(doc['findings'][0]['identity'], 'DUPLICATE_CURRENT_SEATS')
        self.assertEqual(doc['findings'][0]['seats'], ['alpha', 'beta'])
        self.assertEqual(doc['latest_owners']['bean-a001'], 'beta')

    def test_clean_native_sources_are_quiet(self):
        self.fixture()
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 0, doc)
        self.assertEqual(doc['status'], 'ok')
        self.assertEqual(doc['findings'], [])
        self.assertEqual(doc['current_assignments'], {'alpha': 'bean-a001'})

    def test_completed_current_owner_is_not_accepted(self):
        self.fixture('completed')
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 1, doc)
        self.assertEqual(doc['findings'][0]['identity'], 'CLOSED_BEAN_OWNED')

    def test_disagreement_is_unknown_not_one_sources_winner(self):
        self.fixture('disagreement')
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 2, doc)
        self.assertEqual(doc['findings'][0]['identity'], 'OWNER_TASK_DISAGREEMENT')
        self.assertEqual(doc['findings'][0]['owner_bean'], 'bean-a001')
        self.assertEqual(doc['findings'][0]['task_bean'], 'bean-a002')

    def test_missing_owner_source_is_unknown(self):
        self.fixture('unavailable')
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 2, doc)
        self.assertIn('OWNERS_UNAVAILABLE', [x['identity'] for x in doc['findings']])

    def test_older_owner_for_reassigned_seat_is_not_a_second_live_claim(self):
        self.fixture()
        (self.path / 'config/assigned-owners.txt').write_text('bean-a002 alpha @900\nbean-a001 alpha @1000\n')
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 0, doc)

    def test_latest_owner_per_bean_and_latest_assignment_per_seat_agree(self):
        self.fixture('duplicate')
        with (self.path / 'config/assigned-owners.txt').open('a') as f:
            f.write('bean-a002 alpha @1002\n')
        (self.path / 'tasks/alpha').write_text('1002\tbean-a002\tabc123\t/report.md\n')
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 0, doc)
        self.assertEqual(doc['latest_owners'], {'bean-a001': 'beta', 'bean-a002': 'alpha'})

    def test_bean_status_comes_from_origin_main_not_dirty_worktree(self):
        self.fixture()
        (self.path / 'repo/.beans/bean-a001--fixture.md').write_text('---\nstatus: completed\n---\n')
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 0, doc)
        self.assertEqual(doc['main_sha'], self.git('rev-parse', 'origin/main').decode().strip())

    def test_closed_historical_owner_is_not_a_live_claim(self):
        self.fixture()
        f = self.path / 'repo/.beans/bean-a002--fixture.md'
        f.write_text('---\nstatus: completed\n---\n')
        self.git('add', '.beans'); self.git('commit', '-qm', 'close old assignment')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        (self.path / 'config/assigned-owners.txt').write_text('bean-a002 alpha @900\nbean-a001 alpha @1000\n')
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 0, doc)

    def test_missing_native_snapshot_is_unknown(self):
        self.fixture()
        (self.path / 'tasks/alpha').unlink()
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 2, doc)
        self.assertIn('TASK_UNAVAILABLE', [f['identity'] for f in doc['findings']])

    def test_native_task_without_owner_is_unknown(self):
        self.fixture()
        (self.path / 'config/assigned-owners.txt').write_text('')
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 2, doc)
        self.assertIn('OWNER_UNAVAILABLE', [f['identity'] for f in doc['findings']])

    def test_missing_roster_pane_requires_matching_pending_marker(self):
        self.fixture()
        (self.path / 'roster.json').write_text('{"result":{"agents":[]}}')
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 2, doc)
        (self.path / 'seat-state/alpha.pending').write_text('2026-10-19T00:00:00Z\tbean-a001\tBLOCKED\n')
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 0, doc)

    def test_different_pending_bean_does_not_confirm_ownership(self):
        self.fixture()
        (self.path / 'roster.json').write_text('{"result":{"agents":[]}}')
        (self.path / 'seat-state/alpha.pending').write_text('2026-10-19T00:00:00Z\tbean-a002\tBLOCKED\n')
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 2, doc)

    def test_no_ask_ttl_or_advisory_file_claims_are_consulted(self):
        self.fixture()
        (self.path / 'config/claims.tsv').write_text('alpha\tbean-a001\tfirst\t2020\nalpha\tbean-a001\tsecond\t2020\n')
        (self.path / 'questions').mkdir()
        (self.path / 'questions/old.json').write_text('{"asked_at":"2000-01-01T00:00:00Z","seat":"alpha","bean":"bean-a002"}')
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 0, doc)

    def test_runtime_status_does_not_invent_task_ownership(self):
        self.fixture()
        roster = json.loads((self.path / 'roster.json').read_text())
        roster['result']['agents'].append({'name': 'daemon-seat', 'pane_id': 'p2', 'agent_status': 'working'})
        (self.path / 'roster.json').write_text(json.dumps(roster))
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 0, doc)

    def test_malformed_owner_line_is_unknown_not_silently_dropped(self):
        self.fixture()
        with (self.path / 'config/assigned-owners.txt').open('a') as f: f.write('broken\n')
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 2, doc)

    def test_audit_leaves_all_source_bytes_unchanged(self):
        self.fixture('duplicate')
        def snapshot():
            return {str(p.relative_to(self.path)): p.read_bytes() for p in self.path.rglob('*') if p.is_file() and '.git' not in p.parts}
        before = snapshot()
        self.run_audit()
        self.assertEqual(snapshot(), before)

    def test_superseded_owner_for_absent_old_bean_does_not_make_current_unknown(self):
        self.fixture()
        (self.path / 'config/assigned-owners.txt').write_text('bean-old1 alpha @900\nbean-a001 alpha @1000\n')
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 0, doc)

    def test_unnamed_roster_pane_is_not_a_task_owner(self):
        self.fixture()
        r = json.loads((self.path / 'roster.json').read_text())
        r['result']['agents'].append({'name': None, 'pane_id': 'unnamed'})
        (self.path / 'roster.json').write_text(json.dumps(r))
        p, doc = self.run_audit()
        self.assertEqual(p.returncode, 0, doc)


    def standup(self, fixture):
        self.fixture(fixture)
        home = self.path / 'standup-home'
        report_dir = home / '.local/share/fleet-reports'
        report_dir.mkdir(parents=True)
        repo_path = home / 'IdeaProjects/project'
        repo_path.parent.mkdir(parents=True); repo_path.symlink_to(self.path / 'repo', target_is_directory=True)
        config = home / '.config/herdr'; config.mkdir(parents=True)
        for name in ['linear-seats.txt', 'linear-map.tsv']:
            (config / name).write_text('')
        (report_dir / 'deploys.log').write_text('now\t'+self.git('rev-parse', 'HEAD').decode().strip()+'\n')
        spec = importlib.util.spec_from_file_location('fixture_stamp', ROOT / 'lib/fleet_stamp.py')
        stamp = importlib.util.module_from_spec(spec); spec.loader.exec_module(stamp)
        stamp.stamp(report_dir / 'measure/fixture.json', ROOT / 'bin/fleet-measure',
                    {'landings_4h': 3, 'unlanded_branches_60m': 0, 'stuck_seats': 0, 'prod_lag_commits': 0})
        stubs = self.path / 'stub-bin'; stubs.mkdir()
        commands = {
            'git': '#!/bin/sh\ncase "$*" in *branch*) echo fleet/alpha-bean-a001;; *log*) echo fixture;; esac\nexit 0\n',
            'herdr': '#!/bin/sh\ncat "$FLEET_CLAIMS_AUDIT_FIXTURE/roster.json"\n',
            'fleet-stale-jobs': '#!/bin/sh\necho "- none"\n',
            'fleet-dispatch': '#!/bin/sh\necho "fixture board"\n',
        }
        for name, text in commands.items():
            f = stubs / name; f.write_text(text); f.chmod(0o755)
        lessons = home / 'lessons.jsonl'; lessons.write_text('')
        env = dict(os.environ, HOME=str(home), FLEET_CLAIMS_AUDIT_FIXTURE=str(self.path),
                   FLEET_LESSONS=str(lessons), DROVER_REPO=str(self.path/'repo'), FLEET_REPORTS=str(report_dir), DROVER_STALE_JOBS_COMMAND='fleet-stale-jobs', PATH=str(stubs)+os.pathsep+os.environ['PATH'])
        p = subprocess.run(['bash', str(ROOT / 'bin/fleet-standup')], env=env,
                           capture_output=True, text=True, timeout=20)
        reports = list(report_dir.glob('standup-*.md'))
        self.assertEqual(len(reports), 1, p.stderr)
        return p, reports[0].read_text()

    def test_standup_propagates_unknown_ownership(self):
        p, report = self.standup('duplicate')
        self.assertEqual(p.returncode, 2, p.stderr + report)
        self.assertIn('UNKNOWN DUPLICATE_CURRENT_SEATS', report)

    def test_standup_propagates_confirmed_violation(self):
        p, report = self.standup('completed')
        self.assertEqual(p.returncode, 1, p.stderr + report)
        self.assertIn('CLOSED_BEAN_OWNED', report)

    def test_standup_clean_ownership_preserves_other_sections(self):
        p, report = self.standup('clean')
        self.assertEqual(p.returncode, 0, p.stderr + report)
        self.assertIn('fleet-claims-audit: OK', report)
        self.assertIn('landings_4h: 3', report)
        self.assertIn('Production stale jobs', report)


if __name__ == '__main__':
    unittest.main()
