"""Front-loaded plan choices for non-interactive orchestration.

Plan confirmation captures every run-affecting choice — subtitle track
selection, analysis audio stream, diarization candidate, ASR mode, and
visual-text scope, plus the decoder and role-metadata refinements the expert
commands accept — into an immutable :class:`RunPlanChoices` carried by a
confirmed RunPlan (source-planning's *front-loaded plan choices* rule). Each
choice records explicit provenance (user-chosen versus recommended and
confirmed). ``vcp run`` then executes without prompting: a choice that a mode
genuinely needs but the plan omits is a machine-detectable gap the orchestrator
surfaces as a Run decision pause, never a mid-run prompt.

This module is a pure leaf: it defines the choice schema, the mode-driven gap
rule, and the translation from stored choices back into the exact selector
shapes the sixteen per-phase functions already accept, so composing them in
process changes none of their behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RunChoicesError(ValueError):
    """A front-loaded-choice failure with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class ChoiceProvenance(StrEnum):
    """How a front-loaded choice came to be fixed in the plan."""

    USER_CHOSEN = "user_chosen"
    RECOMMENDED_AND_CONFIRMED = "recommended_and_confirmed"


class AsrMode(StrEnum):
    """The transcription-versus-enhancement mode fixed at plan confirmation."""

    SUBTITLE_FIRST = "subtitle_first"
    FULL_ASR = "full_asr"
    ENHANCEMENT = "enhancement"


STAGE_RUN = "run"
STAGE_SUBTITLES = "subtitles"
STAGE_AUDIO_ANALYSIS = "audio_analysis"
STAGE_TRANSCRIPTION = "transcription"
STAGE_ENHANCEMENT = "enhancement"
STAGE_VISUAL_TEXT = "visual_text"

KEY_ASR_MODE = "asr_mode"
KEY_VISUAL_TEXT_ENABLED = "visual_text_enabled"
KEY_SUBTITLE_TRACK = "subtitle_track"
KEY_SUBTITLE_DECODER = "subtitle_decoder"
KEY_AUDIO_STREAM = "audio_stream"
KEY_DIARIZATION_CANDIDATE = "diarization_candidate"
KEY_ROLE_METADATA = "role_metadata"
KEY_TRANSCRIPTION_UPGRADE_ALL = "upgrade_all"
KEY_ENHANCEMENT_PART = "enhancement_part"
KEY_ENHANCEMENT_RANGE = "enhancement_range"
KEY_ENHANCEMENT_CUE = "enhancement_cue"
KEY_VISUAL_TEXT_ALL = "visual_text_all"
KEY_VISUAL_TEXT_PART = "visual_text_part"
KEY_VISUAL_TEXT_RANGE = "visual_text_range"

COLLECTION_SCOPE = "collection"

_SCHEMA_VERSION = 1

# Every legal (stage, key) pair, so an unrecognized choice fails closed rather
# than silently persisting a run-affecting selection no stage will ever read.
_KNOWN_CHOICES: frozenset[tuple[str, str]] = frozenset(
    {
        (STAGE_RUN, KEY_ASR_MODE),
        (STAGE_RUN, KEY_VISUAL_TEXT_ENABLED),
        (STAGE_SUBTITLES, KEY_SUBTITLE_TRACK),
        (STAGE_SUBTITLES, KEY_SUBTITLE_DECODER),
        (STAGE_AUDIO_ANALYSIS, KEY_AUDIO_STREAM),
        (STAGE_AUDIO_ANALYSIS, KEY_DIARIZATION_CANDIDATE),
        (STAGE_AUDIO_ANALYSIS, KEY_ROLE_METADATA),
        (STAGE_TRANSCRIPTION, KEY_TRANSCRIPTION_UPGRADE_ALL),
        (STAGE_ENHANCEMENT, KEY_ENHANCEMENT_PART),
        (STAGE_ENHANCEMENT, KEY_ENHANCEMENT_RANGE),
        (STAGE_ENHANCEMENT, KEY_ENHANCEMENT_CUE),
        (STAGE_VISUAL_TEXT, KEY_VISUAL_TEXT_ALL),
        (STAGE_VISUAL_TEXT, KEY_VISUAL_TEXT_PART),
        (STAGE_VISUAL_TEXT, KEY_VISUAL_TEXT_RANGE),
    }
)

