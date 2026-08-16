"""Model runtime subprocess: the transport seam for heavy MLX engines.

Phase 11 runs every MLX-scale engine (mlx-audio ASR/alignment, mlx-whisper
review ASR, mlx-lm text semantics) in its *own* subprocess so that unified
memory is returned to the OS at exit rather than trusting in-process framework
unloading on a 16 GiB machine (ADR: Model runtime subprocess). This module is
that seam and nothing more -- pure transport with a JSON request/response
contract:

* The parent (:func:`run_engine_subprocess`) serializes an :class:`EngineRequest`
  (model path, task payload, sampling/limits) to the child's stdin, forces the
  hub-offline environment guards, waits for the child, and parses one
  :class:`EngineResult` (the engine's output plus a *required* peak-memory
  evidence field) back from stdout.
* The child (:func:`execute_child` and friends) reads that request, runs the
  engine, measures peak memory, writes the response, and exits -- returning its
  memory to the OS.

No model, no MLX, and no network live here: this module only moves JSON across a
process boundary, so its protocol is testable against a stub executable. Every
abnormal outcome -- a child killed by signal, garbage on stdout, a nonzero exit,
or a timeout -- isolates into a distinct typed :class:`ModelRuntimeError` that
retains the child's exit code, stderr, and stdout as evidence. There is no
automatic retry and the parent writes nothing to disk, so a failed stage leaves
the parent's state clean for its caller to decide what to do.

ONNX-scale models (vad, diarization, ocr) are small enough to run in-process;
only MLX-scale engines cross this boundary. The size rationale is recorded in
the ADR.
"""

from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import IO, Any

# Environment guards forced on every production child so a model load can never
# reach the hub, emit telemetry, or pick up an implicit token. They are also the
# guards the child sees regardless of what the parent's own environment carried.
HUB_OFFLINE_GUARDS: dict[str, str] = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
}

# Retained-evidence caps: enough to diagnose a failure, bounded so a runaway
# child cannot balloon the parent's memory through captured output.
_STDERR_EVIDENCE_LIMIT = 8192
_STDOUT_EVIDENCE_LIMIT = 2048


