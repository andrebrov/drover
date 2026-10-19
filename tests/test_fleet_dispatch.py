#!/usr/bin/env python3
"""bean-wqs7: fleet-dispatch against fixture boards. Known-bad (the 2026-10-14 incident shape) and known-good cases."""
import json
import signal
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import time
import unittest
from unittest import mock
import importlib.machinery
import importlib.util

TOOL = Path(os.environ.get('FLEET_DISPATCH_TEST_TOOL') or
            Path(__file__).resolve().parents[1] / 'lib' / 'fleet_dispatch.py')


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
        env = dict(os.environ, FLEET_DISPATCH_FIXTURE=str(self.root), DROVER_REFILL_DEDICATED='agy-coder fable-architect judge special-purpose-seat', DROVER_DEDICATED_FILE=str(self.root/'.config/drover/dedicated-seats'), HOME=str(self.root))
        r = subprocess.run(['python3', str(TOOL), *args], capture_output=True, text=True, env=env)
        return r

    def owner(self, bean, seat, at=None):
        """bean-yqk7: the record `fleet assign` leaves for a bean it handed to a seat."""
        p = self.root / 'config' / 'assigned-owners.txt'
        with open(p, 'a') as f:
            f.write(f'{bean} {seat}' + (f' @{int(at)}' if at else '') + '\n')

    def landed(self, *beans):
        (self.root / 'landed.txt').write_text(''.join(f'{b}\n' for b in beans))

    def skip(self, *beans, who='lead', date=None, reason='parked by fixture'):
        """A well-formed skip entry: `# <who> <date>: <reason>` (bean-4un4). Default date is today, local."""
        date = date or time.strftime('%Y-%m-%d')
        with open(self.root / 'config' / 'dispatch-skip.txt', 'a') as f:
            for bean in beans:
                f.write(f'{bean}\t# {who} {date}: {reason}\n')

    def report(self, seat, bean, verdict=''):
        (self.root / 'reports' / f'{seat}-{bean}.md').write_text(f'# {bean}\n{verdict}\n' if verdict else f'# {bean}\nwork in progress\n')

    def assigned(self):
        p = self.root / 'assign.log'
        return p.read_text().split('\n')[:-1] if p.exists() else []


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('FLEET_PROOF_SCRATCH'))
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

    def test_dry_json_snapshot_has_counts_and_never_assigns(self):
        self.b.bean('bean-0001')
        self.b.bean('bean-0002')
        self.b.bean('bean-0003')
        self.b.seat('busy', 'working', 'fleet/busy-bean-0001')
        self.b.seat('free')
        self.b.skip('bean-0002')
        r = self.b.run('--dry-run', '--json')
        self.assertEqual(r.returncode, 0)
        try:
            payload = json.loads(r.stdout)
        except json.JSONDecodeError:
            payload = {}
        self.assertEqual(payload.get('board_ready'), 3)
        self.assertEqual(payload.get('dispatchable_count'), 1)
        self.assertEqual([b['id'] for b in payload['dispatchable']], ['bean-0003'])
        self.assertEqual(self.b.assigned(), [])

    def test_json_without_dry_run_refuses_without_assigning(self):
        self.b.bean('bean-0001')
        self.b.seat('free')
        r = self.b.run('--json')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.b.assigned(), [])

    def test_known_good_working_seat_bean_and_skip_listed_are_never_dispatched(self):
        self.b.bean('bean-0001'); self.b.bean('bean-0002'); self.b.bean('bean-0003')
        self.b.seat('busy', 'working', 'fleet/busy-bean-0001')
        self.b.seat('free1'); self.b.seat('free2')
        self.b.skip('bean-0002')
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
        """Known-bad: the real claude-coder bean-s7s3 report begins '# bean-s7s3 (claude-coder) — BLOCKED …' and has no BLOCKED last line; the
        tool re-sent that bean. Fixture is a byte copy of that report."""
        fixture = Path(__file__).resolve().parent / 'fixtures' / 'report-blocked-heading-bean-s7s3.md'
        self.b.bean('bean-s7s3'); self.b.bean('bean-v001'); self.b.bean('bean-v002'); self.b.bean('bean-ok01')
        (self.b.root / 'reports' / 'claude-coder-bean-s7s3.md').write_text(fixture.read_text())
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
        (self.b.root / 'config' / 'refill-pins.txt').write_text('idler\tbean-done\nworker\tbean-live\n')
        r = self.b.run()
        self.assertIn('pin removed: idler bean-done', r.stdout)
        pins = (self.b.root / 'config' / 'refill-pins.txt').read_text()
        self.assertNotIn('bean-done', pins)
        self.assertIn('worker\tbean-live', pins)

    def test_pin_survives_idle_seat_with_newer_report_until_main_completes(self):
        self.b.bean('bean-0001', ready=False, status='in-progress')
        self.b.seat('idler')
        pins = self.b.root / 'config' / 'refill-pins.txt'
        pins.write_text('idler\tbean-0001 @%d\n' % (time.time() - 3600))
        (self.b.root / 'reports' / 'idler-bean-0001.md').write_text('done\nDONE /p\n')
        r = self.b.run()
        self.assertNotIn('pin removed: idler bean-0001', r.stdout)
        self.assertIn('idler\tbean-0001', pins.read_text())

    def test_a_pinned_bean_goes_to_its_free_seat_first_and_not_to_others(self):
        self.b.bean('bean-0001', priority='low'); self.b.bean('bean-0002', priority='critical')
        self.b.seat('pinned'); self.b.seat('other')
        (self.b.root / 'config' / 'refill-pins.txt').write_text('pinned\tbean-0001\n')
        r = self.b.run()
        self.assertEqual(sorted(self.b.assigned()), ['bean-0001 pinned', 'bean-0002 other'])

    def test_cap_and_load_limit_assignments_but_never_hide_the_board(self):
        for i in range(3):
            self.b.bean(f'bean-000{i}')
        self.b.seat('free1'); self.b.seat('free2')
        for i in range(8):
            self.b.seat(f'w{i}', 'working', f'fleet/w{i}-bean-9{i}99')
        (self.b.root / 'config' / 'fleet-cap.txt').write_text('cap_working=8\nresume_load_below=20\n')
        r = self.b.run()
        self.assertIn('board_ready=3 dispatchable=3', r.stdout)
        self.assertEqual(self.b.assigned(), [])
        self.assertIn('cap or load limit', r.stdout)

    def test_dedicated_seats_are_never_free(self):
        self.b.bean('bean-0001')
        for s in ('agy-coder', 'fable-architect', 'judge', 'lead-backup', 'special-purpose-seat'):
            self.b.seat(s)
        r = self.b.run()
        self.assertIn('free_seats=0', r.stdout)
        self.assertEqual(self.b.assigned(), [])

    def test_zero_dispatchable_prints_top_reasons_never_no_work(self):
        self.b.bean('bean-0001', body='- [ ] founder approves'); self.b.bean('bean-0002', body='- [ ] OPERATOR: merchant sign-off')
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


