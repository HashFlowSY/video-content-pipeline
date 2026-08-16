"""Phase 10 ticket 06: the dedicated direct unit layer for ``subtitle_pipeline``.

Until this file existed the module's coverage was entirely *indirect* — nothing
imported it to exercise a single function in isolation. This audit enumerates
the module's public surface, records where each behavior is already pinned
indirectly, and then targets only the genuinely unpinned ones. The load-bearing
internal helpers named in the ticket (Primary-track selection, coverage/interval
derivation, source/readable derivation, atomic writes) are otherwise reachable
*only* through the full host-ffmpeg CLI path, so they are pinned here directly
rather than by re-running that integration path.

Public surface → prior coverage → this file
--------------------------------------------

Module functions:

* ``process_subtitles`` — happy/partial/blocked paths pinned end to end by
  ``tests/integration/test_phase_4_cli_contract.py``. Its exception→blocked
  contract (a bad plan yields a persisted BLOCKED report, never a raised error)
  is *newly* pinned here without ffmpeg.
* ``resume_subtitles`` — resume/revalidation/invalid-promotion pinned by the
  same integration module. Its exception→blocked contract is newly pinned here.
* ``subtitle_rules_fingerprint`` — only the happy path is touched indirectly
  (every ``process_subtitles`` run fingerprints the rules). Its rejection matrix
  (missing / non-JSON / non-object / wrong ``schema_version``) and determinism
  are *newly* pinned here.

Dataclass (de)serializers:

* ``SubtitleCandidate.as_json``/``.from_json`` and
  ``SubtitleCandidateReport.as_json``/``.from_json`` — round-trip equality and
  structured-mutation rejection are already pinned *generatively* by
  ``tests/property/test_serialization_roundtrip_properties.py`` (ticket 05).
  This file adds one concrete worked example of each as a readable regression
  anchor only; it does not re-derive the exhaustive rejection contract.
* ``CaptionTimeCoverage.as_json`` (ratio arithmetic), ``SubtitlePartReport.as_json``
  (null branches for every optional field), ``SubtitleTrackSelection.as_json``,
  and ``SubtitleReportError.reason`` — nested inside the round-trips above but
  never isolated. Newly pinned here as direct examples.

Load-bearing internal helpers (only reachable through host ffmpeg otherwise):

* Primary-track selection — ``_selected_candidate``, ``_ambiguous_source_ids``,
  ``_unresolved_ambiguous_source_ids``, ``_requires_asr_planning``. Newly pinned,
  including the mixed-language (two valid tracks on one Part) ambiguity rule.
* Interval/coverage derivation — ``_union_intervals``, ``_interval_duration``,
  ``_playback_coverage``. Newly pinned, including the empty-track, overlapping-cue
  and touching (rolling-overlap proof) boundary edges.
* Collection reporting — ``_collection_reporting`` end to end over an empty Part,
  a single auto-selected Part with overlapping cues, an ambiguous Part, and a
  multi-Part collection timeline. Newly pinned.
* Source/readable derivation — ``_source_format`` (codec→format matrix),
  ``_candidate_codec``, ``_write_candidate_artifacts`` (artifact-path derivation
  and atomicity; the *content* of the exports is already pinned by the phase-4
  integration module, so it is not re-asserted here).
* Atomic immutable writes — ``_write_json_once`` / ``_write_text_once``.
* Input parsing — ``_parse_selection``, ``_parse_decoders``, ``_validated_report_id``.

Enums (``CandidateState``/``CandidateReportState``/``SubtitlePartState``) and the
plain records (``RawPayloadEvidence``/``ExtractionFormat``/``CandidateArtifacts``)
carry no behavior beyond their fields and are exercised wherever the functions
above consume them.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from video_content_pipeline.coverage import CoverageDiagnostic, StreamCoverage
from video_content_pipeline.inspection import PlanInspectionEvidence, SubtitleTrackCandidate
from video_content_pipeline.planning import PlanningDiagnostic
from video_content_pipeline.probe import ProbeDocument
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.subtitle_pipeline import (
    CandidateReportState,
    CandidateState,
    CaptionTimeCoverage,
    ExtractionFormat,
    SubtitleCandidate,
    SubtitleCandidateReport,
    SubtitlePartReport,
    SubtitlePartState,
    SubtitleReportError,
    SubtitleTrackSelection,
    _ambiguous_source_ids,
    _candidate_codec,
    _collection_reporting,
    _interval_duration,
    _parse_decoders,
    _parse_selection,
    _playback_coverage,
    _requires_asr_planning,
    _selected_candidate,
    _source_format,
    _union_intervals,
    _unresolved_ambiguous_source_ids,
    _validated_report_id,
    _write_candidate_artifacts,
    _write_json_once,
    _write_text_once,
    process_subtitles,
    resume_subtitles,
    subtitle_rules_fingerprint,
)
from video_content_pipeline.subtitles import accept_subtitle_track
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval

# --------------------------------------------------------------------------- #
# Small factories                                                             #
# --------------------------------------------------------------------------- #


def _interval(start: int, end: int) -> HalfOpenInterval:
    return HalfOpenInterval(ExactTime(start), ExactTime(end))


def _candidate(
    source_id: str,
    stream_index: int,
    state: CandidateState,
    *,
    diagnostic: PlanningDiagnostic | None = None,
    cue_intervals: tuple[HalfOpenInterval, ...] = (),
) -> SubtitleCandidate:
    return SubtitleCandidate(
        source_id,
        stream_index,
        state,
        diagnostic=diagnostic,
        raw_pts_cue_intervals=cue_intervals,
    )


def _artifact(source_id: str) -> SourceArtifact:
    return SourceArtifact(source_id, "0" * 64, 1, Path("/nonexistent") / source_id)


def _coverage_evidence(source_id: str, coverage: HalfOpenInterval | None) -> PlanInspectionEvidence:
    coverage_by_stream: tuple[tuple[int, StreamCoverage], ...] = (
        ((0, StreamCoverage(coverage, (), ())),) if coverage is not None else ()
    )
    return PlanInspectionEvidence(
        source_id=source_id,
        structural_document=None,
        coverage_document=None,
        coverage_by_stream=coverage_by_stream,
        subtitle_tracks=(),
    )


def _structural_evidence(
    streams: list[dict[str, object]] | str | None,
) -> PlanInspectionEvidence:
    # ``None`` → no structural document; a ``str`` → a non-list ``streams`` value
    # for the rejection cases; a list → the well-formed stream table.
    document = None if streams is None else ProbeDocument(json.dumps({"streams": streams}))
    return PlanInspectionEvidence(
        source_id="part",
        structural_document=document,
        coverage_document=None,
        coverage_by_stream=(),
        subtitle_tracks=(),
    )


def _track_candidate(stream_index: int) -> SubtitleTrackCandidate:
    return SubtitleTrackCandidate(stream_index, "zh", "matroska", "embedded", True)


def _write_rules(project_root: Path, text: str) -> None:
    (project_root / "config").mkdir(parents=True, exist_ok=True)
    (project_root / "config" / "subtitle-rules.json").write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# subtitle_rules_fingerprint — validation + determinism                       #
# --------------------------------------------------------------------------- #


def test_fingerprint_is_deterministic_and_content_addressed(tmp_path: Path) -> None:
    body = '{"schema_version": 1, "id": "phase-04-subtitle-rules-v1"}\n'
    _write_rules(tmp_path, body)

    first = subtitle_rules_fingerprint(tmp_path)
    second = subtitle_rules_fingerprint(tmp_path)

    assert first == second
    # The fingerprint is over the raw bytes, so a semantically-equal reformat is
    # a different fingerprint — the byte content is the identity.
    _write_rules(tmp_path, '{"id": "phase-04-subtitle-rules-v1", "schema_version": 1}\n')
    assert subtitle_rules_fingerprint(tmp_path) != first


def test_fingerprint_rejects_a_missing_rules_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="subtitle_rules_invalid"):
        subtitle_rules_fingerprint(tmp_path)


@pytest.mark.parametrize(
    "body",
    [
        "not json at all",
        "[]",
        '{"schema_version": 2}',
        '{"id": "no-version"}',
    ],
)
def test_fingerprint_rejects_invalid_schema(tmp_path: Path, body: str) -> None:
    _write_rules(tmp_path, body)
    with pytest.raises(ValueError, match="subtitle_rules_invalid"):
        subtitle_rules_fingerprint(tmp_path)


# --------------------------------------------------------------------------- #
# process_subtitles / resume_subtitles — exception → persisted BLOCKED report #
# --------------------------------------------------------------------------- #


def test_process_subtitles_persists_a_blocked_report_for_a_missing_plan(tmp_path: Path) -> None:
    result = process_subtitles("no-such-plan", tmp_path)

    assert result["status"] == "blocked"
    report = result["report"]
    assert isinstance(report, dict)
    assert report["state"] == "blocked"
    assert report["diagnostics"][0]["reason"] == "run_plan_not_confirmed"
    # The blocked report is persisted immutably and re-reads to the same object.
    report_path = Path(str(report["report_path"]))
    assert report_path.is_file()
    reloaded = SubtitleCandidateReport.from_json(
        json.loads(report_path.read_text(encoding="utf-8")), report_path
    )
    assert reloaded.state is CandidateReportState.BLOCKED


def test_resume_subtitles_persists_a_blocked_report_for_a_missing_plan(tmp_path: Path) -> None:
    result = resume_subtitles("no-such-plan", uuid.uuid4().hex, (), tmp_path)

    assert result["status"] == "blocked"
    report = result["report"]
    assert isinstance(report, dict)
    assert report["state"] == "blocked"
    assert report["diagnostics"][0]["reason"] == "run_plan_not_confirmed"


# --------------------------------------------------------------------------- #
# Dataclass serializers — direct examples (round-trip exhaustiveness: ticket 05)#
# --------------------------------------------------------------------------- #


def test_subtitle_report_error_carries_a_machine_readable_reason() -> None:
    error = SubtitleReportError("subtitle_report_invalid", "human readable message")
    assert error.reason == "subtitle_report_invalid"
    assert str(error) == "human readable message"


def test_caption_time_coverage_reduces_its_ratio() -> None:
    assert CaptionTimeCoverage(ExactTime(1), ExactTime(3)).as_json()["ratio"] == {
        "numerator": 1,
        "denominator": 3,
    }
    # Zero displayed captions is a legitimate ratio, never a division error.
    assert CaptionTimeCoverage(ExactTime(0), ExactTime(4)).as_json()["ratio"] == {
        "numerator": 0,
        "denominator": 1,
    }
    assert CaptionTimeCoverage(ExactTime(5), ExactTime(5)).as_json()["ratio"] == {
        "numerator": 1,
        "denominator": 1,
    }


def test_part_report_as_json_nulls_every_absent_optional() -> None:
    document = SubtitlePartReport(
        source_id="part",
        state=SubtitlePartState.BLOCKED,
        selected_stream_index=None,
        collection_virtual_time=None,
        caption_time_coverage=None,
        risks=(),
    ).as_json()

    assert document["selected_stream_index"] is None
    assert document["collection_virtual_time"] is None
    assert document["caption_time_coverage"] is None
    assert document["asr_planning_handoff"] is None
    assert document["audio_completeness"] == "not_verified"
    assert document["risks"] == []


def test_part_report_as_json_renders_a_populated_part() -> None:
    document = SubtitlePartReport(
        source_id="part",
        state=SubtitlePartState.COMPLETED,
        selected_stream_index=2,
        collection_virtual_time=_interval(0, 10),
        caption_time_coverage=CaptionTimeCoverage(ExactTime(4), ExactTime(10)),
        risks=(),
        asr_planning_handoff=None,
    ).as_json()

    assert document["state"] == "completed"
    assert document["selected_stream_index"] == 2
    assert document["collection_virtual_time"] == {
        "start": {"numerator": 0, "denominator": 1},
        "end": {"numerator": 10, "denominator": 1},
    }


def test_selection_as_json_is_a_flat_pair() -> None:
    assert SubtitleTrackSelection("part", 3).as_json() == {
        "source_id": "part",
        "stream_index": 3,
    }


def test_subtitle_candidate_round_trips_a_worked_example() -> None:
    candidate = SubtitleCandidate(
        source_id="part",
        stream_index=1,
        state=CandidateState.VALID,
        source_format="srt",
        raw_payload_path="work/part/payload.srt",
        raw_payload_sha256="a" * 64,
        raw_payload_bytes=12,
        cue_count=2,
        coverage_start={"numerator": 0, "denominator": 1},
        attempt_id=uuid.uuid4().hex,
        codec="subrip",
        decoder="utf-8",
        raw_pts_cue_intervals=(_interval(0, 2), _interval(2, 4)),
    )
    assert SubtitleCandidate.from_json(candidate.as_json()) == candidate


def test_candidate_report_round_trips_a_worked_example() -> None:
    report_path = Path("work/part/candidate-report.json")
    report = SubtitleCandidateReport(
        report_id=uuid.uuid4().hex,
        plan_id="plan",
        state=CandidateReportState.COMPLETED,
        subtitle_rules_fingerprint="f" * 64,
        candidates=(_candidate("part", 1, CandidateState.VALID),),
        diagnostics=(),
        report_path=report_path,
        part_reports=(
            SubtitlePartReport(
                "part",
                SubtitlePartState.COMPLETED,
                1,
                _interval(0, 10),
                CaptionTimeCoverage(ExactTime(4), ExactTime(10)),
                (),
            ),
        ),
        caption_time_coverage=CaptionTimeCoverage(ExactTime(4), ExactTime(10)),
    )
    assert SubtitleCandidateReport.from_json(report.as_json(), report_path) == report


# --------------------------------------------------------------------------- #
# Primary-track selection rules                                               #
# --------------------------------------------------------------------------- #


def test_single_valid_candidate_is_auto_selected() -> None:
    candidates = [
        _candidate("part", 1, CandidateState.VALID),
        _candidate("part", 2, CandidateState.INVALID),
    ]
    # A lone valid track needs no explicit choice, even when one is offered.
    assert _selected_candidate(candidates, None) is candidates[0]
    assert _selected_candidate(candidates, 7) is candidates[0]


def test_ambiguous_candidates_require_the_matching_explicit_choice() -> None:
    valid_one = _candidate("part", 1, CandidateState.VALID)
    valid_two = _candidate("part", 2, CandidateState.VALID)
    candidates = [valid_one, valid_two]

    assert _selected_candidate(candidates, None) is None
    assert _selected_candidate(candidates, 2) is valid_two
    assert _selected_candidate(candidates, 9) is None


def test_no_valid_candidate_selects_nothing() -> None:
    candidates = [_candidate("part", 1, CandidateState.UNAVAILABLE)]
    assert _selected_candidate(candidates, None) is None
    assert _selected_candidate(candidates, 1) is None


def test_ambiguous_source_ids_flags_only_multi_valid_parts_sorted() -> None:
    candidates = [
        # "b" — two valid language tracks: mixed-language ambiguity.
        _candidate("b", 1, CandidateState.VALID),
        _candidate("b", 2, CandidateState.VALID),
        # "a" — two valid tracks as well; sorted output puts it first.
        _candidate("a", 1, CandidateState.VALID),
        _candidate("a", 2, CandidateState.VALID),
        # "c" — one valid track: never ambiguous.
        _candidate("c", 1, CandidateState.VALID),
        _candidate("c", 2, CandidateState.INVALID),
    ]
    assert _ambiguous_source_ids(candidates) == ("a", "b")
    assert _ambiguous_source_ids([]) == ()


def test_unresolved_ambiguous_source_ids_drops_selected_parts() -> None:
    candidates = (
        _candidate("a", 1, CandidateState.VALID),
        _candidate("a", 2, CandidateState.VALID),
        _candidate("b", 1, CandidateState.VALID),
        _candidate("b", 2, CandidateState.VALID),
    )
    selections = (SubtitleTrackSelection("a", 1),)
    assert _unresolved_ambiguous_source_ids(candidates, selections) == ("b",)


def test_requires_asr_planning_only_without_any_recoverable_track() -> None:
    assert _requires_asr_planning([_candidate("p", 1, CandidateState.UNAVAILABLE)]) is True
    assert _requires_asr_planning([]) is True
    assert _requires_asr_planning([_candidate("p", 1, CandidateState.VALID)]) is False
    assert _requires_asr_planning([_candidate("p", 1, CandidateState.ENCODING_AMBIGUOUS)]) is False


# --------------------------------------------------------------------------- #
# Interval / coverage derivation — empty, overlapping, touching boundaries    #
# --------------------------------------------------------------------------- #


def test_union_intervals_is_empty_for_no_input() -> None:
    assert _union_intervals(()) == ()


def test_union_intervals_merges_overlaps_and_touching_boundaries() -> None:
    # Overlapping [0,3)+[2,5) collapse; a touching pair [5,6) merges too because
    # the half-open end coincides with the next start — the rolling-overlap proof
    # boundary. A disjoint [8,9) stays separate. Input order is irrelevant.
    merged = _union_intervals((_interval(8, 9), _interval(2, 5), _interval(0, 3), _interval(5, 6)))
    assert merged == (_interval(0, 6), _interval(8, 9))


def test_interval_duration_dedups_overlap_and_is_zero_when_empty() -> None:
    assert _interval_duration(()) == ExactTime(0)
    # [0,3) and [2,5) overlap → union [0,5) → 5, not the naïve sum of 6.
    assert _interval_duration((_interval(0, 3), _interval(2, 5))) == ExactTime(5)
    # A touching pair [0,2)+[2,4) unions to [0,4) → 4.
    assert _interval_duration((_interval(0, 2), _interval(2, 4))) == ExactTime(4)


def test_playback_coverage_is_none_for_empty_or_undecidable_streams() -> None:
    assert _playback_coverage(()) is None
    unusable = (
        (0, StreamCoverage(None, (), ())),
        (1, StreamCoverage(_interval(0, 5), (), (CoverageDiagnostic("x", "p", "m"),))),
    )
    assert _playback_coverage(unusable) is None


def test_playback_coverage_unions_across_streams() -> None:
    coverage_by_stream = (
        (0, StreamCoverage(_interval(0, 5), (), ())),
        (1, StreamCoverage(_interval(3, 10), (), ())),
    )
    assert _playback_coverage(coverage_by_stream) == (_interval(0, 10),)


# --------------------------------------------------------------------------- #
# Collection reporting                                                        #
# --------------------------------------------------------------------------- #


def _plan_and_report(
    entries: list[tuple[str, HalfOpenInterval | None]],
) -> tuple[SimpleNamespace, SimpleNamespace]:
    artifacts = tuple(_artifact(source_id) for source_id, _ in entries)
    evidence = tuple(_coverage_evidence(source_id, coverage) for source_id, coverage in entries)
    return (
        SimpleNamespace(source_artifacts=artifacts),
        SimpleNamespace(inspection_evidence=evidence),
    )


def test_collection_reporting_hands_off_an_empty_part_to_asr() -> None:
    plan, report = _plan_and_report([("part", None)])

    part_reports, coverage, risks = _collection_reporting(plan, report, (), ())

    assert len(part_reports) == 1
    part = part_reports[0]
    assert part.state is SubtitlePartState.SUBTITLE_UNAVAILABLE_REQUIRES_ASR_PLAN
    assert part.asr_planning_handoff is not None
    assert part.collection_virtual_time is None
    assert coverage is None
    assert risks and risks[0].reason == "partial_subtitle_collection"


def test_collection_reporting_auto_selects_and_dedups_overlapping_cues() -> None:
    plan, report = _plan_and_report([("part", _interval(0, 10))])
    candidate = _candidate(
        "part",
        1,
        CandidateState.VALID,
        cue_intervals=(_interval(0, 3), _interval(2, 5)),
    )

    part_reports, coverage, risks = _collection_reporting(plan, report, (candidate,), ())

    part = part_reports[0]
    assert part.state is SubtitlePartState.COMPLETED
    assert part.selected_stream_index == 1
    assert part.collection_virtual_time == _interval(0, 10)
    assert part.caption_time_coverage is not None
    # Overlapping cue intervals union to [0,5) → 5 displayed of 10 playable.
    assert part.caption_time_coverage.covered_duration == ExactTime(5)
    assert part.caption_time_coverage.playback_duration == ExactTime(10)
    assert coverage is not None and coverage.covered_duration == ExactTime(5)
    assert risks == ()


def test_collection_reporting_marks_a_mixed_language_part_ambiguous() -> None:
    plan, report = _plan_and_report([("part", _interval(0, 10))])
    candidates = (
        _candidate("part", 1, CandidateState.VALID),
        _candidate("part", 2, CandidateState.VALID),
    )

    unresolved, _, _ = _collection_reporting(plan, report, candidates, ())
    assert unresolved[0].state is SubtitlePartState.AWAITING_SUBTITLE_SELECTION
    assert unresolved[0].selected_stream_index is None

    resolved, _, _ = _collection_reporting(
        plan, report, candidates, (SubtitleTrackSelection("part", 2),)
    )
    assert resolved[0].state is SubtitlePartState.COMPLETED
    assert resolved[0].selected_stream_index == 2


def test_collection_reporting_stacks_the_collection_timeline_across_parts() -> None:
    plan, report = _plan_and_report([("a", _interval(0, 10)), ("b", _interval(100, 105))])
    candidates = (
        _candidate("a", 1, CandidateState.VALID, cue_intervals=(_interval(0, 2),)),
        _candidate("b", 2, CandidateState.VALID, cue_intervals=(_interval(101, 103),)),
    )

    part_reports, coverage, _ = _collection_reporting(plan, report, candidates, ())

    part_a, part_b = part_reports
    # Part B is virtually placed immediately after Part A's playable duration.
    assert part_a.collection_virtual_time == _interval(0, 10)
    assert part_b.collection_virtual_time == _interval(10, 15)
    assert coverage is not None
    assert coverage.covered_duration == ExactTime(4)  # 2 per part, disjoint
    assert coverage.playback_duration == ExactTime(15)


# --------------------------------------------------------------------------- #
# Source / readable derivation                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("codec", "expected"),
    [
        ("subrip", ExtractionFormat("srt", "copy", "srt")),
        ("srt", ExtractionFormat("srt", "copy", "srt")),
        ("webvtt", ExtractionFormat("vtt", "copy", "webvtt")),
        ("mov_text", ExtractionFormat("srt", "srt", "srt")),
    ],
)
def test_source_format_maps_supported_codecs(codec: str, expected: ExtractionFormat) -> None:
    evidence = _structural_evidence([{"index": 1, "codec_type": "subtitle", "codec_name": codec}])
    assert _source_format(evidence, _track_candidate(1)) == expected


@pytest.mark.parametrize(
    "evidence",
    [
        _structural_evidence([{"index": 1, "codec_type": "subtitle", "codec_name": "dvd_sub"}]),
        _structural_evidence([{"index": 9, "codec_type": "subtitle", "codec_name": "subrip"}]),
        _structural_evidence(None),
        _structural_evidence("not-a-list"),
    ],
)
def test_source_format_returns_none_for_unsupported_or_absent(
    evidence: PlanInspectionEvidence,
) -> None:
    assert _source_format(evidence, _track_candidate(1)) is None


def test_source_format_returns_none_on_malformed_structural_json() -> None:
    evidence = PlanInspectionEvidence(
        source_id="part",
        structural_document=ProbeDocument("{not json"),
        coverage_document=None,
        coverage_by_stream=(),
        subtitle_tracks=(),
    )
    assert _source_format(evidence, _track_candidate(1)) is None


def test_candidate_codec_reports_the_named_codec_or_none() -> None:
    evidence = _structural_evidence(
        [{"index": 1, "codec_type": "subtitle", "codec_name": "webvtt"}]
    )
    assert _candidate_codec(evidence, _track_candidate(1)) == "webvtt"
    assert _candidate_codec(evidence, _track_candidate(2)) is None


def test_write_candidate_artifacts_derives_four_named_exports(tmp_path: Path) -> None:
    source = "1\n00:00:00,000 --> 00:00:02,000\nHello\n"
    track = accept_subtitle_track(
        source,
        "srt",
        part_id="part",
        track_id="stream-1",
        coverage=_interval(0, 5),
    )
    assert track.valid
    raw_payload = tmp_path / "stream-1.payload.srt"
    raw_payload.write_text(source, encoding="utf-8")

    artifacts = _write_candidate_artifacts(raw_payload, track)

    assert Path(artifacts.source_vtt_path).name == "stream-1.source.vtt"
    assert Path(artifacts.source_srt_path).name == "stream-1.source.srt"
    assert Path(artifacts.readable_vtt_path).name == "stream-1.readable.vtt"
    assert Path(artifacts.readable_corrections_path).name == "stream-1.readable.corrections.json"
    for path in (
        artifacts.source_vtt_path,
        artifacts.source_srt_path,
        artifacts.readable_vtt_path,
        artifacts.readable_corrections_path,
    ):
        assert Path(path).is_file()
    # Re-deriving identical artifacts is idempotent, never a conflict.
    _write_candidate_artifacts(raw_payload, track)


# --------------------------------------------------------------------------- #
# Atomic immutable writes                                                     #
# --------------------------------------------------------------------------- #


def test_write_json_once_is_write_once_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "record.json"
    _write_json_once(path, {"b": 1, "a": 2})
    # Keys are sorted and the file ends with a newline.
    assert path.read_text(encoding="utf-8") == '{\n  "a": 2,\n  "b": 1\n}\n'
    # An identical re-write is a silent no-op.
    _write_json_once(path, {"a": 2, "b": 1})


def test_write_json_once_refuses_to_mutate_a_retained_record(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    _write_json_once(path, {"a": 1})
    with pytest.raises(ValueError, match="subtitle_report_conflict"):
        _write_json_once(path, {"a": 2})


def test_write_text_once_is_write_once_and_refuses_mutation(tmp_path: Path) -> None:
    path = tmp_path / "export.vtt"
    _write_text_once(path, "WEBVTT\n")
    _write_text_once(path, "WEBVTT\n")
    with pytest.raises(ValueError, match="subtitle_artifact_conflict"):
        _write_text_once(path, "WEBVTT changed\n")


# --------------------------------------------------------------------------- #
# Input parsing                                                               #
# --------------------------------------------------------------------------- #


def test_parse_selection_accepts_a_part_stream_pair() -> None:
    assert _parse_selection("part=2") == SubtitleTrackSelection("part", 2)
    assert _parse_selection("part=0") == SubtitleTrackSelection("part", 0)


@pytest.mark.parametrize("value", ["part", "=2", "part=", "part=-1", "part=x"])
def test_parse_selection_rejects_malformed_pairs(value: str) -> None:
    with pytest.raises(SubtitleReportError) as raised:
        _parse_selection(value)
    assert raised.value.reason == "subtitle_selection_invalid"


def test_parse_decoders_canonicalizes_and_keys_by_part_and_stream() -> None:
    assert _parse_decoders(("part=1=utf-8",)) == {("part", 1): "utf-8"}


def test_parse_decoders_rejects_duplicate_targets() -> None:
    with pytest.raises(SubtitleReportError) as raised:
        _parse_decoders(("part=1=utf-8", "part=1=latin-1"))
    assert raised.value.reason == "subtitle_decoder_duplicate"


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("part=1", "subtitle_decoder_invalid"),
        ("part=x=utf-8", "subtitle_decoder_invalid"),
        ("part=-1=utf-8", "subtitle_decoder_invalid"),
        ("part=1=no-such-encoding", "subtitle_decoder_invalid"),
        ("=1=utf-8", "subtitle_decoder_invalid"),
    ],
)
def test_parse_decoders_rejects_malformed_specs(value: str, reason: str) -> None:
    with pytest.raises(SubtitleReportError) as raised:
        _parse_decoders((value,))
    assert raised.value.reason == reason


def test_validated_report_id_requires_a_uuid() -> None:
    generated = uuid.uuid4().hex
    assert _validated_report_id(generated) == generated
    with pytest.raises(SubtitleReportError) as raised:
        _validated_report_id("not-a-uuid")
    assert raised.value.reason == "subtitle_report_invalid"
