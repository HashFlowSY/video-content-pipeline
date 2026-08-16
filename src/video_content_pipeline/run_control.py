"""Control requests: how a second terminal pauses or cancels a running run.

``vcp pause`` and ``vcp cancel`` do not touch the run's state or journal — the
run process is their sole writer (ADR 0053). Instead they leave a Control
request file under ``work/<source-id>/<run-id>/control/`` with
:func:`request_control`. The run process, at each completed Stage unit boundary,
calls :func:`observe_controls_at_boundary`: it reads any pending requests,
journals a single ``control_request_observed`` event, consumes the request
files, and returns a :class:`ControlDirective` telling the run loop what to do
next. The resulting state transition is a *separate*, explicit step
(:func:`apply_pause` / :func:`apply_cancel`), so a request, its observation, and
its transition remain three distinct auditable events.

Because only a live run process ever journals or transitions, a request that no
running process observes — for example one left behind for a run that has
already terminated — can never corrupt run state; it simply sits unconsumed on
disk. Cancel supersedes pause: if both are pending at a boundary, the run
cancels (and both files are consumed, so a later resume never re-observes the
stale pause).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from video_content_pipeline.durable_io import atomic_replace, to_utc_isoformat, utc_now
from video_content_pipeline.orchestration import RunLayout
from video_content_pipeline.run_state import RunState, RunStateWriter, RunStatus

CONTROL_SCHEMA_VERSION = 1


class ControlRequestError(ValueError):
    """A control-request failure with an auditable machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class ControlKind(StrEnum):
    """The kinds of control a second terminal may request."""

    PAUSE = "pause"
    CANCEL = "cancel"


class ControlDirective(StrEnum):
    """What the run loop should do after observing controls at a boundary."""

    CONTINUE = "continue"
    PAUSE = "pause"
    CANCEL = "cancel"


#: Precedence when several requests are pending: cancel supersedes pause, so a
#: cancel issued alongside (or after) a pause wins at the next boundary.
_PRECEDENCE: dict[ControlKind, int] = {ControlKind.CANCEL: 0, ControlKind.PAUSE: 1}


@dataclass(frozen=True)
class ControlRequest:
    """One pending control request read back from disk (read-only)."""

    kind: ControlKind
    requested_at: str
    detail: Mapping[str, object]


def _instant(value: datetime) -> str:
    return to_utc_isoformat(
        value,
        on_naive=lambda: ControlRequestError(
            "naive_control_timestamp", "A control-request timestamp must be timezone-aware."
        ),
    )


def control_dir(layout: RunLayout) -> Path:
    """The directory holding this run's pending control request files."""

    return layout.work_dir / "control"


def _request_path(layout: RunLayout, kind: ControlKind) -> Path:
    return control_dir(layout) / f"{kind.value}.json"


def request_control(
    layout: RunLayout,
    kind: ControlKind,
    *,
    detail: Mapping[str, object] | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> Path:
    """Record a control request for the run process to observe (command side).

    Writes ``control/<kind>.json`` atomically (temp-then-rename) inside the
    run's existing work directory. Re-requesting the same kind overwrites the
    prior file — a request is a standing intent, not a queue — so repeated
    ``vcp pause`` calls never pile up. Does not touch run state or journal.
    """

    if not layout.work_dir.is_dir():
        raise ControlRequestError(
            "run_workspace_missing", f"The run workspace {layout.work_dir} does not exist."
        )
    directory = control_dir(layout)
    directory.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "kind": kind.value,
        "requested_at": _instant(clock()),
        "detail": dict(detail) if detail is not None else {},
    }
    payload = json.dumps(document, sort_keys=True, indent=2) + "\n"
    path = _request_path(layout, kind)
    atomic_replace(path, payload)
    return path


