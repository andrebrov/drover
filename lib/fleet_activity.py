"""Live landing evidence shared by status and dispatch, independent of pane ancestry.

Process arguments identify the executable; cwd identifies the owning worktree.
Only landing candidates need cwd lookups. Never expose process arguments to users.
"""
import json
import os
from pathlib import Path
import shlex
import re
import subprocess
import sys


LANDERS = {'fleet-land', 'fleet-land.sh', 'pre-push'}
INTERPRETERS = {'bash', 'sh', 'zsh', 'dash', 'python', 'python3', 'node', 'perl'}


def is_landing(row):
    if row[1].startswith('Z'):
        return False
    try:
        args = shlex.split(row[3]) if len(row) > 3 else []
    except ValueError:
        return False
    if not args:
        return False
    executable = os.path.basename(args[0])
    if executable in LANDERS:
        return True
    if executable not in INTERPRETERS:
        return False
    # Only the script operand is executable evidence. A shell -c string, grep,
    # editor or test filename merely mentioning fleet-land.sh is not a lander.
    for arg in args[1:]:
        if arg in ('-c', '--eval') or (arg == '-e' and executable in {'node', 'perl'}) or (arg.startswith('-') and 'c' in arg[1:]):
            return False
        if arg == '--':
            continue
        if arg.startswith('-'):
            continue
        return os.path.basename(arg) in LANDERS
    return False


def candidate_cwds(pids):
    if not pids:
        return {}
    if Path('/proc/self/cwd').exists():
        result = {}
        for pid in pids:
            try:
                result[pid] = os.readlink(f'/proc/{pid}/cwd')
            except OSError:
                pass
        return result
    # macOS: one bounded lsof call for candidate PIDs, not one call per seat.
    try:
        result = subprocess.run(['lsof', '-a', '-p', ','.join(map(str, pids)),
                                 '-d', 'cwd', '-Fpn'], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    cwds, pid = {}, None
    for line in result.stdout.splitlines():
        if line.startswith('p') and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith('n') and pid in pids:
            cwds[pid] = line[1:]
    return cwds


def process_table():
    try:
        result = subprocess.run(['ps', '-axo', 'pid=,ppid=,stat=,etime=,comm='],
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            return None
        table = {}
        for line in result.stdout.splitlines():
            pid, parent, state, elapsed, command = line.split(None, 4)
            table[int(pid)] = (int(parent), state, os.path.basename(command), '', None, elapsed)
        arguments = subprocess.run(['ps', '-axwwo', 'pid=,args='],
                                   capture_output=True, text=True, timeout=10)
        if arguments.returncode:
            return None
        for line in arguments.stdout.splitlines():
            pid, _, args = line.strip().partition(' ')
            if pid.isdigit() and int(pid) in table:
                table[int(pid)] = table[int(pid)][:3] + (args.strip(),) + table[int(pid)][4:]
        candidates = {pid for pid, row in table.items() if is_landing(row)}
        cwds = candidate_cwds(candidates)
        for pid in candidates:
            table[pid] = table[pid][:4] + (cwds.get(pid),) + table[pid][5:]
        return table
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def landing_state(cwd, table):
    """True: owned lander; False: none; None: cannot establish ownership."""
    if table is None:
        return None
    if not cwd:
        return False
    root = os.path.realpath(cwd)
    unknown = False
    for row in table.values():
        if not is_landing(row):
            continue
        process_cwd = row[4] if len(row) > 4 else None
        if not process_cwd:
            unknown = True
            continue
        process_cwd = os.path.realpath(process_cwd)
        if process_cwd == root or process_cwd.startswith(root + os.sep):
            return True
    return None if unknown else False


def landing_label(cwd, table):
    root = os.path.realpath(cwd)
    for pid, row in sorted(table.items()):
        if not is_landing(row) or len(row) < 5 or not row[4]:
            continue
        process_cwd = os.path.realpath(row[4])
        if process_cwd != root and not process_cwd.startswith(root + os.sep):
            continue
        bean = next((arg for arg in shlex.split(row[3])[1:]
                     if re.fullmatch(os.environ.get('DROVER_BEAN_RE', r'bean-[a-z0-9]+'), arg)), None)
        if bean is None:
            # pre-push receives remote arguments, not a bean; use the owning
            # worktree branch, never a task title/report or a process substring.
            try:
                branch = subprocess.run(['git', '-C', root, 'branch', '--show-current'],
                                        capture_output=True, text=True, timeout=10)
                match = re.search(r'(?:^|[-/])(' + os.environ.get('DROVER_BEAN_RE', r'bean-[a-z0-9]+') + r')(?:$|[-/])', branch.stdout.strip())
                if branch.returncode == 0 and match:
                    bean = match[1]
            except (OSError, subprocess.TimeoutExpired):
                pass
        age = row[5] if len(row) > 5 else 'age-unavailable'
        return f"{bean or 'bean-unavailable'} {age}"
    return 'bean-unavailable age-unavailable'


def annotate_status(rows, table):
    for row in rows:
        row = dict(row)
        landing = landing_state(row.get('cwd'), table)
        if landing:
            row['agent_status'] = 'LANDING'
            row['landing_label'] = landing_label(row['cwd'], table)
            row['terminal_title_stripped'] = row['landing_label'] + ' ' + (row.get('terminal_title_stripped') or '')
        elif landing is None and row.get('agent_status') in ('done', 'idle'):
            row['agent_status'] = 'unknown'
        yield row


if __name__ == '__main__':
    rows = [json.loads(line) for line in sys.stdin if line.strip()]
    for row in annotate_status(rows, process_table()):
        print(json.dumps(row))
