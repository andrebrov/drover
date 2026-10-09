# Agent land loop (observer mode): you own your bean from code to origin/main

In observer mode (`touch state/observer`; see the README's "Operating model") the watcher stops
dispatching, reviewing, landing and pushing — it only observes and alerts. The lead ASSIGNS; you own
everything after that. Nothing else routes your review, lands, or pushes for you. If you stop before
step 9, nothing else finishes your work.

Repo: `$DROVER_REPO`. You run in your own worktree (`$DROVER_WORKTREES/fleet-<you>`).

1. Branch: `git fetch -q origin && git switch -c fleet/<you>-<bean> origin/main` (or switch to it if it
   already exists). Never stage `.env*`.
2. Bring or write the work. Existing commits elsewhere: cherry-pick the NON-merge commits that belong to
   the bean; resolve conflicts keeping main's newer behaviour AND the bean's fix.
3. Dependencies missing? Symlink them in from `$DROVER_REPO` (`DROVER_CLONE_DIRS`, default
   `node_modules`) — **never** `npm install`/`npm ci` (or your package manager's equivalent) through a
   symlink; it corrupts the shared copy every other worktree depends on.
4. Test-first: a failing test for the defect, watch it fail, then fix. Verify:
   `${DROVER_BUILD:-the project build}` (exit 0)${DROVER_TEST:+ and `$DROVER_TEST`}, plus the bean's own
   touched suites.
5. Update the bean file NOW, before review: tick the checklist items you proved, add a summary of
   changes, and set it complete only if every item is done (else leave it open and write what is left).
   Commit code + the bean file together, explicit paths.
6. Rebase: `git fetch -q origin && git rebase origin/main`. Re-run the build and the RELATED tests after
   the rebase — the tests of every file that imports what you changed (e.g. `jest --findRelatedTests
   <changed files>`), the same set the pre-push hook runs. Your touched suites alone miss callers: a change
   that passed all 33 of its own suites broke a route test it never touched.
7. Review the FINAL diff (after rebase) with a DIFFERENT model than yours — pipe the diff to whichever
   other harness is free (`git diff origin/main...HEAD | <other harness> "<review prompt>"`), read-only,
   asking it to check correctness, tests that fail when the code is broken, and production safety, and
   to reply with a clear APPROVE/CHANGES verdict and file:line findings.
   - If the reviewer returns an execution error or an empty result (seen from a sandboxed seat while the
     same command worked from the lead's shell), do NOT retry in a loop and do NOT push unreviewed: write
     the diff to `<reports>/<you>-<bean>.diff`, make the first line of your report
     `REVIEW-NEEDED <local sha> <diff path>`, and `fleet-done ... BLOCKED`. The observer holds your seat
     (it is waiting, not free) and alerts the lead, who runs the review and sends you the findings.
   - Leave generated files out of the review diff (append `-- . ':(exclude)<generated path>'` for each of
     `DROVER_GENERATED` to every `git diff` above). A generated snapshot can run to millions of characters; a
     reviewer CLI refuses input over its size limit ("Input exceeds the maximum length") and the snapshot carries
     nothing to review.
   - If the preferred reviewer harness is at its usage limit, use any other available harness in a fresh
     session; the rule is a different model than yours, not a particular one.
   - Never put a list-taking flag (`--allowedTools`, `--tools`, ...) before a positional prompt: it
     swallows the prompt. Pipe the prompt and the diff on stdin instead.
   Fix real findings, commit, and review AGAIN — up to 3 rounds. Any code change after an APPROVE,
   including conflict resolution in a later rebase, needs a fresh review of the new final diff.
   After round 3 with the last fixes still unreviewed, do NOT stop and wait for the lead: run ONE final
   review yourself against the SHIP bar — "Answer ONLY: (1) any production regression vs origin/main (a
   behaviour that works today breaks), (2) any security/auth/data-loss/write hole, (3) any test that
   cannot fail. Style and edge-case polish are out of scope." APPROVE on that bar → push. CHANGES on a
   real (1)-(3) item → fix it and repeat that final review once; if it still finds one, report BLOCKED
   with the finding. Endless new-phrasing or polish findings are NOT blockers: file them as a follow-up
   bean and push anyway.
8. Push: `git push origin HEAD:main`. The pre-push hook builds, runs the related tests, and holds a
   fleet-wide push lock (it waits while another push runs — do not kill it; see
   `examples/pre-push-lock.sh`; with `fleet-slot run --push` the push gate takes one of `FLEET_PUSH_SLOTS` slots,
   default 1, in a pool separate from general runs). NEVER skip the hook, and no environment variable or alternate hooks path overrides it. Non-fast-forward →
   back to step 6. A message like "main moved to <sha> while this push waited ... (no gate was run)" costs
   seconds, not a gate: rebase onto origin/main and push again at once, as many times as it takes. Only a
   refusal AFTER the gate ran (a build or test failure) is a reason to stop. A hook failure is fixed, never
   bypassed.
9. Prove it: `git fetch -q origin && git merge-base --is-ancestor <your SHA> origin/main` must exit 0.
   The observer alerts on any report that claims landed for a SHA not on origin/main.
10. Report: write `<reports>/<you>-<bean>.md` (first line: verdict + pushed SHA), then
    `fleet-done <you> <bean> DONE "<one line>" <report>`. Blocked →
    `fleet-done ... BLOCKED "<exact unblock action>"`.

Never apply migrations (write them; numbers only from `fleet-mignum <bean>`; say "migration NNN needs
applying" in the report). Never touch the shared checkout at `$DROVER_REPO` from your worktree (`git -C`
against it is forbidden — read-only reads through your own worktree are fine).

Generated files (`DROVER_GENERATED`): never hand-edit or hand-merge one; if your change makes them
stale, regenerate with the repo's own generator and commit the result in the SAME commit; after any
rebase, regenerate again rather than carrying the old generated output forward.

## After the ancestry proof: delete your branch
Once `git merge-base --is-ancestor <sha> origin/main` passes for every commit on your `fleet/<seat>-<bean>`
branch (`git cherry origin/main origin/<branch>` prints no `+`), delete it:
`git push origin --delete fleet/<seat>-<bean>`. Landed branches piled up by the dozen otherwise. Never delete a
branch with an unlanded commit.

## Clean up everything the task created, the same turn it lands
A finished task leaves nothing behind. After the ancestry proof, in this order:
1. Extra worktrees you added for the bean (`git worktree list` — anything that is not your seat worktree):
   `git worktree remove <path>` only when `git -C <path> status --porcelain` is empty and its branch has no
   unlanded commit. Never `--force`.
2. The local branch: `git branch -d fleet/<seat>-<bean>` (`-d`, not `-D`: git refuses an unmerged branch, which
   is the check you want). Then the remote branch as above.
3. Scratch copies, logs and test databases you made for this bean. Keep reports.
4. Then `git worktree prune`.
Anything with uncommitted or unlanded work is NOT removed: list it in your report under `Left in place:` with
the reason. Report what you removed in one line (`Cleaned: 1 worktree, 2 branches, 1.2 GB scratch`).

## Start the next task in a fresh session
When a task is DONE, the lead starts your next one in a fresh session. Everything the next task needs is in its
brief, the bean, and your report, never in your old context. Write the report so a fresh session can continue
from it.

## Review routing
- **Bean-file / docs-only changes skip review.** A push whose diff touches only the bean files, specs, docs or
  `*.md` lands without a reviewer (the pre-push gate already skips them). Anything touching source, scripts,
  migrations or hooks keeps one cross-model review of the final diff.
- **Anything that changes how a page or an email LOOKS is judged on the rendered output**, by a design seat that
  compares it with the brand's references, not by a code reviewer. The code review still runs for correctness;
  acceptance of the look is the design seat's verdict on the rendered images. A code-level judge passed renders
  the human failed at a glance.
- Unchanged: one reviewer, a different harness; two CHANGES rounds -> REVIEW-NEEDED to the lead, never a third round.

## PARTIAL is not a stopping point (2026-10-06)
Measured: six seats landed a slice, wrote PARTIAL and sat idle 4-6 hours until the human noticed the fleet had gone
stale. After landing a slice, start the next slice of the same bean in the same turn. Stop only when (a) every
task of the bean is done -> LANDED, or (b) the next step needs someone else -> BLOCKED naming the exact person and
action. A finding in your own branch — even a review's CHANGES — is your work, never a blocker. A tool or review
that returns empty output is not a review: re-run it. The observer never releases a seat on a report that starts
with PARTIAL, IN PROGRESS or WIP: the rest of the bean is still yours.

## Landed and reviewed are decided by the repo, not by the report (2026-10-06)
- **Landed** = a commit on origin/main that names the bean in its message and changes code, or a SHA in the bean
  body that is an ancestor of origin/main. `fleet-landed-check` verifies every bean marked completed; an unproven
  one alerts the lead and is reopened. A bean that lands no code says `Landed: none — <reason>` in its body.
- **Reviewed** = a review artifact with a verdict (APPROVE/CHANGES) or file:line findings, from a different model.
  `fleet-review-check` flags anything else as EMPTY; an empty review does not count — re-run it. Never ask the
  same model "are you sure?" as a substitute: self-correction without outside feedback makes answers worse
  (Huang et al., "Large Language Models Cannot Self-Correct Reasoning Yet", ICLR 2024).
- **One run proves little** (a multi-turn study found run-to-run spread doubled while skill barely moved): a flaky
  gate or a generator gets k>=3 runs, and you report the spread, not the best run.

## A new slice starts from a consolidated spec (2026-10-06)
Long multi-turn sessions lose the task: in a multi-turn study, performance fell ~39% when a spec arrived in pieces,
and restating it as one prompt recovered ~95%. So after landing a slice, when the session is long: (1) write the
REMAINING scope into the bean as one self-contained block — goal, done-criteria, files you own, what already
landed with SHAs, open decisions; (2) report PARTIAL with that block's location; (3) the lead restarts you in a
FRESH session pointed at that block. Do not carry a long session across slices.