class SkipList(unittest.TestCase):
    """bean-4un4 — retro 2026-10-17 rule 3: 37 skip entries had no reason, 15 were seat work and 8 already
    closed; the dispatcher showed dispatchable=0 for hours. An entry parks a bean ONLY when it carries
    `# <who> <date>: <reason>` and its date is at most 72 h old; every other entry is ignored AND reported."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('FLEET_PROOF_SCRATCH'))
        self.addCleanup(self.tmp.cleanup)
        self.b = Board(self.tmp.name)
        self.now = 1800000000  # 2027-01-15 03:00 local — a fixed "now" for the 72 h clock
        patch = mock.patch.dict(os.environ, FLEET_DISPATCH_NOW=str(self.now)); patch.start()
        self.addCleanup(patch.stop)
        self.b.seat('free')

    def date(self, epoch):
        return time.strftime('%Y-%m-%d', time.localtime(epoch))

    def test_an_entry_without_a_reason_is_ignored_and_printed_as_a_finding(self):
        self.b.bean('bean-0001')
        (self.b.root / 'config' / 'dispatch-skip.txt').write_text('bean-0001\n')
        r = self.b.run('--dry-run')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('board_ready=1 dispatchable=1', r.stdout)  # the unexplained entry no longer hides work
        self.assertIn('skip entry ignored: bean-0001', r.stdout)
        self.assertIn('no reason', r.stdout)

    def test_a_hand_written_comment_is_not_a_reason(self):
        """The real file has `# owned by claude-reviewer (lead 15:14)` — no who/date/reason triple."""
        self.b.bean('bean-0001')
        (self.b.root / 'config' / 'dispatch-skip.txt').write_text('bean-0001\t# owned by claude-reviewer (lead 15:14)\n')
        r = self.b.run('--dry-run')
        self.assertIn('dispatchable=1', r.stdout)
        self.assertIn('skip entry ignored: bean-0001', r.stdout)

    def test_an_entry_older_than_72h_is_ignored_and_printed_as_a_finding(self):
        self.b.bean('bean-0001')
        old = self.date(self.now - 4 * 86400)
        (self.b.root / 'config' / 'dispatch-skip.txt').write_text(f'bean-0001\t# lead {old}: closed long ago\n')
        r = self.b.run('--dry-run')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('board_ready=1 dispatchable=1', r.stdout)
        self.assertIn('skip entry ignored: bean-0001', r.stdout)
        self.assertIn('expired', r.stdout)
        self.assertIn('> 72h', r.stdout)

    def test_an_unparseable_date_is_ignored_and_reported_not_a_crash(self):
        self.b.bean('bean-0001')
        (self.b.root / 'config' / 'dispatch-skip.txt').write_text('bean-0001\t# lead 2026-13-45: bogus date\n')
        r = self.b.run('--dry-run')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('dispatchable=1', r.stdout)
        self.assertIn('skip entry ignored: bean-0001', r.stdout)
        self.assertIn('unparseable date', r.stdout)

    def test_a_valid_recent_reasoned_entry_still_skips_and_shows_age_and_reason(self):
        self.b.bean('bean-0001')
        today = self.date(self.now)
        (self.b.root / 'config' / 'dispatch-skip.txt').write_text(f'bean-0001\t# lead {today}: waiting on founder\n')
        r = self.b.run('--dry-run')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('board_ready=1 dispatchable=0', r.stdout)
        self.assertIn('excluded skip_listed: 1', r.stdout)
        self.assertIn('skip entry: bean-0001 age=', r.stdout)
        self.assertIn(f'reason="lead {today}: waiting on founder"', r.stdout)
        self.assertNotIn('skip entry ignored', r.stdout)

    def test_two_days_old_still_skips_and_three_days_old_expires(self):
        self.b.bean('bean-keep'); self.b.bean('bean-drop')
        (self.b.root / 'config' / 'dispatch-skip.txt').write_text(
            f'bean-keep\t# lead {self.date(self.now - 2 * 86400)}: still current\n'
            f'bean-drop\t# lead {self.date(self.now - 3 * 86400)}: past the window\n')
        r = self.b.run('--dry-run')
        self.assertIn('excluded skip_listed: 1', r.stdout)
        self.assertIn('skip entry ignored: bean-drop', r.stdout)
        self.assertIn('dispatchable=1', r.stdout)

    def test_json_mode_keeps_stdout_pure_and_reports_ignored_entries_in_the_payload(self):
        self.b.bean('bean-0001')
        (self.b.root / 'config' / 'dispatch-skip.txt').write_text('bean-0001\n')
        r = self.b.run('--dry-run', '--json')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        payload = json.loads(r.stdout)  # any stray text line breaks this
        self.assertEqual(payload['dispatchable_count'], 1)
        self.assertEqual([e['bean'] for e in payload['skip_ignored']], ['bean-0001'])


class WaitingUntil(unittest.TestCase):
    """bean-km76 — retro 2026-10-17 rule 5: 5 beans (50jg, l8gh, qedc, rgle, w8sg) were dispatched and came
    back 'no change' in 2-5 min because they wait on a scheduled run or data. A seat that waits writes
    `WAITING-UNTIL <ISO or EDT time>: <what>` as its report's LAST line; the dispatcher parks the bean
    (reason `waiting_until`) until that time passes, then offers it again."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('FLEET_PROOF_SCRATCH'))
        self.addCleanup(self.tmp.cleanup)
        self.b = Board(self.tmp.name)
        self.b.seat('free')
        self.now = 1800000000  # 2027-01-15 08:00Z / 03:00 EST — a fixed clock
        patch = mock.patch.dict(os.environ, FLEET_DISPATCH_NOW=str(self.now)); patch.start()
        self.addCleanup(patch.stop)

    def report_line(self, line):
        (self.b.root / 'reports' / 'seat-bean-wait1.md').write_text(f'# bean-wait1\n{line}\n')

    def snapshot(self, now):
        with mock.patch.dict(os.environ, FLEET_DISPATCH_NOW=str(now)):
            r = self.b.run('--dry-run', '--json')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def test_a_future_waiting_until_parks_the_bean_and_the_passed_time_offers_it_again(self):
        self.b.bean('bean-wait1')
        self.report_line('WAITING-UNTIL 2027-01-15T09:00:00Z: nightly run has not landed yet')
        d = self.snapshot(self.now)
        self.assertEqual([b['id'] for b in d['dispatchable']], [])   # parked, not dispatchable
        self.assertEqual(d['excluded'].get('waiting_until'), 1)
        d = self.snapshot(self.now + 3600)                            # the clock passes it
        self.assertEqual([b['id'] for b in d['dispatchable']], ['bean-wait1'])
        self.assertNotIn('waiting_until', d['excluded'])

    def test_an_edt_local_time_is_read_as_america_new_york(self):
        """04:00 EST is 09:00Z, an hour ahead of the clock; read as UTC it would already be past."""
        self.b.bean('bean-wait1')
        self.report_line('WAITING-UNTIL 2027-01-15 04:00 EST: data sync at 04:00 EST')
        self.assertEqual(self.snapshot(self.now)['excluded'].get('waiting_until'), 1)

    def test_a_bare_stamp_is_read_as_america_new_york(self):
        self.b.bean('bean-wait1')
        self.report_line('WAITING-UNTIL 2027-01-15 04:00: data sync at 04:00 local')
        self.assertEqual(self.snapshot(self.now)['excluded'].get('waiting_until'), 1)

    def test_an_unparseable_time_never_parks_the_bean_and_is_reported(self):
        self.b.bean('bean-wait1')
        self.report_line('WAITING-UNTIL whenever the sync lands: data')
        d = self.snapshot(self.now)
        self.assertEqual([b['id'] for b in d['dispatchable']], ['bean-wait1'])
        self.assertEqual([e['bean'] for e in d['waiting_until_ignored']], ['bean-wait1'])
        r = self.b.run('--dry-run')
        self.assertIn('waiting-until ignored: bean-wait1', r.stdout)

    def test_a_waiting_line_that_is_not_the_last_line_does_not_park(self):
        self.b.bean('bean-wait1')
        (self.b.root / 'reports' / 'seat-bean-wait1.md').write_text(
            '# bean-wait1\nWAITING-UNTIL 2027-01-15T09:00:00Z: sync\nmore work happened after\n')
        self.assertEqual([b['id'] for b in self.snapshot(self.now)['dispatchable']], ['bean-wait1'])


