import os
import stat

import pytest

from mscopilotporter import cli, paths


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MSCP_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("MSCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("MSCP_CONFIG", raising=False)
    return tmp_path


def test_paths_follow_xdg_when_no_override(monkeypatch, tmp_path):
    monkeypatch.delenv("MSCP_CONFIG_DIR", raising=False)
    monkeypatch.delenv("MSCP_STATE_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    assert paths.config_dir() == tmp_path / "c" / "mscopilotporter"
    assert paths.state_dir() == tmp_path / "s" / "mscopilotporter"


def test_init_creates_config_and_private_key(home, capsys):
    assert cli.main(["init"]) == 0
    assert (home / "cfg" / "config.yaml").exists()
    key_file = home / "cfg" / "secrets.env"
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert key_file.read_text().startswith("MSCP_KEY=")


def test_init_is_idempotent_and_keeps_key(home):
    cli.main(["init"])
    before = (home / "cfg" / "secrets.env").read_text()
    cli.main(["init"])
    assert (home / "cfg" / "secrets.env").read_text() == before


def test_key_show_and_rotate(home, capsys):
    cli.main(["init"])
    capsys.readouterr()
    cli.main(["key", "show"])
    first = capsys.readouterr().out.strip()
    assert len(first) >= 32
    cli.main(["key", "rotate"])
    capsys.readouterr()
    cli.main(["key", "show"])
    assert capsys.readouterr().out.strip() != first


def test_key_show_without_init_fails(home):
    assert cli.main(["key", "show"]) == 1


def test_doctor_fails_without_config(home, capsys):
    assert cli.main(["doctor"]) == 1
    assert "config file exists" in capsys.readouterr().out


def test_doctor_passes_with_valid_setup(home, capsys):
    cli.main(["init"])
    ws = home / "proj"
    ws.mkdir()
    (home / "cfg" / "config.yaml").write_text(
        f"server:\n  port: 8000\nworkspaces:\n  p:\n    path: {ws}\n"
    )
    assert cli.main(["doctor"]) == 0


def test_service_print_uses_current_interpreter(capsys):
    assert cli.main(["service", "--print"]) == 0
    out = capsys.readouterr().out
    assert "-m mscopilotporter serve" in out
    assert "/home/" not in out.replace(os.path.expanduser("~"), "")


def test_tunnel_requires_name(home, capsys):
    assert cli.main(["tunnel"]) == 1
