"""Review-report parsing shared by the fleet tools (fleet regression).

fleet-cycle-detect groups a bean's review reports into rounds; fleet-precedent reads the same reports
to show a landed bean's CHANGES findings and the revision they reviewed. One copy of the tuned
header/finding patterns, so the two tools cannot disagree about what a report says.
"""
import os
import re

SEVERITY = r"(CRITICAL|HIGH|MEDIUM|LOW)"
# "## HIGH 2 — title", "### LOW-1 — title", "## MEDIUM — title", "- **HIGH** title"
FINDING = re.compile(r"^\s*(?:#{2,4}\s+|[-*]\s+\*\*)" + SEVERITY + r"\b[\s\-\d.:*)]*(?:[—–-]\s*)?(.*)$")
VERDICT = re.compile(r"VERDICT\W{0,4}(APPROVE|CHANGES)", re.I)
NOT_A_FINDING = re.compile(r"\b(resolved|pre-existing|not introduced here)\b", re.I)

# The commit a review names as the one it reviewed. Reports are free prose, so only explicit forms count, in rank
# order: the coder's branch followed by its commit ("fleet/x-bean-y`, tip `abc1234`", "@ abc1234", "= `abc1234`",
# "(`abc1234`)", "at commit abc1234", "resolves to abc1234"), then a labelled tip ("Branch tip:", "Reviewed commit:",
# "Reviewed head:", "**Commit:**", "Tip `abc1234`"). A bare "at"/"commit" mention (a base, an earlier round's tip,
# a main SHA) is not one: the report then names no commit and joins its coder's round. Measured on 120 hand-labelled
# report headers (cycle-detect/eval-2026-09-29.json).
_SHA = r"`?([0-9a-f]{7,40})\b"
REVIEWED = [
    re.compile(r"fleet/[\w.\-]+`?\s*(?:,\s*tip(?: commit)?|@|=|\((?:tip commit|commit)?|at(?: commit)?|resolves to)\s*" + _SHA),
    re.compile(r"(?i)(?:branch tip(?: tested)?|reviewed (?:commit|head)|\*\*(?:commit|sha):?\*\*:?|(?<!round-\d )(?<!my )\btip(?: commit)?)"
               r"\s*[:=]?\s*" + _SHA),
]
HEADER_LINES = 15

def reviewed_commit(header):
    for pattern in REVIEWED:
        m = pattern.search(header)
        if m:
            return m.group(1)
    return None


def parse_report(path):
    """{'verdict', 'findings': [(severity, title)], 'commit'} of one review report."""
    verdict, findings, header = None, [], []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for n, line in enumerate(f):
                if n < HEADER_LINES:
                    header.append(line)
                m = FINDING.match(line)
                if m and m.group(2).strip() and not NOT_A_FINDING.search(line):
                    findings.append((m.group(1), m.group(2).strip()))
                v = VERDICT.search(line)
                if v:
                    verdict = v.group(1).upper()
    except OSError:
        pass
    return {"verdict": verdict, "findings": findings, "commit": reviewed_commit("".join(header))}


def reports_for(bean, reports_dir, until=None):
    """The bean's review reports in time order (optionally only those written by `until`, an epoch)."""
    pat = re.compile(r"(^|[^a-z0-9])" + re.escape(bean) + r"([^a-z0-9]|$)")
    names = [n for n in os.listdir(reports_dir) if n.endswith(".md") and "review" in n and pat.search(n)]
    paths = [os.path.join(reports_dir, n) for n in names]
    if until is not None:
        paths = [p for p in paths if os.path.getmtime(p) <= until]
    return sorted(paths, key=os.path.getmtime)


SUBMITTER = re.compile(r"-by-([a-z0-9][a-z0-9-]*?)(?:-coder)?\.md$")


def same_commit(a, b):
    return a is None or b is None or a.startswith(b) or b.startswith(a)


def rounds_of(paths):
    """Group time-ordered reports into submissions: consecutive reports of the same coder's attempt of the same
    commit are one round (findings combined; CHANGES if any report says so). The same coder naming a different
    reviewed commit is a resubmission, a new round; a report naming no commit joins its coder's round. A report
    naming no coder is a round of its own."""
    rounds = []
    for p in paths:
        rep = parse_report(p)
        m = SUBMITTER.search(os.path.basename(p))
        who = m.group(1) if m else None
        if rounds and who is not None and rounds[-1]["who"] == who and same_commit(rounds[-1]["commit"], rep["commit"]):
            last = rounds[-1]
            last["commit"] = last["commit"] or rep["commit"]
            last["findings"] += rep["findings"]
            last["reports"].append(os.path.basename(p))
            if rep["verdict"] == "CHANGES" or last["verdict"] is None:
                last["verdict"] = rep["verdict"] if rep["verdict"] == "CHANGES" else (last["verdict"] or rep["verdict"])
        else:
            rounds.append({"who": who, "commit": rep["commit"], "verdict": rep["verdict"], "findings": list(rep["findings"]),
                           "reports": [os.path.basename(p)]})
    return rounds


# Ops text only: strip anything shaped like a credential before it leaves the machine.
REDACT = re.compile(r"\b(?:sk-|ghp_|gho_|github_pat_|xox[abp]-|AKIA|ASIA)[A-Za-z0-9_\-]{8,}"
                    r"|(?i:\b\w*(?:key|token|secret|passw(?:or)?d|pass|pwd|credential)\w*\s*[:=]\s*\S+)"
                    r"|://[^/\s:@]+:[^@\s]+@"  # userinfo in a URL
                    r"|(?i:\bauthorization\s*:\s*\S+(?:\s+\S+)?)|(?i:\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,})"
                    r"|[\w.+-]+@[\w-]+\.[\w.]+")


# A list of known key prefixes always misses the next one, so a token is also dropped by its SHAPE: a long run with a
# segment that mixes several digits and letters (a random key, a hash, a base64 blob) or a JWT. Identifiers and paths
# survive (measured on the labelled history's 680 live findings: only bean slugs and a migration name are dropped).
TOKEN = re.compile(r"[A-Za-z0-9_+/=.\-]{20,}")


def _secret_shaped(token):
    for seg in re.split(r"[/.]", token):
        if len(seg) >= 16 and (seg.startswith("eyJ") or
                               (sum(c.isdigit() for c in seg) >= 4 and sum(c.isalpha() for c in seg) >= 4)):
            return True
    return False


def redact(text):
    text = REDACT.sub("", text)
    return TOKEN.sub(lambda m: "" if _secret_shaped(m.group(0)) else m.group(0), text)
