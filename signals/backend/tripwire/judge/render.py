"""Render Laminar spans as judge input with citable evidence labels."""

from __future__ import annotations

import json
from dataclasses import dataclass

from tripwire.poller.poll import ROOT_PARENT_ID


@dataclass
class Rendered:
    text: str
    labels: dict[str, str]


def render_trace(spans: list[dict], earlier_turns: list[dict]) -> Rendered:
    """Render one turn and its context without modifying the source rows."""
    ordered = sorted(spans, key=lambda span: (span["started_utc"], span["span_id"]))
    labels = {}
    current = []
    for span in ordered:
        body = _render_current_span(span)
        if body is None:
            continue
        label = f"S{len(labels) + 1}"
        labels[label] = span["span_id"]
        current.append(f"[{label}] {body}")

    earlier = [
        f"USER: {_format_payload(_decode(turn['input']))}\n"
        f"ASSISTANT: {_format_payload(_decode(turn['output']))}"
        for turn in earlier_turns
    ]
    text = "\n\n".join([
        _block("system_prompt", _system_prompt(ordered)),
        _block("earlier_turns", "\n\n".join(earlier), ' context_only="true"'),
        _block("current_turn", "\n\n".join(current)),
    ])
    return Rendered(text=text, labels=labels)


def _block(name: str, body: str, attributes: str = "") -> str:
    return f"<{name}{attributes}>\n" + (f"{body}\n" if body else "") + f"</{name}>"


def _decode(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def _format_payload(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _system_prompt(spans: list[dict]) -> str:
    llm = next((span for span in spans if span["span_type"] == "LLM"), None)
    messages = _decode(llm["input"]) if llm else None
    if not isinstance(messages, list):
        return ""
    return "\n\n".join(
        _format_payload(message["content"])
        for message in messages
        if isinstance(message, dict) and message.get("role") == "system"
        and message.get("content") is not None
    )


def _render_llm(output) -> str:
    messages = _decode(output)
    if not isinstance(messages, list):
        return f"ASSISTANT: {_format_payload(messages)}"

    lines = []
    for message in messages:
        if not isinstance(message, dict):
            lines.append(f"ASSISTANT: {_format_payload(message)}")
            continue
        if message.get("content"):
            lines.append(f"ASSISTANT: {_format_payload(message['content'])}")
        for call in message.get("tool_calls") or []:
            function = call.get("function") if isinstance(call, dict) else None
            if isinstance(function, dict) and function.get("name"):
                arguments = _format_payload(function.get("arguments"))
                lines.append(f"ASSISTANT TOOL CALL: {function['name']}({arguments})")
            else:
                lines.append(f"ASSISTANT TOOL CALL: {_format_payload(call)}")
    return "\n".join(lines)


def _exception_details(events) -> str:
    for event in _decode(events) or []:
        if isinstance(event, dict):
            name, attributes = event.get("name"), event.get("attributes")
        elif isinstance(event, (list, tuple)) and len(event) == 3:
            _, name, attributes = event
        else:
            continue
        attributes = _decode(attributes)
        if name == "exception" and isinstance(attributes, dict):
            return ": ".join(
                str(attributes[key])
                for key in ("exception.type", "exception.message")
                if attributes.get(key)
            )
    return ""


def _render_current_span(span: dict) -> str | None:
    failed = span["status"] == "error"
    if span["parent_span_id"] == ROOT_PARENT_ID:
        body = f"USER: {_format_payload(_decode(span['input']))}"
    elif span["span_type"] == "LLM":
        body = _render_llm(span["output"])
    elif span["span_type"] == "TOOL":
        body = (
            f"TOOL {span['name']}\n"
            f"Arguments: {_format_payload(span['input'])}\n"
            f"Result: {_format_payload(span['output'])}"
        )
    elif failed:
        body = f"{span['name']}\nResult: {_format_payload(span['output'])}"
    else:
        return None

    if failed:
        details = _exception_details(span.get("events"))
        error = f"ERROR {details}" if details else "ERROR"
        return f"{body}\n{error}" if body else error
    return body or None
