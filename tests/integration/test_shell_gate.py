from __future__ import annotations

import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path("/Users/apple/Desktop/wp/video-content-pipeline")
SHELL_GATE = PROJECT_ROOT / "scripts" / "require-project-venv.sh"
VCP_WRAPPER = PROJECT_ROOT / "scripts" / "run-vcp.sh"


def test_shell_gate_rejects_an_unactivated_environment_before_python_starts() -> None:
    environment = {"PATH": os.environ["PATH"]}

    result = subprocess.run(
        [str(SHELL_GATE)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 78
    assert "activate" in result.stderr


def test_vcp_wrapper_rejects_an_unactivated_environment_before_python_starts() -> None:
    environment = {"PATH": os.environ["PATH"]}

    result = subprocess.run(
        [str(VCP_WRAPPER), "check-environment"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 78
    assert "VCP environment gate" in result.stderr


def test_shell_gate_accepts_the_activated_project_environment() -> None:
    result = subprocess.run(
        [str(SHELL_GATE)],
        cwd=PROJECT_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
