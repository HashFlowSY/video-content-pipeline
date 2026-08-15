"""Phase 6's append-only synthetic human-review records (ticket 07).

An *append-only human text-analysis review* is an independent record that may
label only its reviewed scope. It is immutable and append-only, cannot rewrite
model output or evidence, and never produces a ``human_verified`` result: this
phase stays on the structural side of the *Phase 6 offline human-review
boundary* and emits no real human verification. These records exist to prove the
record shape a future authorized human-review stage would append; the Text
analysis report status never depends on them.

See ``docs/PHASE_06_SPECIFICATION.md`` and the Text Analysis Context.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from video_content_pipeline.evidence import validated_report_id, write_json_once

# Structural dispositions a synthetic reviewer may record. ``human_verified`` is
# intentionally absent: no append-only review can certify real-world quality.
_ALLOWED_DISPOSITIONS = frozenset({"reviewed_ok", "flagged", "needs_followup"})
_ALLOWED_SCOPE_KINDS = frozenset({"segment", "chapter", "collection", "report"})
_HUMAN_REVIEW_BOUNDARY = "structural_review_only"


class HumanReviewError(ValueError):
    """A rejected human-review record with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def append_human_review_record(
    project_root: Path,
    report_id: str,
    *,
    reviewer: str,
    scope: Mapping[str, object],
    disposition: str,
    note: str,
) -> dict[str, object]:
    """Append one immutable structural human-review record to a retained report.

    The record labels its reviewed scope, carries a structural disposition, and
    is written to a fresh sequence-numbered file so a prior record is never
    rewritten or deleted. ``human_verified`` is always ``False`` and no allowed
    disposition can assert real-world verification.
    """

    review_dir = _human_review_dir(project_root, report_id)
    if disposition not in _ALLOWED_DISPOSITIONS:
        raise HumanReviewError(
            "human_review_disposition_invalid",
            f"Disposition {disposition!r} is not an allowed structural review outcome.",
        )
    kind = scope.get("kind")
    if not isinstance(kind, str) or kind not in _ALLOWED_SCOPE_KINDS:
        raise HumanReviewError(
            "human_review_scope_invalid",
            "A human-review scope requires a known 'kind'.",
        )
    if not isinstance(reviewer, str) or not reviewer:
        raise HumanReviewError(
            "human_review_reviewer_invalid",
            "A human-review record requires a non-empty reviewer label.",
        )

    index = _next_index(review_dir)
    record: dict[str, object] = {
        "index": index,
        "report_id": _validated_report_id(report_id),
        "reviewer": reviewer,
        "scope": dict(scope),
        "disposition": disposition,
        "note": note,
        "boundary": _HUMAN_REVIEW_BOUNDARY,
        "human_verified": False,
    }
    write_json_once(
        review_dir / f"{index:04d}.json",
        record,
        conflict_error=lambda message: HumanReviewError("human_review_conflict", message),
    )
    return record


def load_human_review_records(project_root: Path, report_id: str) -> tuple[dict[str, object], ...]:
    """Return the retained append-only human-review records in append order."""

    review_dir = _human_review_dir(project_root, report_id)
    if not review_dir.exists():
        return ()
    records: list[dict[str, object]] = []
    for path in sorted(review_dir.glob("*.json")):
        decoded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(decoded, Mapping):
            raise HumanReviewError(
                "human_review_record_invalid", f"Human-review record {path.name} is not an object."
            )
        records.append(dict(decoded))
    return tuple(records)


def _human_review_dir(project_root: Path, report_id: str) -> Path:
    workspace = project_root / "work" / "text-analysis-reports" / _validated_report_id(report_id)
    if not (workspace / "text-analysis-report.json").exists():
        raise HumanReviewError(
            "text_analysis_report_invalid",
            "Human review requires a retained text-analysis report.",
        )
    return workspace / "human-review"


def _next_index(review_dir: Path) -> int:
    if not review_dir.exists():
        return 0
    return len(list(review_dir.glob("*.json")))


def _validated_report_id(value: str) -> str:
    return validated_report_id(
        value,
        invalid_error=lambda: HumanReviewError(
            "text_analysis_report_invalid", "Text analysis report ID must be a UUID."
        ),
    )
