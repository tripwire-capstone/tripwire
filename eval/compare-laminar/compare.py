"""Compare Tripwire's signals against Laminar Cloud's own Signals on the same traffic.

    ./compare-laminar --traffic park --n 50 --label glacier-fix-attempt-1
    ./compare-laminar --traffic tau --n 20 --label tau-smoke

One run = one traffic source (never both; they need different ground truth, see README.md
"Reading a report"). Walks through the manual setup once (wizard()), then runs traffic, waits
for both systems to settle, snapshots, compares, and writes runs/<label>/.

Status (2026-11-16): park-traffic's ground truth (planted bug) and tau-bench's two ground
truths (pass/fail, raw-JSON-reply) are all built — each traffic type gets however many
ground-truth dimensions make sense for it, each crosstabbed against agreement independently.
The raw-JSON-reply check reads straight from Laminar's own spans table rather than Tripwire's
judge_input, so it doesn't depend on a rendering bug filed against tripwire/judge/render.py
(2026-11-16: it silently drops assistant content for any LLM call shaped like a raw OpenAI
response rather than LiteLLM's — see the Jira ticket for the full writeup) being fixed first.
Only piece still NOT done: the noise baseline (needs a second Tripwire judging pass over the
same traces; that feature doesn't exist yet) — left out of the report with a note rather than
faked.

Everything through compare() was smoke-tested end-to-end on 2026-11-15 — a real Laminar trace,
inserted into a throwaway row in the (shared, pre-existing) dev DB, came back correctly matched
in both the agreement counts and the cluster crosstab. That test caught and fixed a real bug:
psycopg returns `laminar_trace_id` as a uuid.UUID, Laminar's SQL API returns the same id as a
plain string, and comparing them un-cast silently matched nothing (see the comment in
snapshot_tripwire()). The tau-bench ground-truth code added 2026-11-16 has NOT yet been run
against a live tau-bench batch — it's been reviewed and is believed correct, but treat it as
unverified until it's actually exercised.

SIGNAL_PAIRS has three real entries for the current project; edit per run.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

HERE = Path(__file__).resolve().parent
# Reuses the poller's Laminar SQL client (retry/backoff on 429/502/503/504) instead of
# duplicating it — this one module has no dependency on the rest of the backend package.
sys.path.insert(0, str(HERE.parent.parent / "signals" / "backend"))
from tripwire.poller.laminar import LaminarSQL  # noqa: E402
SIGNALS_DIR = HERE / "signals"
RUNS_DIR = HERE / "runs"

SETTLE_POLL_SECONDS = 10
SETTLE_MAX_WAIT_SECONDS = 30 * 60


def main():
    load_dotenv(HERE / ".env")
    args = parse_args()
    ask_run_parameters(args)

    run_dir = RUNS_DIR / args.label
    if run_dir.exists():
        sys.exit(f"runs/{args.label} already exists — pick a different --label")
    run_dir.mkdir(parents=True)

    wizard(args)

    if args.skip_traffic:
        started_at = datetime.fromisoformat(args.since)
        print(f"\n--- skipping traffic send; comparing traffic already sent since {args.since} ---")
    else:
        started_at = datetime.now(timezone.utc)
        print(f"\n--- sending traffic ({args.traffic}, n={args.n}) ---")
        send_traffic(args)

    print("\n--- waiting for both systems to settle ---")
    wait_for_settle()
    ended_at = datetime.now(timezone.utc)

    print("\n--- snapshotting ---")
    snapshot = {
        "label": args.label,
        "traffic": args.traffic,
        "taken_at": ended_at.isoformat(),
        "tripwire": snapshot_tripwire(),
        "laminar": snapshot_laminar(started_at, ended_at, fetch_llm_texts=(args.traffic == "tau")),
    }
    if args.traffic == "park":
        snapshot["answer_key"] = load_answer_key()
    else:
        snapshot["tau_results"] = load_tau_results()

    (run_dir / "raw.json").write_text(json.dumps(snapshot, indent=2, default=str))

    print("\n--- comparing ---")
    report_text = render_report(compare(snapshot), snapshot)
    (run_dir / "report.md").write_text(report_text)

    print(f"\nDone: runs/{args.label}/report.md")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--traffic", choices=["park", "tau"], required=True,
                    help="traffic source for this run — never both in one run")
    p.add_argument("--n", type=int, help="conversations to send — asked interactively if "
                   "omitted (unless --skip-wizard, where it defaults to 10)")
    p.add_argument("--label", help="run name; output goes to runs/<label>/ — asked "
                   "interactively if omitted (unless --skip-wizard, where one is generated)")
    p.add_argument("--seed", type=int, help="asked as part of 'advanced settings' if omitted "
                    "(unless --skip-wizard, where it defaults to 1)")
    p.add_argument("--agent-model", help="tau-bench only; asked interactively if omitted "
                    "(unless --skip-wizard, where tau-traffic's own default applies)")
    p.add_argument("--customer-model", help="tau-bench only; same as --agent-model")
    p.add_argument("--skip-wizard", action="store_true",
                    help="skip the manual-setup prompts (use once .env is already pointed at a fresh project)")
    p.add_argument("--skip-traffic", action="store_true",
                    help="don't send new traffic — compare traffic already sent (e.g. recovering "
                         "after an infra failure mid-run, so you're not paying to re-send). "
                         "Requires --since.")
    p.add_argument("--since", help="ISO timestamp traffic was sent from — required with --skip-traffic")
    p.add_argument("--traffic-args", default="",
                    help="extra args passed through verbatim to the traffic generator, e.g. "
                         "--traffic-args '--domain retail' (tau-bench) or "
                         "--traffic-args '--mix forgetting' (park-traffic). See that tool's "
                         "own --help for what it supports — not duplicated here, since it "
                         "would drift out of sync as those tools grow.")
    args = p.parse_args()
    if args.skip_traffic and not args.since:
        p.error("--skip-traffic requires --since <ISO timestamp>")
    return args


# ------------------------------------------------------------------ manual setup (wizard)

def _mask_value(key, value):
    """A secret value for display only — never used for what actually gets written."""
    value = (value or "").strip()
    if not value:
        return "(not set)"
    if "API_KEY" in key:
        return f"{value[:6]}...{value[-4:]}" if len(value) > 12 else "***"
    return value


def _propose_env_update(path, updates):
    """Computes what writing `updates` (KEY -> new value) into `path` would change. Returns
    (new_text, changes), where changes is a list of (key, old_value_or_None, new_value) for
    only the keys whose value would actually change. Writes nothing — the caller decides
    whether to show the changes and write them.

    For each key, prefers an existing *active* (uncommented) line over a commented one — a
    file can have both (e.g. "# previous key, for reference" above the real line), and the
    active line is the one that actually takes effect. Picking the first textual match
    regardless of comment state (the original bug here, caught 2026-11-16 mid-run: it
    "updated" a commented historical line and silently left the real active line, and
    therefore the real key in effect, untouched) would corrupt exactly this file shape."""
    text = path.read_text() if path.exists() else ""
    lines = text.splitlines()

    def key_of(line):
        bare = line.lstrip("#").strip()
        return bare.split("=", 1)[0].strip() if "=" in bare else None

    def value_of(line):
        bare = line.lstrip("#").strip()
        return bare.split("=", 1)[1] if "=" in bare else None

    target_line = {}  # key -> (line index, is_commented) of the line we'll actually change
    for i, line in enumerate(lines):
        key = key_of(line)
        if key not in updates:
            continue
        is_commented = line.lstrip().startswith("#")
        if key not in target_line or (target_line[key][1] and not is_commented):
            target_line[key] = (i, is_commented)

    new_lines = list(lines)
    changes = []
    for key, new_value in updates.items():
        if key in target_line:
            i, _ = target_line[key]
            old_value = value_of(lines[i])
            if old_value == new_value:
                continue
            new_lines[i] = f"{key}={new_value}"
            changes.append((key, old_value, new_value))
        else:
            new_lines.append(f"{key}={new_value}")
            changes.append((key, None, new_value))

    new_text = "\n".join(new_lines) + "\n"
    return new_text, changes


def _apply_env_updates(targets):
    """targets: dict of Path -> {KEY: value}. Shows what would actually change in every file,
    masked, asks once, writes only on explicit confirmation — never silently."""
    pending = []
    for path, updates in targets.items():
        new_text, changes = _propose_env_update(path, updates)
        if not changes:
            print(f"  {path}: already up to date")
            continue
        print(f"  {path}:")
        for key, old_value, new_value in changes:
            print(f"    {key}: {_mask_value(key, old_value)} -> {_mask_value(key, new_value)}")
        pending.append((path, new_text))
    if not pending:
        return
    if input("\nApply the change(s) above? [y/N] ").strip().lower() == "y":
        for path, new_text in pending:
            path.write_text(new_text)
        print("Updated.")
    else:
        print("Skipped — update these files yourself before continuing, or this run will use "
              "whatever key is already in each of them.")


_LIST_ITEM = re.compile(r"^\d+\.\s")


def _extract_blockquote(text, header):
    """The '> ...' lines right after a '**Header:**' line, as plain text. The file hard-wraps
    long lines for readability (e.g. "...wasted substantial\nwork." splits mid-sentence) —
    those aren't real line breaks in the original instruction, so a wrapped continuation line
    (not blank, not starting a new numbered point) is joined to the previous one with a space
    instead of a newline. A bare '>' (blank quoted line) or a line starting a new numbered
    point ("1. ", "2. ", ...) always starts a new line — those are the real breaks."""
    after = text.split(header, 1)[1]
    lines, started = [], False
    for line in after.splitlines():
        stripped = line.strip()
        if not started:
            if stripped.startswith(">"):
                started = True
            elif stripped == "":
                continue  # the blank line between the header and the quote
            else:
                break
        if not stripped.startswith(">"):
            break
        content = stripped[1:].strip()
        if not content:
            lines.append("")
        elif lines and lines[-1] and not _LIST_ITEM.match(content):
            lines[-1] = f"{lines[-1]} {content}"
        else:
            lines.append(content)
    return "\n".join(lines).rstrip()


def _extract_table(text, header):
    """The markdown table right after a '**Header:**' line, as Tripwire's own output_schema
    shape: [{"name", "type", "nullable", "description"}, ...] (plus "choices" for an enum
    column written as 'enum (`a`/`b`/`c`)'). Skips the header row and the '---' separator."""
    after = text.split(header, 1)[1]
    rows = [line.strip() for line in after.splitlines() if line.strip().startswith("|")]
    fields = []
    for row in rows[2:]:
        name, type_cell, nullable_cell, description = (c.strip() for c in row.strip("|").split("|"))
        field = {"name": name.strip("`"), "nullable": nullable_cell.lower() == "yes",
                  "description": description}
        enum_match = re.match(r"enum \(([^)]+)\)", type_cell)
        if enum_match:
            field["type"] = "enum"
            field["choices"] = [c.strip().strip("`") for c in enum_match.group(1).split("/")]
        else:
            field["type"] = type_cell
        fields.append(field)
    return fields


def _parse_signal_md(path):
    """A signal's {instruction, output_schema} straight from its signals/*.md reference file —
    parsed, not duplicated, so there's nothing to drift out of sync with the prose a human
    copies into Laminar's dashboard by hand."""
    text = path.read_text()
    return _extract_blockquote(text, "**Instruction:**"), _extract_table(text, "**Output schema:**")