class Retro(unittest.TestCase):
    """bean-yqk7 — retro 2026-10-15: 7 beans were dispatched to a second seat while the first still held them
    or had already finished them. Each shape below is one of those 7; the last bean is genuinely free."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('FLEET_PROOF_SCRATCH'))
        self.addCleanup(self.tmp.cleanup)
        self.b = Board(self.tmp.name)

    def test_the_seven_wrong_dispatches_are_all_refused_and_a_free_bean_still_goes(self):
        # rhv3 — lead-assigned to a seat that is now working on something else; no terminal report at all.
        self.b.bean('bean-rhv3'); self.b.owner('bean-rhv3', 'claude-coder'); self.b.report('claude-coder', 'bean-rhv3')
        # v58h — owned by its dedicated seat and blocked with a fresh report.
        self.b.bean('bean-v58h'); self.b.owner('bean-v58h', 'special-purpose-seat')
        self.b.report('special-purpose-seat', 'bean-v58h', 'BLOCKED /r/v58h.md')
        # s4tf, a26i, 9op6, ml9a, fyt4 — the seat reported DONE and its commits are already on origin/main,
        # while the bean file still said in-progress (5 of the 7).
        for bid in ('bean-s4tf', 'bean-a26i', 'bean-9op6', 'bean-ml9a', 'bean-fyt4'):
            self.b.bean(bid); self.b.report('claude-coder', bid, 'DONE /r/%s.md' % bid)
        self.b.landed('bean-s4tf', 'bean-a26i', 'bean-9op6', 'bean-ml9a', 'bean-fyt4')
        # the genuinely free bean
        self.b.bean('bean-free1')

        self.b.seat('worker', 'working', 'fleet/worker-bean-zzzz')
        self.b.seat('spare')
        r = self.b.run()

        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('board_ready=8 dispatchable=1', r.stdout)
        self.assertIn('excluded assigned_to_owner_seat: 2', r.stdout)
        self.assertIn('excluded landed_report_done: 5', r.stdout)
        self.assertEqual(self.b.assigned(), ['bean-free1 spare'])

    def test_a_done_report_without_commits_on_main_is_still_work(self):
        """After the installed 24-hour review hold, unlanded work stays on the board."""
        self.b.bean('bean-0001'); self.b.report('coder', 'bean-0001', 'DONE /r/0001.md')
        old = time.time() - 25 * 3600
        os.utime(self.b.root / 'reports' / 'coder-bean-0001.md', (old, old))
        self.b.seat('free1')
        r = self.b.run()
        self.assertIn('board_ready=1 dispatchable=1', r.stdout)
        self.assertEqual(self.b.assigned(), ['bean-0001 free1'])

    def test_landed_commits_without_a_done_report_are_still_work(self):
        """Partial work already on main (the usual case) must not hide an open bean."""
        self.b.bean('bean-0002'); self.b.report('coder', 'bean-0002', 'BLOCKED /r/0002.md')
        self.b.landed('bean-0002')
        self.b.seat('free1')
        r = self.b.run()
        # the fresh BLOCKED report is the reason; the landed commits alone are not.
        self.assertIn('excluded recently_blocked: 1', r.stdout)
        self.assertNotIn('landed_report_done', r.stdout)

    def test_an_owner_record_is_released_when_its_bean_completes_on_main(self):
        # lead 2026-10-17: only a completed/scrapped bean or a BLOCKED report releases an owner. An idle seat's
        # newer DONE report is a slice hand-back, not a hand-back of the bean (see OwnerHandback).
        self.b.bean('bean-0003', status='completed')
        self.b.owner('bean-0003', 'idler', at=time.time() - 48 * 3600)
        self.b.report('idler', 'bean-0003', 'DONE /r/0003.md')
        self.b.seat('idler'); self.b.seat('free1')
        r = self.b.run()
        self.assertIn('owner record removed: idler bean-0003 (bean completed/scrapped)', r.stdout)
        self.assertNotIn('idler', (self.b.root / 'config' / 'assigned-owners.txt').read_text())

    def test_an_owner_record_survives_while_the_seat_is_working(self):
        self.b.bean('bean-0004')
        self.b.owner('bean-0004', 'worker', at=time.time() - 3600)
        self.b.seat('worker', 'working', 'fleet/worker-bean-other')
        self.b.seat('free1')
        r = self.b.run()
        self.assertIn('excluded assigned_to_owner_seat: 1', r.stdout)
        self.assertIn('bean-0004 worker', (self.b.root / 'config' / 'assigned-owners.txt').read_text())
        self.assertEqual(self.b.assigned(), [])

    def test_record_owner_writes_one_record_per_bean(self):
        self.b.bean('bean-0005')
        r = self.b.run('--record-owner', 'bean-0005', 'first')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('owner recorded: bean-0005 -> first', r.stdout)
        self.b.run('--record-owner', 'bean-0005', 'second')
        self.b.run('--record-owner', 'bean-0006', 'other')
        lines = (self.b.root / 'config' / 'assigned-owners.txt').read_text().splitlines()
        self.assertEqual([l.split()[:2] for l in lines], [['bean-0005', 'second'], ['bean-0006', 'other']])


class OwnerHandback(unittest.TestCase):
    """lead 2026-10-17 — one owner end to end. A slice report (DONE/PARTIAL on a multi-slice bean) is not a
    hand-back: only a BLOCKED report releases the bean to another seat, a completed/scrapped bean releases it,
    and a seat owns only its newest assignment (s28a/rgle were re-sent to other seats between slices)."""

    setUp = T.setUp

    def test_a_slice_done_report_does_not_release_the_owner(self):
        self.b.bean('bean-slice1')
        self.b.owner('bean-slice1', 'owner', at=time.time() - 3600)
        self.b.report('owner', 'bean-slice1', 'DONE /r/slice1.md')  # slice 1 of N, not the whole bean
        self.b.seat('owner')                                        # idle between slices
        self.b.seat('spare')
        r = self.b.run()
        self.assertIn('excluded assigned_to_owner_seat: 1', r.stdout)
        self.assertNotIn('owner record', r.stdout)                  # nothing was released
        self.assertIn('bean-slice1 owner', (self.b.root / 'config' / 'assigned-owners.txt').read_text())
        self.assertEqual(self.b.assigned(), [])

    def test_a_blocked_report_releases_the_owner(self):
        self.b.bean('bean-block1')
        self.b.owner('bean-block1', 'owner', at=time.time() - 26 * 3600)
        self.b.report('owner', 'bean-block1', 'BLOCKED /r/block1.md')
        old = time.time() - 25 * 3600                                  # newer than the record, older than the 24 h hold
        os.utime(self.b.root / 'reports' / 'owner-bean-block1.md', (old, old))
        self.b.seat('owner')
        self.b.seat('spare')
        (self.b.root / 'config' / 'refill-pins.txt').write_text('spare bean-block1\n')
        r = self.b.run()
        self.assertIn('owner record removed: owner bean-block1 (owner reported BLOCKED)', r.stdout)
        self.assertNotIn('bean-block1 owner', (self.b.root / 'config' / 'assigned-owners.txt').read_text())
        self.assertEqual(self.b.assigned(), ['bean-block1 spare'])     # released to another seat

    def test_an_older_record_of_a_seat_is_released_when_the_seat_moves_on(self):
        now = time.time()
        self.b.bean('bean-old1'); self.b.bean('bean-new1')
        self.b.owner('bean-old1', 'seat', at=now - 3600)
        self.b.owner('bean-new1', 'seat', at=now - 60)                 # the seat's newest assignment
        self.b.seat('seat'); self.b.seat('spare')
        (self.b.root / 'config' / 'refill-pins.txt').write_text('spare bean-old1\n')
        r = self.b.run()
        self.assertIn('owner record removed: seat bean-old1 (seat moved to a newer assignment)', r.stdout)
        self.assertIn('bean-new1 seat', (self.b.root / 'config' / 'assigned-owners.txt').read_text())
        self.assertEqual(self.b.assigned(), ['bean-old1 spare'])       # the older assignment is free to move on


class InstalledRouting(unittest.TestCase):
    """Preserve live routing policy while merging the yqk7 exclusion checks."""

    setUp = T.setUp

    def test_linear_only_seats_do_not_receive_bean_work(self):
        self.b.bean('bean-free1')
        self.b.seat('a-linear'); self.b.seat('z-coder')
        config = self.b.root / '.config' / 'drover'
        config.mkdir(parents=True)
        (config / 'dedicated-seats').write_text('a-linear\n')
        r = self.b.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.b.assigned(), ['bean-free1 z-coder'])

    def test_unanswered_question_holds_seat_until_answered(self):
        self.b.bean('bean-free1'); self.b.seat('coder')
        root = self.b.root / '.local/share/drover/reports'
        (root / 'questions').mkdir(parents=True)
        (root / 'decisions').mkdir()
        (root / 'questions' / 'q1.json').write_text(json.dumps({'id':'q1','seat':'coder'}))
        r = self.b.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.b.assigned(), [])
        (root / 'decisions' / 'q1.json').write_text('{}')
        self.b.run()
        self.assertEqual(self.b.assigned(), ['bean-free1 coder'])

    def ask(self, qid, seat, bean):
        root = self.b.root / '.local/share/drover/reports'
        (root / 'questions').mkdir(parents=True, exist_ok=True)
        (root / 'decisions').mkdir(parents=True, exist_ok=True)
        (root / 'questions' / f'{qid}.json').write_text(json.dumps({'id': qid, 'seat': seat, 'bean': bean}))

    def test_question_holds_its_seat_only_while_it_is_about_the_seats_current_assignment(self):
        # bean-jzex: retro rule 4. A seat owns only its current assignment (newest record in assigned-owners.txt).
        self.b.bean('bean-free1'); self.b.bean('bean-old1', ready=False); self.b.bean('bean-cur1', ready=False)
        self.b.seat('coder')
        self.b.owner('bean-old1', 'coder', at=1000); self.b.owner('bean-cur1', 'coder', at=2000)
        # open question on the CURRENT bean: still holds
        self.ask('q-cur', 'coder', 'bean-cur1')
        r = self.b.run()
        self.assertEqual(self.b.assigned(), [], r.stdout)
        self.assertNotIn('stale question ignored', r.stdout)
        # the same seat also has an older, unanswered question: it is reported and does not hold
        (self.b.root / '.local/share/drover/reports/decisions/q-cur.json').write_text('{}')
        self.ask('q-old', 'coder', 'bean-old1')
        r = self.b.run()
        self.assertEqual(self.b.assigned(), ['bean-free1 coder'], r.stdout)
        self.assertIn('stale question ignored: coder bean-old1 q-old', r.stdout)

    def test_question_about_a_completed_or_scrapped_bean_does_not_hold_even_if_it_is_the_current_one(self):
        self.b.bean('bean-free1'); self.b.bean('bean-done1', status='completed', ready=False)
        self.b.seat('coder'); self.b.owner('bean-done1', 'coder', at=1000)
        self.ask('q-done', 'coder', 'bean-done1')
        r = self.b.run()
        self.assertEqual(self.b.assigned(), ['bean-free1 coder'], r.stdout)
        self.assertIn('stale question ignored: coder bean-done1 q-done', r.stdout)

    def test_question_with_no_provable_staleness_still_holds(self):
        # no owner record for the seat, bean unknown, or no bean on the question: absent evidence never frees a seat
        self.b.bean('bean-free1'); self.b.seat('coder')
        self.ask('q-nobean', 'coder', '')
        self.assertEqual(self.b.run().returncode, 0)
        self.assertEqual(self.b.assigned(), [])

    def test_cpu_idle_allows_work_despite_high_load_average(self):
        self.b.bean('bean-free1'); self.b.seat('coder')
        (self.b.root / 'load1.txt').write_text('99')
        (self.b.root / 'idle.txt').write_text('80')
        self.b.run()
        self.assertEqual(self.b.assigned(), ['bean-free1 coder'])

    def test_cpu_busy_respects_minimum_working_floor(self):
        for n in range(3): self.b.bean(f'bean-fre{n}'); self.b.seat(f'free{n}')
        self.b.seat('w1','working'); self.b.seat('w2','working')
        (self.b.root / 'idle.txt').write_text('0')
        (self.b.root / 'config' / 'fleet-cap.txt').write_text('cap_working=8\nmin_working=3\n')
        self.b.run()
        self.assertEqual(len(self.b.assigned()), 1)

    def test_cpu_busy_with_floor_met_does_not_assign(self):
        self.b.bean('bean-free1'); self.b.seat('free')
        self.b.seat('worker','working')
        (self.b.root / 'idle.txt').write_text('0')
        (self.b.root / 'config' / 'fleet-cap.txt').write_text('cap_working=8\nmin_working=1\n')
        self.b.run()
        self.assertEqual(self.b.assigned(), [])

    def test_previous_owner_is_preferred_when_free(self):
        self.b.bean('bean-free1')
        self.b.seat('a-spare'); self.b.seat('z-owner')
        self.b.report('z-owner','bean-free1')
        self.b.run()
        self.assertEqual(self.b.assigned(), ['bean-free1 z-owner'])

    def test_done_seat_keeps_its_branch_until_terminal_report(self):
        self.b.bean('bean-held1'); self.b.seat('owner','done','fleet/owner-bean-held1')
        self.b.report('owner','bean-held1')
        self.b.seat('spare')
        r = self.b.run()
        self.assertIn('excluded on_working_seat_branch: 1', r.stdout)
        self.assertEqual(self.b.assigned(), [])

    def test_merchant_and_approved_copy_are_coding_work(self):
        self.b.bean('bean-free1',body='- [ ] Render approved copy the merchant sees')
        self.b.seat('coder')
        self.b.run()
        self.assertEqual(self.b.assigned(), ['bean-free1 coder'])

    def test_awaiting_lead_tag_is_preserved(self):
        self.b.bean('bean-wait1'); self.b.seat('coder')
        path = self.b.root / 'beans' / 'bean-wait1--x.md'
        path.write_text(path.read_text().replace('type: task', 'type: task\ntags:\n  - awaiting-lead'))
        r = self.b.run()
        self.assertIn('excluded awaiting_lead_or_founder: 1', r.stdout)
        self.assertEqual(self.b.assigned(), [])

    def test_done_landed_slice_with_more_coding_work_still_dispatches(self):
        self.b.bean('bean-free1',body='- [x] First slice\n- [ ] Implement second slice')
        self.b.landed('bean-free1'); self.b.report('old','bean-free1','DONE /r/slice.md')
        self.b.seat('coder')
        self.b.run()
        self.assertEqual(self.b.assigned(), ['bean-free1 coder'])

    def test_landed_done_is_not_redispatched_after_recent_report_window(self):
        self.b.bean('bean-done1'); self.b.landed('bean-done1')
        self.b.report('old','bean-done1','DONE /r/done.md')
        old = time.time() - 25*3600
        os.utime(self.b.root / 'reports' / 'old-bean-done1.md',(old,old))
        self.b.seat('coder')
        r = self.b.run()
        self.assertIn('excluded landed_report_done: 1',r.stdout)
        self.assertEqual(self.b.assigned(), [])


class Revival(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ.get('FLEET_PROOF_SCRATCH'))
        self.addCleanup(self.tmp.cleanup)
        self.b=Board(self.tmp.name)
        self.now=2000000000
        patch=mock.patch.dict(os.environ,FLEET_DISPATCH_NOW=str(self.now));patch.start();self.addCleanup(patch.stop)
        self.b.seat('free')

    def progress(self,bid,age):
        p=self.b.root/'progress.json'
        data=json.loads(p.read_text()) if p.exists() else {}
        data[bid]={'at':self.now-age,'sha':'a'*40}
        p.write_text(json.dumps(data))

    def test_orphaned_old_in_progress_is_visible_even_outside_ready_list(self):
        self.b.bean('bean-old1',status='in-progress',ready=False)
        self.progress('bean-old1',5*3600)
        r=self.b.run('--dry-run','--json'); self.assertEqual(r.returncode,0,r.stderr)
        d=json.loads(r.stdout)
        self.assertEqual([b['id'] for b in d['dispatchable']],['bean-old1'])
        self.assertEqual(d['revived'][0]['id'],'bean-old1')
        self.assertEqual(self.b.assigned(),[])

    def test_recent_in_progress_is_not_revived(self):
        self.b.bean('bean-new1',status='in-progress',ready=False)
        self.progress('bean-new1',3*3600)
        r=self.b.run('--dry-run','--json'); d=json.loads(r.stdout)
        self.assertEqual(d['dispatchable'],[])
        self.assertEqual(d['excluded'].get('in_progress_recent_or_unknown'),1)

    def test_four_hour_boundary_is_literal_and_working_owner_still_protects(self):
        for bid,age in [('bean-edge',4*3600),('bean-soon',4*3600-1),('bean-busy',8*3600)]:
            self.b.bean(bid,status='in-progress',ready=False);self.progress(bid,age)
        self.b.seat('worker','working','fleet/worker-bean-busy')
        d=json.loads(self.b.run('--dry-run','--json').stdout)
        self.assertEqual([b['id'] for b in d['dispatchable']],['bean-edge'])
        self.assertNotIn('bean-busy',[b['id'] for b in d['revived']])

    def test_stale_revival_preserves_pin_and_dry_run_does_not_mutate_it(self):
        self.b.bean('bean-old1',status='in-progress',ready=False)
        self.progress('bean-old1',5*3600)
        self.b.seat('departed','unknown','fleet/departed-bean-old1')
        self.b.owner('bean-old1','departed',at=self.now-5*3600)
        pins=self.b.root/'config/refill-pins.txt';pins.write_text('departed bean-old1 @'+str(self.now-5*3600)+'\n')
        owner=self.b.root/'config/assigned-owners.txt';before=(pins.read_text(),owner.read_text())
        r=self.b.run('--dry-run'); self.assertIn('would revive bean-old1',r.stdout)
        self.assertNotIn('would assign bean-old1 -> free',r.stdout)
        self.assertEqual((pins.read_text(),owner.read_text()),before)
        r=self.b.run();self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self.b.assigned(),[])
        self.assertIn('bean-old1',pins.read_text())

    def test_revived_assignment_carries_prior_progress_in_native_scope_argument(self):
        self.b.bean('bean-old1',status='in-progress',ready=False)
        self.progress('bean-old1',5*3600)
        self.b.report('retired','bean-old1')
        self.b.run()
        p=self.b.root/'assign-commands.jsonl'
        self.assertTrue(p.exists(),'native assignment payload was not captured')
        command=json.loads(p.read_text().splitlines()[0])
        self.assertEqual(command[:4],[str(TOOL.parents[1]/'bin/fleet'),'assign','bean-old1','free'])
        self.assertIn('Revived stale in-progress bean bean-old1',command[4])
        self.assertIn('a'*40,command[4]);self.assertIn(str(self.b.root/'reports/retired-bean-old1.md'),command[4])

    def test_revival_preserves_awaiting_founder_gate(self):
        self.b.bean('bean-wait',status='in-progress',ready=False)
        p=self.b.root/'beans/bean-wait--x.md';p.write_text(p.read_text().replace('type: task','type: task\ntags:\n  - awaiting-founder'))
        self.progress('bean-wait',5*3600)
        d=json.loads(self.b.run('--dry-run','--json').stdout)
        self.assertEqual(d['dispatchable'],[]);self.assertEqual(d['excluded'].get('awaiting_lead_or_founder'),1)

    def test_unknown_progress_does_not_fabricate_an_expired_commit(self):
        self.b.bean('bean-unkn',status='in-progress',ready=False,created='unavailable')
        d=json.loads(self.b.run('--dry-run','--json').stdout)
        self.assertEqual(d['dispatchable'],[]);self.assertEqual(d['excluded'].get('in_progress_recent_or_unknown'),1)

    def test_non_coder_parent_does_not_expire_its_owner_or_appear_revived(self):
        self.b.bean('bean-root',type='epic',status='in-progress',ready=False)
        self.progress('bean-root',8*3600);self.b.owner('bean-root','departed',at=self.now-8*3600)
        d=json.loads(self.b.run('--dry-run','--json').stdout)
        self.assertEqual(d['revived'],[])
        self.b.run()
        self.assertIn('departed',(self.b.root/'config/assigned-owners.txt').read_text())

    def test_founder_reserved_owner_is_not_reassigned_by_revival(self):
        self.b.bean('bean-pin1',status='in-progress',ready=False)
        self.progress('bean-pin1',8*3600)
        self.b.owner('bean-pin1','special-purpose-seat',at=self.now-8*3600)
        self.b.run()
        self.assertEqual(self.b.assigned(),[])
        self.assertIn('special-purpose-seat',(self.b.root/'config/assigned-owners.txt').read_text())

    def test_native_assignment_call_and_existing_brief_generator_receive_revival_scope(self):
        self.b.bean('bean-old1',status='in-progress',ready=False)
        self.progress('bean-old1',5*3600);self.b.write()
        env=dict(HOME=str(self.b.root),FLEET_ROOT=str(self.b.root),FLEET_DISPATCH_FIXTURE=str(self.b.root))
        with mock.patch.dict(os.environ,env):
            loader=importlib.machinery.SourceFileLoader('native_dispatch',str(TOOL))
            spec=importlib.util.spec_from_loader('native_dispatch',loader)
            module=importlib.util.module_from_spec(spec);loader.exec_module(module)
            board=module.load_board(str(self.b.root))
            calls=[]
            def native(command,cwd=None): calls.append(command);return 0,'',''
            with mock.patch.object(module,'FIX',None),mock.patch.object(module,'load_board',return_value=board), \
                 mock.patch.object(module,'load_seats',return_value=self.b.seats), \
                 mock.patch.object(module,'seat_busy_reasons',return_value=[]), \
                 mock.patch.object(module,'latest_progress',return_value=json.loads((self.b.root/'progress.json').read_text())), \
                 mock.patch.object(module,'landed_ids',return_value=set()),mock.patch.object(module,'cpu_idle',return_value=100), \
                 mock.patch.object(module,'load_load1',return_value=0),mock.patch.object(module,'sh',side_effect=native), \
                 mock.patch.object(module.sys,'argv',[str(TOOL)]):
                with self.assertRaises(SystemExit) as done: module.main()
                self.assertEqual(done.exception.code,0)
        self.assertEqual(len(calls),1)
        command=calls[0];self.assertEqual(command[:4],[str(TOOL.parents[1]/'bin/fleet'),'assign','bean-old1','free'])
        self.assertIn('Revived stale in-progress bean',command[4])
        bindir=self.b.root/'bin';bindir.mkdir()
        git=bindir/'git';git.write_text('#!/bin/bash\ncase "$*" in *ls-tree*) echo .beans/bean-old1--x.md;; esac\n');git.chmod(0o755)
        transport=bindir/'no-transport';transport.write_text('#!/bin/bash\nexit 88\n');transport.chmod(0o755)
        preview_env=dict(os.environ,HOME=str(self.b.root),FLEET_ROOT=str(self.b.root),FLEET='free:codex',
                         FLEET_HERDR=str(transport),PATH=str(bindir)+':'+os.environ['PATH'])
        r=subprocess.run(['bash',str(TOOL.parents[1]/'bin/fleet'),'--dry-run',*command[1:]],env=preview_env,capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('prompt=',r.stdout);self.assertIn(command[4],r.stdout)

    def test_real_history_finds_commit_without_bean_in_subject_on_bean_branch(self):
        loader=importlib.machinery.SourceFileLoader('revival_dispatch',str(TOOL))
        spec=importlib.util.spec_from_loader('revival_dispatch',loader)
        module=importlib.util.module_from_spec(spec);loader.exec_module(module)
        collector=getattr(module,'latest_progress',None)
        self.assertTrue(callable(collector),'missing actual Git progress collector')
        repo=self.b.root/'repo';repo.mkdir()
        env={k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_AUTHOR_DATE='2026-10-16T12:00:00Z',GIT_COMMITTER_DATE='2026-10-16T12:00:00Z')
        def git(*args): return subprocess.check_output([shutil.which('git'),*args],cwd=repo,env=env,text=True,stderr=subprocess.DEVNULL).strip()
        git('init','-q','-b','fleet/left-bean-old1');git('config','user.email','fixture@example.test');git('config','user.name','Fixture')
        (repo/'work').write_text('real progress');git('add','work');git('commit','-qm','progress with no bean in subject')
        sha=git('rev-parse','HEAD')
        with mock.patch.object(module,'FIX',None):
            progress=collector(str(repo))
        self.assertEqual(progress['bean-old1']['sha'],sha)
        self.assertEqual(progress['bean-old1']['at'],1792152000)





class BusySeatEvidence(unittest.TestCase):
    """bean-y5xh: execute dispatch with real process trees and real Git graphs."""
    setUp = T.setUp

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def make_repo(self):
        self.repo = self.b.root / 'seat'
        self.repo.mkdir()
        self.git('init', '-b', 'fleet/reviewer-bean-old1')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Fixture')
        self.git('commit', '--allow-empty', '-m', 'base')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')

    def test_done_seat_with_unlanded_commit_is_held_until_ancestor_of_main(self):
        self.make_repo()
        self.git('commit', '--allow-empty', '-m', 'own pending fix')
        self.b.bean('bean-new1')
        self.b.seat('reviewer', 'done', 'fleet/reviewer-bean-old1')
        self.b.seats[-1]['cwd'] = str(self.repo)
        r = self.b.run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.b.assigned(), [], r.stdout)
        self.assertIn('unlanded_commits', r.stdout)
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.b.run()
        self.assertEqual(self.b.assigned(), ['bean-new1 reviewer'])

    def test_done_seat_with_live_pre_push_child_is_never_assigned(self):
        self.make_repo()
        # A real local Git push blocked inside its pre-push hook, not a status stub.
        remote = self.b.root / 'provider.git'
        subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
        hook = self.repo / '.git/hooks/pre-push'
        self.git('config', 'core.hooksPath', str(hook.parent))
        hook.write_text('#!/usr/bin/env bash\nprintf ready > .git/hook-started\nsleep 60\n')
        hook.chmod(0o755)
        shell = subprocess.Popen(['bash', '-c', 'git push "$1" HEAD:refs/heads/main & wait', '_', str(remote)],
                                 cwd=self.repo, start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def stop():
            if shell.poll() is None:
                os.killpg(shell.pid, signal.SIGTERM)
            shell.wait(timeout=5)
        self.addCleanup(stop)
        self.b.bean('bean-old1', status='in-progress', ready=False,
                    created='2020-01-01T00:00:00Z')
        self.b.bean('bean-new1')
        self.b.seat('reviewer', 'done', 'fleet/reviewer-bean-old1')
        self.b.seats[-1].update(cwd=str(self.repo), shell_pid=shell.pid)
        self.b.report('reviewer', 'bean-old1', 'DONE /report')
        self.b.owner('bean-old1', 'reviewer', at=time.time()-3600)
        # Wait for the actual pre-push entry, not a timing assumption.
        for _ in range(200):
            if (self.repo / '.git/hook-started').exists(): break
            time.sleep(.01)
        else:
            self.fail('fixture pre-push hook never started')
        r = self.b.run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.b.assigned(), [], r.stdout)
        self.assertIn('live_child', r.stdout)
        self.assertIn('bean-old1 reviewer',
                      (self.b.root/'config/assigned-owners.txt').read_text())
        stop()
        idle_shell = subprocess.Popen(['sleep', '60'])
        self.addCleanup(lambda: (idle_shell.terminate(), idle_shell.wait(timeout=5)))
        self.b.seats[-1]['shell_pid'] = idle_shell.pid
        self.b.run()
        self.assertEqual(self.b.assigned(), ['bean-new1 reviewer'])

    def test_unknown_git_evidence_holds_seat(self):
        self.b.bean('bean-new1')
        self.b.seat('reviewer', 'done')
        self.b.seats[-1]['cwd'] = str(self.b.root / 'missing')
        r = self.b.run()
        self.assertEqual(self.b.assigned(), [], r.stdout)
        self.assertIn('git_unavailable', r.stdout)

    def module(self):
        self.b.write()
        with mock.patch.dict(os.environ, FLEET_DISPATCH_FIXTURE=str(self.b.root), HOME=str(self.b.root)):
            loader = importlib.machinery.SourceFileLoader('busy_dispatch', str(TOOL))
            spec = importlib.util.spec_from_loader('busy_dispatch', loader)
            module = importlib.util.module_from_spec(spec)
            loader.exec_module(module)
        return module

    def test_harness_launcher_and_code_host_are_idle_but_their_work_children_are_busy(self):
        module = self.module()
        table = {1:(0,'S','shell'), 2:(1,'S','node'), 3:(2,'S','codex'),
                 4:(3,'S','codex-code-mode-host'), 5:(4,'Z','bash')}
        self.assertIs(module.live_work_child(1, table), False)
        table[6] = (4,'S','bash')
        table[7] = (6,'S','sleep')
        self.assertIs(module.live_work_child(1, table), True)
        self.assertIsNone(module.live_work_child(1, None))
        self.assertIsNone(module.live_work_child(999, table))

    def test_harness_infrastructure_children_are_not_work_but_a_real_work_child_is(self):
        # bean-emi6: tsserver/language servers and MCP servers live for days under a seat shell and must not read as work.
        module = self.module()
        base = {1:(0,'S','shell','zsh'), 2:(1,'S','node','node harness'), 3:(2,'S','claude','claude')}
        infra = {**base,
                 4:(3,'S','node','node /x/node_modules/typescript-language-server/lib/cli.mjs --stdio'),
                 5:(4,'S','node','node /x/node_modules/typescript/lib/tsserver.js --useInferredProjectPerProjectRoot'),
                 6:(3,'S','npm','npm exec @modelcontextprotocol/server-github'),
                 7:(6,'S','node','node /x/.bin/mcp-server-github'),
                 8:(3,'S','tessl','example mcp')}
        self.assertIs(module.live_work_child(1, infra), False)
        self.assertIs(module.live_work_child(1, {**infra, 9:(3,'S','node','node node_modules/.bin/jest --runInBand src/x.test.ts')}), True)
        # a test run whose ARGUMENTS merely mention mcp is work, not infrastructure
        self.assertIs(module.live_work_child(1, {**infra, 9:(3,'S','node','node jest --runInBand src/mcp/__tests__/a.test.ts')}), True)
        self.assertIs(module.live_work_child(1, {**infra, 9:(3,'S','node','node jest src/mcp')}), True)
        # work started BY an infrastructure process (a tsserver child) is still not counted: the tree under infra is infra
        self.assertIs(module.live_work_child(1, {**infra, 9:(5,'S','node','node typingsInstaller.js')}), False)
        # a three-column row (no args captured) stays work: absent evidence never hides a child
        self.assertIs(module.live_work_child(1, {**base, 4:(3,'S','node')}), True)

    def test_process_evidence_unavailable_refuses_assignment(self):
        self.b.bean('bean-new1')
        self.b.seat('reviewer', 'done')
        self.b.seats[-1]['shell_pid'] = -1
        r = self.b.run()
        self.assertEqual(self.b.assigned(), [], r.stdout)
        self.assertIn('process_unavailable', r.stdout)

    def test_recheck_refuses_a_child_that_appears_after_planning(self):
        self.make_repo()
        self.b.bean('bean-new1')
        self.b.seat('reviewer', 'done')
        self.b.seats[-1].update(cwd=str(self.repo), shell_pid=1)
        module = self.module()
        idle = {1:(0,'S','shell')}
        busy = {**idle, 2:(1,'S','bash'), 3:(2,'S','git')}
        with mock.patch.object(module, 'process_table', side_effect=[idle, busy]), \
             mock.patch.object(module.sys, 'argv', [str(TOOL)]):
            with self.assertRaises(SystemExit) as done:
                module.main()
            self.assertEqual(done.exception.code, 0)
        self.assertEqual(self.b.assigned(), [])

    def test_scrapped_pin_is_not_a_completed_main_bean(self):
        self.b.bean('bean-old1', status='scrapped', ready=False)
        self.b.seat('reviewer', 'done')
        self.b.bean('bean-new1')
        pins = self.b.root/'config/refill-pins.txt'
        pins.write_text('reviewer bean-old1\n')
        r = self.b.run()
        self.assertEqual(self.b.assigned(), [], r.stdout)
        self.assertEqual(pins.read_text(), 'reviewer bean-old1\n')

if __name__ == '__main__':
    unittest.main()
