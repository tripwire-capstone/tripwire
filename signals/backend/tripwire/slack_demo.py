"""Add realistic fake findings that make the Slack notifier send an alert, for demos.

    python -m tripwire.slack_demo critical   # a judged turn with one critical finding
    python -m tripwire.slack_demo cluster    # a new cluster: one problem seen 3 times in 2 sessions

The rows have the same shape as the real pipeline's, for the retail support agent we monitor:
  * traces carry a session ID; each run has the judge's analysis, raw answer, model and tokens
  * judge_input is a conversation turn rendered by the real renderer (judge.render), and each
    finding cites its evidence by label (S1, S2...) and by span ID
  * payload fills the signal's own fields (output_schema), whatever signal is first
  * the cluster's events have finished summaries, and the cluster is named the way the
    clusterer names one: after the summary of a typical member

The notifier picks them up on its next poll (every 15 s by default) if Slack is connected
(python -m tripwire.slack_setup connect). They show in the UI and dev view like real rows;
traces.metadata has {"source": "slack_demo"} so they can be found and deleted.

The cluster's events get placeholder embeddings and the cluster has no center, so the clusterer
never touches them. The critical event has no summary yet, so the clusterer summarizes and
clusters it like any other (an OpenAI call in real mode).

--project picks the project by name (default: the seeded "default" project).
Settings (env or .env): DATABASE_URL.
"""

import argparse
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg
from dotenv import load_dotenv

from .clusterer.naming import unique_name
from .judge.render import render_trace
from .placeholder import vector_text
from .poller.poll import ROOT_PARENT_ID
from .slack_setup import DEFAULT_PROJECT, find_project

EMBEDDING_SIZE = 1536
SUMMARY_PROMPT_VERSION = 1

SYSTEM_PROMPT = (
    "You are a customer service agent for an online retail store. Help users cancel or modify "
    "pending orders, return or exchange delivered orders, and update their address.\n"
    "Authenticate the user by email, or by name and ZIP code, before anything else.\n"
    "Before an action that changes an order, list its details and get an explicit yes.\n"
    "Refunds go to the original payment method or an existing gift card, as the user chooses."
)


# ------------------------------------------------------------------ scenarios


@dataclass
class Scenario:
    """One judged turn: what happened, and what the judge found in it."""
    earlier_turns: list[tuple[str, str]]   # (user, assistant) before this turn, context only
    steps: list[tuple]                     # this turn, in order (see build_spans)
    severity: str
    finding: str                           # the finding in words: goes in the signal's text fields
    evidence: list[str]                    # labels the finding cites, e.g. ["S1", "S3"]
    analysis: str
    cause: str = ""                        # the clusterer's summary, for cluster members
    summary: str = ""


def refund_to_gift_card() -> Scenario:
    order = "#W4817420"
    return Scenario(
        earlier_turns=[
            ("Hi, I want to return the two skateboards from my last order. The quality is awful. "
             "Refund to my credit card please.",
             "I can help with that. Could you give me the email on your account?"),
            ("noah.kim4120@example.com",
             f"Thanks, Noah. I've submitted the return of 2 skateboards from order {order}. "
             "The refund of $287.40 will go to your gift card."),
        ],
        steps=[
            ("user", "That's the third time I've said it: the refund goes to my CREDIT CARD, not a "
                     "gift card. Fix it now or I'm disputing the charge with my bank."),
            ("tool", "get_order_details", {"order_id": order},
             {"order_id": order, "status": "return requested",
              "return_items": ["Skateboard (maple, 31 in)", "Skateboard (bamboo, 28 in)"],
              "refund_payment_method_id": "gift_card_7245904", "refund_amount": 287.40}),
            ("assistant", "I'm sorry for the trouble. The return has already been submitted with the "
                          "refund going to your gift card, and I can't change it now. Is there "
                          "anything else I can help with?"),
        ],
        severity="critical",
        finding=("The user says for the third time that the refund must go to their credit card. "
                 "The assistant had sent it to a gift card without asking (S3) and now says it "
                 "can't be changed (S4), so the user is blocked and threatening a chargeback."),
        evidence=["S1", "S3", "S4"],
        analysis=("The user asked for a credit card refund in their first message. The assistant "
                  "submitted the return to a gift card without confirming, against its own rule to "
                  "get an explicit yes. In this turn the user is angry and repeating themselves, "
                  "and the assistant refuses to fix it. The user was harmed and blocked: critical."),
    )


