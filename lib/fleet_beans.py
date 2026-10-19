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
