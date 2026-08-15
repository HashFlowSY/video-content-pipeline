"""Unit coverage for Phase 7 ticket 08 retained-report loading and affected-Part selection.

ADR 0046 recomputes semantic analysis at Part granularity after transcription or
enhancement changes the cue evidence basis: affected Parts are regenerated and
unaffected Parts are carried forward from a retained prior report. This ticket
supplies the two deterministic text-analysis capabilities that recomputation
needs -- a loader that deserializes a retained ``text-analysis-report.json`` back
into the aggregation domain objects with hash verification, and an affected-Part
selector keyed on changed cue identities.

The tests assert deterministic contract properties -- reconstructed Part, chapter,
and collection identities; the retained report is never mutated by loading;
structured diagnostics for unloadable or drifted reports; and the exact
affected/unaffected classification of a new cue basis -- never prose quality.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline import text_reanalysis as reanalysis
from video_content_pipeline.text_aggregation import AvailablePart, Chapter, OmittedPart
from video_content_pipeline.text_contracts import render_text_analysis_markdown


def _segment(
    part_id: str,
    ordinal: int,
    cue_ids: tuple[str, ...],
    *,
    origin: str = "adjudicated",
    source_languages: tuple[str, ...] = ("zh",),
) -> dict[str, object]:
    return {
        "part_id": part_id,
        "ordinal": ordinal,
        "origin": origin,
        "cue_ids": list(cue_ids),
        "source_languages": list(source_languages),
        "title": None,
        "details": [],
        "questions_and_answers": [],
        "people": [],
        "contradictions": [],
        "unresolved_questions": [],
        "content_diagnostics": [],
    }


def _cue(part_id: str, ordinal: int) -> str:
    return f"{part_id}:stream-1:{ordinal}"


def _report_document(
    *,
    report_id: str = "00000000000000000000000000000001",
    status: str = "complete",
    segments: list[dict[str, object]] | None = None,
    chapters: list[dict[str, object]] | None = None,
    collection_summary: dict[str, object] | None = None,
    diagnostics: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Build a minimally valid retained report and bind its rendition hash.

    The rendition hash is computed exactly as production does -- from the report
    content before ``rendered_report`` is populated -- so the loader's drift check
    passes for an untampered report and fails for a tampered one.
    """

    if segments is None:
        segments = [
            _segment("part-a", 0, (_cue("part-a", 0), _cue("part-a", 1))),
            _segment("part-a", 1, (_cue("part-a", 2),)),
        ]
    document: dict[str, object] = {
        "report_id": report_id,
        "plan_id": "plan-1",
        "subtitle_report_id": "sub-1",
        "status": status,
        "audio_completeness": "not_verified",
        "segments": segments,
        "chapters": chapters if chapters is not None else [],
        "collection_summary": collection_summary,
        "unsupported_item_count": 0,
        "diagnostics": diagnostics if diagnostics is not None else [],
        "required_decision": None,
        "rendered_report": None,
    }
    rendition = render_text_analysis_markdown(document)
    document["rendered_report"] = rendition.as_json()
    return document


def _write_report(tmp_path: Path, document: dict[str, object]) -> Path:
    report_id = str(document["report_id"])
    workspace = tmp_path / "work" / "text-analysis-reports" / report_id
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "text-analysis-report.json"
    path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


# --- Loader: reconstruction --------------------------------------------------


