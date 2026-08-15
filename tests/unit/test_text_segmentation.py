"""Unit coverage for Phase 6 ticket 04 cue-bound boundary adjudication.

Ticket 04 turns valid model-proposed cue-pair boundaries into formal
SemanticSegments with exactly-once PresentationCue ownership. The adjudicator is
deterministic: it permits final boundaries only between existing PresentationCues
within one Part; rejects duplicate, empty, out-of-range, and coverage-breaking
candidates; deduplicates overlapping technical-block candidates by complete cue
identity; and uses the one-segment-per-Part conservative fallback (retaining a
reason and ``partial`` status) whenever no valid proposal tiles the Part. It
never invents a theme boundary.
"""

from __future__ import annotations

import pytest

from video_content_pipeline import text_segmentation as seg


def _inventory(part_id: str = "part-a", count: int = 4) -> seg.PartCueInventory:
    return seg.PartCueInventory(
        part_id=part_id,
        cue_ids=tuple(f"{part_id}:cue-{index}" for index in range(count)),
    )


def _proposed(
    inventory: seg.PartCueInventory,
    start: int,
    end: int,
    *,
    technical_block_id: str | None = None,
) -> seg.ProposedSegment:
    return seg.ProposedSegment(
        part_id=inventory.part_id,
        start_cue_id=inventory.cue_ids[start],
        end_cue_id=inventory.cue_ids[end],
        technical_block_id=technical_block_id,
    )


def _owned_cue_ids(adjudication: seg.PartAdjudication) -> list[str]:
    return [cue_id for segment in adjudication.segments for cue_id in segment.cue_ids]


def test_valid_tiling_becomes_ordered_segments_with_exactly_once_ownership() -> None:
    inventory = _inventory(count=4)
    proposed = [_proposed(inventory, 0, 1), _proposed(inventory, 2, 3)]

    result = seg.adjudicate_part_segments(inventory, proposed)

    assert result.used_fallback is False
    assert result.partial is False
    assert result.fallback is None
    assert result.rejected == ()
    assert [segment.cue_ids for segment in result.segments] == [
        ("part-a:cue-0", "part-a:cue-1"),
        ("part-a:cue-2", "part-a:cue-3"),
    ]
    assert [segment.ordinal for segment in result.segments] == [0, 1]
    assert all(segment.origin == "adjudicated" for segment in result.segments)
    # Exactly-once ownership: every cue owned exactly once, in Part order.
    assert _owned_cue_ids(result) == list(inventory.cue_ids)


def test_single_whole_part_candidate_is_adjudicated_not_fallback() -> None:
    inventory = _inventory(count=3)
    proposed = [_proposed(inventory, 0, 2)]

    result = seg.adjudicate_part_segments(inventory, proposed)

    assert result.used_fallback is False
    assert len(result.segments) == 1
    assert result.segments[0].cue_ids == inventory.cue_ids
    assert result.segments[0].origin == "adjudicated"


def test_single_cue_segment_is_a_valid_non_empty_boundary() -> None:
    inventory = _inventory(count=3)
    proposed = [_proposed(inventory, 0, 0), _proposed(inventory, 1, 2)]

    result = seg.adjudicate_part_segments(inventory, proposed)

    assert result.used_fallback is False
    assert [segment.cue_ids for segment in result.segments] == [
        ("part-a:cue-0",),
        ("part-a:cue-1", "part-a:cue-2"),
    ]


def test_out_of_range_candidate_is_rejected_and_forces_fallback() -> None:
    inventory = _inventory(count=3)
    proposed = [
        seg.ProposedSegment("part-a", "part-a:cue-0", "part-a:cue-99"),
        seg.ProposedSegment("part-a", "part-a:ghost", "part-a:cue-2"),
    ]

    result = seg.adjudicate_part_segments(inventory, proposed)

    reasons = {diagnostic.reason for diagnostic in result.rejected}
    assert reasons == {"boundary_out_of_range"}
    # No valid candidate survived, so the whole Part falls back conservatively.
    assert result.used_fallback is True
    assert result.partial is True


def test_empty_or_inverted_candidate_is_rejected() -> None:
    inventory = _inventory(count=4)
    # cue-3 precedes cue-1 in the Part order: an empty/inverted span.
    inverted = seg.ProposedSegment("part-a", "part-a:cue-3", "part-a:cue-1")
    proposed = [inverted, _proposed(inventory, 0, 3)]

    result = seg.adjudicate_part_segments(inventory, proposed)

    assert [diagnostic.reason for diagnostic in result.rejected] == ["boundary_empty"]
    # The surviving whole-Part candidate still tiles, so no fallback is needed.
    assert result.used_fallback is False
    assert result.segments[0].cue_ids == inventory.cue_ids


def test_overlapping_technical_blocks_deduplicate_by_complete_cue_identity() -> None:
    inventory = _inventory(count=4)
    # Two overlapping context windows both surface the same cue spans.
    proposed = [
        _proposed(inventory, 0, 1, technical_block_id="block-1"),
        _proposed(inventory, 2, 3, technical_block_id="block-1"),
        _proposed(inventory, 0, 1, technical_block_id="block-2"),
        _proposed(inventory, 2, 3, technical_block_id="block-2"),
    ]

    result = seg.adjudicate_part_segments(inventory, proposed)

    assert result.used_fallback is False
    assert len(result.segments) == 2
    assert _owned_cue_ids(result) == list(inventory.cue_ids)
    duplicate_reasons = [diagnostic.reason for diagnostic in result.rejected]
    assert duplicate_reasons == ["boundary_duplicate", "boundary_duplicate"]


