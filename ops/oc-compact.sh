#!/usr/bin/env bash
# oc-compact — compact ~/.local/share/opencode/opencode.db safely (2026-09-18, second attempt).
#
# Why not plain VACUUM: in WAL mode it builds a temp copy (~49 GB) AND then writes the whole new DB into the
# WAL (~49 GB more) before checkpointing — peak ~100 GB extra. It ran the disk down to 13 GB and was killed.
# VACUUM INTO writes ONE new file (~49 GB), no WAL copy. Then we verify it and swap it in.
#
# Pauses the fleet watcher (its spend guard and seat guard open this DB), stops opencode, compacts, verifies,
# swaps, removes the old 133 GB file, and resumes the watcher. Aborts without touching the DB on any doubt.
# The free-space floor below was sized for that 49 GB copy; set MIN_FREE_GB for your own database.
#
# Why it matters to the fleet: every opencode seat writes this one database, and at 133 GB it ate the disk.
set -uo pipefail
DB="$HOME/.local/share/opencode/opencode.db"; NEW="$DB.compact"; OUT="$HOME/.local/share/opencode-archive/$(date +%F)"
PLIST="$HOME/Library/LaunchAgents/dev.drover.fleet-watch.plist"
mkdir -p "$OUT"
say(){ echo "$(date +%H:%M:%S) $*"; }
resume_watch(){ launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; say "fleet-watch resumed"; }

free_gb=$(df -g "$DB" | awk 'NR==2{print $4}')
[ "$free_gb" -ge "${MIN_FREE_GB:-65}" ] || { say "only ${free_gb} GB free; need >= ${MIN_FREE_GB:-65} GB for the compacted copy with margin — aborting"; exit 1; }

# 1. pause the watcher so nothing reopens the DB mid-swap
launchctl bootout "gui/$(id -u)/dev.drover.fleet-watch" 2>/dev/null; say "fleet-watch paused"
trap resume_watch EXIT

# 2. snapshot + stop opencode (same as before)
herdr agent list 2>/dev/null | python3 -c '
import json,sys
for x in json.load(sys.stdin)["result"]["agents"]:
  if x.get("agent")!="opencode": continue
  s=(x.get("agent_session") or {}).get("value","")
  print("\t".join([x.get("pane_id",""), x.get("name") or "-", s, x.get("cwd","")]))' > "$OUT/panes-before-compact.tsv"
pids=$(pgrep -x opencode | tr '\n' ' ')
say "stopping opencode: $pids (panes snapshotted: $(wc -l < "$OUT/panes-before-compact.tsv"))"
[ -n "$pids" ] && kill -TERM $pids
for i in $(seq 1 60); do pgrep -x opencode >/dev/null || break; sleep 1; done
pgrep -x opencode >/dev/null && { say "opencode still running after 60s — aborting, DB untouched"; exit 1; }
holders=$(lsof "$DB" 2>/dev/null | awk 'NR>1{print $1"("$2")"}' | sort -u | tr '\n' ' ')
[ -z "$holders" ] || { say "DB still open by: $holders — aborting, DB untouched"; exit 1; }

# 3. compact into a new file
rm -f "$NEW"
sqlite3 "$DB" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null
say "VACUUM INTO (free ${free_gb} GB)"
if ! sqlite3 "$DB" "VACUUM INTO '$NEW';"; then rm -f "$NEW"; say "VACUUM INTO failed — DB untouched"; exit 1; fi
qc=$(sqlite3 "$NEW" "PRAGMA quick_check;" | head -1)
[ "$qc" = "ok" ] || { rm -f "$NEW"; say "compact copy failed quick_check ($qc) — DB untouched"; exit 1; }
cnt_old=$(sqlite3 "$DB" "select count(*) from session"); cnt_new=$(sqlite3 "$NEW" "select count(*) from session")
[ "$cnt_old" = "$cnt_new" ] || { rm -f "$NEW"; say "session count differs ($cnt_old vs $cnt_new) — DB untouched"; exit 1; }

# 4. swap
mv "$DB" "$DB.pre-compact" && rm -f "$DB-wal" "$DB-shm" && mv "$NEW" "$DB" || { say "swap failed — restore with: mv '$DB.pre-compact' '$DB'"; exit 1; }
[ "$(sqlite3 "$DB" "PRAGMA quick_check;" | head -1)" = "ok" ] || { mv -f "$DB.pre-compact" "$DB"; say "swapped file failed check — original restored"; exit 1; }
rm -f "$DB.pre-compact"
say "done: opencode.db $(( $(stat -f %z "$DB") / 1073741824 )) GB, sessions $cnt_new, disk free $(df -g "$DB" | awk 'NR==2{print $4}') GB"
say "opencode is still stopped — restart the seats (panes are listed in $OUT/panes-before-compact.tsv)"
