"""The summarizer: one event -> one short summary saved in event_summaries (prepare half, PR 2).

Decisions: Design Notes Q17-Q20 and docs/clustering/DESIGN.md. The pieces, in call order:

  load_event      one SQL query: the event, the Signal version that judged it, the Signal's name
  build_messages  pure: the system prompt + one user message (trace, Signal, then THIS finding last)
  llm.complete    one paid call (llm_client.py); failures come back as one of four errors
  decide          pure: every rule for what the row becomes after that call
  save            one upsert of what decide returned

summarize_event() runs them for one event. Bugs in our own code are deliberately NOT caught here
(fail loudly, Q20): the row stays as it was and the caller logs the traceback.
"""

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from ..llm import LLMBadOutput, LLMError, LLMInputTooLong, LLMRefused, LLMServiceError
from .sql import event_signal

log = logging.getLogger("clusterer.summarize")

PROMPTS = Path(__file__).resolve().parent / "prompts"

# MVP constants (Q20). Growing waits and settings for these come after the MVP.
MAX_ATTEMPTS = 3
BAD_OUTPUT_WAIT = timedelta(minutes=1)
SERVICE_WAIT = timedelta(seconds=30)
LONG_SUMMARY_CHARS = 300      # longer is logged as a warning, not rejected (Q19)


# ------------------------------------------------------------------ prompt files

@dataclass(frozen=True)
class Prompt:
    version: int
    system: str
    schema: dict


def prompt_paths(version):
    return PROMPTS / f"summarize_v{version}.md", PROMPTS / f"summarize_v{version}.schema.json"


def load_prompt(version):
    text_path, schema_path = prompt_paths(version)
    return Prompt(version, text_path.read_text().strip(), json.loads(schema_path.read_text()))


# ------------------------------------------------------------------ input (Q18)

@dataclass(frozen=True)
class EventInput:
    event_id: int
    signal_name: str
    instruction: str
    field_descriptions: dict          # field name -> description ("" if the schema has none)
    severity: str
    payload: dict
    description: str | None           # may be dropped by the Signals pair: optional here
    evidence_labels: list             # labels the finding cited in judge_input, e.g. ["S3"] (Signals S25)
    judge_input: str


# The version joined is the one that judged THIS event (shared rule: sql.event_signal).
LOAD_EVENT_SQL = f"""
SELECT e.id, s.name, v.instruction, v.output_schema, e.severity, e.payload, e.description,
       e.judge_input, e.evidence_labels
FROM signal_events e
{event_signal("e", "v")}
JOIN signals s ON s.id = v.signal_id
WHERE e.id = %s
"""


def load_event(conn, event_id):
    row = conn.execute(LOAD_EVENT_SQL, (event_id,)).fetchone()
    if row is None:
        raise LookupError(f"signal event {event_id} not found")
    event_id, name, instruction, output_schema, severity, payload, description, judge_input, labels = row
    return EventInput(event_id, name, instruction, field_descriptions(output_schema), severity,
                      payload or {}, description, list(labels or []), judge_input)


def field_descriptions(output_schema):
    """Field name -> description, from a version's output_schema.

    The format is a list of fields, [{name, type, description, choices, nullable}] (Signals S20).
    Anything else is logged as a warning and gives no descriptions: the summary still works, but
    the warning makes the gap visible (an earlier version silently returned nothing here).
    """
    if not isinstance(output_schema, list):
        log.warning("output_schema isn't a list of fields (Signals S20); field descriptions left out")
        return {}
    return {field["name"]: field.get("description") or ""
            for field in output_schema if isinstance(field, dict) and field.get("name")}


