import { useEffect, useState } from "react";
import * as client from "./client";
import {
  eventFromApi,
  runFromApi,
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

export const useEvents = (id, range) =>
  useApi(
    () =>
      client
        .listEvents(id, range)
        .then((rows) => rows.map((e) => eventFromApi(e, id))),
    [id, range?.start, range?.end],
  );

export const useRuns = (id, range) =>
  useApi(
    () =>
      client
        .listRuns(id, range)
        .then((rows) => rows.map((r) => runFromApi(r, id))),
    [id, range?.start, range?.end],
  );
