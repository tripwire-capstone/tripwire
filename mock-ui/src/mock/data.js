// Shared fake data for the Tripwire mock UI.
//
// Every page reads from this file, so counts and names agree across screens.
// Don't edit it in a page PR: propose changes in their own small PR.
//
// The story (proposed): the Olympic bug, over 7 days.
//   Day 1 (Oct 29): a content update leaves some Olympic trails without a length. The trail
//     lookup tool errors, the assistant gives vague answers, visitors get frustrated.
//   Day 3 (Oct 31): fix 1 (app v1.4.1) stops the error, but the assistant now invents trail
//     lengths. No error anywhere. The custom tripwire "Made-up park facts" catches it (Critical).
//   Day 6 (Nov 3): fix 2 (app v1.4.2) repairs the data; both problems go quiet.
//   Background: sold-out campsites and large-party bookings, so lists don't look staged.
//
// Events, runs, cluster counts and notifications are all generated from the tables below,
// so they can't disagree. Use the helper functions at the bottom instead of re-counting.

export const DAYS = ['2026-10-29', '2026-10-30', '2026-10-31', '2026-11-01', '2026-11-02', '2026-11-03', '2026-11-04'];
export const DAY_LABELS = ['Oct 29', 'Oct 30', 'Oct 31', 'Nov 1', 'Nov 2', 'Nov 3', 'Nov 4'];
export const NOW = '2026-11-04T09:30:00';

// App version in production on each day (from trace metadata).
const APP_VERSION_BY_DAY = ['v1.4.0', 'v1.4.0', 'v1.4.1', 'v1.4.1', 'v1.4.1', 'v1.4.2', 'v1.4.2'];

// Traces arriving per day (every tripwire reads these, unless it started later).
const TRACES_PER_DAY = [21, 23, 22, 20, 24, 22, 12];

export const LAMINAR_PROJECT_URL = 'http://localhost:5667/project/demo';

// ---------------------------------------------------------------- tripwires

export const tripwires = [
  {
    id: 'failure-detector', name: 'Failure Detector', kind: 'preset', status: 'active', createdAt: '2026-10-21',
    instruction: 'Find runs where the agent failed to do what the user asked: tool errors it could not recover from, wrong actions, or giving up.',
    payloadFields: [
      { name: 'failure', description: 'What went wrong, in one sentence' },
      { name: 'source', description: 'tool, model, or user input' },
    ],
    triggerFilters: [], sampleRate: 100,
    alerts: { newCluster: true, newCriticalEvent: true, skipSimilar: true, slack: true },
  },
  {
    id: 'friction-detector', name: 'Friction Detector', kind: 'preset', status: 'active', createdAt: '2026-10-21',
    instruction: 'Find turns where the user shows friction: repeating themselves, correcting the assistant, or expressing annoyance.',
    payloadFields: [
      { name: 'friction', description: 'What the user struggled with' },
      { name: 'user_signal', description: 'The words that show it' },
    ],
    triggerFilters: [], sampleRate: 100,
    alerts: { newCluster: true, newCriticalEvent: true, skipSimilar: true, slack: true },
  },
  {
    id: 'intent-classifier', name: 'Intent Classifier', kind: 'preset', status: 'active', createdAt: '2026-10-21',
    instruction: 'What was the user trying to accomplish? Classify the primary intent.',
    payloadFields: [{ name: 'intent', description: 'The primary intent, in a few words' }],
    triggerFilters: [], sampleRate: 100,
    alerts: { newCluster: false, newCriticalEvent: false, skipSimilar: true, slack: false },
  },
  {
    id: 'task-evaluator', name: 'Task Evaluator', kind: 'preset', status: 'active', createdAt: '2026-10-21',
    instruction: 'Did the agent complete the task the user asked for? Record the task and the outcome.',
    payloadFields: [
      { name: 'task', description: 'The task the user asked for' },
      { name: 'outcome', description: 'completed, partly completed, or not completed' },
    ],
    triggerFilters: [], sampleRate: 100,
    alerts: { newCluster: false, newCriticalEvent: false, skipSimilar: true, slack: false },
  },
  {
    id: 'made-up-park-facts', name: 'Made-up park facts', kind: 'custom', status: 'active', createdAt: '2026-10-31',
    instruction: "Find replies that state park facts (trail lengths, hours, fees, amenities) that the tool outputs don't support.",
    payloadFields: [
      { name: 'claim', description: 'The fact the assistant stated' },
      { name: 'evidence', description: 'What the tool output actually said' },
    ],
    triggerFilters: [{ field: 'metadata.app_version', op: 'starts with', value: 'v1.4' }], sampleRate: 100,
    alerts: { newCluster: true, newCriticalEvent: true, skipSimilar: true, slack: true },
  },
];

