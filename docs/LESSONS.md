# Lessons

These are the dated, measured lessons from running a fleet of 10–25 coding agents (Claude Code, Codex,
opencode, Grok, Cursor, Antigravity) on one production codebase, August–October 2026. Each one was
written the day it cost something, by the lead or in a retro, and most end in a rule that can visibly fail.

They are the reason drover's scripts look the way they do. The operational subset that agents load is
in [`skills/fleet-peers/SKILL.md`](../skills/fleet-peers/SKILL.md); this file keeps the evidence.

Names like `claude-coder` or `codex-reviewer` are seats (one agent in one herdr pane). "The lead" is the
Claude session the human talks to; "the human" is the person who owns the project. Product-specific
detail from the original codebase has been removed; the numbers have not.

---

## Coding lessons (they apply to any agent, fleet or not)

### Migration numbers — check every fleet branch, not just main (2026-08-31)

The highest migration number on **main** is not the next free number in the fleet: another agent may
already have claimed it on their branch. This produced four collisions in one day. Before choosing a
number, list the migrations on every fleet branch:

```bash
git for-each-ref --format='%(refname:short)' refs/heads/fleet/ | while read b; do
  git ls-tree -r --name-only "$b" -- <your migrations dir> | sed 's#.*/##'
done | sort -n | tail -3
```

Take the next number above the highest across main AND every fleet branch. A duplicate check in the build
catches it only after merge, which is the expensive moment.

### Commit before you report, and name the SHA (2026-08-31)

Three times in one day a report described work that was not yet committed — once a checker claimed as
checked in existed only on disk, once a migration number the branch did not yet carry, once a whole bean
reported done while its files sat uncommitted in a shared checkout where another agent's branch switch
put them at risk.

A report is a claim about the branch. Commit first, then write the report; put the SHA in it and make
sure every file you name is in that commit (`git show --stat <sha>`); if you change anything afterwards,
update the report AND the SHA. A reviewer reads the branch, not your description of it.

### Adding parameters to inlined SQL breaks every literal `%` (2026-08-31, third occurrence that day)

Three breakages, one root: a migration failed in production on 73 `format()` calls carrying literal `%`
from copied DDL; a tool hit it the same day; a fix re-introduced it in the same file by adding a
parameter dict to a query that had always contained `LIKE '%,x,%'`. With no parameters those wildcards
were inert; supplying parameters turned them into placeholders and the query raised `TypeError`.

**Adding parameterisation to SQL that was previously fully inlined is a breaking change to every literal
`%` already in that string.** Grep the assembled SQL — including anything a helper appends — for `%` and
escape it (`%%`), then write a test whose config actually reaches the branch that produces the wildcards.

### Never use a whole-tree git command to undo a single-file change (2026-09-01)

An agent ran `git checkout-index -f -a` to revert one mutated file. That rewrote the ENTIRE working tree:
the human's uncommitted work, in-progress migration work, and the agent's own edits. Its scratch backup was
then refreshed from the already-reverted files, so the backup was worthless too.

1. Mutation testing restores from a per-file copy, never through git: `cp f f.bak`, mutate, run, `cp f.bak f`.
2. Never run a command whose blast radius is the working tree to fix one file: `checkout-index -a`,
   `checkout .`, `reset --hard`, `clean -fd`, `stash` without a pathspec.
3. A backup taken before a destructive step lives outside the tree and is never refreshed by a later step.
4. If you do destroy something, say so FIRST in your report. This agent did, which is what made the
   restore possible.

### NaN and its string form are the same defect class as a fabricated zero (2026-09-01)

Three bugs in one day shipped a value that looked computed and was not: a headline reading `NaN% off`
(`Number('20%')` is NaN — a percent carrying its own sign parsed as if bare); NaN codes in user-visible
output (33 reports); and coverage reported as 100% when it was 33%, because pandas returned SQL NULLs as
`float('nan')` and `str(nan)` is `'nan'` — a non-empty, truthy string that passed every emptiness check.
The last was dtype-inference dependent: a second environment saw `None` instead and could not reproduce it.

- Normalise at the parse boundary, not the output: one canonical internal representation.
- Test absence for NaN explicitly and for the string forms: `nan`, `none`, `null`, `<na>`, `nat`, empty.
- NaN never reaches user-visible output. Unparseable input is UNAVAILABLE with a reason — never NaN, never a silent 0.
- Verify against real data. All three were invisible in unit tests and obvious against the warehouse.

### A test that inspects source text is a spellchecker (2026-09-01)

One bean went three rounds with the guard one level too shallow each time: renaming a label (a renderer
re-created the defect), banning an identifier (a reviewer wrote the same computation under a new name),
grepping the source for a token (it guards the NAME, not the MEANING). **The discriminating test is a
payload assertion or a captured query, never a source string.**

Corollary: a green suite is not evidence the mutations discriminate. The bean reported six mutations
pinned; on re-run five were. A later round had all three new mutations survive while the suite was green,
because the test called a helper with pre-converted values and never exercised the real call site.
Re-run every mutation yourself before reporting all-green.

### A rolling-window total must never be summed (2026-09-01, second occurrence)

A metrics table stored the SAME metric at several spans (1d, 1w, 4w, 30d, …), each a rolling TOTAL, not
a slice. Summing across spans multiplied the answer by ~270x: a careful reviewer omitted the span filter,
got 1,885,997 where the truth was 6,889, and reported a discrepancy that was an artefact of its own query.
The first instance: a revenue column in the ads tables was a repeated window total, and summing it
inflated one account's ROAS twelvefold. Before aggregating any column, check whether the row is a slice or
a running total — the column name will not tell you.

### A test must not grade itself against the thing it tests (2026-09-01)

```ts
expect(used).toBeLessThanOrEqual(WINDOW_LIMITS[angle]);
```

Loosening the cap loosened the assertion with it. Changing one cap from 1 to 8 — re-admitting the exact
production failure the bean existed to fix — left **325 tests passing**; so did emptying the limits. **A
value that encodes a product decision is asserted as a LITERAL.** And at least one test must make that
value load-bearing, from a scenario that can actually occur.

### The cheapest test of a test: does it need input from the system? (2026-09-01)

An agent that wrote two self-grading tests in one day found the shared shape. Its words:

> **If a test needs no input from the system, it is documentation.**

A counts fixture asserted properties of a literal it defined itself — no corpus row was ever classified;
changing a live adjudication left all 41 tests green. The cap test above took no window as input. The
replacement is what a real pin looks like: all 3,275 rows checked in as a golden corpus, classified by the
real classifier inside the test, and compared against the published counts.

### Prove a claim by running the path, not by reading it (2026-09-02, retro round 1)

Across four branches in one night, **8 of 19 review findings were not broken code — they were statements
about code that stopped being true the moment someone ran them.** "Typed so vocabulary drift is a compile
error" (a subset compiles clean; the reviewer added a member and `tsc` passed). "Already paired — verified"
(the check was a 1.6:1 visibility test, not a 4.5:1 contrast guarantee). Mutation counts carried forward
from a previous round and published as this commit's evidence.

Before you report, execute every load-bearing sentence: claim a compile error → add the member and watch it
fail; cite a number → re-run it at the SHA you are reporting. Two corollaries: a surviving mutant is a
result only you can classify (gap in the tests, or a broken mutant) — disclose it; and **validate before
you normalise** (an all-null payload normalised to `{}` skipped an invariant a single zero would have tripped).

This rule is measurable on purpose: the claim class was 8/19. If it does not fall, the rule is inert and
should be deleted rather than restated.

---

## Voice: blunt

The lead reads twenty reports a night. Padding costs it time and hides the one sentence that mattered.

- **Verdict first, evidence second.** Never a paragraph of context before the finding.
- **No hedging.** Ban "maybe", "possibly", "it seems", "I think". Say the unsure thing as a fact:
  "not verified: X", "I did not run Y". Uncertainty is a datum, never a cushion.
- **No praise, no apology, no filler**, no restating the task, no announcing what you are about to do.
- **"No" is a full answer.** If the bean, the scope or the fix is wrong, say so in one line and say what is right.
- **Numbers and file:line, not adjectives.** "3 of 5 fallbacks identical" beats "several looked similar".
- Blunt about the work, never about the person.

---

## How the fleet works: Extreme Programming (adopted 2026-09-03)

Adopted after a day whose failures were the ones XP exists to prevent: 166 commits unlanded while reviews
piled up, four test suites running zero tests, three approved commits that did not pass their own tests,
and a production pipeline silently stopped for two days.

- **A branch that cannot land is not done.** The queue reached 166 unlanded commits across 21 branches;
  they collided, went stale against a moving main (24 commits in one day), and three needed the same
  conflict resolved three times. Land a bean's work the day it is approved.
- **Every commit builds on its own.** The lead lands by cherry-pick; a set where commit 3 needs an export
  added in commit 7 is a tangle.
- **A test that does not run is worse than no test: it reports success.** Four suites on main executed
  ZERO tests while looking like ordinary failures — three from partial ESM mocks (`jest.unstable_mockModule`
  that omits a named export is a LINK error), one from git's hook environment leaking in. Run the suite
  the way the gate runs it: two suites passed alone and failed in the full run.
- **A reviewer's verdict is worthless without execution.** Cross-model review caught design problems all
  day and never ran the thing; three approved commits failed their own tests. Before APPROVE: run the build
  end to end and the tests for every touched file.
- **Collective ownership.** Any agent fixes any branch; a red main is everyone's emergency (one broken mock
  on main blocked every push touching that route while five agents worked around it).
- **Simple design.** Fix the root cause in the shared function, never a guard per caller. Add no field,
  tool or store that no measurement asks for.
- **A number you did not measure is not a claim you may make.** Publish the losing measurement too.

---

