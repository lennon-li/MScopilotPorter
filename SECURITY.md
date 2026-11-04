# Security model

MScopilotPorter gives a remote AI client controlled access to a local machine. Treat it
as remote code execution that you have chosen to allow, and scope it accordingly.

## Boundaries

- **Network:** binds to `127.0.0.1` only. Public access is your tunnel or reverse proxy.
- **Authentication:** every request needs the API key (`X-MSCP-Key`). Comparison is
  constant-time. Requests are rate limited and size limited (10 MB).
- **Host header allow-list:** only loopback plus hostnames in `server.allowed_hosts`.
- **Workspaces:** only directories listed in the config are reachable. Paths are resolved
  and checked against traversal and symlink escape; `.git`, `.ssh` and similar internal
  paths are refused.
- **Sandbox:** commands run in bubblewrap with a synthetic environment, a synthetic
  `/etc/passwd`, no network unless a call opts in, and secrets stripped from the
  environment. Execution also needs `allow_execute: true` on the workspace.
- **Writes:** workspaces are read-only unless `writable: true`.

## What the sandbox does not cover

- Delegated CLI workers (`dispatch_agent`) run with your own CLI credentials and profile
  directories. They are bounded by their CLI's permission flags, not by the kernel sandbox.
- `git_push`, `git_pull` and `git_fetch` use your git credentials. Do not enable
  `writable` or `allow_execute` on repositories you would not let the client push from.
- Network opt-in (`network_access=true`) uses a host allow-list for supported workers, and
  passes only sanitized proxy variables; it is not a firewall.

## Operating guidance

- Keep `secrets.env` at mode 0600. Never commit it. `mscopilotporter doctor` checks the mode.
- Rotate the key (`mscopilotporter key rotate`) if it is ever logged, pasted or shared.
- Prefer read-only workspaces. Enable writes and execution per project, only when needed.
- Run on a machine and account that hold no more credentials than the task requires.
- Treat file contents and command output as untrusted; they can contain prompt injection.

## Reporting a vulnerability

Open a private security advisory on the GitHub repository. Please do not file public issues
for undisclosed vulnerabilities.