def test_load_reconstructs_available_parts_with_ordered_cue_identities(tmp_path: Path) -> None:
    document = _report_document(
        segments=[
            _segment("part-a", 0, (_cue("part-a", 0), _cue("part-a", 1)), source_languages=("zh",)),
            _segment("part-a", 1, (_cue("part-a", 2),), source_languages=("en", "zh")),
            _segment("part-b", 0, (_cue("part-b", 0),)),
        ]
    )
    path = _write_report(tmp_path, document)

    loaded = reanalysis.load_text_analysis_report(path)

    assert loaded.report_id == document["report_id"]
    assert loaded.plan_id == "plan-1"
    assert loaded.subtitle_report_id == "sub-1"
    assert loaded.status == "complete"
    assert [part.part.part_id for part in loaded.parts] == ["part-a", "part-b"]
    part_a = loaded.parts[0]
    assert isinstance(part_a.part, AvailablePart)
    assert part_a.part.part_id == "part-a"
    assert len(part_a.part.segments) == 2
    # Cue identities are reconstructed in Part order with exactly-once ownership.
    assert part_a.cue_ids == (_cue("part-a", 0), _cue("part-a", 1), _cue("part-a", 2))
    assert loaded.part_cue_bases == {
        "part-a": (_cue("part-a", 0), _cue("part-a", 1), _cue("part-a", 2)),
        "part-b": (_cue("part-b", 0),),
    }


def test_load_records_conservative_fallback_and_source_languages(tmp_path: Path) -> None:
    document = _report_document(
        segments=[
            _segment(
                "part-a",
                0,
                (_cue("part-a", 0),),
                origin="conservative_fallback",
                source_languages=("en", "zh"),
            )
        ]
    )
    path = _write_report(tmp_path, document)

    loaded = reanalysis.load_text_analysis_report(path)

    assert loaded.parts[0].part.used_fallback is True
    assert loaded.parts[0].part.segments[0].source_languages == ("en", "zh")


def test_load_reconstructs_chapters_and_collection_summary(tmp_path: Path) -> None:
    chapters = [
        Chapter(
            part_id="part-a",
            ordinal=0,
            title="话题",
            segment_ordinals=(0, 1),
            source_languages=("zh",),
        ).as_json()
    ]
    collection_summary = {
        "part_ids": ["part-a", "part-z"],
        "partial": True,
        "entries": [
            {"text": "摘要", "segment_refs": [{"part_id": "part-a", "ordinal": 0}]},
        ],
        "omitted_parts": [
            {
                "part_id": "part-z",
                "reason": "no_primary_subtitle",
                "virtual_time_range": {
                    "start": {"numerator": 0, "denominator": 1},
                    "end": {"numerator": 10, "denominator": 1},
                },
            }
        ],
        "limitations": [{"reason": "text_content_unavailable", "message": "part-z omitted"}],
        "rejected": [],
    }
    document = _report_document(chapters=chapters, collection_summary=collection_summary)
    path = _write_report(tmp_path, document)

    loaded = reanalysis.load_text_analysis_report(path)

    assert len(loaded.chapters) == 1
    assert isinstance(loaded.chapters[0], Chapter)
    assert loaded.chapters[0].segment_ordinals == (0, 1)
    assert loaded.collection_summary is not None
    assert loaded.collection_summary.part_ids == ("part-a", "part-z")
    assert all(isinstance(omitted, OmittedPart) for omitted in loaded.omitted_parts)
    assert [omitted.part_id for omitted in loaded.omitted_parts] == ["part-z"]
    assert loaded.omitted_parts[0].reason == "no_primary_subtitle"


def test_load_binds_hash_pinned_provenance_and_never_mutates_the_report(tmp_path: Path) -> None:
    document = _report_document()
    path = _write_report(tmp_path, document)
    original_bytes = path.read_bytes()

    loaded = reanalysis.load_text_analysis_report(path)

    assert loaded.source_evidence.path == path
    assert loaded.source_evidence.byte_count == len(original_bytes)
    from hashlib import sha256

    assert loaded.source_evidence.sha256 == sha256(original_bytes).hexdigest()
    # Loading is read-only: the retained report is byte-for-byte unchanged.
    assert path.read_bytes() == original_bytes


# --- Loader: unloadable and drifted diagnostics ------------------------------


def test_load_rejects_an_unreadable_report(tmp_path: Path) -> None:
    missing = tmp_path / "work" / "text-analysis-reports" / "x" / "text-analysis-report.json"
    with pytest.raises(reanalysis.TextReanalysisError) as excinfo:
        reanalysis.load_text_analysis_report(missing)
    assert excinfo.value.reason == "text_analysis_report_unloadable"


