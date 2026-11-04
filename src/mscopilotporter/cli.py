"""mscopilotporter command line: init | serve | key | doctor | tunnel | service."""
from __future__ import annotations

import argparse
import os
import secrets
import shutil
import stat
import subprocess
import sys
from importlib import resources
from pathlib import Path

from mscopilotporter import paths

KEY_NAME = "MSCP_KEY"


def _read_key() -> str | None:
    f = paths.secrets_file()
    if not f.exists():
        return None
    for line in f.read_text().splitlines():
        line = line.strip()
        if line.startswith((f"{KEY_NAME}=", "M3_MCP_KEY=")):
            return line.split("=", 1)[1].strip()
    return None


def _write_key(key: str) -> None:
    f = paths.secrets_file()
    f.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(f"{KEY_NAME}={key}\n")
    os.chmod(f, 0o600)


def cmd_init(args) -> int:
    cfg = paths.config_file()
    cfg.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if cfg.exists() and not args.force:
        print(f"Config exists, left unchanged: {cfg}")
    else:
        template = resources.files("mscopilotporter").joinpath("templates/config.example.yaml").read_text()
        cfg.write_text(template)
        print(f"Wrote config: {cfg}")
    if _read_key() is None:
        _write_key(secrets.token_urlsafe(32))
        print(f"Generated API key: {paths.secrets_file()} (mode 0600)")
    else:
        print(f"API key exists, left unchanged: {paths.secrets_file()}")
    print("Next: edit the config (add workspaces), then run `mscopilotporter doctor`.")
    return 0


def cmd_key(args) -> int:
    if args.action == "rotate":
        _write_key(secrets.token_urlsafe(32))
        print("Key rotated. Update the Copilot Studio connection and restart the gateway.")
        return 0
    key = _read_key()
    if key is None:
        print("No key yet. Run `mscopilotporter init`.", file=sys.stderr)
        return 1
    print(key)
    return 0


def cmd_serve(args) -> int:
    from mscopilotporter.config import load_config

    cfg = load_config().get("server", {}) or {}
    hosts = cfg.get("allowed_hosts") or []
    if hosts and not os.environ.get("MSCP_ALLOWED_HOSTS"):
        os.environ["MSCP_ALLOWED_HOSTS"] = ",".join(
            h for host in hosts for h in (host, f"{host}:*")
        )
    port = args.port or int(os.environ.get("MSCP_PORT") or cfg.get("port") or 8000)
    from mscopilotporter import server

    server.run(port)
    return 0


def _check(ok: bool, label: str, detail: str = "", fatal: bool = True) -> bool:
    mark = "ok  " if ok else ("FAIL" if fatal else "warn")
    print(f"[{mark}] {label}" + (f" - {detail}" if detail else ""))
    return ok or not fatal


def cmd_doctor(args) -> int:
    good = True
    good &= _check(sys.version_info >= (3, 11), "Python >= 3.11", sys.version.split()[0])
    good &= _check(shutil.which("bwrap") is not None, "bubblewrap (bwrap) installed", "sudo apt install bubblewrap")
    good &= _check(shutil.which("git") is not None, "git installed")
    cfg_file = paths.config_file()
    good &= _check(cfg_file.exists(), "config file exists", str(cfg_file))
    config = {}
    if cfg_file.exists():
        try:
            from mscopilotporter.config import load_config

            config = load_config()
            _check(True, "config parses")
        except Exception as exc:  # noqa: BLE001
            good &= _check(False, "config parses", str(exc))
    workspaces = config.get("workspaces") or {}
    good &= _check(bool(workspaces), "at least one workspace configured")
    for name, info in workspaces.items():
        p = Path(os.path.expanduser(str((info or {}).get("path", ""))))
        good &= _check(p.is_dir(), f"workspace '{name}' path exists", str(p))
    key_file = paths.secrets_file()
    if key_file.exists():
        mode = stat.S_IMODE(key_file.stat().st_mode)
        good &= _check(mode == 0o600, "key file mode 0600", oct(mode))
        good &= _check(bool(_read_key()), "API key present")
    else:
        good &= _check(False, "API key file exists", "run `mscopilotporter init`")
    server_cfg = config.get("server") or {}
    _check(bool(server_cfg.get("allowed_hosts")), "tunnel hostname in server.allowed_hosts",
           "needed for Copilot Studio to reach the gateway", fatal=False)
    _check(shutil.which("devtunnel") is not None, "devtunnel CLI installed", "optional, for `tunnel`", fatal=False)
    from mscopilotporter.agents.registry import build_registry

    registry, _ = build_registry(config.get("agents"))
    for name, info in registry.items():
        found = os.path.isfile(info["executable"]) and os.access(info["executable"], os.X_OK)
        _check(found, f"agent '{name}' ({info['worker_cli']}) executable", info["executable"], fatal=False)
    print("\nAll required checks passed." if good else "\nSome required checks failed.")
    return 0 if good else 1


def cmd_tunnel(args) -> int:
    from mscopilotporter.config import load_config

    name = args.name or ((load_config().get("tunnel") or {}).get("name"))
    if not name:
        print("Set tunnel.name in the config or pass --name.", file=sys.stderr)
        return 1
    exe = shutil.which("devtunnel")
    if not exe:
        print("devtunnel CLI not found. Install Microsoft Dev Tunnels first.", file=sys.stderr)
        return 1
    return subprocess.call([exe, "host", name])


UNIT = """[Unit]
Description=MScopilotPorter MCP gateway
After=network.target

[Service]
Type=simple
ExecStart={python} -m mscopilotporter serve
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


def cmd_service(args) -> int:
    unit = UNIT.format(python=sys.executable)
    if args.print_only:
        print(unit)
        return 0
    target = Path.home() / ".config/systemd/user/mscopilotporter.service"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(unit)
    print(f"Wrote {target}\nEnable with:\n  systemctl --user daemon-reload\n"
          "  systemctl --user enable --now mscopilotporter\n  loginctl enable-linger \"$USER\"")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="mscopilotporter", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init", help="create config and API key")
    p.add_argument("--force", action="store_true", help="overwrite an existing config")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("serve", help="run the gateway on 127.0.0.1")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_serve)
    p = sub.add_parser("key", help="show or rotate the API key")
    p.add_argument("action", choices=["show", "rotate"])
    p.set_defaults(func=cmd_key)
    p = sub.add_parser("doctor", help="check this machine and config")
    p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("tunnel", help="host the configured Dev Tunnel")
    p.add_argument("--name")
    p.set_defaults(func=cmd_tunnel)
    p = sub.add_parser("service", help="install a systemd user unit")
    p.add_argument("--print", dest="print_only", action="store_true", help="print instead of writing")
    p.set_defaults(func=cmd_service)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
