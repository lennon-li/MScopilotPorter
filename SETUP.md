# Setup guide

Connect a Microsoft 365 Copilot Studio agent to a local machine through an MCP endpoint.

## What you need

- Linux or WSL2 (Ubuntu 22.04+), Python 3.11+
- `bubblewrap`, `git` and `ripgrep` (`sudo apt install bubblewrap git ripgrep python3-venv`)
- A Microsoft 365 account with Copilot Studio access
- A tunnel to expose the local port over HTTPS: Microsoft Dev Tunnels (`devtunnel` CLI) or
  any HTTPS reverse proxy you control

## 1. Install

```bash
git clone https://github.com/lennon-li/MScopilotPorter.git
cd MScopilotPorter
python3 -m venv .venv
.venv/bin/pip install -e .       
```

## 2. Initialise

```bash
.venv/bin/mscopilotporter init
```

This creates `~/.config/mscopilotporter/config.yaml` and a random API key in
`~/.config/mscopilotporter/secrets.env` (mode 0600). Never commit either file.

## 3. Declare workspaces

Edit `~/.config/mscopilotporter/config.yaml`. Only listed directories are visible to Copilot.

```yaml
server:
  port: 8000
  allowed_hosts: [127.0.0.1, localhost, <your-tunnel-hostname>]
workspaces:
  myproject:
    path: ~/projects/myproject
    writable: false          # default false; true lets Copilot edit files
    allow_execute: false     # default false; true allows sandboxed commands and CLI workers
    allow_artifacts: false   # default false; true allows listing/reading artifacts
```

Everything is off by default. Start read-only and enable capabilities one at a time.
`doctor` reads this file, so edit it first.

## 4. Check the machine

```bash
.venv/bin/mscopilotporter doctor
```

Fix anything it reports (missing bubblewrap, wrong key-file mode, invalid config).

## 5. Start the gateway

```bash
.venv/bin/mscopilotporter serve         # foreground, 127.0.0.1:8000
```

In a second terminal:

```bash
KEY=$(.venv/bin/mscopilotporter key show)
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health                      # 401
curl -s -o /dev/null -w "%{http_code}\n" -H "X-MSCP-Key: $KEY" http://127.0.0.1:8000/health  # 200
```

Run it as a service so it survives logout:

```bash
.venv/bin/mscopilotporter service install
systemctl --user enable --now mscopilotporter
loginctl enable-linger "$USER"          # keep user services running after logout
```

## 6. Expose it over HTTPS

Copilot Studio needs a public HTTPS URL. With Dev Tunnels:

```bash
devtunnel user login -d -g              # or -d -e for Microsoft Entra ID
devtunnel create <tunnel-name> --allow-anonymous
devtunnel port create <tunnel-name> -p 8000
devtunnel host <tunnel-name>            # or: mscopilotporter tunnel (set tunnel.name in the config); prints https://<id>-8000.<region>.devtunnels.ms
```

Add that hostname to `server.allowed_hosts` and restart the gateway. The tunnel carries
traffic, but access is controlled by the API key. Keep the key secret.

Verify from outside the machine:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://<tunnel-host>/health                         # 401
curl -s -o /dev/null -w "%{http_code}\n" -H "X-MSCP-Key: $KEY" https://<tunnel-host>/health   # 200
```

## 7. Connect Copilot Studio

1. Open Copilot Studio and select your agent.
2. Go to Actions, then Add an action, then Model Context Protocol.
3. Server URL: `https://<tunnel-host>` (add `/mcp` if the connector asks for the route).
4. Authentication: API key, type Header, name `X-MSCP-Key`, value is your key.
5. Test the connection. The tools should be discovered.
6. Ask the agent: "List my workspaces." It should call `list_workspaces`.

If the Microsoft 365 Copilot desktop app reports "Connector request failed ... Not Found",
authorise the connection under Channels, then Microsoft 365 Copilot, then User connections.

## 8. Operate

| Task | Command |
|---|---|
| Rotate the key | `mscopilotporter key rotate`, then update the Copilot connection |
| View the key | `mscopilotporter key show` |
| Logs | `journalctl --user -u mscopilotporter -f` |
| Health | `mscopilotporter doctor` |

## Security notes

- The gateway binds to loopback only. Do not change that. Use a tunnel or proxy.
- Commands run in a bubblewrap sandbox with no network unless a tool call opts in.
- Writable workspaces and delegated CLI agents run with your own credentials. Grant
  them only to projects you trust Copilot to modify.
- Treat the API key like a password. Rotate it if it is ever shared or logged.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `401 Unauthorized` | Missing or wrong key. Header must be `X-MSCP-Key`; get the value with `mscopilotporter key show`. |
| `403 Host header not allowed` | Add your tunnel hostname to `server.allowed_hosts` and restart. |
| `429 Too Many Requests` | Rate limit is 120 requests per minute per client. Wait and retry. |
| `PermissionError ... allow_execute` (or `allow_artifacts`) | The workspace has not opted in. Set the flag to `true` in the config and restart. |
| `bwrap: loopback: Failed RTM_NEWADDR` or `Operation not permitted` | Unprivileged user namespaces are restricted (common on Ubuntu 24.04). Run `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`, or install an AppArmor profile for `bwrap`. `doctor` reports this. |
| `search_text` returns nothing or errors | Install ripgrep: `sudo apt install ripgrep`. |
| Tunnel URL works but Copilot Studio cannot connect | Check the tunnel is hosted (`devtunnel host`), the gateway is running, and the Server URL matches exactly. Test with the `curl` commands in step 6. |
| Copilot desktop app says "Not Found" | Authorise the connection: Channels, Microsoft 365 Copilot, User connections. |
| Only one machine can host a tunnel | Starting `devtunnel host` on a second machine disconnects the first. |

## Uninstall

```bash
systemctl --user disable --now mscopilotporter 2>/dev/null
rm -f ~/.config/systemd/user/mscopilotporter.service
rm -rf ~/.config/mscopilotporter ~/.local/state/mscopilotporter
```
