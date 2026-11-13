import { browserZone } from "../components/format";

// Raw calls to the Tripwire API. Pages don't use this file directly: they use src/api/index.js.

export class ApiError extends Error {
  constructor(status, detail) {
    super(Array.isArray(detail) ? detail.join("; ") : String(detail));
    this.status = status; // 0 when the API couldn't be reached at all
    this.detail = detail; // a list of problems for a 422, otherwise a sentence
  }
}

async function request(method, path, body) {
  let res;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Can't reach the API. Is the backend running?");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? res.statusText);
  return data;
}

// Pages use the mock's time format: UTC without a zone ("2026-11-11T09:30:00"). The API wants a zone.
const utc = (time) => (/Z|[+-]\d\d:\d\d$/.test(time) ? time : `${time}Z`);

// A query string from a range and extra parameters; empty values are left out.
function query(range, extra = {}) {
  const params = new URLSearchParams();
  if (range?.start) params.set("since", utc(range.start));
  if (range?.end) params.set("until", utc(range.end));
  for (const [key, value] of Object.entries(extra)) {
    if (value !== null && value !== undefined && value !== "") params.set(key, value);
  }
  return params.toString();
}

export const listSignals = (range) => request("GET", `/signals?${query(range)}`);
export const getSignal = (id) => request("GET", `/signals/${id}`);
// filters: { severity, cluster, q, offset, limit }. Returns { total, events }.
export const listEvents = (id, range, filters) =>
  request("GET", `/signals/${id}/events?${query(range, filters)}`);
// filters: { severity, cluster, q }. Returns [{ bucket, cluster_id, events }].
export const countEvents = (id, range, bucketMinutes, filters) =>
  request("GET", `/signals/${id}/event-counts?${query(range, { ...filters, bucket_minutes: bucketMinutes, tz: browserZone() })}`);
// filters: { status, source, outcome, q, offset, limit }. Returns { total, runs }.
export const listRuns = (id, range, filters) =>
  request("GET", `/signals/${id}/runs?${query(range, filters)}`);
export const countRuns = (id, range, bucketMinutes, filters) =>
  request("GET", `/signals/${id}/run-counts?${query(range, { ...filters, bucket_minutes: bucketMinutes, tz: browserZone() })}`);
export const listClusters = (id, range) => request("GET", `/signals/${id}/clusters?${query(range)}`);
export const getOverview = (range, bucketMinutes) =>
  request("GET", `/overview?${query(range, { bucket_minutes: bucketMinutes, tz: browserZone() })}`);
export const createSignal = (body) => request("POST", "/signals", body);
export const editSignal = (id, body) => request("PUT", `/signals/${id}`, body);
export const setActive = (id, active) =>
  request("PATCH", `/signals/${id}`, { active });
export const testRun = (laminarTraceId, definition) =>
  request("POST", "/test-run", {
    laminar_trace_id: laminarTraceId,
    definition,
  });
