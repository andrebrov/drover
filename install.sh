#!/usr/bin/env bash
# install.sh — put drover on this machine. Idempotent; prints every action before (or instead of) doing it.
#
#   ./install.sh                  symlink bin/* into ~/.local/bin, create state dirs, seed config + briefs,
#                                 render launchd plists into ~/Library/LaunchAgents (not loaded)
#   ./install.sh --load           ...and load fleet-watch, fleet-stale, fleet-standup-hourly
#   ./install.sh --load-watchdog  ...and also lead-watchdog (only if you run a backup-lead seat)
#   ./install.sh --dry-run        print the plan, change nothing
#   ./install.sh --force          replace existing files in ~/.local/bin that are not drover symlinks
#   ./install.sh --uninstall      unload + remove the plists and the symlinks; keeps config and state
#
# Env: DROVER_BIN_DIR (default ~/.local/bin), DROVER_HOME (default ~/.local/share/drover),
#      DROVER_CONFIG_DIR (default ~/.config/drover), LAUNCH_AGENTS (default ~/Library/LaunchAgents)
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="${DROVER_BIN_DIR:-$HOME/.local/bin}"
HOME_DIR="${DROVER_HOME:-$HOME/.local/share/drover}"
CFG_DIR="${DROVER_CONFIG_DIR:-$HOME/.config/drover}"
LA="${LAUNCH_AGENTS:-$HOME/Library/LaunchAgents}"
DRY=0 FORCE=0 LOAD=0 WATCHDOG=0 UNINSTALL=0
for a in "$@"; do case $a in
  --dry-run) DRY=1 ;; --force) FORCE=1 ;; --load) LOAD=1 ;; --load-watchdog) LOAD=1 WATCHDOG=1 ;;
  --uninstall) UNINSTALL=1 ;; -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
  *) echo "unknown option: $a" >&2; exit 64 ;; esac; done

run(){ echo "  $*"; [ "$DRY" = 1 ] || "$@"; }
say(){ echo "$*"; }
[ "$DRY" = 1 ] && say "(dry run — nothing is changed)"

if [ "$UNINSTALL" = 1 ]; then
  say "launchd:"
  for t in "$SRC"/launchd/*.plist.in; do
    label=$(basename "$t" .plist.in); p="$LA/$label.plist"
    [ -f "$p" ] || continue
    launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 && run launchctl bootout "gui/$(id -u)/$label"
    run rm -f "$p"
  done
  say "symlinks in $BIN_DIR:"
  for f in "$SRC"/bin/*; do
    l="$BIN_DIR/$(basename "$f")"
    [ -L "$l" ] && [ "$(readlink "$l")" = "$f" ] && run rm -f "$l"
  done
  say "kept: $CFG_DIR (config) and $HOME_DIR (state, reports). Remove them by hand if you want them gone."
  exit 0
fi

say "symlinks in $BIN_DIR:"
[ -d "$BIN_DIR" ] || run mkdir -p "$BIN_DIR"
for f in "$SRC"/bin/*; do
  l="$BIN_DIR/$(basename "$f")"
  if [ -L "$l" ] && [ "$(readlink "$l")" = "$f" ]; then continue; fi
  if [ -e "$l" ] || [ -L "$l" ]; then
    if [ "$FORCE" = 1 ]; then run rm -f "$l"
    else say "  SKIP $l — exists and is not a drover symlink (rerun with --force to replace it)"; continue; fi
  fi
  run ln -s "$f" "$l"
done

say "state dirs in $HOME_DIR:"
for d in queue inbox tasks briefs reports snapshots watch/state retro pump logs; do
  [ -d "$HOME_DIR/$d" ] || run mkdir -p "$HOME_DIR/$d"
done

say "config in $CFG_DIR:"
[ -d "$CFG_DIR" ] || run mkdir -p "$CFG_DIR"
for f in config caps; do
  [ -f "$CFG_DIR/$f" ] || run cp "$SRC/examples/$f" "$CFG_DIR/$f"
done
for f in "$SRC"/examples/briefs/*; do
  [ -f "$HOME_DIR/briefs/$(basename "$f")" ] || run cp "$f" "$HOME_DIR/briefs/"
done

say "launchd plists in $LA:"
[ -d "$LA" ] || run mkdir -p "$LA"
for t in "$SRC"/launchd/*.plist.in; do
  label=$(basename "$t" .plist.in); p="$LA/$label.plist"
  tmp=$(mktemp)
  sed -e "s|@BIN@|$BIN_DIR|g" -e "s|@LOGS@|$HOME_DIR/logs|g" "$t" > "$tmp"
  if [ -f "$p" ] && cmp -s "$tmp" "$p"; then :
  else echo "  render launchd/$(basename "$t") -> $p"; [ "$DRY" = 1 ] || cp "$tmp" "$p"; fi
  rm -f "$tmp"
  case $label in
    *lead-watchdog) [ "$WATCHDOG" = 1 ] || continue ;;
  esac
  if [ "$LOAD" = 1 ]; then
    launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 && run launchctl bootout "gui/$(id -u)/$label"
    run launchctl bootstrap "gui/$(id -u)" "$p"
  fi
done

# shellcheck disable=SC1091
( . "$CFG_DIR/config" 2>/dev/null; [ -n "${DROVER_REPO:-}" ] ) || \
  say "NEXT: set DROVER_REPO in $CFG_DIR/config to your project checkout, then run: fleet up"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) say "NOTE: $BIN_DIR is not on your PATH." ;; esac
[ "$LOAD" = 1 ] || say "Plists are rendered but not loaded. Load with: ./install.sh --load"
