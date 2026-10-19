#!/usr/bin/env bash
# Tests for fleet-branch-landed, fleet-done (landing gate), fleet-prep and fleet-refill's run(): every case has a known-bad AND a known-good input.
# Scratch directories with a bare origin and a clone; touches nothing real. Usage: bash cadence-tools.test.sh [bin-dir]   (TEST_VERBOSE=1 shows failing output)
set -uo pipefail
BIN="$(cd "${1:-$(dirname "$0")/../bin}" && pwd)"; T=$(mktemp -d); pass=0; failn=0
ok(){ pass=$((pass+1)); echo "ok   $1"; }; bad(){ failn=$((failn+1)); echo "FAIL $1"; }
export DROVER_HOME="$T/home" DROVER_CONFIG=/nonexistent DROVER_BEAN_PREFIX=bean- DROVER_PUBLISH_MODE_FILE="$T/mode" DROVER_WORKTREES="$T/wts"
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
unset GIT_DIR GIT_WORK_TREE
mkdir -p "$DROVER_HOME/inbox" "$DROVER_HOME/reports"; echo direct > "$T/mode"
git init -q --bare "$T/origin.git"; git clone -q "$T/origin.git" "$T/repo" 2>/dev/null
W="$T/repo"; export DROVER_REPO="$W"; cd "$W"; git config user.email t@t; git config user.name t; git checkout -q -b main
mkdir -p .beans; echo base > f.txt; echo "# bean" > .beans/bean-aaaa--x.md; git add -A; git commit -q -m "base"; git push -q origin main
fc(){ "$BIN/fleet-branch-landed" "$@"; }; fd(){ "$BIN/fleet-done" "$@"; }
code(){ "$@" >/dev/null 2>&1; echo $?; }                 # run, print only the exit code
expect(){ local name=$1 want=$2; shift 2; local got; got=$(code "$@"); if [ "$got" = "$want" ]; then ok "$name"; else bad "$name (want exit $want, got $got)"; fi; }
# ---------------- fleet-branch-landed
git checkout -q -b fleet/s-bean-aaaa; echo 1 > a.txt; git add -A; git commit -q -m "feat: thing one (bean-aaaa)"
out=$(fc fleet/s-bean-aaaa bean-aaaa); rc=$?
if [ $rc = 1 ] && echo "$out" | head -1 | grep -qE '^AGE [0-9]+ NEWEST [0-9]+$'; then ok "branch-landed: unlanded commit -> exit 1 with 'AGE n NEWEST n'"; else bad "branch-landed: unlanded commit (rc=$rc out=$(echo "$out" | head -1))"; fi
git checkout -q main; git merge -q --ff-only fleet/s-bean-aaaa; git push -q origin main
expect "branch-landed: landed by exact subject -> exit 0" 0 fc fleet/s-bean-aaaa bean-aaaa
git checkout -q -b fleet/r-bean-aaaa main~1; echo 2 > b.txt; git add -A; git commit -q -m "feat: second (bean-aaaa)"
git checkout -q main; git cherry-pick fleet/r-bean-aaaa >/dev/null 2>&1; git commit -q --amend -m "feat: second, reworded after rebase (bean-aaaa)"; git push -q -f origin main
expect "branch-landed: reworded subject, same patch (git cherry) -> exit 0" 0 fc fleet/r-bean-aaaa bean-aaaa
p70="feat(x): a very long shared prefix that goes past seventy characters for sure 1"
git checkout -q -b fleet/p-bean-aaaa main; echo c > c.txt; git add -A; git commit -q -m "$p70 tail-A (bean-aaaa)"
git checkout -q main; echo d > d.txt; git add -A; git commit -q -m "$p70 tail-B (bean-aaaa)"; git push -q -f origin main
expect "branch-landed: shared 70-char prefix, different tail -> still UNLANDED (exit 1)" 1 fc fleet/p-bean-aaaa bean-aaaa
expect "branch-landed: another bean's commits are not checked -> exit 0" 0 fc fleet/p-bean-aaaa bean-zzzz
expect "branch-landed: unknown ref -> exit 2 (never 'landed')" 2 fc no/such/branch
expect "branch-landed: not a git repo -> exit 2" 2 bash -c "cd '$T' && '$BIN/fleet-branch-landed' HEAD"
# ---------------- fleet-done landing gate (direct mode)
git checkout -q fleet/p-bean-aaaa
expect "fleet-done: DONE with this bean's commit unlanded -> exit 7" 7 fd coder bean-aaaa DONE all
if cut -f4,5 "$DROVER_HOME/inbox"/* 2>/dev/null | grep -q 'BLOCKED.*wanted DONE'; then ok "fleet-done: refusal delivered as BLOCKED keeping '[wanted DONE]'"; else bad "fleet-done: refusal delivered as BLOCKED keeping '[wanted DONE]'"; fi
expect "fleet-done: a different bean on the same branch does not block -> exit 0" 0 fd coder bean-zzzz DONE x
expect "fleet-done: unlanded BLOCKED is refused -> exit 7" 7 fd coder bean-aaaa BLOCKED "need decision"
FLEET_UNLANDED_OK="depends on S9 decision" fd coder bean-aaaa PARTIAL p >/dev/null 2>&1; wrc=$?
if [ $wrc = 0 ] && grep -q 'depends on S9 decision' "$DROVER_HOME/reports/unlanded-waivers.log" && grep -q 'tail-A' "$DROVER_HOME/reports/unlanded-waivers.log"; then ok "fleet-done: a waiver is allowed AND logged with the reason and the subjects"; else bad "fleet-done: waiver (rc=$wrc)"; fi
git checkout -q -b scratch-not-a-fleet-name fleet/p-bean-aaaa
expect "fleet-done: the gate also applies on a non-fleet branch name -> exit 7" 7 fd coder bean-aaaa DONE all
git checkout -q main
expect "fleet-done: on main nothing is gated -> exit 0" 0 fd coder bean-aaaa DONE ok
# ---------------- fleet-prep: an ignored local file that the target branch tracks must survive
git checkout -q -b tgt main; echo tracked > local.cfg; git add -f local.cfg; echo "# b" > .beans/bean-bbbb--y.md; git add -A; git commit -q -m "track local.cfg (bean-bbbb)"; git branch -q fleet/s2-bean-bbbb; git checkout -q main
git checkout -q -b fleet/s2-bean-aaaa main; echo "local.cfg" >> .git/info/exclude; echo "unsaved local data" > local.cfg
git checkout -q main; git worktree add -q "$T/wt-s2" fleet/s2-bean-aaaa; echo "unsaved local data" > "$T/wt-s2/local.cfg"; echo "local.cfg" >> "$W/.git/info/exclude"
expect "fleet-prep: a switch that would overwrite an ignored file stops -> exit 3" 3 "$BIN/fleet-prep" s2 bean-bbbb "$T/wt-s2"
if grep -q 'unsaved local data' "$T/wt-s2/local.cfg"; then ok "fleet-prep: the ignored file's content survived"; else bad "fleet-prep: the ignored file's content was lost"; fi
cd "$W"
# ---------------- fleet-refill run(): the whole process group dies on timeout
cat > "$T/runtest.py" <<'P'
import sys, subprocess, os, signal, time
src = open(sys.argv[1]).read(); i = src.index('class Watchdog'); j = src.index('def load():')
ns = {'subprocess': subprocess, 'os': os, 'signal': signal, 'time': time}; exec(src[i:j], ns)
r = ns['run'](['sh', '-c', 'sleep 31 & sleep 32 & wait'], timeout=1)
time.sleep(0.5); left = subprocess.run(['pgrep', '-f', 'sleep 3[12]'], capture_output=True, text=True).stdout.split()
sys.exit(0 if r.returncode == 124 and not left else 1)
P
expect "refill run(): a timed-out command's whole process group is killed" 0 python3 "$T/runtest.py" "$BIN/fleet-refill"
# ---------------- extra cases (not in the original 17): a daemon that calls a function nobody defined, and the stuck check itself
cat > "$T/undef.py" <<'P'
import ast, builtins, sys
src = open(sys.argv[1]).read(); tree = ast.parse(src)
defined = set(dir(builtins))
for n in ast.walk(tree):
    if isinstance(n, (ast.FunctionDef, ast.ClassDef)): defined.add(n.name)
    elif isinstance(n, ast.Import): defined.update((a.asname or a.name).split('.')[0] for a in n.names)
    elif isinstance(n, ast.ImportFrom): defined.update(a.asname or a.name for a in n.names)
    elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)): defined.add(n.id)
    elif isinstance(n, ast.arg): defined.add(n.arg)
    elif isinstance(n, ast.ExceptHandler) and n.name: defined.add(n.name)
missing = sorted({n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id not in defined})
print('undefined calls:', missing); sys.exit(1 if missing else 0)
P
for d in fleet-refill fleet-feed fleet-batch; do expect "$d: every function it calls is defined" 0 python3 "$T/undef.py" "$BIN/$d"; done
cat > "$T/stucktest.py" <<'P'
import importlib.machinery, importlib.util, os, sys, time, subprocess
os.environ['DROVER_ROSTER'] = 's-coder:claude'
l = importlib.machinery.SourceFileLoader('fr', sys.argv[1]); m = importlib.util.module_from_spec(importlib.util.spec_from_loader('fr', l)); l.exec_module(m)
wt = sys.argv[2]; m.WORKTREES = os.path.dirname(wt); m.STATE_DIR = sys.argv[3]
m.SEATS_ALL = ['s-coder']; sent = []
m.seat_view = lambda seat: ('working', '')
orig = m.run
m.run = lambda cmd, timeout=20: (sent.append(cmd) or subprocess.CompletedProcess(cmd, 0, '', '')) if cmd[:3] == ['herdr', 'agent', 'send-keys'] else orig(cmd, timeout)
st = {}; t0 = time.time()
m.stuck_check(st, t0)                                  # first sight: records the signature, flags nothing
assert open(f'{sys.argv[3]}/alerts/stuck.txt').read() == '' and os.path.exists(f'{sys.argv[3]}/alerts/stuck.txt.ok')
m.stuck_check(st, t0 + 50 * 60)                        # 50 minutes, same worktree signature: stuck
txt = open(f'{sys.argv[3]}/alerts/stuck.txt').read()
assert 's-coder' in txt and len(sent) == 1, (txt, sent)
m.stuck_check(st, t0 + 55 * 60)
assert len(sent) == 1, 'Esc is sent once per signature'
(open(f'{wt}/changed', 'w').write('x'))                # the worktree changes: the seat is working again
m.stuck_check(st, t0 + 60 * 60)
assert open(f'{sys.argv[3]}/alerts/stuck.txt').read() == ''
P
mkdir -p "$T/wts"; git worktree add -q "$T/wts/fleet-s-coder" -b w-stuck main
expect "refill stuck_check: flags a 'working' seat with no worktree change, sends Esc once, clears when it changes, stamps .ok" 0 python3 "$T/stucktest.py" "$BIN/fleet-refill" "$T/wts/fleet-s-coder" "$T/state"
# ---------------- injection: a branch NAME is data. git accepts $(...) in ref names; the landing audit must not execute it
git worktree add -q "$T/wts/fleet-s-coder" -b w-inj main 2>/dev/null || true
git branch 'fleet/s$(touch${IFS}INJECTED)-bean-zzzz' main
cat > "$T/injtest.py" <<'P'
import importlib.machinery, importlib.util, os, sys, time
os.environ['DROVER_ROSTER'] = 's-coder:claude'
l = importlib.machinery.SourceFileLoader('fr', sys.argv[1]); m = importlib.util.module_from_spec(importlib.util.spec_from_loader('fr', l)); l.exec_module(m)
m.STATE_DIR = sys.argv[2]; m.REPO = sys.argv[3]
os.chdir(sys.argv[4]); m.landing_audit({}, time.time(), dry=True)
P
mkdir -p "$T/injcwd"; ( cd "$T/injcwd" && python3 "$T/injtest.py" "$BIN/fleet-refill" "$T/state2" "$W" "$T/injcwd" >/dev/null 2>&1 )
if [ -e "$T/injcwd/INJECTED" ] || [ -e "$W/INJECTED" ]; then bad "refill landing_audit: a branch name with \$(...) was EXECUTED"; else ok "refill landing_audit: a branch name with \$(...) is data, not code"; fi
echo; echo "passed=$pass failed=$failn"; rm -rf "$T"; [ $failn = 0 ]
