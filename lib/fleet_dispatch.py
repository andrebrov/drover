#!/usr/bin/env python3
"""fleet-dispatch — the one dispatch tool; it cannot hide ready work.

Measured incident: for six hours the lead said "no ready work" while `beans list --ready` held 150 coder beans: an ad-hoc filter dropped
every bean with no checklist, a hand-kept "recent" list went stale, and four seats sat on stale pins. This tool replaces that filter.

  fleet-dispatch [--dry-run]      print the board, assign dispatchable beans to free seats via `fleet assign <bean> <seat>`

ALWAYS prints `board_ready=N dispatchable=M` and a reason histogram, and checks board_ready == dispatchable + sum(reasons). It never prints
"no work": when dispatchable is 0 while board_ready is above 0 it prints the top reasons instead.

Ready = main-branch beans, `beans list --json --ready`, type bug|task|feature, priority critical > high > normal > low > deferred. A bean is excluded
only for a NAMED reason, the first that applies, in this order:
  on_working_seat_branch   a working seat has fleet/<seat>-<bean> checked out right now
  skip_listed              in $DROVER_DISPATCH_SKIP (default ~/.config/drover/dispatch-skip)
  pinned_to_busy_seat      pinned ($DROVER_PINS_FILE, lines `seat bean`) to a seat that is not free
  recently_blocked         the newest report for the bean, written in the last 24 h, ends with a BLOCKED line or has BLOCKED in its first 3 lines (heading, Verdict)
  needs_architect_or_founder   the bean text says architect-first / founder reviews
  all_open_items_gated     a checklist exists, items are open, and every open item matches $DROVER_DISPATCH_GATED (default founder|approv|architect|merchant|operator|human)
  all_items_checked        a checklist exists and nothing is open
A bean with NO checklist is dispatchable. "Already tried" comes only from reports and beans: there is no hand-kept list.

Pin expiry: a pin is removed (logged) when its bean is completed/scrapped on the main branch, or its seat is idle/done and a report for that bean is newer
than the pin (the optional `@<epoch>` on the line, else the pins file's mtime).
Free seats: idle/done and not in DROVER_LEAD_SEATS, DROVER_REFILL_DEDICATED or DROVER_DISPATCH_EXCLUDE (a regex for seats that work only one thing).
A free seat with a pin gets its pinned bean first. Cap: working seats < cap_working and load1 < resume_load_below ($DROVER_DISPATCH_CAP, default ~/.config/drover/dispatch-cap, `key=value` lines).

Test hook: FLEET_DISPATCH_FIXTURE=<dir> with beans/*.md, ready.txt, seats.json ({name,status,branch}), reports/, config/, load1.txt, assign.log.
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HOME = os.path.expanduser('~')
E = os.environ.get
FIX = os.environ.get('FLEET_DISPATCH_FIXTURE')
NOW = float(os.environ.get('FLEET_DISPATCH_NOW') or time.time())
CONFIG = os.path.join(FIX, 'config') if FIX else f'{HOME}/.config/drover'
REPORTS = os.path.join(FIX, 'reports') if FIX else (E('FLEET_REPORTS') or f'{HOME}/.local/share/drover/reports')
PINS = os.path.join(CONFIG, 'refill-pins') if FIX else (E('DROVER_PINS_FILE') or os.path.join(CONFIG, 'refill-pins'))
SKIP = os.path.join(CONFIG, 'dispatch-skip') if FIX else (E('DROVER_DISPATCH_SKIP') or os.path.join(CONFIG, 'dispatch-skip'))
CAPF = os.path.join(CONFIG, 'dispatch-cap') if FIX else (E('DROVER_DISPATCH_CAP') or os.path.join(CONFIG, 'dispatch-cap'))
MAIN_REF = E('DROVER_MAIN_REF') or 'origin/main'
BEANS_DIR = (E('DROVER_BEANS_DIR') or '.beans').strip('/')
BEAN_RE = E('DROVER_BEAN_RE') or r'[a-z0-9]+-[a-z0-9]{4}'
FLEET_BIN = E('DROVER_BIN') or os.path.dirname(os.path.abspath(__file__))
TYPES = {'bug', 'task', 'feature'}
PRIORITY = {'critical': 0, 'high': 1, 'normal': 2, 'low': 3, 'deferred': 4}
NOT_SEATS = re.compile('^(' + '|'.join(x for x in [E('DROVER_DISPATCH_EXCLUDE') or '', '|'.join((E('DROVER_LEAD_SEATS') or 'lead lead-backup').split()), '|'.join((E('DROVER_REFILL_DEDICATED') or '').split())] if x) + ')$')
FREE_STATES = {'idle', 'done'}
GATED = re.compile(E('DROVER_DISPATCH_GATED') or r'founder|approv|architect|merchant|operator|human', re.I)   # open items only a person can do
ARCH_FIRST = re.compile(r'architect[- ]first|founder reviews?\b', re.I)
REASON_ORDER = ['on_working_seat_branch', 'skip_listed', 'pinned_to_busy_seat', 'recently_blocked', 'needs_architect_or_founder',
                'all_open_items_gated', 'all_items_checked']


def sh(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def parse_bean(path):
    text = open(path).read()
    m = re.match(r'---\n(.*?)\n---\n?(.*)', text, re.S)
    head, body = (m.group(1), m.group(2)) if m else ('', text)
    meta = {}
    for line in head.splitlines():
        if ':' in line and not line.startswith(' '):
            k, v = line.split(':', 1)
            meta[k.strip()] = v.strip().strip('"\'')
    bid = re.match('(' + BEAN_RE + ')', os.path.basename(path))
    return {'id': bid.group(1) if bid else os.path.basename(path), 'status': meta.get('status', ''), 'type': meta.get('type', ''),
            'priority': meta.get('priority', 'normal'), 'created': meta.get('created_at', ''), 'body': body, 'title': meta.get('title', '')}


def load_board(repo):
    """-> (beans by id, ready ids)"""
    if FIX:
        beans = {b['id']: b for b in map(parse_bean, glob.glob(os.path.join(FIX, 'beans', '*.md')))}
        ready = [l.strip() for l in open(os.path.join(FIX, 'ready.txt')) if l.strip()]
        return beans, ready
    tmp = tempfile.mkdtemp(prefix='fleet-dispatch-')
    rc, _, err = sh(['bash', '-c', f'git -C "{repo}" archive {MAIN_REF} {BEANS_DIR} | tar -x -C "{tmp}"'])
    if rc != 0:
        sys.exit(f'fleet-dispatch: cannot read the beans from {MAIN_REF}: {err.strip()[:200]}')
    beans = {b['id']: b for b in map(parse_bean, glob.glob(os.path.join(tmp, BEANS_DIR, '*.md')))}
    rc, out, err = sh(['beans', '--beans-path', os.path.join(tmp, BEANS_DIR), 'list', '--json', '--ready'])
    if rc != 0:
        sys.exit(f'fleet-dispatch: beans list --ready failed: {err.strip()[:200]}')
    return beans, [b['id'] for b in json.loads(out)]


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
        if a.get('agent_status') == 'working' and a.get('cwd'):
            branch = sh(['git', '-C', a['cwd'], 'branch', '--show-current'])[1].strip()
        seats.append({'name': a['name'], 'status': a.get('agent_status'), 'branch': branch})
    return seats


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


def read_list(path):
    if not os.path.exists(path):
        return []
    return [l.split('#')[0].strip() for l in open(path) if l.split('#')[0].strip()]


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


def newest_report(bean):
    files = [f for f in glob.glob(os.path.join(REPORTS, f'*{bean}*.md'))]
    return max(files, key=os.path.getmtime) if files else None


def report_blocked_recently(bean):
    f = newest_report(bean)
    if not f or NOW - os.path.getmtime(f) > 24 * 3600:
        return False
    lines = [l for l in open(f, errors='replace').read().splitlines() if l.strip()]
    # A seat announces BLOCKED in its last line (`BLOCKED <path>`), in a heading (`# <bean> (<seat>) — BLOCKED …`) or as `Verdict: BLOCKED`.
    # The heading and verdict sit in the first lines, so the first three non-empty lines count too.
    return bool(lines) and (lines[-1].strip().startswith('BLOCKED') or any(re.search(r'\bBLOCKED\b', l) for l in lines[:3]))


def open_items(body):
    return [l for l in body.splitlines() if re.match(r'^\s*(?:[-*]\s*)?\[ \]', l)], len(re.findall(r'^\s*(?:[-*]\s*)?\[[xX]\]', body, re.M))


def classify(bean, ctx):
    """First named reason this bean is not dispatchable, else None."""
    bid = bean['id']
    if bid in ctx['beans_in_work']:
        return 'on_working_seat_branch'
    if bid in ctx['skip']:
        return 'skip_listed'
    pin = ctx['pins_by_bean'].get(bid)
    if pin and pin not in ctx['free_names']:
        return 'pinned_to_busy_seat'
    if report_blocked_recently(bid):
        return 'recently_blocked'
    if ARCH_FIRST.search(bean['body']):
        return 'needs_architect_or_founder'
    unchecked, checked = open_items(bean['body'])
    if unchecked and all(GATED.search(l) for l in unchecked):
        return 'all_open_items_gated'
    if not unchecked and checked:
        return 'all_items_checked'
    return None


def main():
    dry = '--dry-run' in sys.argv
    repo = os.environ.get('FLEET_ROOT') or (sh(['git', 'rev-parse', '--show-toplevel'])[1].strip() or os.getcwd())
    beans, ready_ids = load_board(repo)
    seats = load_seats()
    status = {s['name']: s['status'] for s in seats}
    free_names = {n for n, st in status.items() if st in FREE_STATES and not NOT_SEATS.match(n)}
    working = {n for n, st in status.items() if st == 'working'}

    # pin expiry (before classification: an expired pin must not block its bean)
    kept, removed = [], []
    for line, seat, bean, at in read_pins():
        b = beans.get(bean)
        rep = newest_report(bean)
        done_bean = bool(b) and b['status'] in ('completed', 'scrapped')
        seat_finished = status.get(seat) in FREE_STATES and rep is not None and os.path.getmtime(rep) > at
        if done_bean or seat_finished:
            removed.append((line, seat, bean, 'bean completed/scrapped' if done_bean else 'seat idle and a newer report exists'))
        else:
            kept.append((line, seat, bean))
    for _, seat, bean, why in removed:
        print(f"pin {'would be removed' if dry else 'removed'}: {seat} {bean} ({why})")
    if removed and not dry and os.path.exists(PINS):
        drop = {l for l, *_ in removed}
        with open(PINS) as f:
            lines = f.readlines()
        with open(PINS, 'w') as f:
            f.writelines(l for l in lines if l not in drop)

    ready = [beans[i] for i in ready_ids if i in beans and beans[i]['type'] in TYPES]
    ready.sort(key=lambda b: (PRIORITY.get(b['priority'], 2), b['created'], b['id']))
    ctx = {
        'skip': set(read_list(SKIP)), 'free_names': free_names,
        'pins_by_bean': {bean: seat for _, seat, bean in kept},
        # the bean a WORKING seat is on right now: its checked-out branch fleet/<seat>-<bean>. An old branch of a seat that has since moved on
        # does not hide its bean (62 beans were hidden that way in a first version).
        'beans_in_work': {m.group(1) for s in seats if s['status'] == 'working'
                          for m in [re.search('(' + BEAN_RE + ')$', s.get('branch') or '')] if m},
    }
    reasons, dispatchable = {}, []
    for b in ready:
        r = classify(b, ctx)
        if r is None:
            dispatchable.append(b)
        else:
            reasons[r] = reasons.get(r, 0) + 1
    n_ready, n_disp = len(ready), len(dispatchable)
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
    room = max(0, cap_working - len(working)) if load1 < load_below else 0
    free = sorted(free_names)
    print(f'free_seats={len(free)} working={len(working)} cap_working={cap_working} load1={load1:.1f} (resume below {load_below:g}) room={room}')
    plan, used = [], set()
    pinned_first = [(seat, bean) for _, seat, bean in kept if seat in free_names and bean in {b['id'] for b in dispatchable}]
    for seat, bean in pinned_first:
        if len(plan) < room:
            plan.append((bean, seat)); used.add(bean)
    pinned_beans = {bean for _, _, bean in kept}
    spare = [s for s in free if s not in {p[1] for p in plan} and not any(seat == s for _, seat, _ in kept)]
    pool = [b for b in dispatchable if b['id'] not in used and b['id'] not in pinned_beans]
    for seat in spare:
        if len(plan) >= room or not pool:
            break
        plan.append((pool.pop(0)['id'], seat))
    if room == 0 and n_disp:
        print('no assignment: cap or load limit reached; dispatchable beans stay on the board')
    failed = 0
    for bean, seat in plan:
        if dry:
            print(f'would assign {bean} -> {seat}')
            continue
        if FIX:
            open(os.path.join(FIX, 'assign.log'), 'a').write(f'{bean} {seat}\n')
            print(f'assigned {bean} -> {seat}')
            continue
        rc, out, err = sh([os.path.join(FLEET_BIN, 'fleet'), 'assign', bean, seat])
        if rc == 0:
            print(f'assigned {bean} -> {seat}')
        else:
            failed += 1
            print(f'ASSIGN FAILED {bean} -> {seat}: exit {rc} {(err or out).strip()[:200]}')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
