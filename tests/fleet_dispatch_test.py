#!/usr/bin/env python3
"""fleet-dispatch against fixture boards. Known-bad (the incident shape: checklist-free beans dropped) and known-good cases."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

TOOL = Path(__file__).resolve().parents[1] / 'lib' / 'fleet_dispatch.py'


class Board:
    def __init__(self, root):
        self.root = Path(root)
        for d in ('beans', 'reports', 'config'):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        self.ready, self.seats = [], []

    def bean(self, bid, body='', type='task', priority='normal', status='todo', ready=True, created='2026-10-01T00:00:00Z'):
        (self.root / 'beans' / f'{bid}--x.md').write_text(f'---\ntitle: {bid}\nstatus: {status}\ntype: {type}\npriority: {priority}\ncreated_at: {created}\n---\n{body}\n')
        if ready:
            self.ready.append(bid)

    def seat(self, name, status='idle', branch=''):
        self.seats.append({'name': name, 'status': status, 'branch': branch})

    def write(self, load1=1.0):
        (self.root / 'ready.txt').write_text('\n'.join(self.ready) + '\n')
        (self.root / 'seats.json').write_text(json.dumps(self.seats))
        (self.root / 'load1.txt').write_text(str(load1))

    def run(self, *args):
        self.write(float((self.root / 'load1.txt').read_text()) if (self.root / 'load1.txt').exists() else 1.0)
        env = dict(os.environ, FLEET_DISPATCH_FIXTURE=str(self.root), HOME=str(self.root), DROVER_LEAD_SEATS='lead lead-backup', DROVER_REFILL_DEDICATED='ded-one')
        r = subprocess.run(['python3', str(TOOL), *args], capture_output=True, text=True, env=env)
        return r

    def assigned(self):
        p = self.root / 'assign.log'
        return p.read_text().split('\n')[:-1] if p.exists() else []


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.b = Board(self.tmp.name)

    def test_known_bad_checklist_free_beans_are_dispatchable(self):
        """The old filter returned 0 here: 3 beans with no checklist and one founder-only bean."""
        for i in 'abc':
            self.b.bean(f'bean-000{i}', body='Fix the thing. No checklist here.')
        self.b.bean('bean-000d', body='## Checklist\n- [ ] FOUNDER: approve the plan\n- [ ] founder decides the vendor')
        for s in ('s1', 's2', 's3'):
            self.b.seat(s)
        r = self.b.run()
        self.assertIn('board_ready=4 dispatchable=3', r.stdout)
        self.assertIn('excluded all_open_items_gated: 1', r.stdout)
        self.assertEqual(len(self.b.assigned()), 3)
        self.assertNotIn('no work', r.stdout.lower())

    def test_known_good_working_seat_bean_and_skip_listed_are_never_dispatched(self):
        self.b.bean('bean-0001'); self.b.bean('bean-0002'); self.b.bean('bean-0003')
        self.b.seat('busy', 'working', 'fleet/busy-bean-0001')
        self.b.seat('free1'); self.b.seat('free2')
        (self.b.root / 'config' / 'dispatch-skip').write_text('bean-0002 # lead '+time.strftime('%Y-%m-%d')+': assigned elsewhere\n')
        r = self.b.run()
        self.assertIn('board_ready=3 dispatchable=1', r.stdout)
        self.assertIn('on_working_seat_branch: 1', r.stdout)
        self.assertIn('skip_listed: 1', r.stdout)
        self.assertEqual([a.split()[0] for a in self.b.assigned()], ['bean-0003'])

    def test_an_old_branch_of_a_seat_that_moved_on_does_not_hide_its_bean(self):
        self.b.bean('bean-0001')
        self.b.seat('worker', 'working', 'fleet/worker-bean-9999')
        self.b.seat('free1')
        r = self.b.run('--dry-run')
        self.assertIn('board_ready=1 dispatchable=1', r.stdout)

    def test_priority_order_critical_first_and_only_coder_types(self):
        self.b.bean('bean-low1', priority='low'); self.b.bean('bean-crit', priority='critical'); self.b.bean('bean-epic', type='epic')
        self.b.seat('only')
        r = self.b.run()
        self.assertIn('board_ready=2 dispatchable=2', r.stdout)  # the epic is not coder work
        self.assertEqual(self.b.assigned(), ['bean-crit only'])

    def test_recent_blocked_report_excludes_and_old_one_does_not(self):
        self.b.bean('bean-new1'); self.b.bean('bean-old1')
        (self.b.root / 'reports' / 'seat-bean-new1.md').write_text('x\nBLOCKED /p\n')
        old = self.b.root / 'reports' / 'seat-bean-old1.md'
        old.write_text('x\nBLOCKED /p\n')
        t = time.time() - 3 * 86400
        os.utime(old, (t, t))
        r = self.b.run('--dry-run')
        self.assertIn('board_ready=2 dispatchable=1', r.stdout)
        self.assertIn('recently_blocked: 1', r.stdout)

    def test_blocked_in_a_heading_or_verdict_line_in_the_first_three_lines_excludes_a_bean(self):
        """Known-bad: a real seat report began '# <bean> (<seat>) — BLOCKED …' and had no BLOCKED last line; the tool re-sent
        that bean. The fixture reproduces that shape."""
        fixture = Path(__file__).resolve().parent / 'fixtures' / 'report-blocked-heading.md'
        self.b.bean('bean-s7s3'); self.b.bean('bean-v001'); self.b.bean('bean-v002'); self.b.bean('bean-ok01')
        (self.b.root / 'reports' / 'seat-coder-bean-s7s3.md').write_text(fixture.read_text())
        (self.b.root / 'reports' / 'seat-bean-v001.md').write_text('# title\n\nVerdict: BLOCKED. nothing left\n\nmore\nDONE /p\n')
        (self.b.root / 'reports' / 'seat-bean-v002.md').write_text('Verdict: PARTIAL\ntext\n')
        (self.b.root / 'reports' / 'seat-bean-ok01.md').write_text('# a report\nline two\nline three\nthe seat was not BLOCKED by anything, line four\nz\n')
        self.b.seat('free1')
        r = self.b.run('--dry-run')
        self.assertIn('board_ready=4 dispatchable=2', r.stdout)
        self.assertIn('recently_blocked: 2', r.stdout)
        self.assertNotIn('bean-s7s3', r.stdout.split('free_seats')[1])

    def test_pin_expiry_completed_bean_removed_and_active_seat_pin_kept(self):
        self.b.bean('bean-done', status='completed', ready=False)
        self.b.bean('bean-live')
        self.b.seat('worker', 'working', 'fleet/worker-bean-live')
        self.b.seat('idler')
        (self.b.root / 'config' / 'refill-pins').write_text('idler\tbean-done\nworker\tbean-live\n')
        r = self.b.run()
        self.assertIn('pin removed: idler bean-done', r.stdout)
        pins = (self.b.root / 'config' / 'refill-pins').read_text()
        self.assertNotIn('bean-done', pins)
        self.assertIn('worker\tbean-live', pins)

    def test_pin_retained_until_main_completes_even_with_newer_report(self):
        self.b.bean('bean-0001', ready=False, status='in-progress')
        self.b.seat('idler')
        pins = self.b.root / 'config' / 'refill-pins'
        pins.write_text('idler\tbean-0001 @%d\n' % (time.time() - 3600))
        (self.b.root / 'reports' / 'idler-bean-0001.md').write_text('done\nDONE /p\n')
        r = self.b.run()
        self.assertNotIn('pin removed: idler bean-0001', r.stdout)
        self.assertIn('idler\tbean-0001', pins.read_text())

    def test_a_pinned_bean_goes_to_its_free_seat_first_and_not_to_others(self):
        self.b.bean('bean-0001', priority='low'); self.b.bean('bean-0002', priority='critical')
        self.b.seat('pinned'); self.b.seat('other')
        (self.b.root / 'config' / 'refill-pins').write_text('pinned\tbean-0001\n')
        r = self.b.run()
        self.assertEqual(sorted(self.b.assigned()), ['bean-0001 pinned', 'bean-0002 other'])

    def test_cap_and_load_limit_assignments_but_never_hide_the_board(self):
        for i in range(3):
            self.b.bean(f'bean-000{i}')
        self.b.seat('free1'); self.b.seat('free2')
        for i in range(8):
            self.b.seat(f'w{i}', 'working', f'fleet/w{i}-bean-9{i}99')
        (self.b.root / 'config' / 'dispatch-cap').write_text('cap_working=8\nresume_load_below=20\n')
        r = self.b.run()
        self.assertIn('board_ready=3 dispatchable=3', r.stdout)
        self.assertEqual(self.b.assigned(), [])
        self.assertIn('cap or load limit', r.stdout)

    def test_dedicated_seats_are_never_free(self):
        self.b.bean('bean-0001')
        for s in ('lead', 'lead-backup', 'ded-one'):
            self.b.seat(s)
        r = self.b.run()
        self.assertIn('free_seats=0', r.stdout)
        self.assertEqual(self.b.assigned(), [])

    def test_zero_dispatchable_prints_top_reasons_never_no_work(self):
        self.b.bean('bean-0001', body='- [ ] founder approves'); self.b.bean('bean-0002', body='- [ ] merchant sign-off')
        self.b.seat('free1')
        r = self.b.run()
        self.assertIn('board_ready=2 dispatchable=0', r.stdout)
        self.assertIn('top reasons: all_open_items_gated=2', r.stdout)
        self.assertNotIn('no work', r.stdout.lower())

    def test_invariant_holds_on_a_mixed_board(self):
        self.b.bean('bean-0001'); self.b.bean('bean-0002', body='architect-first'); self.b.bean('bean-0003', body='- [x] done')
        self.b.bean('bean-0004', body='- [ ] real work\n- [ ] founder ok')
        self.b.seat('free1')
        r = self.b.run('--dry-run')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('board_ready=4 dispatchable=2', r.stdout)


if __name__ == '__main__':
    unittest.main()
