import os
import yaml
import subprocess
import logging

from mscopilotporter import paths
from typing import Optional

logger = logging.getLogger("mscopilotporter.tools_workspace")

CONFIG_PATH = str(paths.config_file())

def load_workspaces_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("workspaces", {})

def _auto_discovered_workspaces(workspaces: dict) -> dict:
    """Find immediate, non-hidden project directories in opted-in workspaces."""
    discovered = {}
    for parent_id, info in workspaces.items():
        if not info.get("auto_discover"):
            continue

        root = os.path.realpath(os.path.expanduser(info["path"]))
        if not os.path.isdir(root):
            continue

        try:
            entries = os.scandir(root)
        except OSError as exc:
            logger.warning("Could not scan auto-discovery root %s: %s", root, exc)
            continue

        with entries:
            for entry in entries:
                # Hidden folders hold metadata, caches, or trash, rather than projects.
                if entry.name.startswith(".") or entry.name in workspaces:
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue

                path = os.path.realpath(entry.path)
                if path == root or not path.startswith(root + os.sep):
                    continue
                discovered[entry.name] = {
                    "path": path,
                    "writable": info.get("writable", True),
                    "description": f"Auto-discovered project in {parent_id}",
                }
    return discovered

def get_workspace(workspace_id: str) -> dict:
    workspaces = load_workspaces_config()
    if workspace_id not in workspaces:
        workspaces = {**_auto_discovered_workspaces(workspaces), **workspaces}
    if workspace_id not in workspaces:
        raise KeyError(f"Unknown workspace_id: '{workspace_id}'. Available: {list(workspaces.keys())}")
    ws = workspaces[workspace_id]
    ws_path = os.path.realpath(os.path.expanduser(ws["path"]))
    return {
        "id": workspace_id,
        "path": ws_path,
        "writable": ws.get("writable", True),
        "description": ws.get("description", "")
    }

def validate_workspace_path(workspace_id: str, rel_path: str, allow_root: bool = False) -> str:
    """Validate that rel_path is strictly project-relative and resolves within workspace root."""
    ws = get_workspace(workspace_id)
    ws_root = ws["path"]

    if not rel_path or rel_path == ".":
        if allow_root:
            return ws_root
        raise ValueError("Root directory not allowed for this operation")

    # Explicitly reject absolute paths
    if os.path.isabs(rel_path) or rel_path.startswith("/"):
        raise ValueError(f"Absolute paths not permitted: '{rel_path}'. Paths must be project-relative.")

    clean_rel = os.path.normpath(rel_path)
    if clean_rel.startswith("..") or clean_rel == "..":
        raise ValueError(f"Path traversal detected: '{rel_path}'")

    target = os.path.realpath(os.path.join(ws_root, clean_rel))
    if not (target == ws_root or target.startswith(ws_root + os.sep)):
        raise ValueError(f"Path escape attempt blocked: '{rel_path}' -> '{target}'")

    # Block access to credentials and sensitive git metadata
    parts = clean_rel.split(os.sep)
    for p in parts:
        if p in [".git", ".ssh", ".env", "id_rsa", "id_ed25519", "secrets.env"]:
            raise ValueError(f"Access to sensitive or internal path blocked: '{rel_path}'")

    return target

def get_capabilities() -> dict:
    """Return gateway capabilities, sandbox status, and available runtimes."""
    return {
        "gateway_version": "1.0.0",
        "transport": "mcp_streamable_http",
        "auth": "header_api_key (X-MSCP-Key)",
        "sandbox": "bubblewrap_0.9.0",
        "sandbox_network": "disabled by default; exec network_access opt-in; delegated Codex network_access opt-in uses the configured host allowlist",
        "supported_tools": [
            "get_capabilities", "list_workspaces", "workspace_info",
            "list_files", "search_text", "read_file", "write_file", "apply_patch", "mkdir", "move_path", "trash_path",
            "exec_run", "exec_start", "job_status", "job_output", "job_result", "job_cancel",
            "git_status", "git_diff", "git_log", "git_show", "git_fetch", "git_pull", "git_push",
            "git_commit", "git_add", "git_branch", "git_checkout",
            "local_http_request", "list_artifacts", "read_artifact",
            "list_agents", "dispatch_agent"
        ]
    }

def list_workspaces() -> list[dict]:
    """List all configured workspaces with ID and status."""
    configured = load_workspaces_config()
    workspaces = {**_auto_discovered_workspaces(configured), **configured}
    result = []
    for ws_id, info in workspaces.items():
        real_path = os.path.realpath(os.path.expanduser(info["path"]))
        result.append({
            "workspace_id": ws_id,
            "path": real_path,
            "exists": os.path.isdir(real_path),
            "writable": info.get("writable", True),
            "description": info.get("description", "")
        })
    return result

def workspace_info(workspace_id: str) -> dict:
    """Return comprehensive development context for a workspace."""
    ws = get_workspace(workspace_id)
    ws_path = ws["path"]

    if not os.path.isdir(ws_path):
        return {"error": f"Workspace directory does not exist on host: {ws_path}"}

    # Git details
    git_branch = None
    git_dirty = False
    git_dir = os.path.join(ws_path, ".git")
    if os.path.exists(git_dir):
        try:
            b_res = subprocess.run(["git", "branch", "--show-current"], cwd=ws_path, capture_output=True, text=True, timeout=5)
            git_branch = b_res.stdout.strip()
            s_res = subprocess.run(["git", "status", "--short"], cwd=ws_path, capture_output=True, text=True, timeout=5)
            git_dirty = bool(s_res.stdout.strip())
        except Exception:
            pass

    # Detected languages and marker files
    languages = []
    markers = []
    for fn in ["DESCRIPTION", "renv.lock", "NAMESPACE"]:
        if os.path.exists(os.path.join(ws_path, fn)):
            markers.append(fn)
            if "R" not in languages:
                languages.append("R")

    for fn in ["pyproject.toml", "requirements.txt", "setup.py", "Pipfile"]:
        if os.path.exists(os.path.join(ws_path, fn)):
            markers.append(fn)
            if "Python" not in languages:
                languages.append("Python")

    # Inspect directory for .R or .py files if not already detected
    if not languages:
        for root, dirs, files in os.walk(ws_path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if f.endswith((".R", ".r", ".Rmd", ".qmd")):
                    if "R" not in languages:
                        languages.append("R")
                elif f.endswith(".py"):
                    if "Python" not in languages:
                        languages.append("Python")
            if len(languages) >= 2:
                break

    # Host runtimes
    r_version = None
    try:
        r_ver = subprocess.run(["Rscript", "--version"], capture_output=True, text=True, timeout=5)
        r_version = (r_ver.stdout + r_ver.stderr).strip()
    except Exception:
        pass

    py_version = None
    try:
        py_ver = subprocess.run(["python3", "--version"], capture_output=True, text=True, timeout=5)
        py_version = py_ver.stdout.strip()
    except Exception:
        pass

    return {
        "workspace_id": workspace_id,
        "path": ws_path,
        "writable": ws["writable"],
        "git": {
            "is_repo": os.path.exists(git_dir),
            "branch": git_branch,
            "dirty": git_dirty
        },
        "languages": languages,
        "marker_files": markers,
        "r_version": r_version,
        "python_version": py_version
    }
