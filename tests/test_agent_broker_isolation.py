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
            else {"COPILOT_HOME": str(profile), "PHIL_COPILOT_HOME": str(profile)} if cli == "copilot"
            else {}
        ),
        "config_path": None,
        "provider": "OpenAI" if cli == "codex" else "GitHub Copilot",
        "default_model": "gpt-6-luna" if cli == "codex" else "auto",
        "reasoning_effort": "high",
        "capabilities": [],
        "description": name,
    }


def test_distinct_profiles_and_legacy_aliases(monkeypatch, tmp_path):
    jax = _fake_info(tmp_path, "jax")
    liz = _fake_info(tmp_path, "liz")
    phil = _fake_info(tmp_path, "phil", "copilot")
    wei = _fake_info(tmp_path, "wei", "opencode")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"jax": jax, "liz": liz, "phil": phil, "wei": wei})
    monkeypatch.setattr(agent_broker, "AGENT_ALIASES", {"codex": "jax", "opencode": "wei"})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": True})
    captured = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kwargs: captured.append(kwargs) or f"job_{len(captured)}")

    jax_result = agent_broker.dispatch_agent("codex", "review", "repos")
    liz_result = agent_broker.dispatch_agent("liz", "implement", "repos")
    wei_result = agent_broker.dispatch_agent("opencode", "inspect", "repos")
    assert jax_result["selected_agent"] == "jax"
    assert jax_result["worker_cli"] == liz_result["worker_cli"] == "codex"
    assert captured[0]["env"]["CODEX_HOME"] == jax["env_overrides"]["CODEX_HOME"]
    assert captured[1]["env"]["CODEX_HOME"] == liz["env_overrides"]["CODEX_HOME"]
    assert captured[0]["env"]["CODEX_HOME"] != captured[1]["env"]["CODEX_HOME"]
    assert captured[0]["agent"] == "jax" and captured[1]["agent"] == "liz"
    assert wei_result["selected_agent"] == "wei"
    assert wei_result["worker_cli"] == "opencode"
    assert captured[2]["agent"] == "wei"


def test_codex_network_requires_explicit_opt_in_and_keeps_host_allowlist(monkeypatch, tmp_path):
    codex = _fake_info(tmp_path, "liz")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"liz": codex})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": True})
    captured = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kwargs: captured.append(kwargs) or f"job_{len(captured)}")

    offline = agent_broker.dispatch_agent("liz", "inspect", "repos")
    online = agent_broker.dispatch_agent("liz", "read GitHub docs", "repos", network_access=True)

    assert offline["network_access"] is False
    assert online["network_access"] is True
    assert "sandbox_workspace_write.network_access=false" in captured[0]["cmd"]
    assert "sandbox_workspace_write.network_access=true" not in captured[0]["cmd"]
    assert "sandbox_workspace_write.network_access=true" in captured[1]["cmd"]
    assert "features.network_proxy.enabled=true" in captured[1]["cmd"]
    domains = next(arg for arg in captured[1]["cmd"] if arg.startswith("features.network_proxy.domains="))
    assert "github.com" in domains


def test_network_request_rejected_for_unsupported_or_read_only_worker(monkeypatch, tmp_path):
    phil = _fake_info(tmp_path, "phil", "copilot")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"phil": phil})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": True})
    with pytest.raises(ValueError, match="not supported for copilot"):
        agent_broker.dispatch_agent("phil", "read GitHub", "repos", network_access=True)

    liz = _fake_info(tmp_path, "liz")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"liz": liz})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": False})
    with pytest.raises(ValueError, match="read-only Codex"):
        agent_broker.dispatch_agent("liz", "read GitHub", "repos", network_access=True)


def test_codex_read_only_dispatch_is_explicitly_offline(monkeypatch, tmp_path):
    liz = _fake_info(tmp_path, "liz")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"liz": liz})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": False})
    captured = {}
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kwargs: captured.update(kwargs) or "job_ro")

    agent_broker.dispatch_agent("liz", "inspect", "repos")

    assert captured["cmd"][captured["cmd"].index("--sandbox") + 1] == "read-only"
    assert "sandbox_workspace_write.network_access=false" in captured["cmd"]


def test_wei_alias_rejects_missing_profile_before_launch(monkeypatch, tmp_path):
    wei = _fake_info(tmp_path, "wei", "opencode")
    wei["profile_path"] = str(tmp_path / "missing-wei-profile")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"wei": wei})
    monkeypatch.setattr(agent_broker, "AGENT_ALIASES", {"opencode": "wei"})
    launched = []
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kwargs: launched.append(kwargs))

    with pytest.raises(RuntimeError, match="Agent profile"):
        agent_broker.dispatch_agent("opencode", "inspect", "repos")
    assert launched == []