# Keys that name at most one selection per scope; every other known key may
# carry several values in one scope (decoders, role metadata, scope selectors).
_SINGLE_VALUED: frozenset[str] = frozenset(
    {
        KEY_ASR_MODE,
        KEY_VISUAL_TEXT_ENABLED,
        KEY_SUBTITLE_TRACK,
        KEY_AUDIO_STREAM,
        KEY_DIARIZATION_CANDIDATE,
        KEY_TRANSCRIPTION_UPGRADE_ALL,
        KEY_VISUAL_TEXT_ALL,
    }
)

# Keys whose only legal values are the strings "true" and "false".
_BOOLEAN_KEYS: frozenset[str] = frozenset(
    {KEY_VISUAL_TEXT_ENABLED, KEY_TRANSCRIPTION_UPGRADE_ALL, KEY_VISUAL_TEXT_ALL}
)


@dataclass(frozen=True)
class RunChoice:
    """One run-affecting selection fixed at plan confirmation with provenance.

    ``scope`` is the Part it applies to, or :data:`COLLECTION_SCOPE` for a
    collection-level or mode-level choice.
    """

    stage: str
    key: str
    scope: str
    value: str
    provenance: ChoiceProvenance

    def as_json(self) -> dict[str, str]:
        return {
            "stage": self.stage,
            "key": self.key,
            "scope": self.scope,
            "value": self.value,
            "provenance": self.provenance.value,
        }

    @classmethod
    def from_json(cls, value: object) -> RunChoice:
        if not isinstance(value, dict):
            raise RunChoicesError("run_choices_invalid", "A run choice must be a JSON object.")
        try:
            return cls(
                stage=_required_string(value, "stage"),
                key=_required_string(value, "key"),
                scope=_required_string(value, "scope"),
                value=_required_string(value, "value"),
                provenance=ChoiceProvenance(_required_string(value, "provenance")),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RunChoicesError(
                "run_choices_invalid", "A run choice has an invalid schema."
            ) from error


@dataclass(frozen=True)
class ChoiceGap:
    """A run-affecting choice a mode needs but the plan does not carry."""

    stage: str
    key: str
    reason: str

    def as_json(self) -> dict[str, str]:
        return {"stage": self.stage, "key": self.key, "reason": self.reason}


@dataclass(frozen=True)
class RunPlanChoices:
    """The immutable, canonically ordered set of front-loaded plan choices."""

    choices: tuple[RunChoice, ...]

    @classmethod
    def build(cls, choices: tuple[RunChoice, ...]) -> RunPlanChoices:
        """Validate, deduplicate, and canonically order raw choices.

        Rejects unknown ``(stage, key)`` pairs, conflicting single-valued
        choices, duplicate multi-valued choices, and out-of-range values, so an
        ill-formed selection can never reach a confirmed plan.
        """

        for choice in choices:
            _validate_choice(choice)
        _reject_conflicts(choices)
        ordered = tuple(sorted(choices, key=_canonical_key))
        return cls(ordered)

    def as_json(self) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "choices": [choice.as_json() for choice in self.choices],
        }

    @classmethod
    def from_json(cls, value: object) -> RunPlanChoices:
        if not isinstance(value, dict):
            raise RunChoicesError("run_choices_invalid", "Run choices must be a JSON object.")
        # An absent document (older plans) reads as empty; a present one must be
        # the version this loader understands, matching the repo's load-time
        # schema_version guard rather than parsing a future shape as this one.
        if value and value.get("schema_version") != _SCHEMA_VERSION:
            raise RunChoicesError(
                "run_choices_invalid", "Run choices have an unsupported schema version."
            )
        raw = value.get("choices", [])
        if not isinstance(raw, list):
            raise RunChoicesError("run_choices_invalid", "Run choices must be a JSON array.")
        return cls.build(tuple(RunChoice.from_json(item) for item in raw))

    def values(self, stage: str, key: str, scope: str | None = None) -> tuple[RunChoice, ...]:
        """Return every choice for a stage and key, optionally within one scope."""

        return tuple(
            choice
            for choice in self.choices
            if choice.stage == stage
            and choice.key == key
            and (scope is None or choice.scope == scope)
        )

    def single(self, stage: str, key: str, scope: str) -> RunChoice | None:
        """Return the sole choice for a single-valued stage, key, and scope.

        The scope is explicit on purpose: per-Part single-valued keys carry one
        choice *per Part*, so a defaulted collection scope would silently miss
        a Part-scoped selection.
        """

        matches = self.values(stage, key, scope)
        return matches[0] if matches else None

    def asr_mode(self) -> AsrMode | None:
        choice = self.single(STAGE_RUN, KEY_ASR_MODE, COLLECTION_SCOPE)
        return AsrMode(choice.value) if choice is not None else None

    def visual_text_enabled(self) -> bool | None:
        choice = self.single(STAGE_RUN, KEY_VISUAL_TEXT_ENABLED, COLLECTION_SCOPE)
        return choice.value == "true" if choice is not None else None


