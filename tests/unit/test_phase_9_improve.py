"""Ticket 11 unit contract: improvement-run scope grammar, plan derivation, and
carry-forward selection, proven offline.

These exercises drive the pure pieces of :mod:`video_content_pipeline.improve`
directly: the ``--asr`` grammar mapping to the retained enhancement scope
choices, the derivation of a new confirmed plan from a source plan, and the
selection of unaffected-Part artifacts carried forward from a published manifest
with recorded source-run provenance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from video_content_pipeline.improve import (
    ImproveError,
    build_improvement_plan,
    carried_forward_artifacts,
    improvement_run_choices,
    parse_asr_scope,
)
from video_content_pipeline.planning import RunPlan, calculate_disk_headroom, load_run_plan
from video_content_pipeline.publication import ManifestArtifact, RunBundleManifest
from video_content_pipeline.publication_projection import (
    ArtifactKind,
    ArtifactStatus,
    TimingBasis,
    TimingView,
)
from video_content_pipeline.run_choices import (
    COLLECTION_SCOPE,
    KEY_ASR_MODE,
    KEY_ENHANCEMENT_PART,
    KEY_ENHANCEMENT_RANGE,
    KEY_SUBTITLE_TRACK,
    KEY_VISUAL_TEXT_ENABLED,
    STAGE_ENHANCEMENT,
    STAGE_RUN,
    STAGE_SUBTITLES,
    AsrMode,
    ChoiceProvenance,
    RunChoice,
    RunPlanChoices,
)
from video_content_pipeline.run_state import RunStatus
from video_content_pipeline.source import SourceArtifact

_PART_A = "a" * 64
_PART_B = "b" * 64
_SOURCE_RUN = "20260816T090000Z-0123456789abcdef"


def _source_plan() -> RunPlan:
    choices = RunPlanChoices.build(
        (
            RunChoice(
                STAGE_RUN,
                KEY_ASR_MODE,
                COLLECTION_SCOPE,
                AsrMode.FULL_ASR.value,
                ChoiceProvenance.USER_CHOSEN,
            ),
            RunChoice(
                STAGE_RUN,
                KEY_VISUAL_TEXT_ENABLED,
                COLLECTION_SCOPE,
                "false",
                ChoiceProvenance.USER_CHOSEN,
            ),
            RunChoice(
                STAGE_SUBTITLES,
                KEY_SUBTITLE_TRACK,
                _PART_A,
                "1",
                ChoiceProvenance.USER_CHOSEN,
            ),
        )
    )
    return RunPlan(
        plan_id="src0123456789abcdef0123x",
        report_id="0" * 32,
        source_artifacts=(
            SourceArtifact(
                source_id=_PART_A,
                sha256=_PART_A,
                byte_count=1,
                media_path=Path("input") / _PART_A / "m",
            ),
            SourceArtifact(
                source_id=_PART_B,
                sha256=_PART_B,
                byte_count=1,
                media_path=Path("input") / _PART_B / "m",
            ),
        ),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint="cfg" + "0" * 61,
        run_choices=choices,
    )


# --- Scope grammar ----------------------------------------------------------


def test_asr_scope_all_targets_every_part() -> None:
    choices, affected = parse_asr_scope("all", (_PART_A, _PART_B))
    assert affected == frozenset({_PART_A, _PART_B})
    assert {c.value for c in choices} == {_PART_A, _PART_B}
    assert all(c.stage == STAGE_ENHANCEMENT and c.key == KEY_ENHANCEMENT_PART for c in choices)


def test_asr_scope_single_part() -> None:
    choices, affected = parse_asr_scope(_PART_B, (_PART_A, _PART_B))
    assert affected == frozenset({_PART_B})
    assert choices == (
        RunChoice(
            STAGE_ENHANCEMENT,
            KEY_ENHANCEMENT_PART,
            COLLECTION_SCOPE,
            _PART_B,
            ChoiceProvenance.USER_CHOSEN,
        ),
    )


def test_asr_scope_range_maps_to_enhancement_range() -> None:
    choices, affected = parse_asr_scope(f"{_PART_A}:1.0-2.0", (_PART_A, _PART_B))
    assert affected == frozenset({_PART_A})
    assert choices == (
        RunChoice(
            STAGE_ENHANCEMENT,
            KEY_ENHANCEMENT_RANGE,
            _PART_A,
            "1.0-2.0",
            ChoiceProvenance.USER_CHOSEN,
        ),
    )


def test_asr_scope_rejects_empty() -> None:
    with pytest.raises(ImproveError) as caught:
        parse_asr_scope("", (_PART_A,))
    assert caught.value.reason == "improve_scope_missing"


def test_asr_scope_rejects_unknown_part() -> None:
    with pytest.raises(ImproveError) as caught:
        parse_asr_scope("c" * 64, (_PART_A, _PART_B))
    assert caught.value.reason == "improve_unknown_part"


def test_asr_scope_rejects_malformed_range() -> None:
    with pytest.raises(ImproveError) as caught:
        parse_asr_scope(f"{_PART_A}:nonsense", (_PART_A,))
    assert caught.value.reason == "improve_scope_invalid"


# --- Improvement choices ----------------------------------------------------


def test_improvement_choices_flip_mode_and_keep_other_selections() -> None:
    scope_choices, _ = parse_asr_scope(_PART_B, (_PART_A, _PART_B))
    choices = improvement_run_choices(_source_plan().run_choices, scope_choices)

    assert choices.asr_mode() is AsrMode.ENHANCEMENT
    # The retained subtitle-track and visual toggle selections are carried over.
    assert choices.values(STAGE_SUBTITLES, KEY_SUBTITLE_TRACK, _PART_A)
    assert choices.visual_text_enabled() is False
    # The enhancement scope is exactly the requested one.
    parts = choices.values(STAGE_ENHANCEMENT, KEY_ENHANCEMENT_PART)
    assert tuple(c.value for c in parts) == (_PART_B,)


# --- Improvement plan -------------------------------------------------------


def test_build_improvement_plan_persists_a_new_distinct_plan(tmp_path: Path) -> None:
    source = _source_plan()
    plans_root = tmp_path / "plans"
    plan, affected = build_improvement_plan(source, _SOURCE_RUN, _PART_B, plans_root)

    assert affected == frozenset({_PART_B})
    assert plan.plan_id != source.plan_id
    assert plan.run_choices.asr_mode() is AsrMode.ENHANCEMENT
    assert plan.source_artifacts == source.source_artifacts
    # Persisted at plans/<new-plan-id>/run-plan.json and reloadable.
    reloaded = load_run_plan(plans_root / plan.plan_id / "run-plan.json")
    assert reloaded == plan


def test_build_improvement_plan_is_deterministic(tmp_path: Path) -> None:
    source = _source_plan()
    first, _ = build_improvement_plan(source, _SOURCE_RUN, _PART_B, tmp_path / "plans")
    # The same source run and scope address the same content-addressed plan id.
    second, _ = build_improvement_plan(source, _SOURCE_RUN, _PART_B, tmp_path / "plans")
    assert first.plan_id == second.plan_id


# --- Carry-forward selection ------------------------------------------------


def _artifact(
    path: str, kind: str, status: ArtifactStatus, sha: str | None, *, provenance=None
) -> ManifestArtifact:
    return ManifestArtifact(
        path=path,
        kind=kind,
        status=status,
        sha256=sha,
        timing_view=TimingView.PART_RELATIVE if path.startswith("parts/") else None,
        timing_basis=TimingBasis.ORIGINAL if path.startswith("parts/") else None,
        provenance=provenance or {},
    )


def _manifest(artifacts: tuple[ManifestArtifact, ...]) -> RunBundleManifest:
    return RunBundleManifest(
        source_id="source",
        run_id=_SOURCE_RUN,
        run_status=RunStatus.COMPLETE,
        projection_stage_version=1,
        artifacts=artifacts,
        plan_id="src0123456789abcdef0123x",
    )


def test_carry_forward_selects_only_unaffected_part_content(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "parts" / _PART_A).mkdir(parents=True)
    (bundle / "parts" / _PART_B).mkdir(parents=True)
    (bundle / "parts" / _PART_A / "subtitles.enhanced.srt").write_text("A", encoding="utf-8")
    (bundle / "parts" / _PART_B / "subtitles.enhanced.srt").write_text("B", encoding="utf-8")

    manifest = _manifest(
        (
            _artifact(
                f"parts/{_PART_A}/subtitles.enhanced.srt",
                ArtifactKind.SUBTITLES.value,
                ArtifactStatus.VALID,
                "hash-a",
                provenance={"per_cue": "x"},
            ),
            _artifact(
                f"parts/{_PART_B}/subtitles.enhanced.srt",
                ArtifactKind.SUBTITLES.value,
                ArtifactStatus.VALID,
                "hash-b",
            ),
            # A collection-level artifact and a document are never carried forward.
            _artifact(
                "transcript.enhanced.md",
                ArtifactKind.TRANSCRIPT.value,
                ArtifactStatus.VALID,
                "hash-t",
            ),
            _artifact("processing-report.md", "document", ArtifactStatus.VALID, "hash-doc"),
        )
    )

    carried = carried_forward_artifacts(manifest, bundle, frozenset({_PART_B}), _SOURCE_RUN)

    assert [a.path for a in carried] == [f"parts/{_PART_A}/subtitles.enhanced.srt"]
    only = carried[0]
    assert only.content == "A"
    assert only.sha256 == "hash-a"
    assert only.timing_view is TimingView.PART_RELATIVE
    assert only.provenance["per_cue"] == "x"
    assert only.provenance["carried_forward_from_run"] == _SOURCE_RUN
    assert only.provenance["carried_forward_sha256"] == "hash-a"


def test_carry_forward_skips_unavailable_and_affected(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "parts" / _PART_A).mkdir(parents=True)
    (bundle / "parts" / _PART_A / "subtitles.enhanced.srt").write_text("A", encoding="utf-8")

    manifest = _manifest(
        (
            # Unavailable per-Part entry (no bytes) is not a formal output.
            _artifact(
                f"parts/{_PART_B}/subtitles.enhanced.srt",
                ArtifactKind.SUBTITLES.value,
                ArtifactStatus.UNAVAILABLE,
                None,
            ),
            _artifact(
                f"parts/{_PART_A}/subtitles.enhanced.srt",
                ArtifactKind.SUBTITLES.value,
                ArtifactStatus.VALID,
                "hash-a",
            ),
        )
    )

    # Part A is affected here, so nothing is carried forward.
    carried = carried_forward_artifacts(manifest, bundle, frozenset({_PART_A}), _SOURCE_RUN)
    assert carried == ()
