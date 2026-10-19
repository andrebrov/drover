"""bean-0s1x: a seat's own language server / MCP daemon is not "work in the worktree".

`fleet-prep` refuses (exit 7) when `fleet-seat-live` sees a node/npm/... process with its cwd in the worktree. An editor's
`typescript-language-server` and its `typingsInstaller` are exactly such processes, so a seat could be refused because of
its own tooling — the same class as bean-emi6 in the dispatcher. The daemon list has one owner
(`lib/fleet_daemons.py`): this test pins both the classification and the real refusal behaviour.
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))

from fleet_daemons import is_daemon  # noqa: E402

SEAT_LIVE = ROOT / 'bin' / 'fleet-seat-live'
NODE = 'node'


def spawn(cwd: str, *args: str) -> subprocess.Popen:
    return subprocess.Popen([NODE, '-e', 'setTimeout(()=>{},60000)', *args], cwd=cwd,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class DaemonClassificationTest(unittest.TestCase):
    def test_language_servers_and_mcp_daemons_are_not_work(self):
        for command in (
            'node /opt/homebrew/lib/node_modules/typescript-language-server/lib/cli.mjs --stdio',
            'node /Applications/Code.app/typescript/lib/tsserver.js --useInferredProjectPerProjectRoot',
            'node /Applications/Code.app/typescript/lib/typingsInstaller.js --globalTypingsCacheLocation /tmp/x',
            'node /home/u/.npm/_npx/1/node_modules/example mcp',
            'node /home/u/.local/share/codegraph/mcp-server.js',
            'node /x/pyright-langserver --stdio',
            'eslint_d --stdio',
        ):
            self.assertTrue(is_daemon(command), command)

    def test_real_builds_tests_and_agents_still_count(self):
        for command in (
            'node scripts/seed-data.js',
            'node /work/backend/dist/server.js',
            'npm run build',
            'npx jest --runInBand src/x.test.ts',
            'tsx scripts/check-something.mts',
            'vite build',
            'playwright test e2e/x.spec.ts',
            'node /work/agent-worker.js',
        ):
            self.assertFalse(is_daemon(command), command)


class SeatLiveBehaviourTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='fleet-seat-live-test-')
        self.procs: list[subprocess.Popen] = []

    def tearDown(self):
        for proc in self.procs:
            proc.kill()
            proc.wait(timeout=10)

    def _start(self, *args: str) -> subprocess.Popen:
        proc = spawn(self.tmp, *args)
        self.procs.append(proc)
        return proc

    def _run(self) -> subprocess.CompletedProcess:
        time.sleep(0.4)
        return subprocess.run([str(SEAT_LIVE), self.tmp], capture_output=True, text=True)

    def test_MUST_FIRE_an_LSP_daemon_does_not_refuse_the_seat(self):
        self._start('typescript-language-server', '--stdio')
        self._start('typingsInstaller.js')
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_MUST_FIRE_an_MCP_daemon_does_not_refuse_the_seat(self):
        self._start('example', 'mcp')
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_real_build_process_still_refuses(self):
        self._start('build.js')
        result = self._run()
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn('build.js', result.stdout)

    def test_a_daemon_does_not_mask_a_real_build(self):
        self._start('typescript-language-server', '--stdio')
        build = self._start('build.js')
        result = self._run()
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn('build.js', result.stdout)
        self.assertNotIn('typescript-language-server', result.stdout)
        self.assertNotEqual(build.pid, 0)


if __name__ == '__main__':
    unittest.main()
