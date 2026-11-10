# Handoff: the judge half of Signals (LLM module, rendering, prompt, checks, templates)

*Written Tue Nov 10, 2026. For whichever Signals-pair member builds this half ("B"), and their Claude
session. The other half (job flow, "A") is built in parallel; see `HANDOFF_JOB_FLOW.md`.*

## Start here (for Claude)

Read, in order:
1. `DESIGN.md` (same folder): every Signals decision (S1–S16). **Treat SETTLED items as decided.** If
   something looks wrong, raise it with the person you're working with; don't silently design around it.
2. `HANDOFF_JOB_FLOW.md`, section **A2**: the `types.py` interface you must return. Don't change it
   without both Signals members approving a separate PR.
3. `FOR_CLUSTERING_AGENTS.md`: the Clusters pair reads your `judge_input` and will import your LLM
   module.
4. `../clustering/DESIGN.md` (their summarizer, Q2–Q4 and Q11, uses your LLM module).
5. `tripwire/poller/laminar.py` (the Laminar SQL client you'll reuse), `traffic/tau-bench/README.md`.

Then propose the first PR (B1, below) before writing code. Walk the person you're working with through
the core logic of each piece before writing it.

**Labels used below:** **SETTLED** (decided), **PROPOSED** (current direction; confirm with the team
before relying on it in a way that's hard to change), **OPEN** (don't decide it yourself: stop and ask).

## Scope

`judge(laminar, trace, version) -> JudgeResult`: read one trace from Laminar, render it as text, ask the
LLM, check the answer. **It writes nothing to the database** (S14); the runner and the test-on-trace
endpoint store or return the result. You never need the database or the job loop: you work from fixture
files until the swap.

| PR | Piece | Decisions |
|---|---|---|
| B1 | **Shared LLM module** `tripwire/llm/`: interface agreed with the Clusters pair **before** coding | PROPOSED (Q27) |
| B2 | **Laminar fetch + fixtures:** the queries the judge needs, and a script that saves ~50 real traces to `tests/fixtures/` | S2, S12 |
| B3 | **`render.py`**: spans → text with span labels, plus the label → span-ID map. This also fixes `judge_input` | OPEN design (Q19, Q26) |
| B4 | **`schema.py`, `prompt.py`, `check.py`**: output schema per signal, the prompt, output checks | S10, S11, S13 |
| B5 | **`judge.py` + templates**: ties it together; Failure and Friction templates | S1 |

**Swap day:** when B5 merges, the runner's stub `judge()` is replaced by yours. Same signature, same
types, nothing else changes.

## B1: shared LLM module (PROPOSED: agree with the Clusters pair first)

The Clusters pair's summarizer is blocked on this interface, so it's the first thing to settle. Post the
shape to them (it's already in `FOR_CLUSTERING_AGENTS.md` §4) and get a yes before writing it.

```python
# tripwire/llm/__init__.py
@dataclass
class LLMResult:
    parsed: dict                 # the JSON output, already parsed
    raw: str                     # the raw text, for debugging
    model: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: float | None       # None when the price is unknown

def complete(model: str, messages: list[dict], json_schema: dict, *, timeout: float = 60) -> LLMResult: ...

class LLMServiceError(Exception):   # timeout, connection, 429, 5xx, auth → caller waits, no attempt used
    retry_after: float | None
class LLMBadOutput(Exception):      # doesn't parse / doesn't match the schema → uses an attempt
class LLMInputTooLong(Exception):   # context length exceeded → fail at once
class LLMRefused(Exception):        # the model refused (strict-mode `refusal`) → fail at once

class FakeLLM: ...                  # scripted LLMResults and each exception, for tests
```

- **Provider: OpenAI** (S16), strict JSON-schema structured output. Map the SDK's errors onto the
  classes above (rate limit, timeout, connection, server and auth errors → `LLMServiceError`; a
  context-length error → `LLMInputTooLong`; a `refusal` → `LLMRefused`; a cut-off response or parse
  failure → `LLMBadOutput`). Read token counts, including cached tokens, from the response's usage.
- `LLMRefused` is an addition to what the Clusters pair asked for; tell them when you post the shape.
- **Prices: OPEN (Q23).** The team may use an open-source library for model calls and pricing. Until
  decided, return `cost_usd = None` or use a small temporary price dict clearly marked as such. Don't
  build a pricing system.
- **Default model: OPEN (Q23).** Read `JUDGE_DEFAULT_MODEL` from the environment; don't hard-code a model.
  When it's chosen, it should differ from the model the traffic generator's agent uses (tau-traffic's
  default agent is gpt-6-luna), so the judge isn't grading its own model family.

## B2: Laminar fetch and fixtures (SETTLED inputs)

Put the queries in `tripwire/judge/fetch.py`, using the existing `LaminarSQL` client. Facts about
Laminar v0.3.3's SQL API (verified against its source):

- **Spans of one trace:** table `spans`; useful columns `span_id`, `parent_span_id`, `name`,
  `span_type` (LLM, TOOL, DEFAULT, …), `start_time`, `end_time`, `input`, `output` (full JSON strings,
  not truncated), `status` (`success` / `error`), `events`, `model`. Always add a `start_time` bound to
  the WHERE clause (around the trace's start) so Laminar's rewriter narrows the time range.
- **Errors:** there's no error-message column. A failed span has an event named `exception` in its
  `events` array; the message is in that event's attributes (`exception.type`, `exception.message`).
- **The root span** has the nil UUID as parent (`00000000-0000-0000-0000-000000000000`).
- **Earlier turns** (S12, SETTLED): up to the last `JUDGE_EARLIER_TURNS` (5) traces with the same
  `session_id` that ended before this one, from Laminar's `traces` table; for each, the root span's input
  (user message) and output (assistant reply) only. No `session_id` → no earlier turns.
- Values go in as SQL `parameters`, never pasted into the query text (as the poller does).

**Fixture script** (e.g. `python -m tripwire.judge.export_fixtures --session ... --limit 50`): run
tau-traffic in **turn mode** (`traffic/tau-bench/`, retail domain is cheapest), then save each trace's
spans and earlier turns as one JSON file in `tests/fixtures/traces/`. Include a few errored traces and a
few multi-turn conversations. Tests read fixtures through a fake Laminar client, never the network.

## B3: rendering (OPEN design: you lead it, the team approves)

How spans become text was parked to revisit. Propose the design (with two or three real rendered
fixtures as examples) before building. Starting point from the design session:

- Compact text, not JSON: a `<system_prompt>` block (from the first LLM span's input, shown once), an
  `<earlier_turns context_only="true">` block (S12: plain `USER:` / `ASSISTANT:` pairs, **no labels**),
  and a `<current_turn>` block.
- In the current turn, walk spans in start-time order with short labels `[S1]`, `[S2]`, …:
  - LLM spans: only their **output** (assistant text or chosen tool calls); their input repeats the
    system prompt and history on every step;
  - TOOL spans: name, arguments, then the result or `ERROR <type>: <message>` from the exception event;
  - other spans: only if they have an error; their children still appear.
- Return `Rendered(text: str, labels: dict[str, str])`, label → real Laminar span ID.
- Deterministic: the same spans always give the same text (S15 relies on re-rendering).
- **Size:** no limit or trimming for now (SETTLED for now). A trace too big for the model raises
  `LLMInputTooLong` → `JudgeError(retryable=False)`.
- **`judge_input`** (OPEN, Q26): proposed to be exactly the rendered trace (the three blocks), without the
  system message or the `<signal>` block. The Clusters pair reads it on every event. **Its shape is a
  contract:** announce it when it's settled and before it changes.

## B4: schema, prompt, checks (SETTLED, S10, S11, S13)

**`schema.py`:** build a strict JSON schema per signal version:
- top level: `analysis` (string, **first**), then `findings` (array);
- each finding: `severity` (enum info / warning / critical), `evidence` (array of label strings),
  `fields` (object generated from `version.output_schema`: every field required,
  `additionalProperties: false`, each field's description included, "may be empty" fields typed as
  `[<type>, "null"]`);
- `description`: **OPEN (Q25b)**, don't add a built-in description field until it's decided.

**`prompt.py`:** two messages (S13):
- **system** (fixed): you are reviewing one turn of an AI agent's conversation; judge only
  `<current_turn>`, earlier turns are context; one finding per distinct problem the signal describes, an
  empty list if none; every finding cites span labels as evidence; severity anchors: *info* = notable,
  not a problem; *warning* = the user got a worse result; *critical* = the user was harmed, misled or
  blocked (the signal may refine these); use only what the trace shows, no outside knowledge;
- **user:** the rendered trace, then `<signal>` with the name, instruction and optional
  `critical_guidance`.

**`check.py`:** (S11)
- evidence labels must exist in `Rendered.labels`; drop unknown labels; drop a finding left with no valid
  evidence and count it in `dropped_findings`; map labels to real span IDs for `Finding.span_ids`;
- more than `JUDGE_MAX_FINDINGS` (5): keep the 5 most severe;
- `LLMBadOutput` → **one repair retry** (resend with the error message appended); still bad →
  `JudgeError(retryable=True, uses_attempt=True)`.

**Mapping errors to `JudgeError`** (the runner relies on this exactly):

| From | `retryable` | `uses_attempt` |
|---|---|---|
| `LLMServiceError`, or Laminar unreachable | true | false |
| `LLMBadOutput` after the repair retry | true | true |
| `LLMRefused`, `LLMInputTooLong` | false | – (pass `rendered_trace`) |

## B5: `judge.py` and templates (SETTLED for Friday: Failure and Friction)

- `judge()`: fetch → render → build schema and prompt → `complete()` → check → `JudgeResult` (fill every
  field of the A2 types, including `analysis`, `raw_output`, `judge_input`, tokens and cost).
- **Templates** in `tripwire/judge/templates.py`: Failure Detector and Friction Detector, adapted from
  Laminar's `frontend/components/signals/prompts.ts` (github.com/lmnr-ai/lmnr, Apache-2.0; keep an
  attribution comment). Each template = name, instruction, output fields. Adapt to our format: severity is
  built in, so drop template fields that duplicate it.
- The API (job-flow half) lists templates from this module; the seed may create signals from them.

## Tests this half needs

- LLM module: each SDK error maps to the right class; usage and cached tokens are read; `FakeLLM` covers
  every path.
- Rendering: fixtures render deterministically; labels map back to real span IDs; earlier turns carry no
  labels; an errored tool shows its exception message.
- Schema: generated from a signal's fields; nullable fields; strict-mode rules hold.
- Checks: unknown labels dropped; uncited finding dropped and counted; 6 findings → 5 most severe;
  repair retry then `JudgeError`.
- `judge()` end to end on fixtures with `FakeLLM`: 0 findings, several findings, each error path.

## Open items (stop and ask; don't decide)

- **Q19 / Q26:** rendering and `judge_input` (you propose; the team approves; the Clusters pair is told).
- **Q25b:** built-in `description` or not.
- **Q23:** model choice and pricing, possibly an open-source library.
- **Q17:** gold sets (proving precision and recall, possibly from tau-traffic's answer key). Not before
  the judge runs end to end.

## Not in scope

Fan-out, the runner, the migration, the API (job-flow half). The Clusters pair's summarizer (they import
your LLM module).

## Working agreements

- Branch + PR for everything; the other Signals-pair member reviews.
- Settings only from env vars (add new ones to `.env.example`).
- Don't edit `tripwire/runner/`, `tripwire/poller/`, `tripwire/api/` or migrations; agree interface
  changes first. Changes to `tripwire/llm/`'s interface are agreed with the Clusters pair too.
- A decision that changes goes into `DESIGN.md` in the same PR.
