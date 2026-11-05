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
// Records follow the product's data model: traces (from Laminar), runs (one per trace a tripwire
// read: trigger or backfill, completed or failed, with cost), tripwire events (a run that found
// something; clusterId null = not in a cluster yet), clusters (a tree), app versions, notifications.
//
// Events, runs, cluster counts and notifications are all generated from the tables below,
// so they can't disagree.
//
// Pages don't import from this file directly. They call useStoryData() (src/components/StoryClock.jsx),
// which returns makeStoryData(asOf): the same helpers, showing only what had happened by the
// story day picked in the header. A page built that way follows the day picker automatically.

export const DAYS = ['2026-10-29', '2026-10-30', '2026-10-31', '2026-11-01', '2026-11-02', '2026-11-03', '2026-11-04'];
export const DAY_LABELS = ['Oct 29', 'Oct 30', 'Oct 31', 'Nov 1', 'Nov 2', 'Nov 3', 'Nov 4'];
export const NOW = '2026-11-04T09:30:00';

// The days the header's day picker offers. asOf = the moment the developer looks at Tripwire.
export const STORY_DAYS = [
  { id: 'day1', label: 'Day 1', asOf: '2026-10-29T18:00:00', note: 'Olympic trail lookup starts failing' },
  { id: 'day3', label: 'Day 3', asOf: '2026-10-31T18:00:00', note: 'Fix 1 shipped (v1.4.1); the assistant starts inventing trail lengths' },
  { id: 'day6', label: 'Day 6', asOf: '2026-11-03T18:00:00', note: 'Fix 2 shipped (v1.4.2); both problems quiet' },
  { id: 'today', label: 'Today', asOf: NOW, note: 'The whole week' },
];
export const DEFAULT_STORY_DAY = 'today';

// A notification counts as unread for 48 hours after it arrives (relative to asOf).
const UNREAD_FOR_HOURS = 48;

// App version in production on each day (from trace metadata).
const APP_VERSION_BY_DAY = ['v1.4.0', 'v1.4.0', 'v1.4.1', 'v1.4.1', 'v1.4.1', 'v1.4.2', 'v1.4.2'];

// Traces arriving per day (every tripwire reads these, unless it started later).
const TRACES_PER_DAY = [21, 23, 22, 20, 24, 22, 12];

export const LAMINAR_PROJECT_URL = 'http://localhost:5667/project/demo';

// ---------------------------------------------------------------- tripwires

