import json
import logging
import os
import re
import shutil
import subprocess
from typing import Optional

from mscopilotporter.config import load_config
from mscopilotporter.tools.workspace import get_workspace
from mscopilotporter.runner.jobs import job_manager
from mscopilotporter.agents.opencode import dispatch_command as opencode_dispatch_command

logger = logging.getLogger("mscopilotporter.agent_broker")

def _profile_path(path: str) -> str:
    return os.path.expanduser(path)


# Defaults per supported worker CLI. Users declare concrete agents (name, profile
# directory, model) under `agents:` in their config file; with no `agents:` section
# one agent per CLI found on PATH is registered under the CLI's own name.
CLI_TEMPLATES = {
    "codex": {
        "provider": "OpenAI",
        "profile_path": "~/.codex",
        "home_env": "CODEX_HOME",
        "config_file": "config.toml",
        "default_model": "unknown",
        "reasoning_effort": "unknown",
        "capabilities": ["implementation", "code_review", "debugging", "verification"],
        "description": "Codex CLI worker",
    },
    "copilot": {
        "provider": "GitHub Copilot",
        "profile_path": "~/.copilot",
        "home_env": "COPILOT_HOME",
        "config_file": "config.json",
        "default_model": "auto",
        "reasoning_effort": "medium",
        "capabilities": ["implementation", "code_review", "github_workflows"],
        "description": "GitHub Copilot CLI worker",
    },
    "opencode": {
        "provider": "OpenCode",
        "profile_path": "~/.config/opencode",
        "home_env": None,
        "config_file": "opencode.json",
        "default_model": "unknown",
        "reasoning_effort": "medium",
        "capabilities": ["fast_implementation", "code_search", "testing"],
        "description": "OpenCode CLI worker",
    },
    "claude": {
        "provider": "Anthropic",
        "profile_path": None,
        "home_env": None,
        "config_file": None,
        "default_model": "unknown",
        "reasoning_effort": "unknown",
        "capabilities": ["deep_reasoning", "architecture", "senior_code_review"],
        "description": "Claude Code CLI worker",
    },
    "agy": {
        "provider": "Google",
        "profile_path": None,
        "home_env": None,
        "config_file": None,
        "default_model": "unknown",
        "reasoning_effort": "unknown",
        "capabilities": ["survey", "synthesis", "audit", "exploration"],
        "description": "Antigravity CLI worker",
    },
}

_CLI_EXECUTABLES = {"codex": "codex", "copilot": "copilot", "opencode": "opencode", "claude": "claude", "agy": "agy"}


def _executable(cli: str) -> str:
    found = shutil.which(_CLI_EXECUTABLES[cli])
    if found:
        return found
    for candidate in ("~/.npm-global/bin/", "~/.local/bin/"):
        path = os.path.expanduser(candidate + _CLI_EXECUTABLES[cli])
        if os.path.isfile(path):
            return path
    return _CLI_EXECUTABLES[cli]


def _build_entry(name: str, spec: dict) -> dict:
    cli = spec.get("cli")
    if cli not in CLI_TEMPLATES:
        raise ValueError(f"Agent '{name}': 'cli' must be one of {sorted(CLI_TEMPLATES)}")
    tpl = CLI_TEMPLATES[cli]
    profile = spec.get("profile_path", tpl["profile_path"])
    profile = _profile_path(profile) if profile else None
    env = {tpl["home_env"]: profile} if tpl["home_env"] and profile else {}
    config_file = os.path.join(profile, tpl["config_file"]) if profile and tpl["config_file"] else None
    executable = spec.get("executable")
    return {
        "name": name,
        "alias": spec.get("alias", name),
        "worker_cli": cli,
        "executable": _profile_path(executable) if executable else _executable(cli),
        **({"profile_path": profile} if profile else {}),
        "env_overrides": env,
        "config_path": config_file,
        **({"required_profile_files": tuple(spec["required_profile_files"])} if spec.get("required_profile_files") else {}),
        "provider": tpl["provider"],
        "default_model": spec.get("model", tpl["default_model"]),
        "reasoning_effort": spec.get("reasoning_effort", tpl["reasoning_effort"]),
        "capabilities": list(spec.get("capabilities", tpl["capabilities"])),
        "description": spec.get("description", tpl["description"]),
    }


