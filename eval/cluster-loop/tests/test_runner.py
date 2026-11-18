"""score_run end to end on a small real database: replay, live raw.json, --naming and
--as-version with fake LLM/embedder (no paid calls), plus the CLI's refusals."""

import json

import pytest

from scorer import cli, registry, runner
from tripwire.clusterer.embed import FakeEmbedder
from tripwire.clusterer.llm_client import LLMResult
from tripwire.llm import LLMUsage


class FakeLLM:
    """Answers the namer ({"name"}), the summarizer ({"cause","summary","details"}) and the grader."""

    def __init__(self):
        self.calls = []

    def complete(self, model, messages, json_schema):
        self.calls.append(model)
        keys = set(json_schema["properties"])
        if keys == {"name"}:
            return LLMResult({"name": "Park lookup timeouts"}, LLMUsage(cost_usd=0.01))
        if "summary" in keys:
            return LLMResult({"cause": "timeout", "summary": "The park lookup timed out.", "details": []},
                             LLMUsage(cost_usd=0.001))
        return LLMResult({"cause": 2, "fit": 2, "distinct": 1}, LLMUsage(cost_usd=0.02))


@pytest.fixture
def park_run(run_db, tmp_path, clean_env):
    """Issues: 3 near-identical events (3 conversations) that the replay clusters at 0.30, plus
    one far event. Answer key: the 3 are 'timeout', the far one 'large_party'. raw.json: the
    stored (live) clusters are empty; Laminar groups the 3 timeouts in one named cluster."""
    from tests.clustering_data import add_event, add_signal, add_summary
    from tests.vectors import vector_at, vectors_all_apart

    name, conn = run_db
    _, version_id = add_signal(conn, "Issues")
    events = [add_event(conn, version_id, vector, session_id=f"c{index}")
              for index, vector in enumerate(vectors_all_apart(3, 0.05))]
    events.append(add_event(conn, version_id, vector_at(0.9, 5), session_id="c9"))
    for event_id in events:
        add_summary(conn, event_id, f"summary of {event_id}")
    traces = {event_id: str(trace) for event_id, trace in conn.execute(
        "SELECT e.id, t.laminar_trace_id FROM signal_events e JOIN traces t ON t.id = e.trace_id")}

    key_dir = tmp_path / "key"
    key_dir.mkdir()
    turns = [{"turn": 1, "trace_id": traces[event_id], "bugs": ["timeout"]} for event_id in events[:3]]
    turns.append({"turn": 2, "trace_id": traces[events[3]], "bugs": ["large_party"]})
    (key_dir / "answer_key.jsonl").write_text(json.dumps({"convo_id": "c", "target_bug": "timeout",
                                                          "bugs_by_turn": turns}) + "\n")
    raw = {"tripwire": {"events": [{"id": event_id, "laminar_trace_id": traces[event_id], "signal_name": "Issues"}
                                   for event_id in events],
                        "member_clusters": [], "all_clusters": []},
           "laminar": {"events": [{"id": f"lm{index}", "trace_id": traces[event_id], "signal_name": "Issues",
                                   "cluster_details": [{"id": "L1", "name": "Lookup timeouts", "level": 1}]}
                                  for index, event_id in enumerate(events[:3])]}}
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(json.dumps(raw))
    run = registry.Run("parktest", "park", "tuning", None, name, key_dir, raw_path, "test run")
    return run, events


def test_score_run_replays_and_scores_live_and_replay(park_run, monkeypatch):
    run, _ = park_run
    monkeypatch.setenv("CLUSTER_MIN_EVENTS", "99")       # a stray shell setting must not leak in
    result = runner.score_run(run, 0.30, log=lambda message: None)

    assert result["settings"]["pool_window_days"] == 36500 and result["settings"]["min_events"] == 3
    assert result["stored_vs_raw"]["identical"]
    live = result["live"]["issues"]
    assert live["coverage"]["tripwire"]["n_unclustered"] == 4             # nothing stored
    replay = result["replay"]["issues"]
    assert replay["coverage"]["tripwire"]["n_clusters"] == 1 and replay["coverage"]["tripwire"]["n_unclustered"] == 1
    assert replay["answer_key"]["tripwire"]["precision"] == pytest.approx(1.0)
    assert replay["answer_key"]["tripwire"]["recall"] == pytest.approx(1.0)
    assert replay["agreement"]["n_scored"] == 3 and replay["agreement"]["recall"] == pytest.approx(1.0)
    assert live["agreement"]["recall"] == pytest.approx(1 / 3)            # live: all alone
    assert "naming" not in result