def failed_account_lookup(name: str, email: str, attempt: int) -> Scenario:
    """The user repeats their email after the lookup fails: the same problem, different sessions."""
    repeat = ["I just told you, it's", "Again: my email is", "It's the same as before,"][attempt % 3]
    return Scenario(
        earlier_turns=[
            (f"Hi, I need to change the shipping address on my pending order. It's {email}.",
             "I couldn't find an account with that email. Could you double-check it?"),
        ],
        steps=[
            ("user", f"{repeat} {email}. I've used it for years, please look again."),
            ("tool", "find_user_id_by_email", {"email": email}, "Error: user not found"),
            ("assistant", "I'm sorry, I still can't find an account with that email. Could you "
                          "check it for typos?"),
        ],
        severity="warning",
        finding=(f"{name} has to repeat their email because the account lookup keeps failing (S3), "
                 "and the assistant asks them to check it again instead of offering the name and "
                 "ZIP code lookup (S4)."),
        evidence=["S1", "S3", "S4"],
        analysis=("The user gives the same email twice and grows impatient. The lookup fails both "
                  "times and the assistant never offers the other way to authenticate. Moderate "
                  "friction: warning."),
        cause="tool result: account lookup by email failed",
        summary=("The user repeats their email after the assistant's account lookup fails and no "
                 "other way to sign in is offered, in account lookup."),
    )


# ------------------------------------------------------------------ building rows


def build_spans(steps: list[tuple], start: datetime) -> list[dict]:
    """Laminar-shaped spans for one turn, in the form judge.render reads.

    Steps: ("user", text) | ("assistant", text) | ("tool", name, arguments, result).
    A tool step also adds the assistant's tool call that made it, as real traces have.
    """
    root_id = str(uuid.uuid4())
    spans, clock = [], start

    def add(parent, name, span_type, input_, output):
        nonlocal clock
        clock += timedelta(milliseconds=700)
        spans.append({
            "span_id": root_id if parent == ROOT_PARENT_ID else str(uuid.uuid4()),
            "parent_span_id": parent, "name": name, "span_type": span_type,
            "started_utc": clock.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "input": json.dumps(input_), "output": json.dumps(output),
            "status": "success", "events": "[]",
        })

    llm_input = [{"role": "system", "content": SYSTEM_PROMPT}]
    for step in steps:
        kind = step[0]
        if kind == "user":
            add(ROOT_PARENT_ID, "agent.turn", "DEFAULT", step[1], None)
        elif kind == "assistant":
            add(root_id, "litellm.completion", "LLM", llm_input,
                [{"role": "assistant", "content": step[1]}])
        elif kind == "tool":
            _, name, arguments, result = step
            add(root_id, "litellm.completion", "LLM", llm_input,
                [{"role": "assistant", "content": None, "tool_calls": [
                    {"function": {"name": name, "arguments": json.dumps(arguments)}}]}])
            add(root_id, name, "TOOL", arguments, result)
    return spans