def _signal_md_for(name):
    """A Tripwire signal name's reference file — lowercase, spaces to underscores, matching
    the existing filenames (signals/user_frustration.md, signals/intent.md, signals/issues.md).
    Keep any new signal's filename to this convention."""
    return SIGNALS_DIR / f"{name.lower().replace(' ', '_')}.md"


def create_tripwire_signals():
    """POST each Tripwire-side signal from SIGNAL_PAIRS to Tripwire's own /api/signals —
    automates the "create this in Tripwire's UI" wizard step, reusing the real API a human
    would otherwise click through, not any Signals-pair-owned code. Shows what would be
    created and asks once before doing anything, same pattern as _apply_env_updates."""
    base = os.environ.get("TRIPWIRE_API_URL", "http://localhost:8080")
    to_create = []
    for tw_name, _ in SIGNAL_PAIRS:
        path = _signal_md_for(tw_name)
        if not path.exists():
            print(f"  (no reference file for '{tw_name}' at signals/{path.name} — skipping)")
            continue
        instruction, output_schema = _parse_signal_md(path)
        to_create.append({"name": tw_name, "instruction": instruction,
                           "output_schema": output_schema, "filters": []})
    if not to_create:
        return
    print("Would create in Tripwire:")
    for sig in to_create:
        print(f"  - {sig['name']}: {len(sig['instruction'])} chars, "
              f"{len(sig['output_schema'])} schema field(s)")
    if input("Create these now via Tripwire's API? [y/N] ").strip().lower() != "y":
        print("Skipped — create them yourself in Tripwire's UI.")
        return
    with httpx.Client(timeout=10) as client:
        for sig in to_create:
            resp = client.post(f"{base}/api/signals", json=sig)
            if resp.status_code == 201:
                print(f"  Created '{sig['name']}'.")
            elif resp.status_code == 409:
                print(f"  '{sig['name']}' already exists — left alone.")
            else:
                print(f"  Failed to create '{sig['name']}': HTTP {resp.status_code} {resp.text[:200]}")


