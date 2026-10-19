"""Print a fleet tool's header and exit before the tool can do anything."""
import sys
import re
from pathlib import Path

# Flags the tool itself accepts. Anything else that starts with -- exits 2.
KNOWN = {
    # Thin wrappers delegate these options to fleet_qa.py.
    'fleet-ask': {'--open'},
    'fleet-answer': {'--wake', '--rationale', '--by'},
    'fleet-prep': set(),
    'fleet-seat-live': set(),
    'fleet-shared-nm': {'--archive-real'},
    'fleet': {
        '--dry-run', '--yes', '--skills', '--yolo', '--model', '--always-approve',
        '--approve-mcps', '--auto', '--dangerously-bypass-approvals-and-sandbox',
        '--dangerously-skip-permissions', '--trust', '--report',
    },
    'fleet-claims-audit': {'--fixture', '--repo', '--json'},
    'fleet-cycle-detect': {'--dry-run', '--reports-dir'},
    'fleet-deploy': {'--no-probe'},
    'fleet-dispatch': {'--dry-run', '--json', '--record-owner'},
    'fleet-done': {'--check', '--sha', '--status'},
    'fleet-guard': {'--base', '--bean-file', '--branch', '--repo'},
    'fleet-main-tripwire': {
        '--ref', '--n', '--dry', '--force', '--db-check', '--check-freshness',
        '--print-alert', '--simulate-red',
    },
    'fleet-lessons': {
        '--id', '--cue', '--rule', '--evidence', '--check-kind', '--check-ref',
        '--status', '--last-verified', '--now', '--ledger',
    },
    'fleet-measure': {'--no-render'},
    'fleet-observe': {'--board', '--dry'},
    'fleet-observatory': {'--fixture', '--json', '--repo', '--output'},
    'fleet-precedent': {'--title', '--paths'},
    'fleet-ref-archive': {'--run', '--age-days', '--prefix', '--repo'},
    'fleet-refill': {'--dry-run', '--once'},
    'fleet-review-triage': {'--dry-run'},
    'fleet-rulebook': {'--title', '--paths'},
    'fleet-stale-jobs': {'--hours', '--min-age-hours', '--log-file', '--beans-json', '--repo', '--now'},
    'fleet-stale-worktree': {'--max'},
    'fleet-unblock': {'--dry-run'},
    'fleet-watch': {'--dry-run'},
    'fleet-wt-reap': {
        '--closed', '--dry-run', '--grace-min', '--retire', '--seats-only',
        '--target-free-gb', '--force', '--run',
    },
    'fleet-yolo': {'--dry-run', '--force', '--model', '--quiet'},
}


def _with_name(script_path, text):
    name = Path(script_path).name
    if name not in text:
        text = f'usage: {name}\n{text}'
    return text


def header_text(script_path):
    lines = Path(script_path).read_text().splitlines()
    index = 1 if lines and lines[0].startswith('#!') else 0
    comments = []
    while index < len(lines) and (lines[index].startswith('#') or lines[index].strip() == ''):
        if lines[index].startswith('#'):
            body = lines[index][1:]
            if body.startswith(' '):
                body = body[1:]
            comments.append(body)
        index += 1
    if comments:
        return _with_name(script_path, '\n'.join(comments).rstrip() + '\n')
    quote = None
    if index < len(lines):
        stripped = lines[index].lstrip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            quote = stripped[:3]
            chunk = [stripped[3:]]
            if stripped.count(quote) >= 2 and stripped.strip() != quote:
                return _with_name(script_path, stripped.split(quote, 2)[1].strip() + '\n')
            index += 1
            while index < len(lines):
                if quote in lines[index]:
                    chunk.append(lines[index].split(quote, 1)[0])
                    break
                chunk.append(lines[index])
                index += 1
            return _with_name(script_path, '\n'.join(chunk).strip() + '\n')
    return f'usage: {Path(script_path).name}\n'


def gate(argv, script_path):
    """Exit 0 on --help/-h, exit 2 on an unknown --flag, otherwise return."""
    known = set(KNOWN.get(Path(script_path).name, ()))
    known.update(re.findall(r'--[a-zA-Z][a-zA-Z0-9_-]*', Path(script_path).read_text()))
    known.update(('--help', '-h'))
    args = argv[1:]
    # A child command owns everything after the standard option terminator.
    if '--' in args:
        args = args[:args.index('--')]
    name = Path(script_path).name
    help_requested = any(arg in ('--help', '-h') for arg in args)
    subcommand = bool(args) and not args[0].startswith('-')
    if help_requested and not (subcommand and name in ('fleet', 'fleet-watch')):
        sys.stdout.write(header_text(script_path))
        raise SystemExit(0)
    for arg in args:
        if arg in ('--help', '-h'):
            continue
        flag = arg.split('=', 1)[0]
        if flag.startswith('--') and flag not in known:
            sys.stderr.write(f'unknown flag: {arg}\n')
            sys.stderr.write(header_text(script_path))
            raise SystemExit(2)


def main():
    if len(sys.argv) < 3 or sys.argv[1] != '--gate':
        sys.stderr.write('usage: fleet_usage.py --gate <script> [args...]\n')
        raise SystemExit(2)
    script = sys.argv[2]
    try:
        gate([script, *sys.argv[3:]], script)
    except SystemExit:
        raise
    raise SystemExit(10)


if __name__ == '__main__':
    main()
