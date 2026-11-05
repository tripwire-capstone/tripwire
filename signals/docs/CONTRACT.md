# Seam contract: turn items (v1)

Track A (sync) turns Laminar traces into **turn items**. Track B (signal) classifies them.
This file is the agreement between the two. The gold files in `eval/gold/` follow it too, so the
classifier sees test cases and live data in exactly the same shape.

**Status:** draft, v1.1. Change it here first, then in code.

*v1.1 (Oct 26): the hand-off is now a database table (`turns`) instead of a function call. The item
itself is unchanged, so items still say `"contract": "v1"`. Table definitions: `schema.sql`.*

## The unit

One item = **one turn to judge, plus the conversation history before it.**

- A *turn* is one visitor message and the assistant's response to it (one Laminar trace).
- Track A produces one item per new turn. History repeats across items; that's fine.
- Track A writes each item as a row in the `turns` table (Postgres). Track B reads the rows that have
  no result yet and calls `classify(item)` once per item. Gold files load into the same table, so
  test cases and live data stay interchangeable.

## Example

```json
{
  "contract": "v1",
  "conversation_id": "convo-5ea3012e-44f5-4ef1-94ac-b4885bf0e86e",
  "target_turn": 3,
  "history_truncated": false,
  "turns": [
    {
      "index": 1,
      "trace_id": "0124c63d-4207-5f1a-...",
      "started_at": "2026-10-26T17:02:11Z",
      "user": "I need to book a campsite at Glacier for 2027-06-15 for 2 people under the name Maria.",
      "tools": [
        {"name": "check_campsite_availability",
         "input": {"park_name": "Glacier", "date": "2027-06-15"},
         "output": {"park": "Glacier Bay National Park", "available": true},
         "error": null}
      ],
      "reply": "Your campsite has been successfully booked! ..."
    },
    {"index": 2, "trace_id": "...", "started_at": "...", "user": "...", "tools": [], "reply": "..."},
    {"index": 3, "trace_id": "...", "started_at": "...", "user": "This is getting frustrating. ...",
     "tools": [], "reply": "..."}
  ]
}
```

## Fields

| Field | Type | Meaning |
| --- | --- | --- |
| `contract` | string | Always `"v1"` for this version |
| `conversation_id` | string | Laminar's session ID for the conversation |
| `target_turn` | integer | `index` of the turn to judge; always the last turn in `turns` |
| `history_truncated` | boolean | `true` if earlier turns were cut by the cap (see below) |
| `turns` | array | Turns in order, oldest first, ending with the target turn |
| `turns[].index` | integer | Position in the full conversation, 1-based. Stays the real position after truncation |
| `turns[].trace_id` | string or null | Laminar trace ID. **Required on the target turn** in live data; `null` allowed in gold files |
| `turns[].started_at` | string or null | Trace start time, ISO 8601 UTC |
| `turns[].user` | string | The visitor's message |
| `turns[].tools` | array | Tool calls the assistant made in this turn, in order |
| `turns[].tools[].name` | string | Tool name |
| `turns[].tools[].input` | object | Arguments |
| `turns[].tools[].output` | any | Result |
| `turns[].tools[].error` | string or null | Error message if the tool failed |
| `turns[].reply` | string or null | The assistant's final reply; `null` if the turn crashed |

## Decisions

1. **The target turn is complete in the item** (its tools and reply included). Each signal decides what
   the classifier sees. User Frustration judges the visitor's message, so its view drops the target
   turn's `tools` and `reply`, matching what the labelers saw. Signals that judge the assistant's
   response (e.g. Task Failure) need them. *Leak check:* gold files and live items go through the same
   view function, so the test stays fair only if that one function is right; give it a unit test.
2. **Tool calls are kept** in every turn. The classifier for a signal may leave them out of its input.
3. **History is capped at the 10 turns before the target** (11 turns in the item at most). The cap
   bounds long conversations; it isn't about cost. When turns are cut, `history_truncated` is `true`
   and the first `index` is greater than 1. park_demo conversations never exceed 7 turns, so the cap
   needs its own test with a longer fake conversation.
4. **No `user_id` or metadata** (app version, model) in the item. The classifier doesn't need them, and
   park_demo's user IDs encode the scenario number, which points at the answer key. The UI can get them
   from Laminar through `trace_id`.
5. **Turn order comes from trace start time** within a session. Track A assigns `index` from that order.
6. **A crashed turn** (a trace with an error status and no reply) stays in the history with
   `reply: null`. It is never a target.
7. **Results link by `turns.id`**, including gold turns without a trace ID. Each result stores a
   signal, question version, and raw probability. Join to `turns` for the item and Laminar trace ID.
   Increment the question version when the question, model, or input handling changes.

## Who does what

- **Track A** produces items: reconstructs turns from Laminar spans, applies the cap, and writes one
  `turns` row per new target turn. The table's unique key on the target `trace_id` guarantees no
  target is written twice.
- **Track B** consumes items: reads `turns` rows with no result yet, applies a signal's view, calls
  the Decisions API, and stores the result linked to its `turns` row.
- **Gold files** (`eval/gold/*.jsonl`) wrap an item as `case["conversation"]`, next to `expected`,
  `split` and `source`. Only `case["conversation"]` goes to the classifier.

## Open questions

- Should the cap be measured in turns or in size (tool outputs can be large)?
