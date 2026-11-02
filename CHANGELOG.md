# Changelog

## Unreleased — tooling sync through 2026-11-02

- Landing: `fleet-review-gate` requires a cross-harness APPROVE receipt naming the landed commit (exact, bean-only descendant or byte-identical rebase); refuses self-review and non-seat reviewers; audit-logs accepted receipts. `fleet-land-bean` runs it (`DROVER_REVIEW_GATE=0` disables) and renames pre-land commit SHAs in beans to their landed equivalents, recording `LANDED_COMMITS`.
- `fleet-slot`: push waiters are admitted in arrival order (flock-held tickets); the push pool keeps its parallel width.
- Seats: usage-limit marks carry the banner's reset time and keep a seat out of dispatch, refill and review routing; `fleet up` refuses panes whose foreground is not an empty shell and never starts lead seats; codex seats launch without the blocking question box; OpenCode seats no longer receive `--model`; coders route their own review with `fleet review-request`.
- Dispatch and assignment: previous-owner stickiness stays within a priority tier; a pinned seat's own BLOCKED handback (or a scrapped bean) releases its pin; every seat left out of `free` is named and `--json` stays parseable; a freshly briefed seat is held for 15 minutes; `fleet assign` refuses the shared checkout and seats holding unlanded work for another bean (`--force-after-land`); `BOARD_DRY` names a thin board's exclusions.
- Delivery: composer placeholders are recognised by rendering (faint, placeholder grey, reverse-video cursor cell), not by wording.

## v0.2.0 — 2026-10-19

- Publish a crawlable GitHub Pages overview, FAQ, source audit, llms.txt, sitemap and SoftwareSourceCode metadata.
- Add dated, attributed single-fleet observations and first-party workflow comparisons; avoid comparative performance claims.
- Add a reproducible synthetic-fixture asciinema demo, local vector banner, citation metadata and macOS CI.
- Include the October 14–19 portable tooling sync: callsigns, configurable seat models, immutable shared dependencies, installation drift preflight, daemon classification, native ownership/precedent, stamped measurements, observatory and project-configured tripwire.

The full operating loop targets macOS and requires Herdr, authenticated coding CLIs and project gates. No live services or paid model calls are needed for the offline fixture suite. This release does not claim a measured speedup, search ranking or AI citation outcome. See docs/UPDATES-2026-10-19.md for tooling revision accounting.
