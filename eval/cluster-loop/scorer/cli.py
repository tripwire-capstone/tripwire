"""The `score` command: restore | run | gate | check | hash. See eval/cluster-loop/README.md."""

import argparse
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import LOOP_DIR, REPO_ROOT, check, gate, hashing, registry, render, restore, runner

OUT_DIR = LOOP_DIR / "out"


def git_state():
    def git(*args):
        return subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True).stdout.strip()
    return git("rev-parse", "HEAD") or "unknown", git("status", "--porcelain") == ""


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="score", description="Frozen cluster-quality scorer.")
    commands = parser.add_subparsers(dest="command", required=True)

    restore_parser = commands.add_parser("restore", help="restore run dumps into their databases")
    group = restore_parser.add_mutually_exclusive_group()
    group.add_argument("--run", action="append", dest="runs", metavar="ID")
    group.add_argument("--set", choices=registry.SETS)

    run_parser = commands.add_parser("run", help="replay and score tuning runs")
    run_parser.add_argument("--set", choices=["tuning"], default="tuning",
                            help="only the tuning set (sealed runs go through `score gate`)")
    run_parser.add_argument("--runs", nargs="+", metavar="ID")
    run_parser.add_argument("--threshold", type=float, default=runner.default_threshold())
    run_parser.add_argument("--as-version", type=int, help="re-summarize under this prompt version first (paid)")
    run_parser.add_argument("--signals", nargs="+", metavar="NAME", help="limit to these signals (names)")
    run_parser.add_argument("--naming", action="store_true",
                            help="name replayed clusters with the real namer and score names (paid)")
    run_parser.add_argument("--grader-model", help="with --naming: run the rubric grader with this model "
                                                   "(only once the user has approved model and rubric)")
    run_parser.add_argument("--out", help="output folder (default eval/cluster-loop/out/<UTC timestamp>)")

    gate_parser = commands.add_parser("gate", help="score sealed runs: aggregates only, logged, 3 uses each")
    group = gate_parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="append", dest="runs", metavar="SEALED_ID")
    group.add_argument("--set", choices=["sealed"])
    gate_parser.add_argument("--threshold", type=float, default=runner.default_threshold())

    commands.add_parser("check", help="recompute the spec's expected numbers (acceptance test)")
    commands.add_parser("hash", help="sha256 of the scorer package")
    args = parser.parse_args(argv)
    if getattr(args, "grader_model", None) and not args.naming:
        parser.error("--grader-model needs --naming")
    if hasattr(args, "threshold") and not 0 < args.threshold <= 1:
        parser.error("--threshold must be in (0, 1]")
    return args


def cmd_restore(args):
    runs = registry.select(registry.load(), args.runs, args.set)
    registry.check_files(runs)
    for run in runs:
        restore.restore_run(run)
    return 0


def cmd_run(args, argv):
    runs = registry.select(registry.load(), args.runs, args.set)
    sealed = [run.id for run in runs if run.set != "tuning"]
    if sealed:
        raise registry.RegistryError(f"`score run` never scores sealed runs ({sealed}); use `score gate`")
    registry.check_files(runs)
    paid = runner.Paid.real(args.grader_model) if (args.as_version is not None or args.naming) else None
    results = [runner.score_run(run, args.threshold, as_version=args.as_version, signals=args.signals,
                                with_naming=args.naming, paid=paid) for run in runs]
    commit, clean = git_state()
    now = datetime.now(timezone.utc)
    meta = {"created_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "git_commit": commit, "git_clean": clean,
            "scorer_hash": hashing.scorer_hash(), "command": "score " + shlex.join(argv)}
    averages = render.averages(results) if len(results) > 1 else {}
    out_dir = Path(args.out).expanduser().resolve() if args.out else OUT_DIR / now.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "scores.json").write_text(json.dumps({"meta": meta, "averages": averages, "runs": results},
                                                    indent=2, default=str))
    (out_dir / "scores.md").write_text(render.render_markdown(meta, results, averages))
    print(f"wrote {out_dir / 'scores.md'} and scores.json")
    return 0


def cmd_gate(args):
    runs = registry.select(registry.load(), args.runs, args.set)
    if not runs:
        raise registry.RegistryError("no sealed runs in runs.json yet")
    gate.check_allowed(runs)
    registry.check_files(runs)
    aggregates = {}
    for run in runs:
        result = runner.score_run(run, args.threshold, log=lambda message: None)
        aggregates[run.id] = render.gate_aggregates(result)
    commit, clean = git_state()
    line = gate.append([run.id for run in runs], {"threshold": args.threshold, "runs": aggregates}, commit,
                       clean, hashing.scorer_hash())
    print(json.dumps(aggregates, indent=2, sort_keys=True))
    print(f"\nlogged to {gate.GATE_LOG.relative_to(REPO_ROOT)}: {line[:80]}...")
    return 0


def cmd_check(args):
    runs_by_id = {run.id: run for run in registry.load()}
    registry.check_files([runs_by_id[run_id] for run_id in ("park2", "park4", "tau1")])
    text, failures = check.render(check.compute(runs_by_id))
    print(text)
    return 1 if failures else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = parse_args(argv)
    try:
        if args.command == "restore":
            return cmd_restore(args)
        if args.command == "run":
            return cmd_run(args, argv)
        if args.command == "gate":
            return cmd_gate(args)
        if args.command == "check":
            return cmd_check(args)
        print(hashing.scorer_hash())
        return 0
    except (registry.RegistryError, restore.RestoreError, gate.GateError) as error:
        print(f"score: {error}", file=sys.stderr)
        return 2
