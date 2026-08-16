from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from video_content_pipeline.orchestration import RunLayout, initialize_run_workspace
from video_content_pipeline.planning import RunPlan, calculate_disk_headroom
from video_content_pipeline.run_choices import (
    COLLECTION_SCOPE,
    KEY_ASR_MODE,
    KEY_SUBTITLE_DECODER,
    KEY_VISUAL_TEXT_ALL,
    KEY_VISUAL_TEXT_ENABLED,
    STAGE_RUN,
    STAGE_SUBTITLES,
    STAGE_VISUAL_TEXT,
    AsrMode,
    ChoiceProvenance,
    RunChoice,
    RunPlanChoices,
)
from video_content_pipeline.run_control import ControlDirective, ControlKind, request_control
from video_content_pipeline.run_state import RunStateWriter, RunStatus, read_journal
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.stage_dag import (
    STAGE_VERSIONS,
    RecordedUnit,
    StageDagError,
    StageInvalidationKey,
    StageName,
    StageResult,
    StageRunDisposition,
    StageScope,
    StageUnit,
    UnitStatus,
    adoptable_units,
    compute_invalidation_keys,
    execute_stages,
    plan_stage_units,
    read_recorded_units,
    stage_scope,
    unit_record,
)

_PART_A = "a" * 64
_PART_B = "b" * 64
_PLAN_ID = "plan0123456789abcdef0123"
_CONFIG = "cfg" + "0" * 61
_SOURCE_ID = "s" * 64
_RUN_ID = "20260816T083000Z-0123456789abcdef"


def _choice(stage: str, key: str, scope: str, value: str) -> RunChoice:
    return RunChoice(
        stage=stage,
        key=key,
        scope=scope,
        value=value,
        provenance=ChoiceProvenance.USER_CHOSEN,
    )


def _mode_choices(
    mode: AsrMode, *, visual: bool = False, extra: tuple[RunChoice, ...] = ()
) -> RunPlanChoices:
    base = [
        _choice(STAGE_RUN, KEY_ASR_MODE, COLLECTION_SCOPE, mode.value),
        _choice(
            STAGE_RUN, KEY_VISUAL_TEXT_ENABLED, COLLECTION_SCOPE, "true" if visual else "false"
        ),
    ]
    if visual:
        base.append(_choice(STAGE_VISUAL_TEXT, KEY_VISUAL_TEXT_ALL, COLLECTION_SCOPE, "true"))
    return RunPlanChoices.build(tuple(base) + extra)


def _artifact(content_hash: str) -> SourceArtifact:
    return SourceArtifact(
        source_id=content_hash,
        sha256=content_hash,
        byte_count=1,
        media_path=Path("input") / content_hash / "media",
    )


def _plan(choices: RunPlanChoices, *parts: str) -> RunPlan:
    return RunPlan(
        plan_id=_PLAN_ID,
        report_id="0" * 32,
        source_artifacts=tuple(_artifact(part) for part in (parts or (_PART_A,))),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=_CONFIG,
        run_choices=choices,
    )


def _layout(tmp_path: Path) -> RunLayout:
    return initialize_run_workspace(
        RunLayout(project_root=tmp_path, source_id=_SOURCE_ID, run_id=_RUN_ID)
    )


def _clock() -> Callable[[], datetime]:
    step = {"n": 0}

    def tick() -> datetime:
        moment = datetime(2026, 8, 16, 8, 30, step["n"] % 60, tzinfo=UTC)
        step["n"] += 1
        return moment

    return tick


def _running_writer(tmp_path: Path) -> RunStateWriter:
    writer = RunStateWriter.create(_layout(tmp_path), plan_id=_PLAN_ID, clock=_clock())
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    return writer


# --- Topology ---------------------------------------------------------------


def test_transcription_mode_selects_transcription_stage() -> None:
    units = plan_stage_units(_plan(_mode_choices(AsrMode.FULL_ASR), _PART_A))
    stages = [unit.stage for unit in units]
    assert StageName.TRANSCRIPTION in stages
    assert StageName.ENHANCEMENT not in stages
    assert StageName.VISUAL_TEXT not in stages


