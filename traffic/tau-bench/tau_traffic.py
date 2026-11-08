"""tau-traffic: generate customer-support traffic for Tripwire with tau2-bench.

Start it with the launcher in this folder (see README.md for setup):

    ./tau-traffic                                     # step-by-step wizard
    ./tau-traffic --domain retail -n 3 --yes          # flags skip questions
    ./tau-traffic view runs/<run>                     # open a run in tau2's viewer

The wizard prints the equivalent command at the end, so a run can be repeated exactly.
The launcher runs this file in tau2-bench's Python environment (uv run --project ...,
with tau2's "knowledge" extra, which banking_knowledge needs).

Conversations can be sent all at once, or spread over time (e.g. -n 40 --over 8h:
one about every 12 minutes), so Tripwire sees traffic arriving the way a real app's does.

Each run writes runs/<time>-<label>/ (saved as each conversation finishes, so a crash or
Ctrl+C keeps everything up to that point):
    answer_key.jsonl   one line per conversation: Laminar session ID, task, pass/fail,
                       how it ended, agent loops, cost
    summary.json       totals: pass rate, cost, how conversations ended
    transcripts.md     readable conversations (customer, agent, tool calls)
    results.json       tau2's full records, in tau2's own format: open it with
                       uv run tau2 view --file runs/<time>-<label>/results.json

Trace modes (what Laminar receives):
    turn          one trace per turn (root span "support_turn"), LLM + TOOL spans inside,
                  session ID = conversation ID. Like park_demo.
    conversation  one trace per conversation (root span "support_conversation").
    minimal       only Laminar.initialize(): one loose trace per agent LLM call,
                  no session ID, no tool spans.
In every mode the simulated customer and tau2's graders are kept out of Laminar.

Settings (.env in this folder or the shell): OPENAI_API_KEY, LMNR_PROJECT_API_KEY.
For self-hosted Laminar also LMNR_BASE_URL, LMNR_HTTP_PORT, LMNR_GRPC_PORT
(leave all three unset for Laminar Cloud).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shlex
import sys
import textwrap
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Domains offered, in the order the wizard shows them. The first one is the default.
#   run_config  extra tau2 run settings for the domain
#   uv_extra    tau2 optional dependencies the domain needs (uv run --extra ...). Every
#               printed command includes all of them, so it works for any domain.
DOMAINS = {
    "retail": {
        "about": "online store: order changes, returns, exchanges (114 tasks)",
    },
    "airline": {
        "about": "flight bookings, changes, cancellations (50 tasks)",
    },
    "banking_knowledge": {
        "about": "bank support that searches 698 policy documents, a RAG app (97 tasks)",
        # tau2's default search setup needs a sandboxed shell (npm + apt installs) and
        # OpenAI embeddings of every document. Keyword search + grep needs neither.
        "run_config": {"retrieval_config": "bm25_grep"},
        "uv_extra": "knowledge",
    },
    "telecom": {
        "about": "phone and mobile-data troubleshooting, hardest for the agent (114 tasks)",
    },
}
DEFAULT_DOMAIN = "retail"

MODES = {
    "turn": "one trace per turn",
    "conversation": "one trace per conversation",
    "minimal": "only Laminar.initialize(), loose LLM traces",
}

# Models offered in the wizard, with list prices per 1M tokens (input, cached input,
# output) for the cost estimate. Any other model name LiteLLM understands can be typed
# in with "Other"; its cost estimate shows as unknown.
MODELS = {
    "gpt-4o-mini": (0.15, 0.075, 0.60),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1": (2.00, 0.50, 8.00),
}
OTHER_MODEL = "__other__"

# Which API key each model provider needs. LiteLLM names models "<provider>/<model>";
# a name without a provider prefix (gpt-4o-mini) is OpenAI. To support another
# provider, add it here (e.g. "anthropic": "ANTHROPIC_API_KEY") and optionally add
# its models to MODELS.
PROVIDER_KEYS = {
    "openai": "OPENAI_API_KEY",
}

DEFAULTS = {
    "num": 3,
    "mode": "turn",
    "agent_model": "gpt-4o-mini",
    "customer_model": "gpt-4.1-mini",
    "app_version": "v1",
    "workers": 3,
    "max_steps": 80,
    "budget": 1.00,
}
SEED_RANGE = (1, 9999)  # default seed is picked at random from this range
SPACINGS = ("regular", "random")

# Typical tokens per conversation, for the estimate shown before a run. The run's
# summary reports the real cost tau2 measured. Calibrated Nov 8, 2026 from real runs
# (gpt-4o-mini agent, gpt-4.1-mini customer): retail ~$0.009 (8 conversations) and
# banking ~$0.009 (3) per conversation. The agent re-sends its policy + tool descriptions (~5K tokens in
# retail) on every LLM call and makes 10-25 calls per conversation, which is why the
# agent is most of the cost. Airline is assumed to match retail; telecom is a guess
# (its only real run looped before the agent loop guard existed).
_RETAIL_AGENT = {"input": 70_000, "cached_share": 0.9, "output": 1_500}
_CUSTOMER = {"input": 7_000, "cached_share": 0.3, "output": 250}
TOKEN_PROFILES = {
    "retail": {"agent": _RETAIL_AGENT, "customer": _CUSTOMER},
    "airline": {"agent": _RETAIL_AGENT, "customer": _CUSTOMER},
    "banking_knowledge": {
        "agent": {"input": 60_000, "cached_share": 0.9, "output": 1_500},
        "customer": _CUSTOMER,
    },
    "telecom": {
        "agent": {"input": 120_000, "cached_share": 0.9, "output": 1_500},
        "customer": {"input": 10_000, "cached_share": 0.3, "output": 300},
    },
}

# The only LLM call that belongs to "the app"; every other tau2 call_name
# (user_simulator_response, *_eval, *_judge*, ...) is the test harness.
TRACED_CALL_NAMES = {"agent_response"}

# Agent loop guard: the same tool call (same tool, same arguments) this many times in
# one turn is not run; the agent sends FALLBACK_REPLY instead and the turn ends.
AGENT_LOOP_LIMIT = 3
FALLBACK_REPLY = ("Sorry, I'm having trouble looking that up right now. "
                  "Could you tell me a bit more, or try again in a moment?")

# --------------------------------------------------------------------------- help texts

HELP = {
    "send": (
        "All at once: conversations start right away, a few at a time. Spread over "
        "time: they start one by one across a period you choose (e.g. 40 over 8 hours), "
        "so Tripwire sees traffic arriving the way a real app's does and its charts "
        "show trends over time."
    ),
    "over": (
        "How long to spread the conversations over, e.g. 30m, 8h, 2d or 1h30m. The "
        "first conversation starts right away. Results and traces are saved as each "
        "conversation finishes, so you can stop with Ctrl+C at any point and keep "
        "everything so far."
    ),
    "spacing": (
        "Regular: evenly spaced, e.g. exactly every 12 minutes. Random: random start "
        "times across the period at the same average rate, so some arrive close "
        "together and some far apart, more like real traffic. The seed fixes the "
        "random pattern, so a run can be repeated."
    ),
    "keep_awake": (
        "Stops the computer from going to sleep until the run ends (Linux: "
        "systemd-inhibit, macOS: caffeinate), then lets it sleep normally again. If "
        "it sleeps anyway, the run pauses and continues on waking, keeping the same "
        "spacing from there: missed conversations are never sent in a burst, so the "
        "run just ends later."
    ),
    "other_model": (
        "Type any model name LiteLLM understands, e.g. gpt-4.1-nano or "
        "openai/gpt-5-mini. Models from other providers use a prefix "
        "(anthropic/..., gemini/...) and need that provider's API key in .env; "
        "this tool only checks keys for providers it knows (OpenAI for now). "
        "The cost estimate can't price models outside the list, so it shows "
        "'unknown'; the run's summary still reports the real cost."
    ),
    "label": (
        "A name for this run. It names the output folder (runs/<time>-<label>/) and "
        "is saved in summary.json and the printed command, so you can tell runs apart "
        "later, e.g. 'before-fix' and 'after-fix'. It stays on this machine unless "
        "you choose to send it to Laminar (an advanced setting)."
    ),
    "app_version": (
        "A release name for the app under test, added to every trace's metadata "
        "(app_version). Tripwire can mark versions on its charts, e.g. run v1 with a "
        "bug, then v2 without it, and check the event rate drops after v2."
    ),
    "seed": (
        "Same seed = same tasks in the same order, so a run can be repeated. "
        "Conversations still vary (LLMs aren't fully repeatable). The default is "
        "a random seed; it's saved in the summary and the printed command."
    ),
    "workers": "How many conversations run at the same time. More is faster, "
               "but may hit OpenAI rate limits. When spreading over time, this is how "
               "many may overlap if one runs longer than the spacing.",
    "max_steps": (
        "Safety net: a conversation ends after this many messages, counting tool "
        "calls and results. Normal conversations stay well under it; it stops "
        "runaway loops the guards don't catch."
    ),
    "budget": (
        "A spending cap for this run, counting what tau2 measured for each finished "
        "conversation (agent + simulated customer). It's checked before each "
        "conversation starts: once the total reaches the budget, the remaining "
        "conversations are skipped (marked skipped_budget). Conversations already "
        "running still finish, so a run can go over by up to as many conversations "
        "as run at once."
    ),
    "loop_guard": (
        "Customer loop guard: ends a conversation when the simulated customer sends "
        "the same message twice in a row (e.g. 'Goodbye!' back and forth). Without "
        "it, such loops run to the step limit, filling Laminar with junk traces and "
        "costing money. Marked loop_guard in the answer key."
    ),
    "agent_loop_guard": (
        f"Agent loop guard: stops the agent from making the same tool call (same "
        f"tool, same arguments) a {AGENT_LOOP_LIMIT}rd time in one turn. That call "
        f"isn't run; the agent replies with a fallback message ('Sorry, I'm having "
        f"trouble looking that up...') and the customer reacts, like a real app's "
        f"cap on tool calls per turn. Without it, a stuck agent runs to the step "
        f"limit and the customer never gets an answer. Counted as agent_loops in the "
        f"answer key; never sent to Laminar."
    ),
    "send_label": (
        "Adds this run's label to every trace's metadata in Laminar (run_label), so "
        "you can find or filter one batch there, e.g. compare 'before-fix' and "
        "'after-fix' runs, or find a test batch to ignore. Leave it off when the "
        "label hints at expected results (like a bug name): anyone or any judge "
        "looking at the traces would see it. Default: No (the label stays on this "
        "machine)."
    ),
}


# =========================================================================== settings


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog=os.environ.get("TAU_TRAFFIC_LAUNCHER") or "tau_traffic.py",
        description="Generate customer-support traffic for Tripwire with tau2-bench.",
    )
    p.add_argument("--domain", choices=list(DOMAINS))
    p.add_argument("-n", "--num", type=int, help="number of conversations")
    p.add_argument("--over", metavar="DURATION",
                   help="spread the conversations over this long, e.g. 30m, 8h, 2d, 1h30m "
                        "(default: all at once)")
    p.add_argument("--spacing", choices=SPACINGS,
                   help="with --over: regular (evenly spaced, default) or random")
    p.add_argument("--no-keep-awake", action="store_true",
                   help="with --over: let the computer sleep during the run")
    p.add_argument("--mode", choices=list(MODES), help="trace shape sent to Laminar")
    p.add_argument("--agent-model", help="model for the support agent (the app)")
    p.add_argument("--customer-model", help="model for the simulated customer")
    p.add_argument("--label", help="run name, used in the output folder name")
    p.add_argument("--app-version", help="added to trace metadata as app_version")
    p.add_argument("--seed", type=int, help="same seed = same tasks in the same order "
                                            "(default: random)")
    p.add_argument("--workers", type=int, help="conversations running at once")
    p.add_argument("--max-steps", type=int, help="safety limit on messages per conversation")
    p.add_argument("--budget", type=float, help="stop starting conversations past this cost ($)")
    p.add_argument("--no-loop-guard", action="store_true",
                   help="don't end conversations where the customer repeats themselves")
    p.add_argument("--no-agent-loop-guard", action="store_true",
                   help=f"don't stop the agent repeating the same tool call "
                        f"{AGENT_LOOP_LIMIT} times in a turn")
    p.add_argument("--send-label", action="store_true",
                   help="add the run label to trace metadata in Laminar (run_label)")
    p.add_argument("--no-send", action="store_true", help="run without sending to Laminar")
    p.add_argument("--dry-run", action="store_true", help="show the plan and estimate only")
    p.add_argument("-y", "--yes", action="store_true",
                   help="no questions: use flags, defaults for the rest, start right away")
    return p.parse_args(argv)


def random_seed():
    return random.randint(*SEED_RANGE)


_DURATION_UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(text):
    """'8h' -> 28800 seconds. Accepts d, h, m (and s), combined like 1h30m."""
    cleaned = (text or "").strip().lower().replace(" ", "")
    parts = re.findall(r"(\d+(?:\.\d+)?)([dhms])", cleaned)
    if not parts or "".join(n + u for n, u in parts) != cleaned:
        raise ValueError(f"Can't read the duration '{text}': use e.g. 30m, 8h, 2d or 1h30m")
    seconds = sum(float(n) * _DURATION_UNITS[u] for n, u in parts)
    if seconds <= 0:
        raise ValueError("The duration must be longer than 0")
    return seconds


def format_duration(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, s = divmod(seconds, 60)
    hours, m = divmod(minutes, 60)
    days, h = divmod(hours, 24)
    out = "".join(f"{v}{u}" for v, u in ((days, "d"), (h, "h"), (m, "min")) if v)
    return out + (f"{s}s" if s and not days and not h else "")


def schedule_text(args):
    """One line describing when conversations start, e.g. for 40 over 8h."""
    period = parse_duration(args.over)
    end = datetime.fromtimestamp(time.time() + period)
    if args.spacing == "random":
        return (f"{args.num} conversations over {args.over}: random times, about one every "
                f"{format_duration(period / args.num)} on average, until around {end:%H:%M}"
                f"{' tomorrow' if end.date() > datetime.now().date() else ''}")
    last = datetime.fromtimestamp(time.time() + period / args.num * (args.num - 1))
    later = " tomorrow" if last.date() > datetime.now().date() else ""
    return (f"{args.num} conversations over {args.over}: one every "
            f"{format_duration(period / args.num)}, last one starts around "
            f"{last:%H:%M}{later}")


def ask_missing(args):
    """Ask, one question at a time, for every setting not given as a flag."""
    import questionary

    def explain(key):
        for line in textwrap.wrap(HELP[key], width=78):
            questionary.print(f"  {line}", style="fg:ansibrightblack")

    def done(result):
        if result is None:  # Ctrl+C
            sys.exit(1)
        return result

    def choose(message, choices, default):
        return done(questionary.select(message, choices=choices, default=default).ask())

    def text(message, default, validate=None):
        return done(questionary.text(message, default=str(default), validate=validate).ask())

    def confirm(message, default):
        return done(questionary.confirm(message, default=default).ask())

    def is_pos_int(v):
        return v.isdigit() and int(v) > 0 or "Enter a whole number above 0"

    def is_money(v):
        try:
            return float(v) > 0 or "Enter an amount above 0"
        except ValueError:
            return "Enter an amount, e.g. 1.00"

    def not_empty(v):
        return bool(v.strip()) or "Enter a model name"

    def is_duration(v):
        try:
            parse_duration(v)
            return True
        except ValueError as e:
            return str(e)

    def choose_model(message, default):
        choices = [questionary.Choice(m, value=m) for m in MODELS]
        choices.append(questionary.Choice("Other (type a model name)", value=OTHER_MODEL))
        model = choose(message, choices, default)
        if model == OTHER_MODEL:
            explain("other_model")
            model = text("Model name:", "", not_empty).strip()
        return model

    if args.domain is None:
        args.domain = choose(
            "Domain:",
            [questionary.Choice(f"{d}: {info['about']}", value=d) for d, info in DOMAINS.items()],
            DEFAULT_DOMAIN,
        )
    spread_given = args.over is not None
    if not spread_given and args.spacing is None:
        explain("send")
        spread = choose("How should conversations be sent?",
                        [questionary.Choice("all at once", value=False),
                         questionary.Choice("spread over time", value=True)], False)
    else:
        spread = spread_given
    if args.num is None:
        args.num = int(text("Conversations:", DEFAULTS["num"], is_pos_int))
    if spread:
        if args.over is None:
            explain("over")
            args.over = text("Over how long?", "1h", is_duration).strip().lower()
        if args.spacing is None:
            explain("spacing")
            args.spacing = choose("Spacing:", list(SPACINGS), "regular")
        questionary.print(f"  → {schedule_text(args)}", style="bold")
        if not args.no_keep_awake:
            explain("keep_awake")
            args.no_keep_awake = not confirm("Keep the computer awake while running?",
                                             default=True)
    if args.mode is None:
        args.mode = choose("Trace mode:", [questionary.Choice(f"{m}: {d}", value=m)
                                           for m, d in MODES.items()], DEFAULTS["mode"])
    if args.agent_model is None:
        args.agent_model = choose_model("Agent model (the app):", DEFAULTS["agent_model"])
    if args.customer_model is None:
        args.customer_model = choose_model("Customer model (simulated customer):",
                                           DEFAULTS["customer_model"])
    if args.label is None:
        explain("label")
        args.label = text("Run label:", f"{args.domain}-{args.mode}")
    if args.app_version is None:
        explain("app_version")
        args.app_version = text("App version:", DEFAULTS["app_version"])

    advanced = (args.seed, args.workers, args.max_steps, args.budget)
    if all(v is None for v in advanced):
        if confirm("Change advanced settings (seed, workers, step limit, budget, "
                   "loop guards, label in Laminar)?", default=False):
            explain("seed")
            args.seed = int(text("Seed:", random_seed(), is_pos_int))
            explain("workers")
            args.workers = int(text("Conversations at once:", DEFAULTS["workers"], is_pos_int))
            explain("max_steps")
            args.max_steps = int(text("Step limit per conversation:", DEFAULTS["max_steps"],
                                      is_pos_int))
            explain("budget")
            args.budget = float(text("Budget ($):", f"{DEFAULTS['budget']:.2f}", is_money))
            explain("loop_guard")
            args.no_loop_guard = not confirm("Customer loop guard on?", default=True)
            explain("agent_loop_guard")
            args.no_agent_loop_guard = not confirm("Agent loop guard on?", default=True)
            explain("send_label")
            args.send_label = confirm("Send the run label to Laminar? (default: No)",
                                      default=False)
    return args


def fill_defaults(args):
    if args.domain is None:
        args.domain = DEFAULT_DOMAIN
    for key, value in DEFAULTS.items():
        if getattr(args, key) is None:
            setattr(args, key, value)
    if args.seed is None:
        args.seed = random_seed()
    if args.over:
        args.over = args.over.strip().lower().replace(" ", "")
        if args.spacing is None:
            args.spacing = "regular"
    if args.label is None:
        args.label = f"{args.domain}-{args.mode}"
    return args


# Set by the ./tau-traffic launcher: printed commands then use the launcher too.
LAUNCHER = os.environ.get("TAU_TRAFFIC_LAUNCHER")


def equivalent_command(args):
    if LAUNCHER:
        parts = [LAUNCHER]
    else:
        parts = ["uv", "run"]
        for extra in sorted({d["uv_extra"] for d in DOMAINS.values() if d.get("uv_extra")}):
            parts += ["--extra", extra]
        parts += ["--with", "lmnr==0.8.9", "--with", "questionary", "python", "tau_traffic.py"]
    parts += ["--domain", args.domain, "-n", str(args.num)]
    if args.over:
        parts += ["--over", args.over, "--spacing", args.spacing]
        if args.no_keep_awake:
            parts.append("--no-keep-awake")
    parts += ["--mode", args.mode,
              "--agent-model", args.agent_model, "--customer-model", args.customer_model,
              "--label", args.label, "--app-version", args.app_version,
              "--seed", str(args.seed), "--workers", str(args.workers),
              "--max-steps", str(args.max_steps), "--budget", f"{args.budget:.2f}"]
    if args.no_loop_guard:
        parts.append("--no-loop-guard")
    if args.no_agent_loop_guard:
        parts.append("--no-agent-loop-guard")
    if args.send_label:
        parts.append("--send-label")
    if args.no_send:
        parts.append("--no-send")
    parts.append("--yes")
    return shlex.join(parts)


def estimate_cost(args):
    """Rough $ for the run, or None if a model has no price listed."""
    profile = TOKEN_PROFILES[args.domain]
    total = 0.0
    for model, tokens in ((args.agent_model, profile["agent"]),
                          (args.customer_model, profile["customer"])):
        if model not in MODELS:
            return None
        price_in, price_cached, price_out = MODELS[model]
        cached = tokens["input"] * tokens["cached_share"]
        fresh = tokens["input"] - cached
        total += (fresh * price_in + cached * price_cached + tokens["output"] * price_out) / 1e6
    return total * args.num


def provider_of(model):
    """LiteLLM provider name for a model: 'openai/gpt-4o' -> openai, 'gpt-4o' -> openai."""
    return model.split("/", 1)[0].lower() if "/" in model else "openai"


def missing_keys(args):
    """(API keys that are needed but not set, providers whose keys we can't check)."""
    missing, unknown = [], []
    for model in {args.agent_model, args.customer_model}:
        provider = provider_of(model)
        key = PROVIDER_KEYS.get(provider)
        if key is None:
            unknown.append(provider)
        elif not os.environ.get(key):
            missing.append(key)
    return sorted(set(missing)), sorted(set(unknown))


def laminar_target():
    base = os.environ.get("LMNR_BASE_URL")
    return f"self-hosted Laminar at {base}" if base else "Laminar Cloud"


def print_plan(args):
    estimate = estimate_cost(args)
    est = f"~${estimate:.2f}" if estimate is not None else "unknown (model not in price list)"
    guards = []
    guards.append("customer loop guard " + ("off" if args.no_loop_guard else "on"))
    guards.append("agent loop guard " + ("off" if args.no_agent_loop_guard else "on"))
    print()
    print(f"  {args.num} {args.domain} conversations · {args.mode} mode · "
          f"agent {args.agent_model} · customer {args.customer_model}")
    print(f"  Sending to: {'nothing (--no-send)' if args.no_send else laminar_target()}"
          f" · app_version {args.app_version}"
          f"{' · run_label ' + args.label if args.send_label else ''}")
    if args.over:
        print(f"  Spread: {schedule_text(args)}"
              f"{'' if args.no_keep_awake else ' · keeping the computer awake'}")
    else:
        print("  All at once")
    print(f"  Seed {args.seed} · step limit {args.max_steps} · {' · '.join(guards)} · "
          f"budget ${args.budget:.2f}")
    print(f"  Estimated cost: {est} (rough; the summary reports the real cost)")
    if estimate is not None and estimate > args.budget:
        print(f"  ⚠ The estimate is above the ${args.budget:.2f} budget: the run will likely "
              f"stop early. Raise it with --budget (or in advanced settings).")
    print(f"  Same run as a command:\n    {equivalent_command(args)}")
    print()


# =========================================================================== Laminar


def init_laminar(app_version, run_label=None):
    from lmnr import Laminar

    metadata = {"app_version": app_version}
    if run_label:
        metadata["run_label"] = run_label
    Laminar.initialize(
        project_api_key=os.environ["LMNR_PROJECT_API_KEY"],
        base_url=os.environ.get("LMNR_BASE_URL") or None,
        http_port=int(os.environ["LMNR_HTTP_PORT"]) if os.environ.get("LMNR_HTTP_PORT") else None,
        grpc_port=int(os.environ["LMNR_GRPC_PORT"]) if os.environ.get("LMNR_GRPC_PORT") else None,
        metadata=metadata,
    )


@contextmanager
def untraced():
    """LiteLLM calls made inside this block don't create Laminar spans."""
    # Internal Laminar SDK pieces (tested with lmnr 0.8.9). Laminar's own openai-agents
    # integration uses this same key to stop a LiteLLM call from being traced.
    from lmnr.opentelemetry_lib.opentelemetry.instrumentation.shared.context_keys import (
        DISABLE_LITELLM_INSTRUMENTATION_CONTEXT_KEY,
    )
    from lmnr.opentelemetry_lib.tracing.context import (
        attach_context,
        detach_context,
        get_current_context,
    )
    from opentelemetry.context import set_value

    token = attach_context(
        set_value(DISABLE_LITELLM_INSTRUMENTATION_CONTEXT_KEY, True, get_current_context())
    )
    try:
        yield
    finally:
        detach_context(token)


# Set by the agent loop guard: the next agent LLM call in this thread is replaced by
# FALLBACK_REPLY. Each conversation runs in one worker thread, so this is per conversation.
_pending_fallback = threading.local()


def keep_harness_untraced():
    """Wrap tau2's single LLM entry point so only the agent's calls are traced.

    Also where the agent loop guard's fallback reply replaces the agent's next LLM call.
    """
    import tau2.utils.llm_utils as llm_utils
    from tau2.data_model.message import AssistantMessage

    original = llm_utils._generate_once

    def _generate_once(*args, call_name=None, **kwargs):
        if call_name in TRACED_CALL_NAMES:
            if getattr(_pending_fallback, "active", False):
                _pending_fallback.active = False
                return AssistantMessage.text(FALLBACK_REPLY, cost=0.0)
            return original(*args, call_name=call_name, **kwargs)
        with untraced():
            return original(*args, call_name=call_name, **kwargs)

    llm_utils._generate_once = _generate_once


# =========================================================================== conversation hooks


def _normalize(text):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", (text or "").lower())).strip()


class RunNotes:
    """What the guards did, per conversation (simulation ID). Local only, never traced."""

    def __init__(self):
        self._lock = threading.Lock()
        self._stops = {}
        self._agent_loops = Counter()

    def record_stop(self, simulation_id, reason):
        with self._lock:
            self._stops[simulation_id] = reason

    def stop(self, simulation_id):
        with self._lock:
            return self._stops.get(simulation_id)

    def add_agent_loop(self, simulation_id):
        with self._lock:
            self._agent_loops[simulation_id] += 1

    def agent_loops(self, simulation_id):
        with self._lock:
            return self._agent_loops[simulation_id]


def install_hooks(mode, send, loop_guard, agent_loop_guard, notes):
    """Hook tau2's conversation loop: loop guards (all modes) and spans (turn/conversation).

    tau2's orchestrator plays the role of the app's main loop. A real app would get the
    same spans with @observe() on its turn handler and tools.
    """
    from tau2.data_model.message import AssistantMessage, ToolMessage, UserMessage
    from tau2.data_model.simulation import TerminationReason
    from tau2.orchestrator.orchestrator import Orchestrator, Role

    structured = send and mode in ("turn", "conversation")
    if structured:
        from lmnr import Laminar
        from opentelemetry.trace import Status, StatusCode

    def end_span(span, output=None, error=None):
        if output is not None:
            span.set_output(output)
        if error is not None:
            span.set_status(Status(StatusCode.ERROR, error))
        span.end()

    original_run = Orchestrator.run
    original_step = Orchestrator.step
    original_execute = Orchestrator._execute_tool_calls

    def run(self):
        self._tt_turn = None
        self._tt_convo = None
        self._tt_last_reply = None
        self._tt_has_input = False
        self._tt_last_customer = None
        self._tt_turn_calls = Counter()  # agent tool calls in the current turn
        _pending_fallback.active = False
        if structured and mode == "conversation":
            self._tt_convo = Laminar.start_active_span(
                "support_conversation", session_id=self.simulation_id
            )
        error = None
        try:
            return original_run(self)
        except Exception as e:
            error = f"conversation raised: {e!r}"
            raise
        finally:
            _pending_fallback.active = False
            # Spans must end in reverse order of starting: the open turn first.
            if self._tt_turn is not None:
                end_span(self._tt_turn, error=error or "conversation ended mid-turn")
                self._tt_turn = None
            if self._tt_convo is not None:
                end_span(self._tt_convo, output=self._tt_last_reply, error=error)
                self._tt_convo = None

    def step(self):
        customer_to_agent = self.from_role == Role.USER and self.to_role == Role.AGENT
        agent_step = self.to_role == Role.AGENT
        customer_step = self.to_role == Role.USER
        if structured and customer_to_agent:
            text = self.message.content
            if mode == "turn" and self._tt_turn is None:
                self._tt_turn = Laminar.start_active_span(
                    "support_turn", input=text, session_id=self.simulation_id
                )
            if self._tt_convo is not None and not self._tt_has_input:
                self._tt_convo.set_input(text)
                self._tt_has_input = True

        original_step(self)

        if customer_step and loop_guard and isinstance(self.message, UserMessage):
            msg = self.message
            if not self.done and not msg.is_tool_call():
                said = _normalize(msg.content)
                if said and said == self._tt_last_customer:
                    # Same message twice in a row (e.g. "Goodbye!" loops): treat it as the
                    # customer leaving, which is what the simulator should have signalled.
                    self.done = True
                    self.termination_reason = TerminationReason.USER_STOP
                    notes.record_stop(self.simulation_id, "loop_guard")
                self._tt_last_customer = said

        if agent_step and isinstance(self.message, AssistantMessage):
            reply = self.message
            if not reply.is_tool_call():
                self._tt_last_reply = reply.content
                self._tt_turn_calls.clear()  # the agent answered: a new turn starts
            # The turn ends when the agent answers the customer (or the conversation ends).
            if self._tt_turn is not None and (self.to_role == Role.USER or self.done):
                end_span(self._tt_turn, output=reply.content)
                self._tt_turn = None

    def blocked(call):
        """The agent loop guard's answer to a call it won't run.

        "rejected" is tau2's own status for calls the orchestrator refused: the
        grader doesn't replay them.
        """
        return ToolMessage(
            id=call.id, role="tool", requestor=call.requestor, error=True,
            execution_status="rejected",
            content=f"Not run: this exact call was already made "
                    f"{AGENT_LOOP_LIMIT - 1} times in this turn.",
        )

    def _execute_tool_calls(self, tool_calls):
        from_agent = self.from_role == Role.AGENT
        if not from_agent or not (structured or agent_loop_guard):
            return original_execute(self, tool_calls)
        results = []
        for call in tool_calls:
            if agent_loop_guard:
                key = (call.name, json.dumps(call.arguments, sort_keys=True, default=str))
                self._tt_turn_calls[key] += 1
                if self._tt_turn_calls[key] >= AGENT_LOOP_LIMIT:
                    # Don't run it; the agent's next reply becomes the fallback message.
                    results.append(blocked(call))
                    _pending_fallback.active = True
                    notes.add_agent_loop(self.simulation_id)
                    continue
            if structured:
                span = Laminar.start_active_span(call.name, input=call.arguments,
                                                 span_type="TOOL")
            result = self.environment.get_response(call)
            if result.error:
                self.num_errors += 1
            if structured:
                end_span(span, output=result.content,
                         error=result.content if result.error else None)
            results.append(result)
        return results

    Orchestrator.run = run
    Orchestrator.step = step
    Orchestrator._execute_tool_calls = _execute_tool_calls


# =========================================================================== running


def pick_tasks(domain, num, seed):
    """`num` tasks in a seeded random order (repeats only if num > tasks available)."""
    from tau2.runner import get_tasks

    tasks = get_tasks(domain, task_split_name="base")
    rng = random.Random(seed)
    picked = []
    while len(picked) < num:
        batch = tasks[:]
        rng.shuffle(batch)
        picked.extend(batch)
    return picked[:num]


def how_it_ended(sim, notes):
    if sim is None:
        return "error"
    if notes.stop(sim.id):
        return notes.stop(sim.id)
    return {
        "user_stop": "customer_stopped",
        "agent_stop": "agent_stopped",
        "max_steps": "step_limit",
    }.get(str(sim.termination_reason.value), str(sim.termination_reason.value))


def count_turns(sim):
    """Customer messages, not counting the simulator's stop signal."""
    return sum(
        1 for m in (sim.messages or [])
        if getattr(m, "role", None) == "user" and not m.is_tool_call() and m.content
        and "###STOP###" not in m.content
    )


def costs(sim):
    """(agent $, customer $). Summed per message: tau2's own totals become None
    when any single message lacks a cost (e.g. the scripted greeting)."""
    agent = sum(m.cost for m in sim.messages or []
                if getattr(m, "role", None) == "assistant" and getattr(m, "cost", None))
    customer = sum(m.cost for m in sim.messages or []
                   if getattr(m, "role", None) == "user" and getattr(m, "cost", None))
    return agent, customer


class Budget:
    def __init__(self, limit):
        self.limit = limit
        self.spent = 0.0
        self._lock = threading.Lock()

    def can_start(self):
        with self._lock:
            return self.spent < self.limit

    def add(self, cost):
        with self._lock:
            self.spent += cost or 0.0


def start_offsets(args, count):
    """Seconds after the run starts at which each conversation should start.

    All at once: every conversation at 0 (the worker limit spaces them out).
    Regular: evenly spaced; the first starts right away, the last one interval
    before the end of the period. Random: random times across the period (same
    average rate), fixed by the seed so a run can be repeated.
    """
    if not args.over:
        return [0.0] * count
    period = parse_duration(args.over)
    if args.spacing == "random":
        rng = random.Random(f"spacing-{args.seed}")
        return sorted([0.0] + [rng.uniform(0, period) for _ in range(count - 1)])[:count]
    interval = period / count
    return [i * interval for i in range(count)]


# A conversation that should have started more than this long ago (e.g. the computer
# slept, or all workers were busy) starts now, and the rest of the schedule moves back
# by the same amount: missed conversations are never started in a burst.
LATE_AFTER_SECONDS = 60


def _sleep_until(when, stop):
    while not stop.is_set():
        left = when - time.time()
        if left <= 0:
            return
        time.sleep(min(left, 0.5))  # short naps so Ctrl+C is handled promptly


def run_conversations(args, tasks, notes, writer):
    import signal

    from tau2.data_model.simulation import TextRunConfig
    from tau2.runner import run_single_task

    config = TextRunConfig(
        domain=args.domain,
        llm_agent=args.agent_model,
        llm_args_agent={},
        llm_user=args.customer_model,
        llm_args_user={},
        max_steps=args.max_steps,
        log_level="ERROR",
        **DOMAINS[args.domain].get("run_config", {}),
    )
    writer.config = config
    spread = bool(args.over)
    budget = Budget(args.budget)
    slots = threading.Semaphore(args.workers)
    stop = threading.Event()
    handled = set()  # indexes started or skipped
    running = set()  # indexes of conversations in progress
    running_lock = threading.Lock()

    # Ctrl+C: the first press stops new conversations and lets running ones finish;
    # the second saves what's done and quits right away.
    presses = []

    def on_ctrl_c(signum, frame):
        presses.append(time.time())
        if len(presses) == 1:
            stop.set()
            print("\n  Stopping: no new conversations; waiting for the ones running to "
                  "finish (Ctrl+C again to quit now; results so far are already saved)...",
                  flush=True)
            return
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # don't interrupt the final save
        with running_lock:
            cut_off = sorted(running)
        writer.finish([(j, t) for j, t in enumerate(tasks) if j not in handled],
                      interrupted=[(j, tasks[j]) for j in cut_off])
        print(f"\n  Quit. Results are saved in {writer.out_dir}/ (conversations that were "
              f"still running are marked interrupted).", flush=True)
        os._exit(130)  # don't wait for running conversations' threads

    previous_handler = signal.signal(signal.SIGINT, on_ctrl_c)

    def one(i, task, started_at):
        try:
            sim = run_single_task(config, task, seed=args.seed + i)
            budget.add(sum(costs(sim)))
            writer.add(i, task, started_at, sim=sim)
        except Exception as e:  # one broken conversation shouldn't stop the run
            writer.add(i, task, started_at, error=repr(e))
        finally:
            with running_lock:
                running.discard(i)
            if spread and not args.no_send:
                from lmnr import Laminar

                Laminar.flush()  # so Tripwire sees each conversation as it finishes
            slots.release()

    offsets = start_offsets(args, len(tasks))
    t0 = time.time()
    shift = 0.0
    pool = ThreadPoolExecutor(max_workers=args.workers)
    try:
        for i, task in enumerate(tasks):
            target = t0 + offsets[i] + shift
            if time.time() > target + LATE_AFTER_SECONDS:
                shift += time.time() - target
                target = time.time()
            _sleep_until(target, stop)
            while not stop.is_set() and not slots.acquire(timeout=0.5):  # free worker
                pass
            if stop.is_set():
                break
            late = time.time() - target
            if late > LATE_AFTER_SECONDS:
                shift += late
            if not budget.can_start():
                slots.release()
                for j in range(i, len(tasks)):
                    writer.skip(j, tasks[j], "skipped_budget")
                    handled.add(j)
                break
            started_at = datetime.now()
            with running_lock:
                handled.add(i)
                running.add(i)
            pool.submit(one, i, task, started_at)
            if spread and i + 1 < len(tasks):
                nxt = datetime.fromtimestamp(t0 + offsets[i + 1] + shift)
                print(f"  [{i + 1}/{len(tasks)}] started task {task.id} · "
                      f"next at {nxt:%H:%M:%S}", flush=True)
            elif spread:
                print(f"  [{i + 1}/{len(tasks)}] started task {task.id} · last one",
                      flush=True)
        pool.shutdown(wait=True)
        writer.finish([(j, t) for j, t in enumerate(tasks) if j not in handled])
    finally:
        signal.signal(signal.SIGINT, previous_handler)
    return budget


# =========================================================================== keep awake


class KeepAwake:
    """Stops the computer from sleeping while a run is going, if the OS allows it.

    The helper process watches this program's process ID and exits when it does,
    so the lock is released even after a crash.
    """

    def __init__(self):
        self.proc = None
        self.note = None

    def start(self):
        import shutil
        import subprocess

        pid = str(os.getpid())
        if shutil.which("systemd-inhibit") and shutil.which("tail"):  # Linux
            cmd = ["systemd-inhibit", "--what=sleep:idle", "--who=tau-traffic",
                   "--why=Generating conversations over time", "--mode=block",
                   "tail", f"--pid={pid}", "-f", "/dev/null"]
        elif shutil.which("caffeinate"):  # macOS
            cmd = ["caffeinate", "-i", "-w", pid]
        else:
            self.note = ("couldn't keep the computer awake on this system; "
                         "make sure it doesn't sleep during the run")
            return self
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE)
            time.sleep(0.5)
            if self.proc.poll() is not None:  # exited right away: not allowed or failed
                err = (self.proc.stderr.read() or b"").decode(errors="replace").strip()
                self.proc = None
                self.note = f"couldn't keep the computer awake ({err or 'helper exited'})"
        except OSError as e:
            self.proc = None
            self.note = f"couldn't keep the computer awake ({e})"
        return self

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None


