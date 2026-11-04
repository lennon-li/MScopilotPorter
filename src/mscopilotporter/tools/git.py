import os
import subprocess
import logging
from mscopilotporter.tools.workspace import get_workspace

logger = logging.getLogger("mscopilotporter.tools_git")

def _run_git_cmd(ws_path: str, args: list[str], timeout: int = 10) -> str:
    res = subprocess.run(
        ["git"] + args,
        cwd=ws_path,
        capture_output=True,
        text=True,
        timeout=timeout
    )
    if res.returncode != 0:
        return f"Git error (exit {res.returncode}): {res.stderr.strip()}"
    return res.stdout

def git_status(workspace_id: str) -> str:
    """Show git working tree status in workspace."""
    ws = get_workspace(workspace_id)
    out = _run_git_cmd(ws["path"], ["status", "--short", "--branch"])
    return out if out.strip() else "Clean working tree."

def git_diff(workspace_id: str, staged: bool = False) -> str:
    """Show git diff in workspace (staged or unstaged)."""
    ws = get_workspace(workspace_id)
    args = ["diff", "--staged"] if staged else ["diff"]
    out = _run_git_cmd(ws["path"], args)
    return out if out.strip() else "No diff."

def git_log(workspace_id: str, limit: int = 10) -> str:
    """Show recent git commit history in workspace."""
    ws = get_workspace(workspace_id)
    n = max(1, min(limit, 50))
    return _run_git_cmd(ws["path"], ["log", f"-n{n}", "--oneline", "--decorate"])

def git_show(workspace_id: str, ref: str = "HEAD") -> str:
    """Show commit details or object content in workspace."""
    ws = get_workspace(workspace_id)
    # Basic sanitize to prevent argument injection
    clean_ref = ref.strip()
    if clean_ref.startswith("-"):
        raise ValueError("Invalid ref")
    return _run_git_cmd(ws["path"], ["show", "--stat", "-p", clean_ref])

def git_fetch(workspace_id: str, remote: str = "origin") -> str:
    """Fetch branches and tags from remote."""
    ws = get_workspace(workspace_id)
    clean_remote = remote.strip()
    if clean_remote.startswith("-"):
        raise ValueError("Invalid remote")
    out = _run_git_cmd(ws["path"], ["fetch", clean_remote], timeout=60)
    return out if out.strip() else f"Fetched from {clean_remote} successfully."

def git_pull(workspace_id: str, remote: str = "origin", branch: str = "") -> str:
    """Pull changes from remote repository into current branch."""
    ws = get_workspace(workspace_id)
    if not ws.get("writable", True):
        raise PermissionError(f"Workspace '{workspace_id}' is read-only")
    clean_remote = remote.strip()
    if clean_remote.startswith("-"):
        raise ValueError("Invalid remote")
    args = ["pull", clean_remote]
    if branch.strip():
        clean_branch = branch.strip()
        if clean_branch.startswith("-"):
            raise ValueError("Invalid branch")
        args.append(clean_branch)
    out = _run_git_cmd(ws["path"], args, timeout=60)
    return out if out.strip() else "Pull completed successfully."

def git_push(workspace_id: str, remote: str = "origin", branch: str = "", set_upstream: bool = False) -> str:
    """Push local commits to remote repository."""
    ws = get_workspace(workspace_id)
    clean_remote = remote.strip()
    if clean_remote.startswith("-"):
        raise ValueError("Invalid remote")
    args = ["push"]
    if set_upstream:
        args.append("-u")
    args.append(clean_remote)
    if branch.strip():
        clean_branch = branch.strip()
        if clean_branch.startswith("-"):
            raise ValueError("Invalid branch")
        args.append(clean_branch)
    out = _run_git_cmd(ws["path"], args, timeout=60)
    return out if out.strip() else f"Pushed to {clean_remote} successfully."

def git_commit(workspace_id: str, message: str, all_files: bool = False) -> str:
    """Commit changes with the specified commit message."""
    ws = get_workspace(workspace_id)
    if not ws.get("writable", True):
        raise PermissionError(f"Workspace '{workspace_id}' is read-only")
    clean_msg = message.strip()
    if not clean_msg:
        raise ValueError("Commit message cannot be empty")
    args = ["commit", "-a", "-m", clean_msg] if all_files else ["commit", "-m", clean_msg]
    out = _run_git_cmd(ws["path"], args, timeout=30)
    return out if out.strip() else "Committed changes successfully."

def git_add(workspace_id: str, paths: list[str]) -> str:
    """Stage file paths for git commit."""
    ws = get_workspace(workspace_id)
    if not ws.get("writable", True):
        raise PermissionError(f"Workspace '{workspace_id}' is read-only")
    if not paths or not isinstance(paths, list):
        raise ValueError("paths must be a non-empty list of strings")
    clean_paths = []
    for p in paths:
        s = p.strip()
        if not s or s.startswith("-"):
            raise ValueError(f"Invalid path: {p}")
        clean_paths.append(s)
    out = _run_git_cmd(ws["path"], ["add", "--"] + clean_paths, timeout=30)
    return out if out.strip() else "Staged files successfully."

def git_branch(workspace_id: str) -> str:
    """List local and remote branches in workspace."""
    ws = get_workspace(workspace_id)
    out = _run_git_cmd(ws["path"], ["branch", "-a"])
    return out if out.strip() else "No branches found."

def git_checkout(workspace_id: str, branch: str, create: bool = False) -> str:
    """Switch branches or restore working tree files."""
    ws = get_workspace(workspace_id)
    if not ws.get("writable", True):
        raise PermissionError(f"Workspace '{workspace_id}' is read-only")
    clean_branch = branch.strip()
    if not clean_branch or clean_branch.startswith("-"):
        raise ValueError(f"Invalid branch: {branch}")
    args = ["checkout", "-b", clean_branch] if create else ["checkout", clean_branch]
    out = _run_git_cmd(ws["path"], args, timeout=30)
    return out if out.strip() else f"Switched to branch {clean_branch}."
