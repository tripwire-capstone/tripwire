import { useEffect, useState } from "react";
import { ApiError } from "./client";

// The developer view's data (GET /api/dev/status). Kept in its own file so the developer view can be
// removed in one go (backend tripwire/api/dev.py lists every place). It repeats a few lines of
// client.js / hooks.js on purpose instead of editing them.

async function getDevStatus() {
  let res;
  try {
    res = await fetch("/api/dev/status");
  } catch {
    throw new ApiError(0, "Can't reach the API. Is the backend running?");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? res.statusText);
  return devStatusFromApi(data);
}

// Times stay as the API sends them (UTC with a zone); format.js shows them in the browser's zone.
// One block per section of GET /api/dev/status (backend tripwire/api/dev.py says how to add one).
function devStatusFromApi(s) {
  return {
    asOf: s.as_of,
    pipeline: {
      lastPolledAt: s.pipeline.last_polled_at,
      newestTraceEndedAt: s.pipeline.newest_trace_ended_at,
      stages: s.pipeline.stages.map((stage) => ({
        stage: stage.stage,
        counts: stage.counts,                     // [{ state, count }] in pipeline order
        oldestWaitingAt: stage.oldest_waiting_at, // null = nothing waiting
        doneRecently: stage.done_recently,        // got through this step in the last 10 minutes
        stuck: stage.stuck,                       // waited over 2 minutes and nothing got through (the API decides)
      })),
    },
    errors: {
      groups: s.errors.groups.map((e) => ({
        stage: e.stage,
        message: e.message,
        pendingOrRunning: e.pending_or_running,
        failed: e.failed,
        done: e.done,                // status done after this error: it recovered
        signals: e.signals,          // names of the signals it hit
        lastSeenAt: e.last_seen_at,  // null = only still-retrying runs (no error time recorded)
        nextRetryAt: e.next_retry_at,
      })),
      more: s.errors.more,
    },
    latestDecisions: s.latest_decisions.map((x) => ({
      runId: x.run_id,
      signalId: x.signal_id,
      signalName: x.signal_name,
      status: x.status,                           // 'done' | 'failed'
      finishedAt: x.finished_at,
      findings: x.findings,                       // severities in finding order; [] = found nothing
      droppedFindings: x.dropped_findings,        // removed because their evidence didn't check out
      analysis: x.analysis,                       // the judge's reasoning
      error: x.error,                             // failed runs only
    })),
    latestSummaries: s.latest_summaries.map((x) => ({
      eventId: x.event_id,
      signalId: x.signal_id,
      signalName: x.signal_name,
      finishedAt: x.finished_at,
      promptVersion: x.prompt_version,
      cause: x.cause,
      summary: x.summary,
      landed: x.landed,                           // 'cluster' | 'pool' | 'not_embedded'
      clusterId: x.cluster_id,
      clusterName: x.cluster_name,                // null for a cluster not named yet
    })),
  };
}

// { data, error, loading, reload }, like the hooks in hooks.js. Loads once; reload() to refresh.
export function useDevStatus() {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let current = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    getDevStatus().then(
      (data) => current && setState({ data, error: null, loading: false }),
      (error) => current && setState({ data: null, error, loading: false }),
    );
    return () => {
      current = false;
    };
  }, [version]);
  return { ...state, reload: () => setVersion((v) => v + 1) };
}
