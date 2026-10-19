#!/usr/bin/env python3
"""fleet-dispatch — the one dispatch tool; it cannot hide ready work (bean-wqs7).

Incident 2026-10-14, 02:51-08:48 EDT: the lead said "no ready work" while `beans list --ready` held 150 coder beans: an ad-hoc filter dropped
every bean with no checklist, a hand-kept "recent" list went stale, and four seats sat on stale pins. This tool replaces that filter.

  fleet-dispatch [--dry-run]      print the board, assign dispatchable beans to free seats via `fleet assign <bean> <seat>`

ALWAYS prints `board_ready=N dispatchable=M` and a reason histogram, and checks board_ready == dispatchable + sum(reasons). It never prints
"no work": when dispatchable is 0 while board_ready is above 0 it prints the top reasons instead.

Ready = origin/main beans, `beans list --json --ready`, type bug|task|feature, priority critical > high > normal > low > deferred. A bean is excluded
only for a NAMED reason, the first that applies, in this order:
  on_working_seat_branch   a working seat has fleet/<seat>-<bean> checked out right now
  assigned_to_owner_seat   fleet assign recorded an owner who still holds the bean
  skip_listed              in ~/.config/herdr/dispatch-skip.txt, with a `# <who> <date>: <reason>` comment no older than 72 h
  waiting_until            the newest report's last line is `WAITING-UNTIL <ISO or EDT time>: <what>` and that time is still ahead
  pinned_to_busy_seat      pinned (refill-pins.txt) to a seat that is not free
  recently_blocked         the newest report for the bean, written in the last 24 h, ends with a BLOCKED line or has BLOCKED in its first 3 lines (heading, Verdict)
  landed_report_done       newest report ends DONE, commits landed, and no coding checklist items remain
  needs_architect_or_founder   the bean text says architect-first / founder reviews
  all_open_items_gated     a checklist exists, items are open, and every open item is founder/approval/architect/merchant/connector-push
  all_items_checked        a checklist exists and nothing is open
A bean with NO checklist is dispatchable. "Already tried" comes only from reports and beans: there is no hand-kept list.

Skip entries expire (retro 2026-10-17 rule 3): 37 entries had no reason, 15 were seat work and 8 already closed, and the board
showed dispatchable=0 for hours. An entry parks a bean ONLY when it carries `# <who> <date>: <reason>` and its date is at most
72 h before now (NOW, i.e. FLEET_DISPATCH_NOW or the system clock; the date is local midnight, the host's timezone). An entry with
no comment, a malformed one, an unparseable date or an expired date is IGNORED and printed as a finding (`--dry-run` prints every
entry's age and reason).

Waiting reports (retro 2026-10-17 rule 5): 5 beans (50jg, l8gh, qedc, rgle, w8sg) were dispatched and came back "no change" in
2-5 min because they wait on a scheduled run or data. A seat that waits writes `WAITING-UNTIL <ISO or EDT time>: <what>` as its
report's LAST line; the dispatcher then parks the bean (reason waiting_until) while that time is in the future and offers it again
once the time passes. An ISO stamp with `Z` or a numeric offset is absolute; a bare ISO stamp and an `EDT`/`EST`/`America/New_York`
stamp are read as America/New_York wall time. A line that is not last, or whose time does not parse, never parks the bean: it is
ignored and an unparseable one is printed as a finding (`waiting_until_ignored` in `--json`).

Pin expiry: a pin is removed (logged) ONLY when its bean is completed on origin/main. Reports, idle status and stale revival do not release a pin.
Free seats: idle/done with no live work child and no unlanded commits (unavailable evidence holds the seat), not the configured dedicated or lead seats.
A free seat with a pin gets its pinned bean first. Cap: working seats < cap_working and load1 < resume_load_below (~/.config/herdr/fleet-cap.txt).

Test hook: FLEET_DISPATCH_FIXTURE=<dir> with beans/*.md, ready.txt, seats.json ({name,status,branch}), reports/, config/, load1.txt, assign.log.
"""
import sys as _fleet_sys
from pathlib import Path as _FleetPath
_fleet_sys.path.insert(0, str(_FleetPath(__file__).resolve().parents[1] / "lib"))
from fleet_config import configure
config=configure(export=__name__=="__main__")
from fleet_ownership import current_assignments as _current_assignments
from fleet_usage import gate as _fleet_gate
_fleet_gate(_fleet_sys.argv, str(_FleetPath(__file__).resolve().parents[1]/"bin/fleet-dispatch"))

import glob
from pathlib import Path
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
    FLEET_TZ = ZoneInfo(config.get('DROVER_DISPATCH_TZ','America/New_York'))  # the fleet's operating timezone (EDT/EST)
except Exception:  # no tz database: a bare or EDT/EST stamp cannot be placed and is treated as unparseable
    FLEET_TZ = None

