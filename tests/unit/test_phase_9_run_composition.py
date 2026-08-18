"""Ticket 10: the production composition's offline-provable logic.

The per-phase functions need media, a model, and the network, so the composition
end-to-end runs only in a real environment. What is provable offline — and what
these tests pin — is the composition's *logic*: how it maps each stage's own
return vocabulary onto the DAG's :class:`StageResult` contract, how it chains one
stage's report id into the next, and how it translates the plan's front-loaded
choices into each function's selector shapes. The stage functions are replaced by
controlled stand-ins.
"""

from __future__ import annotations

from pathlib import Path

from video_content_pipeline.orchestration import RunLayout
from video_content_pipeline.planning import RunPlan, calculate_disk_headroom
from video_content_pipeline.run_choices import (
    COLLECTION_SCOPE,
    KEY_ASR_MODE,
    KEY_AUDIO_STREAM,
    KEY_SUBTITLE_DECODER,
    KEY_VISUAL_TEXT_ENABLED,
    STAGE_AUDIO_ANALYSIS,
    STAGE_RUN,
    STAGE_SUBTITLES,
    AsrMode,
    ChoiceProvenance,
    RunChoice,
    RunPlanChoices,
)
from video_content_pipeline.run_composition import (
    StageFunctions,
    _CompositionState,
    _is_full_asr_handoff,
    build_run_composition,
    map_stage_return,
)
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.stage_dag import (
    StageName,
    StageResult,
    StageResultKind,
    StageUnit,
    plan_stage_units,
)

_PLAN_ID = "plan0123456789abcdef0123"
_PART = "a" * 64


def _plan(*, mode: AsrMode = AsrMode.FULL_ASR, extra: tuple[RunChoice, ...] = ()) -> RunPlan:
    choices = (
        RunChoice(
            STAGE_RUN, KEY_ASR_MODE, COLLECTION_SCOPE, mode.value, ChoiceProvenance.USER_CHOSEN
        ),
        RunChoice(
            STAGE_RUN,
            KEY_VISUAL_TEXT_ENABLED,
            COLLECTION_SCOPE,
            "false",
            ChoiceProvenance.USER_CHOSEN,
        ),
        *extra,
    )
    return RunPlan(
        plan_id=_PLAN_ID,
        report_id="0" * 32,
        source_artifacts=(
            SourceArtifact(source_id=_PART, sha256=_PART, byte_count=1, media_path=Path("m")),
        ),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint="cfg" + "0" * 61,
        run_choices=RunPlanChoices.build(choices),
    )


def _layout(tmp_path: Path) -> RunLayout:
    return RunLayout(project_root=tmp_path, source_id="s" * 64, run_id="20260816T083000Z-0000")


# --- map_stage_return -------------------------------------------------------


def test_map_success_completes_and_carries_report_id() -> None:
    result = map_stage_return("complete", {"report_id": "r1"})
    assert result.kind is StageResultKind.COMPLETED
    assert result.detail["report_id"] == "r1"


def test_map_top_level_required_decision_is_a_decision_pause() -> None:
    payload = {"reason": "resource_envelope_exceeded", "decision": "resource_configuration_changed"}
    result = map_stage_return("resource_envelope_exceeded", {"required_decision": payload})
    assert result.kind is StageResultKind.DECISION_REQUIRED
    assert result.required_decision == payload


def test_map_nested_audio_required_decision_is_a_decision_pause() -> None:
    payload = {"reason": "model_release_unverified"}
    result = map_stage_return("partial", {"partial_analysis": {"required_decision": payload}})
    assert result.kind is StageResultKind.DECISION_REQUIRED
    assert result.required_decision == payload


def test_map_acquisition_status_synthesizes_a_decision_pause() -> None:
    result = map_stage_return("model_acquisition_required", {"report_id": "r"})
    assert result.kind is StageResultKind.DECISION_REQUIRED
    assert result.required_decision == {
        "reason": "model_acquisition_required",
        "decision": "model_acquisition_required",
    }


def test_map_blocked_audio_with_acquisition_capability_is_a_decision_pause() -> None:
    report = {"capabilities": [{"state": "model_acquisition_required"}]}
    result = map_stage_return("blocked", report)
    assert result.kind is StageResultKind.DECISION_REQUIRED


def test_map_plain_blocked_is_a_failure() -> None:
    result = map_stage_return("blocked", {"report_id": "r", "diagnostics": []})
    assert result.kind is StageResultKind.FAILED


# --- Subtitle full-ASR handoff override -------------------------------------

#: ``_is_full_asr_handoff`` only checks the report parsed (is not ``None``); the
#: report's own contents are irrelevant to the predicate, so a sentinel suffices.
_PARSED_REPORT = object()


