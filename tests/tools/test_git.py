import pytest

from coding_agent.services.workspace import Workspace
from coding_agent.tools.filesystem import ToolError
from coding_agent.tools.git import make_git_tools


@pytest.fixture
def git(settings, git_workspace):
    return {t.name: t for t in make_git_tools(settings, Workspace(git_workspace))}


async def test_status_and_diff(git, git_workspace):
    (git_workspace / "hello.py").write_text("print('changed')\n")
    status = await git["git_status"].ainvoke({})
    assert "M hello.py" in status
    diff = await git["git_diff"].ainvoke({})
    assert "+print('changed')" in diff


async def test_add_commit_log(git, git_workspace):
    (git_workspace / "new.py").write_text("x = 1\n")
    await git["git_add"].ainvoke({"paths": ["new.py"]})
    staged = await git["git_diff"].ainvoke({"staged": True})
    assert "+x = 1" in staged
    await git["git_commit"].ainvoke({"message": "Add new.py"})
    log = await git["git_log"].ainvoke({"max_count": 5})
    assert "Add new.py" in log
    show = await git["git_show"].ainvoke({"revision": "HEAD"})
    assert "new.py" in show


async def test_branch_create_and_list(git):
    await git["git_branch"].ainvoke({"create": "feature/x"})
    branches = await git["git_branch"].ainvoke({})
    assert "* feature/x" in branches


async def test_option_injection_is_rejected(git):
    with pytest.raises(ToolError):
        await git["git_show"].ainvoke({"revision": "--output=/tmp/x"})
    with pytest.raises(ToolError):
        await git["git_branch"].ainvoke({"create": "-D"})


async def test_git_errors_are_reported(git):
    with pytest.raises(ToolError, match="failed"):
        await git["git_show"].ainvoke({"revision": "does-not-exist"})