def test_load_rejects_malformed_json(tmp_path: Path) -> None:
    workspace = tmp_path / "work" / "text-analysis-reports" / "malformed"
    workspace.mkdir(parents=True)
    path = workspace / "text-analysis-report.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(reanalysis.TextReanalysisError) as excinfo:
        reanalysis.load_text_analysis_report(path)
    assert excinfo.value.reason == "text_analysis_report_unloadable"


def test_load_rejects_a_report_missing_its_rendition_hash(tmp_path: Path) -> None:
    document = _report_document()
    document["rendered_report"] = None
    path = _write_report(tmp_path, document)
    with pytest.raises(reanalysis.TextReanalysisError) as excinfo:
        reanalysis.load_text_analysis_report(path)
    assert excinfo.value.reason == "text_analysis_report_unloadable"


def test_load_rejects_a_non_loadable_status(tmp_path: Path) -> None:
    document = _report_document(status="failed", segments=[])
    path = _write_report(tmp_path, document)
    with pytest.raises(reanalysis.TextReanalysisError) as excinfo:
        reanalysis.load_text_analysis_report(path)
    assert excinfo.value.reason == "text_analysis_report_not_loadable"


def test_load_detects_a_drifted_rendition_hash(tmp_path: Path) -> None:
    document = _report_document()
    # Tamper with a summarized field after the rendition hash was bound.
    document["status"] = "partial"
    path = _write_report(tmp_path, document)
    with pytest.raises(reanalysis.TextReanalysisError) as excinfo:
        reanalysis.load_text_analysis_report(path)
    assert excinfo.value.reason == "text_analysis_report_drifted"


def test_load_rejects_non_contiguous_segment_ordinals(tmp_path: Path) -> None:
    document = _report_document(
        segments=[
            _segment("part-a", 0, (_cue("part-a", 0),)),
            _segment("part-a", 2, (_cue("part-a", 2),)),
        ]
    )
    path = _write_report(tmp_path, document)
    with pytest.raises(reanalysis.TextReanalysisError) as excinfo:
        reanalysis.load_text_analysis_report(path)
    assert excinfo.value.reason == "text_analysis_report_drifted"


def test_load_rejects_duplicated_cue_ownership(tmp_path: Path) -> None:
    document = _report_document(
        segments=[
            _segment("part-a", 0, (_cue("part-a", 0),)),
            _segment("part-a", 1, (_cue("part-a", 0),)),
        ]
    )
    path = _write_report(tmp_path, document)
    with pytest.raises(reanalysis.TextReanalysisError) as excinfo:
        reanalysis.load_text_analysis_report(path)
    assert excinfo.value.reason == "text_analysis_report_drifted"


def test_load_rejects_a_chapter_citing_an_unknown_segment(tmp_path: Path) -> None:
    chapters = [
        Chapter(
            part_id="part-a",
            ordinal=0,
            title=None,
            segment_ordinals=(0, 5),
            source_languages=("zh",),
        ).as_json()
    ]
    document = _report_document(chapters=chapters)
    path = _write_report(tmp_path, document)
    with pytest.raises(reanalysis.TextReanalysisError) as excinfo:
        reanalysis.load_text_analysis_report(path)
    assert excinfo.value.reason == "text_analysis_report_drifted"


def test_load_rejects_a_non_string_cue_identity_rather_than_dropping_it(tmp_path: Path) -> None:
    # A malformed cue identity must fail the load, never be silently filtered out:
    # dropping it would silently alter the reconstructed cue basis the selector keys on.
    segment = _segment("part-a", 0, ())
    segment["cue_ids"] = [_cue("part-a", 0), 7]
    document = _report_document(segments=[segment])
    path = _write_report(tmp_path, document)
    with pytest.raises(reanalysis.TextReanalysisError) as excinfo:
        reanalysis.load_text_analysis_report(path)
    assert excinfo.value.reason == "text_analysis_report_drifted"


