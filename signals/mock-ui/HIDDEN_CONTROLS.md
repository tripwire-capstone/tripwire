# Hidden controls

The signals pages read the real API. Controls with no API behind them yet are hidden, each behind one
flag in `src/api/features.js`. This file says what each flag hides, why, and what has to exist before
it comes back.

**To bring a control back:** build what the last column needs, set its flag to `true`, and delete its
row here, all in the same PR.

| Flag | Hides | Why | Comes back when |
|---|---|---|---|
| `clusterCounts` | Clusters column on the Signals list; cluster strip and cluster colors on the signal page; cluster filter | No clusters endpoint yet | The clustering pair adds its clusters endpoint and UI |
| `activityColumn` | Activity sparkline on the Signals list | The API returns totals, not counts per day | An activity endpoint (counts per signal per day) |
| `appVersions` | App version markers on charts; App version filter and column | Traces' app versions aren't stored | The poller stores app versions from trace metadata |
| `templates` | Template tiles on the New signal form | Templates live in the judge half, not built yet | `templates.py` and a templates endpoint |
| `spanTrigger` | "When a specific span finishes" trigger | Runs are only created when a trace finishes | The poller supports span triggers |
| `tokenFilter` | "Total tokens" filter | `matches()` supports only `status` | The poller stores token totals and `matches()` supports them |
| `spanAndTagFilters` | "Span names" and "Tags" filters | Same | The poller stores span names and tags and `matches()` supports them |
| `sampling` | Sampling slider | No sampling in the backend | Sampling is built |
| `backfill` | Backfill button; Settings → Backfill | Backfill isn't built | Backfill jobs exist |
| `versionsTab` | Settings → Versions | No versions endpoint | `GET /api/signals/{id}/versions` |
| `alertsTab` | Settings → Alerts | Alerts aren't built | Alerts exist |
