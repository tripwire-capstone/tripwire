import { useEffect, useState } from "react";
import * as client from "./client";
import {
  clustersFromApi,
  eventCountsFromApi,
  eventPageFromApi,
  overviewFromApi,
  runCountsFromApi,
  runFiltersToApi,
  runPageFromApi,
  signalFromApi,
  summaryFromApi,
} from "./adapt";

// Loads data when the page opens or a dependency changes. Returns { data, error, loading, reload }.
function useApi(load, deps) {
  const [state, setState] = useState({
    data: null,
    error: null,
    loading: true,
  });
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let current = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    load().then(
      (data) => current && setState({ data, error: null, loading: false }),
      (error) => current && setState({ data: null, error, loading: false }),
    );
    // An answer that arrives after the page moved on (another signal, another range) is ignored.
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, version]);
  return { ...state, reload: () => setVersion((v) => v + 1) };
}

// `range` is { start, end } as rangeFromParams() returns it. Keep it stable between renders,
// or the page reloads forever.
export const useSignals = (range) =>
  useApi(
    () => client.listSignals(range).then((rows) => rows.map(summaryFromApi)),
    [range?.start, range?.end],
  );

export const useSignal = (id) =>
  useApi(() => client.getSignal(id).then(signalFromApi), [id]);

// `filters` is a plain object of strings and numbers; its values are the dependencies.
const valuesOf = (filters) => Object.values(filters ?? {});

// { total, events }: one page of events matching `filters` ({ severity, cluster, q, offset, limit }).
export const useEvents = (id, range, filters) =>
  useApi(
    () => client.listEvents(id, range, filters).then((page) => eventPageFromApi(page, id)),
    [id, range?.start, range?.end, ...valuesOf(filters)],
  );

// Events per bucket and cluster, with the same filters as useEvents (no paging).
export const useEventCounts = (id, range, bucketMinutes, filters) =>
  useApi(
    () => client.countEvents(id, range, bucketMinutes, filters).then(eventCountsFromApi),
    [id, range?.start, range?.end, bucketMinutes, ...valuesOf(filters)],
  );

// { total, runs }: one page of runs. Filters use the page's words (status "completed", source "trigger").
export const useRuns = (id, range, filters) =>
  useApi(
    () => client.listRuns(id, range, runFiltersToApi(filters ?? {})).then((page) => runPageFromApi(page, id)),
    [id, range?.start, range?.end, ...valuesOf(filters)],
  );

// Finished runs per bucket: { bucket, event, noEvent, failed }.
export const useRunCounts = (id, range, bucketMinutes, filters) =>
  useApi(
    () => client.countRuns(id, range, bucketMinutes, runFiltersToApi(filters ?? {})).then(runCountsFromApi),
    [id, range?.start, range?.end, bucketMinutes, ...valuesOf(filters)],
  );

// { events, clusteredEvents, clusters: [{ id, name, count, problemCount, ... }] } in the range.
export const useClusters = (id, range) =>
  useApi(() => client.listClusters(id, range).then(clustersFromApi), [id, range?.start, range?.end]);

// Everything Home shows.
export const useOverview = (range, bucketMinutes) =>
  useApi(
    () => client.getOverview(range, bucketMinutes).then(overviewFromApi),
    [range?.start, range?.end, bucketMinutes],
  );
