# Tripwire mock UI: rules

Read this before changing anything in `mock-ui/`. What each screen shows is in `SPEC.md`.

- **Two data sources.** The signals pages (Signals list, signal detail with its Events, Runs and Settings
  tabs, and the New signal / edit form) read the **real API** through `src/api/` (`useSignals`, `useSignal`,
  `useEvents`, `useRuns`, `createSignal`, `editSignal`, `setActive`, `testRun`). Never call `fetch` in a
  page; add a function to `src/api/` instead. `src/api/adapt.js` turns API responses into the shapes the
  pages use. Every other page (Home, Notifications) and everything about clusters still reads mock data
  through `useStoryData()` from `src/components/StoryClock.jsx`; the rules about `useStoryData()` below
  apply to them.
- **Hidden controls.** Controls with no API behind them yet are hidden behind flags in
  `src/api/features.js`. `HIDDEN_CONTROLS.md` lists each flag, what it hides, and what must exist before
  it comes back. Don't show a hidden control without turning its flag on and removing its row from that
  file in the same PR.
- **Running with real data.** Start the backend (`docker compose up --build` in `signals/backend`); `npm run
  dev` proxies `/api` to `localhost:8080`. Without the backend, the signals pages show a "can't reach the
  API" message and the mock pages still work.
- **Never import data from `src/mock/data.js` in a page.** On mock pages, call `useStoryData()` and use what it returns
  (`tripwires`, `tripwireSummary`, `eventsFor`, `eventsBetween`, `runsFor`, `runsBetween`, `eventsPerDay`,
  `topClusters`, `subClusters`, `eventsInCluster`, `leafIdsOf`, `versions`, `tripwireTemplates`, `sampleTraces`, `notifications`, `unreadCount`, `asOf`, ...). It shows the data as of the story day picked in the header
  (Day 1 / Day 3 / Day 6 / Today), so every page follows the day picker. A tripwire, cluster or event that
  doesn't exist yet on that day is simply missing: handle "not found" and empty lists.
- **Links between screens come from `src/components/links.js`** (`tripwireUrl(id, { tab, clusterId, eventId, range })`; pass `range` so the signal page opens on the same time range,
  `notificationUrl(n)`, `tripwireListUrl()`, `laminarTraceUrl(traceId)`, ...). Never write a route by hand.
  Signal detail reads `?tab=`, `?cluster=`, `?event=` and the time range (`?pastHours=` or `?startDate=&endDate=`).
- **Time ranges:** use `TimeRangePicker` (it keeps the range in the address) and `rangeFromParams()` /
  `bucketMinutesFor()` / `makeBuckets()` from `src/components/timeRange.js`. On mock pages, "now" is
  `useStoryData().asOf`, never `Date.now()`. On API pages, "now" is the real clock.
- **Charts over time:** `TimeBarChart` (bars) and `TimeLineChart` (lines, for dashboard cards) from `src/components/Charts.jsx`. Cluster colors: `clusterColor(i)` from `theme.js`
  (events in no cluster use `UNCLUSTERED_COLOR`).
- Read `SPEC.md` before building a page. Build only what it describes; ask instead of inventing features.
- Use Mantine components and `src/theme.js`. No hard-coded colors, font sizes or spacing.
- Conventions: main action = filled `Button`; secondary = `variant="light"`; tertiary = `variant="subtle" color="gray"`.
  Severity badges: `SeverityBadge`. Preset/custom badges: `KindBadge`. Times and counts: `formatTime`, `formatDay`, `formatRelative(iso, asOf)`, `formatCost`, `shortId`, `plural` from `src/components/format.js`.
- Reuse components from `src/components/`, the hooks in `src/api/` (API pages) and the helpers from
  `useStoryData()` (mock pages: counts, events per day, cluster tree). Don't copy data into a page.
- Don't edit `src/components/`, `src/api/`, `src/mock/data.js`, `src/theme.js`, `src/App.jsx`, `vite.config.js`, `package.json` or this file in a page PR.
  If a page needs a shared change, stop and say what you need; it goes in its own small PR first.
- Each page lives in `src/pages/<Page>.jsx`. Only edit the page you were asked to build. Replace its placeholder entirely.
- `src/pages/StyleSample.jsx` (route `/style`) is the visual reference. Pages should look like it.
- Words: "Tripwire" is only the product name in the header. Everywhere else in the UI: **signal** (a detector),
  **event** or **signal event** (one turn where it fired), **cluster** (a group of similar events).
  Code names (`tripwire*` helpers, `tripwireId`, file names) stay as they are for now; only UI text and routes say signal.
- Dark mode only. Don't add new npm packages without asking.
- Run `npm run build` before saying a page is done; it must build without errors.