def build_registry(agents_config=None) -> tuple[dict, dict]:
    """Return (registry, aliases) from the `agents:` config section."""
    registry, aliases = {}, {}
    if agents_config:
        for name, spec in agents_config.items():
            key = str(name).lower().strip()
            registry[key] = _build_entry(key, spec or {})
            for alias in (spec or {}).get("aliases", []):
                aliases[str(alias).lower().strip()] = key
    else:
        for cli in CLI_TEMPLATES:
            if shutil.which(_CLI_EXECUTABLES[cli]):
                registry[cli] = _build_entry(cli, {"cli": cli})
    return registry, aliases


AGENT_REGISTRY, AGENT_ALIASES = build_registry(load_config().get("agents"))

def _read_config(agent_info: dict) -> dict:
    """Read only non-secret model settings; malformed or absent files use defaults."""
    cfg_path = agent_info.get("config_path")
    if not cfg_path or not os.path.isfile(cfg_path):
        return {}
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            content = f.read()
        if agent_info["worker_cli"] == "codex":
            model = re.search(r'^\s*model\s*=\s*["\']([^"\']+)["\']', content, re.M)
            effort = re.search(r'^\s*model_reasoning_effort\s*=\s*["\']([^"\']+)["\']', content, re.M)
            return {
                **({"configured_model": model.group(1)} if model else {}),
                **({"reasoning_effort": effort.group(1)} if effort else {}),
            }
        data = json.loads(content)
        if not isinstance(data, dict):
            return {}
        if agent_info["worker_cli"] == "opencode":
            model = data.get("model")
            return {"configured_model": model} if isinstance(model, str) and model else {}
        if agent_info["worker_cli"] == "copilot":
            model = data.get("model")
            return {"configured_model": model} if isinstance(model, str) and model else {}
    except (OSError, ValueError, TypeError):
        logger.debug("Could not parse non-secret model configuration for %s", agent_info["name"])
    return {}


def get_agent_live_metadata(agent_key: str, default_info: dict) -> dict:
    """Inspect a worker executable and its own profile/configuration."""
    exe = default_info["executable"]
    profile = default_info.get("profile_path") or next(iter(default_info.get("env_overrides", {}).values()), None)
    profile_available = not profile or os.path.isdir(profile)
    if default_info.get("required_profile_files"):
        profile_available = bool(profile) and profile_available and all(
            os.path.isfile(os.path.join(profile, name))
            for name in default_info["required_profile_files"]
        )
    is_installed = os.path.isfile(exe) and os.access(exe, os.X_OK) and profile_available

    cli_version = "unknown"
    if is_installed:
        try:
            res = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
            raw = (res.stdout + res.stderr).strip()
            match = re.search(r"(\d+\.\d+\.\d+)", raw)
            cli_version = match.group(1) if match else (raw.splitlines()[0] if raw else "unknown")
        except Exception:
            cli_version = "unknown"

    configured = {
        "configured_model": default_info["default_model"],
        "reasoning_effort": default_info["reasoning_effort"],
        "provider": default_info["provider"],
    }
    configured.update(_read_config(default_info))

    if default_info["worker_cli"] == "opencode":
        model = configured["configured_model"].lower()
        configured["provider"] = "Google Vertex AI" if model.startswith("vertex/") else "OpenCode"

    return {
        "is_installed": is_installed,
        "profile_available": profile_available,
        "cli_version": cli_version,
        **configured,
    }


