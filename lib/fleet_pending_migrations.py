"""Read-only production migration inventory for watcher ticks and standups (bean-8kfz)."""
import argparse
from fleet_config import configure
config=configure(export=__name__=="__main__")
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import time

PREFIX = config.get('DROVER_MIGRATIONS_DIR', '').rstrip('/') + '/'
COOLDOWN = 600


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True, stderr=subprocess.DEVNULL, timeout=15).strip()


def inventory(repo, revision):
    return [name for name in git(repo, 'ls-tree', '-r', '--name-only', revision, '--', PREFIX).splitlines()
            if re.fullmatch(r'\d+[_-][^/]+\.sql', name.removeprefix(PREFIX))]


def version(name):
    return int(re.match(r'\d+', Path(name).name)[0])


def destructive(sql):
    # Conservative advisory scan, including comments/strings; not a SQL safety certification.
    return bool(re.search(r'\b(?:DROP|DELETE|TRUNCATE|ALTER)\b', sql, re.I))


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        file.write(text)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def status(repo, revision, paths, state):
    # Query the exact fetched tree, even when the lead's checkout lags origin/main.
    # git archive is read-only: no checkout, worktree registration or canonical source mutation.
    command = config.get('DROVER_MIGRATIONS_STATUS', '')
    if not command or PREFIX == '/':
        raise ValueError('migration status command/inventory unconfigured')
    with tempfile.TemporaryDirectory(prefix='migration-status-', dir=state) as name:
        directory = Path(name)
        archive = directory / 'source.tar'
        subprocess.run(['git', '-C', str(repo), 'archive', '--format=tar', '--output='+str(archive), revision],
                       check=True, capture_output=True, timeout=30)
        source = directory / 'source'; source.mkdir()
        with tarfile.open(archive) as file:
            file.extractall(source, filter='data')
        for target in config.get('DROVER_CLONE_DIRS','node_modules backend/node_modules').split():
            if (repo / target).exists() and not (source / target).exists():
                (source / target).parent.mkdir(parents=True,exist_ok=True)
                (source / target).symlink_to((repo / target).resolve(), target_is_directory=True)
        if (repo / '.env').is_file() and not (source / '.env').exists():
            (source / '.env').symlink_to((repo / '.env').resolve())
        env = {key:value for key,value in os.environ.items() if not key.startswith('PG_') and key not in
               {'PGHOST','PGPORT','PGDATABASE','PGUSER','PGPASSWORD','DATABASE_URL'}}
        env['PGOPTIONS'] = config.get('PGOPTIONS','')+' -c default_transaction_read_only=on'
        result = subprocess.run(['bash','-c',command],cwd=source,env=env,capture_output=True,text=True,timeout=45)
        if result.returncode: raise ValueError('migration status failed')
    # Same strict summary/pending inventory contract used by productionReadiness.mjs.
    # Script-outcome trailers, drift notices and successful-but-empty output are not a status summary.
    summary = re.search(r'^applied: (\d+)\s+pending: (\d+)\s+total files: (\d+)\s*$', result.stdout, re.M)
    pending = re.findall(r'^\s*(?:PENDING|SKIPPED)\s+(\S+)', result.stdout, re.M)
    names = {Path(path).name for path in paths}
    if (not summary or int(summary[1])+int(summary[2]) != int(summary[3])
            or int(summary[3]) != len(paths) or int(summary[2]) != len(pending)
            or len(set(pending)) != len(pending) or not set(pending) <= names):
        raise ValueError('migration status inventory unavailable')
    return sorted(names-set(pending))


def metadata(repo, revision, path):
    fields = git(repo, 'log', '--diff-filter=A', '-1', '--format=%H%x00%an%x00%ae%x00%s', revision, '--', path).split('\0')
    if len(fields) != 4:
        raise ValueError('landing metadata unavailable')
    sha, author, email, subject = fields
    bean = re.search(r'\bbean-[a-z0-9]+\b', subject)
    branches = git(repo, 'for-each-ref', '--format=%(refname:short)', '--contains='+sha, 'refs/heads/fleet/').splitlines()
    seats = {branch.removeprefix('fleet/').removesuffix('-'+bean[0]) for branch in branches
             if bean and branch.endswith('-'+bean[0])}
    seat = next(iter(seats)) if len(seats) == 1 else 'unattributed (git author: '+author+' <'+email+'>)'
    return sha, seat