// Creation presets mirror the approved Laminar template content. They are not live tripwires.
// Source: lmnr-ai/lmnr, frontend/components/signals/prompts.ts (Apache-2.0).
export const tripwireTemplates = [
  {
    "id": "failure",
    "name": "Failure Detector",
    "shortName": "Failure",
    "icon": "alert-circle",
    "description": "Spot errors, loops and wrong tool usage",
    "instruction": "Report every distinct error the agent made in this run that meets the bar below. One finding per distinct error. [IMPORTANT]: If nothing meets the bar, submit no findings. A clean run is a valid outcome, and an unsupported finding is worse than no finding.\n\nReportable:\n1. A wrong action or flawed reasoning step that affected the outcome or wasted substantial work.\n2. A violation of a stated instruction or precondition — from the system prompt, the user's request, or a tool's stated contract. Reportable even if the run continued past it and even if the agent later corrected it. An instruction here is an explicit requirement or prohibition; a description of a typical procedure, a suggestion, or general guidance ('keep changes minimal', 'test your work', 'you may use X') is not one, and departing from it is reportable only under rule 1 by its effect. A violation is a forbidden action the agent actually performed, or a required action or check it never performed; an attempt the environment rejected before it took effect, which the agent then corrected, is a slip under rule 5, not a violation. Formatting or wording deviations count when they are in the final answer or deliverable, or when the instruction states the format is required; the same deviation in intermediate reasoning or scratch output does not.\n3. Repeated near-identical failures (same tool, same arguments, same kind of error) that together consumed a substantial part of the run, even if the agent eventually recovered. Report these as ONE finding: state how many times it happened and cite the spans.\n4. A factual assurance made to the user that no tool result or provided context supports. Unsupported is enough; you do not need an in-trace contradiction for the agent's own claims. This includes an unqualified answer when the trace shows the agent saw conflicting sources, an alternative reading, or an unresolved check that would change it — the missing caveat is the error, whichever option the agent picked — and a claim about every item or the whole set when only a sample was checked.\n\nNot reportable:\n5. A single minor slip the agent corrected within a step or two. Rule 2 overrides this exemption: an instruction violation is reportable even when minor and corrected.\n6. Skipping a planned lookup or verification, when the evidence the agent did gather is sufficient to support its answer. Rule 2 overrides this too: if the skipped step was itself a stated instruction, report it. So does rule 4: if the agent told the user the check was done, or its answer asserts something only the skipped step could establish (completeness of a search, that every item was checked), report it.\n\nGrounding:\n7. Cite the specific spans and quote the decisive evidence for every finding.\n8. The agent's own reasoning is always in scope: flag a conclusion the trace's evidence doesn't support. This requires no external knowledge, only reading the trace.\n9. External facts are different: never declare a result wrong from your own knowledge of the world unless the contradiction is visible in the trace itself. Never assert mechanical details the trace does not literally show — when judging \"substantial\" under rules 1 and 3, count the spans or tool calls you can actually see and cite them.\n10. Never infer environment rules or prerequisites that are not stated. This does not limit rule 4: the agent's assurances still need support.",
    "payloadFields": [
      {
        "name": "description",
        "description": "What went wrong, the decisive evidence with span references, and the impact on the run's outcome",
        "type": "string",
        "choices": []
      }
    ]
  },
  {
    "id": "logic",
    "name": "Logic Analyzer",
    "shortName": "Logic",
    "icon": "brain",
    "description": "Find flaws in reasoning and decision-making",
    "instruction": "Analyze this trace for flaws in the agent's reasoning or decision-making.\nLook for poor planning, contradictions, unnecessary steps, or missed opportunities.",
    "payloadFields": [
      {
        "name": "description",
        "description": "Description of the reasoning flaw or issue",
        "type": "string",
        "choices": []
      },
      {
        "name": "severity",
        "description": "Severity of the logic issue",
        "type": "enum",
        "choices": [
          "low",
          "medium",
          "high"
        ]
      }
    ]
  },
  {
    "id": "task",
    "name": "Task Evaluator",
    "shortName": "Task",
    "icon": "check-circle",
    "description": "Evaluate if the agent completed the user's request",
    "instruction": "Did the agent successfully complete what the user asked for?\nConsider whether the output matches the user's intent and is correct.",
    "payloadFields": [
      {
        "name": "success",
        "description": "Level of task completion",
        "type": "enum",
        "choices": [
          "full",
          "partial",
          "failed"
        ]
      },
      {
        "name": "description",
        "description": "Description of task completion status. If the task was not completed, describe what went wrong.",
        "type": "string",
        "choices": []
      }
    ]
  },
  {
    "id": "friction",
    "name": "Friction Detector",
    "shortName": "User friction",
    "icon": "frown",
    "description": "Identify user frustration and poor UX",
    "instruction": "Analyze this session for signs of user frustration or friction.\nLook for confusion, repeated attempts, or poor user experience.",
    "payloadFields": [
      {
        "name": "description",
        "description": "Description of the friction or frustration observed",
        "type": "string",
        "choices": []
      },
      {
        "name": "severity",
        "description": "Severity of the user friction",
        "type": "enum",
        "choices": [
          "low",
          "medium",
          "high"
        ]
      }
    ]
  },
  {
    "id": "hallucination",
    "name": "Hallucination Detector",
    "shortName": "Hallucination",
    "icon": "cloud-off",
    "description": "Detect made-up facts and incorrect claims",
    "instruction": "Did the agent make up facts, hallucinate information, or confidently\nstate something incorrect? Compare claims against available context.",
    "payloadFields": [
      {
        "name": "description",
        "description": "Description of the hallucination",
        "type": "string",
        "choices": []
      },
      {
        "name": "type",
        "description": "Type of hallucination detected",
        "type": "enum",
        "choices": [
          "factual",
          "contextual",
          "fabrication"
        ]
      }
    ]
  },
  {
    "id": "intent",
    "name": "Intent Classifier",
    "shortName": "Intent",
    "icon": "target",
    "description": "Classify what the user was trying to accomplish",
    "instruction": "What was the user trying to accomplish in this session?\nClassify the primary intent.",
    "payloadFields": [
      {
        "name": "intent",
        "description": "The user's primary intent",
        "type": "string",
        "choices": []
      },
      {
        "name": "complexity",
        "description": "Complexity of the user's request",
        "type": "enum",
        "choices": [
          "simple",
          "moderate",
          "complex"
        ]
      }
    ]
  }
];

