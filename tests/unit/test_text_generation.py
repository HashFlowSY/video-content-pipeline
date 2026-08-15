"""Unit contract for Phase 6 ticket 08's controlled generation orchestration.

``text_generation`` is the Controlled offline text adapter and the deterministic
orchestration that composes the ticket 04 boundary adjudicator, the ticket 05
content validator, and the ticket 06 aggregator into one auditable
``GeneratedAnalysis``. These tests assert the composition rules directly, without
the CLI or the immutable workspace: authoritative cue inventories come from
retained subtitle evidence, model-proposed structure is adjudicated and validated
against that evidence, and every rejected boundary, unsupported item, and
limitation is retained rather than lost.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from video_content_pipeline import text_generation as tg
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval


def _write_source_candidate(path: Path, texts: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cues": [
                    {
                        "source_ordinal": ordinal,
                        "text": text,
                        "raw_pts_interval": {
                            "start": {"numerator": ordinal, "denominator": 1},
                            "end": {"numerator": ordinal + 1, "denominator": 1},
                        },
                    }
                    for ordinal, text in enumerate(texts)
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_load_cue_inventory_synthesizes_part_local_cue_ids(tmp_path: Path) -> None:
    candidate = tmp_path / "source-candidate.json"
    _write_source_candidate(candidate, ["你好", "world"])

    part = tg.load_cue_inventory(candidate, part_id="source-a", stream_index=1)

    assert part.part_id == "source-a"
    assert part.track_id == "stream-1"
    assert part.cue_ids == ("source-a:stream-1:0", "source-a:stream-1:1")


def test_load_cue_inventory_rejects_a_malformed_candidate(tmp_path: Path) -> None:
    candidate = tmp_path / "source-candidate.json"
    candidate.write_text(json.dumps({"schema_version": 2, "cues": []}) + "\n", encoding="utf-8")

    try:
        tg.load_cue_inventory(candidate, part_id="source-a", stream_index=1)
    except tg.TextGenerationError as error:
        assert error.reason == "cue_inventory_invalid"
    else:  # pragma: no cover - the loader must reject a bad schema
        raise AssertionError("loader accepted a malformed candidate")


def _part(part_id: str, cue_count: int) -> tg.LoadedPart:
    return tg.LoadedPart(
        part_id=part_id,
        track_id="stream-1",
        cue_ids=tuple(f"{part_id}:stream-1:{ordinal}" for ordinal in range(cue_count)),
    )


def _cue(part_id: str, ordinal: int) -> str:
    return f"{part_id}:stream-1:{ordinal}"


def test_generation_produces_complete_status_with_verified_content() -> None:
    part = _part("p1", 3)
    result = {
        "parts": [
            {
                "part_id": "p1",
                "segments": [
                    {
                        "boundary": {
                            "start_cue_id": _cue("p1", 0),
                            "end_cue_id": _cue("p1", 1),
                        },
                        "content": {
                            "title": {"text": "开场", "cue_ids": [_cue("p1", 0)]},
                            "detailed_content": [{"text": "细节", "cue_ids": [_cue("p1", 1)]}],
                        },
                    },
                    {
                        "boundary": {
                            "start_cue_id": _cue("p1", 2),
                            "end_cue_id": _cue("p1", 2),
                        },
                        "content": {
                            "title": {"text": "结尾", "cue_ids": [_cue("p1", 2)]},
                        },
                    },
                ],
                "chapters": [{"start_ordinal": 0, "end_ordinal": 1, "title": "全部"}],
            }
        ],
        "collection_summary": {
            "entries": [{"segment_refs": [{"part_id": "p1", "ordinal": 0}], "text": "摘要"}]
        },
    }

    analysis = tg.generate_analysis([part], [], result)

    assert analysis.status == "complete"
    assert [segment.ordinal for segment in analysis.segments] == [0, 1]
    assert analysis.segments[0].origin == "adjudicated"
    assert analysis.segments[0].content.title is not None
    assert analysis.segments[0].content.title.text == "开场"
    assert analysis.segments[0].cue_ids == (_cue("p1", 0), _cue("p1", 1))
    assert len(analysis.chapters) == 1
    assert analysis.chapters[0].segment_ordinals == (0, 1)
    assert analysis.collection_summary is not None
    assert len(analysis.collection_summary.entries) == 1
    assert analysis.unsupported_item_count == 0


def test_out_of_segment_citation_is_pruned_without_failing_the_segment() -> None:
    part = _part("p1", 2)
    result = {
        "parts": [
            {
                "part_id": "p1",
                "segments": [
                    {
                        "boundary": {
                            "start_cue_id": _cue("p1", 0),
                            "end_cue_id": _cue("p1", 1),
                        },
                        "content": {
                            "title": {"text": "标题", "cue_ids": [_cue("p1", 0)]},
                            "numeric_values": [
                                # cites a cue outside the whole Part -> unsupported.
                                {"text": "999", "cue_ids": [_cue("p1", 9)]}
                            ],
                        },
                    }
                ],
                "chapters": [],
            }
        ],
        "collection_summary": None,
    }

    analysis = tg.generate_analysis([part], [], result)

    assert analysis.status == "complete"
    segment = analysis.segments[0]
    assert segment.content.title is not None
    assert segment.content.details == ()
    assert analysis.unsupported_item_count == 1
    assert any(
        diagnostic.reason == "unsupported_generated_claim"
        for diagnostic in segment.content.diagnostics
    )


def test_coverage_breaking_boundaries_fall_back_to_partial() -> None:
    part = _part("p1", 3)
    result = {
        "parts": [
            {
                "part_id": "p1",
                "segments": [
                    {
                        # A single boundary that leaves cue 2 uncovered -> no exact tiling.
                        "boundary": {
                            "start_cue_id": _cue("p1", 0),
                            "end_cue_id": _cue("p1", 1),
                        },
                        "content": {"title": {"text": "唯一", "cue_ids": [_cue("p1", 2)]}},
                    }
                ],
                "chapters": [],
            }
        ],
        "collection_summary": None,
    }

    analysis = tg.generate_analysis([part], [], result)

    assert analysis.status == "partial"
    assert len(analysis.segments) == 1
    assert analysis.segments[0].origin == "conservative_fallback"
    # The fallback segment owns every cue, so a cue-2 citation is now in-segment.
    assert analysis.segments[0].content.title is not None


def test_unavailable_part_lowers_status_and_declares_omitted_range() -> None:
    part = _part("p1", 1)
    unavailable = tg.UnavailablePartInfo(
        part_id="p2",
        reason="no_valid_primary_track",
        virtual_time_range=HalfOpenInterval(ExactTime(0), ExactTime(5)),
    )
    result = {
        "parts": [
            {
                "part_id": "p1",
                "segments": [
                    {
                        "boundary": {
                            "start_cue_id": _cue("p1", 0),
                            "end_cue_id": _cue("p1", 0),
                        },
                        "content": {"title": {"text": "仅有", "cue_ids": [_cue("p1", 0)]}},
                    }
                ],
                "chapters": [],
            }
        ],
        "collection_summary": {"entries": []},
    }

    analysis = tg.generate_analysis([part], [unavailable], result)

    assert analysis.status == "partial"
    assert analysis.collection_summary is not None
    omitted = analysis.collection_summary.omitted_parts
    assert [item.part_id for item in omitted] == ["p2"]
    assert analysis.collection_summary.partial is True


def test_generate_part_regenerates_one_part_through_the_same_adjudication() -> None:
    # generate_part is the per-Part unit re-analysis reuses; it must produce the
    # same segments, chapters, and fallback flag generate_analysis records for one
    # available Part, so a regenerated affected Part obeys the Phase 6 contracts.
    part = _part("p1", 3)
    part_result = {
        "part_id": "p1",
        "segments": [
            {
                "boundary": {"start_cue_id": _cue("p1", 0), "end_cue_id": _cue("p1", 1)},
                "content": {"title": {"text": "开场", "cue_ids": [_cue("p1", 0)]}},
                "source_languages": ["en", "zh"],
            },
            {
                "boundary": {"start_cue_id": _cue("p1", 2), "end_cue_id": _cue("p1", 2)},
                "content": {"title": {"text": "结尾", "cue_ids": [_cue("p1", 2)]}},
            },
        ],
        "chapters": [{"start_ordinal": 0, "end_ordinal": 1, "title": "全部"}],
    }
    result = {"parts": [part_result], "collection_summary": None}

    generation = tg.generate_part(part, part_result)
    whole = tg.generate_analysis([part], [], result)

    assert generation.part_id == "p1"
    assert generation.used_fallback is False
    assert [segment.ordinal for segment in generation.segments] == [0, 1]
    assert generation.segments[0].source_languages == ("en", "zh")
    assert generation.available_part.ordinals == {0, 1}
    assert [chapter.segment_ordinals for chapter in generation.chapters] == [(0, 1)]
    # Every cue is owned exactly once across the regenerated Part's segments.
    owned = [cue for segment in generation.segments for cue in segment.cue_ids]
    assert owned == [_cue("p1", 0), _cue("p1", 1), _cue("p1", 2)]
    # The per-Part unit matches the whole-analysis path for the same Part.
    assert [segment.as_json() for segment in generation.segments] == [
        segment.as_json() for segment in whole.segments
    ]


def test_generate_part_falls_back_to_one_segment_when_no_boundary_tiles() -> None:
    part = _part("p1", 3)
    part_result = {
        "part_id": "p1",
        "segments": [
            {
                # Leaves cue 2 uncovered, so no exact tiling and the fallback owns all cues.
                "boundary": {"start_cue_id": _cue("p1", 0), "end_cue_id": _cue("p1", 1)},
                "content": {"title": {"text": "唯一", "cue_ids": [_cue("p1", 2)]}},
            }
        ],
        "chapters": [],
    }

    generation = tg.generate_part(part, part_result)

    assert generation.used_fallback is True
    assert len(generation.segments) == 1
    assert generation.segments[0].origin == "conservative_fallback"
    assert generation.available_part.used_fallback is True


def test_load_controlled_generation_is_absent_without_a_generation_block(tmp_path: Path) -> None:
    assert tg.load_controlled_generation({"version": "x"}, tmp_path, "deadbeef") is None


def test_load_controlled_generation_reads_a_hash_pinned_fixture(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "config" / "text-analysis" / "fixtures"
    fixture_dir.mkdir(parents=True)
    fixture_path = fixture_dir / "output.json"
    raw = json.dumps({"result": {"parts": []}}, sort_keys=True).encode("utf-8")
    fixture_path.write_bytes(raw)
    adapter = {
        "generation": {
            "output_fixture_path": "config/text-analysis/fixtures/output.json",
            "output_fixture_sha256": sha256(raw).hexdigest(),
            "input_fixture_sha256": "abc123",
        }
    }

    generation = tg.load_controlled_generation(adapter, tmp_path, "abc123")

    assert generation is not None
    assert generation.raw_output == raw
    assert generation.input_fixture_sha256 == "abc123"


def test_load_controlled_generation_rejects_a_tampered_fixture(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "config" / "text-analysis" / "fixtures"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "output.json").write_bytes(b"{}")
    adapter = {
        "generation": {
            "output_fixture_path": "config/text-analysis/fixtures/output.json",
            "output_fixture_sha256": "0" * 64,
            "input_fixture_sha256": "abc123",
        }
    }

    try:
        tg.load_controlled_generation(adapter, tmp_path, "abc123")
    except tg.TextGenerationError as error:
        assert error.reason == "controlled_generation_invalid"
    else:  # pragma: no cover - the loader must reject a tampered fixture
        raise AssertionError("loader accepted a tampered fixture")


def test_load_controlled_generation_rejects_a_path_escape(tmp_path: Path) -> None:
    adapter = {
        "generation": {
            "output_fixture_path": "../escape.json",
            "output_fixture_sha256": "0" * 64,
            "input_fixture_sha256": "abc123",
        }
    }

    try:
        tg.load_controlled_generation(adapter, tmp_path, "abc123")
    except tg.TextGenerationError as error:
        assert error.reason == "controlled_generation_invalid"
    else:  # pragma: no cover - the loader must reject a path escape
        raise AssertionError("loader accepted a path escape")


def test_generation_fails_when_no_available_part_has_a_segment() -> None:
    unavailable = tg.UnavailablePartInfo(
        part_id="p2",
        reason="no_valid_primary_track",
        virtual_time_range=HalfOpenInterval(ExactTime(0), ExactTime(5)),
    )

    analysis = tg.generate_analysis([], [unavailable], {"parts": [], "collection_summary": None})

    assert analysis.status == "failed"
    assert analysis.segments == ()
