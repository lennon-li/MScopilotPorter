import os
import time
import signal
import subprocess
import logging
import tempfile
from urllib.parse import urlsplit

from mscopilotporter import paths
from typing import Optional

logger = logging.getLogger("mscopilotporter.sandbox")

DEFAULT_TIMEOUT = 30
MAX_OUTPUT_BYTES = 100 * 1024  # 100 KB
SYNTHETIC_ETC_DIR = str(paths.synthetic_etc_dir())
os.makedirs(SYNTHETIC_ETC_DIR, exist_ok=True)
PASSWD_FILE = os.path.join(SYNTHETIC_ETC_DIR, "passwd")
GROUP_FILE = os.path.join(SYNTHETIC_ETC_DIR, "group")
PROXY_ENV_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)

def ensure_synthetic_identity():
    uid, gid = os.getuid(), os.getgid()
    records = {
        PASSWD_FILE: f"root:x:0:0:root:/tmp:/bin/bash\nsandbox:x:{uid}:{gid}:Sandbox User:/tmp:/bin/bash\nnobody:x:65534:65534:nobody:/tmp:/bin/false\n",
        GROUP_FILE: f"root:x:0:\nsandbox:x:{gid}:\nnobody:x:65534:\n",
    }
    for path, content in records.items():
        fd, temporary = tempfile.mkstemp(prefix=".identity-", dir=SYNTHETIC_ETC_DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


ensure_synthetic_identity()


def sandbox_host_env(network_access: bool = False) -> dict[str, str]:
    """Return the minimal host environment passed to bwrap, including safe proxies only online."""
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "USER": "sandbox",
        "LOGNAME": "sandbox",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
    }
    if not network_access:
        return env

    for name in PROXY_ENV_VARS:
        value = os.environ.get(name)
        if value is None:
            continue
        if name.lower() != "no_proxy":
            parsed = urlsplit(value)
            if parsed.username is not None or parsed.password is not None:
                raise ValueError(
                    "Network access is unavailable because the configured proxy contains embedded credentials."
                )
            if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname:
                raise ValueError("Network access is unavailable because the configured proxy URL is invalid.")
        env[name] = value
    return env

