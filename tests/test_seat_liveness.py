#!/usr/bin/env python3
"""One owner of per-seat usage-limit state — known-good and known-bad cases."""
import importlib.util
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / 'lib' / 'seat_liveness.py'
spec = importlib.util.spec_from_file_location('seat_liveness', LIB)
liveness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(liveness)

CODEX_BANNER = "You've hit your usage limit. Try again at Nov 4th, 2099 1:35 AM"


class SeatLiveness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'state'
        self._old = os.environ.get('FLEET_STATE')
        os.environ['FLEET_STATE'] = str(self.state)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop('FLEET_STATE', None)
        else:
            os.environ['FLEET_STATE'] = self._old

    def test_a_codex_limit_banner_records_its_reset_time(self):
        reset = liveness.parse_reset(CODEX_BANNER)
        self.assertIsNotNone(reset)
        self.assertGreater(reset, time.time() + 86400)          # a far-future reset
        liveness.mark_limit('codex-coder', reset, CODEX_BANNER)
        self.assertIn('usage limit until', liveness.limit_reason('codex-coder'))
        self.assertEqual(liveness.limit_reason('claude-coder'), None)   # non-marked seat unaffected

    def test_a_past_reset_clears_the_mark(self):
        liveness.mark_limit('codex-coder', time.time() - 60, 'stale')
        self.assertIsNone(liveness.limit_reason('codex-coder'))
        self.assertFalse(liveness.mark_path('codex-coder').exists())

    def test_an_unknown_reset_survives_the_fallback_only(self):
        liveness.mark_limit('opencode-coder', None, 'out of credits')
        self.assertEqual(liveness.limit_reason('opencode-coder'), 'usage limit (reset unknown)')
        old = time.time() - (liveness.FALLBACK_S + 1)
        os.utime(liveness.mark_path('opencode-coder'), (old, old))
        self.assertIsNone(liveness.limit_reason('opencode-coder'))

    def test_the_next_successful_turn_clears_a_mark_before_its_reset(self):
        liveness.mark_limit('codex-coder', time.time() + 86_400, CODEX_BANNER)
        self.assertFalse(liveness.clear_recovered('codex-coder', 'working', 'OK'))
        self.assertIsNotNone(liveness.limit_reason('codex-coder'))
        self.assertFalse(liveness.clear_recovered('codex-coder', 'done', 'UNKNOWN'))
        self.assertIsNotNone(liveness.limit_reason('codex-coder'))
        self.assertTrue(liveness.clear_recovered('codex-coder', 'done', 'OK'))
        self.assertIsNone(liveness.limit_reason('codex-coder'))

    def test_legacy_empty_marks_are_read_and_clear_after_the_fallback(self):
        path = liveness.mark_path('codex-reviewer')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('')
        self.assertEqual(liveness.limit_reason('codex-reviewer'), 'usage limit (reset unknown)')
        old = time.time() - (liveness.FALLBACK_S + 1)
        os.utime(path, (old, old))
        self.assertIsNone(liveness.limit_reason('codex-reviewer'))

    def test_the_watch_pane_banner_marks_the_seat_through_the_cli(self):
        env = {**os.environ, 'FLEET_STATE': str(self.state)}
        mark = subprocess.run(['python3', '-B', str(LIB), 'mark', 'codex-coder'], input=CODEX_BANNER,
                              text=True, capture_output=True, env=env)
        self.assertEqual(mark.returncode, 0, mark.stderr)
        self.assertIn('usage limit until', mark.stdout)
        busy = subprocess.run(['python3', '-B', str(LIB), 'busy', 'codex-coder'],
                              text=True, capture_output=True, env=env)
        self.assertEqual(busy.returncode, 0, busy.stderr)
        self.assertIn('usage limit until', busy.stdout)

    def test_a_seat_is_parseable_from_the_installed_pane_format(self):
        self.assertEqual(liveness.parse_reset('resets 3:45pm') is not None, True)
        self.assertIsNone(liveness.parse_reset('no time here'))


if __name__ == '__main__':
    unittest.main()
