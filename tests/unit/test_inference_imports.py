"""Offline import smoke tests for the Phase 11 ticket 03 inference stack.

Each one-shot authorized package must import cleanly with **no network and no
filesystem writes at import time** (spec Workstream B acceptance). We prove that
the strong way: every import runs in a fresh subprocess whose network syscalls
are hard-blocked and whose HOME/cache/cwd are redirected into empty temp trees.
An import that needed the network would raise and fail the test; an import that
wrote a cache/model/config file would leave a file behind and fail the test.

These are import-only checks -- no model is loaded, downloaded, hashed, or
executed. They also reconfirm, per package, that torch never enters the process
(the torch-free invariant that ``test_lockfile_gate`` enforces at the lock).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

# Import names for the eight importable members of the inference stack.
# (sherpa-onnx-core is a native-library companion with no import name of its
# own; importing ``sherpa_onnx`` exercises that its shared libraries loaded.)
INFERENCE_IMPORTS = (
    "mlx_audio",
    "mlx_whisper",
    "mlx_lm",
    "sherpa_onnx",
    "onnxruntime",
    "rapidocr",
    "cv2",
    "huggingface_hub",
)

# Runs inside the subprocess: block the network, import the target, and refuse
# to pass if torch was pulled in transitively.
_IMPORT_PROBE = r"""
import socket, sys


def _no_network(*args, **kwargs):
    raise RuntimeError("NETWORK_ACCESS_AT_IMPORT")


socket.socket.connect = lambda self, *a, **k: _no_network()
socket.socket.connect_ex = lambda self, *a, **k: _no_network()
socket.create_connection = _no_network
socket.getaddrinfo = _no_network

module = sys.argv[1]
__import__(module)

if "torch" in sys.modules:
    print("TORCH_WAS_IMPORTED", module)
    sys.exit(3)
print("IMPORT_OK", module)
"""


def _run_isolated_import(module: str, home: Path, work: Path) -> subprocess.CompletedProcess[str]:
    home.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "HF_HOME": str(home / "hf"),
        # Belt and suspenders: even if a hub client is constructed at import, it
        # must not try to reach the network.
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        # Keep __pycache__ out of the isolated trees so the write check is exact.
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE, module],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _files_under(*roots: Path) -> list[str]:
    return [str(p) for root in roots for p in root.rglob("*") if p.is_file()]


@pytest.mark.parametrize("module", INFERENCE_IMPORTS)
def test_import_is_offline_and_side_effect_free(module: str, tmp_path: Path) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    result = _run_isolated_import(module, home, work)

    assert result.returncode == 0, (
        f"importing {module!r} failed with the network blocked and caches "
        f"isolated (rc={result.returncode}).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert f"IMPORT_OK {module}" in result.stdout, (
        f"{module!r} did not report a clean import.\nstdout:\n{result.stdout}"
    )

    written = _files_under(home, work)
    assert not written, f"{module!r} wrote files at import time: {written}"


def test_torch_is_not_installed() -> None:
    """The banned framework is absent from the environment entirely."""
    assert importlib.util.find_spec("torch") is None, (
        "torch is importable in the project environment; the inference stack must stay torch-free"
    )
