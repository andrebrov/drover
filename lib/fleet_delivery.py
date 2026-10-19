"""Guard native prompt delivery; no scheduler, queue, model calls, or key injection.

Independently implemented after reviewing herdr-synapse's stable-idle gate:
https://github.com/vitalysim/herdr-synapse/tree/b8a5c5f27ddc4ddb23a4b6ee929ee3adc3bb9f6a
A hold is exit 75, with a reason only. Pane text never enters logs.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

STABLE_SECONDS = 2.0
IDLE = {'idle', 'done'}
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
DIALOG = re.compile(r'(?i)(?:enter to (?:select|confirm|submit)|esc to cancel|allow once|allow always|do you (?:want|trust)|question \d+ of \d+)')

class Held(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def transport():
    return os.environ.get('FLEET_HERDR') or os.environ.get('HERDR') or 'herdr'


def state_directory():
    return Path(os.environ.get('FLEET_STATE', str(Path.home()/'.local/share/fleet-watch/state')))


def hold_unknown(seat):
    """Use the existing operator-hold marker, preserving any concurrent hold."""
    directory=state_directory()/'hold'
    directory.mkdir(parents=True,exist_ok=True)
    try:
        with (directory/seat).open('x') as marker:
            marker.write('UNKNOWN_DELIVERY: inspect the native pane before retry\n')
    except FileExistsError:
        pass


def native(command, args):
    try:
        result = subprocess.run([command, 'agent', *args], text=True, capture_output=True,
                                timeout=5, stdin=subprocess.DEVNULL)
        if result.returncode:
            raise Held('native_unavailable')
        return result.stdout
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise Held('native_unavailable') from None


def agent_row(raw, seat):
    try:
        row = json.loads(raw)['result']['agent']
        if not isinstance(row, dict) or row.get('name') != seat:
            raise ValueError()
        required = ('agent', 'pane_id', 'terminal_id', 'cwd')
        if any(not isinstance(row.get(k), str) or not row[k] for k in required):
            raise ValueError()
        if type(row.get('state_change_seq')) is not int:
            raise ValueError()
        if row.get('agent_status') not in IDLE:
            raise Held('not_idle')
        if row.get('interactive_ready') is not True:
            raise Held('not_ready')
        return row
    except (ValueError, KeyError, TypeError):
        raise Held('identity_unavailable') from None


def identity(row):
    session = row.get('agent_session')
    # A native session phase may change on compaction without changing conversation.
    value = session.get('value') if isinstance(session, dict) else None
    return tuple(row[k] for k in ('name', 'agent', 'pane_id', 'terminal_id', 'cwd', 'state_change_seq')) + (value,)


def composer_reason(screen, kind):
    """Recognize empty native composers; absent/unknown layouts always hold."""
    lines = ANSI.sub('', screen).splitlines()
    if kind not in {'codex', 'claude', 'opencode', 'grok', 'agy', 'cursor'}:
        return 'composer_unavailable'
    glyphs = ('›',) if kind == 'codex' else ('❯', '>') if kind == 'claude' else ('→', '❯', '>', '›')
    candidates = [(i, line.lstrip()) for i, line in enumerate(lines)
                  if any(line.lstrip() == g or line.lstrip().startswith(g+' ') for g in glyphs)]
    if not candidates:
        return 'composer_unavailable'
    index, prompt = candidates[-1]
    # A visible footer fences the current composer from an old transcript prompt.
    tail = lines[index+1:]
    footer = next((i for i, line in enumerate(tail)
                   if re.search(r'\? (?:for )?shortcuts|(?:ctrl|tab)[+ ]', line, re.I)
                   or (kind == 'codex' and re.match(r'\s{2,}(?:GPT|gpt|o[134])[-\w.]* (?:low|medium|high|xhigh|max) · ', line))
                   or re.fullmatch(r'\s*[─━]{4,}\s*', line)), None)
    if footer is None:
        return 'composer_unavailable'
    region = '\n'.join([prompt, *tail[:footer+1]])
    if DIALOG.search(region):
        return 'dialog_present'
    draft = prompt[1:].strip()
    if kind == 'codex' and draft.strip(' \t'+''.join(chr(n) for n in range(0x2800, 0x2900))) == 'Ask Codex to do anything':
        draft = ''
    if kind == 'opencode' and draft == 'Add a follow-up':
        draft = ''
    if draft or any(line.strip() for line in tail[:footer]):
        return 'draft_present'
    return None


def snapshot(seat, command, read=native):
    first = agent_row(read(command, ['get', seat]), seat)
    try:
        explain = json.loads(read(command, ['explain', first['pane_id'], '--json']))
        if (not isinstance(explain, dict) or explain.get('state') not in IDLE
                or explain.get('visible_blocker') is not False
                or explain.get('skip_state_update') is not False
                or explain.get('visible_working') is True
                or explain.get('skipped_update_reason')):
            raise Held('detection_blocked')
    except (ValueError, TypeError):
        raise Held('detection_unavailable') from None
    screen = read(command, ['read', first['pane_id'], '--source', 'detection', '--lines', '80'])
    reason = composer_reason(screen, first['agent'])
    if reason:
        raise Held(reason)
    fresh = agent_row(read(command, ['get', seat]), seat)
    if identity(first) != identity(fresh):
        raise Held('identity_changed')
    return fresh, hashlib.sha256(screen.encode()).hexdigest()


@contextmanager
def delivery_gate(seat, command=None, read=native, sleep=time.sleep, clock=time.monotonic):
    """Keep the seat lock through the native send; never unlink a shared lock inode."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', seat):
        raise Held('invalid_seat')
    state = state_directory()
    if (state/'hold'/seat).exists():
        raise Held('seat_held')
    locks = state/'delivery-locks'
    locks.mkdir(parents=True, exist_ok=True)
    with (locks/(seat+'.lock')).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Held('delivery_in_flight') from None
        command = command or transport()
        first, screen = snapshot(seat, command, read)
        start = clock()
        sleep(STABLE_SECONDS)
        last, last_screen = snapshot(seat, command, read)
        if identity(first) != identity(last):
            raise Held('identity_changed')
        if screen != last_screen or clock()-start < STABLE_SECONDS:
            raise Held('unstable')
        yield last


