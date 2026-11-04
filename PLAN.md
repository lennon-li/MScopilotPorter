# Plan: turn M3 into the `mscopilotporter` package

Goal: a reusable, installable MCP gateway that lets Microsoft 365 Copilot Studio agents
use a local machine (files, git, sandboxed execution, CLI-agent workers). The package
contains no machine names, URLs, keys, or user paths. All deployment detail lives in
per-user config and state outside the package.

Source: an existing private deployment repo (gateway, broker, runner modules and tests).

## Principles

1. Package = code + defaults + templates. Deployment = config, secrets, state, tunnel.
2. Nothing user-specific in the repo: no hostnames, tunnel IDs, public URLs, home paths.
3. Secure by default: bind to 127.0.0.1, API key required, sandbox offline, no workspace
   writable unless configured.
4. One tool surface (MCP over Streamable HTTP, port configurable).

## Target layout

```text
MScopilotPorter/
  pyproject.toml            # name=mscopilotporter, console script `mscopilotporter`
  README.md  SETUP.md  SECURITY.md  LICENSE
  src/mscopilotporter/
    cli.py                  # init | serve | key | doctor | tunnel | service
    server.py auth.py       # from gateway/
    tools/{agents,artifacts,exec,files,git,workspace}.py
    agents/registry.py opencode.py   # from broker/
    runner/{jobs,sandbox}.py
    paths.py                # XDG config/state resolution (single source of truth)
    templates/              # config.example.yaml, systemd unit templates
  tests/
  docs/
```

## Phase 1 – Remove local assumptions (code changes)

| Today (hardcoded) | Becomes |
|---|---|
| `~/.config/m3-agent-gateway/secrets.env` | `$XDG_CONFIG_HOME/mscopilotporter/secrets.env` (`MSCP_KEY` env override) |
| `~/m3-agent-gateway/runtime/{jobs.db,jobs,etc}` | `$XDG_STATE_HOME/mscopilotporter/` |
| `config/workspaces.yaml` in repo | `$XDG_CONFIG_HOME/mscopilotporter/config.yaml` (`MSCP_CONFIG` override) |
| Fallback executables `/home/<user>/.local/bin/...` in the agent registry | `shutil.which()` only; agent unavailable if not found |
| Agent profiles `~/.codex-liz`, `~/.codex-prof`, personas Jax/Liz/Prof/Wei/Argie | Agents declared in `config.yaml` (name, CLI, profile path, default model); ship generic examples only |
| Sandbox `PATH` containing a user home | Derived from the config and the detected home |
| `X-M3-MCP-Key` header | `X-MSCP-Key` (accept `X-M3-MCP-Key` for one release, then drop) |
| Service name `m3-gateway`, port 8000 | `mscopilotporter.service`, port from config (default 8000) |
| `m3_switch.sh` (Asgard/BCC host switching) | Not shipped. Host failover is deployment-specific |
| `ALLOWED_HOSTS` env | `server.allowed_hosts` in config, default `127.0.0.1,localhost` |

## Phase 2 – CLI and packaging

- `pyproject.toml` (hatchling), Python >= 3.11, dependencies from the current `requirements.txt` (runtime only; pytest in a `dev` extra).
- `mscopilotporter init` creates the config dir, writes `config.yaml` from the template, generates the API key (mode 0600).
- `mscopilotporter serve` starts the gateway.
- `mscopilotporter key show|rotate`.
- `mscopilotporter doctor` checks bubblewrap, git, config validity, key file mode, loopback bind, and which agent CLIs are found.
- `mscopilotporter tunnel` is a thin wrapper around `devtunnel host <tunnel-name>` using names from config.
- `mscopilotporter service install` renders a systemd user unit from a template using the resolved executable path. No static unit files with paths.
- The bundled `cloudflared` binary is not shipped. Document how to install tunnel tooling instead.

## Phase 3 – Docs and tests

- `SETUP.md`: step-by-step install and Copilot Studio connection (drafted in this repo).
- `SECURITY.md`: threat model, key handling, sandbox guarantees, what to never expose.
- Tests: move existing tests to the package, add tests for the path resolution, `init`, and the config schema. Run in CI on Linux (GitHub Actions).
- Leak check in CI: fail if the tree matches any local-info patterns (`/home/`, `devtunnels.ms`, personal hostnames, key-like strings).

## Phase 4 – Publish

1. Scrub history: copy source into a fresh tree. Do not carry over `m3` git history, because it holds hostnames, URLs and tunnel IDs.
2. Run the leak check. Review the first commit manually. The repo is public.
3. Tag `v0.1.0`. Optionally publish to PyPI.
4. Convert the original `m3` repo into a thin private deployment wrapper that pins a
   `mscopilotporter` version and keeps only its own config, units and runbooks.

## Status (v0.1.0)

Phases 1-3 are implemented. Phase 4: the history-free tree is published; PyPI publishing and
converting the private deployment repo into a thin wrapper are left to the maintainer.
Decisions taken: workers included, configured under `agents:`; MIT license; single API key.

## Open decisions (resolved above)

- Keep the Codex/OpenCode/Agy/Claude/Copilot CLI workers in v0.1, or ship exec/files/git only and make workers an optional plugin?
- License (MIT vs Apache-2.0).
- Single-tenant only (one key), or per-client keys?

## Risks

- Renaming the header and service breaks existing Copilot connections. Use the compatibility window in Phase 1.
- History scrub means losing `m3` commit history in the public repo. This is intentional.
- Workers run with the user's CLI credentials. The package must document that and never copy profiles.
