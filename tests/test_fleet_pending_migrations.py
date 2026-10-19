"""bean-8kfz: run actual watcher/standup entry points; only production status is stubbed."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'lib'))
LIB = ROOT / 'lib/fleet_pending_migrations.py'
WATCH = ROOT / 'bin/fleet-watch'
STANDUP = ROOT / 'bin/fleet-standup'
GIT = shutil.which('git')


class PendingMigrations(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('FLEET_PROOF_SCRATCH'))
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / 'IdeaProjects/project'
        self.repo.mkdir(parents=True)
        self.state = self.home / '.local/share/fleet-watch/state'
        self.state.mkdir(parents=True)
        (self.state / 'observer').touch()
        self.bin = self.home / '.local/bin'
        self.bin.mkdir(parents=True)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(HOME=str(self.home), DROVER_REPO=str(self.repo), DROVER_BIN=str(self.bin), DROVER_SRC=str(ROOT), FLEET_STATE=str(self.state), FLEET_REPORTS=str(self.home/'.local/share/drover/reports'), DROVER_MIGRATIONS_DIR='backend/src/db/migrations', DROVER_MIGRATIONS_STATUS='cd backend && npx tsx scripts/migrate.mts --status', DROVER_HELD_MIGRATIONS=str(self.home/'held-migrations.txt'), PATH=str(self.bin)+os.pathsep+os.environ['PATH'])
        self.git('init', '-q', '-b', 'fleet/test-coder-bean-aa01')
        self.git('config', 'user.name', 'Fixture Author')
        self.git('config', 'user.email', 'fixture@example.test')
        self.migrations = self.repo / 'backend/src/db/migrations'
        self.migrations.mkdir(parents=True)
        (self.repo / 'backend/scripts').mkdir()
        (self.repo / 'backend/scripts/migrate.mts').write_text('// production command stubbed\n')
        (self.repo / '.env').write_text('PG_HOST=production.example.invalid\n')
        (self.repo / 'package.json').write_text('{}\n')
        self.add_migration('001_initial.sql', 'SELECT 1;', 'initial (bean-aa01)')
        self.held = self.home / 'held-migrations.txt'
        self.held.parent.mkdir(parents=True,exist_ok=True)
        self.held.write_text('002_held.sql # founder hold\n')
        self.add_migration('002_held.sql', 'ALTER TABLE x ADD COLUMN y int;', 'hold (bean-aa01)')
        self.landing = self.add_migration('003_new.sql', 'DROP TABLE old_table;', 'new migration (bean-aa01)')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.status = self.home / 'status.txt'
        self.status.write_text('applied: 1   pending: 2   total files: 3\n  PENDING 002_held.sql\n  PENDING 003_new.sql\n[migrate] 0 of 0 succeeded\n')
        self.env['STATUS_FILE'] = str(self.status)
        self.stub('npx', '''#!/usr/bin/env python3
import json,os,pathlib,sys,time
home=pathlib.Path(os.environ['HOME'])
assert sys.argv[1:]==['tsx','scripts/migrate.mts','--status'],sys.argv
assert os.environ['PGOPTIONS'].endswith('-c default_transaction_read_only=on')
assert (pathlib.Path.cwd()/'src/db/migrations/003_new.sql').exists()
with (home/'status-calls').open('a') as f:f.write(json.dumps({'args':sys.argv[1:],'readonly':os.environ['PGOPTIONS']})+'\\n')
time.sleep(float(os.environ.get('STATUS_SLEEP','0')))
print(pathlib.Path(os.environ['STATUS_FILE']).read_text())
sys.exit(int(os.environ.get('STATUS_EXIT','0')))
''')
        for name in ['fleet-budget', 'fleet-review-check', 'fleet-stale-worktree', 'fleet-landed-check', 'fleet-alert']:
            self.stub(name, '#!/bin/sh\nexit 0\n')
        self.stub('fleet-observe', '#!/bin/sh\necho fixture\n')

    def stub(self, name, text):
        f = self.bin / name
        f.write_text(text)
        f.chmod(0o755)

    def git(self, *args):
        return subprocess.check_output([GIT, '-C', str(self.repo), *args], env=self.env, stderr=subprocess.DEVNULL, text=True).strip()

    def add_migration(self, name, text, subject):
        (self.migrations / name).write_text(text)
        self.git('add', '.')
        self.git('commit', '-qm', subject)
        return self.git('rev-parse', 'HEAD')

    def watch(self):
        p = subprocess.run(['bash', str(WATCH), 'once'], env=self.env, capture_output=True, text=True, timeout=25)
        self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
        return p

    def tick(self, now=1000):
        return subprocess.run([sys.executable, str(LIB), 'tick', '--repo', str(self.repo), '--state', str(self.state), '--now', str(now)], env=self.env, capture_output=True, text=True, timeout=25)

    def alert(self):
        return self.state / 'alerts/pending-migrations.txt'

    def test_watcher_tick_alerts_with_landing_seat_and_scan(self):
        self.watch()
        self.assertTrue(self.alert().is_file(), 'watcher tick did not create the pending migration alert')
        alert = self.alert().read_text()
        self.assertIn('003_new.sql', alert)
        self.assertIn(self.landing, alert)
        self.assertIn('test-coder', alert)
        self.assertIn('destructive: yes', alert)
        self.assertNotIn('002_held.sql', alert)
        self.assertEqual(len((self.home/'status-calls').read_text().splitlines()), 1)

    def test_applied_and_held_do_not_alert_but_held_is_reported(self):
        self.status.write_text('applied: 2   pending: 1   total files: 3\n  PENDING 002_held.sql\n')
        self.assertEqual(self.tick().returncode, 0)
        self.assertFalse(self.alert().exists())
        p = subprocess.run([sys.executable, str(LIB), 'report', '--state', str(self.state)], env=self.env, text=True, capture_output=True)
        self.assertIn('HELD 002_held.sql', p.stdout)
        self.assertNotIn('PENDING', p.stdout)
        self.assertNotIn('001_initial.sql', p.stdout)

    def test_throttle_refresh_and_new_landing_use_cached_applied_set(self):
        self.assertEqual(self.tick().returncode, 0)
        self.add_migration('004_safe.sql', 'SELECT 2;', 'safe (bean-aa01)')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.assertEqual(self.tick(1599).returncode, 0)
        self.assertEqual(len((self.home/'status-calls').read_text().splitlines()), 1)
        self.assertIn('004_safe.sql', self.alert().read_text())
        self.assertIn('destructive: no', self.alert().read_text())
        self.status.write_text('applied: 3   pending: 1   total files: 4\n  PENDING 002_held.sql\n')
        self.assertEqual(self.tick(1600).returncode, 0)
        self.assertFalse(self.alert().exists())
        self.assertEqual(len((self.home/'status-calls').read_text().splitlines()), 2)

    def test_status_failure_is_unavailable_and_also_throttled(self):
        self.env['STATUS_EXIT'] = '2'
        self.assertNotEqual(self.tick().returncode, 0)
        self.assertIn('UNAVAILABLE', self.alert().read_text())
        self.tick(1001)
        self.assertEqual(len((self.home/'status-calls').read_text().splitlines()), 1)

    def test_empty_or_partial_status_is_not_an_applied_result(self):
        for text in ['', '[migrate] 0 of 0 succeeded', 'applied: 2   pending: 1   total files: 3\n']:
            with self.subTest(text=text):
                self.status.write_text(text)
                self.assertNotEqual(self.tick(int(time.time())).returncode, 0)
                self.assertIn('UNAVAILABLE', self.alert().read_text())
                (self.state/'migration-status.json').unlink(missing_ok=True)

    def test_two_concurrent_ticks_issue_one_readonly_query(self):
        self.env['STATUS_SLEEP'] = '0.3'
        command = [sys.executable, str(LIB), 'tick', '--repo', str(self.repo), '--state', str(self.state), '--now', '1000']
        a = subprocess.Popen(command, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        b = subprocess.Popen(command, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        a.communicate(timeout=25); b.communicate(timeout=25)
        self.assertEqual(a.returncode, 0)
        self.assertEqual(b.returncode, 0)
        self.assertEqual(len((self.home/'status-calls').read_text().splitlines()), 1)

    def test_status_uses_origin_tree_even_when_checkout_lags(self):
        self.git('checkout', '-q', '--detach', 'HEAD~1')
        self.assertFalse((self.migrations/'003_new.sql').exists())
        self.assertEqual(self.tick().returncode, 0)
        self.assertIn('003_new.sql', self.alert().read_text())

    def test_applied_number_does_not_hide_another_filename_with_same_number(self):
        self.add_migration('001_late.sql', 'DELETE FROM old_rows;', 'duplicate historical number (bean-aa01)')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.status.write_text('applied: 1   pending: 3   total files: 4\n  PENDING 002_held.sql\n  PENDING 003_new.sql\n  PENDING 001_late.sql\n')
        self.assertEqual(self.tick().returncode, 0)
        self.assertIn('001_late.sql', self.alert().read_text())
        self.assertNotIn('001_initial.sql', self.alert().read_text())

    def test_scan_covers_each_requested_keyword(self):
        spec = importlib.util.spec_from_file_location('pending', LIB)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        for sql in ['drop table x', 'DELETE FROM x', 'Truncate x', 'ALTER TABLE x ADD y int']:
            self.assertTrue(module.destructive(sql), sql)
        self.assertFalse(module.destructive('SELECT 1;'))

    def test_actual_standup_prints_pending_and_held_inventory(self):
        self.assertEqual(self.tick().returncode, 0)
        self.stub('herdr', '#!/bin/sh\necho \'{"result":{"agents":[]}}\'\n')
        self.stub('fleet-stale-jobs', '#!/bin/sh\necho "- none"\n')
        self.stub('fleet-dispatch', '#!/bin/sh\necho fixture\n')
        reports = self.home/'.local/share/drover/reports'
        reports.mkdir(parents=True, exist_ok=True)
        (reports/'deploys.log').write_text('now\t'+self.landing+'\tDEPLOYED\n')
        for name in ['linear-seats.txt', 'linear-map.tsv']:
            (self.held.parent/name).write_text('')
        subprocess.run(['bash', str(STANDUP)], env=self.env, capture_output=True, text=True, timeout=25)
        report = next(reports.glob('standup-*.md')).read_text()
        self.assertIn('PENDING 003_new.sql', report)
        self.assertIn('HELD 002_held.sql', report)
        self.assertIn(self.landing, report)


if __name__ == '__main__':
    unittest.main()
