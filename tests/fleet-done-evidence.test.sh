#!/usr/bin/env bash
# Tests for fleet-done's evidence gate: --check NAME=EXIT, --sha, --status. Known-bad (claims more than ran) is downgraded to PARTIAL, known-good passes.
# Scratch repo with a bare origin; touches nothing real. Usage: bash fleet-done-evidence.test.sh [bin-dir]   (TEST_VERBOSE=1 shows failing output)
set -uo pipefail
BIN="$(cd "${1:-$(dirname "$0")/../bin}" && pwd)"; T=$(mktemp -d); pass=0; failn=0
ok(){ pass=$((pass+1)); echo "ok   $1"; }; bad(){ failn=$((failn+1)); echo "FAIL $1"; }
export DROVER_HOME="$T/home" DROVER_CONFIG=/nonexistent DROVER_BEAN_PREFIX=bean- DROVER_PUBLISH_MODE_FILE="$T/mode" DROVER_WORKTREES="$T/wts"
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
unset GIT_DIR GIT_WORK_TREE FLEET_DONE_STRICT
mkdir -p "$DROVER_HOME/inbox" "$DROVER_HOME/reports"; echo direct > "$T/mode"
git init -q --bare "$T/origin.git"; git clone -q "$T/origin.git" "$T/repo" 2>/dev/null
cd "$T/repo"; git config user.email t@t; git config user.name t; git checkout -q -b main
echo base > f; git add -A; git commit -q -m base; git push -q origin main; export DROVER_REPO="$T/repo"
landed=$(git rev-parse HEAD); git checkout -q -b side; echo x > x; git add -A; git commit -q -m side; unlanded=$(git rev-parse HEAD); git checkout -q main
fd(){ "$BIN/fleet-done" "$@"; }
last(){ ls -t "$DROVER_HOME"/inbox/* | head -1 | xargs cut -f4,5; }
code(){ "$@" >/dev/null 2>&1; echo $?; }
expect(){ local name=$1 want=$2; shift 2; local got; got=$(code "$@"); if [ "$got" = "$want" ]; then ok "$name"; else bad "$name (want exit $want, got $got)"; fi; }
check(){ local name=$1 pat=$2; shift 2; fd "$@" >/dev/null 2>&1; if last | grep -q "$pat"; then ok "$name"; else bad "$name ($(last))"; fi; }
check "no flags and no FLEET_DONE_STRICT keeps the legacy DONE" '^DONE' seat-coder bean-aaaa DONE all
FLEET_DONE_STRICT=1 fd seat-coder bean-aaaa DONE all >/dev/null 2>&1; last | grep -q '^PARTIAL.*no passing build check.*no passing test check' && ok "KNOWN-BAD: strict DONE with no checks -> PARTIAL with the reason" || bad "strict DONE no checks ($(last))"
check "KNOWN-BAD: build only, no test -> PARTIAL" '^PARTIAL.*no passing test check' seat-coder bean-aaaa DONE all --check build=0
check "KNOWN-BAD: a failing test exit -> PARTIAL naming it" '^PARTIAL.*test exited 1' seat-coder bean-aaaa DONE all --check build=0 --check test=1
check "KNOWN-GOOD: build=0 and test=0 -> DONE, checks recorded" '^DONE.*checks: build=0 test=0' seat-coder bean-aaaa DONE all --check build=0 --check test=0
check "KNOWN-BAD: LANDED with a sha that is not on origin/main -> PARTIAL" '^PARTIAL.*not an ancestor of origin/main' seat-coder bean-aaaa DONE all --status landed --sha "$unlanded" --check build=0 --check test=0
check "KNOWN-GOOD: LANDED with an ancestor sha -> DONE" '^DONE.*status=landed' seat-coder bean-aaaa DONE all --status landed --sha "$landed" --check build=0 --check test=0
check "KNOWN-BAD: LANDED with no --sha -> PARTIAL" '^PARTIAL.*without --sha' seat-coder bean-aaaa DONE all --status landed --check build=0 --check test=0
expect "an unknown --status is refused -> exit 2" 2 fd seat-coder bean-aaaa DONE all --status shipped
expect "a malformed --check is refused -> exit 2" 2 fd seat-coder bean-aaaa DONE all --check build
expect "a non-numeric --check exit is refused -> exit 2" 2 fd seat-coder bean-aaaa DONE all --check build=ok
check "BLOCKED is never downgraded" '^BLOCKED' seat-coder bean-aaaa BLOCKED "need a decision"
echo "passed=$pass failed=$failn"; [ "$failn" = 0 ]