# tau-bench's own domains (traffic/tau-bench/tau_traffic.py's DOMAINS) — asked about here,
# before any setup, rather than left for someone to discover only via --traffic-args after
# they've already spent time creating a project and signals for the wrong one.
TAU_DOMAINS = {
    "retail": "online store: order changes, returns, exchanges (114 tasks)",
    "airline": "flight bookings, changes, cancellations (50 tasks)",
    "banking_knowledge": "bank support that searches 698 policy documents, a RAG app (97 tasks)",
    "telecom": "phone and mobile-data troubleshooting, hardest for the agent (114 tasks)",
}


def _pick_tau_domain(traffic_args):
    """If --traffic-args already names a domain, leave it alone — the CLI flag is for
    scripting/automation and wins. Otherwise ask interactively, here, before any setup starts,
    rather than leaving someone to discover domain selection exists only via --traffic-args
    after they've already created a project and signals for the wrong one. Returns the
    (possibly updated) traffic_args string; exits if an unrecognized domain is typed."""
    if "--domain" in shlex.split(traffic_args):
        return traffic_args
    print("Which tau-bench domain?")
    for name, about in TAU_DOMAINS.items():
        default_note = " (default)" if name == "retail" else ""
        print(f"  {name}: {about}{default_note}")
    domain = input("Press Enter for retail, or type one of the names above: ").strip()
    if not domain:
        return traffic_args
    if domain not in TAU_DOMAINS:
        sys.exit(f"'{domain}' isn't one of: {', '.join(TAU_DOMAINS)}")
    return f"{traffic_args} --domain {domain}".strip()


# tau-bench's own agent-model notes (traffic/tau-bench/tau_traffic.py's MODEL_NOTES) — shown
# only for the agent-model question, matching tau-traffic's own wizard (customer-model offers
# the same names with no notes, since the notes describe agent behavior specifically). Worth a
# dedicated question, not bundled into free-form --traffic-args: model choice changes what
# failure modes even show up (e.g. gpt-6-luna's raw-JSON-reply quirk is model-specific — pick
# a different model and that comparison dimension will just read "no" everywhere, correctly,
# not usefully).
TAU_MODEL_NOTES = {
    "gpt-6-luna": "newest and cheapest; mistakes mostly silent; ~1 in 8 replies is raw JSON",
    "gpt-4o-mini": "older; more varied, louder mistakes (transfers to a human, frustration)",
    "gpt-4.1-mini": "older; more capable than gpt-4o-mini, pricier",
    "gpt-4.1": "older and expensive",
}


def _pick_tau_model(cli_value, label, show_notes):
    """If the CLI flag was already given, leave it alone. Otherwise ask, with tau-traffic's
    own known models as suggestions — but accept any typed name, since tau-traffic itself
    accepts any model LiteLLM understands via its "Other" choice. Returns None (meaning: don't
    pass the flag at all, let tau-traffic's own default apply) on a bare Enter."""
    if cli_value is not None:
        return cli_value
    print(f"{label}?")
    for name, note in TAU_MODEL_NOTES.items():
        print(f"  {name}: {note}" if show_notes else f"  {name}")
    chosen = input("Press Enter for gpt-6-luna, or type a model name: ").strip()
    return chosen or None


