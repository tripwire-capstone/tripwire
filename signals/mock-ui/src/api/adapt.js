// Turns API responses into the shapes the pages were built on, and form values into API bodies.
// When the API changes, this is the one file to update.

// API times ("2026-11-11T22:30:43.992398Z") -> the pages' format: UTC without a zone
// ("2026-11-11T22:30:43"), which formatTime, rangeFromParams and the charts expect.
const pageTime = (iso) => (iso ? iso.slice(0, 19) : null);

const kindOf = (template) => (template ? "preset" : "custom");
const statusOf = (active) => (active ? "active" : "paused");

// One row of the Signals list.
export function summaryFromApi(s) {
  return {
    id: s.id,
    name: s.name,
    kind: kindOf(s.template),
    templateId: s.template,
    status: statusOf(s.active),
    createdAt: pageTime(s.created_at),
    events: s.events,
    problemEvents: s.problem_events,
    runs: s.runs,
    lastEventAt: pageTime(s.last_event_at),
    clusters: null, // no clusters endpoint yet (FEATURES.clusterCounts)
    activity: [], // no per-day counts yet (FEATURES.activityColumn)
  };
}

// A signal with its current version, as the signal page and the edit form use it.
export function signalFromApi(s) {
  return {
    id: s.id,
    name: s.name,
    kind: kindOf(s.template),
    templateId: s.template,
    status: statusOf(s.active),
    createdAt: pageTime(s.created_at),
    instruction: s.instruction,
    criticalGuidance: s.critical_guidance,
    model: s.model,
    versionNumber: s.version_number,
    payloadFields: s.output_schema.map((f) => ({
      name: f.name,
      type: f.type,
      description: f.description,
      choices: f.choices ?? [],
      nullable: f.nullable,
    })),
    filters: s.filters.map((f) => ({
      field: f.field,
      operator: f.op,
      value: f.value,
    })),
    trigger: "trace",
    spanName: "",
    sampling: 100,
  };
}

export function eventFromApi(e, signalId) {
  return {
    id: e.id,
    tripwireId: signalId,
    runId: e.run_id,
    findingIndex: e.finding_index,
    severity: e.severity,
    payload: e.payload,
    evidenceLabels: e.evidence_labels,
    traceId: e.laminar_trace_id,
    laminarUrl: e.laminar_url,
    conversationId: e.session_id,
    time: pageTime(e.trace_ended_at), // when the turn happened
    createdAt: pageTime(e.created_at), // when the judge found it
    versionNumber: e.version_number,
    source: e.source,
    clusterId: e.cluster_id,
    appVersion: null, // not stored yet (FEATURES.appVersions)
    excerpt: null, // not in the API; the trace is one click away in Laminar
  };
}

const RUN_STATUS = {
  done: "completed",
  failed: "failed",
  pending: "pending",
  running: "running",
};

export function runFromApi(r, signalId) {
  return {
    id: r.id,
    tripwireId: signalId,
    status: RUN_STATUS[r.status] ?? r.status,
    source: r.source === "live" ? "trigger" : "backfill",
    events: r.events,
    eventId: null, // a run can have several events; the Runs tab shows the count
    attempts: r.attempts,
    model: r.model,
    cost: r.cost_usd,
    error: r.last_error,
    traceId: r.laminar_trace_id,
    laminarUrl: r.laminar_url,
    time: pageTime(r.finished_at ?? r.created_at),
    versionNumber: r.version_number,
    clusterId: null,
  };
}

// The form's saved values (TripwireForm's onSubmit) -> the API's signal definition.
export function definitionToApi(values) {
  return {
    name: values.name,
    instruction: values.instruction,
    output_schema: values.payloadFields.map((f) => ({
      name: f.name,
      type: f.type,
      description: f.description,
      ...(f.type === "enum" ? { choices: f.choices } : {}),
      ...(f.nullable !== undefined ? { nullable: f.nullable } : {}),
    })),
    filters: values.filters.map((f) => ({
      field: f.field,
      op: f.op,
      value: f.value,
    })),
  };
}

export const templateToApi = (templateId) =>
  templateId && templateId !== "blank" ? templateId : null;
