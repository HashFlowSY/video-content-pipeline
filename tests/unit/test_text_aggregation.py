"""Unit coverage for Phase 6 ticket 06 Part-local chapter and collection aggregation.

Ticket 06 aggregates the verified SemanticSegments of tickets 04 and 05 into two
higher-level, deterministic structures:

* Part-local chapters — an optional consecutive sequence of verified segments from
  one Part. A chapter never crosses a Part boundary, never cites an unknown or
  non-consecutive segment, and never overlaps another chapter; every discarded
  proposal is retained as a diagnostic. Chapters need not tile the Part.
* a collection summary — entries that may cite verified segments from multiple
  Parts while retaining each segment's Part identity. Subtitle-unavailable Parts
  are never invented: their CollectionVirtualTime range and reason are declared as
  omitted ranges, and every conservative-fallback or unavailable Part is preserved
  as a limitation so aggregation lowers the collection to ``partial``.

The tests assert deterministic contract properties — Part-local boundaries,
exactly-cited segment identities, retained Part identity, declared omissions,
preserved limitations and source languages — rather than prose quality.
"""

from __future__ import annotations

import pytest

from video_content_pipeline import text_aggregation as agg
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval


def _segment(
    ordinal: int,
    *,
    part_id: str = "part-a",
    source_languages: tuple[str, ...] = ("zh",),
) -> agg.AggregatableSegment:
    return agg.AggregatableSegment(
        part_id=part_id,
        ordinal=ordinal,
        source_languages=source_languages,
    )


def _available(
    part_id: str = "part-a",
    count: int = 4,
    *,
    used_fallback: bool = False,
    source_languages: tuple[str, ...] = ("zh",),
) -> agg.AvailablePart:
    return agg.AvailablePart(
        part_id=part_id,
        segments=tuple(
            _segment(index, part_id=part_id, source_languages=source_languages)
            for index in range(count)
        ),
        used_fallback=used_fallback,
    )


def _unavailable(
    part_id: str = "part-z", *, start: int = 0, end: int = 10, reason: str = "no_primary_subtitle"
) -> agg.UnavailablePart:
    return agg.UnavailablePart(
        part_id=part_id,
        reason=reason,
        virtual_time_range=HalfOpenInterval(ExactTime(start), ExactTime(end)),
    )


def _proposed_chapter(
    part_id: str, start: int, end: int, *, title: str | None = None
) -> agg.ProposedChapter:
    return agg.ProposedChapter(part_id=part_id, start_ordinal=start, end_ordinal=end, title=title)


def _reasons(items: tuple[object, ...]) -> list[str]:
    return [item.reason for item in items]  # type: ignore[attr-defined]


# --- Chapter adjudication ---------------------------------------------------


def test_consecutive_chapters_are_part_local_and_ordered() -> None:
    part = _available(count=4)
    proposed = [
        _proposed_chapter("part-a", 0, 1, title="导论"),
        _proposed_chapter("part-a", 2, 3, title="进阶"),
    ]

    result = agg.adjudicate_part_chapters(part, proposed)

    assert result.rejected == ()
    assert [chapter.segment_ordinals for chapter in result.chapters] == [(0, 1), (2, 3)]
    assert [chapter.ordinal for chapter in result.chapters] == [0, 1]
    assert [chapter.title for chapter in result.chapters] == ["导论", "进阶"]
    assert all(chapter.part_id == "part-a" for chapter in result.chapters)


def test_chapters_need_not_cover_every_segment() -> None:
    part = _available(count=4)

    result = agg.adjudicate_part_chapters(part, [_proposed_chapter("part-a", 1, 2)])

    # A chapter is optional; leaving segments 0 and 3 outside any chapter is legal.
    assert result.rejected == ()
    assert [chapter.segment_ordinals for chapter in result.chapters] == [(1, 2)]


def test_single_segment_chapter_is_valid() -> None:
    part = _available(count=3)

    result = agg.adjudicate_part_chapters(part, [_proposed_chapter("part-a", 1, 1)])

    assert result.rejected == ()
    assert result.chapters[0].segment_ordinals == (1,)


