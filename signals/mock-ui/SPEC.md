# Tripwire Mock UI Spec

*Copy of the "Tripwire Mock UI Spec" doc, Nov 4, 2026. The doc is the source of truth; refresh this file when the doc changes. Rules for AIs are in AGENTS.md.*

> **Nov 12, 2026:** every page now reads the real Tripwire API, and the fake data, story day picker,
> Notifications page and `/style` page are gone. The "Fake data", "Story day" and "Where things are" sections
> below describe the Nov 5 mock and are kept for history. How the pages use the API: `SIGNALS_API.md`.

A clickable React mock of Tripwire's agreed core features, running on fake data, for the demo on Nov 5, 2026. Not connected to Laminar or our services. 

## Terms

"Tripwire" is the product name (header only). In the UI: **signal**, **event** (signal event), **cluster**. Decided Nov 4; may change.

## Screens

Five screens, agreed Nov 4. Each follows Laminar where Laminar has the same thing. Reports are not decided yet, so no screen for them.

| Screen | Route | Shows |
| --- | --- | --- |
| Home | `/` | A fixed dashboard for signals, built from Laminar's dashboard card types (not customizable). Top bar: time range (default 24 hours), By hour / By day (default: hour up to 7 days, day beyond), Refresh. "Problem" = critical or warning, so signals that produce an event for every trace don't turn event cards into a traffic count. Cards: **Problem events by signal**, **Top problem clusters**, **Signal run cost** (ranked lists, top 5 by the card's number in the range; ties by name; zeros hidden; "View all signals →"), **Problem events over time** (top 5 signals + Other; legend items hide/show lines, ↗ opens the signal), **What visitors asked for** (Intent Classifier's clusters with share; only when a signal made from the Intent template exists), **Signal runs (all signals)** (each run = one signal reading one trace; found an event / found nothing / failed, with a tooltip on Failed). Works with any set of signals; only Failure Detector is created by default in Laminar. Rows and legend links open the signal on the same time range. |
| Signals list | `/signals` | Modeled on Laminar's Signals list: table with Status, Name, Events, Clusters, Activity (sparkline), Last event, Runs, Created. Search by name, time range (default 7 days), "New tripwire" button. Click a row to open its detail. |
| New signal | `/signals/new` | Start from a preset or blank; name; plain-language instruction; payload fields (name + description each); trigger filters and sample rate (which traces it reads); test on a sample trace (pick a trace, see the event it would produce); run on past traces (backfill); Create. |
| Signal detail | `/signals/:id` | Modeled on Laminar's Signal page. Tabs: **Events** (cluster strip with drill-down into sub-clusters; stacked events-over-time bars by cluster, gray = not in a cluster; app version markers; Add filter, Columns, Refresh; search; table of Timestamp, Severity, the tripwire's payload fields, Trace ID (opens Laminar), ID; row opens an event panel), **Runs** (runs chart: event / no event / failed, total; table of Status, Source, Event, Clusters, Cost, Updated at, Event ID), **Settings** (Laminar's sections: General / Versions / Alerts / Backfill; all placeholders for now. General will be the shared signal form in edit mode). Time range picker on Events and Runs (default: past 3 days): typed duration, presets, absolute dates; bar size follows the range. |
| Notifications | `/notifications` + bell in the header | In-app list of both alert types; alert settings per tripwire: new cluster on/off, new critical event on/off, "skip similar events" on/off, Slack on/off; a mock Slack message. |

Not screens: sync, backfill jobs, reliability and AWS deployment. The demo can mention them while showing the screens.

**Notifications model (agreed):** like Laminar, two alert types. **New cluster** fires when a cluster forms. **New event** fires only for Critical tripwire events, with "skip similar events" on by default: only the first critical event in a cluster notifies.

"Open in Laminar" links can point at real local Laminar URLs if handy; otherwise a placeholder is fine for the mock.

## Look

Agreed: the style sample as it is. Mantine components keep everyone's pages consistent.

- **Stack:** React + Vite + Mantine (components, theme) + Mantine Charts. React Router for the five routes.
- **Layout:** left sidebar (Home, Tripwires, Notifications), header with "Tripwire" and the notification bell.
- **Theme:** `theme.js` from the style sample (teal accent, medium corners). Dark mode only, with a darker background close to Laminar's.
- **Conventions:** main action = filled button; secondary = `variant="light"`; severity badges Critical red, Warning yellow, Info gray; preset vs custom badges blue vs grape.
- **Tone:** plain labels a newcomer understands ("Tripwire events this week").
- **Where it lives:** a `mock-ui/` folder in our repo, separate from the existing prototype frontend.

## Fake data

Every screen reads one file, `mock-ui/src/mock/data.js`. Proposed story: the Olympic bug from the Nov 3 mentor demo, over 7 days (not final yet).

**The story**

1. Day 1: a content update leaves some Olympic trails without a length. The trail lookup tool errors, the assistant gives vague answers, visitors get frustrated. Friction Detector events form the cluster "Trail details missing for Olympic" → **new cluster** notification.
2. Day 3: the first fix stops the error, but the assistant now invents trail lengths. No error anywhere. The custom tripwire "Made-up park facts" fires with **Critical** events → **new critical event** notification (once; later similar events are skipped).
3. Day 6: the real fix lands; both clusters go quiet.
4. Background: ordinary events (sold-out campsites, large-party bookings) so lists don't look staged.

**Data shape** (a starting point; plain JS objects)

| Object | Fields |
| --- | --- |
| tripwire | id, name, kind (preset / custom), status, createdAt, templateId, instruction, payloadFields [{name, description, type, choices}], trigger (trace / span) + spanName, filters [{field, operator, value}], sampling (%), alerts {newCluster, newCriticalEvent, skipSimilar, slack}, backfill? |
| tripwireEvent | id, tripwireId, traceId, conversationId, time, appVersion, severity (critical / warning / info), payload {…}, excerpt (visitor message + reply), clusterId |
| cluster | id, tripwireId, parentId (null for top level), name, summary |
| run | id, tripwireId, traceId, time, source (trigger / backfill), status (completed / failed), eventId (null = no event), clusterId, cost |
| tripwireTemplate | the 6 Laminar templates (Failure, Logic, Task, Friction, Hallucination, Intent): instruction + payloadFields; preset tripwires use them |
| sampleTrace | scripted traces for "Test on a sample trace", with each template's result |
| version | app version + when it was first seen (version markers on charts) |
| notification | id, time, type (new cluster / new critical event), tripwireId, clusterId, text, read |

Tripwires on the list: Failure Detector, Friction Detector, Intent Classifier, Task Evaluator (Laminar's presets), plus the custom "Made-up park facts". Intent Classifier and Task Evaluator produce an event for every trace they read, as in Laminar. Task Evaluator samples 20% of traces to keep its cost down.

## Story day and links

- **Story day picker** (header): Day 1 (Oct 29, 6 pm), Day 3 (Oct 31, 6 pm), Day 6 (Nov 3, 6 pm), Today (Nov 4, 9:30 am).
  Every screen shows only what had happened by then. A demo control, not a product feature. A notification is unread for 48 hours.
- **Developer's path** the screens must support: notification → Signal detail with that cluster selected and the event
  highlighted → "Open in Laminar".
- **Signal detail URL:** `/signals/<id>?cluster=<clusterId>&event=<eventId>` (both optional). Build links with
  `src/components/links.js`.

## Where things are in the code

- Data: `src/mock/data.js`. Pages read it with `useStoryData()` (`src/components/StoryClock.jsx`): `tripwireSummary`, `eventsFor`, `eventsPerDay`, `topClusters`, `subClusters`, `eventsInCluster`, `notifications`, `unreadCount`, ...
- Links: `src/components/links.js`.
- Shared components: `src/components/` (`AppLayout`, `PageHeader`, `Badges`, `Charts`, `LaminarLink`, `StoryClock`, `format`).
- Reference page with every component: `/style` (`src/pages/StyleSample.jsx`).