## Working across worktrees

You cannot see the other agents' edits. On 2026-09-03 six unlanded branches had edited the same test file,
six the same schema template, and three needed the same conflict resolved three separate times.

- **Claim files before you edit them** with `fleet-claim take <you> <bean> <path>...`. It is advisory, not a
  lock: it tells you to pick different work, or to pair, before the conflicting edit exists. `fleet-claim hot`
  lists files several unlanded branches are editing. Claims of completed beans are reaped from main.
- **Never commit a generated file.** Six branches carried conflicting versions of two generated catalogs;
  every hunk was noise and the build's drift check refused the result. The lead regenerates at land time.
- **Rebase before you report, not after.** A branch not rebased in the last hour is reviewed against a tree
  that will never ship.

### `core.worktree` in the shared git config hijacks linked worktrees (2026-09-03, re-diagnosed 2026-09-07)

In a brand-new worktree:

```
$ touch .probe2 && git add .probe2
fatal: pathspec '.probe2' did not match any files
```

`git add` was a no-op. A coder lost a whole rebase to it and correctly refused to hand over a branch it
could not verify. The first diagnosis blamed a token-saving git proxy; it was wrong. The shared
`.git/config` of the main checkout carried `core.worktree=<another path>` with
`extensions.worktreeConfig=true`. Any linked worktree without its own `config.worktree` pin inherits it:
git resolves paths, the index and `add`/`commit`/`diff` against THAT tree while HEAD and refs stay the
worktree's own — so a real file is "not found", or a commit carries a different tree than the one you
edited. Three worktrees resolved `git rev-parse --show-toplevel` to somewhere else.

```sh
git rev-parse --show-toplevel   # must print THIS worktree's path
git config --get core.worktree  # empty is fine; a value pointing elsewhere is the bug
```

If it points elsewhere, pin the worktree (`git -C <wt> config core.worktree <wt>`, or pass `--git-dir` and
`--work-tree` explicitly). Do not use `git stash` as an A/B control while diagnosing: the stash stack is
shared by every worktree. **If a git command's effect matters, verify the result, not the exit code.**

---

## Where things live, and why not /tmp

Reports, briefs, the queue and the ledger live under `~/.local/share/drover`. Nothing under `/tmp`: a reboot
wiped it twice (2026-09-02, 2026-09-05); the second time it killed a round of in-flight work on seven tasks,
because the reports were the only record of what each agent had found.

## Who does what — role charter (2026-09-05)

The human corrected the lead: the design agent had been given an engineering gate to specify and the PM was
being asked for things a coder should write.

| Role | Owns | Never does |
|---|---|---|
| **pm** | Requirements: what the user needs, why, and what "done" means. Works with the architect. | Writes code. Ships a branch. |
| **architect** | Requirements and system design: proposals, boundaries, state machines, data contracts. | Implements. |
| **design** | UI/UX. The only seat that renders a page and LOOKS at the pixels. | Specifies engineering gates or backend contracts. |
| **coder** | Implementation and its tests, on a branch, against a bean. | Applies migrations. Pushes. Starts dev servers. |
| **reviewer** | Runs the build and tests on someone else's branch, then a verdict. Always a different harness. | Edits code. Commits. |
| **analyst** | Measurement: numbers with the query that produced them. | Opinions, ratings, recommendations. |
| **sre** | Production logs. Fixes application code; PROPOSES infrastructure. | Restarts a production service. |
| **judge** | Rules on conflicts between agents and on evidence quality. | Takes sides without re-running the evidence. |

pm + architect establish the requirement, design how it looks, a coder builds it, a reviewer on another
harness verifies it, the lead lands it. Skipping the requirement step is how two agents built the same
module twice in one week.

## The coding process (2026-09-06)

Measured: **20 of 20 fleet branches contained none of main**, several 400+ commits behind. The prompt was
the cause — it said "never git checkout/reset/stash", which forbade the merge that would have kept branches
current. The order now, and why:

1. **Merge main first.** A stale branch is reviewed as code that will never ship, and its merge deletes work
   that landed after it diverged — one branch would have removed 652 lines, including 399 of a test file.
   Merge LOCAL `main`, not `origin/main`: origin was 7 commits behind local main once, and four agents
   truthfully reported "rebased" against the wrong target.
2. **Check it does not already exist.** Two coders wrote the same new module for one bean with incompatible
   APIs; only one could land.
3. **Build to the requirement** where a pm or architect wrote one. Disagree in writing.
4. **Prove the fix fires**: revert, watch the new test fail, restore, watch it pass. This caught a fail-open
   gate in the lead's own commit.
5. **Paste the build and test output.** A command you did not run cannot appear in the report.
6. **Finish with REBASED / VERIFIED / DONE.** `REBASED: NO` means the work is not finished.

Why the review gate alone was not enough: reviewers returned CHANGES nine times in a row; a judge audited
them and ten of eleven top findings were real. Six were unlandable, not imperfect — a branch deleting a
module main imports, a build stopping at a parity gate, an INSERT that raised on every tenant.

## Staying inside the budget (2026-09-07)

Harnesses run on different meters. opencode (prepaid credits) and codex (usage cap) can run out mid-task;
Claude, Grok and Antigravity ran on subscriptions with session limits.

**A spent budget is invisible from `fleet status`.** On 2026-09-07 opencode's credits ran out and all ten
opencode agents reported `done` with no work, no report and no error — indistinguishable from finishing. The
lead dispatched into that gap repeatedly. Codex had failed the same way earlier that day. `fleet-budget`
reads each pane for the balance and cap messages and counts only NON-working agents, because a message left
in scrollback after a top-up reports a solved problem as live.

1. Put metered harnesses on work that fits in one turn; long iterative work belongs on a subscription.
2. **Never dispatch the same bean to two agents.** It happened four times in one week; only one copy can land.
3. `done` with no report and no commit is not a finished task.
4. Reviews are cheap, rounds are not. Diagnose the defect in the brief — root cause, file, line. Every brief
   the lead pre-diagnosed landed in one round.
5. Verify on the combined merge, once — integration defects only appear when branches meet.

### A spend cap, measured in (2026-09-18)

opencode spent **$290, then $376** on two consecutive days, against $27–$82 on each of the four days before.
At or over `opencode_daily_usd` in the caps file, fleet-watch stops new opencode dispatches and reviews while
running work finishes; the pause logs once when it starts and once when it lifts.

**The first version of the cap measured the wrong thing, and so did the headline number.** It read
`opencode stats --days 1`, which reported **$952** for that day. That command sums the *lifetime* cost of every
session touched in the window: at the moment it said $856.01, `sum(session.cost)` over sessions updated in the
last 24 h was $856.01 to the cent, while the per-message cost inside those 24 h was $212.60. So a long-lived
session that did one cheap thing today counts its whole history, and yesterday's spend blocks today. The cap
now sums per-message cost since local midnight straight from opencode's database — an indexed query that takes
milliseconds instead of a multi-second stats run. The number was quoted for a day before anyone asked what the
instrument added up. (`ops/oc-compact.sh` exists because that database had grown to 133 GB.)

## Idle non-coders go hunting (2026-09-07)

The human: "when sre/architect/pm/code-reviewers are not working it would be great to send them for bug
hunting." `fleet-hunt [bugs|ux|perf|both]`. Three bars: **bugs** — file:line, trigger, wrong output;
**ux** — a named route or component plus the user's goal; **perf** — a BEFORE-number and the exact command.
The bar is proof, not suspicion; an honest empty hunt is a real result. Hunters report; they do not fix.

## Never judge an agent by its status or its pane (2026-09-07)

**`fleet status` reports `done` for an agent that is actively mid-turn.** Its pane at the same moment showed
"esc interrupt". The lead re-dispatched over busy agents for hours on that signal. The pane is no better:
scrollback keeps stale text, and a prompt sitting UNSUBMITTED in the composer looks identical to one taken.

**Two signals cannot lie: a commit on the agent's branch, and a report file on disk.** `fleet-track` records
the branch SHA at dispatch and checks exactly those. COMMITS=none and REPORT=none after ~15 minutes means the
dispatch did not take.

1. **Dispatch serially, and wait.** A parallel `&` loop silently lost six of six prompts.
2. **Long multi-line prompts do not reach an agent; short ones do** (three of four review dispatches lost
   2026-09-06). Write the brief to a file and send a one-line pointer.
3. An agent that returns `done` with nothing to show has not finished anything — check `fleet-budget`.

## Announce when you finish — push, not poll (2026-09-07)

Before `fleet-done` and the watcher, the lead polled in 8-minute sleeps. Measured from the ledger, 650 gaps:
**median 44 minutes between one dispatch and the next to the same agent; p90 285 minutes**, for tasks that
take 10–20. Every transition waited for the lead to notice. Now an agent runs
`fleet-done <me> <bean> DONE|BLOCKED|CHANGES "<one line>" <report>` and fleet-watch reacts within a tick.
BLOCKED says precisely what would unblock it; the one-line summary is read.

## The beans board follows `main` — never delete a bean on a branch (2026-09-07)

One coder branch carried 23 bean deletions (swept in by a code commit made with a shared index). Merging
main cannot resurrect a file the branch deleted, so the coder reported a bean "absent from the board" while
it sat on main; four reviewer worktrees were 100+ beans behind. A bean file is only created, edited or
status-changed on a branch — never deleted. Before saying a bean does not exist:
`git ls-files --with-tree=main .beans | grep <id>`; if it is on main and not on disk,
`git checkout main -- .beans`. fleet-watch refuses to dispatch a bean that is not on main, and
`fleet-harvest-beans` copies beans that agents filed in their worktrees onto main every tick.

## Lead: never `fleet-track clear` while fleet-watch runs (2026-09-07)

