"""Read-only current assignment projections shared by dispatch and ownership audit.

Rows are native assigned-owners.txt records: (line, bean, seat, epoch).
File-edit claims, question age and historical event conservation are unrelated.
"""
import hashlib
import json
from pathlib import Path
import re
import os
import subprocess

BEAN = re.compile(os.environ.get('DROVER_BEAN_RE', r'bean-[a-z0-9]+'))
CLOSED = {'completed', 'scrapped'}


def current_assignments(rows):
    """Existing fleet-dispatch selection: newest row per seat, later row wins ties."""
    current = {}
    for _, bean, seat, at in rows:
        if seat not in current or at >= current[seat][1]:
            current[seat] = (bean, at)
    return {seat: bean for seat, (bean, _) in current.items()}


def latest_owner_rows(rows):
    """Latest owner per bean; do not mistake older records for current owners."""
    latest = {}
    for row in rows:
        _, bean, _, at = row
        if bean not in latest or at >= latest[bean][3]:
            latest[bean] = row
    return latest


def issue(identity, detail, **evidence):
    return {'identity': identity, 'detail': detail, **evidence}


def read_owners(path):
    path = Path(path)
    rows, errors = [], []
    try:
        data = path.read_bytes()
        at = path.stat().st_mtime
        for number, line in enumerate(data.decode().splitlines(True), 1):
            fields = line.split('#', 1)[0].split()
            if not fields:
                continue
            if (len(fields) not in (2, 3) or not BEAN.fullmatch(fields[0])
                    or not re.fullmatch(r'[A-Za-z0-9_.-]+', fields[1])
                    or (len(fields) == 3 and not re.fullmatch(r'@\d+', fields[2]))):
                errors.append(issue('OWNERS_MALFORMED', 'invalid native owner record', line=number))
                continue
            epoch = float(fields[2][1:]) if len(fields) == 3 else at
            rows.append((line, fields[0], fields[1], epoch))
        return rows, errors, hashlib.sha256(data).hexdigest()
    except (OSError, UnicodeError):
        return [], [issue('OWNERS_UNAVAILABLE', 'cannot read native owner source')], None


def read_tasks(directory):
    tasks, errors, hashes = {}, [], {}
    try:
        files = sorted(Path(directory).iterdir())
    except OSError:
        return {}, [issue('TASKS_UNAVAILABLE', 'cannot read native task directory')], {}
    for path in files:
        if path.name.startswith('.') or not path.is_file():
            continue
        try:
            data = path.read_bytes()
            hashes[path.name] = hashlib.sha256(data).hexdigest()
            fields = data.decode().rstrip('\n').split('\t')
            if len(fields) != 4 or not fields[0].isdigit() or not BEAN.fullmatch(fields[1]):
                raise ValueError()
            tasks[path.name] = fields[1]
        except (OSError, UnicodeError, ValueError):
            errors.append(issue('TASK_MALFORMED', 'cannot read native four-column task', seat=path.name))
    return tasks, errors, hashes


def main_beans(repo, beans):
    """Read the pinned origin/main tree, never the seat's dirty .beans directory."""
    errors, statuses = [], {}
    def git(*args):
        return subprocess.check_output(['/usr/bin/git', '-C', str(repo), *args], stderr=subprocess.DEVNULL, timeout=15).decode()
    try:
        sha = git('rev-parse', '--verify', 'origin/main^{commit}').strip()
        paths = git('ls-tree', '-r', '--name-only', sha, '--', '.beans').splitlines()
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return {}, [issue('MAIN_UNAVAILABLE', 'cannot read the fetched origin/main bean tree')], None
    for bean in sorted(beans):
        matches = [p for p in paths if p.startswith(f'.beans/{bean}--') and p.endswith('.md')]
        if len(matches) != 1:
            errors.append(issue('BEAN_UNAVAILABLE', 'expected one bean file on origin/main', bean=bean))
            continue
        try:
            text = git('show', f'{sha}:{matches[0]}')
            header = text.split('---', 2)[1]
            found = re.findall(r'^status:\s*([^\n]+)', header, re.M)
            if len(found) != 1:
                raise ValueError()
            status = found[0].strip().strip("'\"")
            if status not in {'todo', 'in-progress', 'draft', 'completed', 'scrapped'}:
                raise ValueError()
            statuses[bean] = status
        except (ValueError, IndexError, OSError, subprocess.SubprocessError, UnicodeError):
            errors.append(issue('BEAN_UNAVAILABLE', 'cannot read bean status on origin/main', bean=bean))
    return statuses, errors, sha


