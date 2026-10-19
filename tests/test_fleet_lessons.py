"""bean-jisv: a lesson without evidence is refused; a failed check shows up in the standup."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LIB = HERE.parent / 'lib' / 'fleet_lessons.py'
BIN = HERE.parent / 'bin' / 'fleet-lessons'
SEED = HERE.parent / 'rulebook' / 'lessons.jsonl'


def load():
    import importlib.machinery
    loader = importlib.machinery.SourceFileLoader('fleet_lessons', str(LIB))
    return loader.load_module()


class LessonLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Path(self.tmp.name) / 'lessons.jsonl'
        self.env = os.environ.copy()
        self.env['FLEET_LESSONS'] = str(self.ledger)
        self.env['FLEET_LESSONS_NOW'] = '2026-10-18'

    def run_cli(self, *args):
        return subprocess.run(
            [str(BIN), *args], env=self.env, text=True, capture_output=True, timeout=15,
        )

    def test_record_without_evidence_is_refused(self):
        lessons = load()
        with self.assertRaises(lessons.LessonRejected) as caught:
            lessons.validate({
                'id': 'bare',
                'cue': 'when a deploy starts',
                'rule': 'Retro 2026-10-18 rules (lead)',
                'evidence': [],
                'check': {'kind': 'manual', 'ref': 'lead looks'},
            })
        self.assertIn('evidence', str(caught.exception))
        result = self.run_cli(
            'add', '--id', 'bare', '--cue', 'when a deploy starts',
            '--rule', 'Retro 2026-10-18 rules (lead)',
            '--check-kind', 'manual', '--check-ref', 'lead looks',
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('evidence', result.stderr)
        self.assertFalse(self.ledger.exists())

    def test_failing_command_marks_failed_and_standup_prints_it(self):
        lessons = load()
        lessons.add(self.ledger, {
            'id': 'boom',
            'cue': 'when the check command runs',
            'rule': 'Retro 2026-10-18 rules (lead)',
            'evidence': ['retro:2026-10-18-2015-lead.md#5.1'],
            'check': {'kind': 'command', 'ref': 'false'},
            'last_verified': '2026-10-18',
            'status': 'held',
        })
        report = lessons.check(self.ledger, now='2026-10-18', timeout=2)
        self.assertEqual(report['failed'], ['boom'])
        stored = [json.loads(line) for line in self.ledger.read_text().splitlines()]
        self.assertEqual(stored[0]['status'], 'failed')
        text = lessons.standup_text(self.ledger, now='2026-10-18')
        self.assertIn('FAILED boom', text)
        cli = self.run_cli('standup')
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertIn('FAILED boom', cli.stdout)

    def test_passing_command_stays_held_and_is_quiet(self):
        lessons = load()
        lessons.add(self.ledger, {
            'id': 'quiet',
            'cue': 'when the check command runs',
            'rule': 'Retro 2026-10-17 rules (lead)',
            'evidence': ['retro:2026-10-17-2015-lead.md#5.6'],
            'check': {'kind': 'command', 'ref': 'true'},
            'last_verified': '2026-10-01',
            'status': 'held',
        })
        report = lessons.check(self.ledger, now='2026-10-18', timeout=2)
        self.assertEqual(report['failed'], [])
        self.assertEqual(report['overdue'], [])
        stored = json.loads(self.ledger.read_text().splitlines()[0])
        self.assertEqual(stored['status'], 'held')
        self.assertNotIn('quiet', lessons.standup_text(self.ledger, now='2026-10-18'))

    def test_overdue_manual_is_listed_and_a_fresh_one_is_not(self):
        lessons = load()
        lessons.add(self.ledger, {
            'id': 'old',
            'cue': 'when the lead pauses a seat',
            'rule': 'Retro 2026-10-18 rules (lead)',
            'evidence': ['retro:2026-10-18-2015-lead.md#5.5'],
            'check': {'kind': 'manual', 'ref': 'resume with fleet assign'},
            'last_verified': '2026-10-01',
            'status': 'held',
        })
        lessons.add(self.ledger, {
            'id': 'fresh',
            'cue': 'when one item is blocked',
            'rule': 'Retro 2026-10-18 rules (lead)',
            'evidence': ['retro:2026-10-18-2015-lead.md#5.6'],
            'check': {'kind': 'manual', 'ref': 'continue with the next item'},
            'last_verified': '2026-10-18',
            'status': 'held',
        })
        report = lessons.check(self.ledger, now='2026-10-18')
        self.assertEqual(report['overdue'], ['old'])
        text = lessons.standup_text(self.ledger, now='2026-10-18')
        self.assertIn('OVERDUE old', text)
        self.assertNotIn('fresh', text)

    def test_missing_project_policy_is_empty_not_fabricated(self):
        self.assertEqual(load().load(self.ledger), [])

    def test_retro_line_comes_from_the_ledger(self):
        lessons = load()
        lessons.add(self.ledger, {
            'id': 'r17-1',
            'cue': 'floor',
            'rule': 'Retro 2026-10-17 rules (lead)',
            'evidence': ['retro:2026-10-17-2015-lead.md#5.1'],
            'check': {'kind': 'manual', 'ref': 'count seats'},
            'last_verified': '2026-10-18',
            'status': 'held',
        })
        lessons.add(self.ledger, {
            'id': 'r18-1',
            'cue': 'stale jobs',
            'rule': 'Retro 2026-10-18 rules (lead)',
            'evidence': ['retro:2026-10-18-2015-lead.md#5.1'],
            'check': {'kind': 'manual', 'ref': 'map jobs'},
            'last_verified': '2026-10-18',
            'status': 'failed',
        })
        text = lessons.retro_text(self.ledger)
        self.assertIn("last retro's rules: held/failed", text)
        self.assertIn('r17-1 held', text)
        self.assertIn('r18-1 failed', text)

if __name__ == '__main__':
    unittest.main()
