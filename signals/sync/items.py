"""Build contract turn items (docs/CONTRACT.md, v1) from Laminar spans.

build_item() is pure: rows in, one item out. No network, no database.

The rows are what the conversation query returns: one conversation's root
spans (one per turn) and their TOOL spans, each a dict with these keys:
trace_id, span_id, parent_span_id, name, span_type, start_time, end_time,
status, input, output.
"""

import json
from datetime import datetime, timezone

CONTRACT_VERSION = "v1"
HISTORY_CAP = 10  # turns kept before the target (contract decision 3)
ROOT_PARENT_ID = "00000000-0000-0000-0000-000000000000"
TOOL_ERROR_FALLBACK = "tool failed (no message recorded)"


class ItemError(ValueError):
    """The rows can't be turned into a valid item: something upstream is wrong."""


def build_item(rows, target_trace_id, conversation_id):
    """Return the contract item that judges the turn `target_trace_id`."""
    roots = [row for row in rows if row["parent_span_id"] == ROOT_PARENT_ID]
    tool_rows = [row for row in rows if row["parent_span_id"] != ROOT_PARENT_ID]

    # Rule 1: attach each tool to its turn by parent_span_id, not by row order.
    tools_by_root = {root["span_id"]: [] for root in roots}
    for row in tool_rows:
        if row["span_type"] != "TOOL":
            raise ItemError(f"unexpected non-root {row['span_type']} span {row['span_id']}")
        if row["parent_span_id"] not in tools_by_root:
            raise ItemError(f"tool span {row['span_id']} has no root span in these rows")
        tools_by_root[row["parent_span_id"]].append(row)

    # Rule 2: order turns by start time (ties by trace_id); index counts from 1.
    roots.sort(key=lambda root: (parse_laminar_time(root["start_time"]), root["trace_id"]))
    turns = [
        _build_turn(index, root, tools_by_root[root["span_id"]])
        for index, root in enumerate(roots, start=1)
    ]

    # Rule 6: keep the target and the turns before it.
    position = next(
        (i for i, turn in enumerate(turns) if turn["trace_id"] == target_trace_id), None)
    if position is None:
        raise ItemError(f"target trace {target_trace_id} is not among the root spans")
    # Rule 5: a crashed turn is never a target.
    if _crashed(roots[position]):
        raise ItemError(f"target trace {target_trace_id} crashed; it can't be a target")
    kept = turns[: position + 1]

    # Rule 7: at most HISTORY_CAP turns before the target; indexes stay real.
    history_truncated = len(kept) - 1 > HISTORY_CAP
    kept = kept[-(HISTORY_CAP + 1):]

    return {
        "contract": CONTRACT_VERSION,
        "conversation_id": conversation_id,
        "target_turn": kept[-1]["index"],
        "history_truncated": history_truncated,
        "turns": kept,
    }


def _build_turn(index, root, tool_rows):
    # Rule 3: Laminar stores inputs and outputs as JSON text.
    user = _decode(root["input"], f"root input of trace {root['trace_id']}")
    if not isinstance(user, str):
        raise ItemError(f"root input of trace {root['trace_id']} is not a message string")

    # Rule 5: a crashed turn keeps its message but has no reply.
    if _crashed(root):
        reply = None
    else:
        reply = _decode(root["output"], f"root output of trace {root['trace_id']}")
        if not isinstance(reply, str):
            raise ItemError(f"root output of trace {root['trace_id']} is not a reply string")

    tool_rows = sorted(tool_rows, key=lambda row: (parse_laminar_time(row["start_time"]),
                                                   row["span_id"]))
    return {
        "index": index,
        "trace_id": root["trace_id"],
        "started_at": to_iso_utc(parse_laminar_time(root["start_time"])),  # Rule 8
        "user": user,
        "tools": [_build_tool(row) for row in tool_rows],
        "reply": reply,
    }


def _build_tool(row):
    tool_input = _decode(row["input"], f"input of tool span {row['span_id']}")
    if not isinstance(tool_input, dict):
        raise ItemError(f"input of tool span {row['span_id']} is not an object")
    output = _decode(row["output"], f"output of tool span {row['span_id']}")

    # Rule 4: status says whether it failed; the output supplies the message.
    error = None
    if row["status"] == "error":
        message = output.get("error") if isinstance(output, dict) else None
        error = message if isinstance(message, str) and message else TOOL_ERROR_FALLBACK

    return {"name": row["name"], "input": tool_input, "output": output, "error": error}


def _crashed(root):
    # The root span's own status: traces.status would also say 'error' when only a
    # tool failed, and that turn still has a reply.
    return root["status"] == "error"


def _decode(text, what):
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError) as e:
        raise ItemError(f"{what} is not valid JSON text: {text!r:.80}") from e


def parse_laminar_time(text):
    """Laminar's '2026-10-26 18:32:46.549806073' (UTC, no zone) -> aware datetime.

    Python keeps microseconds only, so nanoseconds are cut off (not rounded).
    """
    whole, _, fraction = text.partition(".")
    micro = (fraction + "000000")[:6]
    try:
        naive = datetime.strptime(f"{whole}.{micro}", "%Y-%m-%d %H:%M:%S.%f")
    except ValueError as e:
        raise ItemError(f"unexpected Laminar timestamp {text!r}") from e
    return naive.replace(tzinfo=timezone.utc)


def to_iso_utc(moment):
    """Aware datetime -> '2026-10-26T18:32:46.549806Z' (the contract's format)."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