// ---------------------------------------------------------------- clusters (a tree)
// parentId null = top level. Events belong to leaf clusters only.

export const clusters = [
  // Failure Detector
  { id: 'fd-tools', tripwireId: 'failure-detector', parentId: null, name: 'Tool errors' },
  { id: 'fd-olympic', tripwireId: 'failure-detector', parentId: 'fd-tools', name: 'Olympic trail lookup failing',
    summary: 'get_park_details errors for some Olympic trails; the assistant cannot answer trail questions.' },
  { id: 'fd-booking', tripwireId: 'failure-detector', parentId: null, name: 'Booking problems' },
  { id: 'fd-party', tripwireId: 'failure-detector', parentId: 'fd-booking', name: 'Booking refused for large parties',
    summary: 'Bookings for parties of 7 to 8 are refused, below the real limit of 8.' },

  // Friction Detector
  { id: 'fr-info', tripwireId: 'friction-detector', parentId: null, name: 'Missing park information' },
  { id: 'fr-olympic', tripwireId: 'friction-detector', parentId: 'fr-info', name: 'Trail details missing for Olympic',
    summary: 'Visitor asks for Olympic trail lengths; the assistant gives vague answers after the trail lookup fails.' },
  { id: 'fr-amenities', tripwireId: 'friction-detector', parentId: 'fr-info', name: 'Campground amenities unknown',
    summary: 'Visitor asks which campgrounds have showers; the tools have no amenity data.' },
  { id: 'fr-booking', tripwireId: 'friction-detector', parentId: null, name: 'Booking problems' },
  { id: 'fr-soldout', tripwireId: 'friction-detector', parentId: 'fr-booking', name: 'Sold-out campsite dates',
    summary: 'Visitor is unhappy that the requested dates are sold out; the assistant behaves correctly.' },
  { id: 'fr-party', tripwireId: 'friction-detector', parentId: 'fr-booking', name: 'Booking refused for large parties',
    summary: 'Visitor with a party of 7 or 8 is told the limit is 6 and has to split the group.' },

  // Intent Classifier (fires on every trace)
  { id: 'ic-plan', tripwireId: 'intent-classifier', parentId: null, name: 'Planning a visit' },
  { id: 'ic-trails', tripwireId: 'intent-classifier', parentId: 'ic-plan', name: 'Trail recommendations', summary: 'Visitor wants a trail that fits their group.' },
  { id: 'ic-info', tripwireId: 'intent-classifier', parentId: 'ic-plan', name: 'Park information', summary: 'Visitor asks about hours, fees, conditions or amenities.' },
  { id: 'ic-book', tripwireId: 'intent-classifier', parentId: null, name: 'Booking' },
  { id: 'ic-campsite', tripwireId: 'intent-classifier', parentId: 'ic-book', name: 'Book a campsite', summary: 'Visitor wants to reserve a campsite.' },
  { id: 'ic-change', tripwireId: 'intent-classifier', parentId: 'ic-book', name: 'Change a booking', summary: 'Visitor wants to fix or change an existing booking.' },

  // Task Evaluator (fires on every trace)
  { id: 'te-done', tripwireId: 'task-evaluator', parentId: null, name: 'Task completed' },
  { id: 'te-done-book', tripwireId: 'task-evaluator', parentId: 'te-done', name: 'Booking completed', summary: 'A campsite was booked as asked.' },
  { id: 'te-done-info', tripwireId: 'task-evaluator', parentId: 'te-done', name: 'Question answered', summary: 'The visitor got the information they asked for.' },
  { id: 'te-not', tripwireId: 'task-evaluator', parentId: null, name: 'Task not completed' },
  { id: 'te-not-trail', tripwireId: 'task-evaluator', parentId: 'te-not', name: 'Trail question unanswered', summary: 'The visitor did not get the trail details they asked for.' },
  { id: 'te-not-book', tripwireId: 'task-evaluator', parentId: 'te-not', name: 'Booking not made', summary: 'The visitor left without the booking they wanted.' },

  // Made-up park facts (custom, from Oct 31)
  { id: 'mf-invented', tripwireId: 'made-up-park-facts', parentId: null, name: 'Invented park details' },
  { id: 'mf-trails', tripwireId: 'made-up-park-facts', parentId: 'mf-invented', name: 'Invented trail lengths at Olympic',
    summary: 'The assistant states a trail length for Olympic trails when the tool returned none.' },
  { id: 'mf-amenities', tripwireId: 'made-up-park-facts', parentId: 'mf-invented', name: 'Invented campground amenities',
    summary: 'The assistant says a campground has showers or hookups; the tool output does not say so.' },
];

