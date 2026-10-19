# Ownership audit — synthetic fixture demo

Recorded 2026-10-19 using the real `fleet-claims-audit` CLI. Native roster, board and owner/task inputs are **synthetic local fixtures** checked into `tests/fixtures/claims-audit`; there is no live Herdr session or paid agent invocation. Output is unmodified; dwell time is edited for a 15-second playback. This is a behavior demonstration, not a speed or throughput measurement.

Replay: `asciinema play docs/assets/demo.cast`.
Re-record offline: `python3 scripts/record-demo.py`.

```text
drover / synthetic fixtures / real CLI output
Playback dwell time edited for reading; no live agents.

$ fleet-claims-audit --fixture clean
fleet-claims-audit: OK — current owner and native task sources agree
exit 0

$ fleet-claims-audit --fixture duplicate
fleet-claims-audit: UNKNOWN DUPLICATE_CURRENT_SEATS bean=bean-a001 seats=['alpha', 'beta'] — competing current sources; ownership is unknown
fleet-claims-audit: UNKNOWN OWNER_TASK_DISAGREEMENT seat=alpha owner_bean=None task_bean=bean-a001 — latest bean owner differs from this task snapshot
exit 2

Check evidence before dispatch. Resolve UNKNOWN; do not release a seat by status alone.
```

Exit 2 is UNKNOWN: competing ownership evidence must be resolved before dispatch. The demo never repairs native state.
