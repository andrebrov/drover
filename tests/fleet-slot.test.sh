#!/usr/bin/env bash
# Tests for fleet-slot's push pool: three push slots by default (parallel, never one at a time), a fourth gate waits, 0 means no limit.
# Scratch state directory. Usage: bash fleet-slot.test.sh [bin-dir]
set -uo pipefail
BIN="$(cd "${1:-$(dirname "$0")/../bin}" && pwd)"; T=$(mktemp -d); pass=0; failn=0
ok(){ pass=$((pass+1)); echo "ok   $1"; }; bad(){ failn=$((failn+1)); echo "FAIL $1"; }
export DROVER_HOME="$T/home" DROVER_CONFIG=/nonexistent; unset FLEET_PUSH_SLOTS FLEET_HEAVY_SLOTS FLEET_SLOT_HELD
"$BIN/fleet-slot" status | grep -c '^slot-p[0-9] push' | grep -qx 3 && ok "default push pool is three slots (p1..p3)" || bad "default push pool is not three slots"
pids=(); for i in 1 2 3; do "$BIN/fleet-slot" run --push -- sleep 8 >/dev/null 2>&1 & pids+=($!); done; sleep 2
n=$("$BIN/fleet-slot" status | grep -c 'push    BUSY'); [ "$n" = 3 ] && ok "three push gates hold three slots at the same time" || bad "expected 3 busy push slots, got $n"
( timeout 3 "$BIN/fleet-slot" run --push -- echo fourth > "$T/out4" 2>&1 ) & sleep 3.5
grep -qx 'fourth' "$T/out4" && bad "a fourth push gate ran while three were held" || ok "KNOWN-BAD: a fourth push gate waits instead of running"
"$BIN/fleet-slot" run -- echo general | grep -qx general && ok "a general run is not blocked by the push pool" || bad "general run blocked"
for p in "${pids[@]}"; do pkill -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; done; sleep 1
FLEET_PUSH_SLOTS=0 "$BIN/fleet-slot" run --push -- echo nolimit | grep -qx nolimit && ok "FLEET_PUSH_SLOTS=0 runs the gate with no slot" || bad "FLEET_PUSH_SLOTS=0 did not run directly"
FLEET_PUSH_SLOTS=1 "$BIN/fleet-slot" status | grep -c '^slot-p[0-9] push' | grep -qx 1 && ok "FLEET_PUSH_SLOTS=1 restores a single push slot" || bad "FLEET_PUSH_SLOTS override ignored"
wait 2>/dev/null; echo "passed=$pass failed=$failn"; [ "$failn" = 0 ]
