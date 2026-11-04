import os
from unittest.mock import patch

from mscopilotporter.runner import sandbox


def test_stale_identity_repaired_on_every_launch(tmp_path):
    passwd = tmp_path / "passwd"
    group = tmp_path / "group"
    passwd.write_text("stale")
    group.write_text("stale")
    with patch.object(sandbox, "SYNTHETIC_ETC_DIR", str(tmp_path)), patch.object(
        sandbox, "PASSWD_FILE", str(passwd)
    ), patch.object(sandbox, "GROUP_FILE", str(group)), patch("os.getuid", return_value=2056259175), patch(
        "os.getgid", return_value=12345
    ):
        sandbox.build_bwrap_args(str(tmp_path))
    assert "sandbox:x:2056259175:12345:" in passwd.read_text()
    assert "sandbox:x:12345:" in group.read_text()
    assert passwd.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".identity-*"))