def list_agents() -> list[dict]:
    """List distinct worker identities and distinguish configured from actual routing."""
    results = []
    for agent_id, info in AGENT_REGISTRY.items():
        live = get_agent_live_metadata(agent_id, info)
        results.append({
            "agent": agent_id,
            "worker_cli": info["worker_cli"],
            "alias": info["alias"],
            "provider": live["provider"],
            "configured_model": live["configured_model"],
            "default_routing": f"{live['provider']} -> {live['configured_model']}",
            "actual_model_used": "unknown (resolved at runtime per dispatch)",
            "reasoning_level": live["reasoning_effort"],
            "cli_version": live["cli_version"],
            "available": live["is_installed"],
            "profile_available": live["profile_available"],
            "capabilities": info["capabilities"],
            "description": info["description"],
            "isolation_note": "Runs in its configured CLI profile; access is scoped to the dispatch workspace where supported.",
            "effective_model": live["configured_model"],
            "default_model": live["configured_model"],
        })
    return results


def dispatch_agent(
    agent: str,
    task: str,
    workspace_id: str,
    purpose: str = "",
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    network_access: bool = False,
) -> dict:
    """Dispatch an authorized worker and report its identity and runtime metadata."""
    requested_agent = agent.lower().strip()
    agent_key = AGENT_ALIASES.get(requested_agent, requested_agent)
    if agent_key not in AGENT_REGISTRY:
        authorized = [*AGENT_REGISTRY.keys(), *AGENT_ALIASES.keys()]
        raise ValueError(f"Unknown agent: '{agent}'. Authorized agents: {authorized}")

    agent_info = AGENT_REGISTRY[agent_key]
    worker_cli = agent_info["worker_cli"]
    if worker_cli == "agy" and reasoning_effort and reasoning_effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise ValueError("Unsupported agy reasoning effort")
    if network_access and worker_cli != "codex":
        raise ValueError(
            f"Network access is not supported for {worker_cli} workers; request rejected."
        )
    exe = agent_info["executable"]
    if not os.path.isfile(exe) or not os.access(exe, os.X_OK):
        raise RuntimeError(f"Agent executable '{exe}' is not available or not executable on host")

    profile = agent_info.get("profile_path") or next(iter(agent_info.get("env_overrides", {}).values()), None)
    if profile and not os.path.isdir(profile):
        raise RuntimeError(f"Agent profile '{profile}' is not available on host")
    if agent_info.get("required_profile_files") and (
        not profile or any(
            not os.path.isfile(os.path.join(profile, name))
            for name in agent_info["required_profile_files"]
        )
    ):
        raise RuntimeError(f"Agent profile for '{agent_key}' is incomplete on host")

    ws = get_workspace(workspace_id)
    ws_path = ws["path"]
    if worker_cli == "opencode" and not ws["writable"]:
        raise ValueError("OpenCode cannot enforce read-only workspace execution; request rejected.")
    if network_access and not ws["writable"]:
        raise ValueError(
            "Network access is not supported for read-only Codex dispatches; request rejected."
        )

    git_clean = True
    if os.path.exists(os.path.join(ws_path, ".git")):
        res = subprocess.run(["git", "status", "--short"], cwd=ws_path, capture_output=True, text=True)
        if res.stdout.strip():
            git_clean = False

    live = get_agent_live_metadata(agent_key, agent_info)
    effective_model = model or live["configured_model"]
    effective_effort = reasoning_effort or live["reasoning_effort"]
    cli_ver = live["cli_version"]
    provider = live["provider"]

    packet = (
        f"Permission Level: 1\n"
        f"Interaction Mode: AUTONOMOUS\n"
        f"Authorization: {'MAY MODIFY FILES WITHIN SCOPE' if ws['writable'] else 'READ-ONLY'}\n"
        f"Step budget: 30\n"
        f"Project Root: {ws_path}\n"
        f"Network access setting: {'enabled for the configured host allowlist' if network_access else 'disabled for Codex' if worker_cli == 'codex' else 'not enforceable by this worker adapter'}\n"
        "Network access does not authorize git push, publishing, releases, or other external writes.\n"
        f"Purpose: {purpose or 'Worker delegation from MScopilotPorter'}\n"
        f"Task:\n{task}\n"
    )

    if worker_cli == "codex":
        cmd = [
            exe, "exec", "--sandbox", "workspace-write" if ws["writable"] else "read-only",
            "-c", "approval_policy=never",
        ]
        if effective_model not in ("", "unknown"):
            cmd.extend(["-m", effective_model])
        if effective_effort not in ("", "unknown"):
            cmd.extend(["-c", f"model_reasoning_effort={effective_effort}"])
        if not network_access:
            cmd.extend(["-c", "sandbox_workspace_write.network_access=false"])
        if network_access:
            cmd.extend([
                "-c", "sandbox_workspace_write.network_access=true",
                "-c", "features.network_proxy.enabled=true",
                "-c", 'features.network_proxy.domains={ "api.openai.com" = "allow", "github.com" = "allow", "api.github.com" = "allow", "raw.githubusercontent.com" = "allow", "codeload.github.com" = "allow", "pypi.org" = "allow", "files.pythonhosted.org" = "allow", "registry.npmjs.org" = "allow", "cloud.r-project.org" = "allow" }',
            ])
        cmd.append(packet)
    elif worker_cli == "claude":
        cmd = [exe, "-p", packet]
    elif worker_cli == "opencode":
        cmd, opencode_profile = opencode_dispatch_command(exe, packet, effective_model)
    elif worker_cli == "copilot":
        # Keep the permission explicit and workspace-bound; read-only dispatches
        # receive only common read-only Git inspection commands.
        cmd = [exe, "-p", packet]
        if effective_model not in ("", "unknown"):
            cmd.extend(["--model", effective_model])
        # Copilot rejects a reasoning effort for its "auto" model router.
        if effective_model not in ("", "unknown", "auto") and effective_effort not in ("", "unknown"):
            cmd.extend(["--reasoning-effort", effective_effort])
        if ws["writable"]:
            # The CLI's default path verifier restricts writes to its working
            # directory; temp-file access is disabled below.
            cmd.extend(["--allow-tool=write", "--disallow-temp-dir"])
        else:
            cmd.append("--deny-tool=write")
        cmd.extend([
            "--allow-tool=shell(git status:*)",
            "--allow-tool=shell(git diff:*)",
            "--allow-tool=shell(git log:*)",
            "--allow-tool=shell(git show:*)",
        ])
    elif worker_cli == "agy":
        cmd = [exe, "-p", packet]
        if model:
            cmd.extend(["--model", model])
        if reasoning_effort:
            cmd.extend(["--effort", reasoning_effort])
    else:
        raise ValueError(f"No dispatch runner defined for {worker_cli}")

    proc_env = os.environ.copy()
    proc_env.update(agent_info.get("env_overrides", {}))
    if worker_cli == "opencode":
        proc_env["OPENCODE_CONFIG_CONTENT"] = opencode_profile
    if worker_cli == "copilot":
        # Use the agent's saved profile auth and the explicit tool grants above.
        for name in (
            "COPILOT_ALLOW_ALL", "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
            "COPILOT_REAL_BIN",
        ):
            proc_env.pop(name, None)

    try:
        job_id = job_manager.start_raw_job(
        workspace_id=workspace_id,
        workspace_path=ws_path,
        cmd=cmd,
        env=proc_env,
        agent=agent_key,
        provider=provider,
        model=effective_model,
        requested_model=effective_model,
        reasoning_level=effective_effort,
        cli_version=cli_ver,
        timeout_seconds=900,
        )
    except Exception:
        raise

    return {
        "worker_cli": worker_cli,
        "provider": provider,
        "requested_model": effective_model,
        "actual_model": "unknown",
        "reasoning_level": effective_effort,
        "cli_version": cli_ver,
        "job_id": job_id,
        "exit_status": None,
        "duration": 0.0,
        "status": "running",
        "selected_agent": agent_key,
        "alias": agent_info["alias"],
        "model": effective_model,
        "thinking_level": effective_effort,
        "workspace_id": workspace_id,
        "git_clean_at_dispatch": git_clean,
        "network_access": network_access,
    }
