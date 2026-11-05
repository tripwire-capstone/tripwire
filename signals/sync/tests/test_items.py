"""Tests for sync/items.py. Run from the repo root:  python -m pytest sync/tests"""

import json
from pathlib import Path

import pytest

from sync.items import (HISTORY_CAP, ROOT_PARENT_ID, TOOL_ERROR_FALLBACK, ItemError,
                        build_item, parse_laminar_time, to_iso_utc)

CONVO = "convo-7fbf383a-bc4c-4ef2-ae09-429067df8d46"
SMOKE_ROWS = json.loads((Path(__file__).parent / "fixtures" / "smoke_convo.json").read_text())
TURN_1, TURN_2, TURN_3 = (
    "25452952-65fa-dee1-0659-1516e32c2387",
    "364f7d71-4ae8-5abf-9f9c-39cb8b95134a",
    "bf482284-34d2-c490-4973-66d011fce2ca",
)


# ------------------------------------------------------------------ fake rows

def root(n, *, status="success", user=None, reply=None):
    """Root span for turn n; turns are 10 seconds apart."""
    return {
        "trace_id": f"trace-{n:02d}", "span_id": f"root-{n:02d}",
        "parent_span_id": ROOT_PARENT_ID, "name": "park_chat", "span_type": "DEFAULT",
        "start_time": f"2026-10-26 18:{n:02d}:00.000000000",
        "end_time": f"2026-10-26 18:{n:02d}:05.000000000",
        "status": status,
        "input": json.dumps(user if user is not None else f"message {n}"),
        "output": "" if status == "error" else json.dumps(reply or f"reply {n}"),
    }


def tool(n, *, status="success", output=None, name="get_park_details", second=1):
    return {
        "trace_id": f"trace-{n:02d}", "span_id": f"tool-{n:02d}-{second}",
        "parent_span_id": f"root-{n:02d}", "name": name, "span_type": "TOOL",
        "start_time": f"2026-10-26 18:{n:02d}:{second:02d}.000000000",
        "end_time": f"2026-10-26 18:{n:02d}:{second:02d}.500000000",
        "status": status,
        "input": json.dumps({"park_name": "Olympic"}),
        "output": json.dumps(output if output is not None else {"park": "Olympic"}),
    }


def conversation(length):
    return [root(n) for n in range(1, length + 1)]


# ------------------------------------------------------------------ the real conversation

def test_smoke_conversation_last_turn():
    item = build_item(SMOKE_ROWS, TURN_3, CONVO)

    assert item["contract"] == "v1"
    assert item["conversation_id"] == CONVO
    assert item["target_turn"] == 3
    assert item["history_truncated"] is False
    assert [t["index"] for t in item["turns"]] == [1, 2, 3]
    assert [t["trace_id"] for t in item["turns"]] == [TURN_1, TURN_2, TURN_3]

    first = item["turns"][0]
    assert first["user"].startswith("Hi there! We're planning")      # Rule 3: no stray quotes
    assert "\n\n1. **Bright Angel Trail**" in first["reply"]           # real line breaks
    assert first["started_at"] == "2026-10-26T18:32:46.549806Z"       # Rule 8
    assert first["tools"] == [{
        "name": "check_trail_status",
        "input": {"park_name": "Grand Canyon"},
        "output": json.loads(SMOKE_ROWS[1]["output"]),
        "error": None,
    }]
    assert item["turns"][1]["user"].startswith("Oh, that’s so helpful")  # curly apostrophe kept
    assert item["turns"][1]["tools"] == []


def test_item_is_json_serializable():
    json.dumps(build_item(SMOKE_ROWS, TURN_3, CONVO))


# ------------------------------------------------------------------ Rule 1: tools by parent

def test_row_order_does_not_matter():
    assert (build_item(list(reversed(SMOKE_ROWS)), TURN_3, CONVO)
            == build_item(SMOKE_ROWS, TURN_3, CONVO))


def test_tool_attached_to_its_parent_even_when_rows_interleave():
    # Turn 1's tool row listed after turn 2's root.
    rows = [root(1), root(2), tool(1)]
    item = build_item(rows, "trace-02", "c")
    assert len(item["turns"][0]["tools"]) == 1
    assert item["turns"][1]["tools"] == []


def test_tools_in_call_order():
    rows = [root(1), tool(1, name="second", second=3), tool(1, name="first", second=2)]
    item = build_item(rows, "trace-01", "c")
    assert [t["name"] for t in item["turns"][0]["tools"]] == ["first", "second"]


def test_orphan_tool_raises():
    orphan = {**tool(1), "parent_span_id": "root-99"}
    with pytest.raises(ItemError, match="has no root span"):
        build_item([root(1), orphan], "trace-01", "c")


def test_unexpected_non_root_span_raises():
    llm = {**tool(1), "span_type": "LLM"}
    with pytest.raises(ItemError, match="unexpected non-root LLM span"):
        build_item([root(1), llm], "trace-01", "c")


# ------------------------------------------------------------------ Rule 2: order and index

def test_turns_ordered_by_start_time_not_row_order():
    item = build_item([root(3), root(1), root(2)], "trace-03", "c")
    assert [t["trace_id"] for t in item["turns"]] == ["trace-01", "trace-02", "trace-03"]
    assert [t["index"] for t in item["turns"]] == [1, 2, 3]


