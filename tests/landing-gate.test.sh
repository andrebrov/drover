#!/usr/bin/env bash
# Tests for the landing enforcement: fleet-branch-landed (by-subject proof), fleet-done's landing gate (exit 7, BLOCKED to the lead,
# FLEET_UNLANDED_OK escape), fleet-refill's landing_audit (nudge once per tip at 30 min, alert at 60 min) and fleet-preflight's unlanded check.
# Real git, a bare repo as origin. Each case states its known-bad and known-good side.
set -u
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
unset GIT_DIR GIT_WORK_TREE
PASS=0; FAILN=0
ok()   { PASS=$((PASS+1)); echo "  ok   $*"; }
fail() { FAILN=$((FAILN+1)); echo "  FAIL $*"; }
eq()   { [ "$2" = "$3" ] && ok "$1" || fail "$1 (want '$3', got '$2')"; }
has()  { case "$2" in *"$3"*) ok "$1" ;; *) fail "$1 (no '$3' in: $(printf '%s' "$2" | tail -3 | tr '\n' '|'))" ;; esac; }

export DROVER_HOME=$T/home DROVER_CONFIG=/nonexistent DROVER_BEAN_PREFIX=bean- DROVER_PUBLISH_MODE_FILE=$T/mode DROVER_ROSTER="s-coder:claude"
git init -q --bare "$T/o.git"; git init -q -b main "$T/r"; cd "$T/r"; git remote add origin "$T/o.git"
echo base > base; git add -A; git commit -qm base; git push -q origin main
export DROVER_REPO=$T/r
git switch -q -c fleet/s-coder-bean-ab12
echo one > one; git add one; git commit -qm "feat(bean-ab12): slice one"
echo noise > noise; git add noise; git commit -qm "feat: names no bean"
D=$ROOT/bin
inbox() { cat "$DROVER_HOME"/inbox/*.msg 2>/dev/null; }

echo "T1 fleet-branch-landed: unlanded bean commit is reported with its age; a commit naming no bean is not"
out=$("$D/fleet-branch-landed" HEAD); rc=$?
eq "T1 exit 1" "$rc" 1; has "T1 AGE line first" "$(printf '%s' "$out" | head -1)" "AGE "; has "T1 names the subject" "$out" "slice one"
case "$out" in *"names no bean"*) fail "T1 noise commit must not be reported" ;; *) ok "T1 noise commit ignored" ;; esac

echo "T2 fleet-done refuses DONE in direct mode (exit 7), delivers BLOCKED UNLANDED, refuses unlanded BLOCKED too; allows CHANGES"
echo direct > "$T/mode"
"$D/fleet-done" s-coder bean-ab12 DONE "finished" >/dev/null 2>"$T/err"; rc=$?
eq "T2 DONE refused with exit 7" "$rc" 7; has "T2 refusal says why" "$(cat "$T/err")" "not on origin/main"; has "T2 lead receives BLOCKED UNLANDED" "$(inbox)" "BLOCKED"
has "T2 ...with the word UNLANDED" "$(inbox)" "UNLANDED"
rm -f "$DROVER_HOME"/inbox/*.msg
"$D/fleet-done" s-coder bean-ab12 BLOCKED "waiting for a decision" >/dev/null 2>&1; eq "T2 BLOCKED refuses unlanded commits" "$?" 7
FLEET_UNLANDED_OK="lead handles publication" "$D/fleet-done" s-coder bean-ab12 READY "finished" >/dev/null 2>&1
eq "T2 delegating publication is not a waiver" "$?" 7
"$D/fleet-done" s-coder bean-ab12 CHANGES "x" >/dev/null 2>&1; eq "T2 CHANGES allowed" "$?" 0
"$D/fleet-done" s-coder bean-ab12 READY "x" >/dev/null 2>&1; eq "T2 READY refused too" "$?" 7

echo "T3 escape hatch records its reason; lander mode does not gate"
rm -f "$DROVER_HOME"/inbox/*.msg
FLEET_UNLANDED_OK="needs an operator decision" "$D/fleet-done" s-coder bean-ab12 DONE "ok" >/dev/null 2>&1; eq "T3 escape hatch delivers" "$?" 0; has "T3 reason recorded in the summary" "$(inbox)" "UNLANDED WAIVED: needs an operator decision"
echo lander > "$T/mode"; "$D/fleet-done" s-coder bean-ab12 DONE "ok" >/dev/null 2>&1; eq "T3 lander mode: not gated" "$?" 0; echo direct > "$T/mode"

echo "T4 landing by REBASE (new SHAs) counts as landed: proof is by subject"
git fetch -q origin; git switch -q -c landing origin/main; git cherry-pick -x fleet/s-coder-bean-ab12~1 >/dev/null 2>&1 || git cherry-pick fleet/s-coder-bean-ab12~1 >/dev/null; git push -q origin HEAD:main; git switch -q fleet/s-coder-bean-ab12
"$D/fleet-branch-landed" HEAD >/dev/null; eq "T4 landed by subject despite a different SHA" "$?" 0
"$D/fleet-done" s-coder bean-ab12 DONE "now landed" >/dev/null 2>&1; eq "T4 DONE accepted once landed" "$?" 0

echo "T5 landing audit: nudge ONCE per tip at 30 min, alert at 60 min; preflight turns the alert into DEAD"
git switch -q -c fleet/s-coder-bean-cd34 origin/main
echo two > two; git add two; GIT_COMMITTER_DATE="$(date -v-90M +%s 2>/dev/null || date -d '90 min ago' +%s)" git commit -q --date="@$(date -v-90M +%s 2>/dev/null || date -d '90 min ago' +%s)" -m "feat(bean-cd34): old slice"
git switch -q main; mkdir -p "$T/wts"; git worktree add -q "$T/wts/fleet-s-coder" fleet/s-coder-bean-cd34; export DROVER_WORKTREES="$T/wts"
mkdir -p "$T/bin"; printf '#!/bin/sh\necho "$@" >> "%s/followups.log"\n' "$T" > "$T/bin/fleet"; chmod +x "$T/bin/fleet"
mkdir -p "$T/bin2"; cp "$D"/* "$T/bin2/" 2>/dev/null; cp "$T/bin/fleet" "$T/bin2/fleet"; cp -R "$ROOT/lib" "$T/lib"
run_audit() { python3 - <<PY
import importlib.machinery, importlib.util, os, time
os.environ['REFILL_EXTRA_SEATS'] = 's-coder'
l = importlib.machinery.SourceFileLoader('fr', '$T/bin2/fleet-refill'); m = importlib.util.module_from_spec(importlib.util.spec_from_loader('fr', l)); l.exec_module(m)
m.SEATS.append('s-coder'); m.mid_task = lambda seat, st=None: False
st = {}; m.landing_audit(st, time.time()); m.landing_audit(st, time.time())
PY
}
: > "$T/followups.log"; run_audit 2>/dev/null
eq "T5 exactly ONE nudge for two audits of the same tip" "$(grep -c LANDING "$T/followups.log")" 1
# The refill nudges; fleet-measure owns the snapshot and its stamped receipt.
[ ! -e "$DROVER_HOME/watch/state/alerts/unlanded.txt" ] && ok "T5 refill does not publish scoreboard counts" || fail "T5 refill wrote the scoreboard"
mkdir -p "$DROVER_HOME/watch/state/alerts"
echo "fleet/s-coder-bean-cd34: unlanded" > "$DROVER_HOME/watch/state/alerts/unlanded.txt"
touch "$DROVER_HOME/watch/state/alerts/unlanded.txt.ok"
has "T5 measured alert read at 60+ min" "$(cat "$DROVER_HOME/watch/state/alerts/unlanded.txt" 2>/dev/null)" "bean-cd34"
out=$("$D/fleet-preflight" 2>&1); has "T5 preflight DEAD on the unlanded branch" "$out" "DEAD  seat branches with bean commits unlanded > 60 min: fleet/s-coder-bean-cd34"
: > "$DROVER_HOME/watch/state/alerts/unlanded.txt"
has "T5 control: no alert, preflight ok" "$("$D/fleet-preflight" 2>&1)" "ok    seat branches with bean commits unlanded > 60 min: none"
rm -f "$DROVER_HOME/watch/state/alerts/unlanded.txt.ok"
has "T5 an audit that never completed reads as DEAD, not clean" "$("$D/fleet-preflight" 2>&1)" "DEAD  seat branches with bean commits unlanded > 60 min: no completed audit on record"

echo
echo "landing-gate: $PASS passed, $FAILN failed"
[ "$FAILN" -eq 0 ]
