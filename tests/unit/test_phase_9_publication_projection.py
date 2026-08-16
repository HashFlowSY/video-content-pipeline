from __future__ import annotations

from pathlib import Path

import pytest

from video_content_pipeline.planning import RunPlan, calculate_disk_headroom
from video_content_pipeline.publication_projection import (
    PUBLICATION_PROJECTION_STAGE_VERSION,
    ArtifactStatus,
    PlainArtifactEvidence,
    ProjectedArtifact,
    ProjectionEvidence,
    ProjectionInvalidationKey,
    ProjectionResult,
    PublicationBasis,
    PublicationProjectionError,
    TimedArtifactEvidence,
    TimingBasis,
    TimingView,
    expected_subtitle_bases,
    project_publication,
    projection_invalidation_key,
    transcript_basis,
)
from video_content_pipeline.run_choices import (
    COLLECTION_SCOPE,
    KEY_ASR_MODE,
    KEY_VISUAL_TEXT_ENABLED,
    STAGE_RUN,
    AsrMode,
    ChoiceProvenance,
    RunChoice,
    RunPlanChoices,
)
from video_content_pipeline.source import SourceArtifact

_PART_A = "a" * 64
_PART_B = "b" * 64
_PLAN_ID = "plan0123456789abcdef0123"
_CONFIG = "cfg" + "0" * 61


def _choice(stage: str, key: str, scope: str, value: str) -> RunChoice:
    return RunChoice(
        stage=stage,
        key=key,
        scope=scope,
        value=value,
        provenance=ChoiceProvenance.USER_CHOSEN,
    )


def _mode_choices(mode: AsrMode) -> RunPlanChoices:
    return RunPlanChoices.build(
        (
            _choice(STAGE_RUN, KEY_ASR_MODE, COLLECTION_SCOPE, mode.value),
            _choice(STAGE_RUN, KEY_VISUAL_TEXT_ENABLED, COLLECTION_SCOPE, "false"),
        )
    )


def _artifact(content_hash: str) -> SourceArtifact:
    return SourceArtifact(
        source_id=content_hash,
        sha256=content_hash,
        byte_count=1,
        media_path=Path("input") / content_hash / "media",
    )


def _plan(mode: AsrMode, *parts: str) -> RunPlan:
    return RunPlan(
        plan_id=_PLAN_ID,
        report_id="0" * 32,
        source_artifacts=tuple(_artifact(part) for part in (parts or (_PART_A,))),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=_CONFIG,
        run_choices=_mode_choices(mode),
    )


def _timed(
    *,
    original: str | None = "ORIGINAL",
    adopted: str | None = None,
    gates: bool = False,
    partial: bool = False,
    invalid: bool = False,
    provenance: dict[str, object] | None = None,
) -> TimedArtifactEvidence:
    return TimedArtifactEvidence(
        original=original,
        adopted_alignment=adopted,
        adopted_gates_passed=gates,
        partial=partial,
        invalid=invalid,
        provenance=provenance or {},
    )


def _full_evidence(
    plan: RunPlan,
    *,
    subtitles: dict[tuple[str, PublicationBasis], TimedArtifactEvidence] | None = None,
    collection_subtitles: dict[PublicationBasis, TimedArtifactEvidence] | None = None,
    transcript: TimedArtifactEvidence | None = None,
    content_report: PlainArtifactEvidence | None = None,
    segments: PlainArtifactEvidence | None = None,
    correction_log: PlainArtifactEvidence | None = None,
) -> ProjectionEvidence:
    """Build evidence that makes every expected artifact available by default."""

    mode = plan.run_choices.asr_mode()
    assert mode is not None
    bases = expected_subtitle_bases(mode)
    parts = tuple(artifact.source_id for artifact in plan.source_artifacts)
    part_subs: dict[tuple[str, PublicationBasis], TimedArtifactEvidence] = {}
    coll_subs: dict[PublicationBasis, TimedArtifactEvidence] = {}
    for basis in bases:
        coll_subs[basis] = _timed(original=f"COLL-{basis.value}")
        for part in parts:
            part_subs[(part, basis)] = _timed(original=f"{part[:4]}-{basis.value}")
    return ProjectionEvidence(
        part_subtitles=subtitles if subtitles is not None else part_subs,
        collection_subtitles=(
            collection_subtitles if collection_subtitles is not None else coll_subs
        ),
        collection_transcript=(
            transcript if transcript is not None else _timed(original="TRANSCRIPT")
        ),
        content_report=(
            content_report
            if content_report is not None
            else PlainArtifactEvidence(content="# 报告")
        ),
        segments=segments if segments is not None else PlainArtifactEvidence(content="[]"),
        correction_log=(
            correction_log if correction_log is not None else PlainArtifactEvidence(content="[]")
        ),
    )


