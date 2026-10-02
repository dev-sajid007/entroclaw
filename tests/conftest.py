import subprocess
from pathlib import Path

import pytest

from coding_agent.config.settings import Settings
from coding_agent.services.workspace import Workspace


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "hello.py").write_text("print('hello')\n")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def login(user):\n    return user is not None\n")
    (root / ".env").write_text("SECRET_TOKEN=super-secret\n")
    return root


@pytest.fixture
def git_workspace(workspace_dir: Path) -> Path:
    git = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com"]
    subprocess.run([*git, "init", "-q"], cwd=workspace_dir, check=True)
    subprocess.run([*git, "add", "hello.py", "src"], cwd=workspace_dir, check=True)
    subprocess.run([*git, "commit", "-qm", "init"], cwd=workspace_dir, check=True)
    return workspace_dir


@pytest.fixture
def settings(workspace_dir: Path, tmp_path: Path) -> Settings:
    return Settings(workspace=workspace_dir, state_dir=tmp_path / "state", checkpoint_path=tmp_path / "state" / "checkpoints.sqlite")


@pytest.fixture
def workspace(settings: Settings) -> Workspace:
    return Workspace(settings.workspace)
