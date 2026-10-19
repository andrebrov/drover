# Lead runbook

How the fleet is run day to day, written for a **backup lead**: a second agent seat (conventionally
`lead-backup`) that takes over when the primary lead goes quiet. It is equally the checklist for a human
running the fleet by hand.

The primary lead touches `$FLEET_STATE/lead-heartbeat` (default
`~/.local/share/drover/watch/state/lead-heartbeat`) every few minutes while alive. `lead-watchdog` (launchd
`dev.drover.lead-watchdog`, no model, no tokens) checks that file every 5 minutes and sends the backup ONE
prompt beginning `TAKE OVER` when it is older than 45 minutes. **The backup does nothing on a schedule —
spends no turns — until that prompt arrives.**

## Standing constraints (never break these)
- Agents never apply migrations, never push, never touch live third-party accounts, never start dev servers.
  The LEAD applies migrations (dry-run first), lands, and pushes.
- Never commit the human's work in progress: `git commit --only -- <paths>`. Never `git stash` as a control —
  the stash stack is shared by every worktree.
- One bean per agent at a time; never one bean on two agents. Beans live on `main` or not at all.
- Never judge an agent by `fleet status` or a short pane read — judge by artifacts (`fleet-track check`: code
  commits, last-commit age, report mtime).

## The machinery
- `fleet-watch run` (60 s loop; launchd `dev.drover.fleet-watch` keeps it running). It drains the inbox (agents
  announce with `fleet-done`), routes cross-harness reviews, turns reviewer CHANGES into followups
  (`state/followups/<coder>`), sends PARTIAL (DONE with unchecked items) back to the same coder (3 strikes →
  `state/blocked`), retries unrouted DONEs (`state/reviews/<bean>`), refills free coders from the queue
  (files `<epoch>-<bean>`, ls order), harvests agent-filed beans onto main, and gates on budget and spend.
  **Patches to the script take effect only after a restart**:
  `launchctl kickstart -k gui/$(id -u)/dev.drover.fleet-watch`, when no `herdr agent prompt` child is running.
- Watch it: `tail -f ~/.local/share/drover/watch/watch.log` for
  ROUTED|FOLLOWUP|PARTIAL|BLOCKED|DISPATCH|SKIP|STALE|BUDGET|LOOP|APPROVED|NO-OP|SEAT.
  `state/blocked` is the lead's inbox: every line is a decision only a human or the lead can make.
- Levers the lead owns (all plain files in `state/`):
  `finish-first` (no new beans; fixes and reviews still flow), `harness-off` (one harness name per line — never
  dispatched to), `lead-owned/<bean>` (the lead is running it), `owner/<bean>` (delete to reassign deliberately),
  `approved/<bean>` (cleared by landing).
- Rules every agent loads: the `fleet-peers` skill (`fleet rules` installs it for each harness).

## Observer mode (agents self-land)

Current boundary (2026-10-19): this excludes the legacy dialog/model/landing/push lanes. The ordinary observer can dispatch explicitly queued work, recover remembered seat names and safely fast-forward a lagging shared main. When finite routing is enabled, the watcher invokes only its board view and the grant-bound router. Automatic self-serve fallback is retired.

Running the lead's own direct assignments alongside the watcher's automated lanes is the one combination
to avoid — see `docs/LESSONS.md`, "Two managers collide". Put the fleet in observer mode instead of running
both:

```bash
touch ~/.local/share/drover/watch/state/observer   # leave legacy action lanes; run fleet-observe instead
fleet-observe --board                               # one-screen read: what's live, what's stuck, what needs you
rm ~/.local/share/drover/watch/state/observer        # restore the watcher's normal dispatch/review/land/push lanes
```

While `state/observer` exists:
- **Assign work through the lead queue**, not `fleet assign` by hand: append a TAB-delimited line to
  `state/lead-queue` — `<bean>\t<harnesses-or-*>\t<prompt>` (the middle field is a comma-separated harness
  or seat-name allowlist, or `*` for anyone). `state/lead-pool` lists the seats eligible to take the next
  matching line, one per line; suffix a seat with `:named` to mean "only take a queue line that names this
  seat explicitly" (narrower remit) instead of matching on `*`/harness too. Every `fleet-observe` tick
  (including each observer-mode watcher tick) hands each free, in-pool seat the next queue line it matches
  and clears it from the queue — nothing to invoke by hand beyond keeping the two files filled.
