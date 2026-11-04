"""Invocation-only OpenCode permissions; never rewrite the user's profile."""

import json
import re
import subprocess


DENIED_COMMANDS = (
    "git commit", "git push", "git reset", "git rebase", "git checkout",
    "git switch", "git branch", "git tag", "git remote", "git clean",
    "git stash", "git worktree", "git cherry-pick", "git revert", "git merge",
    "git pull", "sudo", "su", "systemctl", "ssh", "scp", "gh pr",
    "gh release", "gh auth", "npm publish", "twine upload", "docker push",
    "rm -rf", "rm -fr", "apt", "apt-get", "dnf", "snap",
)


def dispatch_command(executable, packet, model):
    """Return argv and a profile for supported V1/V2 schemas; fail closed."""
    try:
        version = subprocess.run(
            [executable, "--version"], capture_output=True, text=True,
            timeout=5, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Cannot verify OpenCode permission schema") from exc
    match = re.fullmatch(r"(?:opencode\s+)?v?([12])\.\d+\.\d+(?:[-+][\w.-]+)?", version)
    if not match:
        raise RuntimeError("Unsupported OpenCode permission schema version")
    major = int(match.group(1))
    if major == 1:
        permissions = {
            "edit": "allow", "external_directory": "deny",
            "bash": {"*": "allow", **{command + "*": "deny" for command in DENIED_COMMANDS}},
        }
        profile = {"agent": {"dispatch": {
            "mode": "primary", "description": "Bounded workspace worker",
            "permission": permissions,
        }}}
    else:
        permissions = [
            {"action": "edit", "resource": "*", "effect": "allow"},
            {"action": "external_directory", "resource": "*", "effect": "deny"},
            {"action": "shell", "resource": "*", "effect": "allow"},
            *({"action": "shell", "resource": command + "*", "effect": "deny"}
              for command in DENIED_COMMANDS),
        ]
        profile = {"agents": {"dispatch": {"mode": "primary", "permissions": permissions}}}
    cmd = [executable, "run", "--agent", "dispatch"]
    if major == 2:
        cmd.append("--standalone")
    if model and model != "unknown":
        cmd.extend(["-m", model])
    cmd.append(packet)
    return cmd, json.dumps(profile)