// Scripted test results, not an evaluation of the editable instruction.
const tripwireSamples = [
  {
    id: 'sample-trail-error', name: 'Olympic trail lookup failed', time: '2026-10-29T12:00:00',
    visitor: 'I already asked twice: how long is the Olympic trail?',
    reply: 'I cannot find that information. Please try again later.',
    evidence: 'get_park_details returned an error: trail length missing.',
    results: {
      failure: { description: 'The trail lookup failed and the assistant offered no useful next step.' },
      logic: { description: 'The assistant repeated an unhelpful reply instead of recovering from the lookup failure.', severity: 'medium' },
      task: { success: 'failed', description: 'The visitor did not receive the requested trail length.' },
      friction: { description: 'The visitor repeated the same question after two unanswered attempts.', severity: 'high' },
      hallucination: null,
      intent: { intent: 'Find a trail length', complexity: 'simple' },
    },
  },
  {
    id: 'sample-made-up-distance', name: 'Unsupported Olympic trail length', time: '2026-10-31T12:00:00',
    visitor: 'How long is this Olympic trail?', reply: 'The trail is exactly 4.2 miles long.',
    evidence: 'get_park_details returned length: null; no source supplied a distance.',
    results: {
      failure: { description: 'The assistant stated a precise trail length unsupported by the tool output.' },
      logic: { description: 'A missing value was treated as evidence of an exact distance.', severity: 'high' },
      task: { success: 'failed', description: 'The visitor received an unsupported answer.' },
      friction: null,
      hallucination: { description: 'The assistant invented a distance of 4.2 miles.', type: 'fabrication' },
      intent: { intent: 'Find a trail length', complexity: 'simple' },
    },
  },
  {
    id: 'sample-booking-success', name: 'Successful campsite booking', time: '2026-11-03T12:00:00',
    visitor: 'Book a campsite for two people.', reply: 'Your campsite booking is confirmed.',
    evidence: 'book_campsite returned status: confirmed, party_size: 2.',
    results: {
      failure: null, logic: null, friction: null, hallucination: null,
      task: { success: 'full', description: 'The requested campsite was booked for two people.' },
      intent: { intent: 'Book a campsite', complexity: 'simple' },
    },
  },
];

// Tripwires use the same shape as the New tripwire form (and Laminar's Create Signal form):
//   templateId  the template it was created from (null = custom)
//   payloadFields [{ name, description, type: 'string'|'number'|'boolean'|'enum', choices: [] }]
//   trigger 'trace' (when a trace finishes) or 'span' (when the span named spanName finishes)
//   filters [{ field: 'total_token_count'|'status'|'span_names'|'tags', operator, value }]
//   sampling  percent of matching traces read (100 = all)
const fromTemplate = (templateId) => {
  const t = tripwireTemplates.find((x) => x.id === templateId);
  return { templateId, instruction: t.instruction, payloadFields: t.payloadFields };
};

