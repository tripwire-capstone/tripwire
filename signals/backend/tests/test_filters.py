import pytest

from tripwire.filters import FilterError, matches


def test_no_filters_match_every_trace():
    assert matches([], {"status": "success"})


def test_status_filter_matches_and_rejects():
    errors_only = [{"field": "status", "op": "eq", "value": "error"}]
    assert matches(errors_only, {"status": "error"})
    assert not matches(errors_only, {"status": "success"})


def test_unknown_filter_raises():
    with pytest.raises(FilterError):
        matches([{"field": "span_name", "op": "includes", "value": ["x"]}], {"status": "success"})