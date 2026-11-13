# Hidden controls

The signals pages read the real API. Controls with no API behind them yet are hidden, each behind one
flag in `src/api/features.js`. This file says what each flag hides, why, and what has to exist before
it comes back.

**To bring a control back:** build what the last column needs, set its flag to `true`, and delete its
row here, all in the same PR.

| Flag | Hides | Why | Comes back when |
|---|---|---|---|
| `appVersions` | App version markers on charts; App version filter and column | Traces' app versions aren't stored | The poller stores app versions from trace metadata |
| `deleteSignals` | Row checkboxes and Delete on the Signals list | No delete endpoint (a signal can be turned off instead) | A delete endpoint, and a decision about what happens to its runs, events and clusters |
| `templates` | Template tiles on the New signal form (and Home's "What visitors asked for" card, which needs an Intent-template signal) | Templates live in the judge half, not built yet | `templates.py` and a templates endpoint |
| `spanTrigger` | "When a specific span finishes" trigger | Runs are only created when a trace finishes | The poller supports span triggers |
| `tokenFilter` | "Total tokens" filter | `matches()` supports only `status` | The poller stores token totals and `matches()` supports them |
| `spanAndTagFilters` | "Span names" and "Tags" filters | Same | The poller stores span names and tags and `matches()` supports them |
| `statusNotEquals` | "does not equal" for the Status filter | `matches()` supports only `status` equals | `matches()` supports `ne` |
| `sampling` | Sampling slider | No sampling in the backend | Sampling is built |
| `backfill` | Backfill button; Settings → Backfill | Backfill isn't built | Backfill jobs exist |
| `versionsTab` | Settings → Versions | No versions endpoint | `GET /api/signals/{id}/versions` |
| `alertsTab` | Settings → Alerts | Alerts aren't built | Alerts exist |
| `notifications` | Notifications page, its sidebar link and the header bell | Alerts aren't built; the page ran on fake data and was removed (Nov 12; it's in git history before branch `ui-clusters-home`) | Alerts exist and an endpoint lists them |
