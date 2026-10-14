#!/usr/bin/env bash
# Tests for fleet-ask / fleet-answer: a seat question is answered with zero keystrokes into its pane. Known-bad and known-good for every rule.
# A stub `herdr` records its argv; scratch directories only. Usage: bash fleet-qa.test.sh [bin-dir]   (TEST_VERBOSE=1 shows failing output)
set -uo pipefail
BIN="$(cd "${1:-$(dirname "$0")/../bin}" && pwd)"; T=$(mktemp -d); pass=0; failn=0
ok(){ pass=$((pass+1)); echo "ok   $1"; }; bad(){ failn=$((failn+1)); echo "FAIL $1"; }
export DROVER_HOME="$T/home" DROVER_CONFIG=/nonexistent DROVER_BEAN_PREFIX=bean- DROVER_REPO="$T/repo" FLEET_QA_DIR="$T/qa"
mkdir -p "$DROVER_REPO/.beans" "$T/qa"; printf -- '---\nstatus: todo\n---\nbody\n' > "$DROVER_REPO/.beans/bean-abcd--fixture.md"
cat > "$T/herdr" <<S
#!/bin/sh
echo "\$@" >> "$T/calls"
[ -n "\$HERDR_FAIL" ] && exit 1
exit 0
S
chmod +x "$T/herdr"; export HERDR_BIN="$T/herdr"; unset HERDR_FAIL
code(){ "$@" >/dev/null 2>&1; echo $?; }
expect(){ local name=$1 want=$2; shift 2; local got; got=$(code "$@"); if [ "$got" = "$want" ]; then ok "$name"; else bad "$name (want exit $want, got $got)"; fi; }
ask(){ "$BIN/fleet-ask" seat-one bean-abcd "Use A or B?" "$@"; }
qid=$(ask A B); [ -n "$qid" ] && ok "ask prints a question id" || bad "ask prints a question id"
[ -f "$FLEET_QA_DIR/questions/$qid-a.json" ] && grep -q '"options"' "$FLEET_QA_DIR/questions/$qid-a.json" && ok "question file written with its options" || bad "question file written"
"$BIN/fleet-ask" --open | grep -q "$qid" && ok "open question listed with age" || bad "open question listed"
"$BIN/fleet-answer" "$qid" 2 --rationale cheaper --by lead >/dev/null 2>&1; rc=$?
[ $rc = 0 ] && grep -q '"decision": "B"' "$FLEET_QA_DIR/decisions/$qid-a.json" && ok "KNOWN-GOOD: option number 2 recorded as B" || bad "answer by number (rc=$rc)"
grep -q "A (lead): B" "$DROVER_REPO/.beans/bean-abcd--fixture.md" && ok "decision appended to the bean" || bad "decision appended to the bean"
[ "$(wc -l < "$T/calls" | tr -d ' ')" = 1 ] && grep -q '^agent prompt seat-one ' "$T/calls" && ! grep -qE 'send-keys|send-text' "$T/calls" && ok "seat woken once by prompt, no keystroke verbs" || bad "wake call shape"
[ -z "$("$BIN/fleet-ask" --open)" ] && ok "answered question no longer open" || bad "answered question still open"
before=$(cat "$FLEET_QA_DIR/decisions/$qid-a.json")
expect "KNOWN-BAD: a second answer is refused (exit 3)" 3 "$BIN/fleet-answer" "$qid" A
[ "$(cat "$FLEET_QA_DIR/decisions/$qid-a.json")" = "$before" ] && [ "$(wc -l < "$T/calls" | tr -d ' ')" = 1 ] && ok "refused answer changed nothing and woke nobody" || bad "refused answer changed state"
expect "unknown question id -> exit 2" 2 "$BIN/fleet-answer" bean-abcd-nope A
expect "not a bean id -> exit 2" 2 "$BIN/fleet-ask" seat-one not-a-bean q
expect "empty question -> exit 2" 2 "$BIN/fleet-ask" seat-one bean-abcd "  "
sleep 1; q2=$(ask); "$BIN/fleet-answer" "$q2" neither, do C >/dev/null 2>&1
grep -q '"decision": "neither, do C"' "$FLEET_QA_DIR/decisions/$q2-a.json" && ok "free text recorded verbatim" || bad "free text recorded"
sleep 1; q3=$(ask); HERDR_FAIL=1 "$BIN/fleet-answer" "$q3" go >/dev/null 2>"$T/err"; rc=$?
[ $rc = 4 ] && grep -q -- '--wake' "$T/err" && ok "failed wake exits 4 and names --wake" || bad "failed wake (rc=$rc)"
expect "decision stays single-fire after a failed wake" 3 "$BIN/fleet-answer" "$q3" other
expect "--wake re-sends without rewriting" 0 "$BIN/fleet-answer" --wake "$q3"
echo "passed=$pass failed=$failn"; [ "$failn" = 0 ]
