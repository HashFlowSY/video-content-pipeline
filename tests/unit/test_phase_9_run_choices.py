"""Front-loaded plan choices: schema, provenance, gaps, and translation."""

from __future__ import annotations

import pytest

from video_content_pipeline.run_choices import (
    COLLECTION_SCOPE,
    KEY_ASR_MODE,
    KEY_AUDIO_STREAM,
    KEY_DIARIZATION_CANDIDATE,
    KEY_ENHANCEMENT_CUE,
    KEY_ENHANCEMENT_PART,
    KEY_ENHANCEMENT_RANGE,
    KEY_ROLE_METADATA,
    KEY_SUBTITLE_DECODER,
    KEY_SUBTITLE_TRACK,
    KEY_TRANSCRIPTION_UPGRADE_ALL,
    KEY_VISUAL_TEXT_ALL,
    KEY_VISUAL_TEXT_ENABLED,
    KEY_VISUAL_TEXT_PART,
    KEY_VISUAL_TEXT_RANGE,
    STAGE_AUDIO_ANALYSIS,
    STAGE_ENHANCEMENT,
    STAGE_RUN,
    STAGE_SUBTITLES,
    STAGE_TRANSCRIPTION,
    STAGE_VISUAL_TEXT,
    AsrMode,
    ChoiceProvenance,
    RunChoice,
    RunChoicesError,
    RunPlanChoices,
    audio_analysis_stage_parameters,
    enhancement_stage_parameters,
    missing_required_choices,
    subtitle_stage_parameters,
    transcription_stage_parameters,
    visual_text_stage_parameters,
)


def _mode(mode: AsrMode) -> RunChoice:
    return RunChoice(
        stage=STAGE_RUN,
        key=KEY_ASR_MODE,
        scope=COLLECTION_SCOPE,
        value=mode.value,
        provenance=ChoiceProvenance.USER_CHOSEN,
    )


def _visual_enabled(enabled: bool) -> RunChoice:
    return RunChoice(
        stage=STAGE_RUN,
        key=KEY_VISUAL_TEXT_ENABLED,
        scope=COLLECTION_SCOPE,
        value="true" if enabled else "false",
        provenance=ChoiceProvenance.RECOMMENDED_AND_CONFIRMED,
    )


def test_run_choice_round_trips_through_json() -> None:
    choice = RunChoice(
        stage=STAGE_SUBTITLES,
        key=KEY_SUBTITLE_TRACK,
        scope="part-a",
        value="2",
        provenance=ChoiceProvenance.USER_CHOSEN,
    )

    assert RunChoice.from_json(choice.as_json()) == choice
    assert choice.as_json()["provenance"] == "user_chosen"


def test_choices_are_canonically_ordered_regardless_of_input_order() -> None:
    first = RunPlanChoices.build(
        (
            RunChoice(
                STAGE_SUBTITLES, KEY_SUBTITLE_TRACK, "part-b", "1", ChoiceProvenance.USER_CHOSEN
            ),
            _mode(AsrMode.FULL_ASR),
            RunChoice(
                STAGE_SUBTITLES, KEY_SUBTITLE_TRACK, "part-a", "0", ChoiceProvenance.USER_CHOSEN
            ),
        )
    )
    second = RunPlanChoices.build(
        (
            RunChoice(
                STAGE_SUBTITLES, KEY_SUBTITLE_TRACK, "part-a", "0", ChoiceProvenance.USER_CHOSEN
            ),
            RunChoice(
                STAGE_SUBTITLES, KEY_SUBTITLE_TRACK, "part-b", "1", ChoiceProvenance.USER_CHOSEN
            ),
            _mode(AsrMode.FULL_ASR),
        )
    )

    assert first == second
    assert first.as_json() == second.as_json()


