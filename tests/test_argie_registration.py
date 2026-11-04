"""The public Argie identity preserves its existing runtime routing."""

import pytest

from test_agent_broker_isolation import agent_broker, _fake_info


def test_argie_registry_preserves_legacy_runtime():
    argie = agent_broker.AGENT_REGISTRY["argie"]
    legacy = agent_broker.AGENT_REGISTRY["agy"]
    assert argie["name"] == "argie"
    assert argie["alias"] == "Argie"
    assert argie["worker_cli"] == "agy"
    assert argie["executable"] == legacy["executable"]
    assert argie["env_overrides"] == legacy["env_overrides"]
    assert argie["capabilities"] == legacy["capabilities"]
    assert argie["default_model"] == "unknown"


def test_argie_discovery_and_dispatch(monkeypatch, tmp_path):
    argie = _fake_info(tmp_path, "argie", "agy")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"argie": argie})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": True})
    calls = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kw: calls.append(kw) or "argie-job")
    assert agent_broker.list_agents()[0]["agent"] == "argie"
    result = agent_broker.dispatch_agent("argie", "Bounded survey", "test")
    assert result["selected_agent"] == "argie"
    assert result["worker_cli"] == "agy"
    assert result["job_id"] == "argie-job"
    assert calls[0]["cmd"][0] == argie["executable"]


@pytest.mark.parametrize("override", [{"model": "unverified-model"}, {"reasoning_effort": "high"}])
def test_argie_does_not_silently_ignore_overrides(monkeypatch, tmp_path, override):
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"argie": _fake_info(tmp_path, "argie", "agy")})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": True})
    calls = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kw: calls.append(kw))
    agent_broker.dispatch_agent("argie", "Survey", "test", **override)
    argv = calls[0]["cmd"]
    for name, value in override.items():
        flag = "--model" if name == "model" else "--effort"
        assert argv[argv.index(flag) + 1] == value


def test_argie_invalid_effort_never_launches(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"argie": _fake_info(tmp_path, "argie", "agy")})
    calls = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kw: calls.append(kw))
    with pytest.raises(ValueError, match="reasoning effort"):
        agent_broker.dispatch_agent("argie", "Survey", "test", reasoning_effort="invalid")
    assert calls == []
