#!/usr/bin/env bash
# Tests for fleet-bean-guard: known-bad (bean deletion, false completion) refused, known-good (ordinary edit, completion naming a landed SHA) allowed.
# Real git repos in a scratch directory. Usage: bash bean-guard.test.sh [bin-dir]   (TEST_VERBOSE=1 shows failing output)
set -uo pipefail
BIN="$(cd "${1:-$(dirname "$0")/../bin}" && pwd)"; T=$(mktemp -d); pass=0; failn=0
ok(){ pass=$((pass+1)); echo "ok   $1"; }; bad(){ failn=$((failn+1)); echo "FAIL $1"; }
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t; unset GIT_DIR GIT_WORK_TREE
git init -q --bare "$T/o.git"; git clone -q "$T/o.git" "$T/r" 2>/dev/null; cd "$T/r"; git checkout -q -b main
mkdir .beans; printf -- '---\nstatus: todo\n---\nbean one\n' > .beans/bean-0001--one.md; printf -- '---\nstatus: todo\n---\nbean two\n' > .beans/bean-0002--two.md
echo code > code.txt; git add -A; git commit -q -m base; git push -q origin main; base=$(git rev-parse HEAD)
git switch -q -c work; echo fix > fix.txt; git add -A; git commit -q -m "fix: the thing"; fix=$(git rev-parse --short=10 HEAD)
git switch -q main; git merge -q --ff-only work; git push -q origin main; landed=$(git rev-parse --short=10 HEAD)   # the fix is on main now
G(){ python3 "$BIN/fleet-bean-guard" "$T/r" origin/main "$@"; }
verdict(){ local name=$1 want=$2; shift 2; local out rc; out=$("$@" 2>&1); rc=$?; if [ "$rc" = "$want" ]; then ok "$name"; else bad "$name (want exit $want, got $rc: $(echo "$out" | tail -2 | tr '\n' '|'))"; fi; }
branch(){ git switch -q -c "$1" main; }
branch ordinary; sed -i.bak 's/bean one/bean one, edited/' .beans/bean-0001--one.md; rm -f .beans/*.bak; git add -A; git commit -q -m edit
verdict "KNOWN-GOOD: an ordinary bean edit is allowed" 0 G "main..ordinary"
branch deleter; git rm -q .beans/bean-0002--two.md; git commit -q -m "drop bean"
verdict "KNOWN-BAD: deleting a bean file is refused" 1 G "main..deleter"
branch falsedone; printf -- '---\nstatus: completed\n---\nfixed (no sha)\n' > .beans/bean-0001--one.md; git add -A; git commit -q -m done
verdict "KNOWN-BAD: completed naming no SHA is refused" 1 G "main..falsedone"
branch unlanded; echo more > more.txt; git add -A; git commit -q -m "unlanded work"; un=$(git rev-parse --short=10 HEAD)
printf -- '---\nstatus: completed\n---\nfixed in %s\n' "$un" > .beans/bean-0001--one.md; git add -A; git commit -q -m done
verdict "KNOWN-BAD: completed naming a SHA that is not on main is refused" 1 G "main..unlanded"
branch gooddone; printf -- '---\nstatus: completed\n---\nfixed in %s\n' "$landed" > .beans/bean-0001--one.md; git add -A; git commit -q -m done
verdict "KNOWN-GOOD: completed naming a SHA that is an ancestor of main is allowed" 0 G "main..gooddone"
verdict "the tip of A..B is resolved on its own (a two-line rev must not make the check pass on nothing)" 1 G "main..falsedone"
verdict "a bare rev works as a range tip" 1 G "falsedone"
verdict "an unresolvable main ref refuses" 1 python3 "$BIN/fleet-bean-guard" "$T/r" origin/nope "main..falsedone"
verdict "usage error -> exit 2" 2 python3 "$BIN/fleet-bean-guard" "$T/r"
branch notbean; echo hi > .beans/notes.txt; git add -A; git commit -q -m "non-bean file under .beans"
verdict "a non-.md file under .beans is not a bean" 0 G "main..notbean"
echo "passed=$pass failed=$failn"; [ "$failn" = 0 ]
