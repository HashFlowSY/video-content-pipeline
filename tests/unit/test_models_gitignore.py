"""No acquired model asset is ever tracked by git; only the registry is.

Phase 11 ticket 04 acceptance: ``models/*`` is git-ignored while
``models/registry.json`` stays tracked. Proven through ``git`` itself
(``check-ignore`` / ``ls-files``), not by re-reading ``.gitignore``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_model_asset_paths_are_git_ignored() -> None:
    # A representative asset under each provider tree -- the path need not exist
    # for check-ignore to answer from .gitignore rules.
    for asset in (
        "models/mlx-community/Qwen3-ASR-1.7B-8bit/rev/model.safetensors",
        "models/snakers4/silero-vad/v6.2.1/silero_vad.onnx",
        "models/k2-fsa/3dspeaker-campplus-zh-en-advanced/tag/embedding.onnx",
    ):
        result = _git("check-ignore", asset)
        assert result.returncode == 0, f"expected {asset} to be git-ignored"
        assert result.stdout.strip() == asset


def test_registry_json_is_not_ignored() -> None:
    result = _git("check-ignore", "models/registry.json")
    # check-ignore exits 1 when the path is NOT ignored.
    assert result.returncode == 1, "models/registry.json must not be git-ignored"


def test_registry_json_is_tracked() -> None:
    result = _git("ls-files", "--", "models/registry.json")
    assert result.stdout.strip() == "models/registry.json"


def test_no_model_asset_is_tracked() -> None:
    # Only the registry may be tracked anywhere under models/.
    result = _git("ls-files", "--", "models/")
    tracked = [line for line in result.stdout.splitlines() if line]
    assert tracked == ["models/registry.json"], f"unexpected tracked model files: {tracked}"