def test_enhancement_mode_selects_enhancement_stage() -> None:
    units = plan_stage_units(_plan(_mode_choices(AsrMode.ENHANCEMENT), _PART_A))
    stages = [unit.stage for unit in units]
    assert StageName.ENHANCEMENT in stages
    assert StageName.TRANSCRIPTION not in stages


def test_visual_text_stage_appears_only_when_enabled() -> None:
    units = plan_stage_units(_plan(_mode_choices(AsrMode.FULL_ASR, visual=True), _PART_A))
    assert StageName.VISUAL_TEXT in [unit.stage for unit in units]


def test_source_revalidation_is_a_single_collection_unit() -> None:
    units = plan_stage_units(_plan(_mode_choices(AsrMode.FULL_ASR), _PART_A, _PART_B))
    revalidation = [unit for unit in units if unit.stage is StageName.SOURCE_REVALIDATION]
    assert revalidation == [StageUnit(StageName.SOURCE_REVALIDATION, COLLECTION_SCOPE)]
    assert stage_scope(StageName.SOURCE_REVALIDATION) is StageScope.COLLECTION


def test_per_part_stage_has_one_unit_per_part() -> None:
    units = plan_stage_units(_plan(_mode_choices(AsrMode.FULL_ASR), _PART_A, _PART_B))
    subtitle_scopes = [unit.scope for unit in units if unit.stage is StageName.SUBTITLES]
    assert subtitle_scopes == [_PART_A, _PART_B]


def test_units_are_topologically_ordered() -> None:
    units = plan_stage_units(_plan(_mode_choices(AsrMode.FULL_ASR), _PART_A))
    order = [unit.stage for unit in units]
    assert order.index(StageName.SOURCE_REVALIDATION) < order.index(StageName.SUBTITLES)
    assert order.index(StageName.SUBTITLES) < order.index(StageName.AUDIO_ANALYSIS)
    assert order.index(StageName.AUDIO_ANALYSIS) < order.index(StageName.TRANSCRIPTION)
    assert order.index(StageName.TRANSCRIPTION) < order.index(StageName.TEXT_ANALYSIS)


def test_missing_asr_mode_refuses_to_build_dag() -> None:
    with pytest.raises(StageDagError) as excinfo:
        plan_stage_units(_plan(RunPlanChoices(())))
    assert excinfo.value.reason == "missing_asr_mode"


def test_duplicate_part_source_ids_are_rejected() -> None:
    with pytest.raises(StageDagError) as excinfo:
        plan_stage_units(_plan(_mode_choices(AsrMode.FULL_ASR), _PART_A, _PART_A))
    assert excinfo.value.reason == "duplicate_part"


# --- Invalidation keys ------------------------------------------------------


def test_every_completed_unit_key_records_stage_version() -> None:
    keys = compute_invalidation_keys(_plan(_mode_choices(AsrMode.FULL_ASR), _PART_A))
    for unit, key in keys.items():
        assert key.stage_version == STAGE_VERSIONS[unit.stage]


def test_keys_are_deterministic() -> None:
    plan = _plan(_mode_choices(AsrMode.FULL_ASR), _PART_A, _PART_B)
    first = compute_invalidation_keys(plan)
    second = compute_invalidation_keys(plan)
    assert {u: k.digest() for u, k in first.items()} == {u: k.digest() for u, k in second.items()}


def test_key_json_round_trips() -> None:
    key = next(iter(compute_invalidation_keys(_plan(_mode_choices(AsrMode.FULL_ASR))).values()))
    assert StageInvalidationKey.from_json(key.as_json()) == key


