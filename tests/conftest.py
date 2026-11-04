"""Hermetic test environment: temp config/state dirs and a fixture workspace.

Environment is set at import time because several modules resolve paths on import.
"""
import os
import subprocess
import tempfile
from pathlib import Path

_root = Path(tempfile.mkdtemp(prefix="mscp-tests-"))
_ws = _root / "poc"
os.environ["MSCP_CONFIG_DIR"] = str(_root / "config")
os.environ["MSCP_STATE_DIR"] = str(_root / "state")
os.environ["MSCP_CONFIG"] = str(_root / "config" / "config.yaml")
os.environ["MSCP_ALLOWED_HOSTS"] = "*.devtunnels.ms,*.devtunnels.ms:*"
os.environ.pop("MSCP_KEY", None)
os.environ.pop("M3_MCP_KEY", None)

(_root / "config").mkdir(parents=True)
(_ws / "R").mkdir(parents=True)
(_ws / "python").mkdir()
(_ws / "tests").mkdir()
(_ws / "artifacts").mkdir()
(_ws / "README.md").write_text("# MScopilotPorter POC\n\nFixture workspace.\n")
(_ws / "R" / "stats.R").write_text("compute_mean <- function(x) sum(x) / length(x)\n")
(_ws / "R" / "demo_plot.R").write_text(
    'png("artifacts/demo.png")\nplot(1:10)\ninvisible(dev.off())\n'
)
(_ws / "python" / "stats.py").write_text("def compute_mean(xs):\n    return sum(xs) / len(xs)\n")
(_ws / "tests" / "test_r.R").write_text('stop("DEFECT DETECTED")\n')
(_ws / "tests" / "test_python.py").write_text(
    "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n\n"
    "if __name__ == '__main__':\n    unittest.main()\n"
)
(_root / "config" / "config.yaml").write_text(
    "workspaces:\n"
    "  poc:\n"
    f"    path: {_ws}\n"
    "    writable: true\n"
    "    allow_execute: true\n"
    "    allow_artifacts: true\n"
    "    description: Fixture workspace\n"
    "agents:\n"
    "  alpha: {cli: codex}\n"
    "  beta: {cli: codex, profile_path: ~/.codex-beta}\n"
    "  gamma: {cli: copilot}\n"
    "  delta: {cli: opencode}\n"
    "  claude: {cli: claude}\n"
    "  agy: {cli: agy}\n"
)
_git = ["git", "-C", str(_ws)]
subprocess.run(_git + ["init", "-q", "-b", "main"], check=True)
subprocess.run(_git + ["add", "-A"], check=True)
subprocess.run(
    _git + ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "initial mixed R/Python"],
    check=True,
)
