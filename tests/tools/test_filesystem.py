import pytest

from coding_agent.tools.filesystem import ToolError, make_filesystem_tools, preview_file_change
from coding_agent.utils.security import SecurityError


@pytest.fixture
def fs(settings, workspace):
    return {t.name: t for t in make_filesystem_tools(settings, workspace)}


def test_read_file(fs, workspace_dir):
    result = fs["read_file"].invoke({"path": "hello.py"})
    assert "print('hello')" in result
    assert result.startswith("1  ")


def test_read_file_line_range(fs, workspace_dir):
    (workspace_dir / "lines.txt").write_text("\n".join(f"line {i}" for i in range(1, 11)))
    result = fs["read_file"].invoke({"path": "lines.txt", "start_line": 3, "end_line": 4})
    assert "line 3" in result and "line 4" in result
    assert "line 5" not in result and "line 2" not in result


def test_read_file_outside_workspace_is_blocked(fs):
    with pytest.raises(SecurityError):
        fs["read_file"].invoke({"path": "/etc/passwd"})
    with pytest.raises(SecurityError):
        fs["read_file"].invoke({"path": "../../../etc/passwd"})


def test_read_file_symlink_escape_is_blocked(fs, workspace_dir, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (workspace_dir / "link.txt").symlink_to(outside)
    with pytest.raises(SecurityError):
        fs["read_file"].invoke({"path": "link.txt"})


def test_read_secret_file_is_blocked(fs):
    with pytest.raises(SecurityError, match="secrets file"):
        fs["read_file"].invoke({"path": ".env"})


def test_read_binary_and_large_files(fs, settings, workspace_dir):
    (workspace_dir / "blob.bin").write_bytes(b"\x00\x01\x02")
    with pytest.raises(ToolError, match="binary"):
        fs["read_file"].invoke({"path": "blob.bin"})
    (workspace_dir / "big.txt").write_text("x" * (settings.max_file_bytes + 1))
    with pytest.raises(ToolError, match="limit"):
        fs["read_file"].invoke({"path": "big.txt"})


def test_list_files_skips_ignored_dirs(fs, workspace_dir):
    (workspace_dir / "node_modules" / "pkg").mkdir(parents=True)
    (workspace_dir / "node_modules" / "pkg" / "index.js").write_text("")
    result = fs["list_files"].invoke({"path": "."})
    assert "hello.py" in result
    assert "src/" in result and "app.py" in result
    assert "node_modules" not in result


def test_search_files(fs):
    result = fs["search_files"].invoke({"pattern": r"def login"})
    assert "src/app.py:1:" in result
    assert "SECRET_TOKEN" not in fs["search_files"].invoke({"pattern": "SECRET"})
    assert fs["search_files"].invoke({"pattern": "nothing-matches-this"}) == "No matches."


def test_write_file_creates_parents(fs, workspace_dir):
    result = fs["write_file"].invoke({"path": "pkg/new.py", "content": "x = 1\n"})
    assert "Created pkg/new.py" in result
    assert (workspace_dir / "pkg" / "new.py").read_text() == "x = 1\n"


def test_write_file_outside_workspace_is_blocked(fs, tmp_path):
    with pytest.raises(SecurityError):
        fs["write_file"].invoke({"path": str(tmp_path / "escape.py"), "content": ""})


def test_edit_file(fs, workspace_dir):
    fs["edit_file"].invoke({"path": "src/app.py", "old_string": "is not None", "new_string": "is not None and user.active"})
    assert "user.active" in (workspace_dir / "src" / "app.py").read_text()


def test_edit_file_requires_unique_match(fs, workspace_dir):
    (workspace_dir / "dup.py").write_text("a = 1\na = 1\n")
    with pytest.raises(ToolError, match="2 times"):
        fs["edit_file"].invoke({"path": "dup.py", "old_string": "a = 1", "new_string": "a = 2"})
    fs["edit_file"].invoke({"path": "dup.py", "old_string": "a = 1", "new_string": "a = 2", "replace_all": True})
    assert (workspace_dir / "dup.py").read_text() == "a = 2\na = 2\n"


def test_edit_file_missing_snippet(fs):
    with pytest.raises(ToolError, match="not found"):
        fs["edit_file"].invoke({"path": "hello.py", "old_string": "nope", "new_string": "x"})


def test_preview_diff(settings, workspace):
    diff = preview_file_change(settings, workspace, "edit_file", {"path": "hello.py", "old_string": "hello", "new_string": "bye"})
    assert "-print('hello')" in diff and "+print('bye')" in diff
    new = preview_file_change(settings, workspace, "write_file", {"path": "new.py", "content": "x = 1\n"})
    assert "+x = 1" in new
