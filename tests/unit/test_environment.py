from __future__ import annotations

from pathlib import Path

import pytest

from video_content_pipeline.environment import (
    EnvironmentGateError,
    assert_project_venv,
    assert_runtime_policy,
)

PROJECT_ROOT = Path("/Users/apple/Desktop/wp/video-content-pipeline")
EXPECTED_VENV = PROJECT_ROOT / ".venv"


def test_in_process_gate_accepts_the_activated_project_environment() -> None:
    identity = assert_project_venv()

    assert identity.virtual_env == EXPECTED_VENV
    assert identity.prefix == EXPECTED_VENV
    assert identity.executable in {
        EXPECTED_VENV / "bin" / "python",
        EXPECTED_VENV / "bin" / "python3",
        EXPECTED_VENV / "bin" / "python3.12",
    }


def test_in_process_gate_rejects_a_missing_virtual_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)

    with pytest.raises(EnvironmentGateError, match="VIRTUAL_ENV"):
        assert_project_venv()


def test_runtime_policy_rejects_global_and_automatic_runtime_behavior() -> None:
    policy = assert_runtime_policy()

    assert policy.project_root == PROJECT_ROOT
    assert policy.virtual_env == EXPECTED_VENV
    assert policy.allow_system_python is False
    assert policy.allow_ordinary_pip_install is False
    assert policy.allow_uv_run is False
    assert policy.automatic_dependency_upgrade is False
    assert policy.automatic_python_download is False
    assert policy.allow_runtime_network is False
    assert policy.allow_automatic_model_downloads is False
    assert policy.allow_paid_apis is False
