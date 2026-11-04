"""Hermetic tests for agent identity/profile routing ."""

import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest


from mscopilotporter.agents import registry as agent_broker  # noqa: E402
from mscopilotporter.runner import jobs  # noqa: E402


def _fake_executable(path: Path, version="worker 1.2.3"):
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _fake_info(tmp_path, name, cli="codex"):
    profile = tmp_path / f"{name}-profile"
    profile.mkdir()
    executable = _fake_executable(
        tmp_path / f"{name}-cli", "opencode v2.0.31" if cli == "opencode" else "worker 1.2.3"
    )
    return {
        "name": name,
        "alias": name.title(),
        "worker_cli": cli,
        "executable": executable,
        "profile_path": str(profile) if cli in ("codex", "copilot", "opencode") else None,
        "env_overrides": (
            {"CODEX_HOME": str(profile)} if cli == "codex"
            else {"COPILOT_HOME": str(profile)} if cli == "copilot"
            else {}
        ),
        "config_path": None,
        "provider": "OpenAI" if cli == "codex" else "GitHub Copilot",
        "default_model": "model-x" if cli == "codex" else "auto",
        "reasoning_effort": "high",
        "capabilities": [],
        "description": name,
    }


def test_distinct_profiles_and_legacy_aliases(monkeypatch, tmp_path):
    alpha = _fake_info(tmp_path, "alpha")
    beta = _fake_info(tmp_path, "beta")
    gamma = _fake_info(tmp_path, "gamma", "copilot")
    delta = _fake_info(tmp_path, "delta", "opencode")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"alpha": alpha, "beta": beta, "gamma": gamma, "delta": delta})
    monkeypatch.setattr(agent_broker, "AGENT_ALIASES", {"codex": "alpha", "opencode": "delta"})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": True})
    captured = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kwargs: captured.append(kwargs) or f"job_{len(captured)}")

    jax_result = agent_broker.dispatch_agent("codex", "review", "repos")
    liz_result = agent_broker.dispatch_agent("beta", "implement", "repos")
    wei_result = agent_broker.dispatch_agent("opencode", "inspect", "repos")
    assert jax_result["selected_agent"] == "alpha"
    assert jax_result["worker_cli"] == liz_result["worker_cli"] == "codex"
    assert captured[0]["env"]["CODEX_HOME"] == alpha["env_overrides"]["CODEX_HOME"]
    assert captured[1]["env"]["CODEX_HOME"] == beta["env_overrides"]["CODEX_HOME"]
    assert captured[0]["env"]["CODEX_HOME"] != captured[1]["env"]["CODEX_HOME"]
    assert captured[0]["agent"] == "alpha" and captured[1]["agent"] == "beta"
    assert wei_result["selected_agent"] == "delta"
    assert wei_result["worker_cli"] == "opencode"
    assert captured[2]["agent"] == "delta"


def test_codex_network_requires_explicit_opt_in_and_keeps_host_allowlist(monkeypatch, tmp_path):
    codex = _fake_info(tmp_path, "beta")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"beta": codex})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": True})
    captured = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kwargs: captured.append(kwargs) or f"job_{len(captured)}")

    offline = agent_broker.dispatch_agent("beta", "inspect", "repos")
    online = agent_broker.dispatch_agent("beta", "read GitHub docs", "repos", network_access=True)

    assert offline["network_access"] is False
    assert online["network_access"] is True
    assert "sandbox_workspace_write.network_access=false" in captured[0]["cmd"]
    assert "sandbox_workspace_write.network_access=true" not in captured[0]["cmd"]
    assert "sandbox_workspace_write.network_access=true" in captured[1]["cmd"]
    assert "features.network_proxy.enabled=true" in captured[1]["cmd"]
    domains = next(arg for arg in captured[1]["cmd"] if arg.startswith("features.network_proxy.domains="))
    assert "github.com" in domains


def test_network_request_rejected_for_unsupported_or_read_only_worker(monkeypatch, tmp_path):
    gamma = _fake_info(tmp_path, "gamma", "copilot")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"gamma": gamma})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": True})
    with pytest.raises(ValueError, match="not supported for copilot"):
        agent_broker.dispatch_agent("gamma", "read GitHub", "repos", network_access=True)

    beta = _fake_info(tmp_path, "beta")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"beta": beta})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": False})
    with pytest.raises(ValueError, match="read-only Codex"):
        agent_broker.dispatch_agent("beta", "read GitHub", "repos", network_access=True)