export const tripwires = [
  {
    id: 'failure-detector', name: 'Failure Detector', kind: 'preset', status: 'active', createdAt: '2026-10-21T10:00:00',
    ...fromTemplate('failure'), trigger: 'trace', spanName: '', filters: [], sampling: 100,
    alerts: { newCluster: true, newCriticalEvent: true, skipSimilar: true, slack: true },
  },
  {
    id: 'friction-detector', name: 'Friction Detector', kind: 'preset', status: 'active', createdAt: '2026-10-21T10:00:00',
    ...fromTemplate('friction'), trigger: 'trace', spanName: '', filters: [], sampling: 100,
    alerts: { newCluster: true, newCriticalEvent: true, skipSimilar: true, slack: true },
  },
  {
    id: 'intent-classifier', name: 'Intent Classifier', kind: 'preset', status: 'active', createdAt: '2026-10-21T10:00:00',
    ...fromTemplate('intent'), trigger: 'trace', spanName: '', filters: [], sampling: 100,
    alerts: { newCluster: false, newCriticalEvent: false, skipSimilar: true, slack: false },
  },
  {
    id: 'task-evaluator', name: 'Task Evaluator', kind: 'preset', status: 'active', createdAt: '2026-10-21T10:00:00',
    ...fromTemplate('task'), trigger: 'trace', spanName: '', filters: [], sampling: 20, // sampled to keep its cost down
    alerts: { newCluster: false, newCriticalEvent: false, skipSimilar: true, slack: false },
  },
  {
    id: 'made-up-park-facts', name: 'Made-up park facts', kind: 'custom', status: 'active', createdAt: '2026-10-31T10:30:00',
    templateId: null,
    instruction: "Find replies that state park facts (trail lengths, hours, fees, amenities) that the tool outputs don't support.",
    payloadFields: [
      { name: 'claim', description: 'The fact the assistant stated', type: 'string', choices: [] },
      { name: 'evidence', description: 'What the tool output actually said', type: 'string', choices: [] },
    ],
    trigger: 'trace', spanName: '',
    filters: [{ field: 'span_names', operator: 'includes', value: 'get_park_details' }], sampling: 100,
    backfill: { pastDays: 14 }, // "Run on past traces" when it was created
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
// Per leaf cluster: events per day (Oct 29 .. Nov 4), severities to rotate through, and examples.
// Each example has the visitor's message, the assistant's reply, and the payload the tripwire
// recorded (its fields match the tripwire's payloadFields).

const RECIPES = {
  'fd-olympic': { perDay: [6, 7, 1, 0, 0, 0, 0], severity: ['critical', 'warning'], examples: [
    { visitor: 'How long is the Hurricane Hill trail?', reply: "I'm having trouble getting trail details right now. Please check the park website.",
      payload: { description: 'get_park_details raised KeyError for Hurricane Hill, so the assistant could not give the trail length the user asked for.' } },
    { visitor: 'What are the easy hikes at Olympic?', reply: "Sorry, I couldn't load the trail information for Olympic.",
      payload: { description: 'The trail lookup failed for every Olympic trail; the assistant gave up without recommending any hike.' } },
    { visitor: 'Is the Lake Crescent trail open?', reply: 'I was unable to retrieve trail conditions for Olympic at the moment.',
      payload: { description: 'Trail conditions request failed with a tool error; the user got no answer about Lake Crescent.' } },
  ] },
  'fd-party': { perDay: [1, 1, 2, 1, 1, 1, 0], severity: ['warning'], examples: [
    { visitor: 'Book a site for 7 people on Nov 12.', reply: 'Sites hold up to 6 people. Would you like to book two sites?',
      payload: { description: 'book_campsite refused a party of 7, though the real site limit is 8.' } },
    { visitor: 'We are 8 people, can we get one site at Kalaloch?', reply: 'Unfortunately the limit is 6 per site.',
      payload: { description: 'A party of 8 was told the limit is 6 per site and had to split the booking.' } },
  ] },
  'fr-olympic': { perDay: [5, 6, 1, 0, 0, 0, 0], severity: ['warning', 'critical', 'warning'], examples: [
    { visitor: 'I asked three times how long Hurricane Hill is. Can you just tell me?', reply: "I'm sorry, I can't retrieve that trail's details right now.",
      payload: { description: 'Could not get the length of Hurricane Hill after asking three times.' } },
    { visitor: "Why can't you tell me anything about the trails?", reply: 'The trail service is not returning details for Olympic at the moment.',
      payload: { description: 'Every Olympic trail question got a vague answer.' } },
    { visitor: 'This is the third time. Just the length of the Lake Crescent trail, please.', reply: "I'm unable to access that information.",
      payload: { description: 'Repeated the same trail question; still no length for Lake Crescent.' } },
  ] },
  'fr-amenities': { perDay: [0, 1, 0, 1, 0, 1, 0], severity: ['info'], examples: [
    { visitor: 'Which campgrounds have showers? I asked already.', reply: "I don't have amenity details for the campgrounds.",
      payload: { description: 'Wanted to know which campgrounds have showers; the assistant had no amenity data.' } },
  ] },
  'fr-soldout': { perDay: [2, 1, 2, 2, 1, 2, 1], severity: ['info'], examples: [
    { visitor: 'Seriously, nothing at all that weekend?', reply: 'All sites at Kalaloch are booked Nov 14 to 16. Mora has sites open.',
      payload: { description: 'Requested weekend at Kalaloch is sold out.' } },
    { visitor: 'Ugh. Any other dates then?', reply: 'Nov 21 to 23 has availability at Kalaloch.',
      payload: { description: 'Had to change plans because the first choice of dates was full.' } },
  ] },
  'fr-party': { perDay: [1, 1, 1, 1, 1, 0, 1], severity: ['warning', 'info'], examples: [
    { visitor: 'Fine, I guess we will split into two sites.', reply: 'Booked sites 14 and 15 for Nov 12.',
      payload: { description: 'A party of 7 was told to split into two sites.' } },
  ] },
  'mf-trails': { perDay: [0, 0, 2, 4, 3, 0, 0], severity: ['critical'], examples: [
    { visitor: 'How long is Hurricane Hill?', reply: 'Hurricane Hill is an easy 3-mile loop with great views.',
      payload: { claim: 'Hurricane Hill is an easy 3-mile loop.', evidence: 'get_park_details returned length_mi: null for Hurricane Hill.' } },
    { visitor: 'Is the Marymere Falls trail long?', reply: "It's about 2.5 miles round trip and mostly flat.",
      payload: { claim: 'Marymere Falls is about 2.5 miles round trip.', evidence: 'The tool returned no length for Marymere Falls.' } },
    { visitor: 'How far is the Sol Duc Falls hike?', reply: 'Sol Duc Falls is a 1.6-mile out-and-back.',
      payload: { claim: 'Sol Duc Falls is a 1.6-mile out-and-back.', evidence: 'length_mi was null in the tool output.' } },
  ] },
  'mf-amenities': { perDay: [0, 0, 0, 1, 0, 1, 0], severity: ['warning'], examples: [
    { visitor: 'Does Kalaloch have showers?', reply: 'Yes, Kalaloch has hot showers near the main loop.',
      payload: { claim: 'Kalaloch campground has hot showers.', evidence: 'The campground record lists no amenities.' } },
  ] },
};

// Events that haven't joined a cluster (a cluster needs similar events from 2+ conversations).
const UNCLUSTERED = [
  { tripwireId: 'failure-detector', day: 1, severity: 'warning', visitor: 'Can you email me the confirmation?', reply: "I can't send emails, but your confirmation number is NPS-40213.",
    payload: { description: 'The user asked for an email confirmation, which the assistant cannot send.' } },
  { tripwireId: 'failure-detector', day: 4, severity: 'info', visitor: 'What time does the visitor center open?', reply: 'Visitor center hours are not available right now.',
    payload: { description: 'No hours data for the visitor center; the question went unanswered.' } },
  { tripwireId: 'friction-detector', day: 2, severity: 'info', visitor: "That's not what I asked. I wanted the Hoh campground.", reply: 'Sorry about that. Here is availability for Hoh campground.',
    payload: { description: 'The assistant answered about the wrong campground at first.' } },
  { tripwireId: 'friction-detector', day: 5, severity: 'info', visitor: 'Can you stop asking me to confirm everything?', reply: 'Understood. I will book it now.',
    payload: { description: 'Annoyed by repeated confirmation questions.' } },
  { tripwireId: 'made-up-park-facts', day: 3, severity: 'warning', visitor: 'Is there cell service at Mora?', reply: 'Yes, there is good cell coverage at Mora campground.',
    payload: { claim: 'Mora campground has good cell coverage.', evidence: 'No tool output mentions cell service.' } },
];

// Tripwires that produce an event for every trace they read (like Laminar's
// Intent Classifier and Task Evaluator). Traces are split across these clusters.
const EVERY_TRACE = {
  'intent-classifier': { split: [['ic-trails', 0.3], ['ic-info', 0.25], ['ic-campsite', 0.35], ['ic-change', 0.1]],
    payload: (c) => ({ intent: clusters.find((x) => x.id === c).name, complexity: c === 'ic-change' ? 'moderate' : 'simple' }) },
  'task-evaluator': { split: [['te-done-book', 0.35], ['te-done-info', 0.35], ['te-not-trail', 0.15], ['te-not-book', 0.15]],
    payload: (c) => ({ success: c.startsWith('te-not') ? 'failed' : 'full', description: clusters.find((x) => x.id === c).summary }) },
};

// ---------------------------------------------------------------- generation (deterministic)
// Everything below is computed once for the whole week. makeStoryData(asOf) then filters it.

let seed = 42;
const rand = () => ((seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296);
const hex = (n) => Array.from({ length: n }, () => Math.floor(rand() * 16).toString(16)).join('');
const pad = (n) => String(n).padStart(2, '0');

// Traces: one per assistant turn, spread through each day (8am to 8pm; today stops before NOW).
export const traces = [];
TRACES_PER_DAY.forEach((count, d) => {
  const span = d === DAYS.length - 1 ? 80 : 12 * 60;
  for (let i = 0; i < count; i += 1) {
    const minutes = 8 * 60 + Math.floor(((i + 0.5) / count) * span);
    traces.push({
      id: hex(32), conversationId: `convo-${hex(8)}`, dayIndex: d,
      time: `${DAYS[d]}T${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}:00`,
      appVersion: APP_VERSION_BY_DAY[d],
    });
  }
});

const addSeconds = (iso, seconds) => {
  const t = new Date(`${iso}Z`); t.setUTCSeconds(t.getUTCSeconds() + seconds);
  return t.toISOString().slice(0, 19);
};

// Which trace each tripwire event comes from. A tripwire produces at most one event per trace.
const usedTraces = {}; // tripwireId -> Set of trace ids
function pickTrace(tripwireId, dayIndex, position) {
  usedTraces[tripwireId] ??= new Set();
  const dayTraces = traces.filter((t) => t.dayIndex === dayIndex);
  const start = Math.min(dayTraces.length - 1, Math.floor(position * dayTraces.length));
  for (let step = 0; step < dayTraces.length; step += 1) {
    for (const idx of [start + step, start - step]) {
      const tr = dayTraces[idx];
      if (tr && !usedTraces[tripwireId].has(tr.id)) { usedTraces[tripwireId].add(tr.id); return tr; }
    }
  }
  throw new Error(`No free trace on day ${dayIndex} for ${tripwireId}`);
}

// Fill in output fields that follow the event's severity (Friction Detector records its own low/medium/high).
const PAYLOAD_SEVERITY = { critical: 'high', warning: 'medium', info: 'low' };
const completePayload = (tripwireId, payload, severity) => (tripwireId === 'friction-detector'
  ? { ...payload, severity: PAYLOAD_SEVERITY[severity] } : payload);

// Raw findings: (tripwire, trace) -> what the event will hold.
const findings = new Map();
const key = (tripwireId, traceId) => `${tripwireId}|${traceId}`;
for (const [clusterId, r] of Object.entries(RECIPES)) {
  const cluster = clusters.find((c) => c.id === clusterId);
  let k = 0;
  r.perDay.forEach((count, d) => {
    for (let i = 0; i < count; i += 1, k += 1) {
      const trace = pickTrace(cluster.tripwireId, d, (i + 0.5) / count);
      const ex = r.examples[k % r.examples.length];
      findings.set(key(cluster.tripwireId, trace.id), {
        clusterId, severity: r.severity[k % r.severity.length],
        payload: completePayload(cluster.tripwireId, ex.payload, r.severity[k % r.severity.length]),
        excerpt: { visitor: ex.visitor, reply: ex.reply },
      });
    }
  });
}
UNCLUSTERED.forEach((u, i) => {
  const trace = pickTrace(u.tripwireId, u.day, ((i * 0.37) % 1));
  findings.set(key(u.tripwireId, trace.id), {
    clusterId: null, severity: u.severity, payload: completePayload(u.tripwireId, u.payload, u.severity),
    excerpt: { visitor: u.visitor, reply: u.reply },
  });
});
for (const [tripwireId, spec] of Object.entries(EVERY_TRACE)) {
  traces.forEach((trace) => {
    let x = rand(); let clusterId = spec.split[spec.split.length - 1][0];
    for (const [c, share] of spec.split) { if (x < share) { clusterId = c; break; } x -= share; }
    findings.set(key(tripwireId, trace.id), {
      clusterId, severity: clusterId.startsWith('te-not') ? 'warning' : 'info',
      payload: spec.payload(clusterId), excerpt: null,
    });
  });
}

// Runs: one per trace a tripwire read. Live traces run as "trigger" ~20 s after the trace ends;
// traces from before a tripwire existed run as "backfill" right after it was created.
// Each run either produced an event or didn't; a few runs fail.
const ALL_RUNS = [];
const ALL_EVENTS = [];
const FAILED_RUNS = new Set(['failure-detector|30', 'friction-detector|33']); // `${tripwireId}|${trace index}`
tripwires.forEach((t) => {
  const backfillFrom = t.backfill ? addSeconds(t.createdAt, -t.backfill.pastDays * 86400) : t.createdAt;
  let backfillCount = 0;
  traces.forEach((trace, ti) => {
    if (trace.time < backfillFrom) return;
    // Sampling: the signal reads only `sampling`% of matching traces (picked by trace id, so it's stable).
    if (t.sampling < 100 && parseInt(trace.id.slice(0, 4), 16) % 100 >= t.sampling) return;
    const isBackfill = trace.time < t.createdAt;
    const time = isBackfill ? addSeconds(t.createdAt, 30 + 4 * (backfillCount += 1)) : addSeconds(trace.time, 15 + Math.floor(rand() * 20));
    const runId = hex(32);
    const failed = FAILED_RUNS.has(`${t.id}|${ti}`);
    const finding = failed ? null : findings.get(key(t.id, trace.id));
    const cost = Math.round((0.0005 + rand() * 0.0015 + (finding ? 0.0003 : 0)) * 1e6) / 1e6;
    let eventId = null;
    if (finding) {
      eventId = hex(32);
      ALL_EVENTS.push({
        id: eventId, runId, tripwireId: t.id, clusterId: finding.clusterId,
        traceId: trace.id, conversationId: trace.conversationId, time, appVersion: trace.appVersion,
        severity: finding.severity, payload: finding.payload,
        excerpt: finding.excerpt ?? { visitor: null, reply: null },
      });
    }
    ALL_RUNS.push({
      id: runId, tripwireId: t.id, traceId: trace.id, traceTime: trace.time, time,
      source: isBackfill ? 'backfill' : 'trigger', status: failed ? 'failed' : 'completed',
      eventId, clusterId: finding?.clusterId ?? null, cost,
    });
  });
});
ALL_EVENTS.sort((a, b) => (a.time < b.time ? 1 : -1)); // newest first
ALL_RUNS.sort((a, b) => (a.time < b.time ? 1 : -1));

// App versions (from trace metadata): when each version was first seen. Shown as markers on charts.
const ALL_VERSIONS = [];
for (const tr of [...traces].sort((a, b) => (a.time < b.time ? -1 : 1))) {
  if (!ALL_VERSIONS.some((v) => v.version === tr.appVersion)) ALL_VERSIONS.push({ version: tr.appVersion, firstSeenAt: tr.time });
}

// Notifications, following the alert rules (like Laminar):
//   new cluster: when a leaf cluster gets its first event;
//   new critical event: the first critical event in each cluster ("skip similar events").
const ALL_NOTIFICATIONS = (() => {
  const out = [];
  const byTime = [...ALL_EVENTS].filter((e) => e.clusterId).sort((a, b) => (a.time < b.time ? -1 : 1));
  const seenCluster = new Set(); const seenCritical = new Set();
  for (const e of byTime) {
    const t = tripwires.find((x) => x.id === e.tripwireId);
    const c = clusters.find((x) => x.id === e.clusterId);
    if (!seenCluster.has(c.id)) {
      seenCluster.add(c.id);
      if (t.alerts.newCluster) {
        out.push({ id: `n-cluster-${c.id}`, time: e.time, type: 'new-cluster', tripwireId: t.id, clusterId: c.id,
          eventId: e.id, text: `New cluster in ${t.name}: ${c.name}` });
      }
    }
    if (e.severity === 'critical' && !seenCritical.has(c.id)) {
      seenCritical.add(c.id);
      if (t.alerts.newCriticalEvent) {
        out.push({ id: `n-critical-${e.id}`, time: e.time, type: 'critical-event', tripwireId: t.id, clusterId: c.id,
          eventId: e.id, text: `Critical event in ${t.name}: ${c.name}` });
      }
    }
  }
  return out.sort((a, b) => (a.time < b.time ? 1 : -1)); // newest first
})();

// ---------------------------------------------------------------- the story at a moment

const hoursBetween = (a, b) => (new Date(b) - new Date(a)) / 3_600_000;

// Everything a page needs, as it looked at `asOf` (an ISO time). Pages get this from useStoryData().
// Lists are newest first. Times are ISO strings without a time zone ('2026-10-31T14:05:00').
export function makeStoryData(asOf = NOW) {
  const asOfDate = asOf.slice(0, 10);
  const lastDayIndex = DAYS.filter((d) => d <= asOfDate).length - 1;

  const events = ALL_EVENTS.filter((e) => e.time <= asOf);
  const runs = ALL_RUNS.filter((r) => r.time <= asOf);
  const liveTripwires = tripwires.filter((t) => t.createdAt <= asOf);
  const liveLeafIds = new Set(events.map((e) => e.clusterId).filter(Boolean));
  const liveClusters = clusters.filter((c) => (c.parentId
    ? liveLeafIds.has(c.id)
    : clusters.some((child) => child.parentId === c.id && liveLeafIds.has(child.id))));
  const versions = ALL_VERSIONS.filter((v) => v.firstSeenAt <= asOf);
  const notifications = ALL_NOTIFICATIONS.filter((n) => n.time <= asOf)
    .map((n) => ({ ...n, read: hoursBetween(n.time, asOf) > UNREAD_FOR_HOURS }));

  const within = (items, start, end) => items.filter((x) => (!start || x.time >= start) && (!end || x.time <= end));

  const getTripwire = (id) => liveTripwires.find((t) => t.id === id);
  const getCluster = (id) => liveClusters.find((c) => c.id === id);
  const getEvent = (id) => events.find((e) => e.id === id);
  const getRun = (id) => runs.find((r) => r.id === id);
  const eventsFor = (tripwireId) => events.filter((e) => e.tripwireId === tripwireId);
  const runsFor = (tripwireId) => runs.filter((r) => r.tripwireId === tripwireId);
  // Events / runs of one tripwire between two ISO times (either may be omitted).
  const eventsBetween = (tripwireId, start, end) => within(eventsFor(tripwireId), start, end);
  const runsBetween = (tripwireId, start, end) => within(runsFor(tripwireId), start, end);

  const subClusters = (clusterId) => liveClusters.filter((c) => c.parentId === clusterId);
  // The leaf cluster ids under a cluster (itself, if it is a leaf).
  const leafIdsOf = (clusterId) => {
    const kids = clusters.filter((c) => c.parentId === clusterId).map((c) => c.id);
    return kids.length ? kids : [clusterId];
  };
  const eventsInCluster = (clusterId) => {
    const ids = leafIdsOf(clusterId);
    return events.filter((e) => ids.includes(e.clusterId));
  };
  const topClusters = (tripwireId) => liveClusters.filter((c) => c.tripwireId === tripwireId && !c.parentId);
  const leafClusters = (tripwireId) => liveClusters.filter((c) => c.tripwireId === tripwireId && c.parentId);
  // The top-level cluster an event's leaf cluster sits under (null if unclustered).
  const topClusterOf = (leafId) => {
    const leaf = clusters.find((c) => c.id === leafId);
    return leaf ? (leaf.parentId ?? leaf.id) : null;
  };

  // Events per day, always all 7 days so the x-axis doesn't jump; days after asOf are null (no line).
  const eventsPerDay = (tripwireId, clusterId) => DAYS.map((day, d) => ({
    day: DAY_LABELS[d],
    events: d > lastDayIndex ? null
      : (clusterId ? eventsInCluster(clusterId) : eventsFor(tripwireId)).filter((e) => e.time.startsWith(day)).length,
  }));

  // One row per tripwire, for the Tripwire list (same columns as Laminar's Signals list).
  const tripwireSummary = (tripwireId, { days } = {}) => {
    const t = getTripwire(tripwireId);
    if (!t) return null;
    // Calendar days ending at the story clock, including its partially completed day.
    // Omitting days preserves the summaries used by existing pages.
    if (days !== undefined && (!Number.isInteger(days) || days < 1)) {
      throw new RangeError('days must be a positive integer');
    }
    const start = new Date(`${asOfDate}T00:00:00Z`);
    if (days !== undefined) start.setUTCDate(start.getUTCDate() - days + 1);
    const rangeStart = days === undefined ? null : start.toISOString().slice(0, 10);
    const own = eventsFor(tripwireId).filter((e) => !rangeStart || e.time.slice(0, 10) >= rangeStart);
    return {
      ...t,
      events: own.length,
      // Leaf clusters with events in the range (unclustered events don't count as a cluster).
      clusters: new Set(own.map((e) => e.clusterId).filter(Boolean)).size,
      runs: runsFor(tripwireId).filter((r) => !rangeStart || r.time.slice(0, 10) >= rangeStart).length,
      activity: eventsPerDay(tripwireId).filter((d, i) => d.events !== null
        && (!rangeStart || DAYS[i] >= rangeStart)).map((d) => d.events),
      lastEventAt: own[0]?.time ?? null,
      rangeStart,
    };
  };

  return {
    asOf, days: DAY_LABELS,
    tripwires: liveTripwires, clusters: liveClusters, tripwireEvents: events, runs, versions, notifications,
    tripwireTemplates, sampleTraces: tripwireSamples.filter((sample) => sample.time <= asOf),
    getTripwire, getCluster, getEvent, getRun, eventsFor, runsFor, eventsBetween, runsBetween,
    eventsInCluster, leafIdsOf, topClusters, subClusters, leafClusters, topClusterOf,
    eventsPerDay, tripwireSummary,
    unreadCount: () => notifications.filter((n) => !n.read).length,
  };
}

