"""Run identity and the run-owned directory layout for the orchestration Context.

This module is the identity and filesystem foundation of a Run: a
collection-level ``source-id`` derived from ordered Part content hashes, a
``run-id`` bound to the immutable plan id and configuration hash, and the
run-owned layout under ``work/<source-id>/<run-id>/`` plus the published layout
under ``outputs/<source-id>/<run-id>/``. Every later orchestration contract
addresses a run through these deterministic, non-colliding, plan-bound names,
and nothing can overwrite a published run by construction (ADR 0051).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from video_content_pipeline.planning import RunPlan

_SHA256_HEX_LENGTH = 64
_SHA256_HEX_ALPHABET = frozenset("0123456789abcdef")
_RUN_ID_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_RUN_ID_DIGEST_LENGTH = 16


class OrchestrationError(ValueError):
    """An orchestration failure with an auditable machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _validated_content_hash(value: str) -> str:
    if len(value) != _SHA256_HEX_LENGTH or any(char not in _SHA256_HEX_ALPHABET for char in value):
        raise OrchestrationError(
            "invalid_content_hash",
            "A Part content hash must be a lowercase sha256 hex digest.",
        )
    return value


def _canonical_json(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def derive_source_id(part_content_hashes: tuple[str, ...]) -> str:
    """Derive a stable collection ``source-id`` from ordered Part content hashes.

    A single medium uses its own content hash directly (plan §4). A multi-Part
    collection hashes its ordered Part content hashes together with the
    collection structure, so that changing Part order or membership always
    changes the ``source-id`` while identical collections always agree.
    """

    if not part_content_hashes:
        raise OrchestrationError(
            "empty_collection", "A source-id needs at least one Part content hash."
        )
    validated = tuple(_validated_content_hash(value) for value in part_content_hashes)
    if len(validated) == 1:
        return validated[0]
    payload = _canonical_json(
        {"kind": "collection", "part_count": len(validated), "parts": list(validated)}
    )
    return hashlib.sha256(payload).hexdigest()


def derive_run_id(run_start: datetime, plan_id: str, configuration_fingerprint: str) -> str:
    """Form a ``run-id`` from the run start time, plan id, and config fingerprint.

    The compact UTC timestamp prefix keeps run ids human-readable and ordered;
    the digest suffix binds the immutable plan id and the configuration
    fingerprint, so that a changed configuration can never reproduce an existing
    ``run-id``.
    """

    if run_start.tzinfo is None:
        raise OrchestrationError("naive_run_start", "The run start time must be timezone-aware.")
    if not plan_id:
        raise OrchestrationError("missing_plan_id", "A run-id needs the immutable plan id.")
    if not configuration_fingerprint:
        raise OrchestrationError(
            "missing_configuration_fingerprint",
            "A run-id needs the configuration fingerprint.",
        )
    instant = run_start.astimezone(UTC)
    timestamp = instant.strftime(_RUN_ID_TIMESTAMP_FORMAT)
    payload = _canonical_json(
        {
            "run_start": instant.isoformat(),
            "plan_id": plan_id,
            "configuration_fingerprint": configuration_fingerprint,
        }
    )
    digest = hashlib.sha256(payload).hexdigest()[:_RUN_ID_DIGEST_LENGTH]
    return f"{timestamp}-{digest}"


def source_id_from_run_plan(plan: RunPlan) -> str:
    """Derive the collection ``source-id`` from a confirmed plan's Part order."""

    return derive_source_id(tuple(artifact.sha256 for artifact in plan.source_artifacts))


def run_id_from_run_plan(plan: RunPlan, run_start: datetime) -> str:
    """Derive the ``run-id`` binding a confirmed plan to a run start time."""

    return derive_run_id(run_start, plan.plan_id, plan.configuration_fingerprint)


@dataclass(frozen=True)
class RunLayout:
    """The run-owned filesystem addresses under ``work/`` and ``outputs/``.

    Run-owned state, journal, stage workspaces, ``tmp/``, and the staging area
    all live under ``work/<source-id>/<run-id>/``; the published RunBundle and
    the per-source latest pointer live under ``outputs/<source-id>/``.
    """

    project_root: Path
    source_id: str
    run_id: str

    @property
    def work_dir(self) -> Path:
        return self.project_root / "work" / self.source_id / self.run_id

    @property
    def state_path(self) -> Path:
        return self.work_dir / "run-state.json"

    @property
    def journal_path(self) -> Path:
        return self.work_dir / "events.jsonl"

    @property
    def stages_dir(self) -> Path:
        return self.work_dir / "stages"

    @property
    def tmp_dir(self) -> Path:
        return self.work_dir / "tmp"

    @property
    def staging_dir(self) -> Path:
        return self.work_dir / "staging"

    @property
    def source_output_dir(self) -> Path:
        return self.project_root / "outputs" / self.source_id

    @property
    def output_dir(self) -> Path:
        return self.source_output_dir / self.run_id

    @property
    def latest_path(self) -> Path:
        return self.source_output_dir / "latest.json"


def initialize_run_workspace(layout: RunLayout) -> RunLayout:
    """Create the run-owned work directories after guarding published outputs.

    Refuses to start when the run's published bundle directory already exists,
    so a published run can never be overwritten by construction (ADR 0051). The
    staging directory is created up front in the final RunBundle layout; the
    published ``outputs/`` directory is written only by the atomic publish.
    """

    if layout.output_dir.exists():
        raise OrchestrationError(
            "run_already_published",
            f"A published RunBundle already exists at {layout.output_dir}.",
        )
    for directory in (
        layout.work_dir,
        layout.stages_dir,
        layout.tmp_dir,
        layout.staging_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return layout
