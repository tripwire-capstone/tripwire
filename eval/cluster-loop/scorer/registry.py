"""The run registry (eval/cluster-loop/runs.json): which runs exist and where their files are."""

import json
from dataclasses import dataclass
from pathlib import Path

from . import LOOP_DIR, REPO_ROOT

REGISTRY_PATH = LOOP_DIR / "runs.json"
KINDS = ("park", "tau")
SETS = ("tuning", "sealed")
FIELDS = ("id", "kind", "set", "dump", "db", "answer_key", "laminar_raw", "notes")
LIVE_DB = "tripwire"          # the live database: never restored into, replayed or scored


class RegistryError(ValueError):
    """runs.json is malformed, names an unknown run, or points at a missing file."""


@dataclass(frozen=True)
class Run:
    id: str
    kind: str
    set: str
    dump: Path
    db: str
    answer_key: Path | None    # folder holding answer_key.jsonl (park only)
    laminar_raw: Path          # compare-laminar raw.json
    notes: str

    @property
    def answer_key_file(self):
        return None if self.answer_key is None else self.answer_key / "answer_key.jsonl"

    def missing_files(self):
        paths = [self.dump, self.laminar_raw] + ([self.answer_key_file] if self.kind == "park" else [])
        return [str(path) for path in paths if path is None or not path.exists()]


def _path(text):
    if text is None:
        return None
    path = Path(text).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def parse(data):
    runs = []
    for entry in data.get("runs", []):
        missing = [field for field in FIELDS if field not in entry]
        if missing:
            raise RegistryError(f"run {entry.get('id', '?')} lacks {missing}")
        if entry["kind"] not in KINDS or entry["set"] not in SETS:
            raise RegistryError(f"run {entry['id']}: kind must be {KINDS}, set must be {SETS}")
        if entry["db"] == LIVE_DB:
            raise RegistryError(f"run {entry['id']}: db may not be the live database {LIVE_DB!r}")
        if entry["kind"] == "park" and not entry["answer_key"]:
            raise RegistryError(f"run {entry['id']}: park runs need an answer_key folder")
        runs.append(Run(entry["id"], entry["kind"], entry["set"], _path(entry["dump"]), entry["db"],
                        _path(entry["answer_key"]), _path(entry["laminar_raw"]), entry["notes"] or ""))
    ids = [run.id for run in runs]
    if len(ids) != len(set(ids)):
        raise RegistryError("run ids must be unique")
    return runs


def load(path=REGISTRY_PATH):
    return parse(json.loads(Path(path).read_text()))


def select(runs, run_ids=None, run_set=None):
    """Runs by id (in the order given) or by set (registry order). Fails loudly on unknown ids."""
    by_id = {run.id: run for run in runs}
    if run_ids:
        unknown = [run_id for run_id in run_ids if run_id not in by_id]
        if unknown:
            raise RegistryError(f"unknown run id(s) {unknown}; known: {sorted(by_id)}")
        chosen = [by_id[run_id] for run_id in run_ids]
        if run_set:
            wrong = [run.id for run in chosen if run.set != run_set]
            if wrong:
                raise RegistryError(f"run(s) {wrong} are not in the {run_set} set")
        return chosen
    if run_set:
        return [run for run in runs if run.set == run_set]
    return list(runs)


def check_files(runs):
    """Raise if any selected run's dump, raw.json or answer key is missing (never work around it)."""
    problems = [f"{run.id}: missing {path}" for run in runs for path in run.missing_files()]
    if problems:
        raise RegistryError("missing files:\n  " + "\n  ".join(problems))