def build_messages(event, system_prompt):
    """The two messages for one event. Trace first, this event's finding last (Q17)."""
    signal_lines = [f"name: {event.signal_name}", f"instruction: {event.instruction}"]
    if event.field_descriptions:
        signal_lines.append("fields:")
        signal_lines += [f"- {name}: {text}" if text else f"- {name}"
                         for name, text in event.field_descriptions.items()]

    finding_lines = [f"severity: {event.severity}"]
    if event.description and event.description.strip():
        finding_lines.append(f"description: {event.description}")
    if event.evidence_labels:
        finding_lines.append("evidence: " + ", ".join(f"[{label}]" for label in event.evidence_labels))
    if event.payload:
        finding_lines.append("fields:")
        finding_lines += [f"- {name}: {_render(value)}" for name, value in event.payload.items()]

    user = (f"<tripwire_trace>\n{event.judge_input}\n</tripwire_trace>\n\n"
            "<tripwire_signal>\n" + "\n".join(signal_lines) + "\n</tripwire_signal>\n\n"
            "<tripwire_finding>\n" + "\n".join(finding_lines) + "\n</tripwire_finding>")
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user}]


def _render(value):
    if value is None:
        return "(empty)"
    return value if isinstance(value, str) else json.dumps(value)


# ------------------------------------------------------------------ outcome -> row (Q20)

@dataclass(frozen=True)
class Row:
    """The parts of an existing event_summaries row that decide() needs."""
    attempts: int = 0
    error_kind: str | None = None
    last_error: str | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True)
class RowUpdate:
    status: str
    cause: str | None
    summary: str | None
    attempts: int
    not_before: object               # datetime or None
    error_kind: str | None
    last_error: str | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    finished_at: object              # datetime or None
    warning: str | None = None       # something to log, e.g. a long summary


ERROR_KINDS = {LLMBadOutput: "bad_output", LLMInputTooLong: "too_long", LLMRefused: "refused",
               LLMServiceError: "service"}


def decide(outcome, row, now):
    """What the row becomes after one call. `outcome` is an LLMResult or an LLMError.

    `row` is the current row, or None if this event was never tried. Pure: no database, no clock.
    """
    row = row or Row()
    if isinstance(outcome, LLMError):
        return _after_error(outcome, row, now)

    cause = (outcome.output.get("cause") or "").strip()
    summary = (outcome.output.get("summary") or "").strip()
    if not cause or not summary:
        blank = "summary" if not summary else "cause"
        return _after_error(LLMBadOutput(f"blank {blank}", usage=outcome.usage), row, now)

    totals = _add_usage(row, outcome.usage)
    warning = (f"summary is {len(summary)} characters (over {LONG_SUMMARY_CHARS})"
               if len(summary) > LONG_SUMMARY_CHARS else None)
    return RowUpdate("done", cause, summary, row.attempts + 1, None,
                     row.error_kind, row.last_error,      # an earlier error is kept (Q16)
                     *totals, finished_at=now, warning=warning)


def _after_error(error, row, now):
    kind = ERROR_KINDS[type(error)]
    message = str(error) or type(error).__name__
    totals = _add_usage(row, error.usage)

    if kind == "service":
        # Not the event's fault: no attempt used, try again after a wait (OpenAI's hint if longer).
        wait = SERVICE_WAIT
        if error.retry_after:
            wait = max(wait, timedelta(seconds=error.retry_after))
        return RowUpdate("pending", None, None, row.attempts, now + wait, kind, message,
                         *totals, finished_at=None)

    attempts = row.attempts + 1
    if kind == "bad_output" and attempts < MAX_ATTEMPTS:
        return RowUpdate("pending", None, None, attempts, now + BAD_OUTPUT_WAIT, kind, message,
                         *totals, finished_at=None)
    # Out of attempts, or too long / refused: a retry would get the same answer.
    return RowUpdate("failed", None, None, attempts, None, kind, message, *totals, finished_at=now)


def crash_update(row, error, now):
    """What the row becomes when OUR code crashed on this event (migration 004, Design Notes Q21).

    `failed` with error_kind 'crashed' and the error, so the event counts as finished, isn't retried
    (or paid for) again by accident, and comes back with the re-queue SQL once the bug is fixed.
    Totals are unchanged: if the crash came after a paid call, its usage was never reported.
    """
    row = row or Row()
    message = f"{type(error).__name__}: {error}"[:1000]
    return RowUpdate("failed", None, None, row.attempts + 1, None, "crashed", message,
                     row.input_tokens, row.cached_input_tokens, row.output_tokens, row.cost_usd,
                     finished_at=now)


