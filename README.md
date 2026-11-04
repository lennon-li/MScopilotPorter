# MScopilotPorter

[![ci](https://github.com/lennon-li/MScopilotPorter/actions/workflows/ci.yml/badge.svg)](https://github.com/lennon-li/MScopilotPorter/actions/workflows/ci.yml)

A local **MCP (Model Context Protocol) gateway for Microsoft 365 Copilot Studio**. It lets a
Copilot Studio agent work with a machine you control: read and edit files, use git, run
commands in a sandbox, and hand work to CLI coding agents. You decide which folders it can
see and what it may do in each.

```text
Copilot Studio agent ──HTTPS──▶ your tunnel ──▶ 127.0.0.1:8000  MScopilotPorter
                                                   ├─ files / git tools
                                                   ├─ bubblewrap sandbox (commands)
                                                   └─ CLI workers (codex, copilot, opencode, claude, agy)
```

## Safe by default

- Listens on `127.0.0.1` only. You provide the HTTPS tunnel or proxy.
- Every request needs an API key (`X-MSCP-Key`).
- A workspace is **read-only, with no command execution and no artifact access** until you
  turn each on in the config.
- Commands run in a [bubblewrap](https://github.com/containers/bubblewrap) sandbox with no
  network unless a call opts in.

Read [SECURITY.md](SECURITY.md) before enabling writes or execution.

## Quick start

Requires Linux or WSL2, Python 3.11+, `bubblewrap`, `git` and `ripgrep`.

```bash
sudo apt install bubblewrap git ripgrep python3-venv
git clone https://github.com/lennon-li/MScopilotPorter.git && cd MScopilotPorter
python3 -m venv .venv && .venv/bin/pip install -e .

.venv/bin/mscopilotporter init      # creates config + API key
$EDITOR ~/.config/mscopilotporter/config.yaml   # add a workspace
.venv/bin/mscopilotporter doctor    # checks everything
.venv/bin/mscopilotporter serve     # http://127.0.0.1:8000
```

Then expose it over HTTPS and connect Copilot Studio. The full walkthrough, with
verification commands at each step, is in **[SETUP.md](SETUP.md)**.

## Configuration

`~/.config/mscopilotporter/config.yaml` (override the directory with `MSCP_CONFIG_DIR`):

```yaml
server:
  port: 8000
  allowed_hosts: ["<tunnel-host>"]   # hostname Copilot Studio reaches you on
workspaces:
  myproject:
    path: ~/projects/myproject
    writable: true         # edit files            (default: false)
    allow_execute: true    # run sandboxed commands and CLI workers (default: false)
    allow_artifacts: true  # list/read generated artifacts          (default: false)
agents:                      # optional CLI workers
  reviewer: {cli: codex}
```

| Key | Meaning |
|---|---|
| `server.port` | Local port (also `MSCP_PORT`). |
| `server.allowed_hosts` | Extra `Host` headers accepted. Loopback is always allowed. |
| `workspaces.<id>.path` | Folder exposed to the agent. `auto_discover: true` also exposes its immediate subfolders. |
| `workspaces.<id>.writable` / `allow_execute` / `allow_artifacts` | Per-workspace capabilities, all off by default. |
| `agents.<name>.cli` | `codex`, `copilot`, `opencode`, `claude` or `agy`. Optional: `profile_path`, `model`, `reasoning_effort`, `aliases`, `required_profile_files`. |
| `tunnel.name` | Dev Tunnel used by `mscopilotporter tunnel`. |

Environment overrides: `MSCP_CONFIG`, `MSCP_CONFIG_DIR`, `MSCP_STATE_DIR`, `MSCP_KEY`,
`MSCP_PORT`, `MSCP_ALLOWED_HOSTS`.

## Tools exposed to Copilot Studio

| Area | Tools |
|---|---|
| Workspace | `get_capabilities`, `list_workspaces`, `workspace_info` |
| Files | `list_files`, `search_text`, `read_file`, `write_file`, `apply_patch`, `mkdir`, `move_path`, `trash_path` |
| Execution | `exec_run`, `exec_start`, `job_status`, `job_output`, `job_result`, `job_cancel`, `local_http_request` |
| Git | `git_status`, `git_diff`, `git_log`, `git_show`, `git_fetch`, `git_pull`, `git_push`, `git_add`, `git_commit`, `git_branch`, `git_checkout` |
| Artifacts | `list_artifacts`, `read_artifact` |
| Agents | `list_agents`, `dispatch_agent` |

## Command line

| Command | Purpose |
|---|---|
| `mscopilotporter init [--force]` | Create the config and a random API key (mode 0600). |
| `mscopilotporter doctor` | Check dependencies, config, workspaces, key file and agent CLIs. |
| `mscopilotporter serve [--port N]` | Run the gateway on `127.0.0.1`. |
| `mscopilotporter key show` / `key rotate` | Print or replace the API key. |
| `mscopilotporter tunnel [--name N]` | Host a Microsoft Dev Tunnel (`devtunnel` CLI required). |
| `mscopilotporter service [--print]` | Write a systemd user unit. |

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
python scripts/leak_check.py   # fails on home paths, tunnel IDs or secrets in tracked files
```

Tests are hermetic: they create a temporary config, state directory and fixture workspace.
Sandbox tests are skipped automatically where bubblewrap cannot create sandboxes.

## Project status and license

Version 0.1.2. See [PLAN.md](PLAN.md) for how the package was derived and what is next.
Released under the [MIT License](LICENSE).