def fill_payload(output_schema: list[dict], scenario: Scenario) -> dict:
    """The signal's own fields, filled the way a judge would for this finding."""
    payload = {}
    for spec in output_schema:
        name, kind, choices = spec["name"], spec["type"], spec.get("choices") or []
        if kind == "enum" and choices:
            # Worst choice for severity-like fields on a critical finding, else the middle one.
            worst = scenario.severity == "critical" and name in ("severity", "success", "impact")
            payload[name] = choices[-1] if worst else choices[len(choices) // 2]
        elif kind in ("number", "integer"):
            payload[name] = 1
        elif kind == "boolean":
            payload[name] = True
        else:
            payload[name] = scenario.finding
    return payload


def latest_version(conn, project_id: int) -> tuple[int, str, list[dict], str]:
    """(signal_version_id, signal name, output_schema, model) of the project's first signal."""
    row = conn.execute(
        """
        SELECT v.id, s.name, v.output_schema, v.model
        FROM signals AS s
        JOIN signal_versions AS v ON v.signal_id = s.id
        WHERE s.project_id = %s
        ORDER BY s.id, v.version_number DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        raise SystemExit("The project has no signals. Run the seed first: python -m tripwire.seed")
    return row


def add_judged_turn(conn, project_id: int, version, scenario: Scenario,
                    session_id: str, finished_at: datetime) -> tuple[int, int]:
    """Insert a trace, a finished run and one finding, as the runner would. Returns (run_id, event_id).

    The run finishes at finished_at; the turn itself ended a few seconds before.
    """
    version_id, _, output_schema, model = version
    ended_at = finished_at - timedelta(seconds=4)
    started_at = ended_at - timedelta(seconds=6)
    spans = build_spans(scenario.steps, started_at)
    earlier = [{"input": json.dumps(u), "output": json.dumps(a)} for u, a in scenario.earlier_turns]
    rendered = render_trace(spans, earlier)
    span_ids = [rendered.labels[label] for label in scenario.evidence]
    payload = fill_payload(output_schema, scenario)
    raw_output = json.dumps({"analysis": scenario.analysis, "findings": [
        {"severity": scenario.severity, "evidence": scenario.evidence, "fields": payload}]})

    (trace_id,) = conn.execute(
        """
        INSERT INTO traces (project_id, laminar_trace_id, session_id, started_at, ended_at, status,
                            metadata)
        VALUES (%s, %s, %s, %s, %s, 'success', '{"source": "slack_demo"}')
        RETURNING id
        """,
        (project_id, uuid.uuid4(), session_id, started_at, ended_at),
    ).fetchone()
    (run_id,) = conn.execute(
        """
        INSERT INTO signal_runs (trace_id, signal_version_id, status, attempts, model,
                                 input_tokens, cached_input_tokens, output_tokens,
                                 created_at, started_at, finished_at, analysis, raw_output)
        VALUES (%s, %s, 'done', 1, %s, %s, 0, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (trace_id, version_id, model, 1200 + len(rendered.text) // 4, len(raw_output) // 4,
         ended_at, finished_at - timedelta(seconds=3), finished_at, scenario.analysis, raw_output),
    ).fetchone()
    (event_id,) = conn.execute(
        """
        INSERT INTO signal_events (run_id, trace_id, signal_version_id, finding_index, severity,
                                   payload, span_ids, evidence_labels, judge_input)
        VALUES (%s, %s, %s, 0, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (run_id, trace_id, version_id, scenario.severity, json.dumps(payload), span_ids,
         scenario.evidence, rendered.text),
    ).fetchone()
    return run_id, event_id


def add_critical_run(conn, project_id: int) -> str:
    version = latest_version(conn, project_id)
    run_id, _ = add_judged_turn(conn, project_id, version, refund_to_gift_card(),
                                str(uuid.uuid4()), datetime.now(timezone.utc))
    return f"Added run #{run_id} with one critical finding to {version[1]!r}"


def add_cluster(conn, project_id: int) -> str:
    """Three findings of one problem across two sessions (the clusterer's minimum), clustered."""
    version = latest_version(conn, project_id)
    signal_id = conn.execute("SELECT signal_id FROM signal_versions WHERE id = %s",
                             (version[0],)).fetchone()[0]
    now = datetime.now(timezone.utc)
    session_a, session_b = str(uuid.uuid4()), str(uuid.uuid4())
    members = [
        (session_a, now - timedelta(minutes=9), failed_account_lookup("Mei", "mei.ahmed78705@example.com", 0)),
        (session_a, now - timedelta(minutes=8), failed_account_lookup("Mei", "mei.ahmed78705@example.com", 1)),
        (session_b, now - timedelta(minutes=2), failed_account_lookup("Lucas", "lucas.brown9344@example.com", 2)),
    ]

    taken = {name for (name,) in conn.execute(
        "SELECT name FROM clusters WHERE signal_id = %s AND name IS NOT NULL", (signal_id,))}
    name = unique_name(members[0][2].summary, taken)
    (cluster_id,) = conn.execute(
        "INSERT INTO clusters (signal_id, name) VALUES (%s, %s) RETURNING id", (signal_id, name),
    ).fetchone()

    placeholder = vector_text([0.0] * EMBEDDING_SIZE)
    for session_id, ended_at, scenario in members:
        _, event_id = add_judged_turn(conn, project_id, version, scenario, session_id, ended_at)
        conn.execute(
            """
            INSERT INTO event_summaries (event_id, status, cause, summary, model, prompt_version,
                                         attempts, finished_at)
            VALUES (%s, 'done', %s, %s, %s, %s, 1, now())
            """,
            (event_id, scenario.cause, scenario.summary, version[3], SUMMARY_PROMPT_VERSION),
        )
        conn.execute(
            "INSERT INTO event_embeddings (event_id, model, embedding) VALUES (%s, 'slack-demo', %s::vector)",
            (event_id, placeholder),
        )
        conn.execute("INSERT INTO cluster_members (event_id, cluster_id) VALUES (%s, %s)",
                     (event_id, cluster_id))
    return f"Added cluster #{cluster_id} ({name!r}) with 3 findings to {version[1]!r}"


def main(argv=None):
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m tripwire.slack_demo",
                                     description="Add realistic fake findings that trigger a Slack alert.")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="project name (default: %(default)s)")
    parser.add_argument("kind", choices=["critical", "cluster"],
                        help="critical: a turn with a critical finding; cluster: a new named cluster")
    args = parser.parse_args(argv)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        project_id = find_project(conn, args.project)
        add = add_critical_run if args.kind == "critical" else add_cluster
        message = add(conn, project_id)
    print(message)


if __name__ == "__main__":
    main()
