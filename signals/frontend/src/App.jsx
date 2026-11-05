import { useEffect, useMemo, useState } from "react";
import LiveView from "./LiveView.jsx";

const FILTERS = [
  ["all", "All outcomes"],
  ["hit", "Hits"],
  ["miss", "Misses"],
  ["false_alarm", "False alarms"],
  ["correct_no", "Correct negatives"],
];

function outcomeFor(item, threshold) {
  const expected = item.expected === "yes";
  const predicted = item.probability >= threshold;
  if (expected && predicted) return "hit";
  if (expected) return "miss";
  if (predicted) return "false_alarm";
  return "correct_no";
}

function outcomeLabel(outcome) {
  return {
    hit: "Hit",
    miss: "Miss",
    false_alarm: "False alarm",
    correct_no: "Correct no",
  }[outcome];
}

function validateReport(report) {
  if (
    !report ||
    report.signal !== "user_frustration" ||
    !["dev", "test"].includes(report.split) ||
    !Array.isArray(report.cases) ||
    !Number.isInteger(report.total_cases) ||
    report.total_cases < report.cases.length ||
    report.pending_cases !== report.total_cases - report.cases.length
  ) {
    throw new Error("The API returned an invalid evaluation report.");
  }
  if (
    report.cases.some(
      (item) =>
        !["yes", "no"].includes(item.expected) ||
        typeof item.probability !== "number" ||
        !Number.isFinite(item.probability) ||
        item.probability < 0 ||
        item.probability > 1,
    )
  ) {
    throw new Error(
      "The report has a case with an invalid label or probability.",
    );
  }
  return report;
}

