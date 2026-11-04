import os
import sys
import time
import json
import logging
import functools
from logging.handlers import RotatingFileHandler
from typing import Optional, Any
from starlette.types import ASGIApp, Scope, Receive, Send
import uvicorn
from mcp.server.mcpserver import MCPServer

from mscopilotporter import paths
from mscopilotporter.auth import AuthAndSecurityMiddleware, API_KEY, SECRETS_FILE
from mscopilotporter.tools.workspace import get_capabilities as _get_capabilities
from mscopilotporter.tools.workspace import list_workspaces as _list_workspaces
from mscopilotporter.tools.workspace import workspace_info as _workspace_info
from mscopilotporter.tools.files import list_files as _list_files
from mscopilotporter.tools.files import search_text as _search_text
from mscopilotporter.tools.files import read_file as _read_file
from mscopilotporter.tools.files import write_file as _write_file
from mscopilotporter.tools.files import apply_patch as _apply_patch
from mscopilotporter.tools.files import mkdir as _mkdir
from mscopilotporter.tools.files import move_path as _move_path
from mscopilotporter.tools.files import trash_path as _trash_path
from mscopilotporter.tools.exec import exec_run as _exec_run
from mscopilotporter.tools.exec import exec_start as _exec_start
from mscopilotporter.tools.exec import job_status as _job_status
from mscopilotporter.tools.exec import job_output as _job_output
from mscopilotporter.tools.exec import job_result as _job_result
from mscopilotporter.tools.exec import job_cancel as _job_cancel
from mscopilotporter.tools.exec import local_http_request as _local_http_request
from mscopilotporter.tools.git import git_status as _git_status
from mscopilotporter.tools.git import git_diff as _git_diff
from mscopilotporter.tools.git import git_log as _git_log
from mscopilotporter.tools.git import git_show as _git_show
from mscopilotporter.tools.git import git_pull as _git_pull
from mscopilotporter.tools.git import git_push as _git_push
from mscopilotporter.tools.git import git_fetch as _git_fetch
from mscopilotporter.tools.git import git_commit as _git_commit
from mscopilotporter.tools.git import git_add as _git_add
from mscopilotporter.tools.git import git_branch as _git_branch
from mscopilotporter.tools.git import git_checkout as _git_checkout
from mscopilotporter.tools.artifacts import list_artifacts as _list_artifacts
from mscopilotporter.tools.artifacts import read_artifact as _read_artifact
from mscopilotporter.tools.agents import list_agents as _list_agents
from mscopilotporter.tools.agents import dispatch_agent as _dispatch_agent
from typing import Literal

# Configure structured logging with rotation
LOG_DIR = str(paths.log_dir())
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "gateway.log")

logger = logging.getLogger("mscopilotporter")
logger.setLevel(logging.INFO)

file_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5)
file_formatter = logging.Formatter('{"time":"%(asctime)s", "level":"%(levelname)s", "event":%(message)s}')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
logger.addHandler(stream_handler)

def log_tool_event(tool_name: str, kwargs: dict, result: Any, duration: float, error: Optional[Exception] = None):
    event = {
        "tool": tool_name,
        "workspace_id": kwargs.get("workspace_id"),
        "path": kwargs.get("path"),
        "job_id": kwargs.get("job_id"),
        "agent": kwargs.get("agent"),
        "network_access": kwargs.get("network_access"),
        "duration_sec": round(duration, 4),
        "status": "error" if error else "ok",
        "error": str(error) if error else None,
    }
    if isinstance(result, (dict, list, str)):
        raw_len = len(json.dumps(result)) if not isinstance(result, str) else len(result)
        event["bytes_returned"] = raw_len
    try:
        logger.info(json.dumps(event))
    except Exception:
        pass

def monitored_tool(tool_name: str):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(**kwargs):
            t0 = time.time()
            try:
                res = fn(**kwargs)
                log_tool_event(tool_name, kwargs, res, time.time() - t0)
                return res
            except Exception as e:
                log_tool_event(tool_name, kwargs, None, time.time() - t0, error=e)
                raise
        return wrapper
    return decorator

mcp = MCPServer("mscopilotporter")

# --- System & Workspace Tools ---

@mcp.tool()
@monitored_tool("get_capabilities")
def get_capabilities() -> dict:
    """Return gateway capabilities, sandbox status, and available runtimes."""
    return _get_capabilities()

@mcp.tool()
@monitored_tool("list_workspaces")
def list_workspaces() -> list[dict]:
    """List all configured workspaces with ID and status."""
    return _list_workspaces()

