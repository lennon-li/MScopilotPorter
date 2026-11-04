"""OpenCode worker receives a versioned, invocation-only noninteractive permission profile."""

import json
import subprocess
from pathlib import Path

import pytest

from test_agent_broker_isolation import agent_broker, _fake_info


@pytest.mark.parametrize("version", ["1.19.0", "opencode v2.0.31"])
def test_opencode_dispatch_permissions(monkeypatch, tmp_path, version):
    delta = _fake_info(tmp_path, "delta", "opencode")
    Path(delta["executable"]).write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"delta": delta})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": True})
    captured = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kw: captured.append(kw) or "delta-job")
    agent_broker.dispatch_agent("delta", "Write scoped output", "test", model="opencode-go/glm-5.3-flash")
    cmd = captured[0]["cmd"]
    assert cmd[:2] == [delta["executable"], "run"]
    assert cmd[cmd.index("--agent") + 1] == "dispatch"
    assert cmd[cmd.index("-m") + 1] == "opencode-go/glm-5.3-flash"
    profile = json.loads(captured[0]["env"]["OPENCODE_CONFIG_CONTENT"])
    if version.startswith("1"):
        permissions = profile["agent"]["dispatch"]["permission"]
        assert permissions["edit"] == "allow"
        assert permissions["external_directory"] == "deny"
        assert permissions["bash"]["git commit*"] == "deny"
        assert permissions["bash"]["git push*"] == "deny"
    else:
        assert "--standalone" in cmd
        permissions = profile["agents"]["dispatch"]["permissions"]
        assert {"action": "edit", "resource": "*", "effect": "allow"} in permissions
        for command in ("git commit", "git push", "systemctl", "ssh"):
            assert {"action": "shell", "resource": command + "*", "effect": "deny"} in permissions


@pytest.mark.parametrize("version", ["worker 1.2.3", "3.0.0", ""])
def test_opencode_unknown_version_cannot_launch(monkeypatch, tmp_path, version):
    delta = _fake_info(tmp_path, "delta", "opencode")
    Path(delta["executable"]).write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"delta": delta})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": True})
    launched = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kw: launched.append(kw))
    with pytest.raises(RuntimeError, match="permission schema"):
        agent_broker.dispatch_agent("delta", "test", "test")
    assert launched == []


def test_opencode_version_probe_failure_cannot_launch(monkeypatch):
    from mscopilotporter.agents import opencode

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 5)

    monkeypatch.setattr(opencode.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="Cannot verify.*permission schema"):
        opencode.dispatch_command("/worker", "test", "opencode-go/glm-5.3-flash")


def test_opencode_readonly_workspace_cannot_launch(monkeypatch, tmp_path):
    delta = _fake_info(tmp_path, "delta", "opencode")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"delta": delta})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": False})
    launched = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kw: launched.append(kw))
    with pytest.raises(ValueError, match="read-only"):
        agent_broker.dispatch_agent("delta", "test", "test")
    assert launched == []
