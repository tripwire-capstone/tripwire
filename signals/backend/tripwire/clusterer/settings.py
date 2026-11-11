"""Every clusterer setting, read once at startup, with its default and its check.

Settings come from environment variables: `.env` locally, the ECS task definition (written by
Terraform) on AWS. A bad value stops the clusterer from starting with a message saying what's
wrong (decided Nov 11): a misconfigured clusterer that runs is worse than one that refuses to
start, because bad clustering looks plausible. On ECS the container then exits, the service
shows unhealthy, and the message is in the logs.

This is the only place defaults live; `.env.example` lists the same ones (a test checks they
match). Defaults come from docs/clustering/DESIGN.md's settings table.
"""

import os
from dataclasses import dataclass

from .summarize import prompt_paths

MODES = ("placeholder", "real")


class SettingsError(ValueError):
    """One or more settings are missing or invalid. The message lists every problem."""


@dataclass(frozen=True)
class ClustererSettings:
    database_url: str
    mode: str                  # CLUSTERER_MODE: "placeholder" (skeleton) or "real" (steps 1-5)
    interval_seconds: float    # CLUSTERER_INTERVAL_SECONDS: sleep between passes (Q13)
    prepare_batch: int         # CLUSTER_PREPARE_BATCH: events prepared per pass (Q13)
    assign_threshold: float    # CLUSTER_ASSIGN_THRESHOLD: joining AND grouping (Q7, Q8)
    min_events: int            # CLUSTER_MIN_EVENTS (Q8)
    min_conversations: int     # CLUSTER_MIN_CONVERSATIONS (Q8)
    pool_window_days: int      # CLUSTER_POOL_WINDOW_DAYS (Q9)
    stub_cluster_rate: float   # STUB_CLUSTER_RATE: placeholder mode only
    summarizer_model: str      # SUMMARIZER_MODEL: the model that writes event summaries (Q19)
    summarizer_prompt_version: int  # SUMMARIZER_PROMPT_VERSION: which prompts/summarize_vN to use
    health_stall_minutes: int  # CLUSTER_HEALTH_STALL_MINUTES: rule 1, a live event waiting longer (Q12)
    health_failure_run: int    # CLUSTER_HEALTH_FAILURE_RUN: rule 2, this many summaries in a row failed
    health_alive_minutes: float  # CLUSTER_HEALTH_ALIVE_MINUTES: --alive fails if no pass for longer
    last_pass_file: str        # CLUSTERER_LAST_PASS_FILE: where each pass records its time (--alive)


# name -> (default, type)
DEFAULTS = {
    "CLUSTERER_MODE": ("placeholder", str),
    "CLUSTERER_INTERVAL_SECONDS": (10.0, float),
    "CLUSTER_PREPARE_BATCH": (50, int),
    "CLUSTER_ASSIGN_THRESHOLD": (0.30, float),   # temporary until calibrated (Q14)
    "CLUSTER_MIN_EVENTS": (3, int),
    "CLUSTER_MIN_CONVERSATIONS": (2, int),
    "CLUSTER_POOL_WINDOW_DAYS": (14, int),
    "STUB_CLUSTER_RATE": (0.5, float),
    "SUMMARIZER_MODEL": ("gpt-6-luna", str),
    "SUMMARIZER_PROMPT_VERSION": (1, int),
    "CLUSTER_HEALTH_STALL_MINUTES": (15, int),
    "CLUSTER_HEALTH_FAILURE_RUN": (10, int),
    "CLUSTER_HEALTH_ALIVE_MINUTES": (3.0, float),
    "CLUSTERER_LAST_PASS_FILE": ("/tmp/clusterer-last-pass", str),
}