def test_codex_read_only_dispatch_is_explicitly_offline(monkeypatch, tmp_path):
    beta = _fake_info(tmp_path, "beta")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"beta": beta})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": False})
    captured = {}
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kwargs: captured.update(kwargs) or "job_ro")

    agent_broker.dispatch_agent("beta", "inspect", "repos")

    assert captured["cmd"][captured["cmd"].index("--sandbox") + 1] == "read-only"
    assert "sandbox_workspace_write.network_access=false" in captured["cmd"]


def test_opencode_alias_rejects_missing_profile_before_launch(monkeypatch, tmp_path):
    delta = _fake_info(tmp_path, "delta", "opencode")
    delta["profile_path"] = str(tmp_path / "missing-delta-profile")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"delta": delta})
    monkeypatch.setattr(agent_broker, "AGENT_ALIASES", {"opencode": "delta"})
    launched = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kwargs: launched.append(kwargs))

    with pytest.raises(RuntimeError, match="Agent profile"):
        agent_broker.dispatch_agent("opencode", "inspect", "repos")
    assert launched == []


@pytest.mark.parametrize("agent", ["evil-agent-binary", "fury", "uatu", "oatu"])
def test_unknown_and_forbidden_agents_are_rejected(monkeypatch, agent):
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"alpha": {}})
    monkeypatch.setattr(agent_broker, "AGENT_ALIASES", {"codex": "alpha"})
    with pytest.raises(ValueError, match="Unknown agent"):
        agent_broker.dispatch_agent(agent, "no-op", "repos")


def test_gamma_env_and_permissions_are_profile_bound(monkeypatch, tmp_path):
    gamma = _fake_info(tmp_path, "gamma", "copilot")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"gamma": gamma})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": True})
    monkeypatch.setenv("COPILOT_HOME", "/wrong/profile")
    monkeypatch.setenv("COPILOT_REAL_BIN", "/wrong/copilot")
    monkeypatch.setenv("COPILOT_ALLOW_ALL", "true")
    monkeypatch.setenv("GH_TOKEN", "test-token")
    captured = {}
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kwargs: captured.update(kwargs) or "job_gamma")

    agent_broker.dispatch_agent("gamma", "read one file", "repos")
    env = captured["env"]
    argv = captured["cmd"]
    assert env["COPILOT_HOME"] == gamma["env_overrides"]["COPILOT_HOME"]
    assert not {"COPILOT_REAL_BIN", "COPILOT_ALLOW_ALL", "GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN"} & env.keys()
    assert "--allow-tool=write" in argv and "--disallow-temp-dir" in argv
    assert any(arg == "--allow-tool=shell(git status:*)" for arg in argv)
    assert not any(arg in {"--allow-all", "--yolo", "--allow-all-tools", "--allow-all-paths", "--allow-all-urls"} for arg in argv)
    # Copilot rejects --reasoning-effort for its "auto" model router.
    assert argv[argv.index("--model") + 1] == "auto"
    assert "--reasoning-effort" not in argv


def test_gamma_read_only_dispatch_cannot_write(monkeypatch, tmp_path):
    gamma = _fake_info(tmp_path, "gamma", "copilot")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"gamma": gamma})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": False})
    captured = {}
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kwargs: captured.update(kwargs) or "job_ro")

    agent_broker.dispatch_agent("gamma", "read a file", "repos")
    argv = captured["cmd"]
    assert "--deny-tool=write" in argv
    assert "--allow-tool=write" not in argv
    assert any(arg == "--allow-tool=shell(git show:*)" for arg in argv)
    assert not any(arg.startswith("--allow-tool=shell(git push") or arg.startswith("--allow-tool=shell(git commit") for arg in argv)