def test_load_rejects_a_malformed_chapter_ordinal_rather_than_dropping_it(tmp_path: Path) -> None:
    chapter = Chapter(
        part_id="part-a",
        ordinal=0,
        title=None,
        segment_ordinals=(0,),
        source_languages=("zh",),
    ).as_json()
    chapter["segment_ordinals"] = [0, -1]
    document = _report_document(chapters=[chapter])
    path = _write_report(tmp_path, document)
    with pytest.raises(reanalysis.TextReanalysisError) as excinfo:
        reanalysis.load_text_analysis_report(path)
    assert excinfo.value.reason == "text_analysis_report_drifted"


# --- Affected-Part selection -------------------------------------------------


def test_select_marks_a_part_with_unchanged_cue_identities_unaffected() -> None:
    prior = {"part-a": (_cue("part-a", 0), _cue("part-a", 1))}
    new_basis = {"part-a": (_cue("part-a", 0), _cue("part-a", 1))}

    selection = reanalysis.select_affected_parts(prior, new_basis)

    assert selection.unaffected == ("part-a",)
    assert selection.affected == ()
    assert selection.classification("part-a").reason == "cue_identities_unchanged"


def test_select_marks_changed_and_reordered_cue_identities_affected() -> None:
    prior = {
        "part-a": (_cue("part-a", 0), _cue("part-a", 1)),
        "part-b": (_cue("part-b", 0), _cue("part-b", 1)),
    }
    new_basis = {
        # part-a gains a cue; part-b keeps the same cues reordered.
        "part-a": (_cue("part-a", 0), _cue("part-a", 1), _cue("part-a", 2)),
        "part-b": (_cue("part-b", 1), _cue("part-b", 0)),
    }

    selection = reanalysis.select_affected_parts(prior, new_basis)

    assert selection.affected == ("part-a", "part-b")
    assert selection.unaffected == ()
    assert selection.classification("part-a").reason == "cue_identities_changed"
    assert selection.classification("part-b").reason == "cue_identities_changed"


def test_select_classifies_added_and_removed_parts_as_affected() -> None:
    prior = {"part-a": (_cue("part-a", 0),), "part-b": (_cue("part-b", 0),)}
    new_basis = {"part-a": (_cue("part-a", 0),), "part-c": (_cue("part-c", 0),)}

    selection = reanalysis.select_affected_parts(prior, new_basis)

    assert selection.unaffected == ("part-a",)
    assert selection.affected == ("part-b", "part-c")
    assert selection.classification("part-b").reason == "part_removed"
    assert selection.classification("part-c").reason == "part_added"


def test_select_is_deterministic_and_serializable() -> None:
    prior = {"part-b": (_cue("part-b", 0),), "part-a": (_cue("part-a", 0),)}
    new_basis = {"part-a": (_cue("part-a", 1),), "part-b": (_cue("part-b", 0),)}

    selection = reanalysis.select_affected_parts(prior, new_basis)
    payload = selection.as_json()

    # Classifications are ordered by Part identity for a stable, auditable record.
    assert [entry["part_id"] for entry in payload["classifications"]] == ["part-a", "part-b"]
    assert payload["affected"] == ["part-a"]
    assert payload["unaffected"] == ["part-b"]


def test_select_from_loaded_report_uses_its_cue_bases(tmp_path: Path) -> None:
    document = _report_document(
        segments=[
            _segment("part-a", 0, (_cue("part-a", 0),)),
            _segment("part-b", 0, (_cue("part-b", 0),)),
        ]
    )
    path = _write_report(tmp_path, document)
    loaded = reanalysis.load_text_analysis_report(path)

    selection = reanalysis.select_affected_parts(
        loaded.part_cue_bases,
        {"part-a": (_cue("part-a", 0),), "part-b": (_cue("part-b", 9),)},
    )

    assert selection.unaffected == ("part-a",)
    assert selection.affected == ("part-b",)