The watcher judges "free" by the absence of the task file. Clearing it to re-nudge an agent opens a window in
which the watcher dispatches a fresh bean to the same agent — one coder received three beans in four minutes.
Re-nudge with the task file in place; to reassign, write the new task file first, then prompt.

## One branch per BEAN, not per agent (2026-09-08; supersedes "one branch per agent" of 2026-09-07)

On 2026-09-07 the rule was one branch per agent, because two reviews came back CHANGES only because the work
sat on a per-bean branch the reviewer was not pointed at. A day later that turned out to be the root cause of
the landing conflicts. Each agent's one long-lived branch accumulated every bean it touched:

| branch | commits ahead of main | distinct beans on it |
|---|---:|---:|
| codex coder | 163 | 16 |
| grok coder | 100 | 20 |
| agy coder | 88 | 14 |
| claude coder | 71 | 18 |

A tip could never be landed (it carried fifteen other beans' unapproved work — three reviewers named a tip
landable that day), and the named commits inside it stopped cherry-picking because repeatedly merging main
rewrote their context: three of three approved beans needed a rebase round. `fleet assign` now forks
`fleet/<agent>-<bean>` from main at dispatch, and the reviewer prompt names that branch. It only switches a
CLEAN tree — never destroy uncommitted work to tidy a branch. Worktrees stay: they are what limited the
`reset --hard` accident below to one agent.

## Retro round 2 (2026-09-07 20:30) — classes, counts, metrics

Evidence: the watcher log (63 announcements), 13 review verdicts, 189 commits.

| Class | Count | Metric that can fail next sprint |
|---|---|---|
| Work invisible to the board or reviewer (bean staged not committed; beans deleted on a branch; work on an unexpected branch) | 4 of 13 verdicts (31%) were branch-only CHANGES | branch-only CHANGES = 0 |
| Announcement with no consequence (DONE dropped 2, fallback-seat CHANGES ignored 1, followup overwritten 1) | 4 of 63 | every INBOX line gets ROUTED/FOLLOWUP/SKIP/STALE/BLOCKED within 2 ticks |
| Dead harness dispatched to (10 opencode seats out of credits, 20–43 min unseen) | 10 dispatches wasted | out-of-credits to BUDGET line ≤ 60 s |
| Detection ≠ correction (a bean closed on "56 tests" while all 15 real renders were wrong; stored scores inflated 102/165) | 1 bean re-opened | quality beans closed without a render proof = 0 |
| Lead errors: zsh word-splitting (4), `fleet-track clear` race (3 beans to one agent), stale queue (5), re-nudging a blocked coder (3), watcher patches inert until restart (5) | ≈20 | wasted dispatches per hour ≤ 1 (≥ 8 in the first hour) |

Rules that can fire: every announcement gets a consequence line within 2 ticks; the lead writes shell with
lists in bash, not zsh (zsh does not word-split `$var`, so `cmd $list` got one argument); the retro instrument
must read today's artifact paths. Standup at every landing and at least hourly, by artifact, never by status.

## Quote every heredoc: `<<'EOF'`, never `<<EOF` (2026-09-07 20:27)

