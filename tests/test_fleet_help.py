"""bean-hv8r: --help exits 0 before any side effect. Unknown flags exit 2."""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / 'bin'
STUB_NAMES = (
    'aws', 'git', 'curl', 'herdr', 'node', 'npx', 'gh', 'beans', 'docker',
    'jq', 'perl', 'tsx', 'npm', 'fleet', 'fleet-slot',
)


def tools():
    return sorted(p for p in BIN.iterdir() if p.is_file() and os.access(p, os.X_OK) and 'node' not in p.read_text().splitlines()[0])


class FleetHelpTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='fleet-help-hv8r-')
        self.home = Path(self._tmp.name)
        self.stub = self.home / 'stubs'
        self.stub.mkdir()
        script = '#!/bin/sh\nprintf "%s\\n" "$0" >> "$HOME/calls"\nexit 99\n'
        for name in STUB_NAMES:
            path = self.stub / name
            path.write_text(script)
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        self.env = os.environ.copy()
        self.env['HOME'] = str(self.home)
        self.env['PATH'] = f'{self.stub}:/usr/bin:/bin'

    def tearDown(self):
        self._tmp.cleanup()

    def run_tool(self, tool, *args):
        return subprocess.run(
            [str(tool), *args],
            cwd=str(self.home),
            env=self.env,
            text=True,
            capture_output=True,
            timeout=5,
        )

    def assert_no_side_effect(self):
        calls = self.home / 'calls'
        self.assertFalse(calls.exists(), calls.read_text() if calls.exists() else '')
        created = [p.name for p in self.home.iterdir() if p.name not in ('stubs', 'Library')]
        self.assertEqual(created, [])

    def test_help_is_inert(self):
        failures = []
        for tool in tools():
            with self.subTest(tool=tool.name, flag='--help'):
                result = self.run_tool(tool, '--help')
                if result.returncode != 0 or tool.name not in result.stdout:
                    failures.append(f'{tool.name} --help rc={result.returncode} out={result.stdout[:120]!r} err={result.stderr[:120]!r}')
                calls = self.home / 'calls'
                if calls.exists():
                    failures.append(f'{tool.name} --help called {calls.read_text().strip()}')
                    calls.unlink()
                created = [p.name for p in self.home.iterdir() if p.name not in ('stubs', 'Library')]
                if created:
                    failures.append(f'{tool.name} --help created {created}')
        self.assertEqual(failures, [])

    def test_slot_child_options_after_double_dash_are_opaque(self):
        result = self.run_tool(BIN / 'fleet-slot', 'run', '--', '/bin/echo', 'child', '--help', '--not-a-fleet-option')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'child --help --not-a-fleet-option')

    def test_unknown_flag_exits_2(self):
        failures = []
        for tool in tools():
            with self.subTest(tool=tool.name):
                result = self.run_tool(tool, '--not-a-real-flag-hv8r')
                if result.returncode != 2 or tool.name not in (result.stdout + result.stderr):
                    failures.append(
                        f'{tool.name} unknown rc={result.returncode} out={result.stdout[:80]!r} err={result.stderr[:80]!r}'
                    )
                calls = self.home / 'calls'
                if calls.exists():
                    failures.append(f'{tool.name} unknown called {calls.read_text().strip()}')
                    calls.unlink()
        self.assertEqual(failures, [])


if __name__ == '__main__': unittest.main()
