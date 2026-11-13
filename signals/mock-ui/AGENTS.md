# Tripwire mock UI: rules

Read this before changing anything in `mock-ui/`. What each screen shows is in `SPEC.md`.
Before touching a signals page or `src/api/`, also read `SIGNALS_API.md` (how they're wired to the API).

- **One data source: the real API.** Every page reads it through `src/api/` (hooks such as `useSignals`,
  `useSignal`, `useEvents`, `useEventCounts`, `useRuns`, `useRunCounts`, `useClusters`, `useOverview`, and actions
  `createSignal`, `editSignal`, `setActive`, `testRun`). Never call `fetch` in a page; add a function to
  `src/api/` instead. `src/api/adapt.js` turns API responses into the shapes the pages use. There is no fake data.
- **Count in the database, never in the page.** Totals, charts, filters, search and paging come from the API
  (lists return `{ total, ... }` for one page; `*-counts` endpoints return per-bucket counts with the same
  filters). A page never counts the rows it loaded: one page of rows isn't the whole time range.
- **Hidden controls.** Controls with no API behind them yet are hidden behind flags in
  `src/api/features.js`. `HIDDEN_CONTROLS.md` lists each flag, what it hides, and what must exist before
  it comes back. Don't show a hidden control without turning its flag on and removing its row from that
  file in the same PR.
- **Running.** Start the backend (`docker compose up --build` in `signals/backend`); `npm run dev` proxies
  `/api` to `localhost:8080`. Without the backend, pages show a "can't reach the API" message.
- **Links between screens come from `src/components/links.js`** (`tripwireUrl(id, { tab, clusterId, eventId, range })`; pass `range` so the signal page opens on the same time range,
  `tripwireListUrl()`, ...). Never write a route by hand. Links to Laminar come from the API (`laminar_url`).
  Signal detail reads `?tab=`, `?cluster=`, `?event=` and the time range (`?pastHours=` or `?startDate=&endDate=`).
- **Time ranges:** use `TimeRangePicker` (it keeps the range in the address) and `rangeFromParams()` /
  `bucketMinutesFor()` / `makeBuckets()` from `src/components/timeRange.js`. "Now" is the real clock, read
  once when the page opens and again on Refresh (pass it to `TimeRangePicker` as `now`). Times are stored UTC
  but shown in the browser's time zone: always display them with `formatTime` / `formatDay`.
- **Charts over time:** `TimeBarChart` (bars) and `TimeLineChart` (lines, for dashboard cards) from `src/components/Charts.jsx`. Cluster colors: `clusterColor(i)` from `theme.js`
  (events in no cluster use `UNCLUSTERED_COLOR`).
- Read `SPEC.md` before building a page. Build only what it describes; ask instead of inventing features.
- Use Mantine components and `src/theme.js`. No hard-coded colors, font sizes or spacing.
- Conventions: main action = filled `Button`; secondary = `variant="light"`; tertiary = `variant="subtle" color="gray"`.
  Severity badges: `SeverityBadge`. Preset/custom badges: `KindBadge`. Times and counts: `formatTime`, `formatDay`, `formatRelative(iso, asOf)`, `formatCost`, `shortId`, `plural` from `src/components/format.js`.
- Reuse components from `src/components/` and the hooks in `src/api/`. Don't copy data into a page.
- Don't edit `src/components/`, `src/api/`, `src/theme.js`, `src/App.jsx`, `vite.config.js`, `package.json` or this file in a page PR.
  If a page needs a shared change, stop and say what you need; it goes in its own small PR first.
- Each page lives in `src/pages/<Page>.jsx`. Only edit the page you were asked to build. Replace its placeholder entirely.
- The existing pages are the visual reference (the `/style` sample page was removed with the fake data).
- Words: "Tripwire" is only the product name in the header. Everywhere else in the UI: **signal** (a detector),
  **event** or **signal event** (one turn where it fired), **cluster** (a group of similar events).
  Code names (`tripwire*` helpers, `tripwireId`, file names) stay as they are for now; only UI text and routes say signal.
- Dark mode only. Don't add new npm packages without asking.
- Run `npm run build` before saying a page is done; it must build without errors.