def _multi_plan(part_ids: tuple[str, ...]) -> RunPlan:
    return RunPlan(
        plan_id=_PLAN_ID,
        report_id="0" * 32,
        source_artifacts=tuple(
            SourceArtifact(source_id=p, sha256=p, byte_count=1, media_path=Path("m"))
            for p in part_ids
        ),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint="cfg" + "0" * 61,
        run_choices=RunPlanChoices.build(
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
            )
        ),
    )


def _handoff_state(
    tmp_path: Path, part_ids: tuple[str, ...], handoff: tuple[str, ...], *, report: object
) -> _CompositionState:
    state = _CompositionState(
        layout=_layout(tmp_path), plan=_multi_plan(part_ids), functions=StageFunctions()
    )
    state.subtitle_report = report  # type: ignore[assignment]
    state.subtitle_asr_handoff = handoff
    return state


_A_FAILURE = map_stage_return("blocked", {"report_id": "r"})


def test_all_parts_handoff_overrides_a_failure(tmp_path: Path) -> None:
    state = _handoff_state(tmp_path, (_PART,), (_PART,), report=_PARSED_REPORT)
    assert _is_full_asr_handoff(state, _A_FAILURE) is True


def test_a_partial_handoff_stays_a_failure(tmp_path: Path) -> None:
    parts = (_PART, "b" * 64)
    state = _handoff_state(tmp_path, parts, (_PART,), report=_PARSED_REPORT)
    assert _is_full_asr_handoff(state, _A_FAILURE) is False


def test_no_handoff_stays_a_failure(tmp_path: Path) -> None:
    state = _handoff_state(tmp_path, (_PART,), (), report=_PARSED_REPORT)
    assert _is_full_asr_handoff(state, _A_FAILURE) is False


def test_an_unparsed_report_stays_a_failure(tmp_path: Path) -> None:
    state = _handoff_state(tmp_path, (_PART,), (_PART,), report=None)
    assert _is_full_asr_handoff(state, _A_FAILURE) is False


def test_a_successful_mapping_is_never_overridden(tmp_path: Path) -> None:
    state = _handoff_state(tmp_path, (_PART,), (_PART,), report=_PARSED_REPORT)
    assert _is_full_asr_handoff(state, StageResult.completed()) is False


# --- Executor chaining and translation --------------------------------------


