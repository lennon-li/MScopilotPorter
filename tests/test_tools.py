import os
import shutil
import time
import pytest
from conftest import needs_bwrap
from mscopilotporter.tools.workspace import get_capabilities, list_workspaces, workspace_info
from mscopilotporter.tools.files import (
    list_files, search_text, read_file, write_file, apply_patch, mkdir, move_path, trash_path
)
from mscopilotporter.tools.exec import (
    exec_run, exec_start, job_status, job_output, job_result, job_cancel
)
from mscopilotporter.tools.git import (
    git_status, git_diff, git_log, git_show, git_fetch, git_pull, git_push, git_commit, git_add, git_branch, git_checkout
)
from mscopilotporter.tools.artifacts import list_artifacts, read_artifact
from mscopilotporter.tools.agents import list_agents, dispatch_agent

WORKSPACE_ID = "poc"
needs_r = pytest.mark.skipif(shutil.which("Rscript") is None, reason="Rscript not installed")

def test_capabilities_and_workspace_info():
    caps = get_capabilities()
    assert "supported_tools" in caps
    assert "bubblewrap_0.9.0" in caps["sandbox"]

    workspaces = list_workspaces()
    assert any(w["workspace_id"] == WORKSPACE_ID for w in workspaces)

    info = workspace_info(WORKSPACE_ID)
    assert info["workspace_id"] == WORKSPACE_ID
    assert "R" in info["languages"]
    assert "Python" in info["languages"]

@needs_bwrap
def test_file_operations():
    files = list_files(WORKSPACE_ID)
    assert "README.md" in files
    assert "R/stats.R" in files
    assert "python/stats.py" in files

    # Search
    hits = search_text(WORKSPACE_ID, "compute_mean")
    assert len(hits) >= 2

    # Ranged read
    read_res = read_file(WORKSPACE_ID, "README.md", start_line=1, end_line=3)
    assert "MScopilotPorter POC" in read_res["content"]
    assert "sha256" in read_res

    # Write file
    test_rel = "tests/scratch_test.txt"
    w_res = write_file(WORKSPACE_ID, test_rel, "Hello Sandbox")
    assert w_res["status"] == "written"

    # Read back
    r_res = read_file(WORKSPACE_ID, test_rel)
    assert r_res["content"] == "Hello Sandbox"

    # Trash file
    t_res = trash_path(WORKSPACE_ID, test_rel)
    assert t_res["status"] == "trashed"

@needs_r
@needs_bwrap
def test_r_execution_and_tests():
    # Calculation 100/pi in R
    r_calc = exec_run(WORKSPACE_ID, ["Rscript", "-e", "cat(100/pi)"])
    assert r_calc["exit_code"] == 0
    assert "31.83" in r_calc["stdout"]

    # Run R tests -> intentional defect should trigger failure
    r_test = exec_run(WORKSPACE_ID, ["Rscript", "tests/test_r.R"])
    assert r_test["exit_code"] != 0
    assert "DEFECT DETECTED" in r_test["stderr"]

@needs_bwrap
def test_python_execution_and_tests():
    # Calculation in Python
    py_calc = exec_run(WORKSPACE_ID, ["python3", "-c", "import math; print(100/math.pi)"])
    assert py_calc["exit_code"] == 0
    assert "31.83" in py_calc["stdout"]

    # Run Python unittest suite
    py_test = exec_run(WORKSPACE_ID, ["python3", "-m", "unittest", "tests/test_python.py"])
    assert py_test["exit_code"] == 0
    assert "OK" in py_test["stderr"]

@needs_bwrap
def test_bash_sandbox_execution():
    bash_res = exec_run(WORKSPACE_ID, ["bash", "-lc", "echo 'sandbox bash' && whoami"])
    assert bash_res["exit_code"] == 0
    assert "sandbox bash" in bash_res["stdout"]

def test_git_inspection():
    st = git_status(WORKSPACE_ID)
    assert "master" in st or "main" in st
    df = git_diff(WORKSPACE_ID)
    assert isinstance(df, str)
    lg = git_log(WORKSPACE_ID, limit=5)
    assert "initial mixed R/Python" in lg

def test_git_operations():
    branches = git_branch(WORKSPACE_ID)
    assert "master" in branches or "main" in branches

    show_out = git_show(WORKSPACE_ID, "HEAD")
    assert "commit" in show_out

    # Test error handling on invalid branch/remote
    with pytest.raises(ValueError):
        git_show(WORKSPACE_ID, "--invalid-flag")
    with pytest.raises(ValueError):
        git_pull(WORKSPACE_ID, remote="--upload-pack=evil")
    with pytest.raises(ValueError):
        git_push(WORKSPACE_ID, remote="-u")
    with pytest.raises(ValueError):
        git_add(WORKSPACE_ID, paths=["--all"])
    with pytest.raises(ValueError):
        git_checkout(WORKSPACE_ID, branch="-b")

