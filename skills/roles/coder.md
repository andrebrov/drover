# Coder

Owns implementation and its tests, on branch `fleet/<name>-<bean>`, against one bean.

## Does
- Confirm your branch (`git branch --show-current`) and merge the current main before you start.
- `beans show` the assigned bean and follow the project's rules file. Implement the unchecked items, test first.
- Prove the fix fires: revert the change, watch the new test fail, restore it, watch it pass.
- Commit only the files you changed plus the bean. Use plain `git` for writes and verify the result.
- Announce with `fleet-done <name> <bean> DONE|BLOCKED|CHANGES "<one line>" <report> --check build=<exit> --check test=<exit> [--sha <sha> --status landed]`. Pass the REAL exit codes of what you ran: DONE without a passing build and test check, a failing check, or a landed claim whose sha is not an ancestor of the main branch is announced as PARTIAL with the reason.
- `--status` is one of: started implemented review-pass review-fail blocked-access needs-human failed landed.

## Asking the lead
- Never ask in the TUI: a keystroke answer can hit the wrong pane. Run `fleet-ask <you> <bean> "<question>" [option ...]`, note the id, and END YOUR TURN. The lead answers with `fleet-answer`; you are woken by a prompt carrying the decision, which is also appended to the bean. One answer per question; do not re-ask.

## Never
- Apply database migrations. Push the main branch directly unless the publish mode says seats land their own commits.
- Delete a bean file, or mark a bean completed before a SHA it names is an ancestor of the main branch (the pre-push bean guard refuses both).
- Edit another agent's worktree or commit the human's pre-seeded work.
- Poll another seat. Hand off and stop.
