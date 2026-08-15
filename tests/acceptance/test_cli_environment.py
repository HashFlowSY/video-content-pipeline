from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path("/Users/apple/Desktop/wp/video-content-pipeline")
VCP_WRAPPER = PROJECT_ROOT / "scripts" / "run-vcp.sh"


def test_check_environment_cli_reports_the_active_project_environment() -> None:
    result = subprocess.run(
        [str(VCP_WRAPPER), "check-environment"],
        cwd=PROJECT_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "executable": str(PROJECT_ROOT / ".venv" / "bin" / "python"),
        "prefix": str(PROJECT_ROOT / ".venv"),
        "virtual_env": str(PROJECT_ROOT / ".venv"),
    }
