import os
import base64
import mimetypes
import logging
from mscopilotporter.tools.workspace import get_workspace, require_flag, validate_workspace_path

logger = logging.getLogger("mscopilotporter.tools_artifacts")

IMAGE_EXTENSIONS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".svg": "image/svg+xml"}

def list_artifacts(workspace_id: str, path: str = "artifacts") -> list[dict]:
    """List artifacts located in the specified workspace directory."""
    ws = get_workspace(workspace_id)
    require_flag(ws, "allow_artifacts")
    try:
        artifact_dir = validate_workspace_path(workspace_id, path)
    except Exception:
        return []

    if not os.path.exists(artifact_dir):
        return []

    results = []
    for root, _, files in os.walk(artifact_dir):
        for fn in files:
            full_p = os.path.join(root, fn)
            rel_p = os.path.relpath(full_p, ws["path"])
            size = os.path.getsize(full_p)
            ext = os.path.splitext(fn)[1].lower()
            results.append({
                "path": rel_p,
                "name": fn,
                "size_bytes": size,
                "type": "image" if ext in IMAGE_EXTENSIONS else "text"
            })
    results.sort(key=lambda x: x["path"])
    return results

def read_artifact(workspace_id: str, path: str) -> dict:
    """Read an artifact file, returning text content or base64 encoded data for images."""
    require_flag(get_workspace(workspace_id), "allow_artifacts")
    full_path = validate_workspace_path(workspace_id, path)
    if not os.path.isfile(full_path):
        raise FileNotFoundError(f"Artifact not found: '{path}'")

    size = os.path.getsize(full_path)
    ext = os.path.splitext(full_path)[1].lower()

    if ext in IMAGE_EXTENSIONS:
        mime = IMAGE_EXTENSIONS[ext]
        with open(full_path, "rb") as f:
            data = f.read()
        b64_str = base64.b64encode(data).decode("ascii")
        return {
            "path": path,
            "type": "image",
            "mime_type": mime,
            "size_bytes": size,
            "base64_data": b64_str
        }

    # Text / data artifact
    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read(200000)
    return {
        "path": path,
        "type": "text",
        "mime_type": mimetypes.guess_type(full_path)[0] or "text/plain",
        "size_bytes": size,
        "content": content,
        "truncated": size > 200000
    }
