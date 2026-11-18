"""Gate bookkeeping, the package hash and the registry (no database)."""

import json
from datetime import datetime, timezone

import pytest

from scorer import gate, hashing, registry, render


def run(run_id, run_set="sealed"):
    return registry.Run(run_id, "park", run_set, None, f"tripwire_{run_id}", None, None, "")


# ------------------------------------------------------------------ gate

def test_gate_refuses_a_fourth_use_and_counts_per_run(tmp_path):
    log = tmp_path / "GATE_LOG.md"
    for _ in range(3):
        gate.append(["park3"], {}, "abc", True, "hash", log_path=log)
    gate.append(["park5"], {}, "abc", True, "hash", log_path=log)
    assert gate.uses(log) == {"park3": 3, "park5": 1}
    gate.check_allowed([run("park5")], log_path=log)                     # 1 use: fine
    with pytest.raises(gate.GateError, match="park3"):
        gate.check_allowed([run("park5"), run("park3")], log_path=log)


def test_gate_refuses_tuning_runs(tmp_path):
    with pytest.raises(gate.GateError, match="sealed runs only"):
        gate.check_allowed([run("park2", "tuning")], log_path=tmp_path / "log.md")


def test_gate_line_has_time_commit_tree_hash_runs_and_aggregates(tmp_path):
    log = tmp_path / "GATE_LOG.md"
    line = gate.append(["park3", "tau2"], {"x": 1}, "deadbeef", False, "cafe", log_path=log,
                       now=datetime(2026, 12, 1, 9, 30, tzinfo=timezone.utc))
    assert line == ('- 2026-12-01T09:30:00Z | commit deadbeef | tree dirty | scorer cafe | runs park3,tau2 '
                    '| {"x": 1}\n')
    assert gate.uses(log) == {"park3": 1, "tau2": 1}


def test_gate_aggregates_hold_no_names_or_ids():
    result = {"replay": {"issues": {
        "name": "Issues",
        "answer_key": {"tripwire": {"precision": 0.9, "recall": 0.5, "f05": 0.78, "n_labeled": 10, "n_events": 12}},
        "agreement": {"precision": 0.8, "recall": 0.4, "n_shared": 5, "n_scored": 4, "n_tripwire_unclustered": 1,
                      "mapping": [{"laminar_cluster": "uuid-1", "laminar_name": "Secret name",
                                   "tripwire_cluster": 7, "tripwire_name": "Other secret", "n_traces": 3,
                                   "n_shared": 2}]},
        "coverage": {"tripwire": {"n_events": 12, "n_clusters": 3, "n_unclustered": 2, "pct_unclustered": 16.7,
                                  "largest_share": 0.5}}}}}
    text = json.dumps(render.gate_aggregates(result))
    assert "Secret" not in text and "uuid-1" not in text and "mapping" not in text and "Issues" not in text
    assert json.loads(text)["issues"]["answer_key"]["f05"] == 0.78


# ------------------------------------------------------------------ hash

def test_hash_is_stable_changes_with_content_and_ignores_pycache(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "p.md").write_text("rubric")
    first = hashing.scorer_hash(tmp_path)
    assert hashing.scorer_hash(tmp_path) == first
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"junk")
    assert hashing.scorer_hash(tmp_path) == first
    (tmp_path / "prompts" / "p.md").write_text("rubric v2")
    assert hashing.scorer_hash(tmp_path) != first


# ------------------------------------------------------------------ registry

def entry(**overrides):
    base = {"id": "park9", "kind": "park", "set": "tuning", "dump": "/nope.dump", "db": "tripwire_park9",
            "answer_key": "/nope", "laminar_raw": "/nope.json", "notes": ""}
    base.update(overrides)
    return base


def test_registry_refuses_the_live_database():
    with pytest.raises(registry.RegistryError, match="live database"):
        registry.parse({"runs": [entry(db="tripwire")]})


def test_registry_reports_missing_files_instead_of_working_around_them():
    runs = registry.parse({"runs": [entry()]})
    with pytest.raises(registry.RegistryError, match="missing"):
        registry.check_files(runs)


def test_registry_select_fails_loudly_on_unknown_ids_and_wrong_set():
    runs = registry.parse({"runs": [entry(), entry(id="park3", set="sealed")]})
    with pytest.raises(registry.RegistryError, match="unknown"):
        registry.select(runs, ["park7"])
    with pytest.raises(registry.RegistryError, match="not in the tuning set"):
        registry.select(runs, ["park3"], "tuning")
    assert [run.id for run in registry.select(runs, run_set="sealed")] == ["park3"]


def test_the_real_registry_parses():
    assert {run.id for run in registry.load()} >= {"park1b", "park2", "park4", "tau1"}


# ------------------------------------------------------------------ check: accepted rows

def test_accepted_row_passes_only_at_its_accepted_value():
    from scorer import check
    accepted = {("s", "row"): (0.85, "why")}
    assert check.status("s", "row", 0.95, 0.851, accepted) == "accepted"
    assert check.status("s", "row", 0.95, 0.87, accepted) == "MISMATCH"     # drifted: fails again
    assert check.status("s", "row", 0.95, 0.95, accepted) == "spec-now"     # matches the spec again
    assert check.status("s", "other", 0.95, 0.85, accepted) == "MISMATCH"   # not accepted
    assert check.status("s", "other", 0.95, 0.954, accepted) == "ok"


def test_render_fails_only_on_mismatches_and_prints_reasons():
    from scorer import check
    accepted = {("s", "a"): (27, "two clusters count for both")}
    rows = [("s", "a", 24, 27), ("s", "b", 0.5, 0.5)]
    text, failures = check.render(rows, accepted)
    assert failures == 0 and "two clusters count for both" in text and "1 accepted" in text
    _, failures = check.render(rows + [("s", "c", 0.5, 0.6)], accepted)
    assert failures == 1


def test_every_accepted_row_names_a_real_expected_row():
    from scorer import check
    items = {(f"answer key, live", f"{run} {key} {system} {name}")
             for (run, key, system) in check.ANSWER_KEY_LIVE for name in ("P", "R", "F0.5")}
    items |= {("agreement, live", f"{run} {key} {name}")
              for (run, key) in check.AGREEMENT for name in ("shared", "scored", "TW-unclustered", "P", "R")}
    items |= {("tau1 Issues mapping", f"{name} {suffix}")
              for name in check.TAU1_ISSUES_MAPPING for suffix in ("scored traces", "-> count")}
    assert set(check.ACCEPTED) <= items
