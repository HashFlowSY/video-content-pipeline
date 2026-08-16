"""Protocol tests for the Phase 11 Model runtime subprocess seam (ticket 05).

The seam is transport only: the parent serializes an :class:`EngineRequest` to a
child's stdin, the child returns a JSON result plus required peak-memory
evidence on stdout and exits, and the parent parses it back into an
:class:`EngineResult`. No model and no MLX are involved -- every child here is a
tiny stub executable, so the engineering gate stays fast, offline, and
model-free (spec Testing Decisions; ticket 05 acceptance).

The four failure modes -- child crash by signal, garbage stdout, nonzero exit,
and timeout -- each isolate into a *distinct* typed :class:`ModelRuntimeError`
that retains the child's exit/stderr/stdout evidence while the parent writes
nothing to disk. A successful run additionally proves that the child process is
gone once :func:`run_engine_subprocess` returns (unified memory returned to the
OS) and that the hub-offline guards were present in the child's environment.
"""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

import pytest

from video_content_pipeline.model_runtime import (
    HUB_OFFLINE_GUARDS,
    EngineRequest,
    EngineResult,
    ModelRuntimeError,
    process_peak_rss_bytes,
    run_engine_subprocess,
)

# --- Stub children -------------------------------------------------------------
# Each stub reads the request JSON from stdin and writes a response to stdout.
# They deliberately import nothing beyond the standard library.

_ECHO_STUB = r"""
import json, os, sys
request = json.loads(sys.stdin.read())
guards = {name: os.environ.get(name) for name in %(guard_names)r}
json.dump(
    {
        "result": {
            "model_path": request["model_path"],
            "task": request["task"],
            "limits": request["limits"],
            "guards": guards,
        },
        "peak_memory_bytes": 123456,
    },
    sys.stdout,
)
"""

_MISSING_PEAK_STUB = r"""
import json, sys
sys.stdin.read()
json.dump({"result": {"ok": True}}, sys.stdout)
"""

_CRASH_STUB = r"""
import os, signal, sys
sys.stdin.read()
os.kill(os.getpid(), signal.SIGKILL)
"""

_GARBAGE_STUB = r"""
import sys
sys.stdin.read()
sys.stdout.write("this is not json {{{")
"""

_NONZERO_STUB = r"""
import sys
sys.stdin.read()
sys.stderr.write("engine blew up: boom\n")
sys.exit(2)
"""

_TIMEOUT_STUB = r"""
import sys, time
sys.stdin.read()
time.sleep(30)
"""

# Uses the real child-side helpers to prove the whole wire round-trips through
# ``execute_child`` (read request -> run handler -> measure peak -> write).
_HELPER_STUB = r"""
import sys
from video_content_pipeline.model_runtime import execute_child

def handler(request):
    return {"seen_model_path": request.model_path, "seen_task": request.task}

sys.exit(execute_child(handler))
"""


def _write_stub(tmp_path: Path, name: str, body: str) -> Path:
    stub = tmp_path / name
    stub.write_text(body, encoding="utf-8")
    return stub


def _command(stub: Path) -> list[str]:
    return [sys.executable, str(stub)]


def _request() -> EngineRequest:
    return EngineRequest(
        model_path="/models/example/rev",
        task={"kind": "transcribe", "audio": "part-0001.wav"},
        limits={"temperature": 0, "seed": 7, "max_kv_size": 4096},
    )


# --- Happy-path protocol -------------------------------------------------------


def test_protocol_round_trips_with_stub(tmp_path: Path) -> None:
    stub = _write_stub(
        tmp_path, "echo.py", _ECHO_STUB % {"guard_names": tuple(HUB_OFFLINE_GUARDS)}
    )
    request = _request()

    result = run_engine_subprocess(_command(stub), request, timeout_seconds=30)

    assert isinstance(result, EngineResult)
    assert result.result["model_path"] == request.model_path
    assert result.result["task"] == dict(request.task)
    assert result.result["limits"] == dict(request.limits)
    assert result.peak_memory_bytes == 123456
    assert result.child_pid > 0


def test_offline_guards_set_in_child_environment(tmp_path: Path) -> None:
    stub = _write_stub(
        tmp_path, "echo.py", _ECHO_STUB % {"guard_names": tuple(HUB_OFFLINE_GUARDS)}
    )

    result = run_engine_subprocess(_command(stub), _request(), timeout_seconds=30)

    assert result.result["guards"] == dict(HUB_OFFLINE_GUARDS)
    # Every guard is a non-empty offline signal, never left unset.
    assert all(value == "1" for value in HUB_OFFLINE_GUARDS.values())


def test_memory_returned_after_exit(tmp_path: Path) -> None:
    """Once the runner returns, the child process no longer exists on the OS."""
    stub = _write_stub(
        tmp_path, "echo.py", _ECHO_STUB % {"guard_names": tuple(HUB_OFFLINE_GUARDS)}
    )

    result = run_engine_subprocess(_command(stub), _request(), timeout_seconds=30)

    with pytest.raises(ProcessLookupError):
        os.kill(result.child_pid, 0)


