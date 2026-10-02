"""Platform-specific behaviour (shell choice, process kill, config/state paths), mocked so it runs anywhere."""

import asyncio
import subprocess
from pathlib import Path

import pytest

import coding_agent.config.settings as settings_module
import coding_agent.services.shell_env as shell_env
from coding_agent.services.shell_env import Shell, resolve_shell


@pytest.fixture(autouse=True)
def clear_cache():
    resolve_shell.cache_clear()
    yield
    resolve_shell.cache_clear()


def test_posix_prefers_bash(monkeypatch):
    monkeypatch.setattr(shell_env.shutil, "which", lambda name: {"bash": "/usr/bin/bash", "sh": "/bin/sh"}.get(name))
    assert resolve_shell("linux") == Shell("bash", ("/usr/bin/bash", "-c"))
    resolve_shell.cache_clear()
    monkeypatch.setattr(shell_env.shutil, "which", lambda name: "/bin/sh" if name == "sh" else None)
    assert resolve_shell("linux").name == "sh"


def test_windows_uses_git_bash_not_wsl(monkeypatch, tmp_path):
    git_root = tmp_path / "Git"
    (git_root / "bin").mkdir(parents=True)
    (git_root / "bin" / "bash.exe").write_text("")
    exec_path = git_root / "mingw64" / "libexec" / "git-core"
    exec_path.mkdir(parents=True)
    monkeypatch.setattr(
        shell_env.shutil, "which", lambda name: str(git_root / "cmd" / "git.exe") if name == "git" else "C:/Windows/System32/bash.exe"
    )
    monkeypatch.setattr(shell_env.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=f"{exec_path}\n", stderr=""))
    shell = resolve_shell("win32")
    assert shell.name == "bash" and shell.argv[0] == str(git_root / "bin" / "bash.exe")
    assert "System32" not in shell.argv[0]


def test_windows_falls_back_to_powershell(monkeypatch):
    monkeypatch.setattr(shell_env.shutil, "which", lambda name: "C:/pwsh.exe" if name == "pwsh" else None)
    shell = resolve_shell("win32")
    assert shell.name == "powershell"
    assert shell.command("Get-ChildItem") == ["C:/pwsh.exe", "-NoProfile", "-NonInteractive", "-Command", "Get-ChildItem"]


def test_windows_kill_uses_taskkill(monkeypatch):
    calls = []

    class Proc:
        pid, returncode = 4242, None

        async def wait(self):
            return 0

    async def fake_exec(*argv, **kwargs):
        calls.append(argv)
        return Proc()

    monkeypatch.setattr(shell_env.os, "name", "nt")
    monkeypatch.setattr(shell_env.asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(shell_env.kill_process_tree(Proc()))
    assert calls == [("taskkill", "/T", "/F", "/PID", "4242")]


def test_config_and_state_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_module, "IS_WINDOWS", True)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert settings_module.config_dir() == tmp_path / "Roaming" / "entroclaw"
    assert settings_module._state_dir() == tmp_path / "Local" / "entroclaw"
    monkeypatch.setattr(settings_module, "IS_WINDOWS", False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert settings_module.config_dir() == tmp_path / "cfg" / "entroclaw"
    assert settings_module._state_dir() == tmp_path / "state" / "entroclaw"


def test_source_checkout_uses_agent_env():
    checkout_env = Path(settings_module.__file__).resolve().parents[3] / ".env"
    assert checkout_env == settings_module.AGENT_ENV_FILE


async def test_health_reports_version_and_shell(settings):
    import httpx

    from coding_agent import __version__
    from coding_agent.agent.fake import ScriptedChatModel
    from coding_agent.server.api import create_app

    app = create_app(settings, ScriptedChatModel(turns=[]))
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        health = (await client.get("/health")).json()
    assert health["version"] == __version__
    assert health["shell"] in {"bash", "sh", "powershell"}
