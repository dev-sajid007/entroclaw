"""install.sh against a fake release directory (no network for the release itself, no real HOME)."""

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.2.0"

pytestmark = pytest.mark.skipif(sys.platform == "win32" or shutil.which("uv") is None, reason="needs POSIX sh and uv")


def target() -> str:
    os_name = "darwin" if sys.platform == "darwin" else "linux"
    arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}[os.uname().machine.lower()]
    return f"{os_name}-{arch}"


@pytest.fixture(scope="module")
def release(tmp_path_factory):
    """A release layout like GitHub's: <base>/v<version>/{archive, wheel, SHA256SUMS}."""
    base = tmp_path_factory.mktemp("release")
    assets = base / f"v{VERSION}"
    assets.mkdir()
    subprocess.run(["uv", "build", "--wheel", "--quiet", "-o", str(assets)], cwd=ROOT / "agent", check=True)
    binary = tmp_path_factory.mktemp("bin") / "entroclaw"
    binary.write_text(f"#!/bin/sh\necho {VERSION}\n")
    binary.chmod(0o755)
    archive = assets / f"entroclaw-{target()}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(binary, arcname="entroclaw")
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in sorted(assets.iterdir())]
    (assets / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    return base


def run_installer(home: Path, release: Path, *args: str) -> subprocess.CompletedProcess:
    real_home = Path.home()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "SHELL": "/bin/bash",
        "ENTROCLAW_RELEASE_BASE": f"file://{release}",
        # Reuse the real uv cache and Python so the test doesn't download them again.
        "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(real_home / ".cache" / "uv")),
        "UV_PYTHON_INSTALL_DIR": os.environ.get("UV_PYTHON_INSTALL_DIR", str(real_home / ".local" / "share" / "uv" / "python")),
    }
    return subprocess.run(["sh", str(ROOT / "install.sh"), *args], env=env, capture_output=True, text=True, timeout=600)


def test_install_verify_and_uninstall(tmp_path, release):
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home, release, "--version", VERSION)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "checksum verified" in result.stdout
    binary = home / ".entroclaw" / "bin" / "entroclaw"
    assert subprocess.run([str(binary)], capture_output=True, text=True).stdout.strip() == VERSION
    agent = home / ".local" / "bin" / "entroclaw-agent"
    assert subprocess.run([str(agent), "--version"], capture_output=True, text=True).stdout.strip() == VERSION
    assert str(home / ".entroclaw" / "bin") in (home / ".bashrc").read_text()

    # Re-running doesn't duplicate the PATH line.
    assert run_installer(home, release, "--version", VERSION).returncode == 0
    assert (home / ".bashrc").read_text().count(".entroclaw/bin") == 1

    result = run_installer(home, release, "--uninstall")
    assert result.returncode == 0
    assert not binary.exists() and not agent.exists()


def test_tampered_archive_is_rejected(tmp_path, release):
    bad = tmp_path / "bad"
    shutil.copytree(release, bad)
    archive = next((bad / f"v{VERSION}").glob("*.tar.gz"))
    archive.write_bytes(archive.read_bytes() + b"tampered")
    home = tmp_path / "home"
    home.mkdir()
    result = run_installer(home, bad, "--version", VERSION, "--no-modify-path")
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr
    assert not (home / ".entroclaw" / "bin" / "entroclaw").exists()
    assert not (home / ".bashrc").exists()


def test_unknown_option(tmp_path, release):
    result = run_installer(tmp_path, release, "--bogus")
    assert result.returncode != 0 and "unknown option" in result.stderr