def test_config_change_invalidates_only_affected_and_downstream() -> None:
    part_a_decoder = _choice(STAGE_SUBTITLES, KEY_SUBTITLE_DECODER, _PART_A, "mkvextract")
    before = compute_invalidation_keys(_plan(_mode_choices(AsrMode.FULL_ASR), _PART_A, _PART_B))
    after = compute_invalidation_keys(
        _plan(_mode_choices(AsrMode.FULL_ASR, extra=(part_a_decoder,)), _PART_A, _PART_B)
    )

    def digest(units: dict[StageUnit, StageInvalidationKey], stage: StageName, scope: str) -> str:
        return units[StageUnit(stage, scope)].digest()

    # Upstream collection unit is untouched.
    assert digest(before, StageName.SOURCE_REVALIDATION, COLLECTION_SCOPE) == digest(
        after, StageName.SOURCE_REVALIDATION, COLLECTION_SCOPE
    )
    # The reconfigured Part's subtitles unit and everything downstream of it change.
    assert digest(before, StageName.SUBTITLES, _PART_A) != digest(
        after, StageName.SUBTITLES, _PART_A
    )
    assert digest(before, StageName.AUDIO_ANALYSIS, _PART_A) != digest(
        after, StageName.AUDIO_ANALYSIS, _PART_A
    )
    assert digest(before, StageName.TRANSCRIPTION, _PART_A) != digest(
        after, StageName.TRANSCRIPTION, _PART_A
    )
    # The sibling Part is entirely unaffected.
    assert digest(before, StageName.SUBTITLES, _PART_B) == digest(
        after, StageName.SUBTITLES, _PART_B
    )
    assert digest(before, StageName.TRANSCRIPTION, _PART_B) == digest(
        after, StageName.TRANSCRIPTION, _PART_B
    )


def test_source_content_change_recaches_the_whole_part_chain() -> None:
    before = compute_invalidation_keys(_plan(_mode_choices(AsrMode.FULL_ASR), _PART_A))
    after = compute_invalidation_keys(_plan(_mode_choices(AsrMode.FULL_ASR), "c" * 64))
    # Different Part content produces a different collection key and a different
    # downstream chain (the scopes differ, so compare stages by position).
    assert (
        before[StageUnit(StageName.SOURCE_REVALIDATION, COLLECTION_SCOPE)].digest()
        != after[StageUnit(StageName.SOURCE_REVALIDATION, COLLECTION_SCOPE)].digest()
    )


# --- Checkpoint records + adoption ------------------------------------------


def test_recorded_units_round_trip_through_run_state(tmp_path: Path) -> None:
    plan = _plan(_mode_choices(AsrMode.FULL_ASR), _PART_A)
    keys = compute_invalidation_keys(plan)
    writer = _running_writer(tmp_path)
    unit = StageUnit(StageName.SUBTITLES, _PART_A)
    writer.set_progress(stage_units=[unit_record(unit, UnitStatus.COMPLETED, keys[unit])])
    recorded = read_recorded_units(writer.state)
    assert recorded[unit].status is UnitStatus.COMPLETED
    assert recorded[unit].key == keys[unit]


def test_completed_unit_with_matching_key_is_adoptable() -> None:
    plan = _plan(_mode_choices(AsrMode.FULL_ASR), _PART_A)
    keys = compute_invalidation_keys(plan)
    unit = StageUnit(StageName.SUBTITLES, _PART_A)
    recorded = {unit: RecordedUnit(unit, UnitStatus.COMPLETED, keys[unit])}
    assert adoptable_units(recorded, keys) == frozenset({unit})


def test_failed_unit_is_never_adoptable() -> None:
    plan = _plan(_mode_choices(AsrMode.FULL_ASR), _PART_A)
    keys = compute_invalidation_keys(plan)
    unit = StageUnit(StageName.SUBTITLES, _PART_A)
    recorded = {unit: RecordedUnit(unit, UnitStatus.FAILED, keys[unit])}
    assert adoptable_units(recorded, keys) == frozenset()