def load_settings(environ=None):
    """Read and check every setting. Raises SettingsError listing all problems at once."""
    environ = os.environ if environ is None else environ
    problems = []
    values = {}

    for name, (default, kind) in DEFAULTS.items():
        raw = environ.get(name, "").strip()
        if raw == "":
            values[name] = default
            continue
        try:
            values[name] = kind(raw)
        except ValueError:
            problems.append(f"{name} must be {'a whole number' if kind is int else 'a number'}, got {raw!r}")
            values[name] = default

    database_url = environ.get("DATABASE_URL", "").strip()
    if not database_url:
        problems.append("DATABASE_URL is required")

    def check(condition, message):
        if not condition:
            problems.append(message)

    mode = values["CLUSTERER_MODE"]
    check(mode in MODES, f"CLUSTERER_MODE must be one of {', '.join(MODES)}, got {mode!r}")
    threshold = values["CLUSTER_ASSIGN_THRESHOLD"]
    check(0 < threshold <= 1,
          f"CLUSTER_ASSIGN_THRESHOLD must be between 0 and 1 (a cosine distance), got {threshold}")
    check(values["CLUSTERER_INTERVAL_SECONDS"] > 0,
          f"CLUSTERER_INTERVAL_SECONDS must be more than 0, got {values['CLUSTERER_INTERVAL_SECONDS']}")
    for name in ("CLUSTER_PREPARE_BATCH", "CLUSTER_MIN_EVENTS", "CLUSTER_MIN_CONVERSATIONS",
                 "CLUSTER_POOL_WINDOW_DAYS", "CLUSTER_HEALTH_STALL_MINUTES",
                 "CLUSTER_HEALTH_FAILURE_RUN"):
        check(values[name] >= 1, f"{name} must be at least 1, got {values[name]}")
    # Each conversation in a new cluster brings at least one event, so a cluster could never
    # form if it needed more conversations than events.
    check(values["CLUSTER_MIN_CONVERSATIONS"] <= values["CLUSTER_MIN_EVENTS"],
          "CLUSTER_MIN_CONVERSATIONS can't be more than CLUSTER_MIN_EVENTS "
          f"({values['CLUSTER_MIN_CONVERSATIONS']} > {values['CLUSTER_MIN_EVENTS']}): "
          "no cluster could ever form")
    # Passes happen every CLUSTERER_INTERVAL_SECONDS, so --alive would always fail if it allowed less.
    alive_seconds = values["CLUSTER_HEALTH_ALIVE_MINUTES"] * 60
    check(alive_seconds > values["CLUSTERER_INTERVAL_SECONDS"],
          "CLUSTER_HEALTH_ALIVE_MINUTES must allow more time than one CLUSTERER_INTERVAL_SECONDS "
          f"({values['CLUSTER_HEALTH_ALIVE_MINUTES']} min vs {values['CLUSTERER_INTERVAL_SECONDS']} s)")
    check(values["CLUSTERER_LAST_PASS_FILE"].strip() != "", "CLUSTERER_LAST_PASS_FILE can't be empty")
    check(0 <= values["STUB_CLUSTER_RATE"] <= 1,
          f"STUB_CLUSTER_RATE must be between 0 and 1, got {values['STUB_CLUSTER_RATE']}")

    version = values["SUMMARIZER_PROMPT_VERSION"]
    missing = [path.name for path in prompt_paths(version) if not path.exists()]
    check(not missing, f"SUMMARIZER_PROMPT_VERSION={version} has no prompt files "
                       f"(missing {', '.join(missing)} in tripwire/clusterer/prompts/)")

    if problems:
        raise SettingsError("clusterer settings are invalid:\n  - " + "\n  - ".join(problems))

    return ClustererSettings(
        database_url=database_url,
        mode=mode,
        interval_seconds=values["CLUSTERER_INTERVAL_SECONDS"],
        prepare_batch=values["CLUSTER_PREPARE_BATCH"],
        assign_threshold=threshold,
        min_events=values["CLUSTER_MIN_EVENTS"],
        min_conversations=values["CLUSTER_MIN_CONVERSATIONS"],
        pool_window_days=values["CLUSTER_POOL_WINDOW_DAYS"],
        stub_cluster_rate=values["STUB_CLUSTER_RATE"],
        summarizer_model=values["SUMMARIZER_MODEL"],
        summarizer_prompt_version=version,
        health_stall_minutes=values["CLUSTER_HEALTH_STALL_MINUTES"],
        health_failure_run=values["CLUSTER_HEALTH_FAILURE_RUN"],
        health_alive_minutes=values["CLUSTER_HEALTH_ALIVE_MINUTES"],
        last_pass_file=values["CLUSTERER_LAST_PASS_FILE"],
    )