def test_chapter_naming_another_part_is_rejected() -> None:
    part = _available("part-a", count=3)

    result = agg.adjudicate_part_chapters(part, [_proposed_chapter("part-b", 0, 2)])

    assert result.chapters == ()
    assert _reasons(result.rejected) == [agg.CHAPTER_CROSSES_PART]


def test_chapter_citing_an_unknown_segment_is_rejected() -> None:
    part = _available("part-a", count=3)

    result = agg.adjudicate_part_chapters(part, [_proposed_chapter("part-a", 0, 9)])

    assert result.chapters == ()
    assert _reasons(result.rejected) == [agg.CHAPTER_UNKNOWN_SEGMENT]


def test_inverted_chapter_span_is_rejected_as_empty() -> None:
    part = _available("part-a", count=3)

    result = agg.adjudicate_part_chapters(part, [_proposed_chapter("part-a", 2, 1)])

    assert result.chapters == ()
    assert _reasons(result.rejected) == [agg.CHAPTER_EMPTY]


def test_overlapping_chapters_reject_the_later_one() -> None:
    part = _available(count=4)
    proposed = [
        _proposed_chapter("part-a", 0, 2),
        _proposed_chapter("part-a", 1, 3),
    ]

    result = agg.adjudicate_part_chapters(part, proposed)

    # First-wins: the earlier proposal is kept, the overlapping later one rejected.
    assert [chapter.segment_ordinals for chapter in result.chapters] == [(0, 1, 2)]
    assert _reasons(result.rejected) == [agg.CHAPTER_OVERLAP]


def test_chapters_are_ordered_by_start_regardless_of_proposal_order() -> None:
    part = _available(count=4)
    proposed = [
        _proposed_chapter("part-a", 2, 3),
        _proposed_chapter("part-a", 0, 1),
    ]

    result = agg.adjudicate_part_chapters(part, proposed)

    assert [chapter.segment_ordinals for chapter in result.chapters] == [(0, 1), (2, 3)]
    assert [chapter.ordinal for chapter in result.chapters] == [0, 1]


def test_chapter_retains_member_source_languages_without_merging() -> None:
    part = agg.AvailablePart(
        part_id="part-a",
        segments=(
            _segment(0, source_languages=("zh",)),
            _segment(1, source_languages=("en",)),
        ),
        used_fallback=False,
    )

    result = agg.adjudicate_part_chapters(part, [_proposed_chapter("part-a", 0, 1)])

    # Mixed Chinese/English is retained distinctly, never collapsed to one language.
    assert result.chapters[0].source_languages == ("en", "zh")


def test_part_chapters_as_json_is_deterministic() -> None:
    part = _available(count=2)

    document = agg.adjudicate_part_chapters(
        part, [_proposed_chapter("part-a", 0, 1, title="全部")]
    ).as_json()

    assert document == {
        "part_id": "part-a",
        "chapters": [
            {
                "part_id": "part-a",
                "ordinal": 0,
                "title": "全部",
                "segment_ordinals": [0, 1],
                "source_languages": ["zh"],
            }
        ],
        "rejected": [],
    }


# --- Collection aggregation -------------------------------------------------


def _ref(part_id: str, ordinal: int) -> agg.SegmentRef:
    return agg.SegmentRef(part_id=part_id, ordinal=ordinal)


def _entry(*refs: agg.SegmentRef, text: str | None = None) -> agg.ProposedCollectionEntry:
    return agg.ProposedCollectionEntry(segment_refs=refs, text=text)


def test_collection_entry_may_cite_multiple_parts_retaining_identity() -> None:
    parts = [_available("part-a", count=2), _available("part-b", count=2)]
    proposed = [_entry(_ref("part-a", 0), _ref("part-b", 1), text="跨部概览")]

    summary = agg.aggregate_collection(parts, proposed)

    assert summary.rejected == ()
    assert len(summary.entries) == 1
    entry = summary.entries[0]
    assert entry.text == "跨部概览"
    # Each cited segment retains its own Part identity; Parts are never merged.
    assert entry.segment_refs == (_ref("part-a", 0), _ref("part-b", 1))
    assert entry.part_ids == ("part-a", "part-b")
    assert summary.part_ids == ("part-a", "part-b")


def test_collection_entry_citing_unknown_segment_is_rejected() -> None:
    parts = [_available("part-a", count=2)]

    summary = agg.aggregate_collection(parts, [_entry(_ref("part-a", 5))])

    assert summary.entries == ()
    assert _reasons(summary.rejected) == [agg.COLLECTION_ENTRY_UNKNOWN_SEGMENT]