def test_agent_listing_checks_executable_and_own_profile(monkeypatch, tmp_path):
    registry = {}
    for name in ("alpha", "beta", "gamma", "delta", "claude", "agy"):
        cli = "copilot" if name == "gamma" else "codex" if name in ("alpha", "beta") else name
        registry[name] = _fake_info(tmp_path, name, cli)
    registry["alpha"]["config_path"] = str(tmp_path / "alpha.toml")
    registry["beta"]["config_path"] = str(tmp_path / "beta.toml")
    (tmp_path / "alpha.toml").write_text('model = "model-x"\nmodel_reasoning_effort = "high"\n', encoding="utf-8")
    (tmp_path / "beta.toml").write_text('model = "model-x"\nmodel_reasoning_effort = "medium"\n', encoding="utf-8")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", registry)
    agents = agent_broker.list_agents()
    assert [agent["agent"] for agent in agents] == ["alpha", "beta", "gamma", "delta", "claude", "agy"]
    assert all(agent["available"] and agent["profile_available"] for agent in agents)
    assert agents[0]["worker_cli"] == agents[1]["worker_cli"] == "codex"
    assert agents[2]["worker_cli"] == "copilot"
    assert agents[0]["reasoning_level"] == "high"
    assert agents[1]["reasoning_level"] == "medium"

    registry["beta"]["env_overrides"]["CODEX_HOME"] = str(tmp_path / "missing-profile")
    registry["beta"]["profile_path"] = str(tmp_path / "missing-profile")
    unavailable = agent_broker.get_agent_live_metadata("beta", registry["beta"])
    assert unavailable["profile_available"] is False
    assert unavailable["is_installed"] is False


@pytest.mark.parametrize("agent,worker_cli", [("alpha", "codex"), ("beta", "codex"), ("gamma", "copilot"), ("delta", "opencode"), ("claude", "claude")])
def test_job_status_keeps_cli_and_agent_identity(monkeypatch, agent, worker_cli):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("""CREATE TABLE jobs (
        job_id TEXT, workspace_id TEXT, argv TEXT, cwd TEXT, pid INTEGER, pgid INTEGER,
        status TEXT, exit_code INTEGER, created_at REAL, started_at REAL, finished_at REAL,
        duration REAL, stdout_path TEXT, stderr_path TEXT, agent TEXT, provider TEXT,
        model TEXT, requested_model TEXT, actual_model TEXT, reasoning_level TEXT, cli_version TEXT
    )""")
    connection.execute(
        "INSERT INTO jobs (job_id, workspace_id, status, agent, provider, model) VALUES (?, ?, ?, ?, ?, ?)",
        ("job_identity", "repos", "running", agent, "Provider", "model"),
    )
    monkeypatch.setattr(jobs, "get_db", lambda: connection)
    status = jobs.job_manager.get_status("job_identity")
    assert status["agent"] == agent
    assert status["worker_cli"] == worker_cli
    connection.close()


def test_sandbox_network_passes_only_sanitized_proxy_environment(monkeypatch, tmp_path):
    from mscopilotporter.runner import sandbox

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("GH_TOKEN", "must-not-enter-sandbox")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-enter-sandbox")
    offline_env = sandbox.sandbox_host_env(network_access=False)
    online_env = sandbox.sandbox_host_env(network_access=True)

    assert "HTTPS_PROXY" not in offline_env
    assert online_env["HTTPS_PROXY"] == "http://proxy.example:8080"
    assert online_env["NO_PROXY"] == "localhost,127.0.0.1"
    assert "GH_TOKEN" not in online_env and "UNRELATED_SECRET" not in online_env
    assert "--clearenv" in sandbox.build_bwrap_args(str(tmp_path), network_access=False)
    assert "--clearenv" not in sandbox.build_bwrap_args(str(tmp_path), network_access=True)

    launched = {}

    class CompletedProcess:
        returncode = 0

        def communicate(self, timeout=None):
            return "", ""

    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda command, **kwargs: launched.update(kwargs) or CompletedProcess())
    sandbox.run_in_sandbox(str(tmp_path), ["true"], network_access=True)
    assert launched["env"] == online_env
    assert "GH_TOKEN" not in launched["env"]


def test_sandbox_rejects_proxy_credentials_without_exposing_url(monkeypatch):
    from mscopilotporter.runner import sandbox

    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@proxy.example:8080")
    with pytest.raises(ValueError, match="embedded credentials") as exc:
        sandbox.sandbox_host_env(network_access=True)
    assert "secret" not in str(exc.value)