class ModelRuntimeError(RuntimeError):
    """A model-subprocess execution failed, with a stable reason and evidence.

    ``reason`` is one of the typed codes documented on
    :func:`run_engine_subprocess`; ``evidence`` retains the child's returncode
    and truncated stderr/stdout so the failure can be audited without a retry.
    """

    def __init__(self, reason: str, message: str, evidence: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.reason = reason
        self.evidence: dict[str, Any] = dict(evidence)


@dataclass(frozen=True)
class EngineRequest:
    """One heavy-engine invocation: which model, what to do, under what limits."""

    model_path: str
    task: Mapping[str, Any]
    limits: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize to the canonical wire form the child parses from stdin."""

        return json.dumps(
            {
                "model_path": self.model_path,
                "task": dict(self.task),
                "limits": dict(self.limits),
            },
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True)
class EngineResult:
    """One heavy-engine result: the engine's output plus peak-memory evidence.

    ``child_pid`` is the OS pid the parent observed; because
    :func:`run_engine_subprocess` only returns after the child has exited, that
    pid is gone by the time a caller sees this result -- the proof that the
    stage's memory was returned to the OS.
    """

    result: Mapping[str, Any]
    peak_memory_bytes: int
    child_pid: int


@dataclass(frozen=True)
class _ChildOutcome:
    """One finished child's observable evidence, carried as a unit to any raise."""

    pid: int
    returncode: int | None
    stdout: str
    stderr: str
    command: tuple[str, ...]

    def evidence(self) -> dict[str, Any]:
        """The retained, size-bounded evidence recorded on a failure."""

        return {
            "child_pid": self.pid,
            "returncode": self.returncode,
            "stdout": self.stdout[:_STDOUT_EVIDENCE_LIMIT],
            "stderr": self.stderr[:_STDERR_EVIDENCE_LIMIT],
            "command": list(self.command),
        }


def run_engine_subprocess(
    command: Sequence[str],
    request: EngineRequest,
    *,
    timeout_seconds: float,
) -> EngineResult:
    """Run ``command`` as a model child, exchanging one JSON request/response.

    ``command`` is the argv of the child executable (production children are
    ``[sys.executable, "-m", "<engine child module>"]``; tests pass a stub). The
    child inherits the parent's environment with the :data:`HUB_OFFLINE_GUARDS`
    forced on top, receives ``request`` on stdin, and must print exactly one JSON
    object ``{"result": <mapping>, "peak_memory_bytes": <int >= 0>}`` on stdout
    before exiting zero.

    On any deviation this raises :class:`ModelRuntimeError` with one of these
    distinct reasons and never retries:

    * ``engine_command_empty`` -- ``command`` was empty (a caller bug).
    * ``engine_timeout`` -- the child exceeded ``timeout_seconds`` and was killed.
    * ``engine_child_crashed`` -- the child was terminated by a signal.
    * ``engine_child_exit_nonzero`` -- the child exited with a nonzero code.
    * ``engine_response_invalid`` -- stdout was not the required JSON shape
      (unparseable, missing/ill-typed ``result`` or ``peak_memory_bytes``).

    The parent writes nothing to disk; captured stderr/stdout are retained only
    in the raised error's ``evidence``.
    """

    if not command:
        raise ModelRuntimeError(
            "engine_command_empty", "A model subprocess command must not be empty.", {}
        )

    child_env = dict(os.environ)
    child_env.update(HUB_OFFLINE_GUARDS)

    process = subprocess.Popen(
        list(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    pid = process.pid
    try:
        stdout, stderr = process.communicate(request.to_json(), timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        outcome = _ChildOutcome(pid, process.returncode, stdout, stderr, tuple(command))
        raise ModelRuntimeError(
            "engine_timeout",
            f"Model subprocess exceeded its {timeout_seconds}s budget and was killed.",
            outcome.evidence(),
        ) from None

    outcome = _ChildOutcome(pid, process.returncode, stdout, stderr, tuple(command))
    if outcome.returncode is not None and outcome.returncode < 0:
        raise ModelRuntimeError(
            "engine_child_crashed",
            f"Model subprocess was killed by signal {-outcome.returncode}.",
            outcome.evidence(),
        )
    if outcome.returncode != 0:
        raise ModelRuntimeError(
            "engine_child_exit_nonzero",
            f"Model subprocess exited with code {outcome.returncode}.",
            outcome.evidence(),
        )

    return _parse_response(outcome)


def _parse_response(outcome: _ChildOutcome) -> EngineResult:
    def invalid(message: str) -> ModelRuntimeError:
        return ModelRuntimeError("engine_response_invalid", message, outcome.evidence())

    try:
        payload = json.loads(outcome.stdout)
    except json.JSONDecodeError as error:
        raise invalid("Model subprocess stdout was not valid JSON.") from error

    if not isinstance(payload, Mapping):
        raise invalid("Model subprocess response was not a JSON object.")

    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise invalid("Model subprocess response is missing a 'result' object.")

    peak = payload.get("peak_memory_bytes")
    if not isinstance(peak, int) or isinstance(peak, bool) or peak < 0:
        raise invalid(
            "Model subprocess response is missing required non-negative "
            "'peak_memory_bytes' evidence."
        )

    return EngineResult(result=result, peak_memory_bytes=peak, child_pid=outcome.pid)


# --- Child-side helpers --------------------------------------------------------
# The other end of the wire. A per-engine child module (tickets 06-11) supplies a
# handler that turns an EngineRequest into a result mapping and calls
# execute_child; the MLX engines pass their own peak_probe returning
# ``int(mx.get_peak_memory())`` in place of the process-RSS default.


def read_request(stream: IO[str] | None = None) -> EngineRequest:
    """Parse an :class:`EngineRequest` from a child's stdin (or ``stream``)."""

    raw = (stream if stream is not None else sys.stdin).read()
    payload = json.loads(raw)
    if not isinstance(payload, Mapping):
        raise ValueError("Model subprocess request was not a JSON object.")
    return EngineRequest(
        model_path=str(payload["model_path"]),
        task=dict(payload["task"]),
        limits=dict(payload.get("limits", {})),
    )


def write_result(
    result: Mapping[str, Any],
    peak_memory_bytes: int,
    stream: IO[str] | None = None,
) -> None:
    """Write one protocol response to a child's stdout (or ``stream``)."""

    json.dump(
        {"result": dict(result), "peak_memory_bytes": int(peak_memory_bytes)},
        stream if stream is not None else sys.stdout,
        separators=(",", ":"),
    )


def process_peak_rss_bytes() -> int:
    """Return this process's peak resident set size in bytes.

    The runtime-equivalent peak-memory probe for engines without an MLX
    allocator to query. ``ru_maxrss`` is reported in bytes on macOS and in
    kibibytes on Linux; both are normalized to bytes here.
    """

    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(maxrss) if sys.platform == "darwin" else int(maxrss) * 1024


def execute_child(
    handler: Callable[[EngineRequest], Mapping[str, Any]],
    *,
    peak_probe: Callable[[], int] = process_peak_rss_bytes,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
) -> int:
    """Drive one child invocation: read request, run ``handler``, report result.

    Peak memory is measured *after* the handler returns, via ``peak_probe``
    (MLX engines pass one that returns ``mx.get_peak_memory()``). Exceptions from
    ``handler`` are intentionally not caught: they propagate to a nonzero exit
    with a traceback on stderr, which the parent isolates as
    ``engine_child_exit_nonzero`` with that stderr retained. Returns ``0`` on
    success so callers can ``sys.exit(execute_child(...))``.
    """

    request = read_request(stdin)
    result = handler(request)
    write_result(result, peak_probe(), stdout)
    return 0