def ask_run_parameters(args):
    """What this run actually is — domain/models (tau only), how many conversations, what to
    call it — decided once, up front, before any manual Laminar/Tripwire setup starts (see
    wizard() below). Mirrors tau-traffic's own wizard structure: ask the few things that
    change what's actually being tested every time, bundle everything else (seed, any other
    flag the traffic generator supports) behind one "advanced settings?" question that
    defaults to no — rather than compare-laminar re-asking every flag each generator has, or
    requiring them all as CLI flags decided before you even see what this tool does.

    Every value here still has a CLI flag that skips its prompt (e.g. for scripting/CI) —
    this only asks for whatever wasn't already given."""
    if args.skip_wizard:
        if args.n is None:
            args.n = 10
        if args.label is None:
            args.label = f"{args.traffic}-{datetime.now():%Y%m%d-%H%M%S}"
        if args.seed is None:
            args.seed = 1
        return

    if args.traffic == "tau":
        args.traffic_args = _pick_tau_domain(args.traffic_args)
        agent_model = _pick_tau_model(args.agent_model, "Agent model (the app)", show_notes=True)
        customer_model = _pick_tau_model(args.customer_model, "Customer model (simulated customer)",
                                          show_notes=False)
        if agent_model:
            args.traffic_args = f"{args.traffic_args} --agent-model {agent_model}".strip()
        if customer_model:
            args.traffic_args = f"{args.traffic_args} --customer-model {customer_model}".strip()

    if args.n is None:
        n_input = input("\nConversations to send? [10] ").strip()
        args.n = int(n_input) if n_input else 10

    if args.label is None:
        default_label = f"{args.traffic}-{datetime.now():%Y%m%d-%H%M%S}"
        label_input = input(f"Run label? [{default_label}] ").strip()
        args.label = label_input or default_label

    if input("\nChange advanced settings (seed, extra flags for the traffic generator)? "
             "[y/N] ").strip().lower() == "y":
        seed_input = input(f"Seed? [{args.seed if args.seed is not None else 1}] ").strip()
        if seed_input:
            args.seed = int(seed_input)
        extra = input("Extra flags for the traffic generator (see its own --help)? ").strip()
        if extra:
            args.traffic_args = f"{args.traffic_args} {extra}".strip()

    if args.seed is None:
        args.seed = 1
    print()


def wizard(args):
    """Walk through the steps only a human can do in Laminar's/Tripwire's dashboards. Step 3
    (pointing every .env at the new project) defaults to a plain checklist — editing a
    teammate's .env files, including ones holding other secrets like OPENAI_API_KEY, without
    them reviewing each write is a different trust posture than printing what to paste where.
    But doing it by hand has its own real cost: caught live 2026-11-16, a key pasted into the
    wrong field in one file and a key never updated at all in another, both from the tedium of
    6 similar-looking paste actions across 3 files. So it's offered as an explicit opt-in:
    paste the values once, review the exact per-file changes (masked), confirm before anything
    is written."""
    if args.skip_wizard:
        return
    print(f"Setting up a {args.traffic}-traffic run against a fresh Laminar project. "
          "See README.md if anything below is unclear.\n")

    input("1. Create a fresh Laminar Cloud project for this run. Press Enter once it exists... ")

    print("\n2. Create each signal below in Laminar's dashboard — prompt + schema are in each "
          "file (signals/*.md); paste them in as-is so both systems match.")
    for f in sorted(SIGNALS_DIR.glob("*.md")):
        input(f"   Press Enter once '{f.stem}' exists in Laminar (using {f.relative_to(HERE)})... ")

    backend_env = HERE.parent.parent / "signals" / "backend" / ".env"
    if args.traffic == "park":
        # park_assistant/telemetry.py defaults to a self-hosted Laminar when these are unset —
        # unlike tau-bench, it has no Cloud-first default, so they must be set explicitly here.
        traffic_env = Path(os.environ["PARK_TRAFFIC_DIR"]).expanduser() / ".env"
        traffic_extra = {"LMNR_BASE_URL": "https://api.lmnr.ai", "LMNR_HTTP_PORT": "443",
                          "LMNR_GRPC_PORT": "8443"}
        traffic_note = (" and LMNR_BASE_URL=https://api.lmnr.ai / LMNR_HTTP_PORT=443 / "
                         "LMNR_GRPC_PORT=8443 (park_assistant/telemetry.py defaults to "
                         "self-hosted when these are unset)")
    else:
        traffic_env = (HERE / os.environ["TAU_TRAFFIC_DIR"]).resolve() / ".env"
        traffic_extra = {}
        traffic_note = ""

    print("\n3. Point every .env below at the new project (its API key, and project ID where asked):")
    print("   (project ID = the UUID in its dashboard URL, right after /project/)")
    print(f"   - {HERE / '.env'}: LMNR_PROJECT_API_KEY, LMNR_PROJECT_ID")
    print(f"   - {backend_env}: LMNR_PROJECT_API_KEY, LMNR_PROJECT_ID")
    print(f"   - {traffic_env}: LMNR_PROJECT_API_KEY{traffic_note}")

    if input("\n   Want me to apply these for you instead? Paste the values once, review the "
             "exact change per file, confirm before anything is written. [y/N] ").strip().lower() == "y":
        api_key = input("   Paste the Project API key: ").strip()
        project_id = input("   Paste the project ID: ").strip()
        targets = {
            HERE / ".env": {"LMNR_PROJECT_API_KEY": api_key, "LMNR_PROJECT_ID": project_id},
            backend_env: {"LMNR_PROJECT_API_KEY": api_key, "LMNR_PROJECT_ID": project_id},
            traffic_env: {"LMNR_PROJECT_API_KEY": api_key, **traffic_extra},
        }
        _apply_env_updates(targets)
    else:
        input("   Press Enter once all of the above are updated... ")
    # The .env files may have just changed, but main() loaded them before the wizard ran. Reload,
    # overriding, so this process and the traffic generator it starts (which inherits this
    # environment, and whose own load_dotenv() won't override it) use the new project.
    # Nov 17: the stale key sent a whole park run to the previous project.
    load_dotenv(HERE / ".env", override=True)

    print("\n4. Reset Tripwire's DB for a clean run: in signals/backend/, `docker compose down -v`,")
    print("   then `docker compose up -d --build` (NOT a plain `up` — see README for why).")
    input("   Press Enter once Tripwire is up against the fresh project... ")
    print("\n5. Create each signal below in Tripwire's own UI, matching the same files.")
    if input("   Want me to create them for you instead, via Tripwire's own API? [y/N] "
             ).strip().lower() == "y":
        create_tripwire_signals()
    else:
        for f in sorted(SIGNALS_DIR.glob("*.md")):
            input(f"   Press Enter once '{f.stem}' exists in Tripwire... ")
    print("\nSetup done. Taking over from here.\n")