export function ConversationDetails({ conversation }) {
  const turns = conversation?.turns ?? [];
  if (!turns.length)
    return (
      <p className="empty-context">No conversation context in this report.</p>
    );

  return (
    <div className="conversation-context">
      <div className="context-title">Conversation context</div>
      {turns.map((turn, index) => {
        const isTarget = index === turns.length - 1;
        return (
          <div
            className={`conversation-line${isTarget ? " is-target" : ""}`}
            key={`${turn.index ?? index}-${index}`}
          >
            <span className="conversation-role">
              {isTarget ? "Current user" : `Turn ${turn.index ?? index + 1}`}
            </span>
            <div className="conversation-content">
              <p>
                <span className="speaker">User</span>
                {turn.user}
              </p>
              {!isTarget && turn.reply && (
                <p>
                  <span className="speaker">Assistant</span>
                  {turn.reply}
                </p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ResultRow({ item, index, outcome, predicted, expanded, onToggle }) {
  const message =
    item.user_message ||
    item.conversation?.turns?.at(-1)?.user ||
    "(No message text)";
  const caseId = item.case_id || `Case ${index + 1}`;

  return (
    <>
      <tr className="case-row">
        <td className="message-cell" data-label="Latest user message">
          <p className="message-text">{message}</p>
          <div className="message-meta">
            <span>{caseId}</span>
            <button
              className="context-toggle"
              type="button"
              onClick={onToggle}
              aria-expanded={expanded}
              aria-controls={`context-${index}`}
            >
              {expanded ? "Hide context" : "View context"}
            </button>
          </div>
        </td>
        <td data-label="Probability">
          <span className="probability-value">
            {Math.round(item.probability * 100)}%
          </span>
          <div className="probability-track" aria-hidden="true">
            <span style={{ width: `${item.probability * 100}%` }} />
          </div>
        </td>
        <td data-label="Human label">
          {item.expected === "yes" ? "Yes" : "No"}
        </td>
        <td data-label="Predicted">{predicted ? "Yes" : "No"}</td>
        <td data-label="Outcome">
          <span className={`outcome outcome-${outcome}`}>
            {outcomeLabel(outcome)}
          </span>
        </td>
      </tr>
      {expanded && (
        <tr className="context-row" id={`context-${index}`}>
          <td colSpan="5">
            <ConversationDetails conversation={item.conversation} />
          </td>
        </tr>
      )}
    </>
  );
}

function Summary({ cases, threshold }) {
  const counts = useMemo(() => {
    const summary = { hit: 0, miss: 0, false_alarm: 0, correct_no: 0 };
    cases.forEach((item) => {
      summary[outcomeFor(item, threshold)] += 1;
    });
    return summary;
  }, [cases, threshold]);

  const flagged = counts.hit + counts.false_alarm;
  const actualYes = counts.hit + counts.miss;
  const precision = flagged ? counts.hit / flagged : 0;
  const recall = actualYes ? counts.hit / actualYes : 0;
  const percent = (value) => `${(value * 100).toFixed(1)}%`;

  return (
    <>
      <section
        className="metric-grid"
        aria-label="Evaluation metrics"
        aria-live="polite"
      >
        <article className="metric-card">
          <span className="metric-label">Precision</span>
          <strong className="metric-value">{percent(precision)}</strong>
          <span className="metric-note">
            {counts.hit} of {flagged} flagged{" "}
            {flagged === 1 ? "case is" : "cases are"} correct
          </span>
        </article>
        <article className="metric-card">
          <span className="metric-label">Recall</span>
          <strong className="metric-value">{percent(recall)}</strong>
          <span className="metric-note">
            {counts.hit} of {actualYes} frustrated{" "}
            {actualYes === 1 ? "case found" : "cases found"}
          </span>
        </article>
        <article className="metric-card case-total-card">
          <span className="metric-label">Cases evaluated</span>
          <strong className="metric-value">{cases.length}</strong>
          <span className="metric-note">
            {actualYes} labeled frustrated by a human
          </span>
        </article>
      </section>
      <div className="outcome-summary" aria-live="polite">
        <span>
          <i className="legend-dot dot-hit" />
          {counts.hit} hits
        </span>
        <span>
          <i className="legend-dot dot-miss" />
          {counts.miss} misses
        </span>
        <span>
          <i className="legend-dot dot-false" />
          {counts.false_alarm} false alarms
        </span>
        <span>
          <i className="legend-dot dot-correct" />
          {counts.correct_no} correct negatives
        </span>
      </div>
    </>
  );
}

function EmptyState({ split, threshold, loading, error }) {
  return (
    <section className="empty-state" role="status" aria-live="polite">
      <div className="empty-mark" aria-hidden="true">
        S
      </div>
      <h2>
        {loading
          ? "Loading saved scores…"
          : error
            ? "Results unavailable"
            : `No saved ${split} scores yet`}
      </h2>
      {loading ? (
        <p>Reading the evaluation results from your database.</p>
      ) : error ? (
        <p>Check the connection message above, then choose Refresh.</p>
      ) : (
        <>
          <p>
            Run the evaluator from the project root, then choose Refresh.
            {split === "test" &&
              " Run the final test evaluation only after choosing your threshold on dev."}
          </p>
          <pre>
            <code>
              python -m eval.evaluate_frustration --split {split} --threshold{" "}
              {threshold.toFixed(2)}
            </code>
          </pre>
          <p className="privacy-note">
            Refreshing reads saved scores and never calls the classifier.
          </p>
        </>
      )}
    </section>
  );
}

export default function App() {
  // Evaluations stays mounted while hidden, so its split, threshold and filter survive a tab switch.
  const [view, setView] = useState("evaluations");
  const [report, setReport] = useState(null);
  const [split, setSplit] = useState("dev");
  const [threshold, setThreshold] = useState(0.5);
  const [filter, setFilter] = useState("all");
  const [expandedCase, setExpandedCase] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshCount, setRefreshCount] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setReport(null);
    setError("");
    setFilter("all");
    setExpandedCase(null);

    async function loadResults() {
      try {
        const response = await fetch(`/api/results?split=${split}`, {
          signal: controller.signal,
          cache: "no-store",
        });
        const data = await response.json().catch(() => null);
        if (!response.ok) {
          throw new Error(
            typeof data?.detail === "string"
              ? data.detail
              : "Cannot load results. Start the Python API and check its connection to Postgres.",
          );
        }
        const nextReport = validateReport(data);
        if (nextReport.split !== split) {
          throw new Error("The API returned results for a different split.");
        }
        if (!controller.signal.aborted) setReport(nextReport);
      } catch (caught) {
        if (!controller.signal.aborted) {
          setError(
            caught instanceof Error
              ? caught.message
              : "Could not load saved results.",
          );
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    loadResults();
    return () => controller.abort();
  }, [split, refreshCount]);

  const cases = report?.split === split ? report.cases : [];
  const visibleCases = cases
    .map((item, index) => ({
      item,
      index,
      outcome: outcomeFor(item, threshold),
    }))
    .filter(({ outcome }) => filter === "all" || filter === outcome);

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="Signals home">
          <span className="brand-mark" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span>Signals</span>
        </a>
        <nav aria-label="Main navigation">
          <button
            className="nav-tab"
            type="button"
            aria-current={view === "evaluations" ? "page" : undefined}
            onClick={() => setView("evaluations")}
          >
            Evaluations
          </button>
          <button
            className="nav-tab"
            type="button"
            aria-current={view === "live" ? "page" : undefined}
            onClick={() => setView("live")}
          >
            Live
          </button>
        </nav>
        <span className="local-badge">
          <span className="status-dot" />
          Local prototype
        </span>
      </header>

      <main id="top" className="page-content" hidden={view !== "evaluations"}>
        <section className="page-heading">
          <div>
            <p className="breadcrumb">
              Signals <span>/</span> Evaluations
            </p>
            <h1>Frustration evaluation</h1>
            <p className="page-description">
              Compare classifier predictions with the human labels.
            </p>
          </div>
          <div className={`split-badge split-${split}`}>
            {split === "dev" ? "Development split" : "Test split"}
          </div>
        </section>

        <div className="data-toolbar">
          <label className="filter-control">
            <span>Evaluation split</span>
            <select value={split} onChange={(event) => setSplit(event.target.value)}>
              <option value="dev">Dev — tune here</option>
              <option value="test">Test — final results</option>
            </select>
          </label>
          <button
            className="secondary-button"
            type="button"
            disabled={loading}
            onClick={() => setRefreshCount((count) => count + 1)}
          >
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>

        {error && (
          <p className="error-message" role="alert">
            {error}
          </p>
        )}

        {loading || error || !cases.length ? (
          <EmptyState
            split={split}
            threshold={threshold}
            loading={loading}
            error={error}
          />
        ) : (
          <>
            {report.pending_cases > 0 && (
              <p className="results-notice" role="status">
                {cases.length} of {report.total_cases} cases have saved scores.
                Metrics cover these scored cases only. Finish the evaluation,
                then refresh to load the remaining {report.pending_cases}.
              </p>
            )}
            <Summary cases={cases} threshold={threshold} />

            <section
              className="threshold-panel"
              aria-label="Classifier threshold"
            >
              <div className="threshold-control">
                <div className="threshold-heading">
                  <label htmlFor="threshold">Frustration threshold</label>
                  <output htmlFor="threshold">{threshold.toFixed(2)}</output>
                </div>
                <input
                  id="threshold"
                  type="range"
                  min="0"
                  max="1"
                  step="0.01"
                  value={threshold}
                  disabled={split === "test"}
                  aria-describedby="threshold-note"
                  onChange={(event) => setThreshold(Number(event.target.value))}
                />
                <div className="range-labels">
                  <span>Flag more cases</span>
                  <span>Flag fewer cases</span>
                </div>
              </div>
              <p className="threshold-explanation">
                Label <strong>yes</strong> when the probability is at least{" "}
                <strong>{threshold.toFixed(2)}</strong>.
                <span id="threshold-note">
                  {split === "test"
                    ? "Threshold fixed to the value selected on dev."
                    : "Metrics update from the saved scores without calling the classifier."}
                </span>
              </p>
            </section>

            <section
              className="results-section"
              aria-labelledby="results-heading"
            >
              <div className="results-toolbar">
                <div>
                  <h2 id="results-heading">Case results</h2>
                  <p>
                    {visibleCases.length === cases.length
                      ? `Showing all ${cases.length} cases`
                      : `Showing ${visibleCases.length} of ${cases.length} cases`}
                  </p>
                </div>
                <div className="toolbar-actions">
                  <label className="filter-control">
                    <span>Show</span>
                    <select
                      value={filter}
                      onChange={(event) => setFilter(event.target.value)}
                    >
                      {FILTERS.map(([value, label]) => (
                        <option value={value} key={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Latest user message</th>
                      <th scope="col">Probability</th>
                      <th scope="col">Human label</th>
                      <th scope="col">Predicted</th>
                      <th scope="col">Outcome</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleCases.map(({ item, index, outcome }) => (
                      <ResultRow
                        key={`${item.case_id ?? index}-${index}`}
                        item={item}
                        index={index}
                        outcome={outcome}
                        predicted={item.probability >= threshold}
                        expanded={expandedCase === index}
                        onToggle={() =>
                          setExpandedCase(expandedCase === index ? null : index)
                        }
                      />
                    ))}
                    {!visibleCases.length && (
                      <tr>
                        <td className="empty-table" colSpan="5">
                          No cases match this outcome at the current threshold.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>
            <footer className="page-footer">
              <span>
                {report.created_at
                  ? `Loaded from database ${new Date(report.created_at).toLocaleString()}`
                  : "Loaded from database"}
              </span>
              <span>
                Question version {report.question_version}. Tune the threshold on dev.
              </span>
            </footer>
          </>
        )}
      </main>
      <LiveView hidden={view !== "live"} />
    </div>
  );
}
