# drover

drover turns [herdr](https://herdr.dev) into a self-running team of coding agents: a lead assigns beans
(tasks), coders work in their own git worktrees, a reviewer on a *different* harness reviews, and a watcher
loop moves work between them without the lead in the path.

It is a set of bash scripts, a launchd job, and an agent skill. It was extracted from one person's fleet —
10 to 25 agents across Claude Code, Codex, opencode, Grok, Cursor and Antigravity, working on one production
codebase — and it carries that fleet's scar tissue: almost every guard in it has a dated comment saying what
broke before it existed.

## Why

herdr already hosts the agents: each coding CLI runs in a named pane, and `herdr agent list|read|prompt`
drives them. What it does not do is notice when one of them has finished. Before drover, the lead did the
noticing, and the ledger says what that cost:

- **44 minutes median** between one dispatch and the next to the same agent (p90: 285 minutes), measured over
  650 gaps, for tasks that take 10–20 minutes. Every transition — done → review → next task — waited for the
  lead, who polled in 8-minute sleeps.
- `fleet status` reported `done` for agents that were mid-turn, and pane text kept stale error banners after
  they were fixed. When opencode's credits ran out, **10 agents reported `done` with no work, no report and no
  error**, and were dispatched into again.
- Reports announced "DONE, landable" **for the ninth time** — each one a paid round that re-verified
  commits that already existed.
- opencode spent **$290, then $376** on two consecutive days, against $27–$82 on each of the four days before.
  The first meter built to watch it (`opencode stats --days 1`) reported **$952**: it adds up the lifetime
  cost of every session touched in the window. The cap now sums per-message cost since midnight.

drover's answers: agents announce completion (`fleet-done`) instead of being polled; state is judged by
artifacts (a commit on the branch, a report on disk), never by status fields; a watcher reacts within a
minute; and each of those failures now has a guard (a spend cap, a no-op detector, credit-dead seat detection).

## The loop

```
   beans list --ready (on main)
            |  top_up_queue
            v
   +---->  queue/  <epoch>-<bean>  <------------------------------+
   |        |  refill: a free coder, harness not broke,           |  requeue: dead seat,
   |        |  bean on main and ready, not owned by another coder |  failed dispatch
   |        v                                                     |
   |   CODER seat  --  own worktree, branch fleet/<coder>-<bean> --+
   |        |  fleet-done <me> <bean> DONE | BLOCKED
   |        v
   |    inbox/  -->  drain_inbox
   |        |   DONE with unchecked items -> PARTIAL -> back to the same coder (3x -> lead)
   |        |   BLOCKED                   -> state/blocked (lead reads it)
   |        |   DONE                      -> one review, on a DIFFERENT harness
   |        v
   |   REVIEWER seat  --  cherry-picks the named commits onto a detached main, builds, tests
   |        |  fleet-done <me> <bean> CHANGES | DONE (verdict APPROVE)
   |        v
   |    drain_inbox
   |        |   CHANGES -> followup to the coder, ahead of anything in the queue --+
   |        |   APPROVE -> state/approved/<bean>: held out of the queue           |
   |        v                                                                     |
   |   LAND  the lead — or, with DROVER_AUTOLAND=1, fleet-land-bean:              |
   |        cherry-pick the bean's own branch into an ISOLATED worktree,          |
   |        gate (typecheck + related tests), fast-forward LOCAL main. Then       |
   |        fleet-verify-main (full build+suite on a clean tree) -> push origin.  |
   +--------------------------------------------------------------- coder <------+
```

By default landing stays with the human lead — the only party that re-verifies against current main, runs
migrations, and pushes. Set `DROVER_AUTOLAND=1` and the watcher does it: `fleet-land-bean` lands each
approved bean to LOCAL main behind a fast per-bean gate, and `fleet-verify-main` runs the full suite on a
clean worktree before `fleet-push` sends the green batch to origin. A cumulative red pauses landing
(`state/land-paused`) so one bad merge can't cascade. Migrations are still never applied automatically.

## What one watcher tick does

`fleet-watch run` ticks every 60 seconds (`FLEET_TICK`). In order:

1. **Clear known blocking dialogs** — only exact matches (an auto-mode prompt, a save confirmation, a
   rate-limit model switch). A generic "Enter to confirm" match once hit a folder-trust dialog whose cursor sat
   on "No, exit" and killed three seats.
2. **Budget** — `fleet-budget` reads each non-working pane for out-of-credits / usage-limit / session-limit
   banners (ignoring stale scrollback); the spend guard sums today's per-message opencode cost from its database and compares it with your daily cap; any
   harness listed in `state/harness-off` is off. A broke harness gets no new dispatches or reviews.
3. **Harvest beans** that agents filed in their worktrees onto main (the board follows main).
4. **Drain the inbox** — route each announcement: DONE → a cross-harness review; PARTIAL → back to the coder;
   CHANGES → a followup; APPROVE → held for landing; BLOCKED → the lead. Stale announcements (a bean the seat is
   no longer on) are logged and ignored; a late DONE still routes its review.
5. **Retry reviews** that found no free reviewer last tick.
6. **Reap task files** whose claim is provably dead (bean completed on main, re-routed, reported and idle, or
   silent past 45 min for a reviewer / 90 min for a coder). A `working` or `blocked` seat is never reaped.
7. **Seat scan** (optional, TypeSafe) — every 10 minutes, read seats that have held a task for 20+ minutes.
8. **Owner sync** — record which coder owns each bean; flag any bean held by two coders.
9. **Sweep** (every 6 h), **WIP snapshot** (every 20 min), **inventory alert** (every 30 min: undeployed
   commits and integrity drift), **yolo guard** (every 5 min). **heal_repo** at the top of every tick
   restores `$DROVER_REPO` to main if an agent op drifted it off (otherwise the whole fleet idles).
10. **Top up the queue** from `beans list --ready` when it is empty (sprint keywords first, then priority) —
    unless the WIP cap is reached (see Guardrails): backpressure stops starting NEW beans while the
    integration backlog is full.
11. **Refill** every free coder: a followup beats the queue; then the next queued bean, after re-checking it
    is on main, still ready, not owned by another coder, not awaiting review or landing, within the epic
    train cap, and that the seat is not credit-dead.
12. **Land + push** (only with `DROVER_AUTOLAND=1`) — one approved bean per tick to LOCAL main via
    `fleet-land-bean`, then the cumulative gate + push via `fleet-verify-main`; a red pauses landing.

`state/last` holds a one-screen summary of the latest tick; `watch/watch.log` has every decision with a
one-word tag (ROUTED, DISPATCH, FOLLOWUP, PARTIAL, SKIP, STALE, BUDGET, APPROVED, NO-OP, SEAT, …).

## Guardrails

| Guard | What it stops | Where |
|---|---|---|
| WIP cap / backpressure | Without it, the watcher is an open-loop dispatch pump: measured over 2 days, 12,240 dispatches vs 195 land attempts (~60:1) — 700+ branches that were inventory, not progress. At or over the cap (`state/wip-cap`, default 6), NO new beans start; followups and landing still flow, so the backlog drains before more work piles on. | `wip_count`, `top_up_queue`, `refill` |
| Epic train cap | Spraying one epic across many parallel beans on shared files is a merge-conflict lottery. Cap in-flight beans per parent epic (`state/epic-cap`, default 2) so a roadmap advances as a sequenced train. | `epic_inflight`, `refill` |
| Merge-queue land gate | A per-bean land gates each bean against the main it forks from; the cumulative main can still break when the batch combines. `fleet-verify-main` runs the full build+suite on a CLEAN worktree at main HEAD before any push; a red pauses landing (`state/land-paused`). | `fleet-land-bean`, `fleet-verify-main`, `fleet-push` |
| Integration integrity | A reset/revert can leave a bean marked `completed` on main while its code is gone. `fleet-completed-audit` finds beans completed-on-main whose branch still has unlanded code; the watcher warns hourly. | `fleet-completed-audit`, `inventory_alert` |
| Repo-off-main heal | An agent git op in the shared checkout can drift `$DROVER_REPO` off main, which idles the whole fleet (dispatch and land both require main). `heal_repo` switches it back when the tree carries cleanly; a real conflict is left for a human. | `heal_repo` |
| Stale-owner release | An owner marker on an idle seat's empty branch (or a recovery-set marker on a real branch not in the pipeline) would block re-dispatch forever. Released at 30/45 min; lead-owned markers expire at 24 h. | `owner_sync` |
| Spend cap | `opencode_daily_usd` in `~/.config/drover/caps`, re-read every tick. At or over the cap: no new opencode dispatches or reviews; running work finishes. Optional `active_from` date. | `fleet-watch` `spend_guard` |
| Credit-dead seats | A seat out of credits accepts a prompt, shows `working`, and does nothing. Both dispatch and review paths read the pane first; a 30-minute marker stops re-reading a dead seat every tick. | `fleet-watch`, `fleet-budget` |
| One bean, one coder | A bean belongs to its first coder. Another coder gets it only if the owner is missing, credit-dead or silent for 72 h — logged. Two coders on one bean is flagged for the lead. | `owner_blocks`, `owner_sync` |
| Held beans | APPROVED beans and beans with two no-op rounds never re-enter the queue. | `state/approved`, `state/noop-held` |
| finish-first freeze | `touch state/finish-first`: no new beans enter any seat; fix rounds and reviews still flow, so everything in flight converges and lands. | `top_up_queue`, `refill` |
| Harness off-switch | One harness name per line in `state/harness-off`. | `check_budget` |
| Dialog clearing | Only exact, known dialogs are answered. Permission and trust dialogs are never answered; with the seat detector enabled they are flagged for the human once. | `clear_dialogs`, `seat_scan` |
| Worktree and scratch sweep | Deletes scratchpad dirs over 50 MB untouched for 2 h, and review worktrees that are stale (12 h), clean, carry no unique commit and have no process inside — via `git worktree remove` without `--force`. | `fleet-sweep` |
| WIP snapshots | Every 20 min, copies every worktree's dirty *tracked* files aside (kept 48 h). A `git reset --hard` once destroyed ~87 unstaged edits that git cannot recover by design. | `fleet-snapshot` |
| Yolo guard | herdr resumes agents after a reboot without their approval-bypass flags; idle seats are relaunched with them, resuming their own session. A `working` seat is never interrupted. | `fleet-yolo` |
| Fail-loud dispatch | Unknown agent, bean not on main, or unwritable brief: exit 2, one line, nothing written. A failed dispatch leaves the seat free and requeues the bean. | `fleet`, `fleet-watch` |
| Serial one-line prompts | Prompts are a one-line pointer to a brief file, sent serially with `--wait`: parallel sends lost 6 of 6 prompts, and long multi-line prompts lost 3 of 4. | `fleet assign/review` |
| Stale processes | Orphaned tool processes (pane gone), dev/test leftovers under idle agents, and heavy processes, reported into herdr's sidebar every 5 min. | `fleet stale` (launchd) |
| Backup lead | A zero-token watchdog prompts a backup-lead seat once when the primary lead's heartbeat is 45 min old. | `lead-watchdog` (optional) |

## Optional: TypeSafe detectors

`fleet-judge` asks [TypeSafe](https://typesafe.ai)'s System One API three typed questions. It is off unless
`~/.config/typesafe/api_key` exists. Without the key, or on any error, it prints nothing and exits 2, and
`fleet-watch` behaves exactly as it does without it.

| Detector | Question | Measured (2026-09-18, on the original fleet's data) | What the watcher does |
|---|---|---|---|
| `noop` | P(this round produced no new work) | AUC 0.98 | At p ≥ 0.8, counts a no-op round; two in a row hold the bean for the lead. |
| `verdict` | P(this review says land it as it is), verdict words removed | AUC 0.96; 0 of 17 CHANGES reviews read as approve at p ≥ 0.8 | Used only when neither the announcement nor a `VERDICT:` line says; p between 0.2 and 0.8 goes to the lead. |
| `seat` | working / idle / out_of_credits / prompt_typed_not_sent / blocked_on_dialog | On the shipped fixtures (`eval/`, 28 panes incl. 3 dialogs): 25 of 28 top-1; at confidence ≥ 0.8, 7 firings, 7 correct, 0 false | out_of_credits → seat marked dead, bean requeued. Dialogs and unsent prompts are flagged for the human once, never answered. |

`eval/` has the seat-state fixtures (real pane tails, rewritten to remove paths and task text) and a script that
scores the shipped detector against them. The numbers above were measured on the unscrubbed originals; the
scrubbed fixtures have not been re-scored. The noop and verdict numbers were measured on the original fleet's
reports, which are not included.

What leaves your machine: report text (first 1,500 chars), review text (1,800) and pane tails (last 2,500), with
credential-shaped strings (`sk-…`, `ghp_…`, `AKIA…`, `*_KEY=…`, …) stripped first. Paths, branch names and task
titles are sent. Decide whether that is acceptable before adding the key.

## Requirements

- macOS (launchd, `stat -f`, APFS `cp -c` clones for fast worktree bootstrap)
- bash, python3, jq, git 2.40+
- [herdr](https://herdr.dev) with the harness CLIs you use (`claude`, `codex`, `opencode`, `cursor-agent`, `grok`, `agy`)
- [beans](https://github.com/hmans/beans) — the task board. Beans must be committed on `main` to be dispatched.
- Optional: `opencode` (for the spend cap), a TypeSafe API key, `sqlite3` (for `ops/oc-compact.sh`)

## Quickstart

```bash
git clone <this repo> ~/src/drover && cd ~/src/drover
./install.sh --dry-run          # shows every symlink, dir, file and plist it would create
./install.sh                    # symlinks bin/* into ~/.local/bin; never overwrites a file it did not create
$EDITOR ~/.config/drover/config # set DROVER_REPO, DROVER_ROSTER, DROVER_BUILD, DROVER_TEST

# inside herdr:
fleet up                        # spawn the roster: one worktree + pane per seat, approval bypass on
fleet rules                     # install the fleet-peers skill for claude/codex/grok/opencode
fleet status

fleet assign <bean>             # hand-dispatch one bean to the least-loaded idle coder, or:
fleet-watch once                # one tick by hand; tops the queue up from beans --ready
./install.sh --load             # let launchd run fleet-watch, fleet stale, and the hourly standup
tail -f ~/.local/share/drover/watch/watch.log
```

To land: follow [`docs/RUNBOOK.md`](docs/RUNBOOK.md). To stop new work while everything in flight finishes:
`touch ~/.local/share/drover/watch/state/finish-first`.

If another `fleet` command is on your PATH (JetBrains Fleet installs one), it may shadow this one. drover's own
scripts call each other by absolute path, so only your interactive shell is affected.

## Commands

| Command | Does |
|---|---|
| `fleet` | Roster, spawn (`up`), `assign`, `review`, `followup`, `land`, `sync`, `rules`, `models`, `usage`, `stale`, and pane helpers (`read`, `keys`, `wait`, `cancel`, `blocked`). `fleet --help`, and `--dry-run` on every mutating verb. |
| `fleet-watch` | The loop. `run`, `once`, `enqueue <bean>`, `queue`. |
| `fleet-land-bean` | Land ONE approved bean to LOCAL main: cherry-pick its own branch in an isolated worktree, gate, ff-only. Never pushes. Used by auto-land; run by hand too. |
| `fleet-verify-main` | The cumulative merge-queue gate: full build+suite on a clean detached worktree at a ref. Exit 0 = safe to push. |
| `fleet-push` | The only sanctioned push: runs `fleet-verify-main`, pushes local main to origin only if green. Never force. |
| `fleet-scoreboard` | What shipped vs what's stuck: landed/pushed today, approved-awaiting-land, in-review, integrity drift. The metric that isn't utilization. |
| `fleet-completed-audit` | List beans `completed` on main whose code isn't actually landed. `--count` for the cached number. |
| `fleet-done` | What an agent runs when it finishes. The only way the fleet learns about it. |
| `fleet-wait` | Block until an agent announces (for a lead working without the watcher). |
| `fleet-track` | Judge seats by artifacts: commits since dispatch and report presence. |
| `fleet-budget` | Per-harness credit / usage-limit state, read from panes. |
| `fleet-judge` | Optional TypeSafe detectors (above). |
| `fleet-yolo` | Relaunch idle seats that lost their approval-bypass flag. |
| `fleet-claim` | Advisory file claims across worktrees; `hot` lists files several branches edit. |
| `fleet-harvest-beans` | Copy beans filed in worktrees onto main. |
| `fleet-snapshot` | Copy dirty tracked files out of every worktree. |
| `fleet-sweep` | Reclaim stale scratchpads and review worktrees. `DRY=1` to preview. |
| `fleet-hunt` | Send idle non-coders on a bug, UX or performance hunt. |
| `fleet-pump` | Per-agent job queue of hand-written briefs. |
| `fleet-sprint` | Themed sprint board: `start`, `board`, `close`. |
| `fleet-standup`, `fleet-standup-auto` | One-screen standup; hourly file fallback. |
| `fleet-retro` | Retro scaffold from the session's own artifacts. |
| `fleet-tasks` | Task ledger derived from reports and git. |
| `lead-watchdog` | Backup-lead takeover trigger. |

## Configuration

Everything is read from `~/.config/drover/config` (plain shell, sourced; `DROVER_CONFIG` to move it). Write
values as `${VAR:-value}` so an environment variable still wins. See [`examples/config`](examples/config).

| Variable | Default | Meaning |
|---|---|---|
| `DROVER_REPO` | — (required) | Main checkout of the project. Landings and bean checks happen here. |
| `DROVER_ROSTER` | 3 coders + 3 reviewers on claude/codex/opencode | `name:harness` pairs. Role = last word of the name. |
| `DROVER_BUILD`, `DROVER_TEST` | empty | Build and single-test commands quoted into coder and reviewer briefs. `DROVER_BUILD` is also the build half of the cumulative push gate. |
| `DROVER_AUTOLAND` | `0` | `1` lets the watcher land approved beans and push green batches. Off = landing stays with the lead. |
| `DROVER_TYPECHECK`, `DROVER_TEST_CHANGED` | empty | Per-bean land gate: a fast typecheck, and tests for the changed files (`$CHANGED`). Empty = that gate is skipped. |
| `DROVER_TEST_ALL` | empty | Full suite for the cumulative push gate (`fleet-verify-main`). Empty + empty `DROVER_BUILD` = the gate refuses to green-light a push. |
| `DROVER_CODE_PATHS` | empty (any non-bean file) | `fleet-completed-audit`: ERE of paths that count as code. |
| `DROVER_CLONE_DIRS` | `node_modules` | Dirs APFS-cloned into a fresh worktree; also symlinked into land/verify scratch worktrees. |
| `DROVER_COPY_FILES` | `.env` | Untracked files copied into a fresh worktree. |
| `DROVER_QUEUE_KEYWORDS` | empty | Ready beans matching these words are queued first. |
| `DROVER_GENERATED` | empty | ERE of generated paths `fleet-claim` refuses to claim. |
| `DROVER_LEAD_SEATS` | `lead lead-backup` | Seats `fleet-yolo` never restarts. |
| `DROVER_HOME` | `~/.local/share/drover` | Queue, inbox, tasks, briefs, reports, snapshots, watcher state, logs. |
| `DROVER_WORKTREES` | `~/.herdr/worktrees/<repo name>` | Where herdr puts seat worktrees. |
| `DROVER_BEAN_PREFIX` | from `.beans.yml` | Bean id prefix; ids are `<prefix>` + 4 chars (`DROVER_BEAN_RE` to change). |
| `DROVER_RULES` | `~/.config/drover/PEERS.md` | If present, replaces the shipped skill body in `fleet rules`. |
| `FLEET_TICK` | `60` | Watcher tick, seconds. |
| `DROVER_YOLO` | `1` | Launch seats with the harness's approval-bypass flag. `0` makes every seat stop at permission prompts (and stop being unattended). |
| `DROVER_SWEEP_SCRATCHPADS` | `0` | Let `fleet-sweep` delete stale >= 50 MB dirs inside Claude Code scratchpads. Only on a machine where every session is a seat. |
| `FLEET_OPENCODE_MODEL`, `FLEET_CLAUDE_MODEL`, `FLEET_AGY_MODEL` | harness default | Spawn-time model pins. |

Spend caps live in `~/.config/drover/caps` (`opencode_daily_usd=`, `active_from=`), re-read every tick.

## Lessons

[`docs/LESSONS.md`](docs/LESSONS.md) is the part of this repository most worth reading even if you never run
it: some forty dated, measured lessons from running the fleet — why agents are never judged by their status
field, why every bean gets its own branch, why a `printf | grep -q` under `pipefail` idled 19 agents for four
hours, why a test that needs no input from the system is documentation. The agent-facing subset is
[`skills/fleet-peers/SKILL.md`](skills/fleet-peers/SKILL.md).

## Limitations

- **macOS only.** launchd, BSD `stat`/`date`/`df`, and APFS clones are assumed throughout.
- **Built for one person's fleet.** Roles are inferred from seat names, harness quirks (dialog texts, credit
  banners, resume flags) are the ones that fleet met, and the prompts assume the target repo has its own agent
  rules (CLAUDE.md / AGENTS.md). Expect to read the scripts.
- **Landing is manual by default.** drover routes work to "approved"; a human lead lands it. `DROVER_AUTOLAND=1`
  automates landing + push behind the gates above, but it never applies migrations and it pauses on a red suite.
- **Seats run with approval prompts bypassed.** That is the only way agents run unattended; it is also why
  worktrees, snapshots, and the never-push / never-migrate rules exist. Run it on a machine and a repository
  where that is acceptable.
- **Not included:** a shared MCP-server hub the original fleet ran to cut per-agent memory (a separate Node
  project). `fleet stale` still recognises a process named `mcp-hub/hub.mjs` and leaves its children alone.

## License

MIT — see [LICENSE](LICENSE).