# --- Mode mapping -----------------------------------------------------------


def test_subtitle_first_bases_are_source_and_readable() -> None:
    assert expected_subtitle_bases(AsrMode.SUBTITLE_FIRST) == (
        PublicationBasis.SOURCE,
        PublicationBasis.READABLE,
    )
    assert transcript_basis(AsrMode.SUBTITLE_FIRST) is PublicationBasis.SOURCE


def test_full_asr_bases_are_verbatim_and_readable() -> None:
    assert expected_subtitle_bases(AsrMode.FULL_ASR) == (
        PublicationBasis.VERBATIM,
        PublicationBasis.READABLE,
    )
    assert transcript_basis(AsrMode.FULL_ASR) is PublicationBasis.VERBATIM


def test_enhancement_bases_are_enhanced_only() -> None:
    assert expected_subtitle_bases(AsrMode.ENHANCEMENT) == (PublicationBasis.ENHANCED,)
    assert transcript_basis(AsrMode.ENHANCEMENT) is PublicationBasis.ENHANCED


def test_subtitle_first_never_fabricates_other_mode_artifacts() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    result = project_publication(plan, _full_evidence(plan))
    paths = {artifact.path for artifact in result.artifacts}
    assert not any("verbatim" in path for path in paths)
    assert not any("enhanced" in path for path in paths)
    assert "subtitles.source.vtt" in paths
    assert "subtitles.readable.srt" in paths


def test_full_asr_never_fabricates_source_or_enhanced() -> None:
    plan = _plan(AsrMode.FULL_ASR, _PART_A)
    result = project_publication(plan, _full_evidence(plan))
    paths = {artifact.path for artifact in result.artifacts}
    assert not any("source" in path for path in paths)
    assert not any("enhanced" in path for path in paths)
    assert "transcript.verbatim.json" in paths


# --- Layout: parts vs collection --------------------------------------------


def test_part_subtitles_land_under_parts_dir_with_part_relative_time() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A, _PART_B)
    result = project_publication(plan, _full_evidence(plan))
    part_a_vtt = _by_path(result, f"parts/{_PART_A}/subtitles.source.vtt")
    assert part_a_vtt.timing_view is TimingView.PART_RELATIVE
    part_b_vtt = _by_path(result, f"parts/{_PART_B}/subtitles.readable.vtt")
    assert part_b_vtt.timing_view is TimingView.PART_RELATIVE


def test_collection_artifacts_use_collection_virtual_time() -> None:
    plan = _plan(AsrMode.FULL_ASR, _PART_A, _PART_B)
    result = project_publication(plan, _full_evidence(plan))
    coll = _by_path(result, "subtitles.verbatim.vtt")
    assert coll.timing_view is TimingView.COLLECTION_VIRTUAL
    transcript = _by_path(result, "transcript.verbatim.json")
    assert transcript.timing_view is TimingView.COLLECTION_VIRTUAL


def test_collection_only_documents_exist_once() -> None:
    plan = _plan(AsrMode.FULL_ASR, _PART_A, _PART_B)
    result = project_publication(plan, _full_evidence(plan))
    paths = [artifact.path for artifact in result.artifacts]
    assert paths.count("content-report.md") == 1
    assert paths.count("segments.json") == 1
    assert paths.count("correction-log.json") == 1


# --- Timing basis (ADR 0026) ------------------------------------------------


