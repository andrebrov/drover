#!/usr/bin/env bash
# Tests for the role briefs (short, and pointed at by every generated seat brief) and fleet-rules-sync (generated copies cannot drift).
# Known-bad cases fail, known-good pass. Scratch directories only. Usage: bash roles-and-rules.test.sh [bin-dir]
set -uo pipefail
BIN="$(cd "${1:-$(dirname "$0")/../bin}" && pwd)"; SRC="$(cd "$BIN/.." && pwd)"; T=$(mktemp -d); pass=0; failn=0
ok(){ pass=$((pass+1)); echo "ok   $1"; }; bad(){ failn=$((failn+1)); echo "FAIL $1"; }
code(){ "$@" >/dev/null 2>&1; echo $?; }
expect(){ local name=$1 want=$2; shift 2; local got; got=$(code "$@"); if [ "$got" = "$want" ]; then ok "$name"; else bad "$name (want exit $want, got $got)"; fi; }
# role briefs
for r in coder reviewer architect lead; do
  f="$SRC/skills/roles/$r.md"; if [ -f "$f" ] && [ "$(wc -l < "$f" | tr -d ' ')" -le 30 ]; then ok "role brief $r.md exists and is <= 30 lines"; else bad "role brief $r.md missing or > 30 lines"; fi
done
t=$T/long.md; seq 1 31 > "$t"; if [ "$(wc -l < "$t" | tr -d ' ')" -le 30 ]; then bad "KNOWN-BAD: a 31-line file passed the length check"; else ok "KNOWN-BAD: a 31-line file fails the length check"; fi
for fn in coder reviewer; do
  if awk -v f="$fn" 'index($0,"Role rules (short, read first)")&&index($0,"roles/" f ".md"){found=1} END{exit !found}' "$BIN/fleet"; then ok "fleet's $fn brief points at roles/$fn.md"; else bad "fleet's $fn brief does not point at roles/$fn.md"; fi
done
# rules sync
export DROVER_REPO="$T/repo"; mkdir -p "$DROVER_REPO/.cursor/rules"
printf -- '---\ndescription: a\nalwaysApply: true\n---\n\n# Alpha\nRule one.\n' > "$DROVER_REPO/.cursor/rules/alpha.mdc"
printf -- '---\n---\npackage rule\n' > "$DROVER_REPO/.cursor/rules/tessl__x.mdc"
expect "KNOWN-BAD: --check fails while the generated copy is missing" 1 "$BIN/fleet-rules-sync" --check
expect "generate" 0 "$BIN/fleet-rules-sync"
head -1 "$DROVER_REPO/.grok/rules/alpha.md" | grep -q 'Source: .cursor/rules/alpha.mdc' && ok "generated copy names its source" || bad "generated copy header"
[ ! -e "$DROVER_REPO/.grok/rules/tessl__x.md" ] && ok "package-managed rules are not mirrored" || bad "package rule was mirrored"
expect "KNOWN-GOOD: --check passes on freshly generated copies" 0 "$BIN/fleet-rules-sync" --check
echo "hand edit" >> "$DROVER_REPO/.grok/rules/alpha.md"
expect "KNOWN-BAD: a hand-edited copy fails --check" 1 "$BIN/fleet-rules-sync" --check
"$BIN/fleet-rules-sync" >/dev/null; echo x > "$DROVER_REPO/.grok/rules/orphan.md"
expect "KNOWN-BAD: an orphan copy fails --check" 1 "$BIN/fleet-rules-sync" --check
echo "passed=$pass failed=$failn"; [ "$failn" = 0 ]