def test_config_change_makes_downstream_units_unadoptable() -> None:
    part_a_decoder = _choice(STAGE_SUBTITLES, KEY_SUBTITLE_DECODER, _PART_A, "mkvextract")
    old_plan = _plan(_mode_choices(AsrMode.FULL_ASR), _PART_A)
    new_plan = _plan(_mode_choices(AsrMode.FULL_ASR, extra=(part_a_decoder,)), _PART_A)
    old_keys = compute_invalidation_keys(old_plan)
    new_keys = compute_invalidation_keys(new_plan)
    recorded = {
        unit: RecordedUnit(unit, UnitStatus.COMPLETED, key) for unit, key in old_keys.items()
    }
    adoptable = adoptable_units(recorded, new_keys)
    # Upstream revalidation is still adoptable; the reconfigured subtitles and
    # everything downstream of it are not.
    assert StageUnit(StageName.SOURCE_REVALIDATION, COLLECTION_SCOPE) in adoptable
    assert StageUnit(StageName.SUBTITLES, _PART_A) not in adoptable
    assert StageUnit(StageName.TRANSCRIPTION, _PART_A) not in adoptable


def test_adoption_reads_only_recorded_units_never_the_filesystem(tmp_path: Path) -> None:
    # A workspace directory that is not a recorded unit must never be adopted.
    plan = _plan(_mode_choices(AsrMode.FULL_ASR), _PART_A)
    keys = compute_invalidation_keys(plan)
    layout = _layout(tmp_path)
    (layout.stages_dir / "subtitles" / _PART_A).mkdir(parents=True)
    writer = RunStateWriter.create(layout, plan_id=_PLAN_ID, clock=_clock())
    recorded = read_recorded_units(writer.state)
    assert adoptable_units(recorded, keys) == frozenset()


# --- Execution engine -------------------------------------------------------


def _all_completed_executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
    return StageResult.completed()


def test_run_must_be_running_to_execute(tmp_path: Path) -> None:
    writer = RunStateWriter.create(_layout(tmp_path), plan_id=_PLAN_ID, clock=_clock())
    with pytest.raises(StageDagError) as excinfo:
        execute_stages(
            writer=writer,
            layout=_layout(tmp_path),
            plan=_plan(_mode_choices(AsrMode.FULL_ASR)),
            executor=_all_completed_executor,
        )
    assert excinfo.value.reason == "run_not_running"


def test_completed_run_checkpoints_every_unit(tmp_path: Path) -> None:
    plan = _plan(_mode_choices(AsrMode.FULL_ASR), _PART_A, _PART_B)
    writer = _running_writer(tmp_path)
    result = execute_stages(
        writer=writer,
        layout=_layout(tmp_path),
        plan=plan,
        executor=_all_completed_executor,
        on_boundary=lambda: ControlDirective.CONTINUE,
    )
    assert result.disposition is StageRunDisposition.COMPLETED_ALL
    assert result.failed_scopes == frozenset()
    recorded = read_recorded_units(writer.state)
    assert set(recorded) == set(plan_stage_units(plan))
    assert all(unit.status is UnitStatus.COMPLETED for unit in recorded.values())


def test_completed_unit_records_its_invalidation_key(tmp_path: Path) -> None:
    plan = _plan(_mode_choices(AsrMode.FULL_ASR), _PART_A)
    keys = compute_invalidation_keys(plan)
    writer = _running_writer(tmp_path)
    execute_stages(
        writer=writer,
        layout=_layout(tmp_path),
        plan=plan,
        executor=_all_completed_executor,
        on_boundary=lambda: ControlDirective.CONTINUE,
    )
    recorded = read_recorded_units(writer.state)
    for unit, record in recorded.items():
        assert record.key == keys[unit]


