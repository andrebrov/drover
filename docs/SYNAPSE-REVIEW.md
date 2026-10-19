# Synapse source review and delivery repair

Read 2026-10-19 at upstream commit `b8a5c5f27ddc4ddb23a4b6ee929ee3adc3bb9f6a`.
Primary source: [herdr-synapse](https://github.com/vitalysim/herdr-synapse/tree/b8a5c5f27ddc4ddb23a4b6ee929ee3adc3bb9f6a),
Apache-2.0, Copyright 2026 Vitaly Simonovich ([NOTICE](https://github.com/vitalysim/herdr-synapse/blob/b8a5c5f27ddc4ddb23a4b6ee929ee3adc3bb9f6a/NOTICE)).
These are independently implemented ideas. No upstream code or assets are vendored.

| Candidate | Decision and failure addressed | Source inspected at the pinned commit |
| --- | --- | --- |
| 1. Stable idle delivery | **Adopt.** Direct sends trusted one status value and typed over drafts or into running turns. Use two native snapshots at least two seconds apart, native detection explanation, an explicitly empty supported composer, and a per-seat lock held through send. A hold names its reason; queues retain their items and do not claim a task. | [`gate.py`](https://github.com/vitalysim/herdr-synapse/blob/b8a5c5f27ddc4ddb23a4b6ee929ee3adc3bb9f6a/herdr_team/gate.py), `evaluate`, `prompt_line_text`, `classify_result`; `nudge.py` |
| 2. Conversation identity | **Adopt the send-time fence only.** Compare name, harness, pane, terminal, working directory, state sequence and native conversation value between observations. A changed target holds instead of inheriting a stale seat name; compaction of the same conversation remains eligible. **Reject a second durable membership/resume store.** The native API does not report a conversation for every seat; absent session metadata is explicitly only a terminal fence, not proof of conversation continuity. Existing native resume/assignment history stays authoritative. | [`roster.py`](https://github.com/vitalysim/herdr-synapse/blob/b8a5c5f27ddc4ddb23a4b6ee929ee3adc3bb9f6a/herdr_team/roster.py), `session_of`, `same_session_value`, `remember_session`, `bind` |
| 3. Quota-triggered harness swap | **Reject automatic replacement.** A quota failure does not authorize closing a pane or changing a harness/model. Keep explicit native spawn/resume and operator reassignment; a swap state machine would add another ownership source and require separate lifecycle requirements. | [`swap.py`](https://github.com/vitalysim/herdr-synapse/blob/b8a5c5f27ddc4ddb23a4b6ee929ee3adc3bb9f6a/herdr_team/swap.py), `plan`, `stale_job`, `change` |
| 4. Append-only shared board/read receipts | **Reject a parallel board.** Beans, the assignment ledger, inbox, reports and question/answer records already carry work and outcomes. Delivery acceptance is not a read receipt. No "read by" claim is added without a native consumption event. | [`cmd_board.py`](https://github.com/vitalysim/herdr-synapse/blob/b8a5c5f27ddc4ddb23a4b6ee929ee3adc3bb9f6a/herdr_team/cmd_board.py), `board_append`, `build_record`, `cursor_advance`, `unread_for` |
| 5. General operator delegation | **Reject a new authority layer.** Existing finite routing grants bind job, source, assignment, generation and expiry; do not turn a trusted seat or incoming message into broad operator power. Synapse itself documents that same-user grant files are not a sandbox. Process ancestry is useful provenance but cannot make same-user files a security boundary. | [`identity.py`](https://github.com/vitalysim/herdr-synapse/blob/b8a5c5f27ddc4ddb23a4b6ee929ee3adc3bb9f6a/herdr_team/identity.py), `Author.trusted_human`, ancestry resolution; [`operator.py`](https://github.com/vitalysim/herdr-synapse/blob/b8a5c5f27ddc4ddb23a4b6ee929ee3adc3bb9f6a/herdr_team/operator.py), `active`, `is_live` |
| 6. Six-section standing instruction document | **Reject another standing-orders store.** Roles, scoped assignment briefs, selected rules, acceptance and handoff/report requirements already exist. Private operator notes must not be injected into agent context; the current brief does not ingest such notes. A section template alone would not fix lost delivery. | [`instructions_doc.py`](https://github.com/vitalysim/herdr-synapse/blob/b8a5c5f27ddc4ddb23a4b6ee929ee3adc3bb9f6a/herdr_team/instructions_doc.py), `SECTIONS`, `PRIVATE_SECTIONS`, `parse`, `injected` |
| 7. Work items / work next | **Reject a second work-item engine.** Keep Beans and the dispatcher as the owners/dependencies/next-work source, and existing explicit DONE/PARTIAL/BLOCKED outcomes. Two claim and settlement engines would recreate competing owners. | [`work.py`](https://github.com/vitalysim/herdr-synapse/blob/b8a5c5f27ddc4ddb23a4b6ee929ee3adc3bb9f6a/herdr_team/work.py), brief/settlement/generation contracts, `OUTCOMES`; `cmd_work.py` |
| 8. Harness/profile/model availability | **Reject a parallel model catalog.** Keep configured harness selection and native model reporting. Upstream distinguishes catalog candidates from proof of login access and permits unchecked names when probes are not authoritative. A list cannot promise per-login access. A new probe matrix would not fix this measured delivery defect. | [`cmd_available.py`](https://github.com/vitalysim/herdr-synapse/blob/b8a5c5f27ddc4ddb23a4b6ee929ee3adc3bb9f6a/herdr_team/cmd_available.py), `running_unassigned`, summary/detail output; [`harnesses.py`](https://github.com/vitalysim/herdr-synapse/blob/b8a5c5f27ddc4ddb23a4b6ee929ee3adc3bb9f6a/herdr_team/harnesses.py), catalog authority contract |

## Delivery contract

This repairs the existing "never prompt a working or blocked seat" rule, rather than adding
team/work/authority stores. `lib/fleet_delivery.py` is shared by the normal `fleet` prompt paths,
the observer queue and finite safe routing. Direct assignment checks before preparing a worktree
or writing a brief, then checks again under the send lock. Native send targets the observed pane,
not a name that could be rebound during sampling. Every ordinary send waits for native acceptance.
Native checks make no provider calls and do not send terminal text to a classifier.

A known empty composer is required. Unknown layouts, unreadable native metadata, blocked/working
state, detector disagreement, drafts, dialogs, changing screens, changing identity or concurrent
delivery hold explicitly. Normal callers retain work for a later attempt; they never clear a draft,
approve a dialog, send Enter, or cancel a running turn. Explicit operator `keys`/`cancel` and
controlled harness lifecycle operations remain separate native controls.

The two-second window is a minimum, not a detector-confidence claim. The lock serializes fleet
senders using this helper. It cannot serialize independent human typing or third-party native
clients, and Herdr has no atomic compare-and-send API. No zero-race claim is made.

The review handoff also retains its existing retry entry when native delivery is
held or refused. It claims the reviewer only after success; a later successful
attempt removes that entry. A behavioral watcher test reproduces both the refusal
and the subsequent accepted attempt. This uses the existing queue, not a new board.

Unknown post-send delivery (exit 76) is different from an unsent hold (exit 75):
the watcher records the former in its existing blocked list, holds that seat, and
requires inspection before retry. It never automatically resends an uncertain prompt.
The gate's normalized hold reason is retained in the watcher log.

The direct CLI also writes the existing per-seat hold marker on an unknown result.
Subsequent sends refuse with `seat_held`; an operator must inspect and release the
hold. Observer and finite routing retain their existing task/reservation instead.
