# How the pages use the API

Every page reads and writes real data from the Tripwire API. There is no fake data.

| Page | Real data | Still hidden |
|---|---|---|
| Home | Problem events by signal, top problem clusters, run cost, problem events over time, finished runs by outcome | "What visitors asked for" (needs templates) |
| Signals list | Signals, event, cluster and run counts, Activity sparkline, last event, on/off | Delete |
| Signal page → Events | Cluster strip, chart by cluster, events with severity, fields and Laminar link; severity / cluster / search filters; paging | App versions |
| Signal page → Runs | Runs with status, attempts, model, cost, error; status / source / outcome / search filters; chart of finished runs; paging | |
| Signal page → Settings | Edit the definition, turn on/off | Versions, Alerts, Backfill tabs |
| New signal | Create, and **Test on a trace** (judges one stored trace, saves nothing) | Templates, sampling, span trigger, most filters |

Everything hidden is behind a flag in `src/api/features.js`. `HIDDEN_CONTROLS.md` lists each one and
what has to exist before it comes back.

## Running it

```
# 1. Backend: Postgres, API (port 8080), poller, runner, clusterer
cd signals/backend
docker compose up --build

# 2. UI
cd signals/mock-ui
npm install
npm run dev        # http://localhost:5174
```

Vite sends every `/api/...` request to `localhost:8080`, so there's no CORS or API URL to set.
If the backend isn't running, pages say they can't reach the API.

To see events, the backend needs traces: point it at a Laminar project with traffic (see
`signals/backend/.env.example`). The seed (runs once on `docker compose up`) creates one signal to start with.

## Counting happens in the database

A page never counts the rows it loaded. Lists return one page plus the total; charts come from `*-counts`
endpoints that take the same filters as the list beside them. So the table total, the chart and the paging
always describe the whole time range, however many events it holds.

| Endpoint | Returns |
|---|---|
| `GET /api/signals?since&until` | each signal with runs, events, problem events, clusters, last event (when the judge last flagged one), `activity` (events in 24 slices of the range) |
| `GET /api/signals/{id}/events?since&until&severity&cluster&q&offset&limit` | `{ total, events }` |
| `GET /api/signals/{id}/event-counts?bucket_minutes&since&until&severity&cluster&q` | `[{ bucket, cluster_id, events }]` |
| `GET /api/signals/{id}/runs?since&until&status&source&outcome&q&offset&limit` | `{ total, runs }` |
| `GET /api/signals/{id}/run-counts?bucket_minutes&...same filters` | finished runs per bucket: `[{ bucket, event, nothing, failed }]` |
| `GET /api/signals/{id}/clusters?since&until` | `{ events, clustered_events, clusters: [...] }`, clusters with events in the range, most first |
| `GET /api/overview?bucket_minutes&since&until` | everything Home shows |

- **Search** (`q`) is case-insensitive "contains", over field *values* (not field names), trace ID, severity
  and evidence labels for events; status, source, trace ID and error for runs. An exact ID also matches.
  The page waits 300 ms after typing stops before searching.
- **Times** are by when the turn happened (the trace's end), except "last event" on the Signals list,
  which is when the judge flagged it.
- **Buckets** are on the viewer's local clock: the page sends its time zone (`tz=America/Los_Angeles`), hour
  buckets start on local hours and day buckets at local midnight (a daylight-saving day is one 23- or 25-hour
  bucket). Same rule as `makeBuckets()`, so the page matches them by time. An unknown `tz` is a 422.
- **Runs charts and Home count finished runs only** (done or failed). A pending or running run isn't counted
  until it finishes.

## Cluster strip

One bar per cluster with events in the range, sized by its share of the *clustered* events. Events in no
cluster aren't in the strip; the caption under it says how many of the range's events are in a cluster.
The top 8 clusters get their own color (the same in the strip and the chart), the rest share "Other
clusters", events in no cluster are gray. Clicking a cluster filters the chart and table to it (`?cluster=`);
clicking it again clears it.

## Event drawer: what's available (for the Signals pair)

The drawer shows the event's fields, IDs and the Laminar link. It was left as it is on purpose, so you
can decide what it shows. Data that already exists and could go in it:

- **`event_summaries.summary`** and **`.cause`**: one sentence on what went wrong and its likely cause,
  written by the clusterer's summarizer (status `done`; may be pending or failed).
- **`signal_runs.analysis`**: the judge's reasoning. It covers the **whole run**, and a run can produce up
  to 5 events, so label it that way or it reads as an explanation of this one event.

Adding a field takes four small steps: a column in `EVENT_COLUMNS` (`tripwire/api/app.py`), a field on
`EventOut`, one line in `eventFromApi` (`src/api/adapt.js`), and the display in `EventDrawer`
(`src/pages/TripwireDetail.jsx`).

## How the code is laid out

```
src/api/
  client.js     raw fetch calls to /api; throws ApiError (status 0 = API unreachable, 422 = list of problems)
  adapt.js      API responses -> the shapes the pages use, and form values / filters -> API parameters
  hooks.js      useSignals, useSignal, useEvents, useEventCounts, useRuns, useRunCounts, useClusters,
                useOverview: { data, error, loading, reload }
  actions.js    createSignal, editSignal, setActive, testRun: return the saved signal in page shape
  features.js   FEATURES flags for controls with no API yet
  templates.js  the six signal templates (hidden until FEATURES.templates)
  index.js      the only file pages import from
```

- **Pages never call `fetch`.** They use the hooks and actions from `src/api/index.js`.
- **`adapt.js` is the one place that knows the API's field names.** The pages use their own names
  (`tripwireId`, `status: "paused"`, `payloadFields`, run status `completed`, source `trigger`...), so if the
  API changes, only `adapt.js` changes.
- **Times:** inside the pages every time is UTC without a zone (`2026-11-11T22:30:43`), in the address too;
  `adapt.js` converts the API's times to that, and `client.js` adds the `Z` back when sending a range.
  **Everything on screen is in the browser's time zone**: `formatTime`, `formatDay` and the chart labels
  convert, and the range picker shows the zone ("3:06 PM PST"). Picked dates are local days. Never format
  a time by hand. "Now" is the real clock, read when a page opens and on Refresh.
- **Editing a signal** sends only what the form shows. The API keeps the signal's model and critical
  guidance when a request leaves them out (send `critical_guidance: null` to clear it).
- **Validation:** the API checks a definition and answers 422 with a list of problems; the form shows them.

## Adding something

- New endpoint: add the call to `client.js`, a hook or action next to the others, the shape mapping to
  `adapt.js`, and export it from `index.js`.
- Bringing back a hidden control: build the backend piece, set its flag to `true` in `features.js`, and
  delete its row from `HIDDEN_CONTROLS.md`, in the same PR.
