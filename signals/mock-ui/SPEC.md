# Tripwire Mock UI Spec

*Copy of the "Tripwire Mock UI Spec" doc, Nov 4, 2026. The doc is the source of truth; refresh this file when the doc changes. Rules for AIs are in AGENTS.md.*

A clickable React mock of Tripwire's agreed core features, running on fake data, for the demo on Nov 5, 2026. Not connected to Laminar or our services. 

## Screens

Five screens, agreed Nov 4. Each follows Laminar where Laminar has the same thing. Reports are not decided yet, so no screen for them.

| Screen | Route | Shows |
| --- | --- | --- |
| Home | `/` | Dashboard, readable at a glance: tripwires that are spiking, newly formed clusters, recent critical tripwire events, unread notifications. Each item links to its tripwire or cluster. |
| Tripwire list | `/tripwires` | Modeled on Laminar's Signals list: table with Status, Name, Events, Clusters, Activity (sparkline), Last event, Runs, Created. Search by name, time range (default 7 days), "New tripwire" button. Click a row to open its detail. |
| New tripwire | `/tripwires/new` | Start from a preset or blank; name; plain-language instruction; payload fields (name + description each); trigger filters and sample rate (which traces it reads); test on a sample trace (pick a trace, see the event it would produce); run on past traces (backfill); Create. |
| Tripwire detail | `/tripwires/:id` | Events-over-time chart; cluster tree (broad clusters that open into sub-clusters; click one to filter); tripwire events table with severity, payload fields, time, app version filter, "Open in Laminar" per event. |
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
| tripwire | id, name, kind (preset / custom), status (active / paused), instruction, payloadFields \[{name, description}\], triggerFilters, sampleRate, alerts {newCluster, newCriticalEvent, skipSimilar, slack}, createdAt |
| tripwireEvent | id, tripwireId, traceId, conversationId, time, appVersion, severity (critical / warning / info), payload {…}, excerpt (visitor message + reply), clusterId |
| cluster | id, tripwireId, parentId (null for top level), name, summary |
| run | per tripwire per day: traces read, events produced (feeds Runs and Activity) |
| notification | id, time, type (new cluster / new critical event), tripwireId, clusterId, text, read |

Tripwires on the list: Failure Detector, Friction Detector, Intent Classifier, Task Evaluator (Laminar's presets), plus the custom "Made-up park facts". Intent Classifier and Task Evaluator produce an event for every trace they read, as in Laminar.

## Where things are in the code

- Data and helpers: `src/mock/data.js` (`tripwireSummary`, `eventsFor`, `eventsPerDay`, `topClusters`, `subClusters`, `eventsInCluster`, `notifications`, `laminarTraceUrl`, ...).
- Shared components: `src/components/` (`AppLayout`, `PageHeader`, `Badges`, `Charts`, `LaminarLink`, `format`).
- Reference page with every component: `/style` (`src/pages/StyleSample.jsx`).
