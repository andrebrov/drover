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
8. Push: `git push origin HEAD:main`. The pre-push hook builds, runs the related tests, and holds ONE
   fleet-wide push lock (it waits while another push runs — do not kill it; see
   `examples/pre-push-lock.sh`). NEVER skip the hook. Non-fast-forward → back to step 6. A hook failure
   is fixed, never bypassed.
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