def build_bwrap_args(workspace_path: str, writable: bool = True, sub_cwd: str = "", network_access: bool = False) -> list[str]:
    """Construct bubblewrap argument list isolating execution to workspace_path."""
    real_workspace = os.path.realpath(workspace_path)
    ensure_synthetic_identity()
    if not os.path.isdir(real_workspace):
        raise ValueError(f"Workspace path does not exist: {real_workspace}")

    target_cwd = "/workspace"
    if sub_cwd:
        rel_sub = os.path.normpath(sub_cwd).lstrip("/")
        if rel_sub.startswith("..") or os.path.isabs(rel_sub):
            raise ValueError(f"Invalid sub_cwd: {sub_cwd}")
        full_sub = os.path.realpath(os.path.join(real_workspace, rel_sub))
        if not (full_sub == real_workspace or full_sub.startswith(real_workspace + os.sep)):
            raise ValueError(f"sub_cwd escapes workspace: {sub_cwd}")
        target_cwd = os.path.join("/workspace", rel_sub)

    args = [
        "bwrap",
        # Kill the sandbox namespace with its launcher so background helpers
        # (dbus, git credential caches) cannot hold output pipes open.
        "--die-with-parent",
        # Read-only standard root paths
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/etc/alternatives", "/etc/alternatives",
        "--ro-bind", PASSWD_FILE, "/etc/passwd",
        "--ro-bind", GROUP_FILE, "/etc/group",
        # System essentials
        "--proc", "/proc",
        "--dev", "/dev",
        # Isolated private temporary filesystem
        "--tmpfs", "/tmp",
    ]

    # Optional read-only binds if they exist
    for opt_dir in ["/etc/R", "/etc/ssl/certs", "/etc/resolv.conf", "/etc/hosts"]:
        if os.path.exists(opt_dir):
            args.extend(["--ro-bind", opt_dir, opt_dir])

    # Host git/user configuration for commit author metadata
    host_home = os.path.expanduser("~")
    gitconfig_path = os.path.join(host_home, ".gitconfig")
    if os.path.isfile(gitconfig_path):
        args.extend(["--ro-bind", gitconfig_path, "/tmp/.gitconfig"])

    # Mount workspace
    if writable:
        args.extend(["--bind", real_workspace, "/workspace"])
    else:
        args.extend(["--ro-bind", real_workspace, "/workspace"])

    # Unshare namespaces
    args.extend(["--unshare-all"])
    if network_access:
        args.append("--share-net")
        ssh_dir = os.path.join(host_home, ".ssh")
        if os.path.isdir(ssh_dir):
            args.extend(["--dir", "/tmp/.ssh"])
            for fname in sorted(os.listdir(ssh_dir)):
                fpath = os.path.join(ssh_dir, fname)
                if os.path.isfile(fpath) and not fname.endswith("~") and not fname.endswith(".bak"):
                    args.extend(["--ro-bind", fpath, f"/tmp/.ssh/{fname}"])

        gh_config_dir = os.path.join(host_home, ".config", "gh")
        if os.path.isdir(gh_config_dir):
            args.extend(["--dir", "/tmp/.config", "--ro-bind", gh_config_dir, "/tmp/.config/gh"])

        local_bin = os.path.join(host_home, ".local", "bin")
        if os.path.isdir(local_bin):
            args.extend(["--ro-bind", local_bin, local_bin])

        ssh_auth_sock = os.environ.get("SSH_AUTH_SOCK")
        if ssh_auth_sock and os.path.exists(ssh_auth_sock):
            args.extend(["--bind", ssh_auth_sock, ssh_auth_sock])
    else:
        args.append("--unshare-net")

    path_env = f"{host_home}/.local/bin:/usr/local/bin:/usr/bin:/bin" if (network_access and os.path.isdir(os.path.join(host_home, ".local", "bin"))) else "/usr/local/bin:/usr/bin:/bin"

    if not network_access:
        args.append("--clearenv")

    args.extend([
        # Synthetic safe environment
        "--setenv", "PATH", path_env,
        "--setenv", "HOME", "/tmp",
        "--setenv", "USER", "sandbox",
        "--setenv", "LOGNAME", "sandbox",
        "--setenv", "LC_ALL", "C.UTF-8",
        "--setenv", "LANG", "C.UTF-8",
    ])
    if network_access and os.environ.get("SSH_AUTH_SOCK") and os.path.exists(os.environ["SSH_AUTH_SOCK"]):
        args.extend(["--setenv", "SSH_AUTH_SOCK", os.environ["SSH_AUTH_SOCK"]])

    # Chdir into workspace
    args.extend(["--chdir", target_cwd])

    return args

def run_in_sandbox(
    workspace_path: str,
    argv: list[str],
    cwd: str = "",
    timeout_seconds: int = DEFAULT_TIMEOUT,
    writable: bool = True,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
    network_access: bool = False
) -> dict:
    """Execute command inside bubblewrap sandbox with resource limits."""
    if not argv or not isinstance(argv, list):
        raise ValueError("argv must be a non-empty list of strings")

    bwrap_prefix = build_bwrap_args(workspace_path, writable=writable, sub_cwd=cwd, network_access=network_access)
    full_cmd = bwrap_prefix + ["--"] + argv

    t0 = time.time()
    timed_out = False
    proc = None

    try:
        proc = subprocess.Popen(
            full_cmd,
            env=sandbox_host_env(network_access=network_access),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=os.setsid  # New process group for clean teardown
        )
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        duration = time.time() - t0
        logger.warning("Execution timed out after %ds: %s", timeout_seconds, argv)
        if proc:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass
            stdout, stderr = proc.communicate()
        else:
            stdout, stderr = "", ""
        exit_code = 124
    except Exception as e:
        duration = time.time() - t0
        logger.error("Execution failed: %s", e)
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": f"Sandbox execution error: {e}",
            "duration": duration,
            "truncated": False,
            "timed_out": False
        }

    duration = time.time() - t0
    truncated = False

    if len(stdout.encode("utf-8")) > max_output_bytes:
        stdout = stdout.encode("utf-8")[:max_output_bytes].decode("utf-8", errors="replace") + "\n... [STDOUT TRUNCATED]"
        truncated = True

    if len(stderr.encode("utf-8")) > max_output_bytes:
        stderr = stderr.encode("utf-8")[:max_output_bytes].decode("utf-8", errors="replace") + "\n... [STDERR TRUNCATED]"
        truncated = True

    return {
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration": duration,
        "truncated": truncated,
        "timed_out": timed_out
    }