# ------------------------------------------------------------------ traffic

def send_traffic(args):
    """Shell out to the existing generators. `--traffic-args` is appended last, so an explicit
    override (e.g. --traffic-args '--mix forgetting') wins over the defaults below — both
    tools' own argparse takes the last occurrence of a repeated flag.

    park-traffic verified end-to-end 2026-11-15 (real traffic, real judging, real report).
    tau-traffic's command shape is from its own --help, not yet exercised for real."""
    extra = shlex.split(args.traffic_args)
    if args.traffic == "park":
        park_dir = Path(os.environ["PARK_TRAFFIC_DIR"]).expanduser()
        cmd = [str(park_dir / ".venv" / "bin" / "python"), "-m", "harness.run",
               "--n", str(args.n), "--mix", "baseline", "--bugs", "all",
               "--seed", str(args.seed), "--label", args.label, *extra]
        subprocess.run(cmd, cwd=park_dir, check=True)
    else:
        tau_dir = (HERE / os.environ["TAU_TRAFFIC_DIR"]).resolve()
        cmd = [str(tau_dir / "tau-traffic"), "-n", str(args.n), "--yes", *extra]
        subprocess.run(cmd, cwd=tau_dir, check=True)


# ------------------------------------------------------------------ settle detection

def wait_for_settle():
    """Poll Tripwire's /api/dev/status (tripwire/api/dev.py) until nothing is pending/running.
    TODO: Laminar's own settle isn't checked automatically yet — Laminar keeps updating clusters
    (see next-task doc), so for now this just waits for Tripwire and prints a reminder to eyeball
    Laminar's Runs tab before trusting the snapshot."""
    base = os.environ.get("TRIPWIRE_API_URL", "http://localhost:8080")
    deadline = time.monotonic() + SETTLE_MAX_WAIT_SECONDS
    with httpx.Client() as client:
        while time.monotonic() < deadline:
            status = client.get(f"{base}/api/dev/status", timeout=10).json()
            stuck = [s["stage"] for s in status["pipeline"]["stages"] if s["stuck"]]
            busy = [s["stage"] for s in status["pipeline"]["stages"]
                    if any(c["count"] > 0 for c in s["counts"]
                           if c["state"] in ("pending", "running", "retrying"))]
            if stuck:
                print(f"  stuck: {stuck} — check runs/dev page before continuing")
            if not busy:
                print("  Tripwire pipeline drained.")
                break
            print(f"  still waiting on: {busy}")
            time.sleep(SETTLE_POLL_SECONDS)
        else:
            sys.exit(f"gave up waiting after {SETTLE_MAX_WAIT_SECONDS}s — check /dev for a stuck stage")
    input("  Check Laminar's Runs tab for this project — press Enter once it shows no runs pending... ")


# ------------------------------------------------------------------ snapshot: Tripwire

def snapshot_tripwire():
    """Pull Tripwire's own tables. TODO: reuse tripwire.clusterer.sql rules (CONTRACT.md: don't
    retype shared SQL) once this moves from eval tooling into something that imports the
    backend package directly.

    `laminar_trace_id` is the real cross-system join key (both sides store the same Laminar
    trace UUID); `session_id` is kept alongside it only for the park-traffic answer-key join,
    which only knows convo_id/session_id, not the Laminar trace UUID."""
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            events = cur.execute("""
                SELECT e.id, e.trace_id, t.laminar_trace_id, t.session_id,
                       e.severity, e.finding_index, v.signal_id, s.name AS signal_name
                FROM signal_events e
                JOIN signal_versions v ON v.id = e.signal_version_id
                JOIN signals s ON s.id = v.signal_id
                JOIN traces t ON t.id = e.trace_id
            """).fetchall()
            # psycopg returns the `uuid` column as a uuid.UUID object; Laminar's SQL API
            # returns the same trace id as a plain string. Left un-cast, every event silently
            # fails to match its Laminar counterpart (UUID != str, even for the same trace) —
            # caught by actually running compare() against a live trace, not by inspection.
            for e in events:
                e["laminar_trace_id"] = str(e["laminar_trace_id"])
            member_clusters = cur.execute(
                "SELECT event_id, cluster_id FROM cluster_members"
            ).fetchall()
            # The full table, not just clusters with direct members: a future ancestor
            # (level 1/2) is only ever reached via another cluster's parent_id, never via
            # cluster_members itself (members attach at the leaf, mirroring Laminar's model —
            # see tripwire_chain() below).
            all_clusters = cur.execute(
                "SELECT id, name, level, parent_id, signal_id FROM clusters"
            ).fetchall()
    return {"events": events, "member_clusters": member_clusters, "all_clusters": all_clusters}


