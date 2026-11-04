import json
import logging
import os
import re
import shutil
import subprocess
from typing import Optional

from mscopilotporter.tools.workspace import get_workspace
from mscopilotporter.runner.jobs import job_manager
from mscopilotporter.agents.opencode import dispatch_command as opencode_dispatch_command

logger = logging.getLogger("mscopilotporter.agent_broker")

def _profile_path(path: str) -> str:
    return os.path.expanduser(path)


AGENT_REGISTRY = {
    "jax": {
        "name": "jax",
        "alias": "Jax",
        "worker_cli": "codex",
        "executable": shutil.which("codex") or "codex",
        "profile_path": _profile_path("~/.codex"),
        "env_overrides": {"CODEX_HOME": _profile_path("~/.codex")},
        "config_path": _profile_path("~/.codex/config.toml"),
        "provider": "OpenAI",
        "default_model": "gpt-6-luna",
        "reasoning_effort": "high",
        "capabilities": ["code_review", "debugging", "verification", "refactoring"],
        "description": "Codex (Jax profile) for review, debugging, and bounded patches",
    },
    "liz": {
        "name": "liz",
        "alias": "Liz",
        "worker_cli": "codex",
        "executable": shutil.which("codex") or "codex",
        "profile_path": _profile_path("~/.codex-liz"),
        "env_overrides": {"CODEX_HOME": _profile_path("~/.codex-liz")},
        "config_path": _profile_path("~/.codex-liz/config.toml"),
        "provider": "OpenAI",
        "default_model": "gpt-6-luna",
        "reasoning_effort": "high",
        "capabilities": ["implementation", "edge_cases", "error_paths", "tests"],
        "description": "Codex (Liz profile) for implementation, edge cases, error paths, and tests",
    },
    "prof": {
        "name": "prof",
        "alias": "Prof",
        "worker_cli": "codex",
        "executable": shutil.which("codex") or "codex",
        "profile_path": _profile_path("~/.codex-prof"),
        "env_overrides": {"CODEX_HOME": _profile_path("~/.codex-prof")},
        "config_path": _profile_path("~/.codex-prof/config.toml"),
        "required_profile_files": ("config.toml", "auth.json"),
        "provider": "OpenAI",
        "default_model": "unknown",
        "reasoning_effort": "unknown",
        "capabilities": ["research", "implementation", "code_review", "verification"],
        "description": "Prof: university-research Codex worker with his own account profile",
    },
    "phil": {
        "name": "phil",
        "alias": "Phil",
        "worker_cli": "copilot",
        "executable": shutil.which("copilot")
        or next(
            (p for p in map(os.path.expanduser, ("~/.npm-global/bin/copilot", "~/.local/bin/copilot")) if os.path.isfile(p)),
            "copilot",
        ),
        "profile_path": _profile_path("~/.copilot-phil"),
        "env_overrides": {
            "COPILOT_HOME": _profile_path("~/.copilot-phil"),
            "PHIL_COPILOT_HOME": _profile_path("~/.copilot-phil"),
        },
        "config_path": _profile_path("~/.copilot-phil/config.json"),
        "provider": "GitHub Copilot",
        "default_model": "auto",
        "reasoning_effort": "medium",
        "capabilities": ["implementation", "code_review", "github_workflows"],
        "description": "GitHub Copilot CLI (Phil profile) for implementation and GitHub-native work",
    },
    "wei": {
        "name": "wei",
        "alias": "Wei",
        "worker_cli": "opencode",
        "executable": shutil.which("opencode") or "opencode",
        "profile_path": _profile_path("~/.config/opencode"),
        "env_overrides": {},
        "config_path": _profile_path("~/.config/opencode/opencode.json"),
        "provider": "OpenCode",
        "default_model": "opencode-go/glm-5.3-flash",
        "reasoning_effort": "medium",
        "capabilities": ["fast_implementation", "code_search", "testing"],
        "description": "OpenCode CLI (Wei profile) for fast implementation and testing",
    },
    "claude": {
        "name": "claude",
        "alias": "Claude",
        "worker_cli": "claude",
        "executable": shutil.which("claude") or "claude",
        "env_overrides": {},
        "config_path": None,
        "provider": "Anthropic",
        "default_model": "claude-3-7-sonnet",
        "reasoning_effort": "high",
        "capabilities": ["deep_reasoning", "architecture", "senior_code_review"],
        "description": "Anthropic Claude Code CLI for high-consequence reasoning and review",
    },
    "agy": {
        "name": "agy",
        "alias": "Argie",
        "worker_cli": "agy",
        "executable": shutil.which("agy") or "agy",
        "env_overrides": {},
        "config_path": None,
        "provider": "Google AI Pro",
        "default_model": "gemini-2.5-pro",
        "reasoning_effort": "medium",
        "capabilities": ["survey", "synthesis", "audit", "exploration"],
        "description": "Antigravity CLI for research, synthesis, and audit consulting",
    },
}

# Public worker identity alongside the backward-compatible runtime identifier.
AGENT_REGISTRY["argie"] = {
    **AGENT_REGISTRY["agy"],
    "name": "argie",
    "default_model": "unknown",
    "reasoning_effort": "unknown",
}

AGENT_ALIASES = {"codex": "jax", "opencode": "wei"}


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
        raise ValueError("Unsupported Argie reasoning effort")
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
        cmd = [exe, "-p", packet, "--model", effective_model]
        # Copilot rejects a reasoning effort for its "auto" model router.
        if effective_model != "auto":
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
        # Use Phil's saved profile auth and the explicit tool grants above.
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
