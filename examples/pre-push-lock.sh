#!/usr/bin/env bash
# pre-push-lock.sh — one fleet-wide push lock, meant to be pasted into (or sourced from) a
# .git/hooks/pre-push hook.
#
# Every seat works in its own worktree and can push at any moment. Two pre-push hooks running
# their build+test gate at once starve each other on the same box — a push that should take
# seconds took 30+ minutes measured this way. This block serializes them: ONE pre-push hook runs
# at a time, fleet-wide, across every worktree.
#
# The lock records the OWNING hook's pid. A waiter blocks while that pid is alive and takes over
# ONLY from a dead owner — NEVER by age. An age-only backstop evicts a live, slow hook (a big
# push's build+test gate can legitimately run 10+ minutes) and starts a second push racing the
# first; checking `kill -0` first is what fixes that (see docs/LESSONS.md).
#
# git runs hooks with GIT_DIR set relative ('.git'); every build step that cd's elsewhere and
# then shells out to git can fail against that relative path. Make it absolute once, here, before
# anything downstream has to defend against it.
if [ -n "${GIT_DIR:-}" ]; then export GIT_DIR="$(cd "$GIT_DIR" && pwd)"; fi
if [ -n "${GIT_WORK_TREE:-}" ]; then export GIT_WORK_TREE="$(cd "$GIT_WORK_TREE" && pwd)"; fi

PUSH_LOCK="${DROVER_HOME:-$HOME/.local/share/drover}/watch/state/.push-lock"
mkdir -p "$(dirname "$PUSH_LOCK")"
until mkdir "$PUSH_LOCK" 2>/dev/null; do
  _owner=$(cat "$PUSH_LOCK/pid" 2>/dev/null || true)
  # kill -0 fails for a DEAD pid ("No such process") AND for a LIVE pid we may not signal ("Operation not
  # permitted", e.g. a hook started from a sandboxed seat). Treating both as dead stole a live lock: two
  # hooks ran at once and approved pushes lost the race to main. Only "no such process" means dead.
  if [ -n "$_owner" ] && kill -0 "$_owner" 2>&1 | grep -qi 'no such process'; then
    printf '%s\tstale lock from dead pid %s taken by pid %s\n' "$(date '+%F %T')" "$_owner" "$$" >> "$(dirname "$PUSH_LOCK")/push-lock-steals.log"
    rm -rf "$PUSH_LOCK"; continue
  fi
  echo "[pre-push] another push (pid ${_owner:-?}) holds the lock; waiting..." >&2
  sleep 15
done
echo $$ > "$PUSH_LOCK/pid"
trap 'rm -rf "$PUSH_LOCK"' EXIT

# --- the rest of your hook (build, related tests) runs here, still holding the lock ---
