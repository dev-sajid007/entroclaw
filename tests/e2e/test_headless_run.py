"""End-to-end: the `coding-agent run` command as a real subprocess, with a scripted model.

The CLI → API → Agent → Tool path through the OpenTUI app is covered by cli/test/e2e.test.ts.
"""

import json
import os
import subprocess
import sys


def run_agent(tmp_path, workspace, turns, *args):
    script = tmp_path / "script.json"
    script.write_text(json.dumps(turns))
    env = {
        **os.environ,
        "FAKE_MODEL_SCRIPT": str(script),
        "WORKSPACE": str(workspace),
        "CHECKPOINT_PATH": str(tmp_path / "cp.sqlite"),
        "LOG_LEVEL": "WARNING",
    }
    return subprocess.run(
        [sys.executable, "-m", "coding_agent.main", "run", *args],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


TURNS = [
    {"tool_calls": [{"name": "write_file", "args": {"path": "out.txt", "content": "done\n"}}]},
    {"content": "Wrote out.txt."},
]


def test_run_with_yes_approves(tmp_path, workspace_dir):
    proc = run_agent(tmp_path, workspace_dir, TURNS, "write out.txt", "--yes")
    assert proc.returncode == 0, proc.stderr
    assert "Wrote out.txt." in proc.stdout
    assert (workspace_dir / "out.txt").read_text() == "done\n"


def test_run_without_tty_rejects(tmp_path, workspace_dir):
    proc = run_agent(tmp_path, workspace_dir, TURNS, "write out.txt")
    assert proc.returncode == 0, proc.stderr
    assert "Approval required" in proc.stderr
    assert "rejecting" in proc.stderr
    assert not (workspace_dir / "out.txt").exists()