def tripwire_chain(cluster_id, clusters_by_id):
    """One event's cluster ancestry starting at `cluster_id`, leaf (level 0) first — same shape
    as Laminar's `cluster_details` (list of {id, name, level}), so compare() can treat both
    sides the same way whether Tripwire has 1 level (today) or up to 3 (planned; see
    handoffs/cluster-trees-exploration.md). Today this is always a 1-element list; walking
    parent_id needs no change when levels 1-2 land."""
    chain = []
    node = clusters_by_id.get(cluster_id)
    while node is not None:
        chain.append({"id": node["id"], "name": node["name"], "level": node["level"]})
        node = clusters_by_id.get(node["parent_id"])
    return chain


def load_answer_key():
    """park-traffic only. Pattern from current-state.md ("Handy facts"): load answer_key.jsonl,
    join ak.line->>'convo_id' = traces.session_id. target_bug null = no planted target."""
    park_dir = Path(os.environ["PARK_TRAFFIC_DIR"]).expanduser()
    matches = sorted(park_dir.glob(f"runs/*/answer_key.jsonl"))
    if not matches:
        print("  WARNING: no answer_key.jsonl found under park-traffic runs/ — did send_traffic run?")
        return []
    lines = matches[-1].read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def load_tau_results():
    """tau-bench only. Same session_id join pattern as park's answer key (tau_traffic.py writes
    the Laminar session_id into each row). `passed` is a bool, or null for an errored/skipped
    task (tau_traffic.py's Recorder.add())."""
    tau_dir = (HERE / os.environ["TAU_TRAFFIC_DIR"]).resolve()
    matches = sorted(tau_dir.glob("runs/*/answer_key.jsonl"))
    if not matches:
        print("  WARNING: no answer_key.jsonl found under tau-bench runs/ — did send_traffic run?")
        return []
    lines = matches[-1].read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ------------------------------------------------------------------ snapshot: Laminar

# Confirmed 2026-11-15 against the live trial project (signals/backend/.env):
#   - signal_events excludes level-0 cluster info; signal_events_all includes it. Use _all.
#   - severity is numeric (0/1/2 = info/warning/critical, by position — not spelled out
#     anywhere in the SQL response, inferred from the design docs' three-level severity).
#   - `clusters` is the event's full ancestor chain (named, level >= 1 only — level 0 is an
#     internal, always-unnamed pre-naming bucket, never returned by `clusters`/
#     `event_clusters_all`, and NOT what Laminar calls a leaf).
#   - `leaf_clusters` is the single most-specific NAMED cluster (level 1 in every case seen) —
#     this is the right thing to compare against Tripwire's one (unnamed, un-leveled) cluster
#     tier, not level 0.
#   - traces.session_id is present and matches the answer-key join in current-state.md.
EVENTS_SQL = """
SELECT id, trace_id, signal_id, signal_name, severity, signal_version, timestamp,
       payload, leaf_clusters, cluster_details
FROM signal_events_all
WHERE timestamp >= toDateTime64({since:String}, 6, 'UTC')
  AND timestamp <  toDateTime64({until:String}, 6, 'UTC')
"""

TRACES_SQL = """
SELECT id, session_id, status, start_time, end_time, total_tokens
FROM traces
WHERE start_time >= toDateTime64({since:String}, 6, 'UTC')
  AND start_time <  toDateTime64({until:String}, 6, 'UTC')
"""

# tau-bench's raw-JSON-reply quirk check reads LLM spans directly, not Tripwire's judge_input —
# deliberately, so this doesn't depend on the judge-rendering bug (filed 2026-11-16) being fixed
# first. `output` can come shaped either of two ways depending on which library made the call
# (see the bug writeup for the full explanation): a flat list of message dicts, or OpenAI's raw
# choices-array shape with the message nested one level deeper. _extract_reply_texts handles
# both without needing to know in advance which one a given span used.
LLM_SPANS_SQL = """
SELECT trace_id, output
FROM spans
WHERE span_type = 'LLM'
  AND start_time >= toDateTime64({since:String}, 6, 'UTC')
  AND start_time <  toDateTime64({until:String}, 6, 'UTC')
"""


