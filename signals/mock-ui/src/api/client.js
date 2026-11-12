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

// The time pickers give local times without a zone ("2026-11-11T09:30:00"); the API wants UTC.
const utc = (time) => new Date(time).toISOString();

function rangeQuery(range) {
  const params = new URLSearchParams();
  if (range?.start) params.set("since", utc(range.start));
  if (range?.end) params.set("until", utc(range.end));
  return params.toString();
}

export const listSignals = (range) =>
  request("GET", `/signals?${rangeQuery(range)}`);
export const getSignal = (id) => request("GET", `/signals/${id}`);
export const listEvents = (id, range, limit = 500) =>
  request("GET", `/signals/${id}/events?${rangeQuery(range)}&limit=${limit}`);
export const listRuns = (id, range, limit = 500) =>
  request("GET", `/signals/${id}/runs?${rangeQuery(range)}&limit=${limit}`);
export const createSignal = (body) => request("POST", "/signals", body);
export const editSignal = (id, body) => request("PUT", `/signals/${id}`, body);
export const setActive = (id, active) =>
  request("PATCH", `/signals/${id}`, { active });
export const testRun = (laminarTraceId, definition) =>
  request("POST", "/test-run", {
    laminar_trace_id: laminarTraceId,
    definition,
  });