def delivered(raw, expected):
    """A zero native exit alone does not prove the prompt left the composer."""
    try:
        envelope=json.loads(raw)
        if envelope.get('error'):
            return False
        row=envelope['result']['agent']
        if any(row.get(k) != expected[k] for k in ('name','agent','pane_id','terminal_id','cwd')):
            return False
        previous=(expected.get('agent_session') or {}).get('value')
        if previous is not None and (row.get('agent_session') or {}).get('value') != previous:
            return False
        return (row.get('agent_status') in {'working','blocked'}
                or (row.get('agent_status') in IDLE
                    and type(row.get('state_change_seq')) is int
                    and row['state_change_seq'] > expected['state_change_seq']))
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def main(args):
    check = args and args[0] == '--check'
    if check:
        args = args[1:]
    if not args or (not check and len(args) < 2):
        print('usage: fleet_delivery.py [--check] SEAT [PROMPT [native flags]]', file=sys.stderr)
        return 2
    try:
        with delivery_gate(args[0]) as target:
            if check:
                return 0
            # Every send waits for native acceptance. No automatic retry on ambiguity.
            flags = args[2:]
            if '--wait' not in flags:
                flags = [*flags, '--wait', '--until', 'working', '--timeout', '10000']
            result = subprocess.run([transport(), 'agent', 'prompt', target['pane_id'], args[1], *flags],
                                    text=True, capture_output=True, stdin=subprocess.DEVNULL)
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            if result.returncode or not delivered(result.stdout, target):
                hold_unknown(args[0])
                print(f'UNKNOWN_DELIVERY {args[0]}: no verified acceptance; inspect before retry', file=sys.stderr)
                return 76
            return 0
    except Held as hold:
        print(f'HELD {args[0]}: {hold.reason}', file=sys.stderr)
        return 75
    except OSError:
        print(f'HELD {args[0]}: native_unavailable', file=sys.stderr)
        return 75

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
