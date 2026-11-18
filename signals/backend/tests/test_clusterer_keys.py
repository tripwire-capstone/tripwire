"""The clusterer's OpenAI key: looked up every pass, clients swapped in place when it changes."""

import logging

import pytest

from tripwire.clusterer.__main__ import OpenAISteps, skip_naming, skip_prepare
from tripwire.clusterer.llm_naming import Namer
from tripwire.clusterer.naming import name_new_clusters
from tripwire.clusterer.settings import load_settings
from tripwire.project_keys import KeyWatch, save_key

BASE = {"DATABASE_URL": "postgresql://unused"}


class Client:
    """Stands in for OpenAIClient / OpenAIEmbedder: remembers the key it was built with."""

    def __init__(self, api_key):
        self.api_key = api_key


@pytest.fixture
def keys_env(monkeypatch):
    monkeypatch.setenv("TRIPWIRE_SECRET_KEY", "ab" * 32)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai-key-1111")


def steps(**settings):
    return OpenAISteps(load_settings({**BASE, **settings}), KeyWatch(logging.getLogger("clusterer-test")),
                       make_llm=Client, make_embedder=Client)


def project(conn):
    return conn.execute("SELECT id FROM projects").fetchone()[0]


def test_clusterer_without_a_key_skips_the_openai_steps(conn, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert steps().for_pass(conn) == (skip_prepare, skip_naming)


def test_clusterer_without_a_key_still_runs_naming_when_it_needs_no_llm(conn, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert steps(NAMING_MODEL="off").for_pass(conn) == (skip_prepare, name_new_clusters)


def test_clusterer_builds_its_clients_with_the_environment_key(conn, keys_env):
    openai = steps()
    prepare, name = openai.for_pass(conn)
    assert prepare == openai.preparer.prepare_events and isinstance(name, Namer)
    assert (openai.preparer.llm.api_key, openai.preparer.embedder.api_key, name.llm.api_key) == \
        ("sk-env-openai-key-1111",) * 3


def test_clusterer_keeps_its_clients_while_the_key_is_unchanged(conn, keys_env):
    openai = steps()
    openai.for_pass(conn)
    llm = openai.preparer.llm
    openai.for_pass(conn)
    assert openai.preparer.llm is llm


def test_clusterer_swaps_in_a_key_saved_in_settings_and_keeps_its_preparer(conn, keys_env):
    openai = steps()
    openai.for_pass(conn)
    preparer, namer = openai.preparer, openai.name
    preparer.skipped.add(42)   # state kept between passes (Q21)

    save_key(conn, project(conn), "openai", "sk-saved-key-2222222")
    openai.for_pass(conn)

    assert openai.preparer is preparer and openai.name is namer and preparer.skipped == {42}
    assert (preparer.llm.api_key, preparer.embedder.api_key, namer.llm.api_key) == ("sk-saved-key-2222222",) * 3


def test_clusterer_starts_working_once_a_key_is_saved(conn, keys_env, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    openai = steps()
    assert openai.for_pass(conn)[0] is skip_prepare
    save_key(conn, project(conn), "openai", "sk-saved-key-2222222")
    assert openai.for_pass(conn)[0] == openai.preparer.prepare_events


def test_clusterer_builds_its_preparer_with_the_summary_threads_setting(conn, keys_env):
    default = steps()
    default.for_pass(conn)
    assert default.preparer.threads == 4
    three = steps(CLUSTERER_SUMMARY_THREADS="3")
    three.for_pass(conn)
    assert three.preparer.threads == 3
