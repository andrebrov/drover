"""Original, read-only fleet snapshot and inert HTML (bean-l8ck).

No owner writes, network requests, shell evaluation, or report execution.
Only the CLI's explicitly chosen HTML output is written.
"""
import argparse
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
from zoneinfo import ZoneInfo

BEAN = re.compile(r'(?<![a-z0-9])' + os.environ.get('DROVER_BEAN_RE', r'bean-[a-z0-9]+') + r'(?![a-z0-9])')
SHA = re.compile(r'\b[0-9a-f]{7,40}\b')
MAX_BYTES = 4 * 1024 * 1024


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def epoch(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        # fleet-deploy writes `date '+%F %T'` in this machine's local zone.
        # Aware question/decision timestamps retain their explicit zone.
        return parsed.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def reported_status(text):
    """Owner's status vocabulary only; never a landing or acceptance proof."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines[:3]:
        match = re.match(r'^(?:Verdict:\s*)?(DONE|PARTIAL|BLOCKED)\b', line.lstrip('# '), re.I)
        if not match and line.startswith('#'):
            match = re.search(r'(?:—|:|\s-\s)\s*(DONE|PARTIAL|BLOCKED)\b', line, re.I)
        if match:
            return match.group(1).upper()
    match = re.match(r'^(DONE|PARTIAL|BLOCKED)\b', lines[-1], re.I) if lines else None
    return match.group(1).upper() if match else None


def origin_bean_statuses(reader, repo, revision):
    """Read only frontmatter from one immutable main revision, in one bounded batch."""
    result = reader.git(repo, 'grep', '-n', '-z', '-E', '^(---|status:)', revision,
                        '--', '.beans/*.md', allowed=(0, 1))
    if result is None:
        return None
    statuses, headers = {}, set()
    for line in result.stdout.splitlines():
        fields = line.split('\0', 2)
        if len(fields) != 3:
            continue
        name, number, value = fields
        name = name.partition(':')[2]
        bean = BEAN.match(Path(name).name)
        if not bean or not Path(name).name.startswith(bean.group() + '--'):
            continue
        if number == '1' and value == '---':
            headers.add(name)
        elif name in headers:
            if value == '---':
                headers.remove(name)
            elif value.startswith('status:'):
                match = re.fullmatch(r"status:\s*['\"]?(completed|scrapped|todo|in-progress|draft)['\"]?\s*(?:#.*)?", value)
                statuses[bean.group()] = match.group(1) if match else None
    return statuses


class Reader:
    def __init__(self, now):
        self.now = now
        self.sources = {}
        self.git_bin = '/opt/homebrew/bin/git' if Path('/opt/homebrew/bin/git').exists() else 'git'
        self.env = {k: v for k, v in os.environ.items() if k not in
                    ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR')}

    def record(self, path, operation):
        key = str(path)
        source = dict(self.sources.get(key, {'path': key, 'mtime': None}),
                      read_at=iso(time.time()), status='AVAILABLE', error=None)
        try:
            p = Path(path)
            if p.exists():
                stat = p.stat()
                source['mtime'] = iso(stat.st_mtime)
                if not stat.st_mode & 0o444:
                    raise PermissionError('Permission denied: source has no read permissions')
            value = operation()
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
            source.update(status='UNAVAILABLE', error=f'{type(exc).__name__}: {exc}')
            value = None
        self.sources[key] = source
        return value

    def text(self, path):
        def read():
            with Path(path).open('rb') as stream:
                data = stream.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError(f'source exceeds {MAX_BYTES} bytes')
            return data.decode('utf-8', errors='replace')
        return self.record(path, read)

    def json(self, path):
        text = self.text(path)
        if text is None:
            return None
        return self.record(path, lambda: json.loads(text))

    def files(self, directory, pattern):
        return self.record(directory, lambda: sorted(p for p in Path(directory).iterdir()
                                                    if p.is_file() and p.match(pattern)))

    def command(self, key, args, cwd=None, allowed=(0,), env=None, stdin=None):
        def run():
            r = subprocess.run(args, cwd=cwd, env=env or self.env, text=True,
                               capture_output=True, timeout=25, input=stdin)
            if r.returncode not in allowed:
                raise ValueError(f'exit {r.returncode}: {(r.stderr or r.stdout).strip()[:400]}')
            if len(r.stdout) > MAX_BYTES:
                raise ValueError('command output exceeds size limit')
            return r
        result = self.record(key, run)
        executable = shutil.which(args[0]) or args[0]
        self.sources[key]['path'] = str(Path(executable).absolute())
        self.sources[key]['command'] = args
        if Path(executable).exists():
            self.sources[key]['mtime'] = iso(Path(executable).stat().st_mtime)
        return result

    def git(self, repo, *args, allowed=(0,), stdin=None):
        return self.command(f'{repo} :: git {" ".join(args)}',
                            [self.git_bin, '-C', str(repo), '-c', f'core.worktree={repo}', *args],
                            allowed=allowed, stdin=stdin)

    def panel(self, keys, **content):
        keys = list(dict.fromkeys(keys))
        bad = sum(self.sources[k]['status'] == 'UNAVAILABLE' for k in keys)
        return dict(content, sources=keys,
                    status='UNAVAILABLE' if bad == len(keys) else 'PARTIAL' if bad else 'AVAILABLE')


def collect_snapshot(home, repo, now=None, fixture=None, dispatch_bin=None):
    home, repo = Path(home).absolute(), Path(repo).absolute()
    now = time.time() if now is None else now
    r = Reader(now)
    reports_dir = (home / '.local/share/drover/reports' if fixture else Path(os.environ['FLEET_REPORTS']))
    tasks_dir = (home / '.local/share/drover/tasks' if fixture else Path(os.environ['FLEET_TASKS']))
    panels = {}

    start = set(r.sources)
    agent_key = str(Path(fixture) / 'herdr.json') if fixture else 'herdr agent list'
    if fixture:
        agents_raw = r.json(Path(fixture) / 'herdr.json')
    else:
        cmd = r.command('herdr agent list', ['herdr', 'agent', 'list'])
        agents_raw = r.record('herdr agent list', lambda: json.loads(cmd.stdout)) if cmd else None
    agents = None
    if agents_raw is not None:
        agents_raw = r.record(agent_key, lambda: agents_raw['result']['agents'])
    if agents_raw is not None and not isinstance(agents_raw, list):
        agents_raw = r.record(agent_key, lambda: (_ for _ in ()).throw(ValueError('agents must be an array')))
    if isinstance(agents_raw, list):
        agents = []
        for a in agents_raw:
            if not isinstance(a, dict) or not re.fullmatch(r'[a-zA-Z0-9_-]+', str(a.get('name', ''))):
                r.record(agent_key, lambda: (_ for _ in ()).throw(ValueError('invalid agent record')))
                continue
            name = a['name']
            task = r.text(tasks_dir / name)
            assignment = {}
            if task is not None:
                def parse_task():
                    fields = task.rstrip('\n').split('\t')
                    if len(fields) != 4 or not BEAN.fullmatch(fields[1]):
                        raise ValueError('expected assigned-epoch, bean, baseline, report TSV')
                    assigned = float(fields[0])
                    return {'bean': fields[1], 'assigned_at': iso(assigned),
                            'elapsed_seconds': max(0, int(now - assigned)), 'report_path': fields[3]}
                assignment = r.record(tasks_dir / name, parse_task) or {}
            branch = r.git(Path(a['cwd']).absolute(), 'branch', '--show-current') if a.get('cwd') else None
            agents.append(dict(name=name, harness=a.get('agent'), status=a.get('agent_status'),
                               branch=branch.stdout.strip() if branch else None, **assignment))
    agent_sources = list(set(r.sources) - start)

    start = set(r.sources)
    newest, report_counts = {}, {}
    for path in r.files(reports_dir, '*.md') or []:
        matches = BEAN.findall(path.name)
        if not matches:
            continue
        bean = matches[0]
        report_counts[bean] = report_counts.get(bean, 0) + 1
        previous = newest.get(bean)
        if previous is None or path.stat().st_mtime > previous.stat().st_mtime:
            newest[bean] = path
    evidence = []
    report_texts = {}
    for bean, path in sorted(newest.items()):
        text = r.text(path)
        report_texts[bean] = text
        evidence.append({'bean': bean, 'path': str(path), 'verdict': text.splitlines()[0] if text else None,
                         'age_seconds': max(0, int(now - path.stat().st_mtime)),
                         'mtime': iso(path.stat().st_mtime)})
    evidence_sources = list(set(r.sources) - start)
    evidence_available = r.sources[str(reports_dir)]['status'] == 'AVAILABLE'
    evidence_complete = evidence_available and all(r.sources[k]['status'] == 'AVAILABLE' for k in evidence_sources)
    panels['evidence'] = r.panel(evidence_sources, reports=evidence if evidence_available else None)
    if agents is not None:
        for a in agents:
            latest = next((e for e in evidence if e['bean'] == a.get('bean')), None)
            a['newest_report'] = latest['path'] if latest else None
            a['report_age_seconds'] = latest['age_seconds'] if latest else None
    panels['agents'] = r.panel(agent_sources + evidence_sources, items=agents)
    if agents is None:
        panels['agents']['status'] = 'UNAVAILABLE'

    start = set(r.sources)
    if fixture:
        dispatch_key = str(Path(fixture) / 'dispatch.json')
        dispatch = r.json(dispatch_key)
    else:
        binary = dispatch_bin or str(Path(__file__).resolve().parents[1] / 'bin/fleet-dispatch')
        if not Path(binary).exists():
            binary = shutil.which('fleet-dispatch') or 'fleet-dispatch'
        dispatch_key = f'{binary} --dry-run --json'
        cmd = r.command(dispatch_key, [binary, '--dry-run', '--json'], cwd=repo,
                        env=dict(r.env, FLEET_ROOT=str(repo)))
        dispatch = r.record(dispatch_key, lambda: json.loads(cmd.stdout)) if cmd else None
    if dispatch is not None:
        def validate_dispatch():
            if not isinstance(dispatch, dict) or not isinstance(dispatch.get('dispatchable'), list):
                raise ValueError('missing dispatchable array')
            for key in ('board_ready', 'dispatchable_count'):
                if type(dispatch.get(key)) is not int or dispatch[key] < 0:
                    raise ValueError(f'missing/invalid {key}')
            if dispatch['dispatchable_count'] != len(dispatch['dispatchable']):
                raise ValueError('dispatchable count mismatch')
            return dispatch
        dispatch = r.record(dispatch_key, validate_dispatch)
    dispatch_sources = list(set(r.sources) - start)
    queued = dispatch['dispatchable'] if dispatch else None
    running = [a for a in agents if a['status'] == 'working'] if agents is not None else None
    landed, awaiting, blocked = [], [], []
    before = set(r.sources)
    origin = r.git(repo, 'rev-parse', '--verify', 'origin/main^{commit}')
    origin_sha = origin.stdout.strip() if origin else None
    bean_statuses = origin_bean_statuses(r, repo, origin_sha) if origin_sha else None
    # Every commit emitted by this traversal is an ancestor of origin/main.
    # One bounded read avoids a subprocess pair for every SHA in thousands of reports.
    ancestry = r.git(repo, 'log', '--format=%H%x09%ct%x09%s', origin_sha) if origin_sha else None
    ancestor_rows = [line.split('\t', 2) for line in ancestry.stdout.splitlines() if line.count('\t') >= 2] if ancestry else []
    ancestors = {fields[0]: fields[2] for fields in ancestor_rows} if ancestry else None
    commit_times = {fields[0]: int(fields[1]) for fields in ancestor_rows}
    # Git resolves prefixes against the entire object store, including unlanded
    # branches. An ambiguous prefix cannot be accepted even if only one of its
    # matching objects is an ancestor. Batch lookup keeps the 60-second job cheap.
    candidates = sorted({sha for text in report_texts.values() if text for sha in SHA.findall(text)})
    resolved = r.git(repo, 'cat-file', '--batch-check', stdin=''.join(sha + '^{commit}\n' for sha in candidates)) if candidates else None
    prefixes = {}
    if resolved:
        for short, line in zip(candidates, resolved.stdout.splitlines()):
            fields = line.split()
            if len(fields) == 3 and fields[1] == 'commit' and re.fullmatch(r'[0-9a-f]{40}', fields[0]):
                prefixes[short] = fields[0]
    git_sources = list(set(r.sources) - before)
    for report in evidence:
        text = report_texts[report['bean']]
        if text is None:
            continue
        report['bean_status'] = bean_statuses.get(report['bean']) if bean_statuses is not None else None
        closed = report['bean_status'] in ('completed', 'scrapped')
        status = reported_status(text)
        accepted = None
        for sha in dict.fromkeys(SHA.findall(text)):
            full = prefixes.get(sha)
            if full and ancestors is not None and full in ancestors and report['bean'] in BEAN.findall(ancestors[full]):
                if accepted is None or commit_times[full] > commit_times[accepted]:
                    accepted = full
        # Main's lifecycle state wins over stale reports. Without a cited,
        # verified implementation SHA, omit the closed report rather than
        # inventing a landing timestamp for the daily KPI.
        if closed and not accepted:
            continue
        if not closed and status == 'BLOCKED':
            blocked.append(report)
        elif not closed and status == 'PARTIAL':
            awaiting.append(report)
        elif accepted:
            landed.append(dict(report, sha=accepted, landed_at=iso(commit_times[accepted])))
        elif status == 'DONE':
            awaiting.append(report)
    lists = dict(queued=queued, running=running,
                 awaiting_review=awaiting if evidence_complete and bean_statuses is not None and ancestors is not None and (resolved or not candidates) else None,
                 blocked=blocked if evidence_complete and bean_statuses is not None else None,
                 landed=landed if evidence_complete and bean_statuses is not None and ancestors is not None and (resolved or not candidates) else None)
    panels['assignment_board'] = r.panel(agent_sources + evidence_sources + dispatch_sources + git_sources,
                                        counts={k: len(v) if v is not None else None for k, v in lists.items()},
                                        origin_main_sha=origin_sha, **lists)

    start = set(r.sources)
    decisions = []
    decision_files = r.files(reports_dir / 'decisions', '*.json')
    for path in decision_files or []:
        data = r.json(path)
        if isinstance(data, dict):
            decisions.append(dict(data, path=str(path)))
    answered = {d.get('id') for d in decisions}
    questions = []
    question_files = r.files(reports_dir / 'questions', '*.json')
    for path in question_files or []:
        q = r.json(path)
        if isinstance(q, dict) and q.get('id') not in answered:
            at = epoch(q.get('asked_at'))
            questions.append(dict(q, path=str(path), age_seconds=max(0, int(now-at)) if at else None))
    # A failed decision read cannot prove that any question is unanswered.
    question_known = decision_files is not None and question_files is not None and all(
        s['status'] == 'AVAILABLE' for k, s in r.sources.items() if k in set(r.sources) - start)
    skip_path = (home / '.config/drover/dispatch-skip' if fixture else Path(os.environ['DROVER_DISPATCH_SKIP']))
    skip = r.text(skip_path)
    parked = []
    bean_files = r.files(repo / '.beans', '*.md')
    for bean in [line.split('#')[0].strip() for line in (skip or '').splitlines()]:
        if not BEAN.fullmatch(bean):
            continue
        path = next((p for p in bean_files or [] if p.name.startswith(bean + '--')), None)
        text = r.text(path) if path else r.record(repo / '.beans' / bean, lambda: (_ for _ in ()).throw(FileNotFoundError('parked bean absent')))
        founder = [line for line in (text or '').splitlines() if 'founder' in line.lower()]
        title = re.search(r'^title:\s*(.*)$', text or '', re.M)
        parked.append({'bean': bean, 'title': title.group(1) if title else None,
                       'founder_line': founder[-1] if founder else None})
    attention_sources = list(set(r.sources) - start)
    panels['operator_attention'] = r.panel(attention_sources,
        open_questions=questions if question_known else None,
        founder_parked=parked if skip is not None and bean_files is not None else None)

    start = set(r.sources)
    events = []
    logs = r.git(repo, 'log', '-50', '--format=%ct%x09%H%x09%s', 'origin/main')
    if logs:
        for line in logs.stdout.splitlines():
            fields = line.split('\t', 2)
            if len(fields) == 3:
                events.append({'at': iso(float(fields[0])), 'epoch': float(fields[0]), 'kind': 'commit',
                               'sha': fields[1], 'text': fields[2]})
    inbox = (home / '.local/share/drover/inbox' if fixture else Path(os.environ['FLEET_INBOX']))
    for path in r.files(inbox, '*.msg') or []:
        text = r.text(path)
        if not text:
            continue
        def notice():
            fields = text.rstrip('\n').split('\t', 5)
            if len(fields) != 6:
                raise ValueError('invalid fleet-done announcement TSV')
            return {'at': iso(float(fields[0])), 'epoch': float(fields[0]), 'kind': 'announcement',
                    'seat': fields[1], 'bean': fields[2], 'text': fields[3] + ' ' + fields[4], 'path': str(path)}
        item = r.record(path, notice)
        if item:
            events.append(item)
    for d in decisions:
        at = epoch(d.get('at'))
        if at is not None:
            events.append({'at': iso(at), 'epoch': at, 'kind': 'decision', 'text': d.get('decision'), 'path': d['path']})
    deploy_path = reports_dir / 'deploys.log'
    deploy_text = r.text(deploy_path)
    deployments = []
    for line in (deploy_text or '').splitlines():
        fields = line.split('\t', 2)
        if len(fields) == 3:
            at = epoch(fields[0])
            if at is not None:
                item = {'at': iso(at), 'epoch': at, 'kind': 'deployment', 'sha': fields[1], 'text': fields[2]}
                events.append(item)
                if re.match(r'^DEPLOYED\b', fields[2]):
                    deployments.append(item)
    activity_sources = list(set(r.sources) - start) + [k for k in attention_sources if '/decisions' in k]
    events.sort(key=lambda e: e['epoch'], reverse=True)
    panels['activity'] = r.panel(activity_sources, events=events[:50])

    start = set(r.sources)
    deployed_sha, undeployed = None, None
    if deployments:
        latest = max(deployments, key=lambda e: e['epoch'])
        if re.fullmatch(r'[0-9a-f]{7,40}', latest['sha']):
            resolved = r.git(repo, 'rev-parse', '--verify', latest['sha'] + '^{commit}')
            if resolved:
                deployed_sha = resolved.stdout.strip()
                ancestor = r.git(repo, 'merge-base', '--is-ancestor', deployed_sha, 'origin/main', allowed=(0, 1))
                if ancestor and ancestor.returncode == 0:
                    lag = r.git(repo, 'rev-list', '--count', deployed_sha + '..origin/main')
                    undeployed = int(lag.stdout.strip()) if lag else None
    measures = r.files(reports_dir / 'measure', '*.json')
    measure_path = max(measures, key=lambda p: p.stat().st_mtime) if measures else reports_dir / 'measure'
    measure = r.json(measure_path) if measures else None
    pending = measure.get('pending_migrations') if isinstance(measure, dict) else None
    if type(pending) is not int or pending < 0:
        pending = None
    if pending is None:
        r.record(measure_path, lambda: (_ for _ in ()).throw(ValueError('latest measure does not provide a nonnegative pending_migrations count')))
    if deployed_sha is None or undeployed is None:
        r.record(deploy_path, lambda: (_ for _ in ()).throw(ValueError('no recorded deployed commit verifiable on origin/main')))
    production_sources = list(set(r.sources) - start) + [str(deploy_path)] + dispatch_sources
    panels['production'] = r.panel(production_sources, deployed_sha=deployed_sha,
                                   undeployed_commits=undeployed, pending_migrations=pending,
                                   dispatch=dispatch.get('summary') if dispatch else None)
    # Titles and evidence totals enrich the snapshot; they never establish landing.
    before = set(r.sources)
    needed = {a.get('bean') for a in agents or []} | set(newest)
    needed |= {b.get('id') for b in queued or []}
    bean_info = {}
    for path in bean_files or []:
        matches = BEAN.findall(path.name)
        if not matches or matches[0] not in needed:
            continue
        text = r.text(path)
        title = re.search(r'^title:\s*(.*?)\s*$', text or '', re.M)
        priority = re.search(r'^priority:\s*(.*?)\s*$', text or '', re.M)
        bean_info[matches[0]] = {'title': title.group(1).strip("\"'") if title else None,
                                 'priority': priority.group(1) if priority else 'normal'}
    seats_by_bean = {a.get('bean'): a['name'] for a in agents or [] if a.get('bean')}
    matched = {item['bean'] for item in landed}
    for report in evidence:
        report['binding'] = 'matched' if report['bean'] in matched else 'unbound'
        report['type'] = Path(report['path']).suffix.lstrip('.').upper()
    for a in agents or []:
        a['title'] = bean_info.get(a.get('bean'), {}).get('title')
        a['role'] = next((role for role in ('coder', 'reviewer', 'architect', 'analyst', 'sre', 'pm', 'judge', 'design', 'lead')
                          if a['name'] == role or a['name'].endswith('-' + role)), None)
        a['seen_at'] = r.sources[agent_key]['read_at'] if r.sources[agent_key]['status'] == 'AVAILABLE' else None
    for stage, items in lists.items():
        for item in items or []:
            bid = item.get('bean') or item.get('id')
            item.setdefault('title', bean_info.get(bid, {}).get('title'))
            item.setdefault('priority', bean_info.get(bid, {}).get('priority', 'normal'))
            item['seat'] = item.get('name') or seats_by_bean.get(bid)
            if item['seat'] is None and item.get('path'):
                item['seat'] = next((a['name'] for a in agents or []
                                     if Path(item['path']).name.startswith(a['name'] + '-' + bid)), None)
            item['evidence_count'] = report_counts.get(bid, 0) if evidence_available and bid else None
    metadata_sources = list(set(r.sources) - before)
    for name in ('agents', 'assignment_board', 'evidence'):
        p = panels[name]
        status = p['status']
        panels[name] = r.panel(p['sources'] + metadata_sources,
                              **{key: value for key, value in p.items() if key not in ('status', 'sources')})
        if status == 'UNAVAILABLE':
            panels[name]['status'] = status
    local_now = datetime.fromtimestamp(now, ZoneInfo('America/New_York'))
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    landed_today = sum(midnight <= epoch(item['landed_at']) <= now for item in lists['landed']) if lists['landed'] is not None else None
    attention = panels['operator_attention']
    needs_attention = len(attention['open_questions']) + len(attention['founder_parked']) if attention['open_questions'] is not None and attention['founder_parked'] is not None else None
    return {'observed_at': iso(now), 'sources': r.sources, 'panels': panels,
            'overview': {'session': os.environ.get('HERDR_SESSION_ID') or 'Local fleet',
                         'host': socket.gethostname(),
                         'kpis': {'working_now': panels['assignment_board']['counts']['running'],
                                  'awaiting_verification': panels['assignment_board']['counts']['awaiting_review'],
                                  'landed_today': landed_today, 'needs_attention': needs_attention}}}


def render_html(snapshot):
    """Bounded, inert reference layout; all foreign text passes through esc()."""
    def esc(value):
        return html.escape(str(value), quote=True)

    def clip(value, length=180):
        if value is None:
            return 'UNAVAILABLE'
        text = str(value)
        return text[:length].rstrip() + '…' if len(text) > length else text

    def text(value, length=180):
        return esc(clip(value, length))

    def number(value):
        return esc(value) if value is not None else 'UNAVAILABLE'

    def count(items):
        return len(items) if items is not None else None

    def clock(value, full=False):
        at = epoch(value)
        if at is None:
            return 'UNAVAILABLE'
        local = datetime.fromtimestamp(at, ZoneInfo('America/New_York'))
        return local.strftime('%H:%M:%S %Z' if full else '%H:%M %Z')

    def age(seconds):
        if seconds is None:
            return 'UNAVAILABLE'
        if seconds < 60:
            return 'just now'
        if seconds < 3600:
            return str(int(seconds // 60)) + 'm ago'
        if seconds < 86400:
            return str(int(seconds // 3600)) + 'h ago'
        return str(int(seconds // 86400)) + 'd ago'

    def icon(name):
        paths = {
            'overview': '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
            'agents': '<circle cx="9" cy="7" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M17 4a3 3 0 0 1 0 6M18 14a5 5 0 0 1 3 5v2"/>',
            'board': '<rect x="3" y="4" width="5" height="16" rx="1"/><rect x="10" y="4" width="5" height="12" rx="1"/><rect x="17" y="4" width="4" height="8" rx="1"/>',
            'activity': '<path d="M2 12h4l3-8 6 16 3-8h4"/>',
            'evidence': '<path d="M5 2h9l5 5v15H5zM14 2v5h5M8 12h8M8 16h8"/>',
            'sources': '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
        }
        return '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + paths[name] + '</svg>'

    def pill(label, kind=None):
        kind = kind if kind in ('working', 'idle', 'blocked', 'done', 'review', 'unknown') else 'unknown'
        return '<span class="pill ' + kind + '">' + text(label, 45) + '</span>'

    def status(panel):
        if panel['status'] == 'AVAILABLE':
            return ''
        return pill(panel['status'], 'blocked' if panel['status'] == 'UNAVAILABLE' else 'review')

    p = snapshot['panels']
    board = p['assignment_board']
    overview = snapshot['overview']
    kpis = overview['kpis']
    priority = {'critical': 0, 'high': 1, 'normal': 2, 'low': 3, 'deferred': 4}
    all_sources = snapshot['sources']

    def source_keys(keys):
        return sorted(set(keys), key=lambda k: (all_sources[k]['status'] != 'UNAVAILABLE', all_sources[k]['path'], k))

    def source_row(key):
        source = all_sources[key]
        error = '<span class="source-error">' + text(source['error'], 220) + '</span>' if source['error'] else ''
        command = '<span class="source-command">' + text(' '.join(source.get('command', [])), 180) + '</span>' if source.get('command') else ''
        return '<div class="source-row"><div class="source-path">' + esc(source['path']) + command + error + '</div><div><span class="label">Read time</span>' + text(source['read_at'], 50) + '</div><div><span class="label">mtime</span>' + text(source['mtime'], 50) + '</div><div>' + pill(source['status'], 'blocked' if source['status'] == 'UNAVAILABLE' else 'done') + '</div></div>'

    def source_footer(name, limit):
        keys = source_keys(p[name]['sources'])
        unavailable = sum(all_sources[k]['status'] == 'UNAVAILABLE' for k in keys)
        return '<div class="sources"><details><summary>Sources · ' + str(len(keys)) + ' recorded' + (' · ' + str(unavailable) + ' UNAVAILABLE' if unavailable else '') + '</summary>' + ''.join(source_row(key) for key in keys[:limit]) + ('<a class="source-more" href="#sources">Showing ' + str(min(limit, len(keys))) + ' of ' + str(len(keys)) + ' · source index</a>' if len(keys) > limit else '') + '</details></div>'

    def section_head(title, note='', panel=None):
        return '<div class="section-head"><h2>' + esc(title) + '</h2><span class="section-note">' + text(note, 90) + (' ' + status(panel) if panel else '') + '</span></div>'

    def empty(items, message):
        return '<div class="empty ' + ('unavailable' if items is None else '') + '">' + ('UNAVAILABLE · see source diagnostics' if items is None else esc(message)) + '</div>'

    def build(source_limit, footer_limit):
        agent_items = p['agents']['items']
        agent_rows = []
        agent_display = sorted(agent_items or [], key=lambda a: {'working': 0, 'blocked': 1, 'idle': 2, 'done': 3}.get(a.get('status'), 4))
        for a in agent_display[:20]:
            initials = ''.join(part[0] for part in a['name'].split('-') if part)[:2].upper()
            report_age = a.get('report_age_seconds')
            freshness = 'report unavailable' if report_age is None else 'fresh report' if report_age <= 900 else 'stale report'
            elapsed = 'dispatch unavailable' if a.get('elapsed_seconds') is None else str(a['elapsed_seconds'] // 60) + 'm since dispatch'
            role = ' · ' + text(a['role'], 25) if a.get('role') else ''
            title = a.get('title') or a.get('bean') or 'Assignment unavailable'
            agent_rows.append('<article class="agent-row" data-agent="' + esc(a['name']) + '"><span class="avatar">' + esc(initials) + '</span><div class="agent-info"><div class="agent-name">' + text(a['name'], 60) + '<span class="agent-role">' + text(a.get('harness'), 25) + role + '</span></div><div class="agent-task">' + text(title, 110) + '</div><div class="agent-meta">' + esc(elapsed) + ' · ' + esc(freshness) + ' · seen ' + esc(clock(a.get('seen_at'))) + '</div></div>' + pill(a.get('status') or 'UNAVAILABLE', a.get('status')) + '</article>')
        agent_note = ('20 of ' + str(len(agent_items)) if agent_items and len(agent_items) > 20 else number(count(agent_items))) + ' observed'
        agents_html = '<section id="agents">' + section_head('Agent activity', agent_note, p['agents']) + '<div class="panel agent-list">' + (''.join(agent_rows) if agent_rows else empty(agent_items, 'No agents recorded')) + '</div>' + source_footer('agents', footer_limit) + '</section>'

        column_titles = [('queued', 'Queued'), ('running', 'Running'), ('awaiting_review', 'Verification'), ('blocked', 'Blocked'), ('landed', 'Landed')]
        columns = []
        for stage, title in column_titles:
            items = board[stage]
            ordered = list(items or [])
            if stage == 'queued':
                ordered.sort(key=lambda item: priority.get(item.get('priority'), 2))
            elif stage == 'landed':
                ordered.sort(key=lambda item: item.get('landed_at', ''), reverse=True)
            elif stage in ('blocked', 'awaiting_review'):
                ordered.sort(key=lambda item: item.get('mtime', ''), reverse=True)
            cards = []
            for item in ordered[:15]:
                bid = item.get('bean') or item.get('id') or 'UNAVAILABLE'
                title_text = item.get('title') or bid
                seat = item.get('seat') or 'Seat unavailable'
                evidence_count = item.get('evidence_count')
                proof = '<div class="card-proof">' + number(evidence_count) + ' evidence' + (' · verified Git' if stage == 'landed' else '') + '</div>'
                cards.append('<article class="task-card" data-bean="' + esc(bid) + '" data-stage="' + stage + '"><div class="bean-id">' + text(bid, 45) + '</div><h3>' + text(title_text, 120) + '</h3><div class="card-seat">' + text(seat, 55) + '</div>' + proof + '</article>')
            total = board['counts'][stage]
            cap_note = '<div class="cap-note">Showing 15 of ' + str(total) + '</div>' if total is not None and total > 15 else ''
            columns.append('<div class="kanban-column" data-column="' + stage + '" data-count="' + number(total) + '"><div class="column-head"><h3>' + title + '</h3><span>' + number(total) + '</span></div>' + (''.join(cards) if cards else empty(items, 'No work recorded')) + cap_note + '</div>')
        board_html = '<section id="board">' + section_head('Work in motion', 'Assignment state · verified by Git', board) + '<div class="board-grid">' + ''.join(columns) + '</div>' + source_footer('assignment_board', footer_limit) + '</section>'

        attention = p['operator_attention']
        attention_groups = [[], [], []]
        questions = sorted(attention['open_questions'] or [], key=lambda q: q.get('age_seconds') or 0, reverse=True)
        for q in questions:
            import shlex
            # Commands are inert text; quote the owner-provided ID for safe copying.
            action = 'fleet-answer ' + shlex.quote(str(q.get('id', 'UNAVAILABLE'))) + ' …'
            attention_groups[0].append(('question', q.get('bean') or q.get('id'), q.get('question'), action, age(q.get('age_seconds'))))
        for bean in attention['founder_parked'] or []:
            attention_groups[1].append(('founder decision', bean.get('title') or bean['bean'], bean.get('founder_line'), 'Founder/lead: resolve ' + bean['bean'] + ' before un-parking.', 'parked'))
        for report in sorted(board['awaiting_review'] or [], key=lambda item: item.get('mtime', ''), reverse=True):
            attention_groups[2].append(('review', report.get('title') or report['bean'], report.get('verdict'), 'Review the report and verify its commit ancestry.', age(report.get('age_seconds'))))
        attention_cards = []
        # A long question queue must not hide every founder decision or review.
        for index in range(max(map(len, attention_groups), default=0)):
            for group in attention_groups:
                if index < len(group) and len(attention_cards) < 12:
                    attention_cards.append(group[index])
            if len(attention_cards) == 12:
                break
        attention_rows = []
        for kind, title_text, summary, action, when in attention_cards[:12]:
            attention_rows.append('<article class="attention-card">' + pill(kind, 'review') + '<h3>' + text(title_text, 110) + '</h3><p class="two-lines">' + text(summary, 220) + '</p><div class="attention-action">' + text(action, 160) + '</div><span class="attention-age">' + esc(when) + '</span></article>')
        if attention['open_questions'] is None or attention['founder_parked'] is None:
            attention_rows.insert(0, empty(None, ''))
        attention_total = sum(map(len, attention_groups))
        att_note = str(attention_total) + ' recorded'
        if attention_total > 12:
            att_note = '12 of ' + att_note
        attention_html = '<section id="attention">' + section_head('Operator attention', att_note, attention) + '<div class="panel attention-list">' + (''.join(attention_rows) if attention_rows else empty([], 'No unanswered questions or parked work')) + '<div class="observation-note">Viewing never answers, resolves or acknowledges an obligation.</div></div>' + source_footer('operator_attention', footer_limit) + '</section>'

        reports = p['evidence']['reports']
        report_rows = []
        for report in sorted(reports or [], key=lambda item: item.get('mtime', ''), reverse=True)[:20]:
            uri = Path(report['path']).absolute().as_uri()
            report_rows.append('<article class="evidence-row">' + icon('evidence') + '<div><a href="' + esc(uri) + '" title="' + esc(report['path']) + '">' + text(Path(report['path']).name, 80) + '</a><div class="evidence-meta">' + text(report.get('type'), 12) + ' · ' + text(report.get('binding'), 15) + ' · ' + esc(age(report.get('age_seconds'))) + '</div><p class="evidence-verdict">' + text(report.get('verdict'), 180) + '</p></div></article>')
        evidence_note = '20 of ' + str(len(reports)) if reports and len(reports) > 20 else number(count(reports)) + ' reports'
        evidence_html = '<section id="evidence">' + section_head('Evidence library', evidence_note, p['evidence']) + '<div class="panel evidence-list">' + (''.join(report_rows) if report_rows else empty(reports, 'No reports recorded')) + '<div class="observation-note">Matched means Git ancestry and bean subject verified. File links only.</div></div>' + source_footer('evidence', footer_limit) + '</section>'

        event_rows = []
        for event in p['activity']['events'][:50]:
            kind = event.get('kind', 'event')
            event_rows.append('<li class="event"><span class="timeline-dot ' + ('deploy' if kind == 'deployment' else '') + '"></span><div class="event-body"><div>' + pill(kind, 'done' if kind == 'commit' else 'idle') + '<span class="event-time">' + esc(clock(event.get('at'), True)) + '</span></div><p>' + text(event.get('text'), 180) + '</p><span class="event-meta">' + text(event.get('bean') or (event.get('sha') or '')[:10], 50) + '</span></div></li>')
        activity_html = '<section id="activity">' + section_head('Recent activity', 'Newest observations first', p['activity']) + '<ol class="panel timeline">' + (''.join(event_rows) if event_rows else '<li>' + empty([], 'No events recorded') + '</li>') + '</ol>' + source_footer('activity', footer_limit) + '</section>'

        production = p['production']
        production_html = '<section id="production">' + section_head('Recorded production', 'Log evidence', production) + '<div class="panel production"><div><span class="label">Deployed SHA</span><code>' + text((production.get('deployed_sha') or 'UNAVAILABLE')[:12], 20) + '</code></div><div><span class="label">Undeployed commits</span><strong>' + number(production.get('undeployed_commits')) + '</strong></div><div><span class="label">Pending migrations</span><strong>' + number(production.get('pending_migrations')) + '</strong></div><div><span class="label">Dispatch board</span><span>' + text(production.get('dispatch'), 90) + '</span></div></div>' + source_footer('production', footer_limit) + '</section>'

        source_ids = source_keys(all_sources)
        bad_sources = sum(s['status'] == 'UNAVAILABLE' for s in all_sources.values())
        sources_html = '<section id="sources" class="source-index">' + section_head('Sources', str(len(source_ids)) + ' recorded · ' + str(bad_sources) + ' UNAVAILABLE') + '<div class="panel">' + ''.join(source_row(key) for key in source_ids[:source_limit]) + '</div><p class="cap-note">Showing ' + str(min(source_limit, len(source_ids))) + ' of ' + str(len(source_ids)) + ' sources, failed reads first. Full diagnostics: <code>fleet-observatory --json</code>.</p></section>'

        values = [('working_now', 'Working now', 'Live working seats', 'agents'),
                  ('awaiting_verification', 'Awaiting verification', 'Claims pending assessment', 'evidence'),
                  ('landed_today', 'Landed today', 'Verified ancestors since 00:00 ET', 'board'),
                  ('needs_attention', 'Needs attention', 'Open questions + parked beans', 'sources')]
        kpi_html = '<div class="kpis">' + ''.join('<article class="kpi"><div class="kpi-label">' + label + icon(glyph) + '</div><div class="kpi-value ' + ('unknown-value' if kpis[key] is None else '') + '">' + number(kpis[key]) + '</div><div class="kpi-note">' + note + '</div></article>' for key, label, note, glyph in values) + '</div>'
        board_total = sum(board['counts'].values()) if all(v is not None for v in board['counts'].values()) else None
        navigation = [('overview', 'Overview', kpis['needs_attention']), ('agents', 'Agents', count(agent_items)), ('board', 'Board', board_total), ('activity', 'Activity', len(p['activity']['events'])), ('evidence', 'Evidence', count(reports)), ('sources', 'Sources', len(all_sources))]
        nav = ''.join('<a href="#' + key + '"' + (' class="selected"' if key == 'overview' else '') + '>' + icon(key) + '<span>' + label + '</span><span class="nav-count">' + number(total) + '</span></a>' for key, label, total in navigation)
        sidebar = '<aside class="sidebar"><a class="brand" href="#overview"><span class="brand-mark" aria-hidden="true"><span></span></span><span>Fleet<span class="brand-sub">Observatory</span></span></a><div class="sidebar-label">FLEET WORKSPACE</div><div class="workspace-name">' + text(overview.get('session'), 55) + '</div><nav aria-label="Dashboard sections">' + nav + '</nav><div class="sidebar-footer"><strong><span class="fresh-dot"></span> Observation mode</strong><p>Every claim has a source.</p><p>Reading never changes the fleet.</p></div></aside>'
        header = '<header id="overview"><div><div class="eyebrow">' + text(overview.get('session'), 45) + ' / ' + text(overview.get('host'), 65) + '</div><h1>A clear view of the fleet.</h1><p class="subtitle">Agents, evidence, and the decisions that move work forward.</p></div><div class="updated"><span><span class="fresh-dot"></span> Snapshot recorded</span><time datetime="' + esc(snapshot['observed_at']) + '">Updated ' + esc(clock(snapshot['observed_at'], True)) + '</time></div></header>'
        return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="30"><meta name="color-scheme" content="light dark"><title>Fleet Observatory</title><style>' + OBSERVATORY_CSS + '</style></head><body><a class="skip-link" href="#overview">Skip to overview</a><div class="app">' + sidebar + '<main>' + header + kpi_html + '<div class="content-grid"><div class="primary">' + agents_html + board_html + activity_html + '</div><aside class="secondary" aria-label="Operator evidence">' + attention_html + evidence_html + production_html + '</aside></div>' + sources_html + '<footer class="page-footer">Observation mode — every claim has a source. Page refresh 30s · generator cadence 60s.</footer></main></div></body></html>\n'

    for limits in ((40, 3), (10, 1)):
        page = build(*limits)
        if len(page.encode('utf-8')) < 300000:
            return page
    raise ValueError('Bounded dashboard exceeds 300 KB; source paths require investigation')


OBSERVATORY_CSS = r'''
:root{color-scheme:light dark;--bg:#f6f7f9;--side:#eef0f3;--surface:#fff;--soft:#f0f2f5;--ink:#20272e;--muted:#5d6874;--border:#e1e5e9;--green:#256751;--green-bg:#eaf4ef;--blue:#2d5788;--blue-bg:#e9f0f8;--red:#9a3641;--red-bg:#fbecef;--amber:#80571e;--amber-bg:#fbf1df;--shadow:0 1px 3px #182b3a06}
*{box-sizing:border-box}html{scroll-padding-top:24px}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:inherit;text-decoration:none;transition:background .15s,color .15s}a:hover{color:var(--green)}a:focus-visible,summary:focus-visible{outline:2px solid var(--blue);outline-offset:4px;border-radius:4px}svg{flex-shrink:0}.icon{width:17px;height:17px}code,.bean-id{font:11px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}h1,h2,h3,p{margin:0}h1{font-size:30px;line-height:1.25;letter-spacing:-.9px;font-weight:650}h2{font-size:15px;font-weight:650;letter-spacing:-.15px}h3{font-size:13px;font-weight:600;line-height:1.5}main{padding:34px 32px 28px;min-width:0}.app{display:grid;grid-template-columns:220px minmax(0,1fr);max-width:1700px;margin:auto;min-height:100vh}.sidebar{position:sticky;top:0;height:100vh;background:var(--side);border-right:1px solid var(--border);padding:30px 18px 22px;display:flex;flex-direction:column;min-width:0}.brand{display:flex;gap:11px;align-items:center;font-size:18px;line-height:1.2;font-weight:650;padding:0 10px;margin-bottom:36px}.brand-sub{display:block;font-size:10px;letter-spacing:1.7px;text-transform:uppercase;color:var(--muted);font-weight:500;margin-top:5px}.brand-mark{width:34px;height:34px;border-radius:10px;background:#273b36;display:grid;place-items:center;flex-shrink:0}.brand-mark>span{width:19px;height:19px;border:2px solid #fff;border-radius:50%;box-shadow:0 0 0 3px #ffffff27}.sidebar-label{font-size:10px;letter-spacing:1.5px;font-weight:600;color:var(--muted);padding:0 10px;margin-bottom:9px}.workspace-name{font-size:12px;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:7px 10px;margin:0 5px 28px;overflow-wrap:anywhere}.sidebar nav{display:grid;gap:6px}.sidebar nav a{display:flex;gap:11px;align-items:center;padding:10px 11px;border-radius:7px;color:var(--muted);font-size:13px;cursor:pointer}.sidebar nav a:hover,.sidebar nav a.selected{background:var(--surface);color:var(--ink);box-shadow:var(--shadow)}.sidebar nav a.selected{font-weight:600}.nav-count{min-width:0;overflow-wrap:anywhere;margin-left:auto;font-size:11px;color:var(--muted)}.sidebar-footer{margin-top:auto;padding:20px 10px 0;font-size:11px;color:var(--muted)}.sidebar-footer strong{display:block;color:var(--green);font-size:11px;font-weight:600;margin-bottom:9px}.sidebar-footer p{margin-top:5px;max-width:165px}.fresh-dot{width:6px;height:6px;border-radius:50%;background:var(--green);display:inline-block;margin-right:5px;box-shadow:0 0 0 3px var(--green-bg)}header{display:flex;justify-content:space-between;gap:18px;align-items:center;margin:8px 0 25px}.eyebrow{font-size:10px;text-transform:uppercase;letter-spacing:1.55px;font-weight:600;color:var(--green);margin-bottom:9px;overflow-wrap:anywhere}.subtitle{color:var(--muted);font-size:12px;margin-top:8px}.updated{text-align:right;font-size:10px;color:var(--muted);flex-shrink:0}.updated>span,.updated time{display:block}.updated time{margin-top:3px}.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-bottom:24px}.kpi{padding:17px 18px;border:1px solid var(--border);border-radius:9px;background:var(--surface);box-shadow:var(--shadow);min-width:0}.kpi-label{font-size:11px;display:flex;justify-content:space-between;gap:10px;align-items:center}.kpi-label .icon{width:13px;height:13px;color:var(--muted)}.kpi-value{font-size:30px;line-height:1.2;letter-spacing:-.7px;font-weight:600;margin:10px 0 4px}.kpi-value.unknown-value{font-size:13px;line-height:36px;color:var(--red);letter-spacing:0}.kpi-note{font-size:10px;color:var(--muted)}.content-grid{display:grid;grid-template-columns:minmax(0,1fr) 280px;gap:24px}.primary,.secondary{min-width:0}section{margin-bottom:24px;min-width:0}.section-head{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:10px}.section-note{font-size:10px;color:var(--muted);text-align:right}.panel{border:1px solid var(--border);border-radius:9px;background:var(--surface);box-shadow:var(--shadow);overflow:hidden}.agent-list{max-height:390px;overflow-y:auto;scrollbar-width:thin}.agent-row{display:flex;align-items:center;gap:12px;padding:15px 16px;border-bottom:1px solid var(--border)}.agent-row:last-child{border-bottom:0}.avatar{background:var(--blue-bg);color:var(--blue);border-radius:9px;width:31px;height:31px;display:grid;place-items:center;font-size:10px;font-weight:650;flex-shrink:0}.agent-info{min-width:0;flex:1}.agent-name{font-size:12px;font-weight:650;overflow-wrap:anywhere}.agent-role{font-size:10px;font-weight:400;color:var(--muted);margin-left:7px}.agent-task{font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:2px}.agent-meta{color:var(--muted);font-size:10px;margin-top:2px;overflow-wrap:anywhere}.pill{display:inline-block;font-size:9px;font-weight:500;line-height:1.35;padding:3px 5px;border-radius:4px;background:var(--soft);color:var(--muted);overflow-wrap:anywhere}.pill.working{background:var(--green-bg);color:var(--green)}.pill.done{background:var(--blue-bg);color:var(--blue)}.pill.blocked,.unavailable{background:var(--red-bg);color:var(--red)}.pill.review{background:var(--amber-bg);color:var(--amber)}.agent-row>.pill{align-self:flex-start;margin-top:2px}.attention-list{max-height:390px;overflow-y:auto;scrollbar-width:thin}.attention-card{padding:15px 16px;border-bottom:1px solid var(--border)}.attention-card h3{margin:9px 0 5px;font-size:12px;overflow-wrap:anywhere}.attention-card p{font-size:11px;color:var(--muted)}.two-lines{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.attention-action{font-size:10px;color:var(--amber);margin-top:9px;overflow-wrap:anywhere}.attention-age{font-size:9px;color:var(--muted);display:block;margin-top:5px}.observation-note{font-size:10px;color:var(--muted);background:var(--soft);padding:11px 14px;line-height:1.5}.board-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;align-items:start}.kanban-column{background:var(--side);border:1px solid var(--border);border-radius:7px;padding:8px;min-width:0;min-height:186px}.column-head{display:flex;gap:5px;justify-content:space-between;align-items:center;margin:2px 0 10px}.column-head h3{font-size:10px}.column-head>span{font-size:10px;color:var(--muted)}.task-card{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:10px 9px;margin-bottom:7px;box-shadow:var(--shadow);min-width:0}.bean-id{font-size:9px;color:var(--muted);overflow-wrap:anywhere;margin-bottom:5px}.task-card h3{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:33px;font-size:11px;line-height:1.5;overflow-wrap:anywhere}.card-seat{font-size:9px;margin-top:8px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.card-proof{font-size:9px;margin-top:3px;color:var(--green);overflow-wrap:anywhere}.cap-note{font-size:10px;line-height:1.5;color:var(--muted);margin-top:10px;overflow-wrap:anywhere}.empty{padding:16px;font-size:11px;color:var(--muted);overflow-wrap:anywhere}.empty.unavailable{color:var(--red)}.kanban-column .empty{padding:10px 2px;font-size:10px}.evidence-list{max-height:400px;overflow-y:auto;scrollbar-width:thin}.evidence-row{display:flex;gap:10px;padding:12px 14px;border-bottom:1px solid var(--border)}.evidence-row>.icon{margin-top:2px;color:var(--muted);width:15px;height:15px}.evidence-row>div{min-width:0}.evidence-row a{display:block;font-size:11px;font-weight:600;overflow-wrap:anywhere}.evidence-row a:hover{text-decoration:underline}.evidence-meta{font-size:9px;color:var(--muted);margin-top:3px}.evidence-verdict{font-size:10px;color:var(--muted);margin-top:5px;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.timeline{list-style:none;margin:0;padding:6px 16px}.event{display:flex;gap:12px;padding:13px 0;position:relative}.event:not(:last-child):before{position:absolute;content:"";left:3px;top:26px;bottom:-12px;width:1px;background:var(--border)}.timeline-dot{width:7px;height:7px;margin-top:6px;border-radius:50%;background:var(--blue);flex-shrink:0}.timeline-dot.deploy{background:var(--green)}.event-body{min-width:0;flex:1}.event-time{font-size:9px;color:var(--muted);float:right;line-height:19px}.event p{font-size:11px;margin-top:5px;overflow-wrap:anywhere}.event-meta{font:9px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted)}.production{padding:4px 14px}.production>div{padding:10px 0;border-bottom:1px solid var(--border);font-size:11px}.production>div:last-child{border:0}.production strong{font-weight:500}.label{display:block;font-size:9px;color:var(--muted);margin-bottom:3px}.sources{font-size:9px;color:var(--muted);margin-top:8px}.sources summary{cursor:pointer;padding:3px 0}.sources details[open]{margin-top:3px}.source-row{display:grid;grid-template-columns:minmax(0,2.6fr) minmax(0,1fr) minmax(0,1fr) minmax(0,.6fr);gap:16px;padding:12px 14px;border-bottom:1px solid var(--border);font-size:10px;align-items:start}.source-row:last-child{border:0}.source-row>div{min-width:0;overflow-wrap:anywhere}.source-path{font:10px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}.source-command{display:block;color:var(--muted);font-size:9px;margin-top:4px}.source-error{display:block;color:var(--red);font:10px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin-top:4px}.sources .source-row{grid-template-columns:minmax(0,1fr);gap:4px;border:1px solid var(--border);background:var(--surface);border-radius:5px;margin:6px 0;padding:8px}.sources .source-row .label{display:inline;margin-right:4px}.source-more{display:block;margin-top:6px;color:var(--green)}.source-index{margin-top:6px}.page-footer{font-size:10px;color:var(--muted);border-top:1px solid var(--border);padding-top:16px}.skip-link{position:absolute;top:-50px;left:10px;z-index:5;background:var(--surface);padding:8px}.skip-link:focus{top:10px}
@media (prefers-color-scheme: dark){:root{--bg:#151b22;--side:#1b232d;--surface:#202b35;--soft:#26313d;--ink:#ecf0f4;--muted:#b2bcc6;--border:#374453;--green:#8ed3b5;--green-bg:#233d33;--blue:#bdd6f6;--blue-bg:#263a56;--red:#ffb9c0;--red-bg:#452d35;--amber:#f2cd89;--amber-bg:#463d2b;--shadow:none}.brand-mark{background:#284a3f}}
@media(max-width:1250px){main{padding:28px 22px}.app{grid-template-columns:200px minmax(0,1fr)}.content-grid{grid-template-columns:minmax(0,1fr) 250px;gap:18px}.kpis{gap:10px}.kpi{padding:14px}.board-grid{gap:5px}.kanban-column{padding:6px}.task-card{padding:8px 6px}.column-head h3{font-size:9px}}
@media(max-width:1050px){.content-grid{grid-template-columns:minmax(0,1fr)}.secondary{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.secondary section:last-child{grid-column:1/-1}.sidebar{padding:24px 12px}.brand{font-size:16px}.kpi-label{font-size:10px}.updated{max-width:130px}.board-grid{gap:8px}.column-head h3{font-size:10px}}
@media(max-width:760px){.app{display:block}.sidebar{position:static;height:auto;border-right:0;border-bottom:1px solid var(--border);padding:18px}.brand{margin:0 0 16px;padding:0}.sidebar-label,.workspace-name,.sidebar-footer{display:none}.sidebar nav{grid-template-columns:repeat(3,minmax(0,1fr));gap:4px}.sidebar nav a{padding:8px;font-size:11px;gap:7px}.nav-count{font-size:9px}main{padding:22px 18px}header{align-items:flex-start;margin-top:0}h1{font-size:25px}.eyebrow{font-size:9px}.subtitle{font-size:11px;max-width:270px}.kpis{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.kpi{padding:14px}.kpi-value{font-size:29px}.kpi-note{font-size:9px}.board-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.kanban-column:last-child{grid-column:1/-1}.kanban-column{min-height:130px;padding:10px}.column-head h3{font-size:12px}.task-card h3{font-size:12px}.task-card{padding:12px}.bean-id,.card-seat,.card-proof{font-size:10px}.secondary{display:block}.source-row{grid-template-columns:minmax(0,1fr);gap:8px}.source-row .label{display:inline;margin-right:5px}.agent-row{padding:13px 12px;gap:9px}.agent-role{display:block;margin-left:0}.agent-meta{font-size:9px}.section-note{max-width:140px}.event-time{float:none;margin-left:10px}}
@media(max-width:450px){header{display:block}.updated{text-align:left;max-width:none;margin-top:12px}.updated>span,.updated time{display:inline}.updated time{margin-left:7px}.board-grid{grid-template-columns:minmax(0,1fr)}.sidebar nav a{font-size:10px}.sidebar nav .icon{width:14px;height:14px}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
'''


def main():
    parser = argparse.ArgumentParser(description='Read-only fleet dashboard; writes only its HTML output.')
    parser.add_argument('--json', action='store_true', help='print snapshot without writing HTML')
    parser.add_argument('--repo', type=Path, default=Path(os.environ.get('FLEET_ROOT', os.getcwd())))
    parser.add_argument('--output', type=Path, default=Path(os.environ.get('FLEET_REPORTS', str(Path.home()/'.local/share/drover/reports'))) / 'observatory.html')
    parser.add_argument('--fixture', type=Path, help='use an isolated home/repo and recorded command payloads')
    args = parser.parse_args()
    home = args.fixture / 'home' if args.fixture else Path.home()
    repo = args.fixture / 'repo' if args.fixture else args.repo
    snapshot = collect_snapshot(home, repo, fixture=args.fixture)
    if args.json:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
    else:
        output = args.output.expanduser().absolute()
        protected = ((home / '.config/drover' if args.fixture else Path(os.environ['DROVER_CONFIG_DIR'])), (home / '.local/share/drover/tasks' if args.fixture else Path(os.environ['FLEET_TASKS'])),
                     (home / '.local/share/drover/inbox' if args.fixture else Path(os.environ['FLEET_INBOX'])), *[(home / '.local/share/drover/reports' if args.fixture else Path(os.environ['FLEET_REPORTS'])) / d for d in ('questions', 'decisions', 'measure')])
        if output.suffix != '.html' or any(output.resolve().is_relative_to(p.resolve()) for p in protected):
            parser.error('HTML output must be an .html file outside fleet owner state')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_html(snapshot), encoding='utf-8')
        print(output)


if __name__ == '__main__':
    main()