class _Recorder:
    """A controlled stand-in for the per-phase functions that records its calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def _make(self, name: str, report_id: str):
        def function(*args: object, **kwargs: object) -> dict[str, object]:
            self.calls.append((name, args, kwargs))
            return {"status": "complete", "report": {"report_id": report_id}}

        return function

    def functions(self) -> StageFunctions:
        return StageFunctions(
            process_subtitles=self._make("subtitles", "sub-1"),
            resume_subtitles=self._make("resume_subtitles", "sub-1"),
            analyze_audio=self._make("audio", "aud-1"),
            transcribe=self._make("transcribe", "asr-1"),
            enhance=self._make("enhance", "enh-1"),
            analyze_text=self._make("text", "txt-1"),
            run_visual_text=self._make("visual", "vis-1"),
        )


def _run_executor(composition, plan: RunPlan) -> dict[StageUnit, object]:
    executor = composition.executor
    from video_content_pipeline.stage_dag import compute_invalidation_keys

    keys = compute_invalidation_keys(plan)
    return {unit: executor(unit, keys[unit]) for unit in plan_stage_units(plan)}


def test_executor_chains_report_ids_across_stages(tmp_path: Path) -> None:
    plan = _plan()
    recorder = _Recorder()
    composition = build_run_composition(_layout(tmp_path), plan, functions=recorder.functions())
    results = _run_executor(composition, plan)

    assert all(r.kind is StageResultKind.COMPLETED for r in results.values())
    # Audio received the subtitle report id; transcription received both prior ids.
    audio_call = next(c for c in recorder.calls if c[0] == "audio")
    assert audio_call[1][1] == "sub-1"
    asr_call = next(c for c in recorder.calls if c[0] == "transcribe")
    assert asr_call[1][1] == "sub-1"
    assert asr_call[1][2] == "aud-1"


def test_full_asr_run_grants_the_resource_confirmation_up_front(tmp_path: Path) -> None:
    # An orchestrated full-ASR run cannot resume a terminal transcription decision
    # pause (INCOMPLETE has no outgoing transitions), so the composition grants the
    # Full-ASR resource confirmation up front: launching a maintainer-confirmed plan
    # whose asr_mode is explicitly full_asr is the before-execution confirmation the
    # gate requires. A subtitle-first run grants nothing.
    from video_content_pipeline.transcription import FULL_ASR_RESOURCE_CONFIRMATION_DECISION

    full_asr = _plan(mode=AsrMode.FULL_ASR)
    recorder = _Recorder()
    composition = build_run_composition(_layout(tmp_path), full_asr, functions=recorder.functions())
    _run_executor(composition, full_asr)
    asr_call = next(c for c in recorder.calls if c[0] == "transcribe")
    assert asr_call[2]["resumption_decision"] == FULL_ASR_RESOURCE_CONFIRMATION_DECISION

    subtitle_first = _plan(mode=AsrMode.SUBTITLE_FIRST)
    recorder2 = _Recorder()
    composition2 = build_run_composition(
        _layout(tmp_path / "b"), subtitle_first, functions=recorder2.functions()
    )
    _run_executor(composition2, subtitle_first)
    asr_call2 = next(c for c in recorder2.calls if c[0] == "transcribe")
    assert asr_call2[2]["resumption_decision"] is None


def test_resume_rebuilds_stage_report_chain_for_downstream_stages(tmp_path: Path) -> None:
    # On resume the composition adopts already-completed upstream stages without
    # re-invoking them, so it must rebuild their report-id chain from the recorded
    # state -- otherwise the first downstream stage that runs (here text analysis)
    # sees no subtitle/transcription report id and fails *_report_unavailable.
    from video_content_pipeline.orchestration import initialize_run_workspace
    from video_content_pipeline.run_state import RunStateWriter, RunStatus
    from video_content_pipeline.stage_dag import (
        UnitStatus,
        compute_invalidation_keys,
        unit_record,
    )

    plan = _plan()
    layout = initialize_run_workspace(_layout(tmp_path))
    keys = compute_invalidation_keys(plan)
    units = {unit.stage: unit for unit in plan_stage_units(plan)}
    writer = RunStateWriter.create(layout, plan_id=plan.plan_id)
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    writer.set_progress(
        stage_units=[
            unit_record(units[stage], UnitStatus.COMPLETED, keys[units[stage]], report_id)
            for stage, report_id in (
                (StageName.SUBTITLES, "sub-1"),
                (StageName.AUDIO_ANALYSIS, "aud-1"),
                (StageName.TRANSCRIPTION, "asr-1"),
            )
        ]
    )

    recorder = _Recorder()
    composition = build_run_composition(layout, plan, functions=recorder.functions())
    text_unit = units[StageName.TEXT_ANALYSIS]
    composition.executor(text_unit, keys[text_unit])

    text_call = next(c for c in recorder.calls if c[0] == "text")
    assert text_call[1][1] == "sub-1"  # subtitle report id, positional
    assert text_call[2]["audio_report_id"] == "aud-1"
    assert text_call[2]["transcription_report_id"] == "asr-1"


def test_executor_translates_front_loaded_choices(tmp_path: Path) -> None:
    plan = _plan(
        extra=(
            RunChoice(
                STAGE_SUBTITLES,
                KEY_SUBTITLE_DECODER,
                _PART,
                "0=utf-8",
                ChoiceProvenance.USER_CHOSEN,
            ),
            RunChoice(
                STAGE_AUDIO_ANALYSIS, KEY_AUDIO_STREAM, _PART, "1", ChoiceProvenance.USER_CHOSEN
            ),
        )
    )
    recorder = _Recorder()
    composition = build_run_composition(_layout(tmp_path), plan, functions=recorder.functions())
    _run_executor(composition, plan)

    subtitles_call = next(c for c in recorder.calls if c[0] == "subtitles")
    assert subtitles_call[1][2] == (f"{_PART}=0=utf-8",)  # decoders selector
    audio_call = next(c for c in recorder.calls if c[0] == "audio")
    assert audio_call[1][3] == (f"{_PART}=1",)  # audio-stream selector


def test_executor_memoizes_one_call_per_stage(tmp_path: Path) -> None:
    plan = _plan()
    recorder = _Recorder()
    composition = build_run_composition(_layout(tmp_path), plan, functions=recorder.functions())
    _run_executor(composition, plan)
    # Each stage function is invoked exactly once even though the DAG revisits it.
    names = [call[0] for call in recorder.calls]
    assert names.count("subtitles") == 1
    assert names.count("audio") == 1


def test_executor_stops_when_a_stage_decision_pauses(tmp_path: Path) -> None:
    plan = _plan()
    recorder = _Recorder()

    def paused_audio(*args: object, **kwargs: object) -> dict[str, object]:
        recorder.calls.append(("audio", args, kwargs))
        return {
            "status": "resource_envelope_exceeded",
            "report": {
                "report_id": "aud-1",
                "required_decision": {
                    "reason": "resource_envelope_exceeded",
                    "decision": "resource_configuration_changed",
                },
            },
        }

    functions = recorder.functions()
    functions.analyze_audio = paused_audio
    composition = build_run_composition(_layout(tmp_path), plan, functions=functions)
    results = _run_executor(composition, plan)
    audio_unit = StageUnit(StageName.AUDIO_ANALYSIS, _PART)
    assert results[audio_unit].kind is StageResultKind.DECISION_REQUIRED
    # Transcription never received a call because audio did not complete.
    assert not any(c[0] == "transcribe" for c in recorder.calls)