An unquoted heredoc in a `beans update` call let the shell expand backticks inside bean prose and EXECUTE a
migration command against production. Three migrations applied by an agent. Any heredoc whose body could
contain `` ` `` or `$` is quoted; prose with commands in it goes through `--body-file`.

## Reviewers: judge the named commits on main, not the coder's whole branch (2026-09-07)

Three reviews came back CHANGES for a build failure belonging to a *different* bean on the same branch.
Cherry-pick the named commits onto a detached main, build and test THERE, and verdict on that.

## A DONE with unchecked checklist items is PARTIAL (2026-09-07)

Nine reviewer rounds in one day were spent confirming "checklist items still open". `fleet-done` now
downgrades a coder's DONE to PARTIAL when the bean file still has `- [ ]` items, and the watcher hands it
straight back to the same coder without spending a reviewer. Three PARTIALs on one bean stop the loop and
go to the lead. (2026-09-18: when the open items were human-only — apply a migration, run against a live
account — telling the coder "the reviewer returned CHANGES" produced three 3x loops. The followup now asks
for the open items and says to announce BLOCKED for human-only ones.)

## Lead: a landing is proven by ancestry, never by the merge command's exit (2026-09-07 22:27)

`git merge … | tail -1 && beans update … -s completed` closed a bean as landed while the merge had failed on
a bean-file conflict — the pipe hid the exit code, and a migration was absent from main for 20 minutes.
After every merge: `git merge-base --is-ancestor <verified-sha> main`, and only then close the bean and push.

## Lead: a landing is shipped only when the deployment says so (2026-09-07)

Six deployments rolled back on a one-line import-time error while fourteen pushes "succeeded". After every
push the lead checks the deployment started by that push and reports two numbers: landings on origin,
landings in production. "Pushed" is not "shipped".

## Landing conflicts go on a fresh branch, never a merge of main (retro round 3, 2026-09-08)

Two coders "resolved the lead's landing conflict" by merging main into their branch. The lead cannot pick a
merge commit, so both beans lost a full round. Create a fresh branch from main, cherry-pick the named commits,
resolve there, build and test, and report its SHAs.

## A bean names its branch; a reviewer judges that branch (retro round 3)

A bean's commits lived on one branch, the bean said so, and the reviewer diffed another and returned CHANGES
on nothing. A reviewer refuses to review a branch with none of the bean's commits
(`git log main..<branch> --grep <bean>` must be non-empty).

## Fleet tooling is production: self-test a guard both ways before it goes live (retro round 3, 2026-09-08)

A new version of the live `fleet` script used `printf | grep -q` under `pipefail` in its bean-on-main guard.
grep exits on the first match, printf takes SIGPIPE, pipefail reports 141 — so EVERY bean read "not on main":
followups stopped dispatching and **all 19 agents sat idle ~4 hours**. `bash -n` passes it; an interactive
test passes it (no pipefail). Any change to fleet tooling ships with a self-test on one input the guard MUST
accept and one it MUST reject. Agents edit a copy; the lead installs it.

The mirror image bit the lead's own landing gate: `if grep ... | head -5` takes its status from `head`, which
always exits 0, so the conflict-marker gate fired on every landing. Both are grep-in-a-pipeline. Redirect to a
file and count it.

## State that says "in flight" is written only after the send succeeded (retro round 3, 2026-09-08)

The watcher marked an agent busy on the line after the dispatch, without checking the exit code. Two separate
`fleet` failures then pinned all five coders for hours with full followup queues and a silent log. Record the
claim only after the action that justifies it returned success; on failure, put the work back.

## Name EVERY commit a bean needs, and prove the set stands alone (retro round 3, 2026-09-08)

One bean's "landable commits" named only the TEST commit; cherry-picked onto main the test failed because the
implementation commit was never named. Another bean was APPROVED while no implementation commit existed at all.
A bean's commit list is the set that makes its tests pass on a detached main; an APPROVE on a bean with zero
commits is an automatic CHANGES.

## When a review is re-routed, the old seat must be freed (retro round 3, 2026-09-08)

The lead re-routed two reviews by hand and left the original seats holding the bean; both sat idle for 7–9
hours while the log repeated "no free cross-harness reviewer". `reap_tasks` now frees them; by hand, write the
new seat's task file AND remove the old one, in that order.

## A landable SHA is never a merge commit (retro round 3, 2026-09-08)

A review named `Merge branch 'main' into fleet/…` as the commit to cherry-pick. Before writing a SHA into a
verdict, check its subject is the work, not a merge.

## Do not copy the repository into your scratchpad (retro round 3, 2026-09-08)

One agent's scratchpad held **13 GB in ~30 full repo copies**, none ever deleted; the machine reached 22 GiB
free and the human noticed before the lead did. You already have an isolated checkout. `fleet-sweep` reclaims
anything over 50 MB untouched for 2 hours, and (2026-09-18, 51 GB of stale review worktrees, disk at 91%)
review worktrees that are stale, clean, and unused — but a sweeper is a backstop, not permission.

## "It merges cleanly" is not evidence for landing (retro round 3, 2026-09-08)

A review proved landability by MERGING the branch onto main. The lead lands by cherry-picking named commits;
a merge resolves against the common ancestor, a cherry-pick replays the patch, and the same set that merged
cleanly conflicted. Prove landability with `git checkout --detach main && git cherry-pick -x <named shas>`.

## "main moved while I reviewed" is not a blocker (retro round 3, 2026-09-08)

In a fleet landing ~30 beans a day, main advances during every review; if that is a blocker nothing is ever
landable. Staleness is the lead's problem, solved by re-verifying against current main before every merge.
Say `LANDABLE: YES` when the work is sound and name the base you tested.

## The sprint board's owner column is a guess, not the assignment (2026-09-08)

`fleet-sprint start` round-robins beans across the roster, including harnesses that are off. The only truth
about who holds a bean is the task file in `$FLEET_TASKS/<agent>`.

## Agents lose uncommitted work; snapshot it mechanically (2026-09-08)

A coder ran `git reset --hard` in its own worktree and destroyed ~87 uncommitted tracked modifications.
Unrecoverable, and not a git defect: unstaged edits never reach the object database, so `fsck`, the stash
list and the reflog all correctly find nothing. The rule "never reset" already existed and was violated anyway;
a rule that depends on compliance is not a safety net. fleet-watch runs `fleet-snapshot` every 20 minutes
(dirty tracked files only, ~48 MB a pass, kept 48 h). Deliberately not a git wrapper: intercepting git for 15
agents to stop one rare command risks breaking every agent's workflow.

## Retro round 4 — cadence (2026-09-13)

The 12-hour retro found 121 unique review headlines: 68 artifact/claim mismatches, 30 test/evidence defects,
11 landing/integration defects, 8 migration/schema defects, 5 generated-artifact violations. The written rules
existed; prose did not enforce them. Two lead failures had one shape — state inferred instead of evidenced:
informal status updates replaced the hourly standup, and a rollout was counted as done because accounts had
been provisioned, while zero seats had used them. Provisioned is not used.

- A standup exists only as a file that names active tasks, exact refs, reviews, blockers, origin landings and
  production landings. `fleet-standup-auto` writes a fallback every hour while tasks exist.
- A retro is complete only with class counts, the prior rule that failed, a rule that can be mechanically
  violated, and a next-session metric. An unfilled `fleet-retro` scaffold is reported as INVALID.
- Before review dispatch, verify every named commit exists on the declared branch.

## Never inspect full process argv or environment (2026-09-13)

`npm exec` copied inherited credentials into child-process command lines, and a liveness check using `pgrep -fl`
printed them while looking for a test runner's PID. The diagnostic caused the exposure. Liveness comes from
durable logs, PID-only checks, `launchctl` state, or `ps -o pid,ppid,etime,comm`. Never pass credentials through
command-line arguments. (fleet-yolo does read a process environment — for `HERDR_PANE_ID` only, and prints none of it.)

---

## The watcher, by incident (2026-09-08 to 2026-09-18)

Each of these is a comment next to the code in `bin/fleet-watch`; collected here so the reasons survive refactors.

- **The reaper releases only on provable facts** (2026-09-08: five seats pinned 45–530 min; the only symptom
  was "no free cross-harness reviewer", repeating). It never reaps a `working` seat — a grok reviewer was
  released mid-review because the bean id was already completed from an earlier landing while a NEW review ran
  under the same id. `done` is transient in herdr; gating release on `done` alone pinned 7 seats for 145–7864
  minutes (2026-09-16), so `idle` and `MISSING` count too.
- **Credit-dead seats** accept a prompt, go `working`, and do nothing (2026-09-08: two beans sat 48 and 21
  minutes in dead seats; a review was routed to a dead architect seat). Both the dispatch and the review path
  read the pane first; a marker with a 30-minute TTL stops the tick re-reading a dead seat every minute.
- **Dialogs are cleared only when the match is exact.** Matching the generic "Enter to confirm" also matched
  "Do you trust this folder?", whose cursor sits on "No, exit" — a bare Enter there killed three seats (2026-09-16).
- **The queue is a directory, so one bean can be queued twice** (2026-09-08). Popping a bean drops every
  entry for it; a bean is re-checked for readiness at pop time, because a queue built before the lead gated a
  bean still dispatched it.
- **One bean, one coder** (2026-09-18: approved-but-unlanded beans were re-dispatched to fresh coders who
  re-implemented them; two beans ended up on three branches each). A bean belongs to its first coder; handover
  only when the owner is missing, credit-dead, or silent for 72 hours — and it is logged.
- **An APPROVE holds the bean out of the queue until landed** (2026-09-18: one approved bean went back to a
  coder every few minutes to re-verify already-landed work).
- **Two no-op rounds hold a bean for the lead** (2026-09-18: reports claiming "DONE, landable" **for the ninth
  time**, each one a paid round that re-verified existing commits).
- **A late DONE still routes its review** (2026-09-18: the reaper freed a seat on seeing its report a minute
  before its `fleet-done` landed; dropping that DONE lost a bean's review).
- **finish-first** (2026-09-18, the human: "before starting new work we will need to finish work in each
  worktree and then merge into main and close"). While `state/finish-first` exists no new bean enters a seat;
  fix rounds and reviews still flow.
- **herdr resumes agents after a reboot without their flags** (herdr 0.9.1), so every reboot dropped approval
  bypass; `fleet-yolo` relaunches idle non-yolo seats every 5 minutes, resuming their own session.
- **`fleet status` exits 1 whenever a seat is MISSING**; under `pipefail` that killed `fleet-budget` on its
  first line and the watcher read "no budget problem" for every harness (found 2026-09-18).
- **Memory** (2026-09-02): 168 node processes (47 GB) plus 16 MCP servers at ~2 GB each put a 128 GB machine at
  183 MB free every day for a week until the kernel watchdog panicked it. `fleet stale` finds orphans (pane gone),
  leftovers (dev/test processes under an idle agent) and heavy processes.

## Backpressure and the merge queue (2026-09-21)

The fleet spent two days busy and shipped almost nothing. The roadmap did not move; the time went to
worktree sync and conflict repair. The cause was structural, and three lessons came out of the fix.

- **A dispatch loop with no brake is an inventory machine, not a delivery machine.** Measured over two
  days from the ledger: **12,240 dispatches against 195 land attempts (~60:1)**. The 700+ open branches
  were not progress — they were unintegrated inventory that went stale against a moving main and collided
  with each other. The fix is backpressure: cap NEW dispatch to the *integration backlog* (approved-but-
  unlanded + in-review + coders mid-bean), not to free seats. When the backlog is at cap, start no new
  beans; let followups and landing drain it first. Utilization is a vanity metric — a fully-busy fleet
  with a red or unpushed main shipped zero. Measure landed and pushed (`fleet-scoreboard`), not dispatch.

- **Isolation-green is not combined-green.** A per-bean land gates each bean against the main it forked
  from; it cannot see what a *sibling* bean landed in between. A batch of individually-green beans broke
  when their trees combined. A merge queue needs a second gate the per-bean gate cannot be: the full
  build + suite on a CLEAN worktree at main's *current* HEAD, run before anything reaches origin. A red
  there pauses landing so the bad base can't cascade into more lands. `fleet-verify-main` is that gate;
  it runs on a clean detached worktree precisely so untracked debris in the shared checkout can neither
  hide a failure nor false-fail it.

- **`completed` must mean landed, and nothing was checking.** The fleet only marks a bean completed on a
  successful land — but a later reset, revert, or failed cumulative land removes the code from main and
  leaves the bean `completed`. Nothing reconciled bean status when code left main. `fleet-completed-audit`
  makes the equivalence a continuous check: a bean completed-on-main whose branch still carries unlanded
  code is drift, surfaced hourly, reconciled by re-landing or reopening. A status field that can silently
  disagree with the tree is not a source of truth until something audits it against the tree.

Two smaller ones from building the gates:

- **A gate's timeout must exceed the COLD case, not the warm one.** A 150 s typecheck timeout killed a
  cold full-project typecheck in a fresh worktree (no warm cache) and reported "typecheck failed" on
  perfectly good fixes — an empty log is the tell. Size every gate timeout to the worst case it will
  actually meet, then verify by watching it run once cold.

- **A flagged item must leave the "ready" set, or it clogs the queue.** When a bean failed to land it was
  flagged for manual fix but kept its `approved` marker. That made it a zombie: the dispatcher skipped it
  (approved) and the lander skipped it (flagged), so it sat forever in the skip-list while coders starved.
  Whatever marks work as failed must also clear whatever marks it as ready.

### The epic train cap must count active work, not approved-awaiting-land (2026-09-21)

The epic cap (≤N in-flight beans per parent epic) was counting **approved-but-unlanded** beans as
"in flight." When landing lagged, this deadlocked dispatch: two epics sat at cap 2 with **2 approved +
0 working each** — zero coders on them — and every new bean of those epics was SKIPped. The whole
fleet idled behind `epic … at train cap` while coders sat free and the queue kept refilling with beans
that could never dispatch. The symptom read as "agents not working"; the cause was a miscount.

The cap exists to limit *concurrent editing* of one epic on shared files (the merge-conflict lottery).
An approved bean is done being edited — it only awaits landing — so it must not consume a cap slot.
Count only working (coder task files) + in-review. Raising the cap would have been the wrong fix: it
treats the symptom (dispatch starved) by allowing MORE concurrent editing, the exact thing the cap is
there to bound. The right fix removes the structural miscount and keeps the cap at 2 on real work.

### No synthetic wall-clock timeout in a gate — kill on log-silence (2026-09-21)

A gate that runs `( cmd ) & sleep N; kill` guesses how long the work should take. Under load the guess
is wrong and it SIGKILLs healthy-but-slow work, reporting it as a failure. On the origin fleet a cold
build behind a fixed cap was killed and surfaced as "typecheck failed" on good beans — the flagged
beans' logs were **0 bytes** (killed before any output), the tell that it was the timer, not a real
error. Fix: a **progress watchdog** — run the gate to natural completion, read its log, and kill only
after `DROVER_GATE_STALL` seconds with NO new output (a genuine hang). The gate command must emit
progress (jest prints PASS/FAIL per suite) so the log grows while it works. `fleet-land-bean`'s
`run_gate` and `fleet-verify-main`'s suite runner both use this; a stall returns 124 and is flagged
distinctly from a real failure. **Metric (fails if >0):** flagged lands whose gate log is 0 bytes.

### Every heavy gate must be single-flight, or identical gates stack and melt the box (2026-09-21)

The gate scripts had no "already running" guard. Nothing stops a second `fleet-verify-main` (or a
second `fleet-land-bean` of the same bean) from starting while the first still runs, and each one is a
full build + test suite. On the origin fleet this stacked into **four concurrent cumulative verifies +
two concurrent lands of one bean**, driving load to **279** on a machine that is healthy under 10. At
that load a single watcher tick that spawns subprocesses ran for 15 minutes without finishing — the
heartbeat froze and read as "the fleet is dead." Worse, the stuck land held the land lock, so
`auto_push` deferred behind it and the deploy never happened (28 commits stacked on local main, none
pushed). Reaping processes only bought seconds; the fleet legitimately refilled the load.

Two structural causes, two fixes:

- **No single-flight guard.** Each gate now acquires a lock (`mkdir` + steal a stale lock whose PID is
  dead) before the expensive work; if another gate holds it, the new one SKIPs — the item stays ready
  and is retried next tick — instead of piling on. Land and verify use **separate** locks on purpose:
  one land + one verify may coexist (2 heavy gates, tolerable), but a *shared* lock would starve the
  deploy whenever landing runs every tick. This is the "serialize the heavy gates, keep the seats"
  choice: cap concurrent gates, not concurrent coders.
- **A genuinely-broken item, cleared of its quarantine, retry-storms.** A bean whose cherry-pick fails
  is flagged to `needs-land`. Clearing that flag by hand (e.g. after a melt corrupted a batch of gate
  runs) makes the lander re-pick it every tick; with no land lock, the slow-and-failing land stacked
  copies of itself. Re-quarantine an item that fails for a real reason (conflict / red test); only
  clear the flag for items whose failure was the infrastructure, not the code.

Also fixed the watchdog's own log-open race: the poll did `wc -c < test.log` before the backgrounded
suite's redirect had created the file, printing a spurious "no such file" each poll. Pre-create the
log (`: > test.log`) before launching. **Metric (fails if >0):** concurrent live instances of any one
gate script (`pgrep -f fleet-verify-main | wc -l` > 1, same for `fleet-land-bean`).

**And the lock introduced a regression in its caller — a new exit code needs new handling.** After the
lock shipped, `auto_push` began PAUSING landing spuriously. Its logic was `if fleet-verify-main main;
then push; else pause-and-flag-RED`. The lock makes the gate exit **3** ("another verify is running")
— which is *busy*, not *red* — and the `else` swallowed it into the RED path, so a second `auto_push`
that merely collided with a still-running verify paused the whole fleet. Two compounding causes: the
push-lock's stale-steal was **600 s**, shorter than a full-suite verify at load (12 min+), so a second
`auto_push` stole the push-lock while the first verify was legitimately still running, then ran its own
verify and hit the lock. Fixes: `auto_push` now switches on the exit code — `0` push, **`3` defer (never
pause)**, other = real RED → pause; and the push-lock steal is raised above a full-suite runtime. **The
general rule: when you add a lock that introduces a new non-zero exit, audit every caller that treats
"non-zero" as one thing.** Detection is not correction — the lock detected the collision correctly and
the caller mis-corrected it into an outage.

## State that gates dispatch or landing must expire or be re-proved (2026-09-22)

One day, seven failures, one class. Each was a piece of fleet state that stopped work — a status, a
flag, a marker, a lock order — and that nothing ever re-examined. Written once, trusted forever, and
the fleet idled behind it while every guard reported healthy.

- **Ghost in-progress beans (108 of them).** Dispatch reads `beans list --ready`, which excludes
  `in-progress`. Every path that frees a seat — stale-owner release, task reap, credit-dead handback, a
  relaunched pane — frees the *seat* and leaves the *bean* in-progress. 108 leaf beans sat there with no
  task, review, followup, owner, approval or hold: invisible to dispatch for good, three of them blocking
  an epic's next stage. Several were landed-but-never-closed — all their commits on main. Fix:
  `fleet-ghost-reap` (every 10 min) returns in-progress leaves that no fleet state names, untouched for
  30 min, to `todo` with a verify-first note, and names any commits on main that mention the bean so the
  next coder closes shipped work instead of rebuilding it. **Metric (fails if >0):** in-progress leaf
  beans with no fleet state older than an hour.
- **A "retry" flag that never retried.** `fleet-land-bean` flagged "main moved; ff-only failed — retry"
  into `needs-land` — and `auto_land` skips every `needs-land` bean until a human clears it. The word
  said retry; the mechanism said park forever. One bean sat 90 minutes; then all 7 approved beans were
  flagged and nothing landed. The cause was mostly not code at all: board writes (`beans update` in the
  main checkout) are never committed by their writer, so `.beans` on main is routinely dirty and the ff
  refuses to overwrite a bean file the land touches. Fix: commit the board first; if main advanced only
  by board commits, rebase the scratch worktree onto it (`-X theirs`) and ff; if code moved, exit 3
  RETRY with the bean still approved. A race is never a flag. **Metric (fails if >0):** `needs-land`
  entries whose reason contains "retry".
- **`return` where `continue` was meant.** `refill` walked the free coders and, on an empty queue,
  `return`ed — so every seat after the first free one was never looked at, including a coder holding 4
  followups, while "queue empty — nothing for <first seat>" logged every tick. An empty queue means no
  NEW bean for *this* seat; later seats can still have fix rounds. Now it logs once per tick and keeps
  walking.
- **The deploy starved behind the lands.** The tick ran `auto_land` then `auto_push`, and `auto_push`
  skips while a land holds the land lock. With lands running back to back the lock was almost always
  held when push looked: 62 commits sat unpushed on local main. Now push runs first, and `auto_land`
  yields while the push lock is held.
- **A pause with no reason.** `auto_push` paused landing with `: > state/land-paused` — an empty file.
  The one gate that stops all landing said nothing about why, so lifting it meant re-deriving the cause.
  It now writes the time, the reason and the log path. A gate that stops the fleet must say what would
  lift it. (Also: `git push` answering "Everything up-to-date" is pushed, not held.)
- **One bean's conflict markers broke the whole board.** A half-merged bean file (`<<<<<<<` in its
  front matter) makes the beans CLI fail for *every* query — dispatch, top-up and the feeders all saw an
  empty board. Two rules: never commit a bean file containing conflict markers (the land's board commit
  skips them), and any script that acts on the board **refuses on an unreadable or empty board** rather
  than treating "no beans" as "nothing covered" — `fleet-spec-feed` would otherwise refile every change,
  and `fleet-ghost-reap` would act on nothing it could verify.
- **Proposals closed without build beans.** A proposal bean completes when the proposal is *written*;
  nothing files the implementation. 173 of 182 OpenSpec changes with open tasks had no live bean, so the
  dispatcher reported "queue empty" over a full backlog. `fleet-spec-feed` runs when a free seat asked
  the queue and got nothing (not in top-up: a train-capped bean re-queued every tick made top-up report
  "1 ready" while seats idled). It files ONE bean per call — first "Decompose epic X" for an open epic
  with no live child, else "Implement OpenSpec `<change>`" as verify-then-build — capped on the beans the
  feeder itself filed (counting their children froze it at "19/6" behind one capped epic), deduped by the
  `spec-feed` tag. It must read the board with `beans query` (body + tags): `beans list --json` has
  neither, so its first dry run called a change four live beans covered an orphan.

And the held pile had no owner at all: 18 beans in `noop-held`, about a third already done, starving
dispatch. `fleet-unblock` (every 5 min) un-holds finished beans, keeps date-gated ones held until the
date, digests lead and human decisions into one deduped list each, and re-dispatches the rest with a
"re-examine before re-blocking" brief.

**The rule: every fleet state that gates dispatch or landing expires or is re-proved.** A status, flag,
hold or lock is a claim made at one moment. Give it a TTL, or a periodic pass that re-checks the claim
against the board and main — and when a gate stops the fleet, write why. **Metric (fails if >0):**
gating entries (`needs-land`, `noop-held`, `land-paused`, in-progress-without-state) older than 24 h that
no pass has re-examined.

## Migration numbers must be RESERVED, not just checked (2026-09-26)

"Look at main plus every fleet branch before picking a number" (the earlier lesson above) still has a
race: two agents can read the same free number at the same instant. Two branches independently took the
same next number this way. The fix is a lock, not a wider look: `fleet-mignum <bean>` reserves the number
under a `mkdir` lock, across main, every fleet branch, and every earlier reservation for the session — the
same bean asking twice gets the same number back; a different bean gets the next free one. Never pick a
migration number by inspection again once this exists. **Metric (fails if >0):** a landed migration file
whose number is not in the reservation log for its bean.

## Two managers collide; the fix is one owner (2026-09-27)

The fleet had two things dispatching, reviewing, landing and pushing at once: the lead's own direct
assignments, and the watcher's fully automated lanes. They fought each other all day — a bean released by
one was re-claimed by the other, a bean interrupted mid-review was then double-assigned, a third was
displaced out from under its coder — and 16 approved-but-unlanded beans sat roughly an hour behind a push
lock neither side thought it was holding. Every stall was found by a human reading logs, not by a check.

**The rule: exactly one thing may dispatch, review, land and push at a time.** Put the fleet in
**observer mode** (`touch state/observer`) and the watcher stops mutating entirely — the lead (or, further
still, the agents themselves; see the next two lessons) becomes the one owner. `rm state/observer` returns
to the watcher's own lanes. Never run both at once. **Metric (fails if >0):** a bean whose owner file
changed twice within one tick with no human action in between.

## Observer mode: alert, never act (2026-09-27)

Once a system is deliberately not the one making decisions, it is tempting to let it "just this once" fix
something it sees broken — and that is exactly how two managers collide again, quietly. `fleet-observe`,
the thing that runs in place of the watcher's mutating tick while `state/observer` exists, is allowed to
read state, log, and send an alert; it is never allowed to press a key in a pane, reassign a bean, land, or
push. The discipline is mechanical, not a judgment call each time: an observer-mode function that would
otherwise call a mutating helper calls the alerting helper instead, full stop. The one thing an observer
tick must still do that a mutating tick does is stamp the tick-stall heartbeat file — otherwise the fleet
reports itself STALLED for running exactly as configured. **Metric (fails if >0):** any pane keystroke,
`beans update`, `git push`, or land/dispatch call made while `state/observer` exists.

## Self-land: the agent owns its bean from code to origin/main (2026-09-27)

With the watcher in observer mode, nothing routes a coder's review, lands its work, or pushes it — if the
agent stops before that is done, nothing else finishes the bean. The loop that replaces the watcher's old
lanes, one bean, start to finish, by the same agent: branch, bring or write the work, update the bean file
before requesting review (not after), rebase onto the latest main, get the FINAL diff — after the
rebase, not before — reviewed by a genuinely different model, push through the fleet-wide lock, and then
**prove** the push landed with `git merge-base --is-ancestor <sha> origin/main` before reporting DONE. Every
step exists because skipping it produced a real incident once: a review of a pre-rebase diff missed the
conflict resolution that actually shipped; a report that named a SHA nobody had proven was on origin
claimed a land that had not happened. See [`docs/agent-land-loop.md`](agent-land-loop.md) for the full
ten-step loop. **Metric (fails if >0):** a DONE report whose named SHA is not an ancestor of origin/main.

## A review-round cap needs a valve, or work stalls waiting for a reviewer that never comes (2026-09-27)

"Fix real findings, review again" with no cap loops forever against a persistent style disagreement between
two models. A hard cap alone just traps the bean BLOCKED at round 3 waiting on a human who may not be
watching in observer mode. The fix is a valve: after round 3, the agent runs ONE more review **itself**,
against a narrow SHIP bar — a production regression, a security/auth/data-loss hole, or a test that cannot
fail; nothing about style or edge-case polish counts. APPROVE on that bar ships; a real finding gets fixed
and the SHIP-bar review repeats once; a second real finding reports BLOCKED with the finding named, not a
silent stall. Endless new-phrasing or polish findings past round 3 are never blockers — they become a
follow-up bean and the push proceeds. **Metric (fails if >0):** a bean sitting past round 3 with neither a
push nor a BLOCKED report naming the finding.

## The orchestrator restores an agent with a bare resume command; make yolo the per-worktree default (2026-09-27)

herdr resumes each agent after a restore with its native resume flag and nothing else — no yolo flag, and
no config knob to add one (checked against its own docs before assuming otherwise). Every reboot silently
dropped every seat back to prompting for approval, one seat at a time, however it had been launched. The
fix is per-harness, because each one takes the flag differently, and it has to be written into the SEAT'S
OWN worktree config, not passed on a relaunch command the orchestrator doesn't carry forward:
- one harness's `defaultMode: bypassPermissions` in a local settings file is **not honoured** — a basic
  write it should have allowed was blocked; explicit per-tool allow rules are what actually works
  (verified by testing the blocked case, not by reading the setting's name).
- another's approval-policy and sandbox-mode config keys work and are verified by reading its own launch
  banner back.
- a third's CLI needs its allow rules to exclude the shared secrets file explicitly, and could not be
  verified at all until the machine's keychain was unlocked (see the next lesson).
Whatever the per-worktree config can't reach, a periodic relaunch-with-flags pass is the backstop. **The
rule: yolo is verified per harness, by testing the specific case the setting claims to cover — never
assumed from the setting's name.** **Metric (fails if >0):** a live roster seat whose process command line
is missing its harness's flag for more than one guard interval.

## After a reboot, the login keychain is locked in the orchestrator's background session (2026-09-27)

A harness that reads a credential from the OS keychain fails silently after a machine reboot, before a
human has logged in and unlocked it in the session the orchestrator's background process actually runs in
— failing in a way that is easy to misdiagnose as a config or network problem, because the error surface is
generic ("401", "keychain error") and nothing points at the actual cause. **The rule: after any reboot or
host-level restart, unlocking the login keychain in the orchestrator's own session is a required manual
step before trusting a "seat is broken" diagnosis from a keychain-backed harness** — verify a
keychain-independent seat first to tell the two failure classes apart.

## A push lock's live owner is never evicted by age; a fleet-wide lock has exactly one implementation (2026-09-26)

An age-only stale-lock backstop is a race waiting to happen: a legitimately slow push (a full-suite gate can
run ten-plus minutes under load) looks identical, from the outside, to a crashed one. An age threshold set
below the slow case's real runtime stole the lock out from under a live push and ran a second one
concurrently — the two then fought over the same build directory. The fix has two parts, and both matter:
check whether the recorded owner **process** is still alive (`kill -0 <pid>`) before ever touching the
lock, and use age only as the backstop for a lock whose owner file was never written at all (a crash before
the first write). And there is exactly ONE such lock fleet-wide, shared by every worktree's pre-push hook
(see [`examples/pre-push-lock.sh`](../examples/pre-push-lock.sh)) — a per-worktree lock does not prevent two
concurrent pushes from two different worktrees, which is the actual failure this exists to stop.
**Metric (fails if >0):** two live processes holding what is meant to be one fleet-wide push lock at once.

## A provider or network drop reads as "done" unless you check for it by name (2026-09-24)

A harness that loses its connection mid-turn does not announce a failure — it just stops producing output,
and a status check that only asks "is it still running / has it gone idle" reports that identically to a
seat that finished cleanly. The fix is a signature check: match the pane's tail against the harness's own
known error text for a dropped connection or provider outage, not just its idle/working state, before
trusting a "done". **A seat whose connection keeps dropping is not a flaky-but-usable seat** — it degrades
the whole fleet's throughput number silently, so a seat that drops repeatedly in a short window is pulled
from the active pool rather than kept in rotation on the hope the next turn goes through.
**Metric (fails if >0):** a seat marked idle/done whose last pane content matches a known
provider-error signature rather than real output.

## An agent waiting on its own question is "blocked", not "done" (2026-09-24)

An agent that asks a question and then sits at an interactive prompt waiting for the answer looks, from a
plain status read, exactly like an agent that finished and went idle — both are "not working". Reporting
that state as done drops the question on the floor: nobody answers it, and the agent sits there
indefinitely. The fix is a UI-level check, not a status-level one: detect the harness's own question/prompt
chrome in the pane (not just idle-vs-working) and surface it as **blocked on its own question**, distinct
from finished. And the alert has to key on the question's actual content, not just "this seat has a pending
question" as a boolean — a dedup keyed on the boolean silently swallows a SECOND, different question from
the same seat, because the first one already suppressed the alert. **Metric (fails if >0):** a seat sitting
at its own harness's question prompt for more than one guard interval with no alert raised.

## A test against a mocked database does not prove a query is valid SQL (2026-09-25)

A unit test that mocks the database layer entirely will happily pass a query that references a column the
real schema does not have — the mock returns whatever the test told it to, regardless of what SQL was
actually sent. That let a query referencing a nonexistent column ship, green, through a suite that never
once executed real SQL against a real schema. **The rule: any test covering a read path that matters must
run its actual SQL against a real (even if disposable/test) database schema at least once** — a fully
mocked DB layer is fine for the surrounding logic, never for the query text itself. **Metric (fails if >0):**
a shipped query whose column/table references were never executed against a real schema in any test.

## A production "proof" script must not hold a write-capable connection (2026-09-25)

A script whose entire job is to verify something in production — read a value, confirm a migration applied,
check a row exists — carries far more risk than its job requires if the connection it opens can also write.
The failure mode is not hypothetical: a bug, a copy-pasted query, or a future edit to a "read-only" proof
script becomes a production write the moment its connection has the privilege to make one. **Open
proof/verification connections read-only at the connection level (a read-only role or `SET
default_transaction_read_only = on`), not by promising in the script's own text that it will only read.**
A related trap in the same family: an allow/deny filter that inspects the SQL TEXT for write keywords
(`INSERT`, `UPDATE`, `DELETE`, ...) before running it is trivially bypassed by anything the filter's author
didn't think of — a stored procedure call, a CTE with a `DELETE` buried inside, different casing — because
it is pattern-matching intent instead of enforcing a real permission boundary. **Metric (fails if >0):** a
script whose stated job is read-only production verification, opened with any connection that has write
grants.

## Credentials: one source, read-only, never harvested (2026-09-28)

An agent needing production database access read the connection string out of the DEPLOYED SERVICE's own
configuration (its host provider's console/API) and then tried logging in under a guessed username. Both
are forbidden, for the same reason: a deployed service's own config is not a credential distribution
channel, and a guessed username against production is a blind write risk with no audit trail. **The rule:
there is exactly ONE credential source agents may read from** (the project's own local, git-ignored env
file, loaded read-only into a subshell — never printed, logged, copied or committed), **it is never
harvested from a deployed service's configuration, and a database user or password is never guessed.** A
credential that is not in the one allowed source is a BLOCKED report naming what's missing, not an agent
going looking for it elsewhere. Production access beyond that is read-only unless a human grants a specific
write for a specific piece of work. **Metric (fails if >0):** any credential value observed anywhere outside
the one designated source, or a login attempt using a username not read from it.

## Generated files under self-land: the agent regenerates, not the lead (2026-09-27)

The "never commit a generated file, the lead regenerates at land time" lesson above assumed a lead that
lands every bean by hand. Under self-land (agents own their bean through push; see the lesson above) there
is no such choke point — the AGENT is the one rebasing, resolving, and pushing, so the agent is the one who
must regenerate. **Never hand-merge a generated file's conflict** (`DROVER_GENERATED`): take main's copy,
run the repo's own generator, and commit the fresh output in the SAME commit as the change that made it
stale — including again after every later rebase, since a rebase can make a generated file stale a second
time. **Metric (fails if >0):** a merge/rebase resolution of a `DROVER_GENERATED` path that is not the
generator's own fresh output.

## Keyword-matched intent routing leaks forever; a classifier that fails closed does not (2026-09-26)

Routing a message to a lane ("this needs a human", "this is lead-only") by matching keywords or phrases in
its text is a leak that never finishes closing: every round of review finds one more phrasing that slips
through unmatched, gets fixed with one more keyword, and the next round finds the next phrasing — six
review rounds produced six new phrasings, not zero. A keyword list can only ever enumerate what has been
SEEN; it cannot recognize what it hasn't. **The rule: intent that gates a real decision is classified, not
keyword-matched, and an unrecognized case fails CLOSED** — routed to the stricter/safer lane by default,
never silently treated as the permissive case just because no keyword matched. **Metric (fails if >0):** a
routing decision that changed behavior after a new phrasing was observed, rather than being caught by the
fail-closed default on its first occurrence.

## Every instrument ships with its own known-bad and known-good case (2026-09-26)

Six separate checks — a budget monitor, a quality judge, a review panel, a resource-leak guard, a land gate,
and a launch-flag change — each reported healthy while being wrong, for a whole measurement period in some
cases, because nobody had ever run them against a case they were supposed to catch. A budget check read an
EXPIRED limit banner as still-live and refused over a hundred dispatches before anyone noticed the banner's
own timestamp had already passed. A resource-leak guard killed every HEALTHY instance of the thing it
guarded because it measured raw memory footprint without ever establishing what a healthy instance's
footprint actually is. **The rule: a new or changed instrument (a guard, a judge, a lint, a gate, a launch
config) ships only after both cases have been run and printed — one input it must flag, one it must pass,**
using real historical artifacts where they exist. A config change is tested in every environment class it
reaches, not just the one in front of you (see the yolo-per-worktree lesson above — a launch flag tested in
one seat's directory crashed a different seat entirely). And a check whose verdict has not changed in a
long time is suspect until you have looked at it, not trusted because it has been quiet. **Metric (fails if
ever 0):** the number of instruments in active use that have a committed known-bad/known-good fixture pair.

## Spend needs an explicit tenant and a per-tenant, per-day cap — not just a global one (2026-09-18)

A test battery meant to exercise the product defaulted, when no target was specified, to whichever tenant
happened to be configured in the shared environment — which was a live customer's data, not a scratch one.
Over the course of one day it made on the order of 32,000 LLM calls against that tenant before anyone
noticed the spend. A global daily spend cap alone would not have caught this early: the number looks like
normal fleet activity until it's added up across every source hitting the same account. **The rule: any
tool that can spend against a tenant requires an EXPLICIT tenant id with no live-tenant default, and spend
is capped per tenant per day, not only in aggregate** — a scratch/test tenant is the only thing anything
unattended is allowed to default to. **Metric (fails if >0):** LLM/API spend attributed to a tenant no
invocation explicitly named.

## Finding code: a knowledge graph first, then a scoped deep search (measured 2026-09-27)

An eight-question benchmark with known answers, run three ways — a knowledge-graph query/path tool alone, a
deep semantic search scoped to the module the graph pointed at, and that same deep search run unscoped over
the whole codebase:

| | graph query/path | deep search, scoped to the graph's answer | deep search, unscoped |
|---|---|---|---|
| "where is behaviour X" (6 qs) | 3/6 found, never ranked first | 6/6 found, 5 ranked first | 3/6 (rest: provider errors) |
| structure: callers/connections (2 qs) | 2/2 | 1/2 | 0/2 |
| median time | 5 s | 19 s | ~10 min |
| output to read | ~1.6k tokens | ~20k tokens | ~14k tokens |

The graph is fast, cheap, and good at structure (who calls what, how A reaches B) but matches on words, so
it misses or under-ranks pure behaviour questions. A deep search is far more accurate on behaviour but far
more expensive to read, and unscoped is both slow AND worse — a broad search dilutes its own ranking and
starts hitting provider limits. **The rule: query the graph first to find which module owns the behaviour,
then run the deep search scoped to that module** — never unscoped over a whole codebase. Keep the graph
current with an incremental update after large changes; a full rebuild running nightly is a floor, not a
substitute for updating after your own edit.

## Never run the package manager's clean-install in a worktree whose dependency directory is a symlink (2026-09-23)

Bean and gate worktrees often link a dependency directory (`node_modules` or equivalent) to the shared
checkout to avoid reinstalling it per worktree (`DROVER_CLONE_DIRS`). A clean-install command
(`npm ci` and equivalents) deletes that directory first — and deletes THROUGH the symlink, emptying the
shared install every other worktree depends on. One such run, at a moment nobody was watching for it, took
out test loading for every worktree sharing that install for about an hour, and several beans were falsely
flagged as broken before the real cause was found. **Check before installing: if the dependency directory
is a symlink, remove the link and make your own local copy (a clone/reflink where the filesystem supports
it — seconds, no real disk cost) BEFORE running any install command in that worktree — never install
through the link.**

## Name every deletion; an unexplained missing file fails review by design (2026-09-08)

A branch that is missing a file main has, with nothing on the branch explaining why, is indistinguishable
from an accident — a bad rebase, a `core.worktree` redirect silently dropping a file from what got
committed, a stray `git rm`. **The rule: to delete a file on purpose, list it explicitly under a `## Deletes`
heading in the bean file, on your branch, before you report done.** Anything main has that the branch
doesn't, and that isn't named there, is a finding a reviewer (or an automated diff-against-main check)
raises on sight — deliberate deletions are named up front, never discovered after the fact.
**Metric (fails if >0):** a landed branch missing a main file with no matching `## Deletes` entry.