@pytest.mark.parametrize("agent", ["evil-agent-binary", "fury", "uatu", "oatu"])
def test_unknown_and_forbidden_agents_are_rejected(monkeypatch, agent):
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"jax": {}})
    monkeypatch.setattr(agent_broker, "AGENT_ALIASES", {"codex": "jax"})
    with pytest.raises(ValueError, match="Unknown agent"):
        agent_broker.dispatch_agent(agent, "no-op", "repos")


def test_phil_env_and_permissions_are_profile_bound(monkeypatch, tmp_path):
    phil = _fake_info(tmp_path, "phil", "copilot")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"phil": phil})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": True})
    monkeypatch.setenv("PHIL_COPILOT_HOME", "/wrong/profile")
    monkeypatch.setenv("COPILOT_REAL_BIN", "/wrong/copilot")
    monkeypatch.setenv("COPILOT_ALLOW_ALL", "true")
    monkeypatch.setenv("GH_TOKEN", "test-token")
    captured = {}
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kwargs: captured.update(kwargs) or "job_phil")

    agent_broker.dispatch_agent("phil", "read one file", "repos")
    env = captured["env"]
    argv = captured["cmd"]
    assert env["COPILOT_HOME"] == phil["env_overrides"]["COPILOT_HOME"]
    assert env["PHIL_COPILOT_HOME"] == phil["env_overrides"]["COPILOT_HOME"]
    assert not {"COPILOT_REAL_BIN", "COPILOT_ALLOW_ALL", "GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN"} & env.keys()
    assert "--allow-tool=write" in argv and "--disallow-temp-dir" in argv
    assert any(arg == "--allow-tool=shell(git status:*)" for arg in argv)
    assert not any(arg in {"--allow-all", "--yolo", "--allow-all-tools", "--allow-all-paths", "--allow-all-urls"} for arg in argv)
    # Copilot rejects --reasoning-effort for its "auto" model router.
    assert argv[argv.index("--model") + 1] == "auto"
    assert "--reasoning-effort" not in argv


def test_phil_read_only_dispatch_cannot_write(monkeypatch, tmp_path):
    phil = _fake_info(tmp_path, "phil", "copilot")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", {"phil": phil})
    monkeypatch.setattr(agent_broker, "get_workspace", lambda _: {"path": str(tmp_path), "writable": False})
    captured = {}
    monkeypatch.setattr(agent_broker.job_manager, "start_raw_job", lambda **kwargs: captured.update(kwargs) or "job_ro")

    agent_broker.dispatch_agent("phil", "read a file", "repos")
    argv = captured["cmd"]
    assert "--deny-tool=write" in argv
    assert "--allow-tool=write" not in argv
    assert any(arg == "--allow-tool=shell(git show:*)" for arg in argv)
    assert not any(arg.startswith("--allow-tool=shell(git push") or arg.startswith("--allow-tool=shell(git commit") for arg in argv)


def test_agent_listing_checks_executable_and_own_profile(monkeypatch, tmp_path):
    registry = {}
    for name in ("jax", "liz", "phil", "wei", "claude", "agy"):
        cli = "copilot" if name == "phil" else "codex" if name in ("jax", "liz") else name
        registry[name] = _fake_info(tmp_path, name, cli)
    registry["jax"]["config_path"] = str(tmp_path / "jax.toml")
    registry["liz"]["config_path"] = str(tmp_path / "liz.toml")
    (tmp_path / "jax.toml").write_text('model = "gpt-6-luna"\nmodel_reasoning_effort = "high"\n', encoding="utf-8")
    (tmp_path / "liz.toml").write_text('model = "gpt-6-luna"\nmodel_reasoning_effort = "medium"\n', encoding="utf-8")
    monkeypatch.setattr(agent_broker, "AGENT_REGISTRY", registry)
    agents = agent_broker.list_agents()
    assert [agent["agent"] for agent in agents] == ["jax", "liz", "phil", "wei", "claude", "agy"]
    assert all(agent["available"] and agent["profile_available"] for agent in agents)
    assert agents[0]["worker_cli"] == agents[1]["worker_cli"] == "codex"
    assert agents[2]["worker_cli"] == "copilot"
    assert agents[0]["reasoning_level"] == "high"
    assert agents[1]["reasoning_level"] == "medium"

    registry["liz"]["env_overrides"]["CODEX_HOME"] = str(tmp_path / "missing-profile")
    registry["liz"]["profile_path"] = str(tmp_path / "missing-profile")
    unavailable = agent_broker.get_agent_live_metadata("liz", registry["liz"])
    assert unavailable["profile_available"] is False
    assert unavailable["is_installed"] is False


@pytest.mark.parametrize("agent,worker_cli", [("jax", "codex"), ("liz", "codex"), ("phil", "copilot"), ("wei", "opencode"), ("claude", "claude")])
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
