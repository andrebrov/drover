# Lessons

These are the dated, measured lessons from running a fleet of 10–25 coding agents (Claude Code, Codex,
opencode, Grok, Cursor, Antigravity) on one production codebase, August–September 2026. Each one was
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