def _read_request(path: Path, kind: ControlKind) -> ControlRequest:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        # A crash can leave a request file torn (a valid-UTF-8 prefix that no
        # longer parses as JSON) or filled with raw non-UTF-8 bytes; both are
        # corruption the reader must reject with its own typed reason rather than
        # leaking a bare ``UnicodeDecodeError`` to the run loop.
        raise ControlRequestError(
            "control_request_unreadable", f"Control request at {path} is not readable JSON."
        ) from error
    if not isinstance(document, Mapping):
        raise ControlRequestError(
            "control_request_unreadable", "A control request must be a JSON object."
        )
    if document.get("schema_version") != CONTROL_SCHEMA_VERSION:
        raise ControlRequestError(
            "control_request_unreadable",
            f"Control request schema_version must be {CONTROL_SCHEMA_VERSION}.",
        )
    requested_at = document.get("requested_at")
    if not isinstance(requested_at, str):
        raise ControlRequestError(
            "control_request_unreadable", "Control request requested_at must be a string."
        )
    detail = document.get("detail", {})
    if not isinstance(detail, Mapping):
        raise ControlRequestError(
            "control_request_unreadable", "Control request detail must be an object."
        )
    return ControlRequest(kind=kind, requested_at=requested_at, detail=dict(detail))


def read_pending_controls(layout: RunLayout) -> tuple[ControlRequest, ...]:
    """Read the pending control requests, highest precedence first (read-only).

    Cancel sorts before pause. Returns an empty tuple when there is no control
    directory or no requests. Never mutates state, journal, or the requests.
    """

    directory = control_dir(layout)
    if not directory.is_dir():
        return ()
    requests: list[ControlRequest] = []
    for kind in ControlKind:
        path = _request_path(layout, kind)
        if path.exists():
            requests.append(_read_request(path, kind))
    requests.sort(key=lambda request: _PRECEDENCE[request.kind])
    return tuple(requests)


def observe_controls_at_boundary(writer: RunStateWriter, layout: RunLayout) -> ControlDirective:
    """Observe pending controls at a Stage unit boundary and say what to do.

    Called by the run loop only when the run is running. Journals a single
    ``control_request_observed`` event for the winning request (cancel over
    pause), consumes every pending request file so nothing is re-observed on a
    later boundary or a resume, and returns the matching directive.
    :data:`ControlDirective.CONTINUE` (no journal event) when nothing is
    pending. It does not itself change run status — the caller applies the
    transition with :func:`apply_pause` or :func:`apply_cancel`.
    """

    pending = read_pending_controls(layout)
    if not pending:
        return ControlDirective.CONTINUE
    winner = pending[0]
    # The command side cannot journal (single-writer, ADR 0053), so the
    # observation event carries the request's own metadata — its requested_at
    # and detail — making the request itself reconstructable from the journal.
    observed_detail: dict[str, object] = {"requested_at": winner.requested_at}
    if winner.detail:
        observed_detail["request_detail"] = dict(winner.detail)
    writer.observe_control_request(winner.kind.value, detail=observed_detail)
    _consume_controls(layout)
    return ControlDirective(winner.kind.value)


def _consume_controls(layout: RunLayout) -> None:
    for kind in ControlKind:
        path = _request_path(layout, kind)
        try:
            path.unlink()
        except FileNotFoundError:
            continue


def apply_pause(writer: RunStateWriter) -> RunState:
    """Run the user-pause sequence ``running -> pausing -> paused``.

    Each edge is journaled as its own transition; after this the run process
    exits cleanly with state and journal already flushed to disk, and ``vcp
    resume`` later starts a fresh process from ``paused``.
    """

    writer.transition_to(RunStatus.PAUSING)
    return writer.transition_to(RunStatus.PAUSED)


def apply_cancel(writer: RunStateWriter) -> RunState:
    """Cancel the run (``running -> cancelled``), stopping later stages.

    Cancellation is terminal for execution: no further stage units run. The
    caller still publishes whatever results already completed — publication is
    not a state transition — so a cancelled run yields a bundle of its
    completed work.
    """

    return writer.transition_to(RunStatus.CANCELLED)
