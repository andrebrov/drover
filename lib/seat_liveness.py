"""One owner of per-seat usage-limit state.

fleet-watch marks a seat when its pane shows a usage-limit banner; dispatch and review routing read
the same marks instead of guessing from `agent_status`, which reports `done` for a seat that is
waiting out a limit. A mark records the reset time parsed from the banner when there is one; it is
cleared at that reset, or as soon as the seat is seen on a successful turn (idle/done with an OK
budget verdict). A mark with no parseable reset survives for FALLBACK_S and no longer, so a topped-up
seat rejoins without the lead having to remember it.

State format: one file per seat under <state>/credit-dead/<seat>, JSON
{"reset_at": <epoch seconds|null>, "at": <epoch seconds>, "source": "<short text>"}.
Legacy empty files are read as reset=null with the file mtime as `at`.
"""
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path

FALLBACK_S = 6 * 60 * 60
MONTHS = {m: i for i, m in enumerate(
    ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'), start=1)}
TRY_AGAIN = re.compile(
    r'try again at\s+([A-Z][a-z]{2})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\s+(\d{1,2}):(\d{2})\s*([AP]M)', re.I)
RESETS_AT = re.compile(r'resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)', re.I)


def state_dir():
    """$FLEET_STATE, else drover-env's value: every tool agrees on where the marks live."""
    if os.environ.get('FLEET_STATE'):
        return Path(os.environ['FLEET_STATE'])
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fleet_config import configure
    return Path(configure()['FLEET_STATE'])


def mark_path(seat, state=None):
    return (Path(state) if state else state_dir()) / 'credit-dead' / seat


def parse_reset(text, now=None):
    """Epoch seconds for a limit banner's reset time, or None when the text has no usable time."""
    now = time.time() if now is None else now
    m = TRY_AGAIN.search(text or '')
    if m:
        month = MONTHS.get(m.group(1)[:3].title())
        if month:
            try:
                local = datetime.datetime(int(m.group(3)), month, int(m.group(2)), int(m.group(4)), int(m.group(5)))
                return local.timestamp()
            except ValueError:
                pass
    m = RESETS_AT.search(text or '')
    if m:
        hour = int(m.group(1)) % 12
        if m.group(3).lower() == 'pm':
            hour += 12
        minute = int(m.group(2) or 0)
        today = datetime.datetime.fromtimestamp(now).replace(hour=hour, minute=minute, second=0, microsecond=0)
        reset = today if today.timestamp() > now else today + datetime.timedelta(days=1)
        return reset.timestamp()
    return None


def _read_raw(seat, state=None):
    path = mark_path(seat, state)
    try:
        raw = path.read_text().strip()
        mtime = path.stat().st_mtime
    except OSError:
        return None
    reset = None
    if raw:
        try:
            payload = json.loads(raw) if raw.startswith('{') else {'reset_at': float(raw)}
            value = payload.get('reset_at')
            reset = float(value) if value is not None else None
        except (ValueError, TypeError, json.JSONDecodeError):
            reset = None
    return path, reset, mtime


def mark_limit(seat, reset_at=None, source='', state=None, now=None):
    now = time.time() if now is None else now
    path = mark_path(seat, state)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {'reset_at': reset_at, 'at': now, 'source': (source or '')[:120]}
    path.write_text(json.dumps(payload) + '\n')
    return payload


def clear(seat, state=None):
    try:
        mark_path(seat, state).unlink()
        return True
    except OSError:
        return False


def limit_reason(seat, state=None, now=None):
    """The printed reason a seat is not usable, or None. Expired marks are cleared here."""
    now = time.time() if now is None else now
    found = _read_raw(seat, state)
    if found is None:
        return None
    path, reset, at = found
    if reset is not None and now >= reset:
        clear(seat, state)
        return None
    if reset is None and now - at >= FALLBACK_S:
        clear(seat, state)
        return None
    if reset is not None:
        return 'usage limit until ' + datetime.datetime.fromtimestamp(reset).strftime('%a %d %b %H:%M')
    return 'usage limit (reset unknown)'


def clear_recovered(seat, status, verdict, state=None):
    """The next successful turn clears the mark: idle/done and the budget read says the seat is OK."""
    if status in ('idle', 'done') and str(verdict).upper() == 'OK' and _read_raw(seat, state) is not None:
        return clear(seat, state)
    return False


def _main(argv):
    command = argv[0] if argv else ''
    seat = argv[1] if len(argv) > 1 else ''
    if command == 'busy':
        reason = limit_reason(seat)
        if reason is None:
            return 1
        print(reason)
        return 0
    if command == 'reason':
        reason = limit_reason(seat)
        if reason is None:
            return 1
        print(reason)
        return 0
    if command == 'mark':
        pane = sys.stdin.read() if not sys.stdin.isatty() else ''
        reset = parse_reset(pane)
        mark_limit(seat, reset, pane.strip().splitlines()[-1] if pane.strip() else 'banner')
        print(limit_reason(seat) or '')
        return 0
    if command == 'clear':
        clear(seat)
        return 0
    if command == 'clear-if-ok':
        clear_recovered(seat, argv[2] if len(argv) > 2 else '', argv[3] if len(argv) > 3 else '')
        return 0
    if command == 'list':
        directory = state_dir() / 'credit-dead'
        try:
            names = sorted(entry.name for entry in directory.iterdir())
        except OSError:
            names = []
        for name in names:
            reason = limit_reason(name)
            if reason:
                print(f'{name}\t{reason}')
        return 0
    print('usage: seat_liveness.py busy|reason|mark|clear|clear-if-ok|list <seat>', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))
