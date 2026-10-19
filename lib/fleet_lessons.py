"""Lesson ledger for fleet rules. A record without evidence or a check kind is refused."""
import json
import os
import shlex
import subprocess
from datetime import date, datetime
from pathlib import Path

KINDS = ('command', 'bean', 'manual')
STATUSES = ('held', 'failed', 'retired')
OVERDUE_DAYS = 7


class LessonRejected(ValueError):
    pass


def ledger_path(explicit=None):
    if explicit:
        return Path(explicit)
    env = os.environ.get('FLEET_LESSONS')
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    repo = here.parent / 'rulebook' / 'lessons.jsonl'
    if repo.is_file():
        return repo
    return Path(os.environ.get('DROVER_HOME', str(Path.home()/'.local/share/drover'))) / 'rulebook' / 'lessons.jsonl'


def validate(record):
    if not isinstance(record, dict):
        raise LessonRejected('lesson must be an object')
    for field in ('id', 'cue', 'rule', 'evidence', 'check'):
        if field not in record or record[field] in (None, '', []):
            raise LessonRejected('lesson missing %s' % field)
    if not isinstance(record['evidence'], list) or not all(isinstance(item, str) and item.strip() for item in record['evidence']):
        raise LessonRejected('lesson missing evidence')
    check = record['check']
    if not isinstance(check, dict) or check.get('kind') not in KINDS or not str(check.get('ref') or '').strip():
        raise LessonRejected('lesson missing check kind')
    status = record.get('status', 'held')
    if status not in STATUSES:
        raise LessonRejected('lesson status must be held, failed, or retired')
    if 'last_verified' in record and record['last_verified'] is not None:
        _parse_day(record['last_verified'])
    return record


def _parse_day(value):
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except ValueError as error:
        raise LessonRejected('last_verified must be YYYY-MM-DD') from error


def _today(now):
    if now is None:
        now = os.environ.get('FLEET_LESSONS_NOW')
    if not now:
        return date.today()
    if isinstance(now, date) and not isinstance(now, datetime):
        return now
    return _parse_day(str(now)[:10])


def load(path):
    path = Path(path)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rows.append(validate(json.loads(line)))
    return rows


def save(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ''.join(json.dumps(row, sort_keys=True) + '\n' for row in rows)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(text)
    temporary.replace(path)


def add(path, record):
    row = validate(dict(record))
    row.setdefault('status', 'held')
    row.setdefault('last_verified', None)
    rows = load(path) if Path(path).is_file() else []
    if any(existing['id'] == row['id'] for existing in rows):
        raise LessonRejected('lesson id already exists')
    rows.append(row)
    save(path, rows)
    return row


def _overdue(row, today):
    if row['check']['kind'] == 'command' or row.get('status') == 'retired':
        return False
    verified = row.get('last_verified')
    if not verified:
        return True
    return (today - _parse_day(verified)).days > OVERDUE_DAYS


def _run_command(ref, timeout):
    if any(token in ref for token in (';', '|', '&', '`', '>', '<', '\n')):
        raise LessonRejected('command check must be one read-only argv')
    args = shlex.split(ref)
    if not args:
        raise LessonRejected('lesson missing check kind')
    completed = subprocess.run(args, timeout=timeout, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return completed.returncode == 0


def check(path, now=None, timeout=5):
    today = _today(now)
    rows = load(path)
    failed, overdue = [], []
    for row in rows:
        if row.get('status') == 'retired':
            continue
        if row['check']['kind'] == 'command':
            try:
                ok = _run_command(row['check']['ref'], timeout)
            except (subprocess.TimeoutExpired, OSError, LessonRejected):
                ok = False
            row['status'] = 'held' if ok else 'failed'
            row['last_verified'] = today.isoformat()
            if not ok:
                failed.append(row['id'])
        elif _overdue(row, today):
            overdue.append(row['id'])
    save(path, rows)
    return {'failed': failed, 'overdue': overdue, 'rows': rows}


def standup_text(path, now=None):
    today = _today(now)
    lines = []
    for row in load(path):
        if row.get('status') == 'failed':
            lines.append('FAILED %s %s' % (row['id'], row['cue']))
        elif _overdue(row, today):
            lines.append('OVERDUE %s %s' % (row['id'], row['cue']))
    return '\n'.join(lines) + ('\n' if lines else '')


def retro_text(path):
    lines = ["last retro's rules: held/failed"]
    for row in load(path):
        if row.get('status') == 'retired':
            continue
        lines.append('%s %s' % (row['id'], row.get('status', 'held')))
    return '\n'.join(lines) + '\n'


def ids_for_rule(section_key, path=None):
    path = ledger_path(path)
    return [row['id'] for row in load(path) if row['rule'] == section_key]


def _flag(argv, name):
    if name not in argv:
        return None
    index = argv.index(name)
    if index + 1 >= len(argv):
        raise LessonRejected('missing value for %s' % name)
    return argv[index + 1]


def main(argv):
    if len(argv) < 2 or argv[1] in ('-h', '--help'):
        return 0
    command = argv[1]
    path = ledger_path(_flag(argv, '--ledger'))
    if command == 'add':
        evidence = [argv[i + 1] for i, arg in enumerate(argv) if arg == '--evidence']
        record = {
            'id': _flag(argv, '--id'),
            'cue': _flag(argv, '--cue'),
            'rule': _flag(argv, '--rule'),
            'evidence': evidence,
            'check': {'kind': _flag(argv, '--check-kind'), 'ref': _flag(argv, '--check-ref')},
        }
        if _flag(argv, '--status'):
            record['status'] = _flag(argv, '--status')
        if _flag(argv, '--last-verified'):
            record['last_verified'] = _flag(argv, '--last-verified')
        try:
            add(path, record)
        except LessonRejected as error:
            print('lesson refused: %s' % error, file=__import__('sys').stderr)
            return 2
        return 0
    if command == 'list':
        for row in load(path):
            print('%s %s %s' % (row['id'], row.get('status', 'held'), row['cue']))
        return 0
    if command == 'check':
        report = check(path, now=_flag(argv, '--now') or os.environ.get('FLEET_LESSONS_NOW'))
        print('failed %s' % ','.join(report['failed']))
        print('overdue %s' % ','.join(report['overdue']))
        return 0
    if command == 'standup':
        __import__('sys').stdout.write(standup_text(path, now=_flag(argv, '--now') or os.environ.get('FLEET_LESSONS_NOW')))
        return 0
    if command == 'retro':
        __import__('sys').stdout.write(retro_text(path))
        return 0
    print('usage: fleet-lessons add|list|check|standup|retro', file=__import__('sys').stderr)
    return 2