@needs_r
@needs_bwrap
def test_r_plotting_and_artifact():
    # Run plotting script
    plot_run = exec_run(WORKSPACE_ID, ["Rscript", "R/demo_plot.R"])
    assert plot_run["exit_code"] == 0

    # Inspect artifact
    artifacts = list_artifacts(WORKSPACE_ID, "artifacts")
    assert any("demo.png" in a["path"] for a in artifacts)

    art = read_artifact(WORKSPACE_ID, "artifacts/demo.png")
    assert art["type"] == "image"
    assert art["mime_type"] == "image/png"
    assert len(art["base64_data"]) > 100

@needs_bwrap
def test_async_job_lifecycle():
    # Start long-running job inside sandbox (sleep 1 and echo)
    start_res = exec_start(WORKSPACE_ID, ["bash", "-c", "echo 'phase 1' && sleep 1 && echo 'phase 2'"])
    job_id = start_res["job_id"]

    # Check status
    st1 = job_status(job_id)
    assert st1["job_id"] == job_id

    # Wait for completion
    time.sleep(1.5)
    res = job_result(job_id)
    assert res["status"] == "completed"
    assert "phase 1" in res["stdout"]
    assert "phase 2" in res["stdout"]

def test_async_job_cancel():
    # Start long sleep job
    start_res = exec_start(WORKSPACE_ID, ["sleep", "30"])
    job_id = start_res["job_id"]

    cancel_res = job_cancel(job_id)
    assert cancel_res["status"] == "cancelled"

    st = job_status(job_id)
    assert st["status"] == "cancelled"

def test_agent_broker_listing():
    agents = list_agents()
    agent_names = [a["agent"] for a in agents]
    assert "alpha" in agent_names
    assert "beta" in agent_names
    assert "gamma" in agent_names
    assert "delta" in agent_names
    assert "claude" in agent_names
    assert "agy" in agent_names

    # Check distinction between configured_model and actual_model_used
    for ag in agents:
        assert "configured_model" in ag
        assert "actual_model_used" in ag
        assert "cli_version" in ag
        if ag.get("available"):
            assert ag["cli_version"] != "unknown"
        assert ag["actual_model_used"] == "unknown (resolved at runtime per dispatch)"

@pytest.mark.skipif(
    shutil.which("codex") is None or not os.path.isdir(os.path.expanduser("~/.codex")),
    reason="codex CLI and profile not installed",
)
def test_dispatch_agent_telemetry():
    res = dispatch_agent(
        agent="alpha",
        task="echo test",
        workspace_id=WORKSPACE_ID,
        purpose="unit test"
    )
    # Required fields per specification:
    # worker CLI, provider, requested model, actual/resolved model, reasoning level, CLI version, job ID, exit status, duration
    assert "worker_cli" in res and res["worker_cli"] == "codex"
    assert "provider" in res and res["provider"] == "OpenAI"
    assert "requested_model" in res and res["requested_model"]
    assert "actual_model" in res and res["actual_model"] == "unknown"
    assert "reasoning_level" in res and res["reasoning_level"]
    assert "cli_version" in res and res["cli_version"] != "unknown"
    assert "job_id" in res and res["job_id"].startswith("job_")
    assert "exit_status" in res and res["exit_status"] is None
    assert "duration" in res and res["duration"] == 0.0

    # Cancel the started job cleanly
    job_cancel(res["job_id"])


def test_exec_and_artifacts_are_denied_unless_workspace_opts_in(tmp_path, monkeypatch):
    import yaml
    from mscopilotporter.tools.exec import exec_run, exec_start
    from mscopilotporter.tools.artifacts import list_artifacts, read_artifact
    from mscopilotporter.tools.files import write_file

    cfg = tmp_path / "config.yaml"
    (tmp_path / "locked").mkdir()
    cfg.write_text(yaml.safe_dump({"workspaces": {"locked": {"path": str(tmp_path / "locked")}}}))
    monkeypatch.setattr("mscopilotporter.tools.workspace.CONFIG_PATH", str(cfg))
    with pytest.raises(PermissionError, match="allow_execute"):
        exec_run("locked", ["true"])
    with pytest.raises(PermissionError, match="allow_execute"):
        exec_start("locked", ["true"])
    with pytest.raises(PermissionError, match="allow_artifacts"):
        list_artifacts("locked")
    with pytest.raises(PermissionError, match="allow_artifacts"):
        read_artifact("locked", "artifacts/x.txt")
    # workspaces are read-only by default
    with pytest.raises((PermissionError, ValueError)):
        write_file("locked", "a.txt", "x")
