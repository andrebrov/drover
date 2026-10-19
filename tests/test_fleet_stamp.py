#!/usr/bin/env python3
"""bean-90dj — stamped measurements: stale, incomplete, other tool version and fresh. Run: python3 tests/test_fleet_stamp.py"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
from fleet_stamp import Unavailable, read_fresh, stamp  # noqa: E402

NOW = 1_000_000.0


def fixture():
    tmp = Path(tempfile.mkdtemp())
    tool = tmp / 'tool.py'
    tool.write_text('print(1)\n')
    return tmp, tool, tmp / 'out.json'


def test_fresh_stamped_file_is_shown():
    tmp, tool, out = fixture()
    stamp(out, tool, {'unlanded': 91}, inputs={'tip': 'abc'}, now=NOW)
    assert read_fresh(out, 7200, tool, now=NOW + 60) == {'unlanded': 91}


def test_frozen_file_with_old_computed_at_is_unavailable_stale():
    tmp, tool, out = fixture()
    stamp(out, tool, {'unlanded': 91}, now=NOW)             # the 10-13 count
    r = read_fresh(out, 7200, tool, now=NOW + 5 * 86400)     # read five days later
    assert isinstance(r, Unavailable) and r.reason == 'stale' and str(r) == 'UNAVAILABLE(stale)'
    assert isinstance(read_fresh(out, 7200, tool, now=NOW - 10), Unavailable)   # a future stamp is not fresh either


def test_touching_the_file_does_not_make_it_fresh():
    tmp, tool, out = fixture()
    stamp(out, tool, {'n': 1}, now=NOW)
    os.utime(out, (NOW + 5 * 86400, NOW + 5 * 86400))        # mtime is not the stamp
    assert read_fresh(out, 7200, tool, now=NOW + 5 * 86400).reason == 'stale'


def test_writer_killed_before_the_marker_is_unavailable_incomplete():
    tmp, tool, out = fixture()
    out.write_text(json.dumps({'stamp': {'computed_at': NOW, 'tool_sha256': 'x', 'complete': False}, 'payload': {'n': 1}}))
    assert read_fresh(out, 7200, tool, now=NOW).reason == 'incomplete'


def test_unstamped_or_missing_or_garbled_is_missing_marker():
    tmp, tool, out = fixture()
    assert read_fresh(out, 7200, tool, now=NOW).reason == 'missing_marker'      # absent
    out.write_text('{"unlanded": 91}')                                           # a bare, unstamped number
    assert read_fresh(out, 7200, tool, now=NOW).reason == 'missing_marker'
    out.write_text('{"stamp": {"complete": true')                                # truncated write
    assert read_fresh(out, 7200, tool, now=NOW).reason == 'missing_marker'


def test_changed_tool_file_makes_old_output_other_tool_version():
    tmp, tool, out = fixture()
    stamp(out, tool, {'n': 1}, now=NOW)
    tool.write_text('print(2)\n')
    assert read_fresh(out, 7200, tool, now=NOW + 1).reason == 'other_tool_version'


def test_other_inputs_are_refused_only_when_the_reader_names_them():
    tmp, tool, out = fixture()
    stamp(out, tool, {'n': 1}, inputs={'tip': 'a'}, now=NOW)
    assert read_fresh(out, 7200, tool, now=NOW, inputs={'tip': 'a'}) == {'n': 1}
    assert read_fresh(out, 7200, tool, now=NOW, inputs={'tip': 'b'}).reason == 'other_inputs'


def test_a_falsy_payload_is_a_value_not_unavailable():
    tmp, tool, out = fixture()
    stamp(out, tool, 0, now=NOW)
    r = read_fresh(out, 7200, tool, now=NOW)
    assert r == 0 and not isinstance(r, Unavailable)


def run_cli(*args):
    import subprocess
    p = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib', 'fleet_stamp.py'), *args],
                       capture_output=True, text=True)
    return p.returncode, p.stdout


def test_cli_prints_fields_when_fresh_and_unavailable_with_exit_1_otherwise():
    tmp, tool, out = fixture()
    stamp(out, tool, {'unlanded_branches_60m': 91, 'stuck_seats': 0}, now=time.time())
    assert run_cli('read', str(out), '7200', str(tool), 'unlanded_branches_60m', 'stuck_seats') == (0, 'unlanded_branches_60m: 91\nstuck_seats: 0\n')
    assert run_cli('read', str(out), '7200', str(tool), 'nope') == (0, 'nope: UNAVAILABLE(missing_field)\n')
    stamp(out, tool, {'unlanded_branches_60m': 91}, now=time.time() - 86400)
    assert run_cli('read', str(out), '7200', str(tool), 'unlanded_branches_60m') == (1, 'UNAVAILABLE(stale)\n')
    assert run_cli('read', str(tmp / 'absent.json'), '7200', str(tool)) == (1, 'UNAVAILABLE(missing_marker)\n')


if __name__ == '__main__':
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_'):
            try:
                fn(); print('ok  ', name)
            except AssertionError as e:
                failed += 1; print('FAIL', name, e)
    sys.exit(1 if failed else 0)