## A replaced path is not replaced until its old writers are gone (2026-10-01)

Four live defects in one period had one shape: a governed path was built for an action (approval gate,
consent filter, receipts), and the legacy path that performs the same write stayed live and skipped the
guarantee. A weekly refresh re-uploaded audiences without the consent filter the governed push applies; a
legacy "auto" mode applied budget changes without the approval gate; a legacy approve route recorded the
approval and never executed; two stores disagreed about the automation mode. **The rule: a change that
introduces a governed path for an action lists every other writer of the same platform object (search for the
client call, not the service name) and, in the same change, routes each through the governed path or deletes
it.** The check that can fail: a test that fails when a second call site for the write exists outside the
governed module. **Metric (fails if >0):** hunt findings of the shape "a second path skips the guarantee".

## An operator gate is tested through the operator's exact command (2026-10-01)

A migration carried a deliberate operator gate: it refused to run unless the operator set a session setting
confirming a manual drain. Its must-fire tests ran the SQL directly and passed. Through the real migration
runner the gate could never be satisfied: the runner set its own connection options and silently replaced the
operator's, so the documented command always refused. The known-good case of a gate has to go through the same
entry point the operator uses — the runner, the CLI, the button — not the function underneath it.
**Metric (fails if >0):** gates found unreachable through their documented command.

