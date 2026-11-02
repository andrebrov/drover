"""Bean-text helpers shared by the fleet tools (fleet regression).

fleet-rulebook sends one bean's title and cited repository paths to Jev; fleet-precedent builds its
query from the same title and paths. Both use the extractor below, so the credential redaction and the
path grammar cannot drift: a token that merely LOOKS like a path (an auth header, a URL) must be
dropped before it is treated as one.
"""
import re
import subprocess

MAX_PATHS = 30

# Ops text only: strip anything shaped like a credential or an address before it leaves the machine.
VAL = r"""(?:"(?:[^"\\\n]|\\.)*"[^\s;&|<>()'"]*|'(?:[^'\\\n]|\\.)*'[^\s;&|<>()'"]*|\S{1,64}(?:[^"\\\n]|\\.)+"[^\s;&|<>()'"]*|\S{1,64}(?:[^'\\\n]|\\.)+'[^\s;&|<>()'"]*|\S+)"""   # a quoted value is one value, spaces and all. Five branches, in order: a quote at the TOKEN START for each style, then the same with a bounded lazy unquoted prefix (shell `PASSWORD=prefix"a b"`), then a bare token. Closing quote REQUIRED, or one stray quote reads to end of line and deletes the operator instruction with the secret (fleet regression). Start-quote branches come first so the OUTER quote wins when a value contains the other style (`'"hint" actualsecret'` is one value; matching the inner quote shipped `actualsecret'`). Inner alternatives are DISJOINT (`\.` handles the backslash the negated class excludes) or an unterminated value backtracks exponentially. The glued tail stops at whitespace and shell separators, so it cannot swallow the path that follows (`PASSWORD="foo";backend/src/a.ts` still yields that path to Jev), and the mid-token prefix is capped at 64 chars so a long token cannot be retried at every offset.   # a quoted value is one value, spaces and all: the quoted part may open anywhere in the token, an escaped quote does not close it, and text glued to the closing quote is still the value. The closing quote is REQUIRED, or one stray quote reads to end of line and deletes the operator's instruction with the secret (fleet regression; fleet-block-route shipped the double-quoted half of this first). The two branches that match a quote AT THE TOKEN'S START come first, so the outer quote wins when a value contains the other quote style -- with only the anywhere-in-token branches, `PASSWORD='"hint" actualsecret'` matched the inner `"hint"` and left `actualsecret'` in egress. The inner alternatives are DISJOINT (the negated class excludes the backslash `\\.` handles): with `\\.` and `[^"\n]` overlapping, a value with no closing quote backtracks exponentially -- 28 backslash pairs took 28.2 s against 0.008 s for the pre-bean form, and the disjoint form takes 0.005 ms.
REDACT = re.compile(r"SERVICE_[A-Z_]+=" + VAL + r"\n?|\b(?:sk-|ghp_|gho_|github_pat_|xox[abp]-|AKIA|ASIA)[A-Za-z0-9_\-]{8,}"
                    r"|\b[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)\s*[:=]\s*" + VAL + r"|[\w.+-]+@[\w-]+\.[\w.]+"
                    r"|(?i:\bbearer\s+[A-Za-z0-9._~+/=-]{8,})"
                    r"|(?i:(?<![\w-])--?[\w-]*(?:passw(?:or)?d|token|secret|api[_-]?key)[\w-]*(?:\s+|=)(?!-)" + VAL + r")"
                    r"|(?i:\b(?:authorization|api[_-]?key|access[_-]?token|token|secret|passw(?:or)?d)\s*[:=]\s*(?:bearer\s+)?"
                    + VAL + r")"
                    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)")
URLISH = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+")
PATH = re.compile(r"(?<![\w./-])(?:\.?[\w-]+/)+(?:[\w.-]*[\w-])?/?")


def extract_paths(body):
    """Repository-looking paths the bean cites, first-seen order, URLs and credentials dropped first (a token can
    look like a path)."""
    got = []
    for m in PATH.finditer(URLISH.sub(" ", REDACT.sub(" ", body or ""))):
        p = m.group(0)
        if p.count("/") < 1 or p.startswith("//") or p in got:
            continue
        got.append(p)
        if len(got) == MAX_PATHS:
            break
    return got