def roster_seats(data):
    agents = data.get('result', data).get('agents')
    if not isinstance(agents, list):
        raise ValueError('roster has no agents list')
    roster = {}
    for row in agents:
        if not isinstance(row, dict):
            raise ValueError('invalid roster row')
        if not row.get('name'):
            continue  # Native roster also contains unnamed panes, not fleet seats.
        if not isinstance(row['name'], str):
            raise ValueError('invalid roster name')
        if row['name'] in roster:
            raise ValueError('duplicate roster seat')
        roster[row['name']] = row
    return roster


def pending_matches(directory, seat, bean):
    try:
        fields = (Path(directory) / f'{seat}.pending').read_text().strip().split('\t')
        return len(fields) == 3 and fields[1] == bean and fields[2] in {'DONE', 'PARTIAL', 'BLOCKED', 'CHANGES', 'READY'}
    except (OSError, UnicodeError):
        return False


def audit(rows, tasks, statuses, roster, pending_directory, input_errors=()):
    """A disagreement is UNKNOWN, not permission to pick a source or repair state."""
    selected = current_assignments(rows)
    latest = latest_owner_rows(rows)
    owners = {bean: row[2] for bean, row in latest.items() if selected.get(row[2]) == bean}
    findings = []
    # Duplicate labels FIRST. Both native task snapshots claim the live bean;
    # the single latest-owner row cannot corroborate both, so certainty is unknown.
    candidates = {}
    for seat, bean in tasks.items():
        if statuses.get(bean) not in CLOSED and bean in statuses:
            candidates.setdefault(bean, set()).add(seat)
    for bean, seat in owners.items():
        if statuses.get(bean) not in CLOSED and bean in statuses:
            candidates.setdefault(bean, set()).add(seat)
    for bean, seats in sorted(candidates.items()):
        if len(seats) > 1:
            findings.append(issue('DUPLICATE_CURRENT_SEATS', 'competing current sources; ownership is unknown', bean=bean, seats=sorted(seats)))
    findings.extend(input_errors)
    for seat, bean in sorted(selected.items()):
        # A latest-per-bean row can supersede a seat's former owner record.
        if owners.get(bean) != seat:
            if tasks.get(seat) == bean and statuses.get(bean) not in CLOSED:
                findings.append(issue('OWNER_TASK_DISAGREEMENT', 'latest bean owner differs from this task snapshot', seat=seat, owner_bean=None, task_bean=bean))
            continue
        task = tasks.get(seat)
        if task is None:
            findings.append(issue('TASK_UNAVAILABLE', 'current owner has no native task snapshot', seat=seat, bean=bean))
        elif task != bean:
            findings.append(issue('OWNER_TASK_DISAGREEMENT', 'owner and native task name different beans', seat=seat, owner_bean=bean, task_bean=task))
        elif statuses.get(bean) in CLOSED:
            findings.append(issue('CLOSED_BEAN_OWNED', 'matching current sources still own a closed bean', seat=seat, bean=bean))
        if not (roster.get(seat, {}).get('pane_id') or pending_matches(pending_directory, seat, bean)):
            findings.append(issue('SEAT_UNAVAILABLE', 'owner has neither a roster pane nor a matching pending marker', seat=seat, bean=bean))
    for seat, bean in sorted(tasks.items()):
        if statuses.get(bean) in CLOSED:
            continue  # A completed native snapshot alone is not a live owner claim.
        if seat not in selected:
            findings.append(issue('OWNER_UNAVAILABLE', 'native task has no current owner evidence', seat=seat, bean=bean))
    # A conflicting or unavailable source always preserves UNKNOWN, even when
    # another finding could otherwise be a closed-owner violation.
    status = 'unknown' if any(f['identity'] != 'CLOSED_BEAN_OWNED' for f in findings) else ('fail' if findings else 'ok')
    return {'status': status, 'findings': findings, 'current_assignments': selected,
            'latest_owners': {bean: row[2] for bean, row in latest.items()}, 'tasks': tasks}