def test_run_plan_choices_round_trip_through_json() -> None:
    choices = RunPlanChoices.build(
        (
            _mode(AsrMode.ENHANCEMENT),
            _visual_enabled(True),
            RunChoice(
                STAGE_ENHANCEMENT,
                KEY_ENHANCEMENT_PART,
                COLLECTION_SCOPE,
                "part-a",
                ChoiceProvenance.USER_CHOSEN,
            ),
            RunChoice(
                STAGE_VISUAL_TEXT,
                KEY_VISUAL_TEXT_ALL,
                COLLECTION_SCOPE,
                "true",
                ChoiceProvenance.RECOMMENDED_AND_CONFIRMED,
            ),
        )
    )

    assert RunPlanChoices.from_json(choices.as_json()) == choices


def test_empty_choices_serialize_and_load() -> None:
    empty = RunPlanChoices(())

    assert empty.as_json() == {"schema_version": 1, "choices": []}
    assert RunPlanChoices.from_json(empty.as_json()) == empty


def test_unsupported_schema_version_is_rejected() -> None:
    with pytest.raises(RunChoicesError) as caught:
        RunPlanChoices.from_json({"schema_version": 2, "choices": []})

    assert caught.value.reason == "run_choices_invalid"


def test_absent_document_loads_as_empty() -> None:
    assert RunPlanChoices.from_json({}) == RunPlanChoices(())


def test_asr_mode_and_visual_flag_accessors() -> None:
    choices = RunPlanChoices.build((_mode(AsrMode.SUBTITLE_FIRST), _visual_enabled(False)))

    assert choices.asr_mode() is AsrMode.SUBTITLE_FIRST
    assert choices.visual_text_enabled() is False


def test_absent_mode_accessors_are_none() -> None:
    empty = RunPlanChoices(())

    assert empty.asr_mode() is None
    assert empty.visual_text_enabled() is None


def test_single_valued_choice_conflict_is_rejected() -> None:
    with pytest.raises(RunChoicesError) as caught:
        RunPlanChoices.build(
            (
                RunChoice(
                    STAGE_SUBTITLES, KEY_SUBTITLE_TRACK, "part-a", "0", ChoiceProvenance.USER_CHOSEN
                ),
                RunChoice(
                    STAGE_SUBTITLES, KEY_SUBTITLE_TRACK, "part-a", "1", ChoiceProvenance.USER_CHOSEN
                ),
            )
        )

    assert caught.value.reason == "conflicting_choice"


def test_duplicate_multi_valued_choice_is_rejected() -> None:
    with pytest.raises(RunChoicesError) as caught:
        RunPlanChoices.build(
            (
                RunChoice(
                    STAGE_AUDIO_ANALYSIS,
                    KEY_ROLE_METADATA,
                    "part-a",
                    "1=narrator",
                    ChoiceProvenance.USER_CHOSEN,
                ),
                RunChoice(
                    STAGE_AUDIO_ANALYSIS,
                    KEY_ROLE_METADATA,
                    "part-a",
                    "1=narrator",
                    ChoiceProvenance.USER_CHOSEN,
                ),
            )
        )

    assert caught.value.reason == "duplicate_choice"


def test_multi_valued_choice_allows_distinct_values_per_scope() -> None:
    choices = RunPlanChoices.build(
        (
            RunChoice(
                STAGE_AUDIO_ANALYSIS,
                KEY_ROLE_METADATA,
                "part-a",
                "1=narrator",
                ChoiceProvenance.USER_CHOSEN,
            ),
            RunChoice(
                STAGE_AUDIO_ANALYSIS,
                KEY_ROLE_METADATA,
                "part-a",
                "2=guest",
                ChoiceProvenance.USER_CHOSEN,
            ),
        )
    )

    assert len(choices.choices) == 2


def test_invalid_asr_mode_value_is_rejected() -> None:
    with pytest.raises(RunChoicesError) as caught:
        RunPlanChoices.build(
            (
                RunChoice(
                    STAGE_RUN,
                    KEY_ASR_MODE,
                    COLLECTION_SCOPE,
                    "nonsense",
                    ChoiceProvenance.USER_CHOSEN,
                ),
            )
        )

    assert caught.value.reason == "invalid_choice_value"


