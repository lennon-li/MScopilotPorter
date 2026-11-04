import os
import json
import httpx
import logging
from typing import Optional
from mscopilotporter.tools.workspace import get_workspace, require_flag
from mscopilotporter.runner.sandbox import run_in_sandbox
from mscopilotporter.runner.jobs import job_manager

logger = logging.getLogger("mscopilotporter.tools_exec")

def exec_run(workspace_id: str, argv: list[str], cwd: str = "", timeout_seconds: int = 30, network_access: bool = False) -> dict:
    """Execute command in bubblewrap; network access is opt-in per command."""
    ws = get_workspace(workspace_id)
    require_flag(ws, "allow_execute")
    return run_in_sandbox(
        workspace_path=ws["path"],
        argv=argv,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        writable=ws["writable"],
        network_access=network_access
    )

def exec_start(workspace_id: str, argv: list[str], cwd: str = "", timeout_seconds: int = 300, network_access: bool = False) -> dict:
    """Start command asynchronously inside bubblewrap sandbox and return job ID."""
    ws = get_workspace(workspace_id)
    require_flag(ws, "allow_execute")
    job_id = job_manager.start_sandbox_job(
        workspace_id=workspace_id,
        workspace_path=ws["path"],
        argv=argv,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        writable=ws["writable"],
        network_access=network_access
    )
    return {
        "job_id": job_id,
        "workspace_id": workspace_id,
        "status": "running"
    }

def job_status(job_id: str) -> dict:
    """Query current status of an asynchronous job."""
    return job_manager.get_status(job_id)

def job_output(job_id: str, cursor: int = 0, max_bytes: int = 65536) -> dict:
    """Retrieve incremental stdout/stderr output from a job."""
    return job_manager.get_output(job_id, cursor=cursor, max_bytes=max_bytes)

def job_result(job_id: str, cursor: int = 0, max_bytes: int = 65536) -> dict:
    """Retrieve final result, status, and output of a job."""
    status_info = job_manager.get_status(job_id)
    output_info = job_manager.get_output(job_id, cursor=cursor, max_bytes=max_bytes)
    return {
        **status_info,
        "cursor": output_info["cursor"],
        "next_cursor": output_info["next_cursor"],
        "stdout": output_info["stdout"],
        "stderr": output_info["stderr"],
    }

def job_cancel(job_id: str) -> dict:
    """Cancel a running job."""
    return job_manager.cancel(job_id)

def local_http_request(workspace_id: str, port: int, method: str = "GET", path: str = "/", body: Optional[str] = None) -> dict:
    """Send an HTTP request strictly to a local application running on loopback (127.0.0.1)."""
    # Verify workspace exists
    get_workspace(workspace_id)

    if not (1 <= port <= 65535):
        raise ValueError(f"Invalid port number: {port}")

    clean_path = "/" + path.lstrip("/")
    url = f"http://127.0.0.1:{port}{clean_path}"

    headers = {"User-Agent": "MScopilotPorter-LocalClient/1.0"}
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.request(
                method=method.upper(),
                url=url,
                headers=headers,
                content=body.encode("utf-8") if body else None
            )
            return {
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "body": resp.text[:100000],
                "truncated": len(resp.text) > 100000
            }
    except Exception as e:
        return {
            "error": f"Failed to connect to local service on 127.0.0.1:{port}: {e}"
        }
