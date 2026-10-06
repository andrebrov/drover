# Self-serve playbook — how a seat on a cheap or free model keeps itself busy and lands work

In observer mode the lead queue routes each line to the harnesses that may take it. Seats on harnesses that
no line names sat idle beside a full queue. A harness listed in `DROVER_SELF_SERVE` gets this playbook instead:
when nothing queued fits it, `fleet-observe` sends the seat one "self-serve" round (throttled to one per 20
minutes per seat), and the seat picks its own work by the rules below.

## What these seats are good at, and what bit them
- Lands cleanly: one bean, narrow scope, tests first, a stronger model reviews the final diff.
- Failed: ten findings in one bean, two review rounds with open High findings, a push to a fleet branch
  instead of main, reported as done. Each lesson is a rule:
  1. One finding = one commit = one landing. Never batch findings.
  2. DONE means `git merge-base --is-ancestor <sha> origin/main` exits 0. A fleet branch is not main.
  3. After TWO review rounds with open High findings, stop: write `BLOCKED: handoff` with what is fixed, what
     is open and your branch, then `fleet-done ... BLOCKED`. A stronger seat finishes it. That is a correct
     result, not a failure.
  4. Answer your own questions from the rules below before asking. Ask the lead only for a human decision,
     a credential, or a migration to apply.

## Single-owner areas are never self-served
Some areas belong to one seat alone (for example, a quality area the human assigned to one reviewer). List them in
`DROVER_OWNED_AREAS` (`area:owner-seat`, space-separated): `fleet-observe` appends "NEVER take a bean in these
single-owner areas" to every self-serve prompt it sends a seat that is not the owner. A scope decision like this
reaches EVERY prompt source the same hour — this playbook, any role playbook, AND the observer's generated prompt.
When only the playbooks said it, three seats drifted into an owned area from the prompt text.

## Self-serve: picking your own work (only when the observer sends you "self-serve")
1. `cd` to your worktree, `git fetch -q && git checkout --detach origin/main` (a fresh base).
2. Candidates: `beans list --json --ready`, type bug, task or small feature (never an epic — an epic's CHILD
   beans are fine), priority critical/high/normal, inside the project's current focus. Skip anything under
   `$FLEET_STATE/noop-held/<bean>`, anything with `$FLEET_STATE/owner/<bean>`, anything whose body says a human
   decision is pending, migration-only work, any bean in an area another seat owns (`DROVER_OWNED_AREAS`), and
   anything touching more than ~5 files (too big — leave it for the lead).
3. CLAIM before touching code, atomically: `set -o noclobber; echo <your-seat> > $FLEET_STATE/owner/<bean>`.
   If that write fails, someone else has it: pick the next candidate. Then `fleet-claim take <seat> <bean>
   <paths>`.
4. Work it with the land loop (`docs/agent-land-loop.md`), cross-model review included.
5. Nothing eligible? Run ONE bug hunt instead (report only, no fixes) on the least-recently hunted area of the
   current focus. Proof bar: file:line, trigger, wrong user-visible output; an honest empty hunt is valid.
6. Set the bean complete in the SAME push as its last change, with a summary of changes: a landed bean left
   open shows as unowned critical work, and a bean marked complete on a branch that never lands is worse.
   Report to the path the observer gave you. First line: `LANDED <sha> <bean>` | `BLOCKED: <why> <bean>` |
   `HUNT <area>: <n> findings`. Then `fleet-done`. Remove your owner file only if you did NOT land, so the
   bean is re-pickable.

## Standing answers (do not ask the lead these)
- Read-only database checks: load credentials from the project's env file in a subshell, open a read-only
  transaction, and never print, copy, log or pass the values on a command line.
- A disposable database for schema checks: run a throwaway container, and stop it after.
- Migration numbers: `fleet-mignum <bean>`. Never apply a migration; say it needs applying.
- Main is red for reasons outside your diff: say so in the report; never skip the push hook; never loosen a gate.
- Never read tokens or keys out of the database or decrypt them; never guess database users.

Each round starts in a fresh session where the harness supports it (`fleet-observe` sends `/new` to opencode
seats and re-asserts the seat name): rounds piled up in one session until its context ran out mid-bean.
