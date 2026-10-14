#!/usr/bin/env bash
# Runs the fleet-dispatch fixture-board tests (python unittest) and a wrapper smoke test: the wrapper must source drover-env and reach the tool.
set -uo pipefail
D="$(cd "$(dirname "$0")" && pwd)"; BIN="$D/../bin"; pass=0; failn=0
ok(){ pass=$((pass+1)); echo "ok   $1"; }; bad(){ failn=$((failn+1)); echo "FAIL $1"; }
out=$(python3 "$D/fleet_dispatch_test.py" 2>&1); echo "$out" | tail -3
echo "$out" | tail -1 | grep -q '^OK' && ok "fixture-board unit tests" || bad "fixture-board unit tests"
T=$(mktemp -d); mkdir -p "$T/beans" "$T/config" "$T/reports"
printf -- '---\ntitle: t\nstatus: todo\ntype: task\npriority: normal\ncreated_at: 2026-01-01T00:00:00Z\n---\nplain bean without a checklist\n' > "$T/beans/bean-0001--x.md"
echo bean-0001 > "$T/ready.txt"; echo '[{"name":"seat-one","status":"idle","branch":""}]' > "$T/seats.json"
o=$(DROVER_CONFIG=/nonexistent FLEET_DISPATCH_FIXTURE="$T" "$BIN/fleet-dispatch" --dry-run 2>&1); echo "$o" | head -3
echo "$o" | grep -q 'board_ready=1 dispatchable=1' && echo "$o" | grep -q 'would assign bean-0001 -> seat-one' && ok "wrapper reaches the tool and plans the assignment" || bad "wrapper smoke test"
echo "passed=$pass failed=$failn"; [ "$failn" = 0 ]