def test_boolean_flag_choice_rejects_non_boolean_value() -> None:
    with pytest.raises(RunChoicesError) as caught:
        RunPlanChoices.build(
            (
                RunChoice(
                    STAGE_RUN,
                    KEY_VISUAL_TEXT_ENABLED,
                    COLLECTION_SCOPE,
                    "maybe",
                    ChoiceProvenance.USER_CHOSEN,
                ),
            )
        )

    assert caught.value.reason == "invalid_choice_value"


def test_unknown_stage_or_key_is_rejected() -> None:
    with pytest.raises(RunChoicesError) as caught:
        RunPlanChoices.build(
            (RunChoice("mystery", "unknown", COLLECTION_SCOPE, "x", ChoiceProvenance.USER_CHOSEN),)
        )

    assert caught.value.reason == "unknown_choice"


def test_missing_mode_toggles_are_reported_as_gaps() -> None:
    gaps = missing_required_choices(RunPlanChoices(()))
    reported = {(gap.stage, gap.key) for gap in gaps}

    assert (STAGE_RUN, KEY_ASR_MODE) in reported
    assert (STAGE_RUN, KEY_VISUAL_TEXT_ENABLED) in reported


def test_enhancement_mode_requires_a_scope() -> None:
    choices = RunPlanChoices.build((_mode(AsrMode.ENHANCEMENT), _visual_enabled(False)))

    gaps = {(gap.stage, gap.key) for gap in missing_required_choices(choices)}

    assert (STAGE_ENHANCEMENT, KEY_ENHANCEMENT_PART) in gaps


def test_enhancement_mode_with_scope_has_no_scope_gap() -> None:
    choices = RunPlanChoices.build(
        (
            _mode(AsrMode.ENHANCEMENT),
            _visual_enabled(False),
            RunChoice(
                STAGE_ENHANCEMENT, KEY_ENHANCEMENT_CUE, "part-a", "3", ChoiceProvenance.USER_CHOSEN
            ),
        )
    )

    gaps = {gap.stage for gap in missing_required_choices(choices)}

    assert STAGE_ENHANCEMENT not in gaps


def test_scope_not_needed_by_mode_is_not_a_gap() -> None:
    # subtitle_first mode never enhances; a missing enhancement scope is fine.
    choices = RunPlanChoices.build((_mode(AsrMode.SUBTITLE_FIRST), _visual_enabled(False)))

    assert missing_required_choices(choices) == ()


def test_visual_text_enabled_requires_a_scope() -> None:
    choices = RunPlanChoices.build((_mode(AsrMode.FULL_ASR), _visual_enabled(True)))

    gaps = {gap.stage for gap in missing_required_choices(choices)}

    assert STAGE_VISUAL_TEXT in gaps


def test_visual_text_disabled_needs_no_scope() -> None:
    choices = RunPlanChoices.build((_mode(AsrMode.FULL_ASR), _visual_enabled(False)))

    assert missing_required_choices(choices) == ()


def test_subtitle_stage_parameters_reconstruct_cli_selectors() -> None:
    choices = RunPlanChoices.build(
        (
            _mode(AsrMode.FULL_ASR),
            _visual_enabled(False),
            RunChoice(
                STAGE_SUBTITLES, KEY_SUBTITLE_TRACK, "part-a", "2", ChoiceProvenance.USER_CHOSEN
            ),
            RunChoice(
                STAGE_SUBTITLES,
                KEY_SUBTITLE_DECODER,
                "part-a",
                "2=utf-8",
                ChoiceProvenance.USER_CHOSEN,
            ),
        )
    )

    parameters = subtitle_stage_parameters(choices)

    assert parameters.select == ("part-a=2",)
    assert parameters.decoders == ("part-a=2=utf-8",)