- **Agents self-land**: once assigned, an agent owns its bean through push — see
  `docs/agent-land-loop.md` for the full loop (branch, code, bean file, rebase, cross-model review of the
  FINAL diff, push through the fleet-wide lock, prove ancestry, report).
- `fleet-observe` still alerts (dead harnesses, a seat stuck on its own question, a critical bean nobody
  owns, a DONE report whose SHA never reached origin) — it just never acts on what it finds. Read
  `watch/watch.log` for `OBSERVE` lines the same way you'd read the mutating lanes' `ROUTED`/`DISPATCH` ones.
- The background 60-second land/push loop skips `auto_push`/`auto_land` while `state/observer` exists, but
  `fleet-alert check` still runs every pass — a self-pause must still be able to wake someone.

## Landing (the lead's job)
1. Reviewer verdict `VERDICT: APPROVE` with `LANDABLE: YES` in `<reports>/<reviewer>-review-<bean>-by-<coder>.md`.
2. In a separate verify worktree: `git checkout --detach main && git cherry-pick -x <named commits>`. Bean-file
   conflicts: take theirs. A CODE conflict → followup to the coder, do not resolve it yourself.
   After any `--theirs` bean resolution and BEFORE `git add`, scan for conflict markers — markers left in one
   bean file break `beans` for EVERY command. Write the scan to a file and count it; do NOT put grep in an `if`
   with a pipe:

   ```bash
   grep -rIn --exclude-dir=.git --exclude-dir=node_modules \
     -e '^<<<<<<< ' -e '^>>>>>>> ' . > "$TMPDIR/markers.txt" 2>/dev/null
   wc -l < "$TMPDIR/markers.txt"    # must be 0
   ```

   Why the file (measured 2026-09-08, the lead's own gate was wrong): `if grep ... | head -5` takes its exit
   status from `head`, which always exits 0, so the gate fired on every landing. The mirror image: under
   `pipefail`, `cmd | grep -q` makes the writer take SIGPIPE (141) on a MATCH. Both are grep-in-a-pipeline.
   Test both directions of any gate once, deliberately, before you trust it.
3. Build (rc 0) and run the tests for the touched areas. If your shell is zsh, it does not word-split `$dirs`,
   so `jest $dirs` gets ONE pattern and prints "No tests found" (measured twice). Use bash, or `${=dirs}`, and
   grep the log for the test summary — an empty grep is a failed run, not a pass.
4. **Deletion gate, before the merge.** `git diff --diff-filter=D --name-only main HEAD` must be empty of
   non-bean paths. A branch that deletes a file main has, and removes its importers too, BUILDS GREEN and passes
   review (measured 2026-09-08: a deleted module silently ended a fallback metric, with an APPROVE on it). If a
   code file is deleted, do not land until the report names what replaced it and where its readers get the
   value now.
5. In the main checkout: commit any dirty `.beans` first, then `git merge --no-ff <verified sha>` with output to a
   file and `$?` checked; then **assert `git merge-base --is-ancestor <sha> main`** before
   `beans update <bean> -s completed`, and `rm state/approved/<bean>`.
6. Push. Then verify the deployment that push started reached success — "pushed" is not "shipped". A rollback
   means reading the new instances' startup logs.

## Automated landing (opt-in: `DROVER_AUTOLAND=1`)
The six manual steps above are exactly what `fleet-land-bean` + `fleet-verify-main` + `fleet-push` do, and
`fleet-watch` runs them once per tick when `DROVER_AUTOLAND=1`:
- `fleet-land-bean <bean>` cherry-picks the bean's own branch in an ISOLATED scratch worktree (never the shared
  checkout), takes theirs on bean-file conflicts, refuses any code conflict (→ `state/needs-land/<bean>` for a
  human), runs the per-bean gate (`DROVER_TYPECHECK`, `DROVER_TEST_CHANGED`), and fast-forwards LOCAL main.
  It never pushes and never resolves a code conflict — same rules as by hand.
- `fleet-verify-main` is the cumulative gate the manual steps lack a name for: full `DROVER_BUILD` +
  `DROVER_TEST_ALL` on a CLEAN worktree at main HEAD, so a batch of isolation-green beans can't ship a
  combined red. `fleet-push` runs it and pushes only on green; a red sets `state/land-paused` (landing stops
  until you `rm` it after the fix).