def test_adopted_alignment_used_only_when_gates_pass() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    subs = {
        (_PART_A, PublicationBasis.SOURCE): _timed(original="ORIG", adopted="ALIGNED", gates=True),
        (_PART_A, PublicationBasis.READABLE): _timed(
            original="ORIG", adopted="ALIGNED", gates=False
        ),
    }
    result = project_publication(plan, _full_evidence(plan, subtitles=subs))
    aligned = _by_path(result, f"parts/{_PART_A}/subtitles.source.vtt")
    assert aligned.timing_basis is TimingBasis.ADOPTED_ALIGNMENT
    assert aligned.content == "ALIGNED"
    original = _by_path(result, f"parts/{_PART_A}/subtitles.readable.vtt")
    assert original.timing_basis is TimingBasis.ORIGINAL
    assert original.content == "ORIG"


def test_adopted_missing_falls_back_to_original_even_if_gates_pass() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    subs = {
        (_PART_A, PublicationBasis.SOURCE): _timed(original="ORIG", adopted=None, gates=True),
        (_PART_A, PublicationBasis.READABLE): _timed(original="ORIG", adopted=None, gates=True),
    }
    result = project_publication(plan, _full_evidence(plan, subtitles=subs))
    artifact = _by_path(result, f"parts/{_PART_A}/subtitles.source.srt")
    assert artifact.timing_basis is TimingBasis.ORIGINAL
    assert artifact.content == "ORIG"


def test_plain_documents_have_no_timing_basis() -> None:
    plan = _plan(AsrMode.FULL_ASR, _PART_A)
    result = project_publication(plan, _full_evidence(plan))
    report = _by_path(result, "content-report.md")
    assert report.timing_basis is None
    assert report.timing_view is None
    segments = _by_path(result, "segments.json")
    assert segments.timing_basis is None
    assert segments.timing_view is TimingView.COLLECTION_VIRTUAL


# --- Availability and status ------------------------------------------------


def test_missing_evidence_yields_unavailable_entry_without_content() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    evidence = _full_evidence(plan, transcript=_timed(original=None, adopted=None))
    result = project_publication(plan, evidence)
    transcript = _by_path(result, "transcript.source.md")
    assert transcript.status is ArtifactStatus.UNAVAILABLE
    assert transcript.content is None
    assert transcript.sha256 is None
    # An unavailable artifact still appears in the manifest entries.
    assert any(
        entry["path"] == "transcript.source.md" and entry["status"] == "unavailable"
        for entry in result.manifest_entries()
    )


def test_partial_evidence_is_recorded_as_partial() -> None:
    plan = _plan(AsrMode.FULL_ASR, _PART_A, _PART_B)
    evidence = _full_evidence(plan, segments=PlainArtifactEvidence(content="[1]", partial=True))
    result = project_publication(plan, evidence)
    segments = _by_path(result, "segments.json")
    assert segments.status is ArtifactStatus.PARTIAL
    assert segments.content == "[1]"


def test_invalid_evidence_is_recorded_as_invalid_with_hash() -> None:
    plan = _plan(AsrMode.FULL_ASR, _PART_A)
    evidence = _full_evidence(plan, transcript=_timed(original="BROKEN", invalid=True))
    result = project_publication(plan, evidence)
    transcript = _by_path(result, "transcript.verbatim.md")
    assert transcript.status is ArtifactStatus.INVALID
    assert transcript.sha256 is not None


def test_available_evidence_is_valid() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    result = project_publication(plan, _full_evidence(plan))
    assert all(artifact.status is ArtifactStatus.VALID for artifact in result.artifacts)


# --- Provenance (enhancement) -----------------------------------------------


def test_enhanced_subtitles_carry_per_cue_provenance() -> None:
    plan = _plan(AsrMode.ENHANCEMENT, _PART_A)
    subs = {
        (_PART_A, PublicationBasis.ENHANCED): _timed(
            original="ENH", provenance={"per_cue": True, "asr_cues": 3}
        ),
    }
    coll = {PublicationBasis.ENHANCED: _timed(original="ENH", provenance={"per_cue": True})}
    evidence = _full_evidence(plan, subtitles=subs, collection_subtitles=coll)
    result = project_publication(plan, evidence)
    enhanced = _by_path(result, f"parts/{_PART_A}/subtitles.enhanced.vtt")
    assert enhanced.provenance == {"per_cue": True, "asr_cues": 3}


