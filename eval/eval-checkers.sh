#!/usr/bin/env bash
# eval-checkers — known-good and known-bad cases for the deterministic checkers, on a scratch repo.
#
# Every instrument ships with a case it must fire on AND a case it must stay quiet on (docs/LESSONS.md):
#   fleet-landed-check   completed bean with no landed code -> UNPROVEN; landed by commit / by named SHA /
#                        `Landed: none` -> quiet
#   fleet-review-check   a CLI banner with no review -> EMPTY; a grounded review, or a seat report whose name
#                        merely contains "reviewer" -> quiet
#   fleet-claim reap     a claim on a bean missing from main: under 24h kept, older released
#   fleet-slot           re-entry under FLEET_SLOT_HELD does not wait; a held general slot blocks a second run
# No network, no keys. Exit 1 on the first wrong answer.
set -uo pipefail
BIN="$(cd "$(dirname "$0")/../bin" && pwd)"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export DROVER_CONFIG=/dev/null DROVER_HOME="$T/home" DROVER_REPO="$T/repo" FLEET_STATE="$T/home/state"
export FLEET_REPORTS="$T/home/reports" FLEET_TASKS="$T/home/tasks"
mkdir -p "$FLEET_STATE" "$FLEET_REPORTS" "$FLEET_TASKS"
fail=0
ok(){ echo "ok    $*"; }
bad(){ echo "FAIL  $*"; fail=1; }

# --- scratch repo with an origin -----------------------------------------------------------------------------
git init -q --bare "$T/origin.git"
git init -q -b main "$DROVER_REPO"; cd "$DROVER_REPO" || exit 1
git config user.email eval@example.invalid; git config user.name eval
mkdir -p .beans src
bean(){ printf -- '---\ntitle: %s\nstatus: %s\n---\n%s\n' "$1" "$2" "${3:-}" > ".beans/$1--t.md"; }
echo 1 > src/a.txt; git add -A; git commit -qm init
git remote add origin "$T/origin.git"; git push -q origin main
# bean-good1: completed in the same commit that changes code and names it
echo 2 > src/a.txt; bean bean-good1 completed; git add -A; git commit -qm 'fix: thing (bean-good1)'
# bean-good2: completed, body names a SHA that is on main
sha=$(git rev-parse --short HEAD); bean bean-good2 completed "Landed in $sha."; git add -A; git commit -qm 'chore(beans): close'
# bean-good3: completed with no code, declared
bean bean-good3 completed 'Landed: none — a decision record'; git add -A; git commit -qm 'chore(beans): decide'
# bean-bad1: completed, names nothing, only a bean-file commit mentions it
bean bean-bad1 completed; git add -A; git commit -qm 'chore(beans): close bean-bad1'
# bean-bad2: completed, names a SHA that exists but is not on main
git checkout -qb side; echo x > src/b.txt; git add -A; git commit -qm side; side=$(git rev-parse --short HEAD)
git checkout -q main; bean bean-bad2 completed "Landed in $side."; git add -A; git commit -qm 'chore(beans): close'
# bean-todo: not completed -> never checked
bean bean-todo todo; git add -A; git commit -qm 'chore(beans): file'
git push -q origin main; git fetch -q origin

out=$("$BIN"/fleet-landed-check --hours 24); rc=$?
for b in bean-bad1 bean-bad2; do grep -q "^UNPROVEN $b:" <<<"$out" && ok "landed-check fires on $b" || bad "landed-check missed $b"; done
for b in bean-good1 bean-good2 bean-good3 bean-todo; do grep -q "$b" <<<"$out" && bad "landed-check flagged $b" || ok "landed-check quiet on $b"; done
[ "$rc" = 1 ] && ok "landed-check exit 1 on violations" || bad "landed-check exit $rc"

# --- review-check --------------------------------------------------------------------------------------------
printf 'Reading additional input from stdin...\nOpenAI Codex v0.50.0\n--------\nworkdir: /x\n' > "$FLEET_REPORTS/s-bean-x-review.out"
{ echo 'VERDICT: CHANGES'; echo 'src/a.ts:12 reads the status before the write commits; a failed write records success.'
  echo 'src/b.ts:40 swallows the error and returns an empty list, which the caller renders as "no data".'
  echo 'Fix both, add a test where the dependency throws.'; } > "$FLEET_REPORTS/s-bean-y-review.md"
echo 'short note' > "$FLEET_REPORTS/codex-reviewer-self-serve.md"
out=$("$BIN"/fleet-review-check --minutes 5)
grep -q '^EMPTY s-bean-x-review.out' <<<"$out" && ok 'review-check fires on a banner-only review' || bad 'review-check missed banner-only review'
grep -q 's-bean-y-review' <<<"$out" && bad 'review-check flagged a grounded review' || ok 'review-check quiet on a grounded review'
grep -q 'self-serve' <<<"$out" && bad 'review-check flagged a reviewer seat report' || ok 'review-check quiet on a seat report named *-reviewer-*'

# --- fleet-claim reap: young-branch-bean ---------------------------------------------------------------------
now=$(date -u +%FT%TZ); old=$(date -u -v-2d +%FT%TZ)
printf 'c1\tbean-new1\tsrc/young.txt\t%s\nc2\tbean-gone\tsrc/old.txt\t%s\n' "$now" "$old" > "$DROVER_HOME/claims.tsv"
out=$("$BIN"/fleet-claim reap)
grep -q 'released src/old.txt' <<<"$out" && ok 'claim reap releases an old claim on a missing bean' || bad 'claim reap kept an old missing-bean claim'
grep -q 'src/young.txt' "$DROVER_HOME/claims.tsv" && ok 'claim reap keeps a young claim on a branch-only bean' || bad 'claim reap dropped a young claim'

# --- fleet-slot ----------------------------------------------------------------------------------------------
export FLEET_HEAVY_SLOTS=2   # one general slot (slot-2)
FLEET_SLOT_HELD=x "$BIN"/fleet-slot run -- true && ok 'slot re-entry runs directly' || bad 'slot re-entry failed'
"$BIN"/fleet-slot run -- sleep 3 & holder=$!; sleep 1
s=$SECONDS; "$BIN"/fleet-slot run -- true 2>/dev/null; waited=$((SECONDS-s)); wait $holder
[ "$waited" -ge 1 ] && ok "second general run waited for the slot (${waited}s)" || bad 'second general run did not wait'
"$BIN"/fleet-slot run -- sleep 3 & holder=$!; sleep 1
s=$SECONDS; "$BIN"/fleet-slot run --push -- true; waited=$((SECONDS-s)); wait $holder
[ "$waited" -lt 1 ] && ok 'a push does not wait behind a general run' || bad "push waited ${waited}s behind a general run"

[ "$fail" = 0 ] && echo 'all checker cases pass' || { echo 'checker cases FAILED'; exit 1; }