def _ch_time(moment):
    """Aware datetime -> ClickHouse parameter text, in UTC. Same format as the poller's
    _ch_time (tripwire/poller/poll.py) — not imported since it's private to that module."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _extract_reply_texts(output_raw):
    """The assistant's plain-text reply content(s) from one LLM span's raw output, whichever of
    the two known shapes it's in. Tool-call arguments are deliberately not included here — they
    are JSON by nature, and we're only checking whether a *reply* looks like JSON."""
    try:
        items = json.loads(output_raw) if isinstance(output_raw, str) else output_raw
    except (TypeError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    texts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        message = item.get("message") if isinstance(item.get("message"), dict) else item
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            texts.append(content)
    return texts


def _looks_like_json(text):
    """True if a reply is itself a JSON object/array rather than natural language — the
    tau-bench quirk this is meant to catch (gpt-6-luna replying with raw JSON ~13% of the time,
    see current-state.md)."""
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return False
    try:
        json.loads(stripped)
    except ValueError:
        return False
    return True


def snapshot_laminar(since, until, fetch_llm_texts=False):
    client = LaminarSQL(
        api_key=os.environ["LMNR_PROJECT_API_KEY"],
        api_url=os.environ.get("LMNR_API_URL", "https://api.lmnr.ai"),
    )
    try:
        params = {"since": _ch_time(since), "until": _ch_time(until)}
        events = client.query(EVENTS_SQL, params)
        traces = client.query(TRACES_SQL, params)
        llm_reply_texts = None
        if fetch_llm_texts:
            llm_reply_texts = defaultdict(list)
            for row in client.query(LLM_SPANS_SQL, params):
                llm_reply_texts[row["trace_id"]].extend(_extract_reply_texts(row["output"]))
            llm_reply_texts = dict(llm_reply_texts)
    finally:
        client.close()
    return {"events": events, "traces": traces, "llm_reply_texts": llm_reply_texts}


# ------------------------------------------------------------------ compare + report

# Which Tripwire signal to compare against which Laminar signal, by name as created in each
# system for THIS run — edit per run. All three below are real in the fresh project set up
# 2026-11-16, with matching instructions/schema (see signals/*.md) except Laminar's capitalized
# "Frustration" where Tripwire's is lowercase — a display-name difference only, doesn't affect
# the comparison.
SIGNAL_PAIRS = [
    ("User frustration", "User Frustration"),
    ("Intent", "Intent"),
    ("Issues", "Issues"),
]

# Tripwire's cluster levels count outward from 0 = leaf (CHECK level BETWEEN 0 AND 2,
# migrations/20261109120000_initial_schema.sql; only level 0 is populated today, up to 2
# planned — see handoffs/cluster-trees-exploration.md). Laminar's level 1 (`leaf_clusters`) is
# its own most-specific NAMED cluster — level 0 there is an internal, always-unnamed bucket,
# not a leaf in Laminar's sense (see snapshot_laminar's notes / the project-laminar-sql-schema
# memory). Both sides anchor "leaf" at their own first meaningful level, so Tripwire level L
# lines up with Laminar level L + 1. Single place to fix if either side's numbering changes.
LEVEL_OFFSET = 1


def _laminar_trace_link(trace_id):
    ui_url = os.environ.get("LMNR_UI_URL", "https://laminar.sh")
    project_id = os.environ.get("LMNR_PROJECT_ID")
    if not project_id:
        return None
    return f"{ui_url}/project/{project_id}/traces?traceId={trace_id}"


def _chain_at_level(chain, level):
    """The chain entry at an exact level, or None — chains are short (<= 3), so a linear scan
    is simpler than indexing by level for no real cost."""
    return next((c for c in chain if c["level"] == level), None)


def _ground_truth_dimensions(snapshot, universe, session_id_of_trace):
    """Per-trace ground-truth labels, one dict per named dimension — every trace in `universe`
    gets a label (never missing), so the crosstab always accounts for all of them.

    park-traffic has one dimension (the planted bug). tau-bench has two, and they're
    independent facts about a trace (a conversation can pass *and* reply in raw JSON, or fail
    and never once reply in raw JSON) — kept as separate tables rather than combined into one,
    so neither obscures the other."""
    if snapshot["traffic"] == "park":
        answer_key = {r["convo_id"]: r for r in snapshot.get("answer_key", [])}
        if not answer_key:
            return {}
        labels = {}
        for trace_id in universe:
            record = answer_key.get(session_id_of_trace.get(trace_id))
            labels[trace_id] = (record or {}).get("target_bug") or "(no planted bug)"
        return {"planted bug": labels}

    dims = {}
    tau_results = {r["session_id"]: r for r in snapshot.get("tau_results", []) if r.get("session_id")}
    if tau_results:
        labels = {}
        for trace_id in universe:
            record = tau_results.get(session_id_of_trace.get(trace_id))
            passed = record.get("passed") if record else None
            labels[trace_id] = "passed" if passed is True else "failed" if passed is False else "(no result)"
        dims["pass/fail"] = labels

    reply_texts = (snapshot.get("laminar") or {}).get("llm_reply_texts") or {}
    if reply_texts:
        dims["raw JSON reply"] = {
            trace_id: "yes" if any(_looks_like_json(t) for t in reply_texts.get(trace_id, [])) else "no"
            for trace_id in universe
        }
    return dims


def compare(snapshot):
    laminar_events = snapshot["laminar"]["events"]
    laminar_traces = {t["id"]: t for t in snapshot["laminar"]["traces"]}
    laminar_events_by_trace = defaultdict(list)
    for e in laminar_events:
        laminar_events_by_trace[e["trace_id"]].append(e)

    tw_events = snapshot["tripwire"]["events"]
    tw_events_by_trace = defaultdict(list)
    for e in tw_events:
        tw_events_by_trace[e["laminar_trace_id"]].append(e)
    tw_clusters_by_id = {c["id"]: c for c in snapshot["tripwire"]["all_clusters"]}
    tw_cluster_of_event = {m["event_id"]: m["cluster_id"] for m in snapshot["tripwire"]["member_clusters"]}

    session_id_of_trace = {t["id"]: t["session_id"] for t in laminar_traces.values() if t.get("session_id")}
    for e in tw_events:
        if e.get("session_id"):
            session_id_of_trace.setdefault(e["laminar_trace_id"], e["session_id"])

    universe = set(laminar_traces) | set(tw_events_by_trace)
    ground_truth_dims = _ground_truth_dimensions(snapshot, universe, session_id_of_trace)

    pairs_out = []
    for tw_name, lm_name in SIGNAL_PAIRS:
        tw_fired = {t for t, es in tw_events_by_trace.items() if any(e["signal_name"] == tw_name for e in es)}
        lm_fired = {t for t, es in laminar_events_by_trace.items() if any(e["signal_name"] == lm_name for e in es)}

        agreement = {"both": [], "laminar_only": [], "tripwire_only": [], "neither": []}
        for trace_id in universe:
            tw, lm = trace_id in tw_fired, trace_id in lm_fired
            key = "both" if tw and lm else "laminar_only" if lm else "tripwire_only" if tw else "neither"
            agreement[key].append(trace_id)

        # vs. ground truth: does agreement track something real, or just the two systems
        # matching each other? One table per dimension (see _ground_truth_dimensions).
        ground_truth = {}
        for dim_name, label_by_trace in ground_truth_dims.items():
            counts = defaultdict(lambda: defaultdict(int))
            for trace_id in universe:
                cause = label_by_trace[trace_id]
                tw, lm = trace_id in tw_fired, trace_id in lm_fired
                key = "both" if tw and lm else "laminar_only" if lm else "tripwire_only" if tw else "neither"
                counts[cause][key] += 1
            ground_truth[dim_name] = {k: dict(v) for k, v in counts.items()}

        # Cluster crosstab, per level pair — only on traces both sides flagged, since that's
        # the only place "did they group it the same way" is a meaningful question.
        crosstab = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        for trace_id in agreement["both"]:
            tw_chains = [
                tripwire_chain(tw_cluster_of_event[e["id"]], tw_clusters_by_id)
                for e in tw_events_by_trace[trace_id]
                if e["signal_name"] == tw_name and e["id"] in tw_cluster_of_event
            ]
            lm_chains = [e["cluster_details"] for e in laminar_events_by_trace[trace_id] if e["signal_name"] == lm_name]
            for tw_chain in tw_chains:
                for node in tw_chain:
                    lm_level = node["level"] + LEVEL_OFFSET
                    for lm_chain in lm_chains:
                        lm_node = _chain_at_level(lm_chain, lm_level)
                        if lm_node is not None:
                            crosstab[node["level"]][node["name"] or "(unnamed)"][lm_node["name"] or "(unnamed)"] += 1
        crosstab = {level: {tw: dict(lm_counts) for tw, lm_counts in by_tw.items()}
                    for level, by_tw in crosstab.items()}

        pairs_out.append({
            "tripwire_signal": tw_name,
            "laminar_signal": lm_name,
            "counts": {k: len(v) for k, v in agreement.items()},
            "disagreements": {
                "laminar_only": [{"trace_id": t, "link": _laminar_trace_link(t)} for t in agreement["laminar_only"]],
                "tripwire_only": [{"trace_id": t, "link": _laminar_trace_link(t)} for t in agreement["tripwire_only"]],
            },
            "ground_truth": ground_truth,
            "cluster_crosstab": crosstab,
        })

    return {
        "universe_size": len(universe),
        "pairs": pairs_out,
        "noise_baseline": None,   # needs Tripwire judged twice on the same traces — not built yet
    }


def render_report(comparison, snapshot):
    lines = [
        f"# Compare report: {snapshot['label']}",
        "",
        f"Traffic: {snapshot['traffic']} · taken at {snapshot['taken_at']} · "
        f"{comparison['universe_size']} traces in the window",
        "",
        f"Tripwire events: {len(snapshot['tripwire']['events'])} · "
        f"Laminar events: {len(snapshot['laminar']['events'])}",
        "",
    ]

    for pair in comparison["pairs"]:
        lines += [
            f"## {pair['tripwire_signal']} (Tripwire) vs. {pair['laminar_signal']} (Laminar)",
            "",
            "| both | Laminar only | Tripwire only | neither |",
            "|---|---|---|---|",
            "| {both} | {laminar_only} | {tripwire_only} | {neither} |".format(**pair["counts"]),
            "",
        ]
        for dim_name, by_cause in pair["ground_truth"].items():
            lines += [f"### vs. {dim_name}", "",
                      f"| {dim_name} | both | Laminar only | Tripwire only | neither |",
                      "|---|---|---|---|---|"]
            for cause, counts in sorted(by_cause.items()):
                lines.append("| {} | {} | {} | {} | {} |".format(
                    cause, counts.get("both", 0), counts.get("laminar_only", 0),
                    counts.get("tripwire_only", 0), counts.get("neither", 0)))
            lines.append("")

        for level, by_tw in sorted(pair["cluster_crosstab"].items()):
            lines += [f"### Cluster crosstab, Tripwire level {level} vs. Laminar level {level + LEVEL_OFFSET}", ""]
            lm_names = sorted({lm for counts in by_tw.values() for lm in counts})
            lines.append("| Tripwire cluster \\ Laminar cluster | " + " | ".join(lm_names) + " |")
            lines.append("|---|" + "---|" * len(lm_names))
            for tw_name_, counts in sorted(by_tw.items()):
                lines.append(f"| {tw_name_} | " + " | ".join(str(counts.get(lm, 0)) for lm in lm_names) + " |")
            lines.append("")

        if pair["disagreements"]["laminar_only"] or pair["disagreements"]["tripwire_only"]:
            lines += ["### Disagreements", ""]
            for kind, label in [("laminar_only", "Only Laminar flagged"), ("tripwire_only", "Only Tripwire flagged")]:
                for d in pair["disagreements"][kind]:
                    link = f" — {d['link']}" if d["link"] else ""
                    lines.append(f"- {label}: `{d['trace_id']}`{link}")
            lines.append("")

    lines += [
        "## Not in this report",
        "",
        "- **Noise baseline** (Tripwire judged twice on the same traces): not built — needs a "
        "second judging pass, which doesn't exist yet.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