def test_collection_entry_with_no_segments_is_rejected() -> None:
    parts = [_available("part-a", count=2)]

    summary = agg.aggregate_collection(parts, [_entry()])

    assert summary.entries == ()
    assert _reasons(summary.rejected) == [agg.COLLECTION_ENTRY_EMPTY]


def test_collection_entry_citing_unavailable_part_is_rejected() -> None:
    parts = [_available("part-a", count=2), _unavailable("part-z")]

    summary = agg.aggregate_collection(parts, [_entry(_ref("part-z", 0))])

    assert summary.entries == ()
    assert _reasons(summary.rejected) == [agg.COLLECTION_ENTRY_CITES_UNAVAILABLE_PART]


def test_unavailable_part_declares_omitted_range_and_reason() -> None:
    parts = [_available("part-a", count=2), _unavailable("part-z", start=100, end=250)]

    summary = agg.aggregate_collection(parts, [])

    assert len(summary.omitted_parts) == 1
    omitted = summary.omitted_parts[0]
    assert omitted.part_id == "part-z"
    assert omitted.reason == "no_primary_subtitle"
    assert omitted.virtual_time_range == HalfOpenInterval(ExactTime(100), ExactTime(250))
    # An unavailable Part invents no segment and lowers the collection to partial.
    assert summary.partial is True
    assert agg.TEXT_CONTENT_UNAVAILABLE in _reasons(summary.limitations)
    # Part identity of the omitted Part is retained in the ordered Part list.
    assert summary.part_ids == ("part-a", "part-z")


def test_conservative_fallback_part_is_preserved_as_limitation() -> None:
    parts = [_available("part-a", count=2, used_fallback=True)]

    summary = agg.aggregate_collection(parts, [])

    assert summary.partial is True
    assert agg.CONSERVATIVE_FALLBACK_LIMITATION in _reasons(summary.limitations)


def test_fully_available_collection_without_fallback_is_not_partial() -> None:
    parts = [_available("part-a", count=2), _available("part-b", count=3)]

    summary = agg.aggregate_collection(parts, [_entry(_ref("part-a", 0))])

    assert summary.partial is False
    assert summary.limitations == ()
    assert summary.omitted_parts == ()


def test_collection_summary_as_json_is_deterministic() -> None:
    parts = [_available("part-a", count=1), _unavailable("part-z", start=5, end=9)]

    document = agg.aggregate_collection(parts, [_entry(_ref("part-a", 0), text="概览")]).as_json()

    assert document == {
        "part_ids": ["part-a", "part-z"],
        "partial": True,
        "entries": [
            {
                "text": "概览",
                "segment_refs": [{"part_id": "part-a", "ordinal": 0}],
            }
        ],
        "omitted_parts": [
            {
                "part_id": "part-z",
                "reason": "no_primary_subtitle",
                "virtual_time_range": {
                    "start": {"numerator": 5, "denominator": 1},
                    "end": {"numerator": 9, "denominator": 1},
                },
            }
        ],
        "limitations": [
            {
                "reason": "text_content_unavailable",
                "message": document["limitations"][0]["message"],
            }
        ],
        "rejected": [],
    }


def test_duplicate_part_ids_are_a_caller_contract_violation() -> None:
    parts = [_available("part-a", count=1), _available("part-a", count=1)]

    with pytest.raises(agg.TextAggregationError) as excinfo:
        agg.aggregate_collection(parts, [])

    assert excinfo.value.reason == "duplicate_part_id"


def test_available_part_requires_at_least_one_segment() -> None:
    with pytest.raises(agg.TextAggregationError) as excinfo:
        agg.AvailablePart(part_id="part-a", segments=(), used_fallback=False)

    assert excinfo.value.reason == "invalid_available_part"


def test_available_part_rejects_out_of_order_or_foreign_segments() -> None:
    with pytest.raises(agg.TextAggregationError) as excinfo:
        agg.AvailablePart(
            part_id="part-a",
            segments=(_segment(1), _segment(0)),
            used_fallback=False,
        )

    assert excinfo.value.reason == "invalid_available_part"