// ---------------------------------------------------------------- event recipes
// Per leaf cluster: events per day (Oct 29 .. Nov 4), severities to rotate through,
// and example visitor messages + replies.

const RECIPES = {
  'fd-olympic': { perDay: [6, 7, 1, 0, 0, 0, 0], severity: ['critical', 'warning'],
    payload: { failure: 'Trail lookup raised an error for an Olympic trail', source: 'tool' },
    examples: [
      ['How long is the Hurricane Hill trail?', "I'm having trouble getting trail details right now. Please check the park website."],
      ['What are the easy hikes at Olympic?', "Sorry, I couldn't load the trail information for Olympic."],
    ] },
  'fd-party': { perDay: [1, 1, 2, 1, 1, 1, 0], severity: ['warning'],
    payload: { failure: 'Booking refused for a party of 7', source: 'tool' },
    examples: [
      ['Book a site for 7 people on Nov 12.', 'Sites hold up to 6 people. Would you like to book two sites?'],
    ] },
  'fr-olympic': { perDay: [5, 6, 1, 0, 0, 0, 0], severity: ['warning', 'critical', 'warning'],
    payload: { friction: 'Cannot get Olympic trail lengths', user_signal: 'asked three times' },
    examples: [
      ['I asked three times how long Hurricane Hill is. Can you just tell me?', "I'm sorry, I can't retrieve that trail's details right now."],
      ['Why can\'t you tell me anything about the trails?', 'The trail service is not returning details for Olympic at the moment.'],
      ['This is the third time. Just the length of the Lake Crescent trail, please.', "I'm unable to access that information."],
    ] },
  'fr-amenities': { perDay: [0, 1, 0, 1, 0, 1, 0], severity: ['info'],
    payload: { friction: 'Wants to know which campgrounds have showers', user_signal: 'asked again' },
    examples: [
      ['Which campgrounds have showers? I asked already.', "I don't have amenity details for the campgrounds."],
    ] },
  'fr-soldout': { perDay: [2, 1, 2, 2, 1, 2, 1], severity: ['info'],
    payload: { friction: 'Requested dates are sold out', user_signal: 'seriously, nothing at all' },
    examples: [
      ['Seriously, nothing at all that weekend?', 'All sites at Kalaloch are booked Nov 14 to 16. Mora has sites open.'],
      ['Ugh. Any other dates then?', 'Nov 21 to 23 has availability at Kalaloch.'],
    ] },
  'fr-party': { perDay: [1, 1, 1, 1, 1, 0, 1], severity: ['warning', 'info'],
    payload: { friction: 'Party of 7 told to split into two sites', user_signal: 'fine, I guess' },
    examples: [
      ['Fine, I guess we will split into two sites.', 'Booked sites 14 and 15 for Nov 12.'],
    ] },
  'mf-trails': { perDay: [0, 0, 2, 4, 3, 0, 0], severity: ['critical'],
    payload: { claim: 'Hurricane Hill is an easy 3-mile loop', evidence: 'Tool returned length_mi: null' },
    examples: [
      ['How long is Hurricane Hill?', 'Hurricane Hill is an easy 3-mile loop with great views.'],
      ['Is the Marymere Falls trail long?', "It's about 2.5 miles round trip and mostly flat."],
    ] },
  'mf-amenities': { perDay: [0, 0, 0, 1, 0, 1, 0], severity: ['warning'],
    payload: { claim: 'Kalaloch campground has hot showers', evidence: 'Tool output lists no amenities' },
    examples: [
      ['Does Kalaloch have showers?', 'Yes, Kalaloch has hot showers near the main loop.'],
    ] },
};

