"""bean-l8ck: payload, inert HTML, source-failure and read-only witnesses."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
import hashlib
import time
from html.parser import HTMLParser
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'lib/fleet_observatory.py'
ART = Path(tempfile.gettempdir())
NOW = 1800000000


def implementation():
    if not MODULE.exists():
        return None
    spec = importlib.util.spec_from_file_location('observatory_subject', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Fixture:
    def __init__(self, base):
        self.base = Path(base)
        self.home = self.base / 'home'
        self.repo = self.base / 'repo'
        self.config = self.home / '.config/drover'
        self.tasks = self.home / '.local/share/drover/tasks'
        self.reports = self.home / '.local/share/drover/reports'
        for p in (self.config, self.tasks, self.reports / 'questions', self.reports / 'decisions', self.reports / 'measure', self.home / '.local/share/drover/inbox', self.repo / '.beans'):
            p.mkdir(parents=True)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.test')
        for bid in ('bean-a001', 'bean-b002', 'bean-c003'):
            (self.repo / '.beans' / (bid + '--fixture.md')).write_text(f'---\ntitle: {bid} fixture\nstatus: todo\ntype: task\npriority: high\n---\nFounder: parked for approval\n')
        self.git('add', '.beans')
        self.git('commit', '-qm', 'feat: delivered fixture (bean-c003)')
        self.landed = self.git('rev-parse', 'HEAD').strip()
        self.git('update-ref', 'refs/remotes/origin/main', self.landed)
        self.git('checkout', '-qb', 'fleet/worker-bean-b002')
        (self.repo / 'change.txt').write_text('candidate')
        self.git('add', 'change.txt')
        self.git('commit', '-qm', 'feat: unlanded candidate (bean-b002)')
        self.unlanded = self.git('rev-parse', 'HEAD').strip()
        self.write(self.base / 'herdr.json', json.dumps({'result': {'agents': [
            {'name': 'worker', 'agent': 'codex', 'agent_status': 'working', 'cwd': str(self.repo)},
            {'name': 'reviewer', 'agent': 'claude', 'agent_status': 'done', 'cwd': str(self.repo)},
        ]}}))
        self.write(self.base / 'dispatch.json', json.dumps({'board_ready': 3, 'dispatchable_count': 1, 'dispatchable': [{'id': 'bean-a001', 'title': 'queued'}], 'summary': 'board_ready=3 dispatchable=1'}))
        self.write(self.tasks / 'worker', f'{NOW-120}\tbean-b002\tnone\t{self.reports}/worker-bean-b002.md\n')
        self.write(self.tasks / 'reviewer', f'{NOW-180}\tbean-c003\tnone\t{self.reports}/reviewer-bean-c003.md\n')
        self.write(self.reports / 'reviewer-bean-c003.md', f'DONE — landed {self.landed}\n')
        self.write(self.reports / 'questions/q-open.json', json.dumps({'id': 'q-open', 'bean': 'bean-b002', 'seat': 'worker', 'question': '<script>alert(1)</script>', 'asked_at': '2027-01-15T07:58:00Z'}))
        self.write(self.reports / 'questions/q-answered.json', json.dumps({'id': 'q-answered', 'bean': 'bean-c003', 'seat': 'reviewer', 'question': 'closed', 'asked_at': '2027-01-15T07:58:00Z'}))
        self.write(self.reports / 'decisions/q-answered.json', json.dumps({'id': 'q-answered', 'decision': 'defer', 'at': '2027-01-15T07:59:00Z'}))
        self.write(self.config / 'dispatch-skip', 'bean-a001\n')
        self.write(self.home / '.local/share/drover/inbox/notice.msg', f'{NOW-20}\tworker\tbean-b002\tPARTIAL\tready\t{self.reports}/worker-bean-b002.md\n')
        self.write(self.reports / 'deploys.log', f'2027-01-15 07:59:00\t{self.landed[:7]}\tDEPLOYED (probe=pass)\n')
        self.write(self.reports / 'measure/latest.json', json.dumps({'pending_migrations': 4}))

    def git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1')
        if args[0] == 'commit':
            env.update(GIT_AUTHOR_DATE=f'@{NOW-180} +0000', GIT_COMMITTER_DATE=f'@{NOW-180} +0000')
        return subprocess.check_output([shutil.which('git'), '-C', str(self.repo), *args], env=env, text=True)

    def write(self, path, text):
        path.write_text(text)
        os.utime(path, (NOW-30, NOW-30))

    def snapshot(self):
        module = implementation()
        return module.collect_snapshot(self.home, self.repo, now=NOW, fixture=self.base) if module else {'panels': {}, 'sources': {}}

    def html(self):
        module = implementation()
        return module.render_html(self.snapshot()) if module else ''

    def owners(self):
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for root in (self.config, self.tasks) for p in root.rglob('*') if p.is_file()}


class ObservatoryTests(unittest.TestCase):
    def setUp(self):
        ART.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ART)
        self.addCleanup(self.temp.cleanup)
        self.f = Fixture(self.temp.name)

    def test_known_good_two_seats_three_beans_question_and_decision(self):
        s = self.f.snapshot()
        p = s['panels']
        self.assertEqual(len(p.get('agents', {}).get('items', [])), 2)
        self.assertEqual(p['agents']['items'][0]['elapsed_seconds'], 120)
        self.assertEqual(p['assignment_board']['counts'], {'queued': 1, 'running': 1, 'awaiting_review': 0, 'blocked': 0, 'landed': 1})
        self.assertEqual(p['operator_attention']['open_questions'][0]['id'], 'q-open')
        self.assertEqual(len(p['operator_attention']['open_questions']), 1)
        self.assertEqual(len(p['operator_attention']['founder_parked']), 1)
        self.assertEqual(p['production']['pending_migrations'], 4)
        self.assertEqual(p['production']['undeployed_commits'], 0)
        self.assertEqual(set(p), {'agents', 'assignment_board', 'operator_attention', 'activity', 'evidence', 'production'})
        self.assertTrue(all(panel['sources'] for panel in p.values()))

    def test_report_saying_landed_cannot_accept_unlanded_commit(self):
        self.f.write(self.f.reports / 'worker-bean-b002.md', f'PARTIAL — LANDED {self.f.unlanded}\n')
        s = self.f.snapshot()['panels'].get('assignment_board', {})
        self.assertEqual(s.get('counts', {}).get('awaiting_review'), 1)
        self.assertEqual(s['counts']['landed'], 1)
        self.assertNotIn('bean-b002', [item['bean'] for item in s['landed']])

    def test_borrowed_other_bean_sha_is_not_acceptance(self):
        self.f.write(self.f.reports / 'worker-bean-b002.md', f'DONE — LANDED {self.f.landed}\n')
        p = self.f.snapshot()['panels'].get('assignment_board', {})
        self.assertEqual(p.get('counts', {}).get('awaiting_review'), 1)
        self.assertEqual(p['counts']['landed'], 1)

    def test_report_heading_status_is_classified_without_accepting_prose(self):
        self.f.write(self.f.reports / 'worker-bean-b002.md', '# bean-b002 (worker) — BLOCKED on operator\nVerdict: no seat work\nDONE /report/path\n')
        panel = self.f.snapshot()['panels']['assignment_board']
        self.assertEqual(panel['counts']['blocked'], 1)
        self.assertEqual(panel['blocked'][0]['bean'], 'bean-b002')
        self.assertEqual(panel['counts']['awaiting_review'], 0)
        self.assertEqual(panel['counts']['landed'], 1)
        self.f.write(self.f.reports / 'worker-bean-b002.md', '# bean-b002 — PARTIAL\nLANDED according to prose only\n')
        panel = self.f.snapshot()['panels']['assignment_board']
        self.assertEqual(panel['counts']['awaiting_review'], 1)
        self.assertEqual(panel['counts']['landed'], 1)

    def set_origin_status(self, status):
        path = self.f.repo / '.beans/bean-b002--fixture.md'
        local = path.read_text()
        path.write_text(local.replace('status: todo', 'status: ' + status))
        self.f.git('add', '.beans')
        # Deliberately no bean ID or SHA in the lifecycle commit/report.
        self.f.git('commit', '-qm', 'docs: lifecycle state')
        self.f.git('update-ref', 'refs/remotes/origin/main', self.f.git('rev-parse', 'HEAD').strip())
        path.write_text(local)  # stale local checkout must not decide the stage

    def test_terminal_origin_status_removes_stale_verification_and_blocked_reports(self):
        for status in ('completed', 'scrapped'):
            self.set_origin_status(status)
            for verdict in ('DONE', 'PARTIAL', 'BLOCKED'):
                with self.subTest(status=status, verdict=verdict):
                    self.f.write(self.f.reports / 'worker-bean-b002.md', verdict + ' — historical report\n')
                    panel = self.f.snapshot()['panels']['assignment_board']
                    for stage in ('awaiting_review', 'blocked'):
                        self.assertNotIn('bean-b002', [row['bean'] for row in panel[stage]])
                    # A closed bean without a cited implementation SHA is dropped;
                    # it must not invent a landing date for the "landed today" KPI.
                    self.assertNotIn('bean-b002', [row['bean'] for row in panel['landed']])

    def test_local_closed_status_cannot_hide_an_open_origin_blocker(self):
        path = self.f.repo / '.beans/bean-b002--fixture.md'
        path.write_text(path.read_text().replace('status: todo', 'status: completed'))
        self.f.write(self.f.reports / 'worker-bean-b002.md', 'BLOCKED — needs operator\n')
        panel = self.f.snapshot()['panels']['assignment_board']
        self.assertEqual([row['bean'] for row in panel['blocked']], ['bean-b002'])

    def test_newest_blocked_report_wins_over_a_prior_landing_even_when_it_cites_that_sha(self):
        older = self.f.reports / 'first-bean-c003.md'
        self.f.write(older, f'DONE {self.f.landed}\n')
        os.utime(older, (NOW-90, NOW-90))
        newest = self.f.reports / 'reviewer-bean-c003.md'
        self.f.write(newest, f'BLOCKED — remaining operator action\nEarlier slice landed {self.f.landed}\n')
        panel = self.f.snapshot()['panels']['assignment_board']
        self.assertEqual([row['bean'] for row in panel['blocked']], ['bean-c003'])
        self.assertEqual(panel['counts']['landed'], 0)
        self.assertEqual(len([r for r in self.f.snapshot()['panels']['evidence']['reports'] if r['bean']=='bean-c003']), 1)

    def test_newest_partial_report_does_not_reuse_its_earlier_slice_as_completion(self):
        self.f.write(self.f.reports / 'reviewer-bean-c003.md', f'PARTIAL — more work remains\nEarlier slice {self.f.landed}\n')
        panel = self.f.snapshot()['panels']['assignment_board']
        self.assertEqual([row['bean'] for row in panel['awaiting_review']], ['bean-c003'])
        self.assertEqual(panel['counts']['landed'], 0)

    def test_newest_done_report_wins_over_an_older_blocker(self):
        older = self.f.reports / 'old-bean-c003.md'
        self.f.write(older, 'BLOCKED — obsolete\n')
        os.utime(older, (NOW-90, NOW-90))
        panel = self.f.snapshot()['panels']['assignment_board']
        self.assertEqual([row['bean'] for row in panel['landed']], ['bean-c003'])
        self.assertEqual(panel['counts']['blocked'], 0)

    def test_body_status_is_not_a_terminal_frontmatter_status(self):
        path = self.f.repo / '.beans/bean-b002--fixture.md'
        path.write_text(path.read_text() + '\nstatus: completed\n')
        self.f.git('add', '.beans'); self.f.git('commit', '-qm', 'docs: quoted old status')
        self.f.git('update-ref', 'refs/remotes/origin/main', self.f.git('rev-parse', 'HEAD').strip())
        self.f.write(self.f.reports / 'worker-bean-b002.md', 'BLOCKED — current blocker\n')
        self.assertEqual(self.f.snapshot()['panels']['assignment_board']['blocked'][0]['bean'], 'bean-b002')

    def test_status_lookup_failure_is_unavailable_instead_of_stale_counts(self):
        module = implementation()
        original = module.Reader.command
        def command(reader, key, args, **kwargs):
            if 'grep' in args:
                def unavailable():
                    raise OSError('fixture status lookup unavailable')
                reader.record(key, unavailable)
                return None
            return original(reader, key, args, **kwargs)
        with patch.object(module.Reader, 'command', command):
            snapshot = module.collect_snapshot(self.f.home, self.f.repo, now=NOW, fixture=self.f.base)
        panel = snapshot['panels']['assignment_board']
        for stage in ('awaiting_review', 'blocked', 'landed'):
            self.assertIsNone(panel['counts'][stage])
        self.assertEqual(panel['status'], 'PARTIAL')
        self.assertTrue(any(s['status']=='UNAVAILABLE' and 'status lookup' in (s['error'] or '')
                            for s in snapshot['sources'].values()))

    def test_one_origin_revision_is_used_when_main_moves_during_the_snapshot(self):
        self.f.write(self.f.reports / 'worker-bean-b002.md', 'BLOCKED — current blocker\n')
        before = self.f.landed
        self.set_origin_status('completed')
        after = self.f.git('rev-parse', 'origin/main').strip()
        self.f.git('update-ref', 'refs/remotes/origin/main', before)
        module = implementation()
        original = module.Reader.git
        def git(reader, repo, *args, **kwargs):
            result = original(reader, repo, *args, **kwargs)
            if args == ('rev-parse', '--verify', 'origin/main^{commit}'):
                self.f.git('update-ref', 'refs/remotes/origin/main', after)
            return result
        with patch.object(module.Reader, 'git', git):
            snapshot = module.collect_snapshot(self.f.home, self.f.repo, now=NOW, fixture=self.f.base)
        panel = snapshot['panels']['assignment_board']
        self.assertEqual(panel['origin_main_sha'], before)
        self.assertEqual([row['bean'] for row in panel['blocked']], ['bean-b002'])

    def test_abbreviated_sha_still_needs_verified_ancestry(self):
        self.f.write(self.f.reports / 'reviewer-bean-c003.md', f'DONE — commit {self.f.landed[:10]}\n')
        self.f.write(self.f.reports / 'worker-bean-b002.md', f'DONE — commit {self.f.unlanded[:10]}\n')
        panel = self.f.snapshot()['panels']['assignment_board']
        self.assertEqual(panel['counts']['landed'], 1)
        self.assertEqual(panel['landed'][0]['sha'], self.f.landed)
        self.assertEqual(panel['counts']['awaiting_review'], 1)

    def test_missing_origin_main_cannot_report_zero_landed_or_unlanded(self):
        self.f.git('update-ref', '-d', 'refs/remotes/origin/main')
        panel = self.f.snapshot()['panels']['assignment_board']
        self.assertIsNone(panel['counts']['landed'])
        self.assertIsNone(panel['counts']['awaiting_review'])
        self.assertEqual(panel['status'], 'PARTIAL')

    def test_known_bad_unreadable_source_is_unavailable_with_error(self):
        path = self.f.base / 'herdr.json'
        path.chmod(0)
        self.addCleanup(lambda: path.chmod(0o600))
        s = self.f.snapshot()
        self.assertEqual(s['panels'].get('agents', {}).get('status'), 'UNAVAILABLE')
        self.assertIsNone(s['panels']['agents']['items'])
        page = self.f.html()
        self.assertIn('UNAVAILABLE', page)
        self.assertIn('Permission', page)
        self.assertIn(str(path), page)

    def test_foreign_report_title_and_question_are_inert_text(self):
        self.f.write(self.f.reports / 'worker-bean-b002.md', 'BLOCKED <script>alert(1)</script><img src="https://example.test/pixel">\n')
        page = self.f.html()
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', page)
        self.assertNotIn('<script>', page)
        self.assertNotIn('<img ', page)
        self.assertIn('file://', page)
        self.assertIn('http-equiv="refresh" content="30"', page)
        self.assertEqual(page.count('class="sources"'), 6)
        self.assertIn('mtime', page)

    def test_missing_measure_number_is_unknown_not_zero(self):
        self.f.write(self.f.reports / 'measure/latest.json', json.dumps({'prod_lag_commits': 10}))
        p = self.f.snapshot()['panels'].get('production', {})
        self.assertIsNone(p.get('pending_migrations'))
        self.assertIn('UNAVAILABLE', self.f.html())

    def test_deployment_local_clock_is_converted_before_event_sorting(self):
        previous = os.environ.get('TZ')
        def restore():
            if previous is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = previous
            time.tzset()
        self.addCleanup(restore)
        os.environ['TZ'] = 'America/New_York'
        time.tzset()
        self.f.write(self.f.reports / 'deploys.log', f'2026-10-14 09:08:00\t{self.f.landed[:7]}\tDEPLOYED (probe=pass)\n')
        events = self.f.snapshot()['panels']['activity']['events']
        deployment = next(e for e in events if e['kind'] == 'deployment')
        self.assertEqual(deployment['at'], '2026-10-14T13:08:00+00:00')

    def test_snapshot_and_page_do_not_change_owner_files(self):
        before = self.f.owners()
        snapshot = self.f.snapshot()
        self.assertEqual(len(snapshot['panels'].get('agents', {}).get('items', [])), 2)
        self.f.html()
        self.assertEqual(before, self.f.owners())

    def test_cli_json_counts_and_html_preserve_owner_checksums(self):
        before = self.f.owners()
        binary = ROOT / 'bin/fleet-observatory'
        result = subprocess.run([str(binary), '--fixture', str(self.f.base), '--json'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = json.loads(result.stdout)
        self.assertEqual(len(snapshot['panels']['agents']['items']), 2)
        self.assertEqual(snapshot['panels']['assignment_board']['counts'],
                         {'queued': 1, 'running': 1, 'awaiting_review': 0, 'blocked': 0, 'landed': 1})
        self.assertEqual(len(snapshot['panels']['operator_attention']['open_questions']), 1)
        output = self.f.base / 'observatory.html'
        result = subprocess.run([str(binary), '--fixture', str(self.f.base), '--output', str(output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_text().count('class="sources"'), 6)
        self.assertEqual(before, self.f.owners())

    def test_failed_decision_read_does_not_claim_question_is_open(self):
        path = self.f.reports / 'decisions/q-answered.json'
        path.chmod(0)
        self.addCleanup(lambda: path.chmod(0o600))
        panel = self.f.snapshot()['panels']['operator_attention']
        self.assertIsNone(panel['open_questions'])
        self.assertEqual(panel['status'], 'PARTIAL')

    def test_html_output_cannot_overwrite_a_task(self):
        output = self.f.tasks / 'worker'
        before = self.f.owners()
        result = subprocess.run([str(ROOT / 'bin/fleet-observatory'), '--fixture', str(self.f.base), '--output', str(output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(before, self.f.owners())

    def test_round_two_kanban_counts_match_json_and_visible_cards_are_capped(self):
        queued = [{'id': f'bean-q{i:03d}', 'title': 'Queued title ' + str(i), 'priority': 'low'} for i in range(20)]
        queued[-1]['priority'] = 'critical'
        self.f.write(self.f.base / 'dispatch.json', json.dumps({'board_ready': 20, 'dispatchable_count': 20,
            'dispatchable': queued, 'summary': 'board_ready=20 dispatchable=20'}))
        snapshot = self.f.snapshot()
        class Board(HTMLParser):
            def __init__(self):
                super().__init__(); self.counts = {}; self.cards = {}
            def handle_starttag(self, tag, attrs):
                fields = dict(attrs)
                if 'data-column' in fields:
                    self.counts[fields['data-column']] = int(fields['data-count'])
                if 'data-bean' in fields:
                    self.cards[fields['data-bean']] = fields.get('data-stage')
        board = Board(); board.feed(implementation().render_html(snapshot))
        self.assertEqual(board.counts, {'queued': 20, 'running': 1, 'awaiting_review': 0, 'blocked': 0, 'landed': 1})
        self.assertEqual(board.counts, snapshot['panels']['assignment_board']['counts'])
        self.assertEqual(sum(stage == 'queued' for stage in board.cards.values()), 15)
        self.assertIn('bean-q019', board.cards)  # critical beyond the first 15 still appears

    def test_round_two_page_budget_with_large_foreign_text_and_many_sources(self):
        queued = [{'id': f'bean-q{i:03d}', 'title': '<script>' * 10000} for i in range(30)]
        self.f.write(self.f.base / 'dispatch.json', json.dumps({'board_ready': 30, 'dispatchable_count': 30,
            'dispatchable': queued, 'summary': 'board_ready=30 dispatchable=30'}))
        self.f.write(self.f.reports / 'worker-bean-b002.md', 'BLOCKED ' + '<script>alert(1)</script>' * 10000)
        snapshot = self.f.snapshot()
        for i in range(1000):
            key = '/fixture/reports/' + str(i) + '-report.md'
            snapshot['sources'][key] = {'path': key, 'read_at': '2027-01-15T08:00:00Z', 'mtime': None,
                'status': 'UNAVAILABLE', 'error': 'PermissionError: denied ' + '<script>' * 1000}
            snapshot['panels']['evidence']['sources'].append(key)
        page = implementation().render_html(snapshot)
        self.assertLess(len(page.encode('utf-8')), 300000)
        self.assertIn('prefers-color-scheme: dark', page)
        self.assertIn('Observation mode', page)
        self.assertNotIn('<script>', page)

    def test_round_two_kpis_use_ancestry_commit_date_not_report_mtime(self):
        snapshot = self.f.snapshot()
        self.assertEqual(snapshot.get('overview', {}).get('kpis'),
            {'working_now': 1, 'awaiting_verification': 0, 'landed_today': 1, 'needs_attention': 2})
        path = self.f.reports / 'reviewer-bean-c003.md'
        os.utime(path, (NOW+86400, NOW+86400))
        module = implementation()
        tomorrow = module.collect_snapshot(self.f.home, self.f.repo, now=NOW+86400, fixture=self.f.base)
        self.assertEqual(tomorrow['overview']['kpis']['landed_today'], 0)

    def test_round_two_live_agent_and_event_limits_preserve_full_json(self):
        self.f.write(self.f.base / 'herdr.json', json.dumps({'result': {'agents':[
            {'name': 'worker', 'agent': 'codex', 'agent_status': 'working', 'cwd': str(self.f.repo)},
            *[{'name': f'seat{i}', 'agent': 'claude', 'agent_status': 'idle'} for i in range(25)]]}}))
        snapshot = self.f.snapshot()
        self.assertEqual(len(snapshot['panels']['agents']['items']), 26)
        page = implementation().render_html(snapshot)
        self.assertEqual(page.count('data-agent='), 20)
        self.assertIn('20 of 26', page)

    def test_round_two_unknown_assignment_does_not_fabricate_zero_evidence(self):
        (self.f.tasks / 'worker').unlink()
        snapshot = self.f.snapshot()
        running = snapshot['panels']['assignment_board']['running']
        self.assertEqual(len(running), 1)
        self.assertIsNone(running[0].get('evidence_count'))


if __name__ == '__main__':
    unittest.main()