def missing_required_choices(choices: RunPlanChoices) -> tuple[ChoiceGap, ...]:
    """Report choices a mode genuinely needs but the plan omits.

    The rule is mode-driven, not topology-driven: a choice a mode never uses is
    never a gap, so a plan can be confirmed while still lacking a choice that
    only another mode would require. Each gap is what the orchestrator turns
    into a Run decision pause instead of prompting mid-run.
    """

    gaps: list[ChoiceGap] = []
    mode = choices.asr_mode()
    if mode is None:
        gaps.append(ChoiceGap(STAGE_RUN, KEY_ASR_MODE, "asr_mode_required"))
    visual_enabled = choices.visual_text_enabled()
    if visual_enabled is None:
        gaps.append(ChoiceGap(STAGE_RUN, KEY_VISUAL_TEXT_ENABLED, "visual_text_toggle_required"))
    if mode is AsrMode.ENHANCEMENT and not _has_stage_choice(choices, STAGE_ENHANCEMENT):
        gaps.append(
            ChoiceGap(STAGE_ENHANCEMENT, KEY_ENHANCEMENT_PART, "enhancement_scope_required")
        )
    if visual_enabled and not _has_stage_choice(choices, STAGE_VISUAL_TEXT):
        gaps.append(ChoiceGap(STAGE_VISUAL_TEXT, KEY_VISUAL_TEXT_ALL, "visual_text_scope_required"))
    return tuple(gaps)


@dataclass(frozen=True)
class SubtitleStageParameters:
    """Subtitle-stage selectors in the shape ``process_subtitles`` accepts."""

    select: tuple[str, ...]
    decoders: tuple[str, ...]


@dataclass(frozen=True)
class AudioAnalysisStageParameters:
    """Audio-analysis selectors in the shape ``analyze_audio`` accepts."""

    audio_stream: tuple[str, ...]
    diarization_candidate: str | None
    role_metadata: tuple[str, ...]


@dataclass(frozen=True)
class TranscriptionStageParameters:
    """Transcription selectors in the shape ``transcribe`` accepts."""

    upgrade_all: bool


@dataclass(frozen=True)
class EnhancementStageParameters:
    """Enhancement selectors in the shape ``enhance`` accepts."""

    part_selectors: tuple[str, ...]
    range_selectors: tuple[str, ...]
    cue_selectors: tuple[str, ...]


@dataclass(frozen=True)
class VisualTextStageParameters:
    """Visual-text selectors in the shape ``run_visual_text`` accepts."""

    all_parts: bool
    part_selectors: tuple[str, ...]
    range_selectors: tuple[str, ...]


def subtitle_stage_parameters(choices: RunPlanChoices) -> SubtitleStageParameters:
    """Translate stored subtitle choices into the expert command's selectors."""

    return SubtitleStageParameters(
        select=tuple(
            _scoped(choice, "=") for choice in choices.values(STAGE_SUBTITLES, KEY_SUBTITLE_TRACK)
        ),
        decoders=tuple(
            _scoped(choice, "=") for choice in choices.values(STAGE_SUBTITLES, KEY_SUBTITLE_DECODER)
        ),
    )


def audio_analysis_stage_parameters(choices: RunPlanChoices) -> AudioAnalysisStageParameters:
    """Translate stored audio-analysis choices into the expert command's selectors."""

    candidate = choices.single(STAGE_AUDIO_ANALYSIS, KEY_DIARIZATION_CANDIDATE, COLLECTION_SCOPE)
    return AudioAnalysisStageParameters(
        audio_stream=tuple(
            _scoped(choice, "=")
            for choice in choices.values(STAGE_AUDIO_ANALYSIS, KEY_AUDIO_STREAM)
        ),
        diarization_candidate=candidate.value if candidate is not None else None,
        role_metadata=tuple(
            _scoped(choice, "=")
            for choice in choices.values(STAGE_AUDIO_ANALYSIS, KEY_ROLE_METADATA)
        ),
    )