# =========================================================================== outputs


def view_command(out_dir):
    if LAUNCHER:
        return f"{LAUNCHER} view {out_dir}"
    return f"uv run tau2 view --file {out_dir / 'results.json'}"


def _short(value, limit=300):
    text = value if isinstance(value, str) else json.dumps(value)
    return text if len(text) <= limit else text[:limit] + " …"


def render_transcript(sim, notes):
    reward = sim.reward_info.reward if sim.reward_info else None
    verdict = "PASSED" if reward == 1.0 else "FAILED"
    loops = notes.agent_loops(sim.id)
    lines = [f"## Task {sim.task_id}: {verdict} ({how_it_ended(sim, notes)}"
             f"{f', {loops} agent loop' + ('s' if loops != 1 else '') if loops else ''})",
             f"Session ID: `{sim.id}`", ""]
    for m in sim.messages or []:
        role = getattr(m, "role", "")
        if role == "tool":
            tag = " (blocked by agent loop guard)" if getattr(
                m, "execution_status", None) == "rejected" else ""
            lines.append(f"> ↳ result{tag}: {_short(m.content or '')}")
        elif m.is_tool_call():
            who = "Agent" if role == "assistant" else "Customer"
            for call in m.tool_calls:
                lines.append(f"> **{who} calls** `{call.name}({_short(call.arguments, 200)})`")
        elif m.content:
            who = "**Agent:**" if role == "assistant" else "**Customer:**"
            lines.append(f"{who} {m.content}")
        lines.append("")
    lines += ["---", "", ""]
    return "\n".join(lines)