# --- Determinism ------------------------------------------------------------


def test_projection_is_byte_identical_for_the_same_inputs() -> None:
    plan = _plan(AsrMode.FULL_ASR, _PART_A, _PART_B)
    first = project_publication(plan, _full_evidence(plan))
    second = project_publication(plan, _full_evidence(plan))
    assert first.digest() == second.digest()
    assert [a.path for a in first.artifacts] == [a.path for a in second.artifacts]
    assert [a.content for a in first.artifacts] == [a.content for a in second.artifacts]


def test_artifacts_are_emitted_in_sorted_path_order() -> None:
    plan = _plan(AsrMode.FULL_ASR, _PART_A, _PART_B)
    result = project_publication(plan, _full_evidence(plan))
    paths = [artifact.path for artifact in result.artifacts]
    assert paths == sorted(paths)


def test_content_change_changes_the_digest() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    base = project_publication(plan, _full_evidence(plan))
    changed_evidence = _full_evidence(plan, content_report=PlainArtifactEvidence(content="# 不同"))
    changed = project_publication(plan, changed_evidence)
    assert base.digest() != changed.digest()


# --- Stage version and invalidation -----------------------------------------


def test_result_records_the_projection_stage_version() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    result = project_publication(plan, _full_evidence(plan))
    assert result.stage_version == PUBLICATION_PROJECTION_STAGE_VERSION


def test_invalidation_key_carries_projection_version() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    key = projection_invalidation_key(plan, _full_evidence(plan))
    assert isinstance(key, ProjectionInvalidationKey)
    assert key.stage_version == PUBLICATION_PROJECTION_STAGE_VERSION


def test_invalidation_key_changes_with_evidence_but_not_between_identical_runs() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    same = projection_invalidation_key(plan, _full_evidence(plan))
    again = projection_invalidation_key(plan, _full_evidence(plan))
    assert same.digest() == again.digest()
    changed = projection_invalidation_key(
        plan, _full_evidence(plan, content_report=PlainArtifactEvidence(content="X"))
    )
    assert same.digest() != changed.digest()


def test_invalidation_key_distinguishes_different_part_sets() -> None:
    # Two plans differing only in their Part set, with no evidence at all, must
    # not collide: the artifact set (and thus the projection) differs even
    # though the evidence fingerprint is identical.
    one_part = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    two_parts = _plan(AsrMode.SUBTITLE_FIRST, _PART_A, _PART_B)
    empty = ProjectionEvidence()
    assert (
        projection_invalidation_key(one_part, empty).digest()
        != projection_invalidation_key(two_parts, empty).digest()
    )


def test_invalidation_key_round_trips_through_json() -> None:
    plan = _plan(AsrMode.FULL_ASR, _PART_A)
    key = projection_invalidation_key(plan, _full_evidence(plan))
    restored = ProjectionInvalidationKey.from_json(key.as_json())
    assert restored == key
    assert restored.digest() == key.digest()


# --- Guards -----------------------------------------------------------------


def test_missing_asr_mode_is_an_error() -> None:
    plan = RunPlan(
        plan_id=_PLAN_ID,
        report_id="0" * 32,
        source_artifacts=(_artifact(_PART_A),),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=_CONFIG,
        run_choices=RunPlanChoices.build(()),
    )
    with pytest.raises(PublicationProjectionError) as excinfo:
        project_publication(plan, _full_evidence(_plan(AsrMode.SUBTITLE_FIRST, _PART_A)))
    assert excinfo.value.reason == "missing_asr_mode"


def _by_path(result: ProjectionResult, path: str) -> ProjectedArtifact:
    for artifact in result.artifacts:
        if artifact.path == path:
            return artifact
    raise AssertionError(f"no projected artifact at {path!r}")
