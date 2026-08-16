"""Offline lockfile gate for Phase 11 ticket 03 -- the torch-free invariant.

The whole inference stack is torch-free by design (spec Workstream B; user
story 3): the project deliberately rejects the CUDA/torch surface so the
dependency graph stays small enough to audit. This gate makes that claim
machine-checked against the committed ``uv.lock``.

**What "torch-free" means here, precisely.** The gate bans a fixed set of
distributions -- ``torch``, ``torchvision``, ``torchaudio``, every ``nvidia-*``
CUDA package, and ``modelscope`` -- from ever being *installed*. A distribution
is installed only when its ``[[package]]`` entry carries an artifact (a wheel or
an sdist); a node with neither can never be materialised into the environment.

**The one recorded deviation.** ``mlx-whisper==0.4.3`` (asr_review) declares
``torch`` as an unconditional runtime dependency in every published version,
even though transcription from pre-converted MLX weights never imports it (the
sole torch user, ``mlx_whisper/torch_whisper.py``, is dead code nothing
imports). ``pyproject.toml``'s ``[tool.uv] override-dependencies`` drops torch
via an always-false ``sys_platform == 'never'`` marker. uv still records a bare
``torch`` node, but with **no wheels and no sdist** -- a neutered phantom that
can never install, and whose only inbound edge is the always-false marker. This
gate tolerates that exact phantom and nothing looser: if the override were
removed, torch would resolve real artifacts and the gate would fail.

No package is downloaded, hashed, executed, or network-accessed here.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

# Distributions that must never be installed. torch's transitive CUDA subtree
# (nvidia-*, and torch itself) plus the sibling torch wheels and modelscope.
_BANNED_EXACT = frozenset({"torch", "torchvision", "torchaudio", "modelscope"})
_BANNED_PREFIXES = ("nvidia-",)

# The exact override that neuters mlx-whisper's phantom torch dependency.
_TORCH_NEVER_MARKER = "sys_platform == 'never'"

# Every distribution the one-shot authorized inference list must lock (spec
# Workstream B). sherpa-onnx-core is the native-library companion pinned
# explicitly because sherpa-onnx's wheel marks requires-dist Dynamic.
_EXPECTED_INFERENCE_DISTS = frozenset(
    {
        "mlx-audio",
        "mlx-whisper",
        "mlx-lm",
        "sherpa-onnx",
        "sherpa-onnx-core",
        "onnxruntime",
        "rapidocr",
        "opencv-python",
        "huggingface-hub",
    }
)


def _is_banned(name: str) -> bool:
    return name in _BANNED_EXACT or any(name.startswith(p) for p in _BANNED_PREFIXES)


def _is_installable(package: dict[str, Any]) -> bool:
    """True when the locked entry carries an artifact that could be installed."""
    return bool(package.get("wheels")) or ("sdist" in package)


def _load_lock() -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))


def _load_pyproject() -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _packages() -> list[dict[str, Any]]:
    return _load_lock()["package"]


def test_no_banned_distribution_is_installable() -> None:
    """The core invariant: no torch/CUDA/modelscope distribution can install."""
    offenders = [
        f"{p['name']}=={p.get('version', '?')}"
        for p in _packages()
        if _is_banned(p["name"]) and _is_installable(p)
    ]
    assert not offenders, (
        "banned distributions carry installable artifacts in uv.lock "
        f"(the torch-free invariant is broken): {sorted(offenders)}"
    )


def test_only_torch_may_appear_and_only_as_a_neutered_phantom() -> None:
    """torch is the single tolerated banned node, and only artifact-free."""
    packages = _packages()
    banned_nodes = {p["name"] for p in packages if _is_banned(p["name"])}
    unexpected = banned_nodes - {"torch"}
    assert not unexpected, (
        "unexpected banned distributions appear as nodes in uv.lock "
        f"(none but the sanctioned torch phantom is allowed): {sorted(unexpected)}"
    )

    torch_nodes = [p for p in packages if p["name"] == "torch"]
    if not torch_nodes:
        return  # torch fully pruned is even better than a phantom; nothing to check.

    (torch,) = torch_nodes
    assert not torch.get("wheels"), "the tolerated torch node must have no wheels"
    assert "sdist" not in torch, "the tolerated torch node must have no sdist"

    # Every inbound edge onto torch must be the always-false marker, so the
    # phantom can never be pulled in on any platform.
    inbound_markers = [
        dep.get("marker")
        for p in packages
        for dep in p.get("dependencies", [])
        if dep.get("name") == "torch"
    ]
    assert inbound_markers, "expected mlx-whisper to declare the phantom torch edge"
    assert all(m == _TORCH_NEVER_MARKER for m in inbound_markers), (
        f"a torch dependency edge is not guarded by the always-false marker: {inbound_markers}"
    )


def test_manifest_records_the_torch_override() -> None:
    """The neutering override is recorded in the lock's manifest, not implicit."""
    overrides = _load_lock().get("manifest", {}).get("overrides", [])
    torch_overrides = [o for o in overrides if o.get("name") == "torch"]
    assert torch_overrides == [{"name": "torch", "marker": _TORCH_NEVER_MARKER}], (
        "uv.lock manifest must record exactly the sanctioned torch override; "
        f"found: {torch_overrides}"
    )


def test_pyproject_declares_the_recorded_torch_override() -> None:
    """The deviation lives in pyproject with its always-false marker."""
    overrides = _load_pyproject().get("tool", {}).get("uv", {}).get("override-dependencies", [])
    assert f"torch; {_TORCH_NEVER_MARKER}" in overrides, (
        "pyproject.toml [tool.uv].override-dependencies must carry the recorded "
        f"torch exclusion; found: {overrides}"
    )


def test_full_inference_stack_is_locked() -> None:
    """Every one-shot authorized dependency actually resolved into the lock."""
    locked = {p["name"] for p in _packages()}
    missing = _EXPECTED_INFERENCE_DISTS - locked
    assert not missing, f"inference dependencies missing from uv.lock: {sorted(missing)}"
