"""The tools' answers as Markdown, which models read more easily than raw JSON."""

import json


def name_of(cluster: dict) -> str:
    return cluster.get("name") or "(not named yet)"


def problem_clusters(clusters: list[dict], days: int, limit: int) -> str:
    if not clusters:
        return f"No clusters with warning or critical events in the last {days} days."
    shown = clusters[:limit]
    lines = [f"Clusters with the most warning or critical events in the last {days} days "
             f"({len(shown)} of {len(clusters)}):", ""]
    for c in shown:
        lines.append(f"- **Cluster {c['id']}: {name_of(c)}** (signal: {c['signal_name']}): "
                     f"{c['problem_events']} problem events")
    lines += ["", "Use get_cluster with a cluster ID for its summaries, causes and example events."]
    return "\n".join(lines)


def cluster(c: dict) -> str:
    lines = [
        f"# Cluster {c['id']}: {name_of(c)}",
        "",
        f"- Signal: {c['signal_name']} (ID {c['signal_id']})",
        f"- Events: {c['events']} ({c['problem_events']} warning or critical), "
        f"in {c['conversations']} conversations",
        f"- First seen: {c['first_event_at']}",
        f"- Last seen: {c['last_event_at']}",
        "",
        "## What goes wrong (one line per event, most typical first)",
        "",
    ]
    if c["summaries"]:
        for s in c["summaries"]:
            cause = f" Likely cause: {s['cause']}" if s.get("cause") else ""
            lines.append(f"- {s['summary']}{cause}")
    else:
        lines.append("- No summaries yet.")
    lines += ["", "## Example events", ""]
    for e in c["examples"]:
        lines += example(e)
    return "\n".join(lines).rstrip() + "\n"


def example(e: dict) -> list[str]:
    lines = [f"### Event {e['id']} ({e['severity']})", ""]
    if e.get("summary"):
        lines.append(f"- Summary: {e['summary']}")
    if e.get("cause"):
        lines.append(f"- Likely cause: {e['cause']}")
    lines.append(f"- Trace: {e['laminar_trace_id']}, ended {e['trace_ended_at']}")
    if e.get("session_id"):
        lines.append(f"- Conversation: {e['session_id']}")
    if e.get("laminar_url"):
        lines.append(f"- Open in Laminar: {e['laminar_url']}")
    if e.get("evidence_labels"):
        lines.append(f"- Evidence spans: {', '.join(e['evidence_labels'])}")
    if e.get("payload"):
        lines.append("- Fields:")
        lines += [f"  - {key}: {value_text(value)}" for key, value in e["payload"].items()]
    if e.get("analysis"):
        lines += ["", "Judge's analysis (it covers the whole turn, which can hold more than one event):", "",
                  quote(e["analysis"])]
    lines.append("")
    return lines


def value_text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.strip().splitlines())
