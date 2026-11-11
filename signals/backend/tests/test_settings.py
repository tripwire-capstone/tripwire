"""Tests for the clusterer's settings: defaults, every check, and .env.example staying in step."""

import re
from pathlib import Path

import pytest

from tripwire.clusterer.settings import DEFAULTS, SettingsError, load_settings

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"
BASE = {"DATABASE_URL": "postgresql://example/tripwire"}


def settings_with(**overrides):
    return load_settings({**BASE, **{name: str(value) for name, value in overrides.items()}})


def problem_with(**overrides):
    with pytest.raises(SettingsError) as raised:
        settings_with(**overrides)
    return str(raised.value)


def test_load_settings_uses_design_defaults_when_nothing_is_set():
    settings = load_settings(BASE)
    assert settings.mode == "placeholder"
    assert settings.interval_seconds == 10
    assert settings.prepare_batch == 50
    assert settings.assign_threshold == 0.30
    assert settings.min_events == 3
    assert settings.min_conversations == 2
    assert settings.pool_window_days == 14


def test_load_settings_reads_values_from_the_environment():
    settings = settings_with(CLUSTERER_MODE="real", CLUSTER_ASSIGN_THRESHOLD="0.25", CLUSTER_MIN_EVENTS="4")
    assert (settings.mode, settings.assign_threshold, settings.min_events) == ("real", 0.25, 4)


def test_load_settings_treats_blank_values_as_unset():
    assert settings_with(CLUSTER_MIN_EVENTS="  ").min_events == 3


def test_load_settings_requires_database_url():
    with pytest.raises(SettingsError, match="DATABASE_URL is required"):
        load_settings({})


def test_load_settings_rejects_unknown_mode():
    assert "CLUSTERER_MODE must be one of placeholder, real, got 'rel'" in problem_with(CLUSTERER_MODE="rel")


def test_load_settings_rejects_threshold_outside_zero_to_one():
    assert "CLUSTER_ASSIGN_THRESHOLD must be between 0 and 1" in problem_with(CLUSTER_ASSIGN_THRESHOLD=3)
    assert "CLUSTER_ASSIGN_THRESHOLD" in problem_with(CLUSTER_ASSIGN_THRESHOLD=0)


def test_load_settings_accepts_threshold_of_exactly_one():
    assert settings_with(CLUSTER_ASSIGN_THRESHOLD=1).assign_threshold == 1


def test_load_settings_rejects_text_where_a_number_belongs():
    assert "CLUSTER_ASSIGN_THRESHOLD must be a number, got '0.3O'" in problem_with(CLUSTER_ASSIGN_THRESHOLD="0.3O")
    assert "CLUSTER_MIN_EVENTS must be a whole number, got '3.5'" in problem_with(CLUSTER_MIN_EVENTS="3.5")


def test_load_settings_rejects_counts_below_one():
    for name in ("CLUSTER_PREPARE_BATCH", "CLUSTER_MIN_EVENTS", "CLUSTER_POOL_WINDOW_DAYS"):
        assert f"{name} must be at least 1, got 0" in problem_with(**{name: 0})


def test_load_settings_rejects_more_conversations_than_events():
    assert "no cluster could ever form" in problem_with(CLUSTER_MIN_EVENTS=2, CLUSTER_MIN_CONVERSATIONS=3)


def test_load_settings_rejects_interval_of_zero():
    assert "CLUSTERER_INTERVAL_SECONDS must be more than 0" in problem_with(CLUSTERER_INTERVAL_SECONDS=0)


def test_load_settings_lists_every_problem_at_once():
    message = problem_with(CLUSTERER_MODE="rel", CLUSTER_ASSIGN_THRESHOLD=3)
    assert "CLUSTERER_MODE" in message and "CLUSTER_ASSIGN_THRESHOLD" in message


def test_env_example_lists_every_clusterer_setting_with_the_same_default():
    # Anti-drift: .env.example documents the defaults, settings.py owns them. They must agree.
    documented = {}
    for line in ENV_EXAMPLE.read_text().splitlines():
        found = re.match(r"^#?\s*([A-Z_]+)=(.*)$", line.strip())
        if found:
            documented[found.group(1)] = found.group(2).strip()
    for name, (default, kind) in DEFAULTS.items():
        assert name in documented, f"{name} is missing from .env.example"
        assert kind(documented[name]) == default, f"{name}: .env.example says {documented[name]}, code says {default}"


def test_summarizer_defaults_to_gpt_6_luna_and_prompt_version_1():
    settings = load_settings(BASE)
    assert (settings.summarizer_model, settings.summarizer_prompt_version) == ("gpt-6-luna", 1)


def test_load_settings_rejects_a_prompt_version_with_no_prompt_files():
    message = problem_with(SUMMARIZER_PROMPT_VERSION=12)
    assert "SUMMARIZER_PROMPT_VERSION=12 has no prompt files" in message
    assert "summarize_v12.md" in message
