"""Tests for run_in_parallel: really concurrent, results in input order, one failure stays contained."""

import threading

import pytest

from tripwire.clusterer.parallel import CallResult, run_in_parallel

BARRIER_TIMEOUT = 2   # seconds; only reached when the calls are NOT running at the same time


def test_calls_really_run_at_the_same_time():
    # Each call waits until all 4 have started. One at a time, the first call would wait alone
    # and the barrier would break after BARRIER_TIMEOUT: the test fails instead of hanging.
    barrier = threading.Barrier(4, timeout=BARRIER_TIMEOUT)

    def wait_for_the_others(item):
        barrier.wait()
        return item

    results = run_in_parallel(wait_for_the_others, [1, 2, 3, 4], threads=4)
    assert [result.error for result in results] == [None] * 4


def test_fewer_threads_than_items_never_runs_more_than_threads_at_once():
    lock = threading.Lock()
    running = peak = 0
    release = threading.Event()

    def count(item):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        release.wait(0.05)     # hold the thread a moment so overlaps can happen
        with lock:
            running -= 1
        return item

    results = run_in_parallel(count, list(range(10)), threads=3)
    assert [result.outcome for result in results] == list(range(10))
    assert peak <= 3


def test_results_come_back_in_input_order_not_finishing_order():
    first_may_finish = threading.Event()

    def slow_first(item):
        if item == "first":
            assert first_may_finish.wait(BARRIER_TIMEOUT), "the other calls never finished"
        else:
            first_may_finish.set()   # "second" finishes before "first" does
        return item.upper()

    results = run_in_parallel(slow_first, ["first", "second"], threads=2)
    assert [result.outcome for result in results] == ["FIRST", "SECOND"]


def test_one_call_raising_does_not_affect_the_others():
    def fail_on_two(item):
        if item == 2:
            raise KeyError("bug in our code")
        return item * 10

    results = run_in_parallel(fail_on_two, [1, 2, 3], threads=3)
    assert [result.outcome for result in results] == [10, None, 30]
    assert [result.error for result in results][0] is None
    assert isinstance(results[1].error, KeyError)
    assert results[2].error is None


def test_a_returned_exception_is_an_outcome_not_an_error():
    # The summarizer returns an LLMError as the call's outcome (decide() judges it); only a raised
    # exception is a bug. The two must stay apart.
    returned = ValueError("rate limited")
    [result] = run_in_parallel(lambda item: returned, ["x"], threads=1)
    assert (result.outcome, result.error) == (returned, None)


def test_one_thread_runs_items_one_at_a_time_in_order():
    order = []
    results = run_in_parallel(order.append, ["a", "b", "c"], threads=1)
    assert order == ["a", "b", "c"]
    assert len(results) == 3


def test_seconds_is_measured_per_call():
    ticks = iter([0.0, 1.5, 10.0, 12.5])    # one thread: start/end of call 1, then call 2
    results = run_in_parallel(lambda item: item, ["a", "b"], threads=1, clock=lambda: next(ticks))
    assert [result.seconds for result in results] == [1.5, 2.5]


def test_no_items_means_no_results():
    assert run_in_parallel(lambda item: item, [], threads=4) == []


def test_threads_below_one_is_refused():
    with pytest.raises(ValueError, match="threads must be at least 1, got 0"):
        run_in_parallel(lambda item: item, [1], threads=0)


def test_call_result_holds_outcome_error_and_seconds():
    assert CallResult("done", None, 2.7) == CallResult(outcome="done", error=None, seconds=2.7)