def bean_text(root, ref, bean):
    """(path, text) of .beans/<bean>--*.md at ref in root, or (None, ''). The path is the artifact the
    caller names so a reader can open the whole bean rather than the excerpt."""
    try:
        names = subprocess.run(["git", "-C", root, "ls-tree", "-r", "--name-only", ref, "--", ".beans/"],
                               capture_output=True, text=True, timeout=20).stdout.split("\n")
        path = next((n for n in names if n.startswith(".beans/%s--" % bean)), None)
        if not path:
            return None, ""
        text = subprocess.run(["git", "-C", root, "show", ref + ":" + path],
                              capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return None, ""
    return path, text


def parse_bean_text(text):
    """(title, body) of a bean file. The body is everything after the front matter."""
    m = re.search(r"^title:\s*(.*)$", text, re.M)
    parts = text.split("---", 2)
    body = parts[2] if text.startswith("---") and len(parts) == 3 else text
    return (m.group(1).strip().strip("'\"") if m else ""), body


def bean_from_main(root, bean):
    """(title, body) of .beans/<bean>--*.md on main in root, or ('', '')."""
    _, text = bean_text(root, "main", bean)
    return parse_bean_text(text)


# --- Rebased-commit identity -----------------------------------------------------------------------------------------
# A SHA written into a bean before landing goes stale when the lander rebases or cherry-picks the work: the bean then
# names a commit that is not on main, and the landed check reopens a bean whose code did land. After the rewrite, a
# token in a landed bean that names a commit outside the landing head's history is replaced by the ONE commit there
# that carries the same work: identical author name, email and timestamp, identical message (a `cherry picked from`
# trailer aside) and an identical whitespace-preserving patch id, read as bytes end to end. A token with no unique
# equivalent stays as written, so a commit that never landed is still visible to every check that judges it.
HEX = re.compile(r'[0-9a-f]{7,40}')
PICKED = re.compile(rb'\n*\(cherry picked from commit [0-9a-f]{40}\)\n*\Z')


def _git(root, *args, binary=False, check=True):
    p = subprocess.run(['git', '-C', str(root), *args], capture_output=True, timeout=60, check=check)
    return p.stdout if binary else p.stdout.decode('utf-8', 'replace').strip()


def _commit_of(root, token):
    p = subprocess.run(['git', '-C', str(root), 'rev-parse', '--verify', '-q', token + '^{commit}'], capture_output=True, timeout=30)
    return p.stdout.decode().strip() if p.returncode == 0 else None


def _is_ancestor(root, sha, of):
    return subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', sha, of], capture_output=True, timeout=30).returncode == 0


def patch_id(root, sha):
    """`--verbatim` keeps every byte of the changed lines (`--stable` strips whitespace, so an indentation-only change
    with different behaviour would collide) and still ignores hunk line numbers, which is what a clean rebase changes."""
    diff = _git(root, 'show', '--format=', '--no-color', '--no-ext-diff', '--binary', '--full-index', sha, binary=True)
    if not diff.strip():
        return None
    out = subprocess.run(['git', 'patch-id', '--verbatim'], input=diff, capture_output=True, timeout=60).stdout.split()
    return out[0].decode('ascii') if out else None


def identity(root, sha):
    """What a rebase or cherry-pick carries over: author name, email, timestamp and the message, as bytes."""
    raw = _git(root, 'show', '-s', '--format=%an%x00%ae%x00%at%x00%B', sha, binary=True).rstrip(b'\n')
    return PICKED.sub(b'', raw)


def rebased_equivalent(root, sha, head, upstream):
    """The one commit in upstream..head made from `sha`, or None when there is none or more than one."""
    pid = patch_id(root, sha)
    if not pid:
        return None
    me = identity(root, sha)
    candidates = _git(root, 'rev-list', '--no-merges', f'{upstream}..{head}').split()
    matches = [h for h in candidates if identity(root, h) == me and patch_id(root, h) == pid]
    return matches[0] if len(matches) == 1 else None


def remap_rebased_shas(root, upstream='origin/main', landed_bean=None):
    """Rewrite stale commit tokens in the beans this landing carries (upstream...HEAD) and, for `landed_bean`, append
    `LANDED_COMMITS: <shas>` naming exactly what lands. Commits the change as one bean-only commit in `root`.
    Returns {old token: new token}. Refuses (ValueError) to overwrite an uncommitted bean edit."""
    from pathlib import Path
    head = _git(root, 'rev-parse', 'HEAD')
    published = _git(root, 'rev-list', '--reverse', '--no-merges', f'{upstream}..HEAD').split()
    paths = [p for p in _git(root, 'diff', '--name-only', f'{upstream}...HEAD', '--', '.beans/').splitlines() if p.endswith('.md')]
    if landed_bean:
        own = [p for p in _git(root, 'ls-files', '--', '.beans/').splitlines() if Path(p).name.startswith(landed_bean + '--')]
        paths += [p for p in own if p not in paths]
    mapping, changed = {}, []
    for relative in paths:
        path = Path(root) / relative
        if not path.is_file() or path.is_symlink():
            continue
        text = path.read_text(encoding='utf-8')
        for token in dict.fromkeys(HEX.findall(text)):
            if token in mapping:
                continue
            sha = _commit_of(root, token)
            if not sha or _is_ancestor(root, sha, head):
                continue
            new = rebased_equivalent(root, sha, head, upstream)
            if new:
                mapping[token] = new[:max(len(token), 10)]
        rewritten = HEX.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)
        if landed_bean and published and Path(relative).name.startswith(landed_bean + '--'):
            rewritten = rewritten.rstrip('\n') + '\n\nLANDED_COMMITS: ' + ' '.join(published) + '\n'
        if rewritten != text:
            if _git(root, 'status', '--porcelain', '--', relative):
                raise ValueError(f'uncommitted bean evidence in {relative}')
            path.write_text(rewritten, encoding='utf-8')
            changed.append(relative)
    if changed:
        beans = sorted({Path(p).name.split('--', 1)[0] for p in changed})
        _git(root, 'commit', '-q', '--no-verify', '--only', '-m', 'chore(beans): name the landed commits in ' + ' '.join(beans), '--', *changed)
    return mapping


if __name__ == '__main__':
    import sys
    if not 3 <= len(sys.argv) <= 5 or sys.argv[1] != 'remap':
        sys.exit('usage: fleet_beans.py remap <worktree> [<upstream> [<landed-bean>]]  (upstream defaults to origin/main)')
    root, upstream, landed = sys.argv[2], (sys.argv[3:4] or ['origin/main'])[0], (sys.argv[4:5] or [None])[0]
    try:
        for old, new in remap_rebased_shas(root, upstream, landed).items():
            print(f'remapped {old} -> {new}')
    except (ValueError, subprocess.CalledProcessError) as err:
        sys.exit(f'remap refused: {err}')