def transcription_stage_parameters(choices: RunPlanChoices) -> TranscriptionStageParameters:
    """Translate the stored transcription upgrade flag into the expert command."""

    return TranscriptionStageParameters(
        upgrade_all=_bool_flag(choices, STAGE_TRANSCRIPTION, KEY_TRANSCRIPTION_UPGRADE_ALL)
    )


def enhancement_stage_parameters(choices: RunPlanChoices) -> EnhancementStageParameters:
    """Translate stored enhancement scope choices into the expert command's selectors."""

    return EnhancementStageParameters(
        part_selectors=tuple(
            choice.value for choice in choices.values(STAGE_ENHANCEMENT, KEY_ENHANCEMENT_PART)
        ),
        range_selectors=tuple(
            _scoped(choice, ":")
            for choice in choices.values(STAGE_ENHANCEMENT, KEY_ENHANCEMENT_RANGE)
        ),
        cue_selectors=tuple(
            _scoped(choice, ":")
            for choice in choices.values(STAGE_ENHANCEMENT, KEY_ENHANCEMENT_CUE)
        ),
    )


def visual_text_stage_parameters(choices: RunPlanChoices) -> VisualTextStageParameters:
    """Translate stored visual-text scope choices into the expert command's selectors."""

    return VisualTextStageParameters(
        all_parts=_bool_flag(choices, STAGE_VISUAL_TEXT, KEY_VISUAL_TEXT_ALL),
        part_selectors=tuple(
            choice.value for choice in choices.values(STAGE_VISUAL_TEXT, KEY_VISUAL_TEXT_PART)
        ),
        range_selectors=tuple(
            _scoped(choice, ":")
            for choice in choices.values(STAGE_VISUAL_TEXT, KEY_VISUAL_TEXT_RANGE)
        ),
    )


def _validate_choice(choice: RunChoice) -> None:
    if (choice.stage, choice.key) not in _KNOWN_CHOICES:
        raise RunChoicesError("unknown_choice", f"Unknown run choice: {choice.stage}/{choice.key}.")
    if not choice.scope or not choice.value:
        raise RunChoicesError("run_choices_invalid", "A run choice needs a scope and a value.")
    if choice.key == KEY_ASR_MODE and choice.value not in tuple(mode.value for mode in AsrMode):
        raise RunChoicesError("invalid_choice_value", f"Unknown ASR mode: {choice.value}.")
    if choice.key in _BOOLEAN_KEYS and choice.value not in ("true", "false"):
        raise RunChoicesError("invalid_choice_value", f"{choice.key} must be 'true' or 'false'.")


def _reject_conflicts(choices: tuple[RunChoice, ...]) -> None:
    single_seen: set[tuple[str, str, str]] = set()
    full_seen: set[tuple[str, str, str, str]] = set()
    for choice in choices:
        full = (choice.stage, choice.key, choice.scope, choice.value)
        if full in full_seen:
            raise RunChoicesError(
                "duplicate_choice",
                f"Duplicate run choice: {choice.stage}/{choice.key}/{choice.scope}.",
            )
        full_seen.add(full)
        if choice.key in _SINGLE_VALUED:
            scoped = (choice.stage, choice.key, choice.scope)
            if scoped in single_seen:
                raise RunChoicesError(
                    "conflicting_choice",
                    f"Conflicting run choice: {choice.stage}/{choice.key}/{choice.scope}.",
                )
            single_seen.add(scoped)


def _has_stage_choice(choices: RunPlanChoices, stage: str) -> bool:
    return any(choice.stage == stage for choice in choices.choices)


def _scoped(choice: RunChoice, separator: str) -> str:
    """Rebuild a ``SCOPE<sep>VALUE`` CLI selector from a Part-scoped choice."""

    return f"{choice.scope}{separator}{choice.value}"


def _bool_flag(choices: RunPlanChoices, stage: str, key: str) -> bool:
    """Read a collection-level boolean flag choice, defaulting to ``False``."""

    flag = choices.single(stage, key, COLLECTION_SCOPE)
    return flag is not None and flag.value == "true"


def _canonical_key(choice: RunChoice) -> tuple[str, str, str, str]:
    return (choice.stage, choice.key, choice.scope, choice.value)


def _required_string(value: dict[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"A run choice needs a non-empty string for {key!r}.")
    return result
