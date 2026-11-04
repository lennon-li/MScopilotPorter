"""The agy worker routes through its own CLI adapter."""

import pytest

from test_agent_broker_isolation import agent_broker, _fake_info


def test_agy_defaults_do_not_invent_a_model():
    registry, _ = agent_broker.build_registry({"surveyor": {"cli": "agy", "alias": "Surveyor"}})
    info = registry["surveyor"]
    assert info["worker_cli"] == "agy"
    assert info["alias"] == "Surveyor"
    assert info["default_model"] == "unknown"
    assert info["env_overrides"] == {}


def test_agy_discovery_and_dispatch(monkeypatch, tmp_path):
    surveyor = _fake_info(tmp_path, "surveyor", "agy")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"surveyor": surveyor})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": True, "allow_execute": True})
    calls = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kw: calls.append(kw) or "surveyor-job")
    assert agent_broker.list_agents()[0]["agent"] == "surveyor"
    result = agent_broker.dispatch_agent("surveyor", "Bounded survey", "test")
    assert result["selected_agent"] == "surveyor"
    assert result["worker_cli"] == "agy"
    assert result["job_id"] == "surveyor-job"
    assert calls[0]["cmd"][0] == surveyor["executable"]


@pytest.mark.parametrize("override", [{"model": "unverified-model"}, {"reasoning_effort": "high"}])
def test_agy_does_not_silently_ignore_overrides(monkeypatch, tmp_path, override):
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"surveyor": _fake_info(tmp_path, "surveyor", "agy")})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": True, "allow_execute": True})
    calls = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kw: calls.append(kw))
    agent_broker.dispatch_agent("surveyor", "Survey", "test", **override)
    argv = calls[0]["cmd"]
    for name, value in override.items():
        flag = "--model" if name == "model" else "--effort"
        assert argv[argv.index(flag) + 1] == value


def test_agy_invalid_effort_never_launches(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"surveyor": _fake_info(tmp_path, "surveyor", "agy")})
    calls = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kw: calls.append(kw))
    with pytest.raises(ValueError, match="reasoning effort"):
        agent_broker.dispatch_agent("surveyor", "Survey", "test", reasoning_effort="invalid")
    assert calls == []
