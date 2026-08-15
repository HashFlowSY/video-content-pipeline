"""Offline unit contract for Phase 8 ticket 06: embedded-media suspicion.

A Suspected embedded-media interval is a low-confidence marker for a possible
embedded video -- never a confirmed fact. It is derived from the picture alone (a
sustained run of transition frames the page index never settled into a page), and
its provenance always states its basis: ``picture_plus_audio`` when a revalidated
Audio analysis report is supplied, ``picture_only`` otherwise. These tests
exercise ``detect_embedded_media`` directly and the tolerant reader that lifts
audio activity regions out of a retained audio report.
"""

from __future__ import annotations

import pytest

from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.visual_page_index import PartPageIndex, RetainedFrame
from video_content_pipeline.visual_text import VisualTextError
from video_content_pipeline.visual_text_suspicion import (
    BASIS_PICTURE_ONLY,
    BASIS_PICTURE_PLUS_AUDIO,
    AudioActivityRegion,
    EmbeddedMediaRuleset,
    audio_activity_regions,
    detect_embedded_media,
)

_RULES = EmbeddedMediaRuleset(
    version="test-embedded-media-v1",
    calibration_required=True,
    minimum_transition_run=3,
)


def _frame(pts: int, page_id: str | None) -> RetainedFrame:
    # region_diff/edge_density are irrelevant to suspicion; only the page-membership
    # (a transition frame belongs to no page) drives the run detection.
    return RetainedFrame(
        pts=ExactTime(pts),
        content_fingerprint="x" if page_id is None else page_id,
        stability=50 if page_id is None else 100,
        edge_density=10,
        region_diff=90 if page_id is None else 0,
        visual_page_id=page_id,
        selected=False,
        selection_reason="unselected_transition_frame" if page_id is None else "x",
    )


def _index(frames: list[RetainedFrame]) -> PartPageIndex:
    return PartPageIndex(
        part_id="part-1",
        detection_version="d",
        sampling_version="s",
        pages=(),
        retained_frames=tuple(frames),
    )


def test_a_sustained_transition_run_is_flagged_picture_only() -> None:
    # A settled page, then a run of four transition frames (motion), then settled again.
    index = _index(
        [
            _frame(0, "page-01"),
            _frame(1, None),
            _frame(2, None),
            _frame(3, None),
            _frame(4, None),
            _frame(5, "page-02"),
        ]
    )
    result = detect_embedded_media(part_id="part-1", index=index, rules=_RULES, audio_regions=None)
    assert result.rules_version == "test-embedded-media-v1"
    assert result.calibration_required is True
    (interval,) = result.intervals
    assert interval.basis == BASIS_PICTURE_ONLY
    assert interval.low_confidence is True
    assert interval.start == ExactTime(1)
    assert interval.end == ExactTime(4)
    assert interval.transition_frame_count == 4
    assert interval.as_json()["overlapping_audio"] == []


def test_a_short_transition_run_is_not_flagged() -> None:
    # A single transition frame is an ordinary page change, not embedded media.
    index = _index([_frame(0, "page-01"), _frame(1, None), _frame(2, "page-02")])
    result = detect_embedded_media(part_id="part-1", index=index, rules=_RULES, audio_regions=None)
    assert result.intervals == ()


def test_supplied_audio_makes_the_basis_picture_plus_audio() -> None:
    index = _index([_frame(1, None), _frame(2, None), _frame(3, None)])
    regions = (
        AudioActivityRegion(HalfOpenInterval(ExactTime(2), ExactTime(3)), "speech_likely"),
        AudioActivityRegion(HalfOpenInterval(ExactTime(50), ExactTime(60)), "speech_likely"),
    )
    result = detect_embedded_media(
        part_id="part-1", index=index, rules=_RULES, audio_regions=regions
    )
    (interval,) = result.intervals
    # An empty-but-present audio-regions list still means audio was supplied.
    assert interval.basis == BASIS_PICTURE_PLUS_AUDIO
    # Only the overlapping active region is recorded as corroborating evidence.
    overlaps = interval.as_json()["overlapping_audio"]
    assert overlaps == [
        {"start": {"numerator": 2, "denominator": 1}, "end": {"numerator": 3, "denominator": 1}}
    ]


def test_supplied_audio_with_no_overlap_still_records_picture_plus_audio_basis() -> None:
    index = _index([_frame(1, None), _frame(2, None), _frame(3, None)])
    result = detect_embedded_media(
        part_id="part-1", index=index, rules=_RULES, audio_regions=()
    )
    (interval,) = result.intervals
    assert interval.basis == BASIS_PICTURE_PLUS_AUDIO
    assert interval.as_json()["overlapping_audio"] == []


def test_detection_is_deterministic() -> None:
    index = _index([_frame(1, None), _frame(2, None), _frame(3, None), _frame(4, None)])
    first = detect_embedded_media(part_id="part-1", index=index, rules=_RULES, audio_regions=None)
    second = detect_embedded_media(part_id="part-1", index=index, rules=_RULES, audio_regions=None)
    assert first.as_json() == second.as_json()


# --- the tolerant audio-report VAD reader ----------------------------------


def test_audio_activity_regions_reads_the_named_source() -> None:
    document = {
        "formal_evidence": [
            {
                "capability": "vad",
                "parts": [
                    {
                        "source_id": "part-1",
                        "voice_activity_intervals": [
                            {
                                "interval": {
                                    "start": {"numerator": 0, "denominator": 1},
                                    "end": {"numerator": 5, "denominator": 1},
                                },
                                "state": "speech_likely",
                            }
                        ],
                    },
                    {"source_id": "part-2", "voice_activity_intervals": []},
                ],
            }
        ]
    }
    regions = audio_activity_regions(document, "part-1")
    (region,) = regions
    assert region.state == "speech_likely"
    assert region.interval == HalfOpenInterval(ExactTime(0), ExactTime(5))


def test_audio_activity_regions_tolerates_a_model_gated_report() -> None:
    # A real offline audio report is model-gated and carries no VAD evidence; that is
    # not an error -- the basis stays picture-plus-audio with no corroboration.
    assert audio_activity_regions({"formal_evidence": []}, "part-1") == ()
    assert audio_activity_regions({}, "part-1") == ()


def test_audio_activity_regions_rejects_a_malformed_present_interval() -> None:
    document = {
        "formal_evidence": [
            {
                "capability": "vad",
                "parts": [
                    {
                        "source_id": "part-1",
                        "voice_activity_intervals": [{"interval": {}, "state": "speech_likely"}],
                    }
                ],
            }
        ]
    }
    with pytest.raises(VisualTextError) as error:
        audio_activity_regions(document, "part-1")
    assert error.value.reason == "visual_text_audio_evidence_invalid"