def line(row):
    return f"{row['status'].upper()} {row['filename']} — landing: {row['landing_commit']} — seat: {row['seat']} — destructive: {'yes' if row['destructive'] else 'no'}"


def tick(repo, state, now):
    state.mkdir(parents=True, exist_ok=True)
    with (state/'migration-status.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        cache_path = state/'migration-status.json'
        cache = read_json(cache_path)
        alert = state/'alerts/pending-migrations.txt'
        try:
            revision = git(repo, 'rev-parse', 'origin/main')
            paths = inventory(repo, revision)
            if not paths:
                raise ValueError('migration inventory unavailable')
            at = cache.get('at')
            if not isinstance(at, (int, float)) or not 0 <= now-at < COOLDOWN:
                # Stamp attempts BEFORE querying: failed/slow/concurrent ticks also obey the cooldown.
                cache = {'at': now, 'error': 'status query incomplete'}
                write(cache_path, json.dumps(cache))
                try:
                    cache = {'at': now, 'applied_filenames': status(repo, revision, paths, state)}
                except (OSError, ValueError, subprocess.SubprocessError):
                    cache['error'] = 'read-only migration status unavailable'
                write(cache_path, json.dumps(cache))
            if cache.get('error') or not isinstance(cache.get('applied_filenames'), list):
                raise ValueError('read-only migration status unavailable')
            held_path = Path(config.get('DROVER_HELD_MIGRATIONS', os.path.join(config['DROVER_CONFIG_DIR'],'held-migrations.txt')))
            held = {text.split('#', 1)[0].strip() for text in held_path.read_text().splitlines()} if held_path.exists() else set()
            rows = []
            for path in paths:
                if Path(path).name in cache['applied_filenames']:
                    continue
                sha, seat = metadata(repo, revision, path)
                rows.append({'filename': Path(path).name, 'version': version(path), 'landing_commit': sha, 'seat': seat,
                             'status': 'held' if Path(path).name in held else 'pending',
                             'destructive': destructive(git(repo, 'show', revision+':'+path))})
            result = {'checked_at': cache['at'], 'revision': revision, 'rows': rows}
            pending = [row for row in rows if row['status'] == 'pending']
            if pending:
                write(alert, '\n'.join(map(line, pending))+f"\nProduction status checked at unix {cache['at']} (cached up to 600s).\n")
            else:
                alert.unlink(missing_ok=True)
            write(state/'pending-migrations.json', json.dumps(result))
            return 0
        except (OSError, ValueError, subprocess.SubprocessError):
            # Never echo status stderr: it can include connection credentials.
            text = 'UNAVAILABLE: production migration inventory/status; see read-only status access.'
            write(alert, text+'\n')
            write(state/'pending-migrations.json', json.dumps({'error': text}))
            return 2


def report(state):
    data = read_json(state/'pending-migrations.json')
    if 'rows' not in data:
        return data.get('error', 'UNAVAILABLE: no watcher migration inventory')
    rows = data['rows']
    checked = datetime.fromtimestamp(data['checked_at'], timezone.utc).isoformat(timespec='seconds')
    stale = '\nSTALE: watcher status snapshot is older than 600s.' if time.time()-data['checked_at'] >= COOLDOWN else ''
    return ('\n'.join(map(line, rows)) if rows else '- none')+f"\nProduction status checked at {checked}."+stale


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['tick', 'report'])
    parser.add_argument('--repo', type=Path, default=Path(config.get('DROVER_REPO') or os.getcwd()))
    parser.add_argument('--state', type=Path, default=Path(config['FLEET_STATE']))
    parser.add_argument('--now', type=float, default=None)
    args = parser.parse_args()
    if args.command == 'report':
        print(report(args.state))
        return 0
    return tick(args.repo.resolve(), args.state, args.now if args.now is not None else time.time())


if __name__ == '__main__':
    raise SystemExit(main())
