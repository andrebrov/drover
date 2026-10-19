#!/usr/bin/env python3
"""bean-yhb7 — fleet-stale-worktree judges a live seat, and never flags an idle one.

The script ships in this tree (bin/fleet-stale-worktree) and is what fleet-watch calls. Two
behaviours are load-bearing and were the reason a restarted watcher raised seven false alerts
(lead 2026-10-17): only a seat with agent_status working/blocked is judged, and the seat's base
is measured against origin/main.

Run: python3 bundle/tests/test_fleet_stale_worktree.py   (pytest also collects it)
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.normpath(os.path.join(HERE, '..', 'bin', 'fleet-stale-worktree'))


def _git(cwd, *args):
    return subprocess.run(['git', '-C', cwd, *args], capture_output=True, text=True, check=True).stdout


def _fixture(tmp):
    """A clone four commits behind its origin/main, plus a stubbed `herdr` on PATH."""
    origin = os.path.join(tmp, 'origin')
    os.makedirs(origin)
    _git(origin, 'init', '-q', '-b', 'main')
    _git(origin, 'config', 'user.email', 't@t')
    _git(origin, 'config', 'user.name', 't')
    for i in range(5):
        open(os.path.join(origin, 'f.txt'), 'w').write(str(i))
        _git(origin, 'add', '-A')
        _git(origin, 'commit', '-q', '-m', f'c{i}')
    seat = os.path.join(tmp, 'seat')
    subprocess.run(['git', 'clone', '-q', origin, seat], check=True, capture_output=True)
    _git(seat, 'checkout', '-q', 'HEAD~4')  # 4 behind origin/main
    return seat


def _stub_herdr(tmp, seat, status):
    """A fake `herdr agent list` reporting one seat with the given agent_status."""
    bindir = os.path.join(tmp, 'stub-bin')
    os.makedirs(bindir, exist_ok=True)
    payload = json.dumps({'result': {'agents': [
        {'name': 'seat-a', 'cwd': seat, 'agent': 'opencode', 'agent_status': status},
    ]}})
    path = os.path.join(bindir, 'herdr')
    with open(path, 'w') as f:
        f.write(f'#!/usr/bin/env python3\nprint({payload!r})\n')
    os.chmod(path, 0o755)
    return bindir


def _run(seat, status):
    tmp = os.path.dirname(seat)
    bindir = _stub_herdr(tmp, seat, status)
    env = dict(os.environ, PATH=bindir + os.pathsep + os.environ['PATH'], FLEET_REPO=seat, DROVER_REPO=seat)
    # An isolated harness-off file keeps the test independent of the machine's watcher state.
    home = os.path.join(tmp, 'home')
    os.makedirs(os.path.join(home, '.local/share/fleet-watch/state'), exist_ok=True)
    env['HOME'] = home
    return subprocess.run(['python3', SCRIPT, '--max', '1'], capture_output=True, text=True, env=env)


def test_idle_seat_is_never_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        seat = _fixture(tmp)
        idle = _run(seat, 'idle')
        assert idle.returncode == 0, idle.stdout + idle.stderr
        assert 'STALE' not in idle.stdout


def test_working_seat_behind_is_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        seat = _fixture(tmp)
        working = _run(seat, 'working')
        assert working.returncode == 1, working.stdout + working.stderr
        assert 'STALE seat-a' in working.stdout


def test_blocked_seat_is_judged_too():
    with tempfile.TemporaryDirectory() as tmp:
        seat = _fixture(tmp)
        blocked = _run(seat, 'blocked')
        assert blocked.returncode == 1, blocked.stdout + blocked.stderr


if __name__ == '__main__':
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print(f'ok   {name}')
            except AssertionError as exc:
                failures += 1
                print(f'FAIL {name}: {exc}')
    sys.exit(1 if failures else 0)
