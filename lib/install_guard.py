"""2026-10-18: preflight all tools before installing any; local fixes are never overwritten."""
import difflib
from pathlib import Path
import subprocess
import sys


def check(source, destination):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source), *args], stderr=subprocess.DEVNULL)
    ref = 'origin/main' if subprocess.run(['git', '-C', str(source), 'rev-parse', '--verify', 'origin/main'],
                                         capture_output=True).returncode == 0 else 'main'
    refused = False
    names = git('ls-files', '-z', '--', 'bin', 'lib').decode().split('\0')
    tracked = set(filter(None, names))
    for candidate in (source / 'bin').iterdir():
        if candidate.is_file() and str(candidate.relative_to(source)) not in tracked:
            print(f'REFUSED: untracked executable source: {candidate.name}', file=sys.stderr)
            refused = True
    for name in filter(None, names):
        path = source / name
        desired = path.read_bytes()
        committed = git('show', f'{ref}:{name}')
        if path.is_symlink() or desired != committed:
            print(f'REFUSED: source must match committed {ref}: {name}', file=sys.stderr)
            refused = True
        if not name.startswith('bin/'):
            continue
        installed = destination / path.name
        if not (installed.exists() or installed.is_symlink()):
            continue
        if installed.is_symlink() and installed.resolve() == path.resolve():
            continue  # Source provenance was checked above, including uncommitted edits.
        previous = installed.read_bytes() if installed.is_file() else b''
        known = not installed.is_symlink() and previous == committed
        if not known and not installed.is_symlink():
            for revision in git('log', '--format=%H', ref, '--', name).decode().splitlines():
                try:
                    if previous == git('show', f'{revision}:{name}'):
                        known = True
                        break
                except subprocess.CalledProcessError:
                    continue
        if not known:
            refused = True
            print(f'REFUSED: installed-only content at {installed}; reconcile and commit it first.', file=sys.stderr)
            print(''.join(difflib.unified_diff(previous.decode(errors='replace').splitlines(True),
                                             desired.decode(errors='replace').splitlines(True),
                                             fromfile=str(installed), tofile=name)), file=sys.stderr)
    return 1 if refused else 0


if __name__ == '__main__':
    try:
        raise SystemExit(check(Path(sys.argv[1]), Path(sys.argv[2])))
    except (OSError, subprocess.SubprocessError):
        print('REFUSED: committed installation evidence unavailable; nothing installed.', file=sys.stderr)
        raise SystemExit(1)
