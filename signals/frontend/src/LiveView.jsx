import { useEffect, useState } from "react";
import { ConversationDetails } from "./App.jsx";
import "./live.css";

// Live turns arrive while the page is open, so it reloads on its own.
const REFRESH_MS = 10_000;

function validateLive(data) {
  if (
    !data ||
    data.signal !== "user_frustration" ||
    !Array.isArray(data.turns) ||
    !Number.isInteger(data.pending_turns) ||
    data.turns.some(
      (turn) =>
        !Number.isInteger(turn.turn_id) ||
        typeof turn.probability !== "number" ||
        !Number.isFinite(turn.probability) ||
        turn.probability < 0 ||
        turn.probability > 1,
    )
  ) {
    throw new Error("The API returned an invalid live results list.");
  }
  return data;
}

function formatTime(iso) {
  const date = new Date(iso);
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function LiveRow({ turn, matched, expanded, onToggle }) {
  const contextId = `live-context-${turn.turn_id}`;
  return (
    <>
      <tr className={`case-row${matched ? " live-match" : ""}`}>
        <td className="message-cell" data-label="Latest user message">
          <p className="message-text">{turn.user_message || "(No message text)"}</p>
          <div className="message-meta">
            <span>
              {turn.conversation_id} · turn {turn.target_index} ·{" "}
              {formatTime(turn.turn_ended_at)}
            </span>
            <button
              className="context-toggle"
              type="button"
              onClick={onToggle}
              aria-expanded={expanded}
              aria-controls={contextId}
            >
              {expanded ? "Hide context" : "View context"}
            </button>
            {turn.laminar_url && (
              <a
                className="context-toggle"
                href={turn.laminar_url}
                target="_blank"
                rel="noreferrer"
              >
                Open in Laminar
              </a>
            )}
          </div>
        </td>
        <td data-label="Probability">
          <span className="probability-value">
            {Math.round(turn.probability * 100)}%
          </span>
          <div className="probability-track" aria-hidden="true">
            <span style={{ width: `${turn.probability * 100}%` }} />
          </div>
        </td>
        <td data-label="Match">
          <span className={`outcome${matched ? " outcome-match" : ""}`}>
            {matched ? "Frustrated" : "No match"}
          </span>
        </td>
      </tr>
      {expanded && (
        <tr className="context-row" id={contextId}>
          <td colSpan="3">
            <ConversationDetails conversation={turn.conversation} />
          </td>
        </tr>
      )}
    </>
  );
}

export default function LiveView({ hidden }) {
  const [data, setData] = useState(null);
  const [threshold, setThreshold] = useState(0.5);
  // By turn id, so a refresh that adds or reorders rows keeps the same one open.
  const [expandedTurn, setExpandedTurn] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadedAt, setLoadedAt] = useState(null);
  const [refreshCount, setRefreshCount] = useState(0);

  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      try {
        const response = await fetch("/api/live", {
          signal: controller.signal,
          cache: "no-store",
        });
        const body = await response.json().catch(() => null);
        if (!response.ok) {
          throw new Error(
            typeof body?.detail === "string"
              ? body.detail
              : "Cannot load live results. Start the Python API and check its connection to Postgres.",
          );
        }
        const next = validateLive(body);
        if (!controller.signal.aborted) {
          setData(next);
          setError("");
          setLoadedAt(new Date());
        }
      } catch (caught) {
        // Keep showing the last good list; just say the refresh failed.
        if (!controller.signal.aborted) {
          setError(caught instanceof Error ? caught.message : "Could not load live results.");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [refreshCount]);

  const turns = data?.turns ?? [];
  const matches = turns.filter((turn) => turn.probability >= threshold).length;

  return (
    <main className="page-content" hidden={hidden}>
      <section className="page-heading">
        <div>
          <p className="breadcrumb">
            Signals <span>/</span> Live
          </p>
          <h1>User frustration in live traffic</h1>
          <p className="page-description">
            Turns synced from Laminar and scored by the classifier. Updates every{" "}
            {REFRESH_MS / 1000} seconds.
          </p>
        </div>
        <div className="split-badge">
          {data ? `Question version ${data.question_version}` : "Live"}
        </div>
      </section>

      <div className="data-toolbar">
        <span className="live-status">
          {loadedAt ? `Last updated ${loadedAt.toLocaleTimeString()}` : "Loading…"}
        </span>
        <button
          className="secondary-button"
          type="button"
          onClick={() => setRefreshCount((count) => count + 1)}
        >
          Refresh
        </button>
      </div>

      {error && (
        <p className="error-message" role="alert">
          {error}
        </p>
      )}

      {data?.pending_turns > 0 && (
        <p className="results-notice" role="status">
          {data.pending_turns} live {data.pending_turns === 1 ? "turn is" : "turns are"} not
          scored yet. If this number doesn't go down, start the classifier:{" "}
          <code>python -m classifier.live</code>
        </p>
      )}

      <section className="threshold-panel" aria-label="Classifier threshold">
        <div className="threshold-control">
          <div className="threshold-heading">
            <label htmlFor="live-threshold">Frustration threshold</label>
            <output htmlFor="live-threshold">{threshold.toFixed(2)}</output>
          </div>
          <input
            id="live-threshold"
            type="range"
            min="0"
            max="1"
            step="0.01"
            value={threshold}
            onChange={(event) => setThreshold(Number(event.target.value))}
          />
          <div className="range-labels">
            <span>Flag more turns</span>
            <span>Flag fewer turns</span>
          </div>
        </div>
        <p className="threshold-explanation">
          <strong>{matches}</strong> of {turns.length} scored{" "}
          {turns.length === 1 ? "turn matches" : "turns match"} at{" "}
          <strong>{threshold.toFixed(2)}</strong>.
          <span>
            Provisional threshold until it is set from the dev split. Moving it never
            calls the classifier.
          </span>
        </p>
      </section>

      <section className="results-section" aria-labelledby="live-heading">
        <div className="results-toolbar">
          <div>
            <h2 id="live-heading">Scored turns</h2>
            <p>Highest probability first</p>
          </div>
        </div>
        <div className="table-scroll">
          <table className="live-table">
            <thead>
              <tr>
                <th scope="col">Latest user message</th>
                <th scope="col">Probability</th>
                <th scope="col">Match</th>
              </tr>
            </thead>
            <tbody>
              {turns.map((turn) => (
                <LiveRow
                  key={turn.turn_id}
                  turn={turn}
                  matched={turn.probability >= threshold}
                  expanded={expandedTurn === turn.turn_id}
                  onToggle={() =>
                    setExpandedTurn(expandedTurn === turn.turn_id ? null : turn.turn_id)
                  }
                />
              ))}
              {!turns.length && (
                <tr>
                  <td className="empty-table" colSpan="3">
                    {loading
                      ? "Loading live results…"
                      : "No scored live turns yet. Run the sync and the classifier, then wait for the next update."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
