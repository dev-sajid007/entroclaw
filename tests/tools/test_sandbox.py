"""Bubblewrap sandbox for run_command. Skipped where bwrap isn't installed (e.g. inside the Docker image)."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from coding_agent.services.sandbox import SandboxError, sandbox_argv, sandbox_status
from coding_agent.tools.shell import make_shell_tools

needs_bwrap = pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap not installed")


@pytest.fixture
def fake_home(workspace_dir):
    # Inside the workspace on purpose: /tmp is a fresh private tmpfs in the sandbox, so a fake home elsewhere
    # under /tmp would be invisible anyway and prove nothing.
    home = workspace_dir / "fakehome"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("PRIVATE KEY")
    (home / ".cache").mkdir()
    (home / ".netrc").write_text("machine x password y")
    return home


@pytest.fixture
def run(settings, workspace, fake_home, monkeypatch):
    import coding_agent.tools.shell as shell

    original = shell.sandbox_argv
    monkeypatch.setattr(shell, "sandbox_argv", lambda s, ws, cwd: original(s, ws, cwd, home=fake_home))
    tools = {}

    async def run_command(command, **overrides):
        s = settings.with_overrides(**overrides) if overrides else settings
        key = tuple(sorted(overrides.items()))
        tools.setdefault(key, make_shell_tools(s, workspace)[0])
        return await tools[key].ainvoke({"command": command, "timeout": 20})

    return run_command


def test_status(settings, monkeypatch):
    assert sandbox_status(settings.with_overrides(sandbox="off")).enabled is False
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert sandbox_status(settings).enabled is False  # auto without bwrap
    with pytest.raises(SandboxError):
        sandbox_status(settings.with_overrides(sandbox="bwrap"))


@needs_bwrap
def test_argv_hides_secrets_and_keeps_workspace_writable(settings, fake_home, workspace_dir):
    argv = sandbox_argv(settings, workspace_dir, workspace_dir, home=fake_home)
    joined = " ".join(argv)
    assert f"--bind {workspace_dir} {workspace_dir}" in joined
    assert f"--tmpfs {fake_home / '.ssh'}" in joined
    assert f"--ro-bind /dev/null {fake_home / '.netrc'}" in joined
    assert "--unshare-all" in argv and "--share-net" not in argv
    assert "--share-net" in sandbox_argv(settings.with_overrides(sandbox_network=True), workspace_dir, workspace_dir, home=fake_home)


@needs_bwrap
async def test_workspace_writable_rest_read_only(run, workspace_dir):
    result = await run("echo ok > inside.txt && cat inside.txt")
    assert "exit code 0" in result and "sandboxed" in result
    assert (workspace_dir / "inside.txt").read_text() == "ok\n"
    escape = Path.home() / f".coding-agent-sandbox-escape-{workspace_dir.name}"
    result = await run(f"touch {escape}")
    assert "Read-only file system" in result and "[sandbox]" in result
    assert not escape.exists()
    result = await run("echo tmp > /tmp/scratch && cat /tmp/scratch")
    assert "tmp" in result  # private /tmp works


@needs_bwrap
async def test_secrets_are_hidden(run, fake_home):
    result = await run(f"ls -A {fake_home}/.ssh | wc -l; cat {fake_home}/.ssh/id_ed25519 {fake_home}/.netrc")
    assert result.split("]\n", 1)[1].split()[0] == "0"  # .ssh is empty
    assert "PRIVATE KEY" not in result and "password y" not in result
    assert (fake_home / ".ssh" / "id_ed25519").read_text() == "PRIVATE KEY"  # untouched on the host


@needs_bwrap
async def test_no_network_by_default(run):
    probe = f"{sys.executable} -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3); print('NET' + '-OK')\""
    result = await run(probe)
    assert "NET-OK" not in result
    assert "exit code 0" not in result


@needs_bwrap
async def test_timeout_still_kills(settings, workspace):
    tool = make_shell_tools(settings, workspace)[0]
    result = await tool.ainvoke({"command": "sleep 30", "timeout": 1})
    assert "timed out after 1s" in result


@needs_bwrap
async def test_python_tests_run_inside_sandbox(run, workspace_dir):
    (workspace_dir / "test_ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
    result = await run(f"{sys.executable} -m pytest -q -p no:cacheprovider test_ok.py")
    assert "1 passed" in result, result


async def test_sandbox_off_runs_unsandboxed(settings, workspace):
    tool = make_shell_tools(settings.with_overrides(sandbox="off"), workspace)[0]
    result = await tool.ainvoke({"command": "echo hi"})
    assert "hi" in result and "sandboxed" not in result


@needs_bwrap
def test_agent_env_file_hidden(settings, workspace_dir, tmp_path, monkeypatch):
    env_file = tmp_path / "agent.env"
    env_file.write_text("OPENAI_API_KEY=sk-secret")
    monkeypatch.setenv("AGENT_ENV_FILE", str(env_file))
    argv = sandbox_argv(settings, workspace_dir, workspace_dir)
    out = subprocess.run([*argv, "bash", "-c", f"cat {env_file} 2>/dev/null | wc -c"], capture_output=True, text=True, timeout=20)
    assert out.stdout.strip() == "0"
    assert Path(env_file).read_text() == "OPENAI_API_KEY=sk-secret"