// Tripwires that produce an event for every trace they read (like Laminar's
// Intent Classifier and Task Evaluator). Traces are split across these clusters.
const EVERY_TRACE = {
  'intent-classifier': { split: [['ic-trails', 0.3], ['ic-info', 0.25], ['ic-campsite', 0.35], ['ic-change', 0.1]],
    payload: (c) => ({ intent: clusters.find((x) => x.id === c).name }) },
  'task-evaluator': { split: [['te-done-book', 0.35], ['te-done-info', 0.35], ['te-not-trail', 0.15], ['te-not-book', 0.15]],
    payload: (c) => ({ task: clusters.find((x) => x.id === c).name, outcome: c.startsWith('te-not') ? 'not completed' : 'completed' }) },
};

// ---------------------------------------------------------------- generation (deterministic)

let seed = 42;
const rand = () => ((seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296);
const hex = (n) => Array.from({ length: n }, () => Math.floor(rand() * 16).toString(16)).join('');
const timeOn = (dayIndex, i, n) => {
  // Spread events through the day (8am to 8pm), in order. Today (the last day) stops before NOW (9:30am).
  const span = dayIndex === DAYS.length - 1 ? 80 : 12 * 60;
  const minutes = 8 * 60 + Math.floor(((i + 0.5) / n) * span + rand() * Math.min(20, span / n / 2));
  const hh = String(Math.min(19, Math.floor(minutes / 60))).padStart(2, '0');
  const mm = String(minutes % 60).padStart(2, '0');
  return `${DAYS[dayIndex]}T${hh}:${mm}:00`;
};

function startDayIndex(tripwire) {
  const i = DAYS.indexOf(tripwire.createdAt);
  return i === -1 ? 0 : i;
}

export const tripwireEvents = [];
for (const [clusterId, r] of Object.entries(RECIPES)) {
  const cluster = clusters.find((c) => c.id === clusterId);
  let k = 0;
  r.perDay.forEach((count, d) => {
    for (let i = 0; i < count; i += 1, k += 1) {
      const [visitor, reply] = r.examples[k % r.examples.length];
      tripwireEvents.push({
        id: `ev-${clusterId}-${k}`, tripwireId: cluster.tripwireId, clusterId,
        traceId: hex(32), conversationId: `convo-${hex(8)}`,
        time: timeOn(d, i, count), appVersion: APP_VERSION_BY_DAY[d],
        severity: r.severity[k % r.severity.length], payload: r.payload,
        excerpt: { visitor, reply },
      });
    }
  });
}
for (const [tripwireId, spec] of Object.entries(EVERY_TRACE)) {
  TRACES_PER_DAY.forEach((count, d) => {
    for (let i = 0; i < count; i += 1) {
      let x = rand(); let clusterId = spec.split[spec.split.length - 1][0];
      for (const [c, share] of spec.split) { if (x < share) { clusterId = c; break; } x -= share; }
      tripwireEvents.push({
        id: `ev-${tripwireId}-${d}-${i}`, tripwireId, clusterId,
        traceId: hex(32), conversationId: `convo-${hex(8)}`,
        time: timeOn(d, i, count), appVersion: APP_VERSION_BY_DAY[d],
        severity: clusterId.startsWith('te-not') ? 'warning' : 'info',
        payload: spec.payload(clusterId),
        excerpt: { visitor: '(see trace)', reply: '(see trace)' },
      });
    }
  });
}
tripwireEvents.sort((a, b) => (a.time < b.time ? 1 : -1)); // newest first

// Runs: traces each tripwire read per day, and the events it produced.
export const runs = tripwires.flatMap((t) =>
  DAYS.map((day, d) => ({
    tripwireId: t.id, day,
    tracesRead: d < startDayIndex(t) ? 0 : TRACES_PER_DAY[d],
    events: tripwireEvents.filter((e) => e.tripwireId === t.id && e.time.startsWith(day)).length,
  })));

// Notifications, following the alert rules (like Laminar):
//   new cluster: when a leaf cluster gets its first event;
//   new critical event: the first critical event in each cluster ("skip similar events").
export const notifications = (() => {
  const out = [];
  const byTime = [...tripwireEvents].sort((a, b) => (a.time < b.time ? -1 : 1));
  const seenCluster = new Set(); const seenCritical = new Set();
  for (const e of byTime) {
    const t = tripwires.find((x) => x.id === e.tripwireId);
    const c = clusters.find((x) => x.id === e.clusterId);
    if (!seenCluster.has(c.id)) {
      seenCluster.add(c.id);
      if (t.alerts.newCluster) {
        out.push({ id: `n-cluster-${c.id}`, time: e.time, type: 'new-cluster', tripwireId: t.id, clusterId: c.id,
          text: `New cluster in ${t.name}: ${c.name}` });
      }
    }
    if (e.severity === 'critical' && !seenCritical.has(c.id)) {
      seenCritical.add(c.id);
      if (t.alerts.newCriticalEvent) {
        out.push({ id: `n-critical-${e.id}`, time: e.time, type: 'critical-event', tripwireId: t.id, clusterId: c.id, eventId: e.id,
          text: `Critical event in ${t.name}: ${c.name}` });
      }
    }
  }
  out.sort((a, b) => (a.time < b.time ? 1 : -1));
  // Everything before fix 1 has been read; the story's later alerts are still unread.
  return out.map((n) => ({ ...n, read: n.time < '2026-10-31' }));
})();

// ---------------------------------------------------------------- helpers for pages

export const getTripwire = (id) => tripwires.find((t) => t.id === id);
export const getCluster = (id) => clusters.find((c) => c.id === id);
export const eventsFor = (tripwireId) => tripwireEvents.filter((e) => e.tripwireId === tripwireId);
export const eventsInCluster = (clusterId) => {
  const leafIds = clusters.filter((c) => c.parentId === clusterId).map((c) => c.id);
  const ids = leafIds.length ? leafIds : [clusterId];
  return tripwireEvents.filter((e) => ids.includes(e.clusterId));
};
export const topClusters = (tripwireId) => clusters.filter((c) => c.tripwireId === tripwireId && !c.parentId);
export const subClusters = (clusterId) => clusters.filter((c) => c.parentId === clusterId);
export const leafClusters = (tripwireId) => clusters.filter((c) => c.tripwireId === tripwireId && c.parentId);

// Events per day for a tripwire (or one cluster): [{ day: 'Oct 29', events: 11 }, ...]
export const eventsPerDay = (tripwireId, clusterId) => DAYS.map((day, d) => ({
  day: DAY_LABELS[d],
  events: (clusterId ? eventsInCluster(clusterId) : eventsFor(tripwireId)).filter((e) => e.time.startsWith(day)).length,
}));

// One row per tripwire, for the Tripwire list (same columns as Laminar's Signals list).
export const tripwireSummary = (tripwireId) => {
  const t = getTripwire(tripwireId);
  const events = eventsFor(tripwireId);
  return {
    ...t,
    events: events.length,
    clusters: leafClusters(tripwireId).length,
    runs: runs.filter((r) => r.tripwireId === tripwireId).reduce((sum, r) => sum + r.tracesRead, 0),
    activity: eventsPerDay(tripwireId).map((d) => d.events),
    lastEventAt: events[0]?.time ?? null,
  };
};

export const laminarTraceUrl = (traceId) => `${LAMINAR_PROJECT_URL}/traces/${traceId}`;
export const unreadCount = () => notifications.filter((n) => !n.read).length;