HOME = os.path.expanduser('~')
FIX = config.get('FLEET_DISPATCH_FIXTURE')
NOW = float(config.get('FLEET_DISPATCH_NOW') or time.time())
CONFIG = os.path.join(FIX, 'config') if FIX else config['DROVER_CONFIG_DIR']
REPORTS = os.path.join(FIX, 'reports') if FIX else config['FLEET_REPORTS']
PINS = os.path.join(CONFIG, 'refill-pins.txt') if FIX else config['DROVER_PINS_FILE']
if FIX and os.path.exists(os.path.join(CONFIG, 'refill-pins')): PINS = os.path.join(CONFIG, 'refill-pins')
SKIP = os.path.join(CONFIG, 'dispatch-skip.txt') if FIX else config['DROVER_DISPATCH_SKIP']
if FIX and os.path.exists(os.path.join(CONFIG, 'dispatch-skip')): SKIP = os.path.join(CONFIG, 'dispatch-skip')
# bean-yqk7: an explicit owner record survives branch changes until its seat releases it.
OWNERS = os.path.join(CONFIG, 'assigned-owners.txt') if FIX else config['DROVER_OWNERS_FILE']
CAPF = os.path.join(CONFIG, 'fleet-cap.txt') if FIX else config.get('DROVER_DISPATCH_CAP', os.path.join(CONFIG,'dispatch-cap'))
if FIX and os.path.exists(os.path.join(CONFIG, 'dispatch-cap')): CAPF = os.path.join(CONFIG, 'dispatch-cap')
TYPES = {'bug', 'task', 'feature'}
PRIORITY = {'critical': 0, 'high': 1, 'normal': 2, 'low': 3, 'deferred': 4}
NOT_SEATS = re.compile('^(' + '|'.join(x for x in [config.get('DROVER_DISPATCH_EXCLUDE',''), '|'.join(config.get('DROVER_LEAD_SEATS','lead lead-backup').split()), '|'.join(config.get('DROVER_REFILL_DEDICATED','').split())] if x) + ')$')
_NS = NOT_SEATS
class _NotSeats:
    def match(self, name):
        path = config.get('DROVER_DEDICATED_FILE')
        names = set(Path(path).read_text().split()) if path and Path(path).is_file() else set()
        return name in names or _NS.match(name)
NOT_SEATS = _NotSeats()
FREE_STATES = {'idle', 'done'}
# An item is gated only when its OWNER is named up front (FOUNDER:/LEAD:/OPERATOR:/ROOT, '(founder ...)') rather than matching an integration name anywhere in the item.
# A substring anywhere ('approved', 'the merchant sees') hid real coding beans: bean-8s4w (critical) sat undispatched.
GATED = re.compile(r'^\s*-\s*\[ \]\s*(\*\*)?\(?\s*(founder|lead|operator|root|architect|human|merchant)\b', re.I)
ARCH_FIRST = re.compile(r'architect[- ]first|founder reviews?\b', re.I)
REASON_ORDER = ['on_working_seat_branch', 'assigned_to_owner_seat', 'skip_listed', 'waiting_until', 'awaiting_lead_or_founder', 'pinned_to_busy_seat', 'recently_blocked', 'landed_report_done', 'reported_done_awaiting_lead_close', 'needs_architect_or_founder',
                'all_open_items_gated', 'all_items_checked', 'in_progress_recent_or_unknown']