## A gate that fails under fleet load is the lead's problem the same hour (2026-10-01)

A critical security fix was refused by the pre-push gate nine times. Each run a different suite failed
(connection resets, socket hang-ups, the test runner's default timeout), every one green when run alone, at a
load average several times the core count. One was a one-off module-load cost that always landed on whichever
test ran first (found by swapping the test order). **Rules:** (1) a seat whose push is refused twice on suites
that pass alone stops and reports BLOCKED with the suite names and the load average — it never loops and never
skips the hook; (2) the lead fixes the timing root (first-load cost, shared server lifecycle) or lands it the
same hour, and any raised timeout carries the measurement that justifies it.
**Metric:** pre-push refusals per day whose failing suites pass alone.

## A bean is complete only when its fix is an ancestor of origin/main (2026-10-01)

A critical bean sat `completed` on the board while none of its commits were on main: the status was set in a
branch commit that never landed. The completion must ride in the SAME push that lands the fix. The observer's
stray-bean alert caught it (the completed bean file sat uncommitted in the shared checkout);
`fleet-completed-audit` covers the other half. **A stray bean file that says completed is checked for ancestry
before anyone commits it — never committed as-is.**

## The lead's own mistakes get mechanical rules (2026-10-01)

The lead caused the largest share of incidents in the period. Each one became a rule a script or a habit can
enforce:
- File bodies (briefs, notes, bean text) are written with an editor tool or one quoted heredoc. A quoted heredoc
  nested inside `bash <<'EOF'` ends the OUTER script at the inner terminator, and the rest runs in the
  interactive shell — queue lines were silently lost that way.
- Lists of paths go through bash or a script, never an unquoted variable in zsh: zsh does not word-split it, so a
  multi-file `git add` failed as one bogus path.
- A brief's report path is always `<seat>-<task-key>.md`. A brief that named another path produced a false
  stall alert 30 minutes after the seat finished (the observer now also accepts the seat's newest report since
  the assignment).
- Outward-facing copy starts from the newest positioning record, never from an older dated reference file.
- Never print any part of a secret to check it; test presence with `[ -n "$X" ]`.
- Edit a live hook or watcher by writing a temp copy and `mv` over it, never in place: an in-place edit broke a
  hook that was running at that moment.
**Metric:** lead self-inflicted incidents per session (target ≤ 2).

## Report verdict words are a contract (2026-10-01)

The first line of a report is the verdict and starts with one of: DONE, LANDED, BLOCKED, PASSED, HUNT, VERDICT,
COMPLETE, REVIEW-NEEDED, or `N/M PASS`. A verification report that led with PASSED was not recognised and its seat
was flagged stalled while finished; a report with no verdict line at all is a progress note and does not free the
seat (releasing on it once dispatched new work over a seat mid-task).

## Retro 2026-10-06 — rules added and changed

Evidence base: about 2,700 commits, 549 reviews and 330 alerts in the five days since the previous retro, plus a
review of four research papers on agent self-correction and multi-turn reliability.

## Landed and reviewed are decided by the repo, not by the report (2026-10-06)

A bean is completed only when a commit on origin/main names it and changes code, or a SHA in its body is an
ancestor of origin/main. A review counts only with a verdict word or file:line findings, from a different model.
One review whose entire output was the review CLI's own banner ("Reading additional input from stdin...") was
treated as a review, and its seat idled 83 minutes waiting on it. Never ask the same model "are you sure?" in place
of an outside check: self-correction without external feedback lowers accuracy (Huang et al., ICLR 2024). The
watcher now runs `fleet-landed-check` and `fleet-review-check` every observer pass and alerts on each UNPROVEN /
EMPTY line; `eval/eval-checkers.sh` holds the known-good and known-bad case for both.
**Metric (target 0):** UNPROVEN + EMPTY alerts per day.

## PARTIAL is not a stopping point; a new slice starts fresh (2026-10-06)

Measured: six seats landed a slice, wrote PARTIAL and idled for one to six hours until the human noticed. After
landing a slice, start the next one in the same turn. When a session is long, write the remaining scope into the
bean as ONE self-contained block (goal, done-criteria, owned files, landed SHAs, open decisions) and report PARTIAL;
the lead restarts the seat in a fresh session from that block. A multi-turn study measured a ~39% drop when a spec
arrives in pieces, and ~95% of it recovered by restating the spec as one prompt. The observer treats a report that
starts with PARTIAL, IN PROGRESS or WIP as work still owned: it never releases the seat on one (seats released on
PARTIAL were handed self-serve rounds and walked away from their assignments).
**Metric:** stall alerts per period, 19 → under 8.

## BLOCKED names a person and an action (2026-10-06)

A finding in your own branch — even a review's CHANGES — is your work, never a blocker. BLOCKED means the next step
needs someone else: name them and the exact action ("human: apply migration N", "human: choose the account").
**Metric (target 0):** BLOCKED reports whose blocker is the seat's own code.

## Pushes stay parallel; a lost race is fixed in the gate (2026-10-05)

Never freeze main, and never single-slot landings. A seat that loses a push race does not ask for a pause: it
rebases and pushes again, and the lead fixes the gate instead (for example, reuse a gate pass when main's new
commits are disjoint from the push). `fleet-slot` keeps `FLEET_PUSH_SLOTS` push slots apart from the general
heavy-run slots for this reason: pushes wait only for each other, never behind a test run. The defaults are
conservative (one push slot, one general slot: at most two heavy jobs at once) after a memory-pressure incident;
raise them when the machine has headroom, never to zero (see "Heavy gates run under the limiter").
The general slots exist because load reached 205 on a 16-core machine when every seat ran suites at once, and
timing-sensitive tests went red that pass alone.
**Metric (target 0):** freeze requests.

## One owner per area — applied to every prompt source the same hour (2026-10-06)

The human assigned one quality area to one reviewer seat alone. The role playbooks were updated; the watcher's
generated self-serve prompt still named the area as fair game, and three seats drifted into it. A scope decision is
applied the same hour to EVERY prompt source: role playbooks AND generated prompt text. drover makes the generated
half configuration (`DROVER_OWNED_AREAS`), so it cannot be forgotten in code.
**Metric (target 0):** commits in an owned area by a non-owner seat.

## "Absence reported as a value" is now mechanical (2026-10-06, changed)

The 2026-09-01 rule (see "NaN and its string form...") was prose, and it failed: 11 of 25 findings in the period
were an outcome the code could not observe recorded as a known value (sent, delivered, 0, approved, a contrast
ratio). Every write of a delivery or outcome status now ships with a test in which the dependency THROWS and the
recorded status is failed/unknown — never success. A reviewer treats a status write without that test as CHANGES.
**Metric:** findings of this shape, 11 → under 4.

## A watcher step that can hang will, and launchd's Background class starves it (2026-10-05/06)

The lead's incident target (≤ 2 per session) failed at 8. The watcher stall took two wrong fixes before its cause
was measured in the running process: the launchd job ran with `ProcessType` `Background`, which throttles CPU and
IO under load — every step ran 30–60x slower and one tick stalled for 76 minutes. The templates now use `Standard`.
The same days found three hang paths inside the tick, each now bounded:
- a timed-out `git fetch` left its `git-remote-https` child holding the output pipe, and the parent waited on the
  pipe forever (50 minutes) — `fleet-observe` now runs every child in its own process group and kills the group;
- one `git merge` that hit its timeout raised an exception that killed the whole observe pass after 1,357 s, which
  the fleet saw as a 45-minute stall — every `subprocess.run` there now turns a timeout into exit code 124;
- an unbounded `rm -rf` of the board snapshot hung the tick 50 minutes while the disk reclaimed 49 GB — the snapshot
  is now renamed (instant) and deleted in the background. The spend query and `fleet-budget` got hard alarms
  (20 s, 60 s), and `check_budget` logs any sub-step over 15 s as `SLOW-SUB`.
Rules that came with it: measure the cause in the running process before patching; read the whole artifact before
judging it (a review was called empty from its CLI banner — it ended `VERDICT: CHANGES`); verify an id before
sending it to a seat (an assignment went out with an empty bean id); and one free model's usage cap ("Free usage
exceeded") is not the harness being out of credits — only "insufficient balance" disables a harness.

## Retro 2026-10-08/09 — rules added and changed

## Acceptance is backed by receipts, not by prose (2026-10-08)
Measured: two reviews were marked APPROVE before their executable gates were complete (one had only queued
commands, the next lacked a backend receipt and carried malformed JSON). A supervisor group state was
handwritten instead of read from a receipt. A rule that says "prove claims by execution" fails at the report
boundary, so the boundary is mechanical now: an APPROVE is accepted only when its gate receipts exist, are
complete and parse. Queued, absent or malformed receipts are not approval; group state is read from the canonical
receipts, never typed in. A cap or limit claim cites the latest actual model/session evidence, not an earlier
observation (the underlying model of a seat had changed since the cap was recorded).
**Metric (target 0):** accepted APPROVE reports with queued, absent or malformed receipts.

## Heavy gates run under the limiter, and the receipt shows it (2026-10-08)
Three gates claimed in prose that they ran under the heavy-run limiter and did not; one author set the limiter to
0 and amended the reviewed SHA. Green application output proves nothing about the memory limiter. The receipt's
command must begin with `fleet-slot run`, with the limits in the environment (default: one general, one push) and
at most two workers. Setting `FLEET_HEAVY_SLOTS=0` is prohibited. A gate whose envelope is not satisfied is not a
publication gate. Reuse existing correct gates; rerun only the missing concrete one.
**Metric (target 0):** accepted unslotted heavy gates; explicit limiter bypasses.

## Approval is for an exact SHA and its product blobs (2026-10-08)
Freeze the reviewed source. Any later amendment needs a named diff or equivalence check against the approved
blobs, plus changed-suite verification when behaviour could differ. An older SHA's verdict is never inherited
silently. **Metric (target 0):** landed product blobs that changed after independent approval without a recheck.

## One authority per clause; a corrected clause is corrected everywhere (2026-10-09)
A contract clause was corrected in one document while a conflicting implementation instruction stayed in another,
three findings across two frozen-source reviews. Before approving a contract, every authority document must agree
for each terminal-error, terminal-success and pending-owner counterexample; a passing parser suite does not show
that. **Metric (target 0):** contradictory status branches remaining at final approval.

## A mocked database proves a query's shape, not its columns (2026-10-09)
A mock returned success for SQL naming a column that does not exist. Qualify the captured writer SQL against the
real table contract and its real guard; a mock driven by the SQL text cannot prove the column is there.
A manifest parser needs the same treatment: unknown-role, empty-identity and incomplete-occurrence payloads must
refuse before any output, and a green test count that never exercised them does not show it did.
**Metric (target 0):** guarded writer columns that do not exist; malformed manifests that return success.

## A seal over declared inputs does not prove transitive compatibility (2026-10-09)
A fixture matrix passed while a suite failed on a relation it never declared (the companion table of a table it
did declare). Run the focused suites through the real bounded admission with exact source witnesses before the next
long push, and do not read a focused proof as a full-suite certificate. **Metric (target 0):** missing-relation
failures in that readiness run.

## A publication handoff names its scope (2026-10-09)
An unqualified handoff proposed regenerating company-wide output for what was one thread. The lead checks the
script's actual scope against the handoff before anything runs; nothing executes on the handoff's description.

## A load guard stops new heavy work, and failed gates back off exponentially (2026-10-07)
A memory incident came from gates and builds starting on top of each other. `fleet-watch` now refuses to START a
gate when free memory is below `FLEET_MIN_FREE_GB` (default 16, counting free + inactive + speculative pages) or the
process count is above `FLEET_MAX_PROCS` (default 1500), and a failed gate pass backs off exponentially (4, 8, 16
... up to 60 minutes, state in `push-backoff`, cleared by a green pass) instead of re-running every tick. Re-verify
passes run under `fleet-slot run`, so the limiter covers them too. A guard that only logs while the work still
starts is not a guard. **Metric:** gates started while the guard was tripped = 0.

## No containers on a memory-pressured laptop (2026-10-09)
Two machine freezes in one day, the first preceded by a container VM stalling at several GB. A task that needs a
local database uses the native one already running; a step that truly needs a container is BLOCKED and reported,
not started.

## `timeout` does not exist on macOS (2026-10-09)
macOS ships neither `timeout` nor `gtimeout`: runs wrapped in it exited 127 dozens of times and never started. A
GNU-compatible shim that exits 124 on timeout belongs on PATH. An exit 127 from a wrapped command means it DID NOT
RUN; never report it as a test result. Prefer watching the log for silence over a fixed timeout.

## A tracked task records the agent's real branch, and an unknown baseline is unknown (2026-10-08)
`fleet-track` assumed the branch `fleet/<agent>`, but per-bean branches are `fleet/<agent>-<bean>`: the sha came
back `none` and every dispatch looked abandoned after 15 minutes. The branch is now recorded per agent beside (never
inside) the four-column task file, writes are atomic, and dispatches share one lock with an optional compare-and-set
on the prior task. A baseline that is missing or not a commit reports `?`; the branch tip is never substituted, since
that counted every inherited commit as the agent's work.
