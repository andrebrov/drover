"""bean-baws: real detached landers must hold their seat in both public CLIs."""
import json
import os
from pathlib import Path
import signal
import sys
from unittest import mock
import subprocess
import time
import unittest

import test_fleet_dispatch as dispatch_tests

BIN = Path(__file__).resolve().parents[1] / 'bin'
sys.path.insert(0, str(BIN.parent / 'lib'))
import fleet_activity as activity


class LandingActivity(unittest.TestCase):
    setUp = dispatch_tests.BusySeatEvidence.setUp
    git = dispatch_tests.BusySeatEvidence.git
    make_repo = dispatch_tests.BusySeatEvidence.make_repo

    def prepare(self, status='done'):
        self.make_repo()
        self.b.bean('bean-new1')
        self.b.seat('reviewer', status, 'fleet/reviewer-bean-old1')
        idle = self.spawn(['sleep', '60'], self.repo)
        self.b.seats[-1].update(cwd=str(self.repo), shell_pid=idle.pid)

    def spawn(self, command, cwd):
        process = subprocess.Popen(command, cwd=cwd, start_new_session=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def stop():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        self.addCleanup(stop)
        return process

    def wait_ready(self):
        for _ in range(200):
            if (self.repo / '.git/hook-started').exists():
                return
            time.sleep(.01)
        self.fail('fixture never entered landing process')

    def status(self, roster=True):
        agents = self.b.root / 'agents.json'
        agents.write_text(json.dumps({'result': {'agents': [{
            'name': 'reviewer', 'agent': 'codex', 'agent_status': self.b.seats[-1]['status'],
            'pane_id': 'fixture', 'cwd': str(self.repo), 'terminal_title_stripped': ''}]}}))
        transport = self.b.root / 'herdr-stub'
        transport.write_text('#!/bin/sh\nif [ "$1 $2" = "agent list" ]; then cat "$FLEET_STATUS_AGENTS"; fi\n')
        transport.chmod(0o755)
        env = dict(os.environ, FLEET_HERDR=str(transport), FLEET_STATUS_AGENTS=str(agents),
                   FLEET='reviewer:codex' if roster else 'absent:codex',
                   FLEET_ROOT=str(self.repo), HOME=str(self.b.root))
        return subprocess.run(['bash', str(BIN / 'fleet'), 'status'], env=env,
                              capture_output=True, text=True, timeout=30)

    def assert_landing(self):
        with self.subTest(surface='dispatch'):
            result = self.b.run('--json', '--dry-run')
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn('landing', payload['busy_seats'].get('reviewer', []), result.stdout)
            result = self.b.run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.b.assigned(), [], result.stdout)
        with self.subTest(surface='status'):
            result = self.status()
            self.assertEqual(result.returncode, 0, result.stderr)
            row = next(line for line in result.stdout.splitlines() if line.startswith('reviewer '))
            self.assertEqual(row.split()[2], 'LANDING', result.stdout)
            self.assertRegex(row, r'bean-old1 [0-9][0-9:-]*', result.stdout)

    def test_done_detached_fleet_land_is_held_even_without_shell_ancestry(self):
        self.prepare()
        script = self.repo / 'fleet-land.sh'
        script.write_text('#!/bin/sh\nprintf ready > .git/hook-started\nsleep 60\n')
        nested = self.repo / 'backend'
        nested.mkdir()
        script.write_text('#!/bin/sh\nprintf ready > ../.git/hook-started\nsleep 60\n')
        self.spawn(['bash', str(script), 'bean-old1'], nested)
        self.wait_ready()
        self.assert_landing()
        result = self.status(roster=False)
        self.assertIn('LANDING', next(line for line in result.stdout.splitlines()
                                     if line.startswith('reviewer ')))

    def test_idle_detached_real_pre_push_is_held(self):
        self.prepare(status='idle')
        remote = self.b.root / 'provider.git'
        subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
        hook = self.repo / '.git/hooks/pre-push'
        hook.write_text('#!/bin/sh\nprintf ready > .git/hook-started\nsleep 60\n')
        hook.chmod(0o755)
        self.git('config', 'core.hooksPath', str(hook.parent))
        self.spawn(['git', 'push', str(remote), 'HEAD:refs/heads/main'], self.repo)
        self.wait_ready()
        self.assert_landing()

    def test_done_seat_without_lander_stays_free(self):
        self.prepare()
        result = self.status()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('LANDING', result.stdout)
        self.assertEqual(next(line for line in result.stdout.splitlines()
                              if line.startswith('reviewer ')).split()[2], 'done')
        result = self.b.run()
        self.assertEqual(self.b.assigned(), ['bean-new1 reviewer'], result.stdout)

    def test_other_worktree_lander_does_not_hold_this_seat(self):
        self.prepare()
        other = self.b.root / 'other'
        other.mkdir()
        script = other / 'fleet-land.sh'
        script.write_text('#!/bin/sh\nprintf ready > ready\nsleep 60\n')
        self.spawn(['bash', str(script)], other)
        for _ in range(200):
            if (other / 'ready').exists(): break
            time.sleep(.01)
        else: self.fail('other lander never started')
        result = self.status()
        self.assertNotIn('LANDING', result.stdout)
        result = self.b.run()
        self.assertEqual(self.b.assigned(), ['bean-new1 reviewer'], result.stdout)


