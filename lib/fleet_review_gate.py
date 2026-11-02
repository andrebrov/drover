"""Landing review gate: a source-changing landing needs a cross-harness APPROVE receipt for what it lands.

A receipt is a review report `<reviewer>-review-<bean>-by-<coder>.md` in $FLEET_REPORTS whose newest verdict is
APPROVE (or APPROVE-WITH-FOLLOWUP) and which names the reviewed commit (`REVIEWED: <sha>` is the explicit form). It
covers the landing head when the head IS that commit, when every commit after it touches only bean files, or when the
head's diff against its base is byte-identical to the reviewed commit's diff against its own base (a pure rebase onto
a moved main; `--binary --full-index`, only hunk line numbers normalised, so a whitespace, binary or path change does
not match). A landing that touches only bean files needs no receipt.

Refused: no receipt; a newer CHANGES from any reviewer that covers the head; a reviewer on the coder's harness; a
reviewer who is the author; a reviewer name that is not a roster seat (for example `lead`); a receipt for a different
commit. Every accepted receipt appends `path sha256 mtime gate-time` to $FLEET_STATE/review-audit.log, and a landing
whose audit line cannot be written is refused (fail closed).

  fleet_review_gate.py check [--head REF] [--base REF] [--author SEAT] <repo-root> <bean>
exit 0 pass, 4 refused, 2 usage.
"""
import datetime
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fleet_review_reports import parse_report  # noqa: E402

BEAN_DIR = '.beans/'
NON_SEATS = {'lead', 'admin', 'operator', 'founder', 'human'}
RECEIPT = re.compile(r'^([a-z0-9][a-z0-9-]*)-review-(.+)-by-([a-z0-9][a-z0-9-]*)\.md$')


def _config():
    try:
        from fleet_config import configure
        return configure()
    except Exception:   # a gate must still run (and refuse) when drover-env cannot be sourced
        return {}


def get_roster():
    return os.environ.get('DROVER_ROSTER') or _config().get('DROVER_ROSTER', '')


def roster_map(roster=None):
    out = {}
    for pair in (roster if roster is not None else get_roster()).split():
        seat, _, kind = pair.partition(':')
        out[seat] = kind or seat.split('-', 1)[0]
    return out


def git(root, *args, binary=False):
    try:
        p = subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=False)
    except OSError as err:
        return 1, b'' if binary else '', str(err)
    out = p.stdout if binary else p.stdout.decode('utf-8', 'replace').strip()
    return p.returncode, out, p.stderr.decode('utf-8', 'replace').strip()


def author_seat(root, seats, head='HEAD', author=None):
    """The landing's author: an explicit --author, else the checked-out fleet branch, else the fleet-<seat>
    worktree directory, else FLEET_SEAT. The branch wins over the environment, so a process variable cannot
    make another seat's work look like its own."""
    if author:
        return author
    bean_re = os.environ.get('DROVER_BEAN_RE') or _config().get('DROVER_BEAN_RE', r'[a-z0-9]+-[a-z0-9]+')
    names = [head] if head != 'HEAD' else []
    rc, branch, _ = git(root, 'branch', '--show-current')
    if rc == 0 and branch:
        names.append(branch)
    for name in names:
        m = re.fullmatch(r'(?:refs/heads/)?fleet/(.+?)-(' + bean_re + ')', name) or \
            re.fullmatch(r'(?:refs/heads/)?fleet/([a-z0-9][a-z0-9-]*)', name)
        if m and m.group(1) in seats:
            return m.group(1)
    for candidate in [Path(root).resolve(), *Path(root).resolve().parents]:
        if candidate.name.startswith('fleet-') and candidate.name[6:] in seats:
            return candidate.name[6:]
    val = (os.environ.get('FLEET_SEAT') or '').strip()
    return val if val in seats else None


def base_of(root, commit, base_ref):
    rc, out, _ = git(root, 'merge-base', base_ref, commit)
    return out if rc == 0 and out else None


def normalized_diff(root, base, commit):
    """The non-bean diff as bytes, with only hunk line numbers removed. Paths, blob ids, modes and binary payloads
    stay byte-for-byte, so a rebase matches and any content change does not."""
    rc, out, _ = git(root, 'diff', '--binary', '--full-index', base, commit, '--', '.', ':!' + BEAN_DIR, binary=True)
    if rc != 0 or not out.strip():
        return None
    return re.sub(rb'^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@', b'@@', out, flags=re.M)