def test_audio_analysis_stage_parameters_reconstruct_cli_selectors() -> None:
    choices = RunPlanChoices.build(
        (
            _mode(AsrMode.FULL_ASR),
            _visual_enabled(False),
            RunChoice(
                STAGE_AUDIO_ANALYSIS, KEY_AUDIO_STREAM, "part-a", "1", ChoiceProvenance.USER_CHOSEN
            ),
            RunChoice(
                STAGE_AUDIO_ANALYSIS,
                KEY_DIARIZATION_CANDIDATE,
                COLLECTION_SCOPE,
                "cand-7",
                ChoiceProvenance.RECOMMENDED_AND_CONFIRMED,
            ),
            RunChoice(
                STAGE_AUDIO_ANALYSIS,
                KEY_ROLE_METADATA,
                "part-a",
                "1=narrator",
                ChoiceProvenance.USER_CHOSEN,
            ),
        )
    )

    parameters = audio_analysis_stage_parameters(choices)

    assert parameters.audio_stream == ("part-a=1",)
    assert parameters.diarization_candidate == "cand-7"
    assert parameters.role_metadata == ("part-a=1=narrator",)


def test_audio_analysis_without_candidate_yields_none() -> None:
    choices = RunPlanChoices.build((_mode(AsrMode.FULL_ASR), _visual_enabled(False)))

    assert audio_analysis_stage_parameters(choices).diarization_candidate is None


def test_transcription_upgrade_flag_translation() -> None:
    upgraded = RunPlanChoices.build(
        (
            _mode(AsrMode.FULL_ASR),
            _visual_enabled(False),
            RunChoice(
                STAGE_TRANSCRIPTION,
                KEY_TRANSCRIPTION_UPGRADE_ALL,
                COLLECTION_SCOPE,
                "true",
                ChoiceProvenance.USER_CHOSEN,
            ),
        )
    )
    default = RunPlanChoices.build((_mode(AsrMode.FULL_ASR), _visual_enabled(False)))

    assert transcription_stage_parameters(upgraded).upgrade_all is True
    assert transcription_stage_parameters(default).upgrade_all is False


def test_enhancement_stage_parameters_reconstruct_cli_selectors() -> None:
    choices = RunPlanChoices.build(
        (
            _mode(AsrMode.ENHANCEMENT),
            _visual_enabled(False),
            RunChoice(
                STAGE_ENHANCEMENT,
                KEY_ENHANCEMENT_PART,
                COLLECTION_SCOPE,
                "part-a",
                ChoiceProvenance.USER_CHOSEN,
            ),
            RunChoice(
                STAGE_ENHANCEMENT,
                KEY_ENHANCEMENT_RANGE,
                "part-b",
                "0-100",
                ChoiceProvenance.USER_CHOSEN,
            ),
            RunChoice(
                STAGE_ENHANCEMENT, KEY_ENHANCEMENT_CUE, "part-c", "4", ChoiceProvenance.USER_CHOSEN
            ),
        )
    )

    parameters = enhancement_stage_parameters(choices)

    assert parameters.part_selectors == ("part-a",)
    assert parameters.range_selectors == ("part-b:0-100",)
    assert parameters.cue_selectors == ("part-c:4",)


def test_visual_text_stage_parameters_reconstruct_cli_selectors() -> None:
    all_parts = RunPlanChoices.build(
        (
            _mode(AsrMode.FULL_ASR),
            _visual_enabled(True),
            RunChoice(
                STAGE_VISUAL_TEXT,
                KEY_VISUAL_TEXT_ALL,
                COLLECTION_SCOPE,
                "true",
                ChoiceProvenance.USER_CHOSEN,
            ),
        )
    )
    scoped = RunPlanChoices.build(
        (
            _mode(AsrMode.FULL_ASR),
            _visual_enabled(True),
            RunChoice(
                STAGE_VISUAL_TEXT,
                KEY_VISUAL_TEXT_PART,
                COLLECTION_SCOPE,
                "part-a",
                ChoiceProvenance.USER_CHOSEN,
            ),
            RunChoice(
                STAGE_VISUAL_TEXT,
                KEY_VISUAL_TEXT_RANGE,
                "part-b",
                "5-9",
                ChoiceProvenance.USER_CHOSEN,
            ),
        )
    )

    assert visual_text_stage_parameters(all_parts).all_parts is True
    assert visual_text_stage_parameters(scoped).all_parts is False
    assert visual_text_stage_parameters(scoped).part_selectors == ("part-a",)
    assert visual_text_stage_parameters(scoped).range_selectors == ("part-b:5-9",)