def _add_usage(row, usage):
    """New totals: every paid call adds up. Cost stays None ("not priced") if any part is unknown."""
    if usage is None:
        return row.input_tokens, row.cached_input_tokens, row.output_tokens, row.cost_usd
    paid_before = row.input_tokens is not None
    cost = None
    if usage.cost_usd is not None and (row.cost_usd is not None or not paid_before):
        cost = round(float(row.cost_usd or 0) + usage.cost_usd, 6)
    return ((row.input_tokens or 0) + usage.input_tokens,
            (row.cached_input_tokens or 0) + usage.cached_input_tokens,
            (row.output_tokens or 0) + usage.output_tokens,
            cost)


# ------------------------------------------------------------------ database

def load_row(conn, event_id):
    found = conn.execute(
        """SELECT attempts, error_kind, last_error, input_tokens, cached_input_tokens,
                  output_tokens, cost_usd
           FROM event_summaries WHERE event_id = %s""", (event_id,)).fetchone()
    if found is None:
        return None
    attempts, kind, error, input_tokens, cached, output_tokens, cost = found
    return Row(attempts, kind, error, input_tokens, cached, output_tokens,
               None if cost is None else float(cost))


SAVE_SQL = """
INSERT INTO event_summaries (event_id, status, cause, summary, model, prompt_version, attempts,
                             not_before, error_kind, last_error, input_tokens, cached_input_tokens,
                             output_tokens, cost_usd, finished_at, updated_at)
VALUES (%(event_id)s, %(status)s, %(cause)s, %(summary)s, %(model)s, %(prompt_version)s,
        %(attempts)s, %(not_before)s, %(error_kind)s, %(last_error)s, %(input_tokens)s,
        %(cached_input_tokens)s, %(output_tokens)s, %(cost_usd)s, %(finished_at)s, %(now)s)
ON CONFLICT (event_id) DO UPDATE SET
    status = EXCLUDED.status, cause = EXCLUDED.cause, summary = EXCLUDED.summary,
    model = EXCLUDED.model, prompt_version = EXCLUDED.prompt_version,
    attempts = EXCLUDED.attempts, not_before = EXCLUDED.not_before,
    error_kind = EXCLUDED.error_kind, last_error = EXCLUDED.last_error,
    input_tokens = EXCLUDED.input_tokens, cached_input_tokens = EXCLUDED.cached_input_tokens,
    output_tokens = EXCLUDED.output_tokens, cost_usd = EXCLUDED.cost_usd,
    finished_at = EXCLUDED.finished_at, updated_at = EXCLUDED.updated_at
"""


def save(conn, event_id, update, model, prompt_version, now):
    values = {name: getattr(update, name) for name in RowUpdate.__dataclass_fields__ if name != "warning"}
    conn.execute(SAVE_SQL, {**values, "event_id": event_id, "model": model,
                            "prompt_version": prompt_version, "now": now})


# ------------------------------------------------------------------ one event, end to end

def summarize_event(conn, llm, event_id, model, prompt, now):
    """Summarize one event and save the outcome. Returns the RowUpdate that was saved.

    The caller (prepare_events, PR 4) chooses which events to try and catches unexpected errors
    per event, so one buggy event can't block the queue (Q20, decision C).
    """
    event = load_event(conn, event_id)
    messages = build_messages(event, prompt.system)
    log.info("summarizing event %s (%d characters of input)", event_id,
             sum(len(message["content"]) for message in messages))
    try:
        outcome = llm.complete(model, messages, prompt.schema)
    except LLMError as error:
        outcome = error
    update = decide(outcome, load_row(conn, event_id), now)
    save(conn, event_id, update, model, prompt.version, now)
    if update.warning:
        log.warning("event %s: %s", event_id, update.warning)
    if update.status != "done":
        log.warning("event %s: %s (%s), status %s, attempts %d", event_id, update.error_kind,
                    update.last_error, update.status, update.attempts)
    return update
