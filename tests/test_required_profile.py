"""Strict agent uses a dedicated profile without inventing a default model."""

import pytest

from test_agent_broker_isolation import agent_broker, _fake_info


def test_registry_built_from_config_declares_profile_and_required_files():
    registry, aliases = agent_broker.build_registry(
        {
            "strict": {
                "cli": "codex",
                "profile_path": "~/.codex-strict",
                "required_profile_files": ["config.toml", "auth.json"],
                "aliases": ["s"],
            }
        }
    )
    info = registry["strict"]
    assert info["worker_cli"] == "codex"
    assert info["profile_path"].endswith("/.codex-strict")
    assert info["env_overrides"]["CODEX_HOME"] == info["profile_path"]
    assert info["default_model"] == "unknown"
    assert info["required_profile_files"] == ("config.toml", "auth.json")
    assert aliases == {"s": "strict"}


def test_unknown_cli_in_config_is_rejected():
    with pytest.raises(ValueError, match="'cli' must be one of"):
        agent_broker.build_registry({"x": {"cli": "nope"}})


def test_strict_dispatch_preserves_account_default_and_workspace(monkeypatch, tmp_path):
    strict = _fake_info(tmp_path, "strict")
    strict.update(default_model="unknown", reasoning_effort="unknown")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"strict": strict})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": True, "allow_execute": True})
    captured = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kw: captured.append(kw) or "strict-job")
    result = agent_broker.dispatch_agent("strict", "Scoped research", "research")
    assert result["selected_agent"] == "strict"
    assert captured[0]["env"]["CODEX_HOME"] == strict["profile_path"]
    assert "-m" not in captured[0]["cmd"]
    assert "model_reasoning_effort=unknown" not in captured[0]["cmd"]
    assert "workspace-write" in captured[0]["cmd"]
    agent_broker.dispatch_agent("strict", "Scoped research", "research", model="strict-model", reasoning_effort="high")
    argv = captured[1]["cmd"]
    assert argv[argv.index("-m") + 1] == "strict-model"
    assert "model_reasoning_effort=high" in argv


@pytest.mark.parametrize("missing", ["config.toml", "auth.json"])
def test_strict_incomplete_profile_cannot_dispatch(monkeypatch, tmp_path, missing):
    from pathlib import Path

    strict = _fake_info(tmp_path, "strict")
    strict["required_profile_files"] = ("config.toml", "auth.json")
    for name in strict["required_profile_files"]:
        if name != missing:
            (Path(strict["profile_path"]) / name).write_text("{}")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"strict": strict})
    assert agent_broker.list_agents()[0]["available"] is False
    launched = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kw: launched.append(kw))
    with pytest.raises(RuntimeError, match="incomplete"):
        agent_broker.dispatch_agent("strict", "test", "research")
    assert launched == []
