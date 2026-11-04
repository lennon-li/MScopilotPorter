import os
import pytest
from mscopilotporter.tools.workspace import get_workspace, validate_workspace_path
from mscopilotporter.tools.files import read_file
from mscopilotporter.auth import verify_api_key, is_host_allowed, API_KEY
from mscopilotporter.runner.sandbox import run_in_sandbox
from mscopilotporter.agents.registry import dispatch_agent

WORKSPACE_ID = "poc"
HOME_SSH = os.path.expanduser("~/.ssh/id_rsa")

def test_auth_rejection():
    assert not verify_api_key("")
    assert not verify_api_key("wrong_key_12345")
    assert verify_api_key(API_KEY)

def test_host_header_validation():
    assert is_host_allowed("localhost")
    assert is_host_allowed("127.0.0.1:8000")
    assert is_host_allowed("abc-xyz.devtunnels.ms")
    assert not is_host_allowed("attacker.com")
    assert not is_host_allowed("malicious-site.org:8000")

def test_path_traversal_blocked():
    with pytest.raises(ValueError, match="Path traversal detected|Path escape attempt blocked"):
        validate_workspace_path(WORKSPACE_ID, "../../.ssh/id_rsa")

    with pytest.raises(ValueError, match="Absolute paths not permitted|Path escape attempt blocked|Invalid sub_cwd"):
        validate_workspace_path(WORKSPACE_ID, "/mnt/c/Windows")

def test_sensitive_files_blocked():
    with pytest.raises(ValueError, match="sensitive or internal"):
        validate_workspace_path(WORKSPACE_ID, ".git/config")

    with pytest.raises(ValueError, match="sensitive or internal"):
        validate_workspace_path(WORKSPACE_ID, ".ssh/id_rsa")

def test_unknown_workspace_blocked():
    with pytest.raises(KeyError, match="Unknown workspace_id"):
        get_workspace("nonexistent-workspace-xyz")

def test_unknown_agent_blocked():
    with pytest.raises(ValueError, match="Unknown agent"):
        dispatch_agent("evil-agent-binary", "do harm", WORKSPACE_ID)

def test_sandbox_python_ssh_blocked():
    ws = get_workspace(WORKSPACE_ID)
    res = run_in_sandbox(
        ws["path"],
        ["python3", "-c", "import os; print(os.path.exists('" + HOME_SSH + "'))"]
    )
    assert res["exit_code"] == 0
    assert res["stdout"].strip() == "False"

    # Attempt direct open
    res_open = run_in_sandbox(
        ws["path"],
        ["python3", "-c", "open('" + HOME_SSH + "')"]
    )
    assert res_open["exit_code"] != 0
    assert "No such file or directory" in res_open["stderr"]

def test_sandbox_r_ssh_blocked():
    ws = get_workspace(WORKSPACE_ID)
    res = run_in_sandbox(
        ws["path"],
        ["Rscript", "-e", "cat(file.exists('" + HOME_SSH + "'))"]
    )
    assert res["exit_code"] == 0
    assert "FALSE" in res["stdout"]

def test_sandbox_mnt_c_blocked():
    ws = get_workspace(WORKSPACE_ID)
    res_py = run_in_sandbox(
        ws["path"],
        ["python3", "-c", "import os; print(os.path.exists('/mnt/c'))"]
    )
    assert res_py["exit_code"] == 0
    assert res_py["stdout"].strip() == "False"

    res_r = run_in_sandbox(
        ws["path"],
        ["Rscript", "-e", "cat(file.exists('/mnt/c'))"]
    )
    assert res_r["exit_code"] == 0
    assert "FALSE" in res_r["stdout"]

def test_sandbox_docker_socket_blocked():
    ws = get_workspace(WORKSPACE_ID)
    res = run_in_sandbox(
        ws["path"],
        ["python3", "-c", "import os; print(os.path.exists('/var/run/docker.sock'))"]
    )
    assert res["exit_code"] == 0
    assert res["stdout"].strip() == "False"

def test_sandbox_network_blocked():
    ws = get_workspace(WORKSPACE_ID)
    res = run_in_sandbox(
        ws["path"],
        ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://1.1.1.1', timeout=1)"]
    )
    assert res["exit_code"] != 0
    assert "Network is unreachable" in res["stderr"] or "URLError" in res["stderr"]

def test_sandbox_background_process_does_not_block_exit():
    # gh/git can fork daemons (dbus, credential cache); they must not hold the run open.
    ws = get_workspace(WORKSPACE_ID)
    res = run_in_sandbox(
        ws["path"],
        ["bash", "-c", "setsid sleep 60 >/dev/null 2>&1 & echo started; exit 3"],
        timeout_seconds=10,
    )
    assert res["exit_code"] == 3
    assert "started" in res["stdout"]
    assert res["duration"] < 5

def test_sandbox_environment_clean():
    ws = get_workspace(WORKSPACE_ID)
    res = run_in_sandbox(
        ws["path"],
        ["python3", "-c", "import os; print(sorted(list(os.environ.keys())))"]
    )
    assert res["exit_code"] == 0
    env_keys = eval(res["stdout"].strip())
    # Sensitive tokens and user env should not exist
    for forbidden in ["SSH_AUTH_SOCK", "AWS_ACCESS_KEY_ID", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "MSCP_KEY"]:
        assert forbidden not in env_keys
