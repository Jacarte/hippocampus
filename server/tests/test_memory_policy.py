"""Test policy wiring, without claiming an LLM always follows the instructions."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.memory_policy import DEFAULT_MEMORY_INSTRUCTIONS, MEMORY_POLICY_VERSION
from services.runtime import get_config_from_env, initialize_memory


def _app() -> FastAPI:
    app = FastAPI()
    app.state.memory_factory = lambda config: {"config": config}
    return app


def test_environment_startup_loads_policy_at_the_sdk_config_level(monkeypatch):
    monkeypatch.delenv("MEM0_CUSTOM_INSTRUCTIONS", raising=False)
    config = get_config_from_env()

    assert config["custom_instructions"] == DEFAULT_MEMORY_INSTRUCTIONS
    assert MEMORY_POLICY_VERSION in config["custom_instructions"]
    assert "custom_instructions" not in config["llm"]["config"]


@pytest.mark.parametrize("override", ["Remember only supported project decisions.", ""])
def test_environment_override_replaces_default_policy(monkeypatch, override):
    monkeypatch.setenv("MEM0_CUSTOM_INSTRUCTIONS", override)

    assert get_config_from_env()["custom_instructions"] == override


def test_explicit_configuration_inherits_policy_without_mutating_input(monkeypatch):
    monkeypatch.delenv("MEM0_CUSTOM_INSTRUCTIONS", raising=False)
    app = _app()
    config: dict[str, Any] = {"llm": {"provider": "ollama", "config": {}}}

    memory = initialize_memory(app, config)

    assert "custom_instructions" not in config
    assert memory["config"]["custom_instructions"] == DEFAULT_MEMORY_INSTRUCTIONS
    assert app.state.memory_config == memory["config"]


def test_explicit_configuration_uses_environment_policy_when_omitted(monkeypatch):
    monkeypatch.setenv("MEM0_CUSTOM_INSTRUCTIONS", "Deployment-specific policy.")
    app = _app()

    initialize_memory(app, {"llm": {"provider": "ollama", "config": {}}})

    assert app.state.memory_config["custom_instructions"] == "Deployment-specific policy."


@pytest.mark.parametrize("override", ["Request-specific policy.", "", None])
def test_explicit_policy_override_wins_over_environment(monkeypatch, override):
    monkeypatch.setenv("MEM0_CUSTOM_INSTRUCTIONS", "Environment policy.")
    app = _app()
    config = {
        "llm": {"provider": "ollama", "config": {}},
        "custom_instructions": override,
    }

    initialize_memory(app, config)

    assert app.state.memory_config == config
    assert app.state.memory_config["custom_instructions"] == override


def test_failed_reconfiguration_keeps_the_previous_policy_and_memory(monkeypatch):
    monkeypatch.delenv("MEM0_CUSTOM_INSTRUCTIONS", raising=False)
    app = _app()
    previous = initialize_memory(app, {"llm": {"provider": "ollama", "config": {}}})
    previous_config = app.state.memory_config

    def fail(_config):
        raise RuntimeError("backend unavailable")

    app.state.memory_factory = fail
    with pytest.raises(RuntimeError, match="Failed to initialize Memory instance"):
        initialize_memory(
            app,
            {
                "llm": {"provider": "ollama", "config": {}},
                "custom_instructions": "Replacement policy.",
            },
        )

    assert app.state.memory is previous
    assert app.state.memory_config is previous_config


def test_pinned_sdk_accepts_the_effective_policy(monkeypatch):
    from mem0.configs.base import MemoryConfig

    monkeypatch.delenv("MEM0_CUSTOM_INSTRUCTIONS", raising=False)
    config = MemoryConfig(**get_config_from_env())

    assert config.custom_instructions == DEFAULT_MEMORY_INSTRUCTIONS