def test_cross_part_candidate_preserves_part_boundaries() -> None:
    inventory = _inventory("part-a", count=3)
    proposed = [
        seg.ProposedSegment("part-b", "part-b:cue-0", "part-b:cue-2"),
        _proposed(inventory, 0, 2),
    ]

    result = seg.adjudicate_part_segments(inventory, proposed)

    assert [diagnostic.reason for diagnostic in result.rejected] == ["boundary_crosses_part"]
    assert result.used_fallback is False
    assert result.segments[0].cue_ids == inventory.cue_ids


def test_coverage_gap_forces_conservative_fallback() -> None:
    inventory = _inventory(count=4)
    # cue-2 is left uncovered: the survivors do not tile the Part.
    proposed = [_proposed(inventory, 0, 1), _proposed(inventory, 3, 3)]

    result = seg.adjudicate_part_segments(inventory, proposed)

    assert result.used_fallback is True
    assert result.partial is True
    assert result.fallback is not None
    assert result.fallback.reason == "conservative_single_segment_fallback"
    assert len(result.segments) == 1
    assert result.segments[0].origin == "conservative_fallback"
    assert result.segments[0].cue_ids == inventory.cue_ids
    assert _owned_cue_ids(result) == list(inventory.cue_ids)
    # The discarded well-formed candidates are retained as coverage-breaking
    # evidence rather than silently dropped.
    assert [diagnostic.reason for diagnostic in result.rejected] == [
        "coverage_breaking",
        "coverage_breaking",
    ]


def test_coverage_overlap_forces_conservative_fallback() -> None:
    inventory = _inventory(count=4)
    # cue-1 would be owned twice: overlapping ownership is coverage-breaking.
    proposed = [_proposed(inventory, 0, 1), _proposed(inventory, 1, 3)]

    result = seg.adjudicate_part_segments(inventory, proposed)

    assert result.used_fallback is True
    assert result.fallback is not None
    assert result.fallback.reason == "conservative_single_segment_fallback"
    # The fallback still owns every cue exactly once.
    assert _owned_cue_ids(result) == list(inventory.cue_ids)
    assert [diagnostic.reason for diagnostic in result.rejected] == [
        "coverage_breaking",
        "coverage_breaking",
    ]


def test_no_candidates_uses_conservative_fallback() -> None:
    inventory = _inventory(count=2)

    result = seg.adjudicate_part_segments(inventory, [])

    assert result.used_fallback is True
    assert result.partial is True
    assert result.fallback is not None
    assert result.fallback.reason == "conservative_single_segment_fallback"
    assert result.segments[0].cue_ids == inventory.cue_ids
    assert result.segments[0].origin == "conservative_fallback"


def test_empty_inventory_is_a_caller_contract_violation() -> None:
    with pytest.raises(seg.TextSegmentationError) as excinfo:
        seg.adjudicate_part_segments(seg.PartCueInventory("part-a", ()), [])

    assert excinfo.value.reason == "empty_cue_inventory"


def test_inventory_rejects_duplicate_cue_ids() -> None:
    with pytest.raises(seg.TextSegmentationError) as excinfo:
        seg.PartCueInventory("part-a", ("cue-0", "cue-0"))

    assert excinfo.value.reason == "invalid_cue_inventory"


def test_adjudication_as_json_is_deterministic_and_complete() -> None:
    inventory = _inventory(count=4)
    proposed = [_proposed(inventory, 0, 1), _proposed(inventory, 2, 3)]

    document = seg.adjudicate_part_segments(inventory, proposed).as_json()

    assert document == {
        "part_id": "part-a",
        "used_fallback": False,
        "fallback": None,
        "segments": [
            {
                "part_id": "part-a",
                "ordinal": 0,
                "origin": "adjudicated",
                "cue_ids": ["part-a:cue-0", "part-a:cue-1"],
            },
            {
                "part_id": "part-a",
                "ordinal": 1,
                "origin": "adjudicated",
                "cue_ids": ["part-a:cue-2", "part-a:cue-3"],
            },
        ],
        "rejected": [],
    }


def test_fallback_as_json_reports_reason_and_single_segment() -> None:
    inventory = _inventory(count=3)

    document = seg.adjudicate_part_segments(inventory, []).as_json()

    assert document["used_fallback"] is True
    assert document["fallback"]["reason"] == "conservative_single_segment_fallback"
    assert document["segments"] == [
        {
            "part_id": "part-a",
            "ordinal": 0,
            "origin": "conservative_fallback",
            "cue_ids": ["part-a:cue-0", "part-a:cue-1", "part-a:cue-2"],
        }
    ]


def test_mixed_part_candidates_are_rejected_preserving_part_boundaries() -> None:
    inventory = _inventory("part-a", count=2)
    proposed = [
        _proposed(inventory, 0, 1),
        # A candidate for another Part is rejected, keeping segments Part-local.
        seg.ProposedSegment("part-b", "part-b:cue-0", "part-b:cue-1"),
    ]

    result = seg.adjudicate_part_segments(inventory, proposed)

    assert result.used_fallback is False
    assert result.segments[0].cue_ids == inventory.cue_ids
    assert [diagnostic.reason for diagnostic in result.rejected] == ["boundary_crosses_part"]
