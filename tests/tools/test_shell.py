import os

import pytest

from coding_agent.tools.shell import make_shell_tools
from coding_agent.utils.security import SecurityError

# These tests drive POSIX tools (seq, sleep, env, bash syntax); Windows has its own tests in test_platform.py.
pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX shell tools")


@pytest.fixture
def run_command(settings, workspace):
    return make_shell_tools(settings, workspace)[0]


async def test_reports_output_and_exit_code(run_command):
    result = await run_command.ainvoke({"command": "echo hi && exit 3"})
    assert "hi" in result
    assert "exit code 3" in result


async def test_runs_in_workspace(run_command, workspace_dir):
    result = await run_command.ainvoke({"command": "pwd"})
    assert str(workspace_dir) in result


async def test_timeout_kills_process(run_command):
    result = await run_command.ainvoke({"command": "sleep 30", "timeout": 1})
    assert "timed out after 1s" in result


async def test_timeout_kills_child_processes(run_command, workspace_dir):
    await run_command.ainvoke({"command": "(sleep 5; touch late.txt) & sleep 30", "timeout": 1})
    import asyncio

    await asyncio.sleep(5.5)
    assert not (workspace_dir / "late.txt").exists()


async def test_output_is_truncated(settings, workspace):
    tool = make_shell_tools(settings.with_overrides(max_tool_output=500), workspace)[0]
    result = await tool.ainvoke({"command": "seq 1 10000"})
    assert "characters truncated" in result
    assert len(result) < 1000


async def test_secrets_are_removed_from_environment(run_command, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("HARMLESS_SETTING", "visible")
    result = await run_command.ainvoke({"command": "env"})
    assert "sk-should-not-leak" not in result
    assert "HARMLESS_SETTING=visible" in result
    assert os.environ["OPENAI_API_KEY"] == "sk-should-not-leak"


async def test_cwd_outside_workspace_is_blocked(run_command):
    with pytest.raises(SecurityError):
        await run_command.ainvoke({"command": "ls", "cwd": "/"})