def test_naming_end_to_end_with_fake_namer_grader_and_embedder(park_run):
    run, _ = park_run
    llm = FakeLLM()
    paid = runner.Paid(llm=llm, embedder=FakeEmbedder(), grader=llm, grader_model="fake-grader")
    result = runner.score_run(run, 0.30, with_naming=True, paid=paid, log=lambda message: None)

    naming = result["naming"]["issues"]
    assert naming["n_named"] == 1 and naming["n_fallback"] == 0
    assert naming["keyword"]["score"] == 1.0                               # "timeout" matches the name
    assert naming["grade"]["score"] == pytest.approx(5 / 6)
    assert naming["laminar_match"]["n_pairs"] == 1
    mapping = result["replay"]["issues"]["agreement"]["mapping"][0]
    assert mapping["tripwire_name"] == "Park lookup timeouts"
    assert result["costs"]["namer_calls"] == 1 and result["costs"]["grader_calls"] == 1


def test_as_version_resummarizes_then_replays_with_fakes(park_run):
    run, events = park_run
    llm = FakeLLM()
    paid = runner.Paid(llm=llm, embedder=FakeEmbedder())
    result = runner.score_run(run, 0.30, as_version=5, signals=["Issues"], paid=paid, log=lambda message: None)
    assert result["costs"]["summarizer_calls"] == len(events)
    assert result["as_version_not_summarized"] == []
    # identical fake summaries embed identically: every event lands in one cluster
    assert result["replay"]["issues"]["coverage"]["tripwire"]["n_clusters"] == 1


def test_run_refuses_sealed_runs(monkeypatch, capsys):
    sealed = registry.Run("park3", "park", "sealed", None, "tripwire_park3", None, None, "")
    monkeypatch.setattr(registry, "load", lambda: [sealed])
    assert cli.main(["run", "--runs", "park3"]) == 2
    assert "not in the tuning set" in capsys.readouterr().err


def test_check_exits_non_zero_on_a_mismatch(monkeypatch):
    from scorer import check
    monkeypatch.setattr(registry, "check_files", lambda runs: None)
    monkeypatch.setattr(check, "compute", lambda runs: [("s", "item", 0.95, 0.89)])
    assert cli.main(["check"]) == 1
    monkeypatch.setattr(check, "compute", lambda runs: [("s", "item", 0.95, 0.951)])
    assert cli.main(["check"]) == 0


def test_retrying_embedder_retries_service_errors_then_gives_up():
    from tripwire.llm import LLMServiceError

    class Flaky:
        model = "fake"

        def __init__(self, failures):
            self.failures, self.calls = failures, 0

        def embed(self, texts):
            self.calls += 1
            if self.calls <= self.failures:
                raise LLMServiceError("500")
            return [[1.0] for _ in texts]

    waits = []
    flaky = Flaky(failures=2)
    assert runner.RetryingEmbedder(flaky, sleep=waits.append).embed(["a"]) == [[1.0]]
    assert flaky.calls == 3 and waits == [5.0, 10.0]
    with pytest.raises(LLMServiceError):
        runner.RetryingEmbedder(Flaky(failures=3), sleep=waits.append).embed(["a"])


def test_retrying_embedder_does_not_retry_other_errors():
    class Broken:
        model = "fake"
        calls = 0

        def embed(self, texts):
            Broken.calls += 1
            raise ValueError("bad request")

    with pytest.raises(ValueError):
        runner.RetryingEmbedder(Broken(), sleep=lambda seconds: None).embed(["a"])
    assert Broken.calls == 1
