# Claims and source audit

Read date: **2026-10-19**. OBSERVED means present in the linked source, not independently validated operation of another product. No comparative performance or reliability inference is asserted.

| Claim | Evidence class | Source |
|---|---|---|
| Drover’s local fleet loop, installation requirements and macOS limitations | OBSERVED in source and existing docs | [README](https://github.com/andrebrov/drover/blob/5f8a056328d83f5b7465bf955643f3fecfe1e4b1/README.md), [watcher](https://github.com/andrebrov/drover/blob/5f8a056328d83f5b7465bf955643f3fecfe1e4b1/bin/fleet-watch) |
| 44-minute median, p90 285 minutes, 650 dispatch gaps; retrospective date 2026-09-07 | STATED in maintainer’s dated retrospective; raw ledger not distributed | [Lesson](https://github.com/andrebrov/drover/blob/5f8a056328d83f5b7465bf955643f3fecfe1e4b1/docs/LESSONS.md#announce-when-you-finish--push-not-poll-2026-09-07) |
| Ten done agents with no work/report/error, 2026-09-07 | STATED in the same fleet’s incident record | [Lesson](https://github.com/andrebrov/drover/blob/5f8a056328d83f5b7465bf955643f3fecfe1e4b1/docs/LESSONS.md#staying-inside-the-budget-2026-09-07) |
| $952 daily-meter bug; $856.01 vs $212.60 checked instant, 2026-09-18 | STATED in dated incident record; figures refer to different measurement instants | [Lesson](https://github.com/andrebrov/drover/blob/5f8a056328d83f5b7465bf955643f3fecfe1e4b1/docs/LESSONS.md#a-spend-cap-measured-in-2026-09-18) |
| herdr-synapse: A Herdr plugin with a shared team board, human-owned charter and idle-gated message delivery. | OBSERVED in first-party documentation | [Source](https://github.com/vitalysim/herdr-synapse/blob/main/README.md) |
| Claude Squad: A terminal app using tmux and Git worktrees to run coding agents on separate tasks and review changes. | OBSERVED in first-party documentation | [Source](https://github.com/smtg-ai/claude-squad/blob/main/README.md) |
| Conductor: Its official docs describe parallel coding agents in isolated workspaces, with diff review and pull-request workflows. | OBSERVED in first-party documentation | [Source](https://www.conductor.build/docs) |
| Vibe Kanban: Its README describes kanban planning, agent workspaces and inline diff review; it also announces the project is sunsetting. | OBSERVED in first-party documentation | [Source](https://github.com/BloopAI/vibe-kanban/blob/main/README.md) |
| Claude Code agent teams: Anthropic documents a lead session coordinating Claude Code teammates through shared tasks and inter-agent messages. | OBSERVED in first-party documentation | [Source](https://code.claude.com/docs/en/agent-teams) |

Conductor has no product README in its public release repo (GitHub README endpoint returned 404); its own product documentation is used instead. [Tutorial README](https://github.com/meltylabs/conductor-tutorial/blob/main/README.md) describes a learning project, not the product contract. [Claude Code README](https://github.com/anthropics/claude-code/blob/main/README.md) supplies the official documentation entry point; agent teams are documented separately. No absence of a feature in a README is treated as proof that the product lacks it.

Inference boundary: the combination of published documentation, machine-readable metadata and versioned source makes the material available for indexing and citation. Actual search ranking, traffic and inclusion in an AI answer have **not been measured**. The GitHub Pages site and llms.txt do not guarantee those outcomes.

Structured-data reference (read 2026-10-19): [SoftwareSourceCode](https://schema.org/SoftwareSourceCode) uses `targetProduct` for the target OS/product. [operatingSystem](https://schema.org/operatingSystem) belongs to `SoftwareApplication`, so it is not placed directly on the source-code object.