def sh(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def bean_tags(head):
    """Front-matter `tags:` list items, as a sorted list (JSON-serialisable)."""
    m = re.search(r'^tags:\n((?:[ \t]+-[ \t]*\S+\n?)+)', head, re.M)
    return sorted(re.findall(r'-[ \t]*(\S+)', m.group(1))) if m else []


def parse_bean(path):
    text = open(path).read()
    m = re.match(r'---\n(.*?)\n---\n?(.*)', text, re.S)
    head, body = (m.group(1), m.group(2)) if m else ('', text)
    meta = {}
    for line in head.splitlines():
        if ':' in line and not line.startswith(' '):
            k, v = line.split(':', 1)
            meta[k.strip()] = v.strip().strip('"\'')
    bid = re.match('(' + config.get('DROVER_BEAN_RE', r'[a-z0-9]+-[a-z0-9]{4}') + ')', os.path.basename(path))
    return {'id': bid.group(1) if bid else os.path.basename(path), 'status': meta.get('status', ''), 'type': meta.get('type', ''),
            'priority': meta.get('priority', 'normal'), 'created': meta.get('created_at', ''), 'body': body, 'title': meta.get('title', ''),
            'tags': bean_tags(head), 'updated': meta.get('updated_at', ''),
            'path': '.beans/' + os.path.basename(path)}


def load_board(repo):
    """-> (beans by id, ready ids)"""
    if FIX:
        beans = {b['id']: b for b in map(parse_bean, glob.glob(os.path.join(FIX, 'beans', '*.md')))}
        ready = [l.strip() for l in open(os.path.join(FIX, 'ready.txt')) if l.strip()]
        ready += [b['id'] for b in beans.values() if b['status'] == 'in-progress' and b['id'] not in ready]
        return beans, ready
    tmp = tempfile.mkdtemp(prefix='fleet-dispatch-')
    extract = ['bash', '-c', f'set -o pipefail; git -C "{repo}" archive origin/main .beans | tar -x -C "{tmp}"']
    rc, _, err = sh(extract)
    if rc != 0:  # origin/main can be mid-update by a concurrent fetch; one retry
        time.sleep(2); rc, _, err = sh(extract)
    if rc != 0:
        sys.exit(f'fleet-dispatch: cannot read .beans from origin/main: {err.strip()[:200]}')
    beans = {b['id']: b for b in map(parse_bean, glob.glob(os.path.join(tmp, '.beans', '*.md')))}
    rc, out, err = sh(['beans', '--beans-path', os.path.join(tmp, '.beans'), 'list', '--json', '--ready'])
    if rc != 0:
        sys.exit(f'fleet-dispatch: beans list --ready failed: {err.strip()[:200]}')
    ready = [b['id'] for b in json.loads(out)]
    # An in-progress bean whose seat stopped is orphaned: `--ready` excludes in-progress, so it was never offered again
    # (45 such beans with ~300 open items, 2026-10-14). Offer the unblocked ones; classify() still drops a bean a
    # working seat holds (on_working_seat_branch), and the report/gate rules still apply.
    rc, out, err = sh(['beans', '--beans-path', os.path.join(tmp, '.beans'), 'query', '--json',
                       '{ beans(filter:{status:["in-progress"], isBlocked:false}) { id } }'])
    if rc == 0:
        ready += [b['id'] for b in json.loads(out)['beans'] if b['id'] not in ready]
    return beans, ready


def latest_progress(repo):
    """Batch Git history, bean-file edits and branch tips; an unavailable history never revives work."""
    if FIX:
        p = os.path.join(FIX, 'progress.json')
        if not os.path.exists(p): return {}
        with open(p) as f: return json.load(f)
    progress = {}
    def remember(bid, at, sha):
        at = int(at)
        if bid not in progress or at > progress[bid]['at']:
            progress[bid] = {'at': at, 'sha': sha}
    rc, commits, _ = sh(['git', '-C', repo, 'log', '--all', '--since=45 days ago', '--format=%ct%x09%H%x09%s'])
    if rc != 0: return None
    for line in commits.splitlines():
        parts = line.split('\t', 2)
        if len(parts) == 3:
            for bid in re.findall(config.get('DROVER_BEAN_RE', r'[a-z0-9]+-[a-z0-9]{4}'), parts[2]): remember(bid, parts[0], parts[1])
    rc, edits, _ = sh(['git', '-C', repo, 'log', '--all', '--since=45 days ago', '--format=@%ct%x09%H', '--name-only', '--', '.beans'])
    if rc != 0: return None
    at, sha = None, None
    for line in edits.splitlines():
        if line.startswith('@'): at, sha = line[1:].split('\t', 1)
        else:
            match = re.search(r'(?:^|/)(' + config.get('DROVER_BEAN_RE', r'[a-z0-9]+-[a-z0-9]{4}') + ')--', line)
            if match and at is not None: remember(match.group(1), at, sha)
    rc, refs, _ = sh(['git', '-C', repo, 'for-each-ref', '--format=%(refname:short)%09%(committerdate:unix)%09%(objectname)', 'refs/heads/fleet/'])
    if rc != 0: return None
    for line in refs.splitlines():
        parts = line.split('\t')
        if len(parts) != 3: continue
        match = re.search('(' + config.get('DROVER_BEAN_RE', r'[a-z0-9]+-[a-z0-9]{4}') + ')$', parts[0])
        if match: remember(match.group(1), parts[1], parts[2])
    return progress


def revived_beans(beans, ready, seats, repo):
    working = {m.group(1) for seat in seats if seat['status'] == 'working' or seat.get('busy_reasons')
               for m in [re.search('(' + config.get('DROVER_BEAN_RE', r'[a-z0-9]+-[a-z0-9]{4}') + ')$', seat.get('branch') or '')] if m}
    candidates = [beans[bid] for bid in ready if bid in beans and beans[bid]['status'] == 'in-progress' and beans[bid]['type'] in TYPES and bid not in working]
    if not candidates: return {}
    history = latest_progress(repo)
    if history is None: return {}
    revived = {}
    for bean in candidates:
        last = history.get(bean['id'])
        if not last:
            dates = []
            for value in (bean.get('created'), bean.get('updated')):
                try: dates.append(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())
                except (AttributeError, ValueError): pass
            if not dates: continue
            last = {'at': max(dates), 'sha': None}
        if NOW - last['at'] < 4 * 3600: continue
        report = newest_report(bean['id'])
        note = (f"Revived stale in-progress bean {bean['id']}: no commit for at least 4 hours and no working seat on its branch. "
                f"Prior progress: commit {last['sha'] or 'not found in retained history'}; activity at {last['at']}; "
                f"bean {bean['path']}. " + (f"Prior report: [{report}]({report})." if report else 'No prior report found.'))
        revived[bean['id']] = {'id': bean['id'], 'last_progress': last, 'report': report, 'scope': note}
    return revived


def current_assignments():
    """seat -> its CURRENT bean: the newest record for that seat in assigned-owners.txt (later line wins a tie)."""
    return _current_assignments(read_owners())


def asking_seats(beans=None):
    """Seats with an unanswered fleet-ask question (questions/<id>.json without decisions/<id>.*).

    bean-jzex (retro rule 4: a seat owns only its current assignment): a question holds its seat only while it is about
    the seat's CURRENT assignment and that bean is still open. A question about an older bean, or about a bean that is
    completed or scrapped, is printed as stale and does not hold. Absent evidence (no bean on the question, no owner
    record for the seat, a bean the board does not know) never frees a seat.
    """
    root = os.path.join(FIX, '.local/share/drover/reports') if FIX else REPORTS
    answered = {os.path.basename(p).split('.')[0] for p in glob.glob(os.path.join(root, 'decisions', '*'))}
    current = current_assignments()
    seats = set()
    for p in sorted(glob.glob(os.path.join(root, 'questions', '*.json'))):
        try:
            q = json.load(open(p))
        except (OSError, ValueError):
            continue
        if q.get('id') in answered or os.path.basename(p)[:-5] in answered or not q.get('seat'):
            continue
        bean, seat = q.get('bean'), q['seat']
        row = (beans or {}).get(bean) if bean else None
        closed = bool(row) and row['status'] in ('completed', 'scrapped')
        superseded = bool(bean) and seat in current and current[seat] != bean
        if closed or superseded:
            print(f"stale question ignored: {seat} {bean} {q.get('id') or os.path.basename(p)[:-5]}")
            continue
        seats.add(seat)
    return seats


def load_seats():
    if FIX:
        return json.load(open(os.path.join(FIX, 'seats.json')))
    rc, out, err = sh(['herdr', 'agent', 'list'])
    if rc != 0:
        sys.exit(f'fleet-dispatch: herdr agent list failed: {err.strip()[:200]}')
    seats = []
    for a in json.loads(out)['result']['agents']:
        if not a.get('name'):
            continue
        branch = ''
        if a.get('cwd'):  # every seat's branch: a done/idle seat may still own an unfinished bean (bean-c8vv, 2026-10-15)
            branch = sh(['git', '-C', a['cwd'], 'branch', '--show-current'])[1].strip()
        seats.append({'name': a['name'], 'status': a.get('agent_status'), 'branch': branch,
                      'cwd': a.get('cwd'), 'pane_id': a.get('pane_id')})
    return seats


# bean-y5xh: UI idle is not task completion. Inspect kernel ancestry and Git,
# never pane text, CPU idleness, report mtime or an assumed command duration.
from fleet_activity import process_table, landing_state


# bean-emi6: harness infrastructure (language servers, MCP servers) lives for days under a seat shell and is not work.
# Matched per argument TOKEN, never as a substring of the command line: `jest src/mcp/__tests__/x.test.ts` is work.
INFRA_TOKEN_RE = re.compile(r'language-server|tsserver|typingsInstaller|(^|/)mcp-server|(^|/)server-[a-z]+$|^@modelcontextprotocol/|webresearch')


def is_harness_infra(args):
    tokens = args.split()
    if any(INFRA_TOKEN_RE.search(t) for t in tokens): return True
    return bool(tokens) and os.path.basename(tokens[0]) == 'example' and 'mcp' in tokens[1:2]


def live_work_child(shell_pid, table):
    if table is None or shell_pid not in table or table[shell_pid][1].startswith('Z'): return None
    descendants = set()
    pending = [shell_pid]
    while pending:
        parent = pending.pop()
        children = {pid for pid, row in table.items()
                    if row[0] == parent and pid not in descendants and pid != shell_pid}
        descendants.update(children)
        pending.extend(children)
    # Ignore the persistent harness and its launcher spine. Its tool/terminal
    # children (including the code-mode host's children) still hold the seat.
    harnesses = {'codex', 'claude', 'opencode', 'grok', 'gemini', 'cursor', 'agy'}
    idle = {shell_pid}
    for pid in descendants:
        if table[pid][2] in harnesses:
            parent = pid
            while parent in descendants and parent not in idle:
                idle.add(parent)
                parent = table[parent][0]
        if table[pid][2] == 'codex-code-mode-host': idle.add(pid)
    # Everything under an infrastructure process (a tsserver's typingsInstaller, an MCP server's node child) is infrastructure.
    for pid in descendants:
        parent = pid
        while parent in descendants:
            if len(table[parent]) > 3 and is_harness_infra(table[parent][3]):
                idle.add(pid)
                break
            parent = table[parent][0]
    return any(pid not in idle and not table[pid][1].startswith('Z') for pid in descendants)


def seat_busy_reasons(seat, table):
    reasons = []
    landing = landing_state(seat.get('cwd'), table)
    if landing: reasons.append('landing')
    elif landing is None: reasons.append('landing_unavailable')
    # Fixtures without runtime fields model a checked idle seat; supplied fields
    # exercise the SAME real process/Git adapters as a native seat.
    if seat.get('cwd'):
        try:
            r = subprocess.run(['git', '-C', seat['cwd'], 'rev-list', '--count', 'origin/main..HEAD'],
                               capture_output=True, text=True, timeout=10)
            if r.returncode or not r.stdout.strip().isdigit(): reasons.append('git_unavailable')
            elif int(r.stdout.strip()) > 0: reasons.append('unlanded_commits')
        except (OSError, subprocess.TimeoutExpired):
            reasons.append('git_unavailable')
    elif not FIX:
        reasons.append('git_unavailable')
    shell_pid = seat.get('shell_pid')
    if shell_pid is None and not FIX:
        try:
            r = subprocess.run(['herdr', 'pane', 'process-info', '--pane', seat.get('pane_id') or ''],
                               capture_output=True, text=True, timeout=10)
            shell_pid = json.loads(r.stdout)['result']['process_info']['shell_pid'] if r.returncode == 0 else None
        except (OSError, KeyError, ValueError, subprocess.TimeoutExpired):
            shell_pid = None
    if shell_pid is not None:
        try: live = live_work_child(int(shell_pid), table)
        except (ValueError, TypeError): live = None
        if live is None: reasons.append('process_unavailable')
        elif live: reasons.append('live_child')
    elif not FIX:
        reasons.append('process_unavailable')
    return reasons


def cpu_idle():
    """Percent CPU idle from `top` (founder 2026-10-15: gate on real idle, not macOS load average, which counts
    fseventsd/disk waits and kept seats idle at 63% CPU idle)."""
    if FIX:
        p = os.path.join(FIX, 'idle.txt')
        return float(open(p).read().strip()) if os.path.exists(p) else 100.0
    out = subprocess.run(['top', '-l', '2', '-n', '0', '-s', '1'], capture_output=True, text=True).stdout
    vals = re.findall(r'([\d.]+)% idle', out)
    return float(vals[-1]) if vals else 0.0


def load_load1():
    if FIX:
        p = os.path.join(FIX, 'load1.txt')
        return float(open(p).read().strip()) if os.path.exists(p) else 0.0
    return os.getloadavg()[0]


def kv(path):
    out = {}
    if os.path.exists(path):
        for l in open(path):
            if '=' in l and not l.lstrip().startswith('#'):
                k, v = l.split('=', 1)
                out[k.strip()] = v.strip()
    return out


# A skip entry parks a bean only when its comment names who wrote it, when, and why (retro 2026-10-17 rule 3).
SKIP_REASON = re.compile(r'#\s*(\S+)\s+(\d{4}-\d{2}-\d{2})\s*:\s*(\S.*)')
SKIP_MAX_AGE_H = 72


def read_skip():
    """-> (entries, ids that still skip). Every entry is kept for reporting.

    An entry skips only when it carries `# <who> <date>: <reason>` and its date is at most 72 h old. Anything else
    (no comment, a hand-written comment, an unparseable date, an expired date) is ignored AND reported — a stale or
    unexplained entry must not hide work. The date is local midnight in the host's timezone; age = NOW - that instant.
    """
    entries = []
    if not os.path.exists(SKIP):
        return entries, set()
    for raw in open(SKIP, errors='replace'):
        bean = raw.split('#')[0].strip()
        if not bean:
            continue
        m = SKIP_REASON.search(raw)
        entry = {'bean': bean, 'who': m.group(1) if m else None, 'date': m.group(2) if m else None,
                 'reason': m.group(3).strip() if m else None, 'age_h': None, 'skips': False, 'why': None}
        if not m:
            entry['why'] = 'no reason: expected "# <who> <date>: <reason>"'
        else:
            try:
                start = datetime.strptime(entry['date'], '%Y-%m-%d').timestamp()
            except ValueError:
                entry['why'] = f"unparseable date {entry['date']!r}"
            else:
                entry['age_h'] = (NOW - start) / 3600
                if entry['age_h'] > SKIP_MAX_AGE_H:
                    entry['why'] = f"expired: dated {entry['date']}, age {entry['age_h']:.0f}h > {SKIP_MAX_AGE_H}h"
                else:
                    entry['skips'] = True
        entries.append(entry)
    return entries, {e['bean'] for e in entries if e['skips']}


def print_skip(entries, dry):
    """Every ignored entry is a finding; --dry-run also lists each valid entry with its age and reason."""
    for e in entries:
        age = f"age={e['age_h']:.0f}h" if e['age_h'] is not None else 'age=unknown'
        reason = f'"{e["who"]} {e["date"]}: {e["reason"]}"' if e['reason'] else 'none'
        if e['skips']:
            if dry:
                print(f"skip entry: {e['bean']} {age} reason={reason}")
        else:
            print(f"skip entry ignored: {e['bean']} {age} reason={reason} ({e['why']})")


def read_pins():
    """-> list of (line, seat, bean, set_at)"""
    pins = []
    if not os.path.exists(PINS):
        return pins
    mtime = os.path.getmtime(PINS)
    for line in open(PINS):
        s = line.split('#')[0].strip()
        if not s:
            continue
        parts = s.split()
        if len(parts) < 2:
            continue
        at = mtime
        for p in parts[2:]:
            if p.startswith('@') and p[1:].isdigit():
                at = float(p[1:])
        pins.append((line, parts[0], parts[1], at))
    return pins


def read_owners():
    """-> list of (line, bean, seat, at). `<bean> <seat> [@<epoch>]`, the same shape the pins file uses."""
    owners = []
    if not os.path.exists(OWNERS):
        return owners
    mtime = os.path.getmtime(OWNERS)
    for line in open(OWNERS):
        parts = line.split('#')[0].split()
        if len(parts) < 2:
            continue
        at = mtime
        for p in parts[2:]:
            if p.startswith('@') and p[1:].isdigit():
                at = float(p[1:])
        owners.append((line, parts[0], parts[1], at))
    return owners


def write_owners(lines):
    with open(OWNERS, 'w') as f:
        f.writelines(lines)


def record_owner(bean, seat):
    """One owner per bean: replace any existing record for it."""
    kept = [l for l, b, _, _ in read_owners() if b != bean]
    os.makedirs(os.path.dirname(OWNERS), exist_ok=True)
    write_owners(kept + [f'{bean} {seat} @{int(NOW)}\n'])
    print(f'owner recorded: {bean} -> {seat}')


def landed_ids(repo):
    """Bean ids whose commits are on origin/main — the same `git log --grep` proof the brief asks a seat for."""
    if FIX:
        p = os.path.join(FIX, 'landed.txt')
        return set(l.strip() for l in open(p) if l.strip()) if os.path.exists(p) else set()
    rc, out, _ = sh(['git', '-C', repo, 'log', 'origin/main', '--format=%s', '--since=45 days ago'])
    if rc != 0:
        return set()
    return set(re.findall(config.get('DROVER_BEAN_RE', r'[a-z0-9]+-[a-z0-9]{4}'), out))


def report_done(bean):
    """The newest report for the bean ends with the seat's DONE line."""
    f = newest_report(bean)
    if not f:
        return False
    lines = [l for l in open(f, errors='replace').read().splitlines() if l.strip()]
    return bool(lines) and lines[-1].strip().startswith('DONE')


def newest_report(bean):
    files = [f for f in glob.glob(os.path.join(REPORTS, f'*{bean}*.md'))]
    return max(files, key=os.path.getmtime) if files else None


def _last_report_line(f):
    try:
        lines = [l.strip() for l in open(f, errors='replace').read().splitlines() if l.strip()]
    except OSError:
        return ''
    return lines[-1] if lines else ''


# bean-km76 — retro 2026-10-17 rule 5: a seat that waits on a scheduled run or data writes its report's LAST
# line as `WAITING-UNTIL <ISO or EDT time>: <what>`, and the dispatcher parks the bean (reason waiting_until)
# until that time passes. An ISO stamp with `Z` or a numeric offset is absolute; a bare ISO stamp and an
# `EDT`/`EST`/`America/New_York` stamp are read as America/New_York wall time (the fleet's operating zone).
WAITING_RE = re.compile(r'^WAITING-UNTIL\b\s*(.*)$', re.I)
WAITING_TIME = re.compile(
    r'^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)'
    r'(?:\s+(EDT|EST|America/New_York))?\s*:\s*(.*)$', re.I | re.S)


def parse_waiting_until(line):
    """-> (epoch, what, error) for a `WAITING-UNTIL <time>: <what>` line, or (None, None, None) for any other line.

    A stamp that does not parse yields `error` (and no epoch): the caller treats the line as absent and reports
    it, so a malformed wait never hides a bean and never crashes the dispatcher.
    """
    m = WAITING_RE.match(line.strip())
    if not m:
        return None, None, None
    t = WAITING_TIME.match(m.group(1).strip())
    if not t:
        return None, None, f"unparseable time in {line.strip()!r}"
    raw, what = t.group(1), (t.group(3) or '').strip()
    text = re.sub(r'^(\d{4}-\d{2}-\d{2}) ', r'\1T', raw.replace('Z', '+00:00'))
    try:
        at = datetime.fromisoformat(text)
    except ValueError:
        return None, None, f"unparseable time {raw!r}"
    if at.tzinfo is None:  # bare ISO, or an EDT/EST name: America/New_York wall time
        if FLEET_TZ is None:
            return None, None, f"no timezone database to place {raw!r}"
        at = at.replace(tzinfo=FLEET_TZ)
    return at.timestamp(), what, None


def read_waiting_until(bean):
    """-> dict for the newest report's LAST line, or None when that line is not a WAITING-UNTIL line.

    `at` is the epoch the seat said the bean waits until (None when the time is unparseable, with `why` set).
    """
    f = newest_report(bean)
    line = _last_report_line(f) if f else ''
    if not WAITING_RE.match(line.strip()):
        return None
    at, what, error = parse_waiting_until(line)
    return {'bean': bean, 'report': f, 'line': line, 'at': at, 'what': what, 'why': error}


def print_waiting(entries, dry):
    """An unparseable wait is a finding in every mode; --dry-run also shows each wait, parked or already passed."""
    for e in entries.values():
        if e['why']:
            print(f"waiting-until ignored: {e['bean']} {e['line']!r} ({e['why']})")
        elif dry:
            left = e['at'] - NOW
            state = f"parks {left:.0f}s more" if left > 0 else f"passed {-left:.0f}s ago"
            print(f"waiting-until: {e['bean']} until {e['at']:.0f} ({state}) what={e['what']!r}")


def report_done_recently(bean):
    # A seat reported DONE in the last 24 h. Seats cannot complete beans (bean-qct5), so the bean stays
    # todo until the lead closes it; re-dispatching it only produces 'duplicate dispatch' reports.
    f = newest_report(bean)
    if not f or NOW - os.path.getmtime(f) > 24 * 3600:
        return False
    lines = [l for l in open(f, errors='replace').read().splitlines() if l.strip()]
    return bool(lines) and (lines[-1].strip().startswith('DONE') or any(re.search(r'\bDONE\b', l) for l in lines[:3]))


def report_blocked_recently(bean):
    f = newest_report(bean)
    if not f or NOW - os.path.getmtime(f) > 24 * 3600:
        return False
    lines = [l for l in open(f, errors='replace').read().splitlines() if l.strip()]
    # A seat announces BLOCKED in its last line (`BLOCKED <path>`), in a heading (`# bean-x (seat) — BLOCKED …`) or as `Verdict: BLOCKED`.
    # The heading and verdict sit in the first lines, so the first three non-empty lines count too.
    return bool(lines) and (lines[-1].strip().startswith('BLOCKED') or any(re.search(r'\bBLOCKED\b', l) for l in lines[:3]))


def open_items(body):
    return [l for l in body.splitlines() if re.match(r'^\s*(?:[-*]\s*)?\[ \]', l)], len(re.findall(r'^\s*(?:[-*]\s*)?\[[xX]\]', body, re.M))


def seat_finished_bean(seat, bean):
    """True when this seat's newest report for the bean ends in a final DONE/BLOCKED line (the seat let it go)."""
    files = sorted(glob.glob(os.path.join(REPORTS, f'{seat}-{bean}*.md')), key=os.path.getmtime)
    if not files:
        return False
    lines = [l.strip() for l in open(files[-1], errors='replace').read().splitlines() if l.strip()]
    return bool(lines) and bool(re.match(r'^(DONE|BLOCKED)\b', lines[-1]))


def classify(bean, ctx):
    """First named reason this bean is not dispatchable, else None."""
    bid = bean['id']
    if bid in ctx['beans_in_work']:
        return 'on_working_seat_branch'
    if bean['status'] == 'in-progress' and bid not in ctx.get('revived', {}):
        return 'in_progress_recent_or_unknown'
    if bid in ctx['owners_by_bean']:
        return 'assigned_to_owner_seat'
    if bid in ctx['skip']:
        return 'skip_listed'
    if bid in ctx['waiting']:
        return 'waiting_until'
    # Code-complete beans whose remaining items belong to the lead or founder: re-sending them only yields another BLOCKED report
    # (25 such reports in 6 h, blocked share 0.68, 2026-10-15). The tag is removed when that owner hands seat work back.
    if {'awaiting-lead', 'awaiting-founder'} & set(bean.get('tags', [])):
        return 'awaiting_lead_or_founder'
    pin = ctx['pins_by_bean'].get(bid)
    if pin and pin not in ctx['free_names']:
        return 'pinned_to_busy_seat'
    if report_blocked_recently(bid):
        return 'recently_blocked'
    # bean-yqk7: a landed final report is finished even after the recent-DONE window.
    # Preserve the installed slice policy: an open coding item is still dispatchable.
    if (bid in ctx['landed'] and report_done(bid)
            and not any(not GATED.search(item) for item in open_items(bean['body'])[0])):
        return 'landed_report_done'
    # A DONE report closes a SLICE, not necessarily the bean: when seat-doable items remain (p5d6 S2/S3, lrxn 23, d6mz 16 on
    # 2026-10-15) the next slice is dispatchable. Only a bean with nothing but gated items left waits for the lead's close.
    if report_done_recently(bean['id']) and not any(not GATED.search(l) for l in open_items(bean['body'])[0]):
        return 'reported_done_awaiting_lead_close'
    # The bean's own author can declare it non-seat work; honour it (bean-uhrt, bean-jmov said so and were dispatched anyway).
    if re.search(r'SEAT-READY:\s*none', bean['body'], re.I):
        return 'awaiting_lead_or_founder'
    if ARCH_FIRST.search(bean['body']):
        return 'needs_architect_or_founder'
    unchecked, checked = open_items(bean['body'])
    if unchecked and all(GATED.search(l) for l in unchecked):
        return 'all_open_items_gated'
    if not unchecked and checked:
        return 'all_items_checked'
    return None


def main():
    if '--record-owner' in sys.argv:
        i = sys.argv.index('--record-owner')
        if len(sys.argv) < i + 3:
            sys.exit('fleet-dispatch --record-owner <bean> <seat>')
        record_owner(sys.argv[i + 1], sys.argv[i + 2])
        return
    dry = '--dry-run' in sys.argv
    as_json = '--json' in sys.argv
    if as_json and not dry:
        print('--json requires --dry-run; no assignment performed', file=sys.stderr)
        sys.exit(2)
    repo = config.get('FLEET_ROOT') or (sh(['git', 'rev-parse', '--show-toplevel'])[1].strip() or os.getcwd())
    beans, ready_ids = load_board(repo)
    seats = load_seats()
    table = process_table()
    for seat in seats:
        seat['busy_reasons'] = seat_busy_reasons(seat, table)
    held = {s['name'] for s in seats if s['busy_reasons']}
    revived = revived_beans(beans, ready_ids, seats, repo)
    status = {s['name']: s['status'] for s in seats}
    free_names = {n for n, st in status.items() if st in FREE_STATES and not NOT_SEATS.match(n)}
    free_names -= held
    free_names -= asking_seats(beans)  # a seat waiting on a fleet-ask answer still owns its bean
    working = {n for n, st in status.items() if st == 'working'} | held

    # pin expiry (before classification: an expired pin must not block its bean)
    kept, removed = [], []
    for line, seat, bean, at in read_pins():
        b = beans.get(bean)
        done_bean = bool(b) and b['status'] == 'completed'
        if done_bean:
            removed.append((line, seat, bean, 'bean completed on origin/main'))
        else:
            kept.append((line, seat, bean))
    if not as_json:
        for _, seat, bean, why in removed:
            print(f"pin {'would be removed' if dry else 'removed'}: {seat} {bean} ({why})")
    # owner records expire exactly like pins: the bean finished on main, or its seat stopped holding it
    kept_owners, removed_owners = [], []
    owner_rows = read_owners()
    latest_at = {}
    for _, _, seat, at in owner_rows:
        latest_at[seat] = max(latest_at.get(seat, 0), at)
    for line, bean, seat, at in owner_rows:
        # A seat owns only its CURRENT assignment; an older record is released once the seat was given another bean.
        if at < latest_at[seat]:
            removed_owners.append((line, bean, seat, 'seat moved to a newer assignment'))
            continue
        b = beans.get(bean)
        rep = newest_report(bean)
        done_bean = bool(b) and b['status'] in ('completed', 'scrapped')
        seat_finished = status.get(seat) in FREE_STATES and rep is not None and os.path.getmtime(rep) > at
        # One owner end to end (lead 2026-10-17): a slice report (DONE/PARTIAL on a multi-slice bean) is not a hand-back.
        # Only a BLOCKED report releases the bean to another seat; otherwise the owner keeps it (s28a/rgle were re-sent
        # to other seats three times in one evening while their owners were between slices).
        released = seat_finished and _last_report_line(rep).startswith('BLOCKED')
        if done_bean or released:
            removed_owners.append((line, bean, seat, 'bean completed/scrapped' if done_bean else 'owner reported BLOCKED'))
        else:
            kept_owners.append((line, bean, seat))
    if not as_json:
        for _, bean, seat, why in removed_owners:
            print(f"owner record {'would be removed' if dry else 'removed'}: {seat} {bean} ({why})")
    if removed_owners and not dry and os.path.exists(OWNERS):
        drop = {l for l, *_ in removed_owners}
        with open(OWNERS) as f:
            lines = f.readlines()
        write_owners([l for l in lines if l not in drop])

    if removed and not dry and os.path.exists(PINS):
        drop = {l for l, *_ in removed}
        with open(PINS) as f:
            lines = f.readlines()
        with open(PINS, 'w') as f:
            f.writelines(l for l in lines if l not in drop)

    ready = [beans[i] for i in ready_ids if i in beans and beans[i]['type'] in TYPES]
    ready.sort(key=lambda b: (PRIORITY.get(b['priority'], 2), b['created'], b['id']))
    skip_entries, skip_ids = read_skip()
    waiting = {}
    for b in ready:
        entry = read_waiting_until(b['id'])
        if entry:
            waiting[b['id']] = entry
    ctx = {
        'skip': skip_ids, 'free_names': free_names, 'revived': revived,
        'waiting': {bid for bid, e in waiting.items() if e['at'] is not None and e['at'] > NOW},
        'owners_by_bean': {bean for _, bean, _ in kept_owners},
        'landed': landed_ids(repo),
        'pins_by_bean': {bean: seat for _, seat, bean in kept},
        # the bean a WORKING seat is on right now: its checked-out branch fleet/<seat>-<bean>. An old branch of a seat that has since moved on
        # does not hide its bean (62 beans were hidden that way in a first version).
        # A seat still owns the bean on its checked-out branch while working/blocked, AND while idle/done but its newest
        # report for that bean has no final DONE/BLOCKED line: a seat waiting on a background push shows `done`
        # (opus-reviewer, bean-c8vv, 2026-10-15) and its bean was handed to a second seat.
        'beans_in_work': {m.group(1) for s in seats
                          for m in [re.search('(' + config.get('DROVER_BEAN_RE', r'[a-z0-9]+-[a-z0-9]{4}') + ')$', s.get('branch') or '')] if m
                          and (m.group(1) not in revived or s['busy_reasons'])
                          and (s['busy_reasons'] or s['status'] in ('working', 'blocked') or not seat_finished_bean(s.get('name') or '', m.group(1)))},
    }
    reasons, dispatchable = {}, []
    for b in ready:
        r = classify(b, ctx)
        if r is None:
            dispatchable.append(b)
        else:
            reasons[r] = reasons.get(r, 0) + 1
    n_ready, n_disp = len(ready), len(dispatchable)
    if as_json:
        if n_ready != n_disp + sum(reasons.values()):
            print('classification count invariant violated', file=sys.stderr)
            sys.exit(3)
        print(json.dumps({'board_ready': n_ready, 'dispatchable_count': n_disp,
                          'dispatchable': dispatchable, 'excluded': reasons, 'revived': list(revived.values()),
                          'skip_ignored': [e for e in skip_entries if not e['skips']],
                          'waiting_until_ignored': [e for e in waiting.values() if e['why']],
                          'busy_seats': {s['name']: s['busy_reasons'] for s in seats if s['busy_reasons']},
                          'summary': f'board_ready={n_ready} dispatchable={n_disp}'}))
        return
    for s in seats:
        if s['busy_reasons']: print(f"seat held: {s['name']} ({', '.join(s['busy_reasons'])})")
    for bid, detail in revived.items():
        print(f"{'would revive' if dry else 'revived'} {bid}: {detail['scope']}")
    print_skip(skip_entries, dry)
    print_waiting(waiting, dry)
    print(f'board_ready={n_ready} dispatchable={n_disp}')
    for r in REASON_ORDER:
        if reasons.get(r):
            print(f'  excluded {r}: {reasons[r]}')
    if n_ready != n_disp + sum(reasons.values()):
        print(f'INVARIANT VIOLATED: board_ready={n_ready} != dispatchable={n_disp} + reasons={sum(reasons.values())}')
        sys.exit(3)
    if n_disp == 0 and n_ready > 0:
        top = sorted(reasons.items(), key=lambda kv: -kv[1])[:3]
        print('dispatchable=0 while board_ready>0; top reasons: ' + ', '.join(f'{k}={v}' for k, v in top))

    cap = kv(CAPF)
    cap_working = int(cap.get('cap_working', '8'))
    load_below = float(cap.get('resume_load_below', '20'))
    load1 = load_load1()
    idle_min = float(cap.get('resume_cpu_idle_above', '25'))
    idle = cpu_idle()
    floor = int(cap.get('min_working', '5'))
    # Floor: background daemons (fseventsd ~2 cores) keep idle low even with few seats; founder: 6-8 is affordable.
    room = max(0, cap_working - len(working)) if idle > idle_min else max(0, floor - len(working))
    free = sorted(free_names)
    print(f'free_seats={len(free)} working={len(working)} cap_working={cap_working} cpu_idle={idle:.0f}% (dispatch above {idle_min:g}%, floor {floor}) load1={load1:.1f} room={room}')
    plan, used = [], set()
    pinned_first = [(seat, bean) for _, seat, bean in kept if seat in free_names and bean in {b['id'] for b in dispatchable}]
    for seat, bean in pinned_first:
        if len(plan) < room:
            plan.append((bean, seat)); used.add(bean)
    pinned_beans = {bean for _, _, bean in kept}
    spare = [s for s in free if s not in {p[1] for p in plan} and not any(seat == s for _, seat, _ in kept)]
    pool = [b for b in dispatchable if b['id'] not in used and b['id'] not in pinned_beans]
    # One owner per bean: a bean goes back to the seat that wrote its newest report when that seat is free
    # (6hgs and lrxn hopped between seats on 2026-10-15, each new seat re-reading what the last one knew).
    for b in list(pool):
        rep = newest_report(b['id'])
        prev = next((s for s in spare if rep and os.path.basename(rep).startswith(s + '-' + config.get('DROVER_BEAN_PREFIX','bean-'))), None)
        if prev and len(plan) < room:
            plan.append((b['id'], prev)); spare.remove(prev); pool.remove(b)
    for seat in spare:
        if len(plan) >= room or not pool:
            break
        plan.append((pool.pop(0)['id'], seat))
    if room == 0 and n_disp:
        print('no assignment: cap or load limit reached; dispatchable beans stay on the board')
    failed = 0
    for bean, seat in plan:
        # Close the planning window: a child or pending commit may have appeared
        # while board/history/capacity were being read. No branch switch on doubt.
        current = next(s for s in seats if s['name'] == seat)
        reasons_now = seat_busy_reasons(current, process_table())
        if reasons_now:
            print(f"assignment refused: {bean} -> {seat} ({', '.join(reasons_now)})")
            continue
        if dry:
            print(f'would assign {bean} -> {seat}')
            continue
        command = [os.path.join(config['DROVER_BIN'], 'fleet'), 'assign', bean, seat]
        if bean in revived: command.append(revived[bean]['scope'])
        if FIX:
            with open(os.path.join(FIX, 'assign-commands.jsonl'), 'a') as f: f.write(json.dumps(command) + '\n')
            open(os.path.join(FIX, 'assign.log'), 'a').write(f'{bean} {seat}\n')
            record_owner(bean, seat)
            print(f'assigned {bean} -> {seat}')
            continue
        rc, out, err = sh(command)
        if rc == 0:
            print(f'assigned {bean} -> {seat}')
        else:
            failed += 1
            print(f'ASSIGN FAILED {bean} -> {seat}: exit {rc} {(err or out).strip()[:200]}')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
