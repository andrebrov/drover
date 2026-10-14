#!/usr/bin/env bash
# pre-push-bean-guard.sh — paste into (or source from) .git/hooks/pre-push, after the front-matter check if you have one.
# Refuses a push from a seat worktree that deletes a bean file or marks one `completed` without a SHA that is already on the main ref.
# Scope it to seat worktrees (here: any path under DROVER_WORKTREES) so the human's own checkout and the lead's lander behave as before.
# git feeds pre-push one line per ref on stdin: <local ref> <local sha> <remote ref> <remote sha>.
_root=$(git rev-parse --show-toplevel)
case "$_root" in "${DROVER_WORKTREES:-$HOME/.herdr/worktrees}"/*)
  if [ "${FLEET_BATCH_LANDER:-}" != 1 ]; then
    _ranges=()
    while read -r _lref _lsha _rref _rsha; do
      [ -z "${_lsha:-}" ] && continue
      case "$_lsha" in 0000000000000000000000000000000000000000) continue ;; esac   # a ref deletion carries no beans
      case "$_rsha" in 0000000000000000000000000000000000000000) _ranges+=("origin/main..$_lsha") ;; *) _ranges+=("$_rsha..$_lsha") ;; esac
    done
    if [ "${#_ranges[@]}" -gt 0 ]; then
      "${DROVER_BIN:-$HOME/.local/bin}/fleet-bean-guard" "$_root" origin/main "${_ranges[@]}" || exit 1
    fi
  fi ;;
esac