def test_parent_writes_nothing_to_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _write_stub(
        tmp_path, "echo.py", _ECHO_STUB % {"guard_names": tuple(HUB_OFFLINE_GUARDS)}
    )
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    run_engine_subprocess(_command(stub), _request(), timeout_seconds=30)

    assert list(workdir.iterdir()) == []


# --- Response validation -------------------------------------------------------


def test_missing_peak_memory_field_is_rejected(tmp_path: Path) -> None:
    stub = _write_stub(tmp_path, "nopeak.py", _MISSING_PEAK_STUB)

    with pytest.raises(ModelRuntimeError) as excinfo:
        run_engine_subprocess(_command(stub), _request(), timeout_seconds=30)

    assert excinfo.value.reason == "engine_response_invalid"


def test_negative_peak_memory_is_rejected(tmp_path: Path) -> None:
    body = (
        "import json, sys\n"
        "sys.stdin.read()\n"
        'json.dump({"result": {}, "peak_memory_bytes": -1}, sys.stdout)\n'
    )
    stub = _write_stub(tmp_path, "negpeak.py", body)

    with pytest.raises(ModelRuntimeError) as excinfo:
        run_engine_subprocess(_command(stub), _request(), timeout_seconds=30)

    assert excinfo.value.reason == "engine_response_invalid"


# --- Typed, distinct failure modes ---------------------------------------------


def test_child_crash_is_typed(tmp_path: Path) -> None:
    stub = _write_stub(tmp_path, "crash.py", _CRASH_STUB)

    with pytest.raises(ModelRuntimeError) as excinfo:
        run_engine_subprocess(_command(stub), _request(), timeout_seconds=30)

    error = excinfo.value
    assert error.reason == "engine_child_crashed"
    assert error.evidence["returncode"] == -signal.SIGKILL


def test_garbage_stdout_is_typed(tmp_path: Path) -> None:
    stub = _write_stub(tmp_path, "garbage.py", _GARBAGE_STUB)

    with pytest.raises(ModelRuntimeError) as excinfo:
        run_engine_subprocess(_command(stub), _request(), timeout_seconds=30)

    error = excinfo.value
    assert error.reason == "engine_response_invalid"
    assert "not json" in error.evidence["stdout"]


def test_nonzero_exit_is_typed_and_retains_stderr(tmp_path: Path) -> None:
    stub = _write_stub(tmp_path, "nonzero.py", _NONZERO_STUB)

    with pytest.raises(ModelRuntimeError) as excinfo:
        run_engine_subprocess(_command(stub), _request(), timeout_seconds=30)

    error = excinfo.value
    assert error.reason == "engine_child_exit_nonzero"
    assert error.evidence["returncode"] == 2
    assert "engine blew up: boom" in error.evidence["stderr"]


def test_timeout_is_typed(tmp_path: Path) -> None:
    stub = _write_stub(tmp_path, "timeout.py", _TIMEOUT_STUB)

    with pytest.raises(ModelRuntimeError) as excinfo:
        run_engine_subprocess(_command(stub), _request(), timeout_seconds=0.5)

    assert excinfo.value.reason == "engine_timeout"


def test_failure_reasons_are_all_distinct(tmp_path: Path) -> None:
    cases = {
        "crash.py": _CRASH_STUB,
        "garbage.py": _GARBAGE_STUB,
        "nonzero.py": _NONZERO_STUB,
    }
    reasons = set()
    for name, body in cases.items():
        stub = _write_stub(tmp_path, name, body)
        with pytest.raises(ModelRuntimeError) as excinfo:
            run_engine_subprocess(_command(stub), _request(), timeout_seconds=30)
        reasons.add(excinfo.value.reason)

    assert len(reasons) == len(cases)


def test_empty_command_is_rejected() -> None:
    with pytest.raises(ModelRuntimeError) as excinfo:
        run_engine_subprocess([], _request(), timeout_seconds=30)

    assert excinfo.value.reason == "engine_command_empty"


# --- Child-side helpers --------------------------------------------------------


def test_child_helper_round_trips_through_the_wire(tmp_path: Path) -> None:
    stub = _write_stub(tmp_path, "helper.py", _HELPER_STUB)
    request = _request()

    result = run_engine_subprocess(_command(stub), request, timeout_seconds=60)

    assert result.result["seen_model_path"] == request.model_path
    assert result.result["seen_task"] == dict(request.task)
    # execute_child measures a real, positive process peak by default.
    assert isinstance(result.peak_memory_bytes, int)
    assert result.peak_memory_bytes > 0


def test_process_peak_rss_is_a_positive_int() -> None:
    peak = process_peak_rss_bytes()
    assert isinstance(peak, int)
    assert peak > 0


def test_request_json_is_canonical() -> None:
    payload = json.loads(_request().to_json())
    assert set(payload) == {"model_path", "task", "limits"}
    assert payload["task"] == {"kind": "transcribe", "audio": "part-0001.wav"}
