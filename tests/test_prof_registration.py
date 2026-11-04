"""Prof uses a dedicated profile without inventing a default model."""

import pytest

from test_agent_broker_isolation import agent_broker, _fake_info


def test_prof_has_own_registry_entry():
    info = agent_broker.AGENT_REGISTRY["prof"]
    assert info["worker_cli"] == "codex"
    assert info["profile_path"].endswith("/.codex-prof")
    assert info["env_overrides"]["CODEX_HOME"] == info["profile_path"]
    assert info["default_model"] == "unknown"


def test_prof_dispatch_preserves_account_default_and_workspace(monkeypatch, tmp_path):
    prof = _fake_info(tmp_path, "prof")
    prof.update(default_model="unknown", reasoning_effort="unknown")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"prof": prof})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": True})
    captured = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kw: captured.append(kw) or "prof-job")
    result = agent_broker.dispatch_agent("prof", "Scoped research", "research")
    assert result["selected_agent"] == "prof"
    assert captured[0]["env"]["CODEX_HOME"] == prof["profile_path"]
    assert "-m" not in captured[0]["cmd"]
    assert "model_reasoning_effort=unknown" not in captured[0]["cmd"]
    assert "workspace-write" in captured[0]["cmd"]
    agent_broker.dispatch_agent("prof", "Scoped research", "research", model="prof-model", reasoning_effort="high")
    argv = captured[1]["cmd"]
    assert argv[argv.index("-m") + 1] == "prof-model"
    assert "model_reasoning_effort=high" in argv


@pytest.mark.parametrize("missing", ["config.toml", "auth.json"])
def test_prof_incomplete_profile_cannot_dispatch(monkeypatch, tmp_path, missing):
    from pathlib import Path

    prof = _fake_info(tmp_path, "prof")
    prof["required_profile_files"] = ("config.toml", "auth.json")
    for name in prof["required_profile_files"]:
        if name != missing:
            (Path(prof["profile_path"]) / name).write_text("{}")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"prof": prof})
    assert agent_broker.list_agents()[0]["available"] is False
    launched = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kw: launched.append(kw))
    with pytest.raises(RuntimeError, match="incomplete"):
        agent_broker.dispatch_agent("prof", "test", "research")
    assert launched == []
