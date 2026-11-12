# Signals pages on the real API

The signals pages now read and write real data from the Tripwire API. The rest of the mock (Home,
Notifications, everything about clusters) still runs on fake data.

| Page | Real data | Still mock or hidden |
|---|---|---|
| Signals list | Signals, event and run counts, last event, on/off | Clusters and Activity columns, Delete |
| Signal page → Events | Events in the time range, severity, fields, link to the trace in Laminar | Cluster strip and colors, app versions |
| Signal page → Runs | Runs with status, attempts, model, cost, error | |
| Signal page → Settings | Edit the definition, turn on/off | Versions, Alerts, Backfill tabs |
| New signal | Create, and **Test on a trace** (judges one stored trace, saves nothing) | Templates, sampling, span trigger, most filters |

Everything hidden is behind a flag in `src/api/features.js`. `HIDDEN_CONTROLS.md` lists each one and
what has to exist before it comes back.

## Running it

```
# 1. Backend: Postgres, API (port 8080), poller, runner
cd signals/backend
docker compose up --build

# 2. UI
cd signals/mock-ui
npm install
npm run dev        # http://localhost:5174
```

Vite sends every `/api/...` request to `localhost:8080`, so there's no CORS or API URL to set.
If the backend isn't running, the signals pages say they can't reach the API; the mock pages still work.

To see events, the backend needs traces: point it at a Laminar project with traffic (see
`signals/backend/.env.example`). The seed (runs once on `docker compose up`) creates one signal to start with.

## How the code is laid out

```
src/api/
  client.js    raw fetch calls to /api; throws ApiError (status 0 = API unreachable, 422 = list of problems)
  adapt.js     API responses -> the shapes the pages were built on, and form values -> API bodies
  hooks.js     useSignals, useSignal, useEvents, useRuns: { data, error, loading, reload }
  actions.js   createSignal, editSignal, setActive, testRun: return the saved signal in page shape
  features.js  FEATURES flags for controls with no API yet
  index.js     the only file pages import from
```

- **Pages never call `fetch`.** They use the hooks and actions from `src/api/index.js`.
- **`adapt.js` is the one place that knows the API's field names.** The pages kept the mock's shapes
  (`tripwireId`, `status: "paused"`, `payloadFields`, run status `completed`...), so if the API changes,
  only `adapt.js` changes.
- **Times:** the API sends UTC with a zone; `adapt.js` cuts it to the mock's format (`2026-11-11T22:30:43`,
  UTC without a zone) so `formatTime`, `rangeFromParams` and the charts work unchanged. `client.js` adds
  the `Z` back when sending a range. On the signals pages "now" is the real clock, not the story clock.
- **Validation:** the API checks a definition and answers 422 with a list of problems; the form shows them.
- **Templates and preset/custom:** a signal made from a template is "preset", otherwise "custom".

## Adding something

- New endpoint: add the call to `client.js`, a hook or action next to the others, the shape mapping to
  `adapt.js`, and export it from `index.js`.
- Bringing back a hidden control: build the backend piece, set its flag to `true` in `features.js`, and
  delete its row from `HIDDEN_CONTROLS.md`, in the same PR.
- Clusters: the cluster parts of the signal page are hidden behind `clusterCounts`. Events already carry
  `clusterId` from the API, so the clustering UI can plug in there.