- A bean that can't land clean flags itself under `state/needs-land/<bean>`; clear the flag after you fix it.
- The deletion gate (step 4) is NOT yet in the script — keep running it by hand on anything the audit flags.
- Migrations are still never applied automatically. Autoland lands and pushes code; the migration steps stay yours.

## Cadence
- Read the board with `fleet-scoreboard`: landed/pushed today, approved-awaiting-land, in-review, and
  `fleet-completed-audit`'s count of beans `completed` on main whose code isn't landed. Low landed while
  inventory climbs = the bottleneck is integration, not capacity.
- Standup by artifact at every landing and at least hourly: landings on origin AND in production, in review,
  coding, what is blocked on the human. Write it to `<reports>/standup-<HHMM>.md`. `fleet-standup-auto` writes an
  hourly fallback while tasks exist; it is a floor, not a standup.
- Retro at sprint close, or when the same class of failure repeats twice in a day: `fleet-retro`, then a dated
  section in your rules with class counts and a metric that can fail.

## One branch per BEAN, not per agent (2026-09-08)

`fleet assign` forks `fleet/<agent>-<bean>` from main at dispatch, so a bean's named commits apply to main by
construction. Before this, each agent's single branch accumulated 14–20 beans and 71–163 commits ahead of main;
no tip could be landed and three of three approved beans needed a rebase round (see `docs/LESSONS.md`).

It only switches a CLEAN tree; a worktree with uncommitted tracked changes keeps its branch and the lead gets a
warning. **Do not remove worktrees to solve branch problems**: worktrees are what limited a `git reset --hard`
accident to one agent. In a shared tree it would have destroyed every agent's work at once.

## Current evidence and portable adapters

- `fleet-claims-audit --json`: read-only native ownership audit. Exit 0 agrees, 1 confirms a closed bean still owned, 2 means UNKNOWN. It never repairs a record.
- `fleet-measure --no-render`: publish the delivery scoreboard and stamped JSON. No application render command or SQL is built in. `fleet-standup` includes the fresh measurement, ownership result, optional status checks and local lesson ledger.
- `fleet-observatory --output /absolute/path/observatory.html`: write a local, inert kanban view. `--json` writes no HTML. It cannot publish over native task/question/decision state.
- `fleet-precedent <bean>`: bounded historical evidence from landed beans and native review reports. It never changes a decision or gates an action. Turn it off with `FLEET_PRECEDENT=off`.
- `fleet-main-tripwire`: run the configured project gate in disposable checkouts. The default uses the existing slot pool and clean verifier. A custom `DROVER_TRIPWIRE_GATE` adapter owns the same provisioning/fixture grants as the push hook; its exit contract is 0 GREEN, 1 RED, 2 UNAVAILABLE (3/75 busy and 124 timeout are also unavailable). It must return 1 for compiler/test failures. Missing lease/infrastructure messages are reported as unavailable. No database driver is guessed.
- `fleet-main-tripwire --check-freshness`: inspect the last evaluated SHA/time and main distance. A pinned `--ref` experiment needs isolated `TRIPWIRE_STATE` and `TRIPWIRE_ALERTS`; it cannot replace the normal monitor’s proof.
- `fleet-lessons add ...`, `check`, `standup`, `retro`: operate on this fleet’s evidence/check ledger. No private team policy is seeded by installation.

Shared dependency preparation is opt-in (`DROVER_SHARED_NM=1`). Before a project build/push, call `fleet-shared-nm check REPO HEAD`; on mismatch run the printed `link ... --archive-real` repair. Keep a single centrally built graph directory in `DROVER_GRAPH_DIR`; preparation links to it and preserves an old local graph in the prep backup.

Finite root-granted routing is disabled unless `state/safe-route/enabled` exists. It runs inside the existing watcher tick, never as a second scheduler. Grants and completions bind exact job/brief/source/task/report bytes and generation. Unknown budget, native availability or memory pressure retains the job. Observer routing does not enter legacy vendor spend queries or the legacy mutation lanes.

Update only through `install.sh` after committing and publishing the source. Preflight recognizes known main-history versions; reconcile unknown installed patches first. `--force` cannot waive drift. The installer still symlinks tools and leaves configuration/state in place. It never runs a runtime hook patcher.
