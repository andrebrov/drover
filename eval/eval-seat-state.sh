#!/usr/bin/env bash
# eval-seat-state — score the shipped detector (`bin/fleet-judge seat`) against labeled pane tails.
#
# It sends every fixture in panes/ and dialogs/ to api.typesafe.ai through fleet-judge, so it needs
# ~/.config/typesafe/api_key. The fixtures are real pane tails with paths, ids and task text rewritten.
# Labels are in labels.tsv (read off the live panes by the human lead, 2026-09-18).
#
# Two numbers matter, and they are different claims:
#   accuracy  — top-1 label vs truth over all fixtures
#   firings   — what fleet-watch would ACT on: a non-idle, non-working state at confidence >= 0.8.
#               A wrong firing strands a bean or pages the human; this is the number to keep at 0 false.
set -euo pipefail
cd "$(dirname "$0")"
python3 - <<'PY'
import subprocess, concurrent.futures as cf
rows = [l.rstrip('\n').split('\t') for l in open('labels.tsv') if l.strip() and not l.startswith('#')]
def judge(row):
    path, truth = row
    r = subprocess.run(['../bin/fleet-judge', 'seat', path], capture_output=True, text=True)
    if r.returncode != 0:
        return path, truth, None, 0.0
    cls, conf = r.stdout.split()
    return path, truth, cls, float(conf)
with cf.ThreadPoolExecutor(8) as ex:
    res = list(ex.map(judge, rows))
if all(r[2] is None for r in res):
    raise SystemExit('fleet-judge returned nothing for every fixture (no key? no network?) — no result')
ok = fire_ok = fire_bad = 0
for path, truth, pred, conf in sorted(res, key=lambda r: r[1]):
    good = pred == truth; ok += good
    fires = pred not in (None, 'idle', 'working') and conf >= 0.8
    if fires: fire_ok += good; fire_bad += not good
    print(f"{'ok ' if good else 'BAD'} {truth:22} -> {str(pred):22} conf={conf:.2f}{'  FIRES' if fires else ''}  {path}")
print(f"accuracy {ok}/{len(res)}; firings at conf>=0.8: {fire_ok} right, {fire_bad} wrong")
PY
