# MScopilotPorter

A local MCP (Model Context Protocol, Streamable HTTP) gateway that lets Microsoft 365
Copilot Studio agents work with a machine you control: files, git, sandboxed command
execution, and delegated CLI-agent workers.

- Binds to `127.0.0.1` only. Expose it with a tunnel or reverse proxy you manage.
- API-key authentication (`X-MSCP-Key` header).
- Commands run in a bubblewrap sandbox, offline by default.
- Only workspaces you list in your config are visible.

See [SETUP.md](SETUP.md) to install and connect Copilot Studio, and [PLAN.md](PLAN.md)
for the packaging roadmap. Status: pre-release, Phase 1 of the plan.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Tests are hermetic: they create a temporary config, state directory and fixture workspace.