class LandingEvidence(unittest.TestCase):
    def test_script_operand_not_incidental_argument_decides_landing(self):
        for args in ('bash /repo/scripts/fleet-land.sh bean-1234',
                     '/bin/sh .git/hooks/pre-push origin /remote',
                     '/bin/sh -e .git/hooks/pre-push origin /remote',
                     'python3 /home/bin/fleet-land bean-1234',
                     '/repo/scripts/fleet-land.sh bean-1234'):
            with self.subTest(args=args):
                self.assertTrue(activity.is_landing((1, 'S', 'bash', args)))
        for args in ('grep fleet-land.sh', 'vim scripts/fleet-land.sh',
                     'bash -c "echo fleet-land.sh"', 'node test.js pre-push',
                     'python3 -c "print(1)" fleet-land', 'bash other.sh fleet-land.sh'):
            with self.subTest(args=args):
                self.assertFalse(activity.is_landing((1, 'S', 'bash', args)))
        self.assertFalse(activity.is_landing((1, 'Z', 'bash', 'bash fleet-land.sh')))

    def test_unknown_evidence_stays_unavailable_and_zombies_do_not_hold(self):
        row = (1, 'S', 'bash', 'bash fleet-land.sh')
        self.assertIsNone(activity.landing_state('/repo', {2: row}))
        self.assertIsNone(activity.landing_state('/repo', None))
        self.assertFalse(activity.landing_state('/repo', {2: (1, 'Z', 'bash', 'bash fleet-land.sh')}))
        self.assertFalse(activity.landing_state('/repo', {2: (*row, '/repo-other')}))
        self.assertTrue(activity.landing_state('/repo', {2: (*row, '/repo/backend')}))
        annotated = list(activity.annotate_status([{'cwd': '/repo', 'agent_status': 'done'}], None))
        self.assertEqual(annotated[0]['agent_status'], 'unknown')

    def test_mac_cwd_adapter_preserves_spaces_and_queries_candidates_once(self):
        with mock.patch.object(activity.Path, 'exists', return_value=False), \
             mock.patch.object(activity.subprocess, 'run', return_value=subprocess.CompletedProcess(
                 [], 0, 'p12\nn/owned/work tree/backend\np13\nn/other\n')) as run:
            self.assertEqual(activity.candidate_cwds({12, 13}), {12: '/owned/work tree/backend', 13: '/other'})
        run.assert_called_once()
        self.assertIn('cwd', run.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