@mcp.tool()
@monitored_tool("workspace_info")
def workspace_info(workspace_id: str) -> dict:
    """Return comprehensive development context (git, language, runtimes) for a workspace."""
    return _workspace_info(workspace_id)

# --- File & Search Tools ---

@mcp.tool()
@monitored_tool("list_files")
def list_files(workspace_id: str, path: str = "", depth: Optional[int] = None) -> list[str]:
    """List files in the workspace, skipping internal cache and git directories."""
    return _list_files(workspace_id, path=path, depth=depth)

@mcp.tool()
@monitored_tool("search_text")
def search_text(workspace_id: str, query: str, glob: str = "*", max_results: int = 50) -> list[dict]:
    """Search for text in workspace files using ripgrep."""
    return _search_text(workspace_id, query=query, glob=glob, max_results=max_results)

@mcp.tool()
@monitored_tool("read_file")
def read_file(workspace_id: str, path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> dict:
    """Read full or sliced lines of a file, returning content and SHA-256."""
    return _read_file(workspace_id, path, start_line=start_line, end_line=end_line)

@mcp.tool()
@monitored_tool("write_file")
def write_file(workspace_id: str, path: str, content: str, expected_sha256: Optional[str] = None) -> dict:
    """Write content to a file atomically with optional stale-file protection."""
    return _write_file(workspace_id, path, content, expected_sha256=expected_sha256)

@mcp.tool()
@monitored_tool("apply_patch")
def apply_patch(workspace_id: str, patch: str) -> dict:
    """Apply a unified diff patch to the workspace."""
    return _apply_patch(workspace_id, patch)

@mcp.tool()
@monitored_tool("mkdir")
def mkdir(workspace_id: str, path: str) -> dict:
    """Create a directory in the workspace."""
    return _mkdir(workspace_id, path)

@mcp.tool()
@monitored_tool("move_path")
def move_path(workspace_id: str, source: str, destination: str) -> dict:
    """Move or rename a file or folder within the workspace."""
    return _move_path(workspace_id, source, destination)

@mcp.tool()
@monitored_tool("trash_path")
def trash_path(workspace_id: str, path: str) -> dict:
    """Move a file to the recoverable .trash folder rather than permanently deleting it."""
    return _trash_path(workspace_id, path)

# --- Execution & Job Tools ---

@mcp.tool()
@monitored_tool("exec_run")
def exec_run(workspace_id: str, argv: list[str], cwd: str = "", timeout_seconds: int = 30, network_access: bool = False) -> dict:
    """Execute inside bubblewrap; opt in to host network per command when needed."""
    return _exec_run(workspace_id, argv=argv, cwd=cwd, timeout_seconds=timeout_seconds, network_access=network_access)

@mcp.tool()
@monitored_tool("exec_start")
def exec_start(workspace_id: str, argv: list[str], cwd: str = "", timeout_seconds: int = 300, network_access: bool = False) -> dict:
    """Start inside bubblewrap; opt in to host network per command when needed."""
    return _exec_start(workspace_id, argv=argv, cwd=cwd, timeout_seconds=timeout_seconds, network_access=network_access)

@mcp.tool()
@monitored_tool("job_status")
def job_status(job_id: str) -> dict:
    """Query the status of an asynchronous job."""
    return _job_status(job_id)

@mcp.tool()
@monitored_tool("job_output")
def job_output(job_id: str, cursor: int = 0, max_bytes: int = 65536) -> dict:
    """Retrieve incremental output from a running or completed job."""
    return _job_output(job_id, cursor=cursor, max_bytes=max_bytes)

@mcp.tool()
@monitored_tool("job_result")
def job_result(job_id: str, cursor: int = 0, max_bytes: int = 65536) -> dict:
    """Retrieve full result and output of a job."""
    return _job_result(job_id, cursor=cursor, max_bytes=max_bytes)

@mcp.tool()
@monitored_tool("job_cancel")
def job_cancel(job_id: str) -> dict:
    """Cancel a running job."""
    return _job_cancel(job_id)

@mcp.tool()
@monitored_tool("local_http_request")
def local_http_request(workspace_id: str, port: int, method: str = "GET", path: str = "/", body: Optional[str] = None) -> dict:
    """Test a local application listening on loopback (127.0.0.1)."""
    return _local_http_request(workspace_id, port=port, method=method, path=path, body=body)

# --- Git Inspection Tools ---

@mcp.tool()
@monitored_tool("git_status")
def git_status(workspace_id: str) -> str:
    """Show git status in the workspace (read-only)."""
    return _git_status(workspace_id)

@mcp.tool()
@monitored_tool("git_diff")
def git_diff(workspace_id: str, staged: bool = False) -> str:
    """Show git diff in the workspace (read-only)."""
    return _git_diff(workspace_id, staged=staged)

@mcp.tool()
@monitored_tool("git_log")
def git_log(workspace_id: str, limit: int = 10) -> str:
    """Show git log history in the workspace (read-only)."""
    return _git_log(workspace_id, limit=limit)

@mcp.tool()
@monitored_tool("git_show")
def git_show(workspace_id: str, ref: str = "HEAD") -> str:
    """Show git object or commit in the workspace (read-only)."""
    return _git_show(workspace_id, ref=ref)

@mcp.tool()
@monitored_tool("git_fetch")
def git_fetch(workspace_id: str, remote: str = "origin") -> str:
    """Fetch branches and tags from remote repository."""
    return _git_fetch(workspace_id, remote=remote)

@mcp.tool()
@monitored_tool("git_pull")
def git_pull(workspace_id: str, remote: str = "origin", branch: str = "") -> str:
    """Pull changes from remote repository into current branch."""
    return _git_pull(workspace_id, remote=remote, branch=branch)

@mcp.tool()
@monitored_tool("git_push")
def git_push(workspace_id: str, remote: str = "origin", branch: str = "", set_upstream: bool = False) -> str:
    """Push local commits to remote repository."""
    return _git_push(workspace_id, remote=remote, branch=branch, set_upstream=set_upstream)

@mcp.tool()
@monitored_tool("git_commit")
def git_commit(workspace_id: str, message: str, all_files: bool = False) -> str:
    """Commit changes with the specified commit message."""
    return _git_commit(workspace_id, message=message, all_files=all_files)

@mcp.tool()
@monitored_tool("git_add")
def git_add(workspace_id: str, paths: list[str]) -> str:
    """Stage file paths for git commit."""
    return _git_add(workspace_id, paths=paths)

@mcp.tool()
@monitored_tool("git_branch")
def git_branch(workspace_id: str) -> str:
    """List local and remote branches in workspace."""
    return _git_branch(workspace_id)

@mcp.tool()
@monitored_tool("git_checkout")
def git_checkout(workspace_id: str, branch: str, create: bool = False) -> str:
    """Switch branches or create a new branch in workspace."""
    return _git_checkout(workspace_id, branch=branch, create=create)

# --- Artifact Tools ---

@mcp.tool()
@monitored_tool("list_artifacts")
def list_artifacts(workspace_id: str, path: str = "artifacts") -> list[dict]:
    """List artifacts in the workspace."""
    return _list_artifacts(workspace_id, path=path)

@mcp.tool()
@monitored_tool("read_artifact")
def read_artifact(workspace_id: str, path: str) -> dict:
    """Read an artifact file (supports text and base64 images)."""
    return _read_artifact(workspace_id, path=path)

# --- Agent Broker Tools ---

@mcp.tool()
@monitored_tool("list_agents")
def list_agents() -> list[dict]:
    """List configured worker agents and their availability."""
    return _list_agents()

@mcp.tool()
@monitored_tool("dispatch_agent")
def dispatch_agent(
    agent: str,
    task: str,
    workspace_id: str,
    purpose: str = "",
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    network_access: bool = False,
) -> dict:
    """Dispatch an authorized CLI agent worker asynchronously per DISPATCH_CONTRACT."""
    return _dispatch_agent(
        agent=agent,
        task=task,
        workspace_id=workspace_id,
        purpose=purpose,
        model=model,
        reasoning_effort=reasoning_effort,
        network_access=network_access,
    )

from mcp.server.transport_security import TransportSecuritySettings

def create_app() -> ASGIApp:
    """Create ASGI application wrapping Streamable HTTP with auth & security middleware."""
    # Internal MCP SDK rebinding check is replaced with our configurable AuthAndSecurityMiddleware
    streamable_app = mcp.streamable_http_app(
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    return AuthAndSecurityMiddleware(streamable_app)

def run(port: int | None = None) -> None:
    """Serve on loopback only; expose it with a tunnel or reverse proxy you manage."""
    port = port or int(os.environ.get("MSCP_PORT") or os.environ.get("GATEWAY_PORT", "8000"))
    logger.info("Starting MScopilotPorter on 127.0.0.1:%d", port)
    uvicorn.run(create_app(), host="127.0.0.1", port=port)


if __name__ == "__main__":
    run()
