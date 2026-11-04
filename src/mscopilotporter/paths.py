"""Single source of truth for per-user config and state locations.

Nothing here is machine specific: everything resolves from XDG variables or the
current user's home directory, and each location can be overridden by environment.
"""
import os
from pathlib import Path

APP = "mscopilotporter"


def _xdg(var: str, fallback: str) -> Path:
    base = os.environ.get(var)
    return Path(base) if base else Path.home() / fallback


def config_dir() -> Path:
    return Path(os.environ["MSCP_CONFIG_DIR"]) if os.environ.get("MSCP_CONFIG_DIR") else _xdg("XDG_CONFIG_HOME", ".config") / APP


def state_dir() -> Path:
    return Path(os.environ["MSCP_STATE_DIR"]) if os.environ.get("MSCP_STATE_DIR") else _xdg("XDG_STATE_HOME", ".local/state") / APP


def config_file() -> Path:
    return Path(os.environ["MSCP_CONFIG"]) if os.environ.get("MSCP_CONFIG") else config_dir() / "config.yaml"


def secrets_file() -> Path:
    return config_dir() / "secrets.env"


def jobs_db() -> Path:
    return state_dir() / "jobs.db"


def jobs_dir() -> Path:
    return state_dir() / "jobs"


def synthetic_etc_dir() -> Path:
    return state_dir() / "etc"


def log_dir() -> Path:
    return state_dir() / "logs"
