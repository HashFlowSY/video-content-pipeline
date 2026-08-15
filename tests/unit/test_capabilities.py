"""Unit contract for the shared, context-neutral capability helpers.

The candidate matrix is read by more than one Context (audio-analysis owns
``vad``/``forced_alignment``/``diarization``; transcription owns
``asr_primary``/``asr_review``). The shared parser must validate every
candidate's shape and global identity uniqueness while returning only the
capabilities the calling Context owns, so one Context's candidates never
invalidate another Context's read of the same registry.
"""

from __future__ import annotations

import pytest

from video_content_pipeline.capabilities import parse_candidate_matrix


class _MatrixError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def _invalid(message: str) -> _MatrixError:
    return _MatrixError(message)


def test_parse_returns_only_requested_capabilities() -> None:
    registry = {
        "candidates": [
            {"candidate_id": "silero-vad", "capability": "vad"},
            {"candidate_id": "qwen3-asr-1-7b", "capability": "asr_primary"},
            {"candidate_id": "whisper-large-v3", "capability": "asr_review"},
        ]
    }

    grouped = parse_candidate_matrix(
        registry, ("asr_primary", "asr_review"), invalid_error=_invalid
    )

    assert set(grouped) == {"asr_primary", "asr_review"}
    assert [candidate["candidate_id"] for candidate in grouped["asr_primary"]] == [
        "qwen3-asr-1-7b"
    ]
    assert [candidate["candidate_id"] for candidate in grouped["asr_review"]] == [
        "whisper-large-v3"
    ]


def test_parse_still_detects_duplicate_ids_across_capabilities() -> None:
    registry = {
        "candidates": [
            {"candidate_id": "shared-id", "capability": "vad"},
            {"candidate_id": "shared-id", "capability": "asr_primary"},
        ]
    }

    with pytest.raises(_MatrixError):
        parse_candidate_matrix(registry, ("asr_primary",), invalid_error=_invalid)


def test_parse_rejects_a_malformed_candidate_shape() -> None:
    registry = {"candidates": [{"candidate_id": "no-capability"}]}

    with pytest.raises(_MatrixError):
        parse_candidate_matrix(registry, ("asr_primary",), invalid_error=_invalid)


def test_parse_requires_a_candidates_list() -> None:
    with pytest.raises(_MatrixError):
        parse_candidate_matrix({"candidates": {}}, ("asr_primary",), invalid_error=_invalid)