def covers(root, head_sha, reviewed, base_ref):
    """(matched, detail): does a receipt for `reviewed` cover `head_sha`?"""
    if not reviewed or len(reviewed) < 7:
        return False, 'receipt names no reviewed commit SHA'
    rc, full, _ = git(root, 'rev-parse', '--verify', '--quiet', reviewed + '^{commit}')
    if rc != 0:
        return False, f'reviewed commit {reviewed[:10]} is not in this repository'
    if full == head_sha:
        return True, f'exact match at {full[:10]}'
    if git(root, 'merge-base', '--is-ancestor', full, head_sha)[0] == 0:
        _, names, _ = git(root, 'diff', '--name-only', f'{full}..{head_sha}')
        rest = [n for n in names.splitlines() if n and not n.startswith(BEAN_DIR)]
        if not rest:
            return True, f'reviewed commit {full[:10]} is an ancestor; follow-up commits bean-only'
        return False, f'unreviewed changes between reviewed commit {full[:10]} and the landing head ({", ".join(rest[:3])})'
    rb, hb = base_of(root, full, base_ref), base_of(root, head_sha, base_ref)
    if rb and hb:
        a, b = normalized_diff(root, rb, full), normalized_diff(root, hb, head_sha)
        if a is not None and a == b:
            return True, f'reviewed commit {full[:10]} is patch-identical after rebase'
    return False, f'reviewed commit {full[:10]}, but the landing head {head_sha[:10]} is not a descendant or patch-identical'


def audit(receipt):
    state = os.environ.get('FLEET_STATE') or _config().get('FLEET_STATE') or ''
    log = Path(os.environ.get('FLEET_REVIEW_AUDIT_LOG') or Path(state) / 'review-audit.log')
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        p = Path(receipt).resolve()
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime, datetime.timezone.utc).isoformat()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with log.open('a', encoding='utf-8') as f:
            f.write(f'{p}\t{digest}\t{mtime}\t{now}\n')
        return None
    except OSError as err:
        return str(err)


def check_review_gate(root, bean, reports_dir=None, fleet_roster=None, head='HEAD', base_ref='origin/main', author=None):
    """(ok, message, receipt_path|None)."""
    rc, head_sha, _ = git(root, 'rev-parse', '--verify', '--quiet', head + '^{commit}')
    if rc != 0 or not head_sha:
        return False, f'cannot resolve the landing head {head}', None
    rc, names, _ = git(root, 'diff', '--name-only', f'{base_ref}...{head_sha}')
    if rc != 0:
        return False, f'cannot diff {base_ref}...{head}', None
    if not [n for n in names.splitlines() if n and not n.startswith(BEAN_DIR)]:
        return True, 'bean-file-only landing; no review receipt required', None

    reports = Path(reports_dir or os.environ.get('FLEET_REPORTS') or _config().get('FLEET_REPORTS', ''))
    seats = roster_map(fleet_roster)
    me = author_seat(root, seats, head, author)
    try:
        entries = sorted((p for p in reports.iterdir() if p.name.endswith('.md')), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return False, f'no review receipt found for {bean} in {reports}', None

    newest, refusals = {}, []
    for p in entries:
        m = RECEIPT.match(p.name)
        if not m or m.group(2) != bean:
            continue
        reviewer, coder = m.group(1), m.group(3)
        if reviewer.lower() in NON_SEATS or reviewer not in seats:
            refusals.append(f"review receipt {p.name}: reviewer '{reviewer}' is not a roster seat (non-seat names such as 'lead' are refused)")
        elif reviewer == coder or reviewer == me:
            refusals.append(f"review receipt {p.name}: reviewer seat '{reviewer}' equals author; self-review is refused")
        elif me and coder != me:
            continue
        else:
            newest.setdefault(reviewer, (p, coder))   # entries are newest first: keep each reviewer's newest

    changes, approvals, other = [], [], []
    for reviewer, (p, coder) in newest.items():
        rep = parse_report(str(p))
        verdict = rep.get('verdict') or ''
        matched, detail = covers(root, head_sha, rep.get('commit'), base_ref)
        if verdict == 'CHANGES':
            if matched:
                changes.append(f'review receipt {p.name} verdict is CHANGES for landing content ({detail})')
        elif verdict.startswith('APPROVE'):
            if seats.get(reviewer) == seats.get(coder):
                other.append(f'review receipt {p.name} from the same harness ({reviewer} and {coder} both on {seats.get(coder)})')
            elif matched:
                approvals.append((p, f'review gate passed: {p.name} (verdict {verdict} by {reviewer} for {coder}; {detail})'))
            else:
                other.append(f'review receipt {p.name}: {detail}')
    if changes:
        return False, changes[0], None
    if approvals:
        p, msg = approvals[0]
        err = audit(p)
        return (False, f'audit logging failed: {err}', None) if err else (True, msg, str(p))
    for msg in other + refusals:
        return False, msg, None
    return False, f'no review receipt found for {bean} in {reports}', None


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0 if argv else 2
    if argv[0] != 'check':
        print(__doc__, file=sys.stderr)
        return 2
    opts, pos, it = {}, [], iter(argv[1:])
    for a in it:
        if a in ('--head', '--base', '--author'):
            opts[a[2:]] = next(it, '')
        else:
            pos.append(a)
    if len(pos) != 2:
        print('usage: fleet_review_gate.py check [--head REF] [--base REF] [--author SEAT] <repo-root> <bean>', file=sys.stderr)
        return 2
    ok, msg, _ = check_review_gate(pos[0], pos[1], head=opts.get('head') or 'HEAD',
                                   base_ref=opts.get('base') or 'origin/main', author=opts.get('author') or None)
    print(f'[review-gate] {msg}' if ok else f'[review-gate] refused: {msg}', file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 4


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
