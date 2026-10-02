import json
import logging
from pathlib import Path

import pytest

from coding_agent.utils.logging import JsonFormatter
from coding_agent.utils.security import SecurityError, filtered_env, is_secret_path, resolve_in_workspace, truncate


def test_workspace_cannot_access_etc_passwd(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "allowed.py").write_text("")
    assert resolve_in_workspace(workspace, "allowed.py") == workspace / "allowed.py"
    for path in ["/etc/passwd", "../outside", "sub/../../outside", "~/.ssh/id_rsa"]:
        with pytest.raises(SecurityError):
            resolve_in_workspace(workspace, path)


def test_prefix_sibling_is_not_inside(tmp_path):
    (tmp_path / "work").mkdir()
    (tmp_path / "workspace-evil").mkdir()
    with pytest.raises(SecurityError):
        resolve_in_workspace(tmp_path / "work", "../workspace-evil/x")


@pytest.mark.parametrize("name", [".env", ".env.local", "id_rsa", "server.pem", "credentials.json"])
def test_secret_paths(name):
    assert is_secret_path(Path(name))


@pytest.mark.parametrize("name", [".env.example", "env.py", "keys.py", "README.md"])
def test_non_secret_paths(name):
    assert not is_secret_path(Path(name))


def test_truncate_keeps_head_and_tail():
    text = "A" * 100 + "B" * 100
    out = truncate(text, 40)
    assert out.startswith("A") and out.endswith("B") and "truncated" in out
    assert truncate("short", 40) == "short"


def test_filtered_env():
    env = filtered_env({"PATH": "/bin", "GITHUB_TOKEN": "x", "AWS_SECRET_ACCESS_KEY": "y", "DB_PASSWORD": "z", "EDITOR": "vim"})
    assert env == {"PATH": "/bin", "EDITOR": "vim"}


def test_logs_redact_secrets():
    record = logging.LogRecord("coding_agent.test", logging.INFO, __file__, 1, "key sk-abcdefghijklmnop", None, None)
    record.tool_arguments = {"token": "ghp_" + "a" * 36}
    payload = json.loads(JsonFormatter().format(record))
    assert "sk-abcdefghijklmnop" not in payload["message"]
    assert "[REDACTED]" in payload["tool_arguments"]["token"]
