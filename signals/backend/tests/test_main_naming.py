"""Which step 4 the clusterer runs: LLM naming, or naming.py when NAMING_MODEL=off (SIG-65)."""

from tripwire.clusterer.__main__ import naming_step
from tripwire.clusterer.llm_client import FakeLLM
from tripwire.clusterer.llm_naming import Namer
from tripwire.clusterer.naming import name_new_clusters
from tripwire.clusterer.settings import load_settings

BASE = {"DATABASE_URL": "postgresql://unused", "OPENAI_API_KEY": "test-key"}


def test_naming_uses_the_llm_with_the_configured_settings_by_default():
    llm = FakeLLM([])
    step = naming_step(load_settings(BASE), llm)
    assert isinstance(step, Namer)
    assert (step.llm, step.model, step.growth, step.max_renames) == (llm, "gpt-6.1-sol", 2.0, 10)


def test_naming_model_off_runs_the_old_step():
    assert naming_step(load_settings({**BASE, "NAMING_MODEL": "off"}), FakeLLM([])) is name_new_clusters