def test_start_time_tie_broken_by_trace_id():
    a, b = root(1), {**root(1), "trace_id": "trace-00", "span_id": "root-00"}
    item = build_item([a, b, root(2)], "trace-02", "c")
    assert [t["trace_id"] for t in item["turns"]] == ["trace-00", "trace-01", "trace-02"]


# ------------------------------------------------------------------ Rule 3: decoding

def test_invalid_json_raises():
    bad = {**root(1), "input": "not json"}
    with pytest.raises(ItemError, match="not valid JSON"):
        build_item([bad], "trace-01", "c")


def test_message_that_is_not_a_string_raises():
    bad = {**root(1), "input": json.dumps({"text": "hi"})}
    with pytest.raises(ItemError, match="not a message string"):
        build_item([bad], "trace-01", "c")


def test_empty_output_on_a_successful_turn_raises():
    bad = {**root(1), "output": ""}
    with pytest.raises(ItemError, match="not valid JSON"):
        build_item([bad], "trace-01", "c")


# ------------------------------------------------------------------ Rule 4: tool errors

def test_failed_tool_gets_message_from_output():
    failed = tool(1, status="error", output={"error": "Park details service timed out after 30s."})
    item = build_item([root(1), failed], "trace-01", "c")
    t = item["turns"][0]["tools"][0]
    assert t["error"] == "Park details service timed out after 30s."
    assert t["output"] == {"error": "Park details service timed out after 30s."}  # kept as is


def test_failed_tool_without_message_gets_fallback():
    failed = tool(1, status="error", output={"detail": "?"})
    item = build_item([root(1), failed], "trace-01", "c")
    assert item["turns"][0]["tools"][0]["error"] == TOOL_ERROR_FALLBACK


def test_successful_tool_with_an_error_key_is_not_an_error():
    ok = tool(1, output={"booked": True, "error": None})
    item = build_item([root(1), ok], "trace-01", "c")
    assert item["turns"][0]["tools"][0]["error"] is None


# ------------------------------------------------------------------ Rule 5: crashed turns

def test_crashed_turn_in_history_has_null_reply():
    rows = [root(1), root(2, status="error"), root(3)]
    item = build_item(rows, "trace-03", "c")
    crashed = item["turns"][1]
    assert crashed["user"] == "message 2"
    assert crashed["reply"] is None


def test_crashed_turn_keeps_tools_it_finished():
    rows = [root(1, status="error"), tool(1), root(2)]
    item = build_item(rows, "trace-02", "c")
    assert len(item["turns"][0]["tools"]) == 1


def test_crashed_target_raises():
    with pytest.raises(ItemError, match="crashed"):
        build_item([root(1), root(2, status="error")], "trace-02", "c")


# ------------------------------------------------------------------ Rule 6: later turns

def test_later_turns_are_dropped():
    item = build_item(SMOKE_ROWS, TURN_2, CONVO)
    assert item["target_turn"] == 2
    assert [t["trace_id"] for t in item["turns"]] == [TURN_1, TURN_2]


def test_first_turn_as_target_has_no_history():
    item = build_item(SMOKE_ROWS, TURN_1, CONVO)
    assert [t["index"] for t in item["turns"]] == [1]


def test_missing_target_raises():
    with pytest.raises(ItemError, match="not among the root spans"):
        build_item(SMOKE_ROWS, "no-such-trace", CONVO)


# ------------------------------------------------------------------ Rule 7: history cap

def test_cap_is_ten():
    assert HISTORY_CAP == 10


def test_exactly_ten_before_target_is_not_truncated():
    item = build_item(conversation(11), "trace-11", "c")
    assert item["history_truncated"] is False
    assert [t["index"] for t in item["turns"]] == list(range(1, 12))


def test_eleven_before_target_drops_the_oldest():
    item = build_item(conversation(12), "trace-12", "c")
    assert item["history_truncated"] is True
    assert [t["index"] for t in item["turns"]] == list(range(2, 13))


def test_long_conversation_keeps_real_indexes():
    item = build_item(conversation(25), "trace-25", "c")
    assert item["history_truncated"] is True
    assert item["target_turn"] == 25
    assert [t["index"] for t in item["turns"]] == list(range(15, 26))


def test_cap_applies_after_dropping_later_turns():
    # Target turn 5 of 25: four turns of history, nothing cut.
    item = build_item(conversation(25), "trace-05", "c")
    assert item["history_truncated"] is False
    assert [t["index"] for t in item["turns"]] == [1, 2, 3, 4, 5]


# ------------------------------------------------------------------ Rule 8: timestamps

def test_nanoseconds_are_cut_to_microseconds():
    assert to_iso_utc(parse_laminar_time("2026-10-26 18:32:46.549806073")) \
        == "2026-10-26T18:32:46.549806Z"


def test_cut_not_rounded():
    # Rounding would give .123457; cutting gives .123456.
    assert to_iso_utc(parse_laminar_time("2026-10-26 18:32:46.123456789")) \
        == "2026-10-26T18:32:46.123456Z"


def test_timestamp_without_fraction():
    assert to_iso_utc(parse_laminar_time("2026-10-26 18:32:46")) == "2026-10-26T18:32:46.000000Z"


def test_parsed_time_is_utc():
    assert parse_laminar_time("2026-10-26 18:32:46.5").utcoffset().total_seconds() == 0


def test_unexpected_timestamp_format_raises():
    with pytest.raises(ItemError, match="unexpected Laminar timestamp"):
        parse_laminar_time("2026-10-26T18:32:46Z")