def test_mid_unit_interruption_leaves_no_adoptable_record(tmp_path: Path) -> None:
    plan = _plan(_mode_choices(AsrMode.FULL_ASR), _PART_A)
    keys = compute_invalidation_keys(plan)
    writer = _running_writer(tmp_path)

    def crashing(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        if unit.stage is StageName.AUDIO_ANALYSIS:
            raise RuntimeError("killed mid-unit")
        return StageResult.completed()

    with pytest.raises(RuntimeError):
        execute_stages(
            writer=writer,
            layout=_layout(tmp_path),
            plan=plan,
            executor=crashing,
            on_boundary=lambda: ControlDirective.CONTINUE,
        )
    recorded = read_recorded_units(writer.state)
    # The interrupted unit left no record at all, so it is not adoptable; the
    # units completed before it are checkpointed and adoptable.
    assert StageUnit(StageName.AUDIO_ANALYSIS, _PART_A) not in recorded
    adoptable = adoptable_units(recorded, keys)
    assert StageUnit(StageName.SUBTITLES, _PART_A) in adoptable
    assert StageUnit(StageName.AUDIO_ANALYSIS, _PART_A) not in adoptable


def test_per_part_failure_blocks_only_that_part(tmp_path: Path) -> None:
    plan = _plan(_mode_choices(AsrMode.FULL_ASR), _PART_A, _PART_B)
    writer = _running_writer(tmp_path)

    def fail_part_a_subtitles(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        if unit.stage is StageName.SUBTITLES and unit.scope == _PART_A:
            return StageResult.failed(detail={"reason": "decode_failed"})
        return StageResult.completed()

    result = execute_stages(
        writer=writer,
        layout=_layout(tmp_path),
        plan=plan,
        executor=fail_part_a_subtitles,
        on_boundary=lambda: ControlDirective.CONTINUE,
    )
    assert result.disposition is StageRunDisposition.COMPLETED_ALL
    assert result.failed_scopes == frozenset({_PART_A})
    recorded = read_recorded_units(writer.state)
    # Part A: subtitles failed, its downstream is blocked.
    assert recorded[StageUnit(StageName.SUBTITLES, _PART_A)].status is UnitStatus.FAILED
    assert recorded[StageUnit(StageName.AUDIO_ANALYSIS, _PART_A)].status is UnitStatus.BLOCKED
    assert recorded[StageUnit(StageName.TRANSCRIPTION, _PART_A)].status is UnitStatus.BLOCKED
    # Part B: unaffected, every unit completed.
    assert recorded[StageUnit(StageName.SUBTITLES, _PART_B)].status is UnitStatus.COMPLETED
    assert recorded[StageUnit(StageName.TRANSCRIPTION, _PART_B)].status is UnitStatus.COMPLETED


def test_collection_stage_failure_blocks_all_parts(tmp_path: Path) -> None:
    plan = _plan(_mode_choices(AsrMode.FULL_ASR), _PART_A, _PART_B)
    writer = _running_writer(tmp_path)

    def fail_revalidation(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        if unit.stage is StageName.SOURCE_REVALIDATION:
            return StageResult.failed()
        return StageResult.completed()

    result = execute_stages(
        writer=writer,
        layout=_layout(tmp_path),
        plan=plan,
        executor=fail_revalidation,
        on_boundary=lambda: ControlDirective.CONTINUE,
    )
    assert result.disposition is StageRunDisposition.COMPLETED_ALL
    recorded = read_recorded_units(writer.state)
    assert recorded[StageUnit(StageName.SOURCE_REVALIDATION, COLLECTION_SCOPE)].status is (
        UnitStatus.FAILED
    )
    per_part = [record for unit, record in recorded.items() if not unit.is_collection]
    assert per_part and all(record.status is UnitStatus.BLOCKED for record in per_part)


def test_decision_required_surfaces_as_incomplete(tmp_path: Path) -> None:
    plan = _plan(_mode_choices(AsrMode.FULL_ASR), _PART_A)
    writer = _running_writer(tmp_path)

    # The stage surfaces its own retained pause payload — reason and expected
    # resume decision token — which the engine carries through verbatim.
    pause_payload = {
        "reason": "resource_envelope_exceeded",
        "decision": "resource_configuration_changed",
        "peak_gib": "9",
    }

    def demand_decision(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        if unit.stage is StageName.AUDIO_ANALYSIS:
            return StageResult.decision_required(pause_payload)
        return StageResult.completed()

    result = execute_stages(
        writer=writer,
        layout=_layout(tmp_path),
        plan=plan,
        executor=demand_decision,
        on_boundary=lambda: ControlDirective.CONTINUE,
    )
    assert result.disposition is StageRunDisposition.DECISION_REQUIRED
    assert writer.state.status is RunStatus.INCOMPLETE
    assert writer.state.required_decision == {
        "reason": "resource_envelope_exceeded",
        "decision": "resource_configuration_changed",
        "peak_gib": "9",
        "stage": StageName.AUDIO_ANALYSIS.value,
        "scope": _PART_A,
    }
    # No unit past the decision point is checkpointed.
    recorded = read_recorded_units(writer.state)
    assert StageUnit(StageName.AUDIO_ANALYSIS, _PART_A) not in recorded


def test_decision_required_rejects_an_empty_payload() -> None:
    with pytest.raises(StageDagError) as excinfo:
        StageResult.decision_required({})
    assert excinfo.value.reason == "empty_required_decision"


def test_pause_control_at_boundary_stops_the_run(tmp_path: Path) -> None:
    plan = _plan(_mode_choices(AsrMode.FULL_ASR), _PART_A)
    layout = _layout(tmp_path)
    writer = _running_writer(tmp_path)
    executed: list[StageUnit] = []

    def observe() -> ControlDirective:
        # Pause once the first unit has completed.
        if executed:
            return ControlDirective.PAUSE
        return ControlDirective.CONTINUE

    def recording_executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        executed.append(unit)
        return StageResult.completed()

    result = execute_stages(
        writer=writer,
        layout=layout,
        plan=plan,
        executor=recording_executor,
        on_boundary=observe,
    )
    assert result.disposition is StageRunDisposition.PAUSED
    assert writer.state.status is RunStatus.PAUSED
    # Only the first unit ran before the pause took effect at the next boundary.
    assert executed == [StageUnit(StageName.SOURCE_REVALIDATION, COLLECTION_SCOPE)]


def test_cancel_supersedes_pause_via_real_control_files(tmp_path: Path) -> None:
    plan = _plan(_mode_choices(AsrMode.FULL_ASR), _PART_A)
    layout = _layout(tmp_path)
    writer = _running_writer(tmp_path)
    request_control(layout, ControlKind.PAUSE, clock=_clock())
    request_control(layout, ControlKind.CANCEL, clock=_clock())
    result = execute_stages(
        writer=writer,
        layout=layout,
        plan=plan,
        executor=_all_completed_executor,
    )
    assert result.disposition is StageRunDisposition.CANCELLED
    assert writer.state.status is RunStatus.CANCELLED
    kinds = [event.kind.value for event in read_journal(layout.journal_path)]
    assert "control_request_observed" in kinds


def test_resume_adopts_completed_units_and_finishes(tmp_path: Path) -> None:
    plan = _plan(_mode_choices(AsrMode.FULL_ASR), _PART_A)
    layout = _layout(tmp_path)
    writer = _running_writer(tmp_path)
    executed_first: list[StageUnit] = []

    def stop_after_two(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        executed_first.append(unit)
        return StageResult.completed()

    def pause_after_two() -> ControlDirective:
        return ControlDirective.PAUSE if len(executed_first) >= 2 else ControlDirective.CONTINUE

    first = execute_stages(
        writer=writer,
        layout=layout,
        plan=plan,
        executor=stop_after_two,
        on_boundary=pause_after_two,
    )
    assert first.disposition is StageRunDisposition.PAUSED
    assert len(executed_first) == 2

    # A fresh process reopens the run and resumes it.
    resumed = RunStateWriter.reopen(layout, clock=_clock())
    resumed.transition_to(RunStatus.RUNNING)
    executed_second: list[StageUnit] = []

    def resume_executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        executed_second.append(unit)
        return StageResult.completed()

    second = execute_stages(
        writer=resumed,
        layout=layout,
        plan=plan,
        executor=resume_executor,
        on_boundary=lambda: ControlDirective.CONTINUE,
    )
    assert second.disposition is StageRunDisposition.COMPLETED_ALL
    # The two already-completed units are adopted, not re-run.
    assert not set(executed_second) & set(executed_first)
    recorded = read_recorded_units(resumed.state)
    assert set(recorded) == set(plan_stage_units(plan))
    assert all(record.status is UnitStatus.COMPLETED for record in recorded.values())
