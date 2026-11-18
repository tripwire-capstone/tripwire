"""`score gate`: score sealed runs, show and log only aggregates, at most 3 uses per sealed run.

GATE_LOG.md gets one line per use:
  - <UTC> | commit <sha> | tree clean|dirty | scorer <sha256> | runs <id,...> | <aggregates JSON>
A run's uses are counted from the `runs` field of earlier lines.
"""

import json
from datetime import datetime, timezone

from . import LOOP_DIR

GATE_LOG = LOOP_DIR / "GATE_LOG.md"
MAX_USES = 3
HEADER = "# Gate log (sealed runs: aggregates only; at most 3 uses per run)\n\n"


class GateError(RuntimeError):
    pass


def uses(log_path=GATE_LOG):
    counts = {}
    if not log_path.exists():
        return counts
    for line in log_path.read_text().splitlines():
        if not line.startswith("- "):
            continue
        fields = [field.strip() for field in line[2:].split(" | ")]
        runs = next((field[len("runs "):] for field in fields if field.startswith("runs ")), "")
        for run_id in filter(None, (part.strip() for part in runs.split(","))):
            counts[run_id] = counts.get(run_id, 0) + 1
    return counts


def check_allowed(runs, log_path=GATE_LOG):
    not_sealed = [run.id for run in runs if run.set != "sealed"]
    if not_sealed:
        raise GateError(f"gate is for sealed runs only; {not_sealed} are not sealed (use `score run`)")
    used = uses(log_path)
    spent = [f"{run.id} ({used[run.id]} uses)" for run in runs if used.get(run.id, 0) >= MAX_USES]
    if spent:
        raise GateError(f"refusing: {', '.join(spent)} already used {MAX_USES} times")


def append(run_ids, aggregates, commit, clean, scorer_sha, log_path=GATE_LOG, now=None):
    now = now or datetime.now(timezone.utc)
    line = (f"- {now.strftime('%Y-%m-%dT%H:%M:%SZ')} | commit {commit} | tree {'clean' if clean else 'dirty'} | "
            f"scorer {scorer_sha} | runs {','.join(run_ids)} | {json.dumps(aggregates, sort_keys=True)}\n")
    if not log_path.exists():
        log_path.write_text(HEADER)
    with log_path.open("a") as handle:
        handle.write(line)
    return line