class OutputWriter:
    """Saves results as conversations finish, so a crash or Ctrl+C loses nothing.

    After every conversation: one line appended to answer_key.jsonl, its transcript
    appended to transcripts.md, summary.json rewritten. results.json (tau2's format,
    can get large) is rewritten at most once a minute, and always at the end.
    """

    RESULTS_EVERY_SECONDS = 60

    def __init__(self, args, notes, out_dir):
        self.args, self.notes, self.out_dir = args, notes, out_dir
        self.config = None  # set once tau2's run config exists
        self.rows, self.sims, self.tasks = [], [], []
        self.started = datetime.now()
        self.finished = None
        self._lock = threading.RLock()  # re-entrant: the Ctrl+C handler may call finish()
        self._results_saved_at = 0.0
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "answer_key.jsonl").write_text("")
        (out_dir / "transcripts.md").write_text("# Transcripts\n\n")
        self._write_summary()

    def _base_row(self, task):
        a = self.args
        return {"task_id": task.id, "domain": a.domain, "mode": a.mode,
                "app_version": a.app_version}

    def _append_row(self, row):
        self.rows.append(row)
        with open(self.out_dir / "answer_key.jsonl", "a") as f:
            f.write(json.dumps(row) + "\n")

    def add(self, i, task, started_at, sim=None, error=None):
        notes = self.notes
        with self._lock:
            row = self._base_row(task)
            row["started_at"] = started_at.isoformat(timespec="seconds")
            if sim is None:
                row.update({"session_id": None, "passed": None, "ended": "error",
                            "error": error})
                print(f"  [{i + 1}/{self.args.num}] task {task.id}: ERROR {error}", flush=True)
            else:
                reward = sim.reward_info.reward if sim.reward_info else None
                agent_cost, customer_cost = costs(sim)
                # tau2 leaves these empty when any message lacks a cost; its viewer reads them.
                sim.agent_cost, sim.user_cost = agent_cost, customer_cost
                turns, loops = count_turns(sim), notes.agent_loops(sim.id)
                row.update({
                    # In minimal mode traces carry no session ID; this ID is then only local.
                    "session_id": sim.id,
                    "passed": reward == 1.0 if reward is not None else None,
                    "reward": reward,
                    "ended": how_it_ended(sim, notes),
                    "turns": turns,
                    "agent_loops": loops,
                    "agent_cost": round(agent_cost, 6),
                    "customer_cost": round(customer_cost, 6),
                })
                self.sims.append(sim)
                self.tasks.append(task)
                with open(self.out_dir / "transcripts.md", "a") as f:
                    f.write(render_transcript(sim, notes))
                print(f"  [{i + 1}/{self.args.num}] task {task.id}: "
                      f"{'passed' if row['passed'] else 'failed'} · {row['ended']} · "
                      f"{turns} turn{'s' if turns != 1 else ''}"
                      f"{f' · {loops} agent loop' + ('s' if loops != 1 else '') if loops else ''}"
                      f" · ${agent_cost + customer_cost:.4f}", flush=True)
            self._append_row(row)
            self._write_summary()
            if time.time() - self._results_saved_at >= self.RESULTS_EVERY_SECONDS:
                self._write_results()

    def skip(self, i, task, reason):
        with self._lock:
            row = self._base_row(task)
            row.update({"session_id": None, "passed": None, "ended": reason})
            self._append_row(row)

    def finish(self, not_started, interrupted=()):
        """Record conversations that never started (or were cut off), then write the
        final files."""
        with self._lock:
            for reason, items in (("interrupted", interrupted), ("not_started", not_started)):
                for _, task in items:
                    row = self._base_row(task)
                    row.update({"session_id": None, "passed": None, "ended": reason})
                    self._append_row(row)
            self.finished = datetime.now()
            self._write_summary()
            self._write_results()

    def _write_results(self):
        """tau2's own results format, so its viewer (tau2 view) can open the run."""
        if self.config is None:
            return
        from tau2.data_model.simulation import Results
        from tau2.runner.helpers import get_info

        Results(info=get_info(self.config, tasks=list(self.tasks)), tasks=list(self.tasks),
                simulations=list(self.sims)).save(self.out_dir / "results.json")
        self._results_saved_at = time.time()

    def _write_summary(self):
        a, rows = self.args, self.rows
        done = [r for r in rows if r.get("session_id")]
        ended = Counter(r["ended"] for r in rows)
        total_cost = sum((r.get("agent_cost") or 0) + (r.get("customer_cost") or 0)
                         for r in done)
        summary = {
            "label": a.label, "domain": a.domain, "mode": a.mode,
            "agent_model": a.agent_model, "customer_model": a.customer_model,
            "app_version": a.app_version, "seed": a.seed, "max_steps": a.max_steps,
            "send": "spread over time" if a.over else "all at once",
            "over": a.over, "spacing": a.spacing if a.over else None,
            "keep_awake": (not a.no_keep_awake) if a.over else None,
            "loop_guard": not a.no_loop_guard,
            "agent_loop_guard": not a.no_agent_loop_guard,
            "label_sent": a.send_label and not a.no_send,
            "sent_to": None if a.no_send else laminar_target(),
            "started_at": self.started.isoformat(timespec="seconds"),
            "finished_at": self.finished.isoformat(timespec="seconds") if self.finished else None,
            "conversations_requested": a.num, "conversations_run": len(done),
            "passed": sum(1 for r in done if r["passed"]),
            "failed": sum(1 for r in done if r["passed"] is False),
            "ended": dict(ended),
            "agent_loops": sum(r.get("agent_loops") or 0 for r in done),
            "cost": {"total": round(total_cost, 4),
                     "agent": round(sum(r.get("agent_cost") or 0 for r in done), 4),
                     "customer": round(sum(r.get("customer_cost") or 0 for r in done), 4)},
            "command": equivalent_command(a),
            "view_command": view_command(self.out_dir),
        }
        (self.out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        self.summary = summary


def print_summary(summary, out_dir):
    run = summary["conversations_run"]
    print()
    print(f"  Done: {run} of {summary['conversations_requested']} conversations · "
          f"{summary['passed']} passed, {summary['failed']} failed · "
          f"${summary['cost']['total']:.4f}")
    print(f"  How they ended: "
          + ", ".join(f"{k} {v}" for k, v in sorted(summary["ended"].items())))
    if summary["agent_loops"]:
        print(f"  Agent loop guard stepped in {summary['agent_loops']} time(s)")
    print(f"  Outputs: {out_dir}/ (answer_key.jsonl, summary.json, transcripts.md, "
          f"results.json)")
    print(f"  View in tau2's viewer: {summary['view_command']}")


# =========================================================================== main


def check_ready(args):
    """Fail before starting, not at the first conversation."""
    missing, unknown = missing_keys(args)
    if missing:
        sys.exit(f"{', '.join(missing)} is not set (add it to .env).")
    for provider in unknown:
        print(f"  Note: can't check the API key for '{provider}' models; "
              f"make sure it's in .env.")
    if not args.no_send and not os.environ.get("LMNR_PROJECT_API_KEY"):
        sys.exit("LMNR_PROJECT_API_KEY is not set (add it to .env), or use --no-send.")
    extra = DOMAINS[args.domain].get("uv_extra")
    if extra == "knowledge":
        try:
            import rank_bm25  # noqa: F401
        except ImportError:
            sys.exit(f"{args.domain} needs tau2's '{extra}' extra: start the tool with "
                     f"'uv run --extra {extra} ...' (the command printed above includes it).")


def main(argv=None):
    load_dotenv()
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.over:
        try:
            parse_duration(args.over)
        except ValueError as e:
            sys.exit(str(e))
    if not args.yes and sys.stdin.isatty():
        args = ask_missing(args)
    args = fill_defaults(args)

    print_plan(args)
    if args.dry_run:
        return 0
    check_ready(args)
    if not args.yes and sys.stdin.isatty():
        import questionary

        if not questionary.confirm("Start?", default=True).ask():
            return 1

    # tau2 logs every step at DEBUG level; keep only real problems on screen.
    from loguru import logger

    logger.remove()
    logger.add(sys.stderr, level="ERROR")

    # Laminar must be initialized before tau2 is imported, so tau2's LiteLLM calls
    # go through Laminar's instrumentation.
    if not args.no_send:
        init_laminar(args.app_version, run_label=args.label if args.send_label else None)
    notes = RunNotes()
    keep_harness_untraced()
    install_hooks(args.mode, send=not args.no_send, loop_guard=not args.no_loop_guard,
                  agent_loop_guard=not args.no_agent_loop_guard, notes=notes)

    tasks = pick_tasks(args.domain, args.num, args.seed)
    out_dir = Path("runs") / f"{datetime.now():%Y%m%d-%H%M%S}-{args.label}"
    writer = OutputWriter(args, notes, out_dir)
    awake = KeepAwake().start() if args.over and not args.no_keep_awake else None
    if awake and awake.note:
        print(f"  Note: {awake.note}.")
    print(f"  Running {len(tasks)} conversation{'s' if len(tasks) != 1 else ''}"
          f"{' over ' + args.over if args.over else ''}... (results are saved as each one "
          f"finishes)", flush=True)
    started = time.time()
    try:
        budget = run_conversations(args, tasks, notes, writer)
    finally:
        if awake:
            awake.stop()
        if not args.no_send:
            from lmnr import Laminar

            Laminar.flush()
    if any(r["ended"] == "skipped_budget" for r in writer.rows):
        print(f"  Budget of ${args.budget:.2f} reached: some conversations were skipped.")
    print_summary(writer.summary, out_dir)
    print(f"  Took {format_duration(time.time() - started)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
