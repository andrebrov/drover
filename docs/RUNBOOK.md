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
