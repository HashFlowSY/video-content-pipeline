"""The in-process stage DAG over ``(stage, Part)`` atomic units.

A Run is a directed acyclic graph of Stage units composed from the existing
per-phase prototypes: source/plan revalidation, subtitles, audio-analysis,
either transcription or enhancement by run mode, text-analysis, and — only when
enabled — visual-text. The evidence stages here stop short of publication
projection, which is a later ticket. Each stage is invoked *in process* through
a :class:`StageExecutor` seam; nothing is ever spawned as a subprocess, and the
sixteen expert commands are untouched (the production executor composes their
underlying functions, and the offline tests substitute a controlled one).

The atomic unit of work, checkpointing, pause, and recovery is the Stage unit:
one stage applied to one Part, or the single collection-level revalidation
stage. Each unit carries a :class:`StageInvalidationKey` — its input hashes, the
hash of the stage's configuration subset, and a manually incremented Stage
version (ADR 0052). Keys are computed top-down so an upstream unit's digest
feeds its dependants' input hashes: changing one stage's configuration re-keys
that unit and everything downstream of it, while sibling Parts and upstream
units keep their keys and stay adoptable.

Adoption is strictly Run-scoped (:func:`adoptable_units`): a completed unit is
re-used only if it is recorded in *this run's* own state document and its
freshly recomputed invalidation key still matches. Retained workspaces from
manual per-phase commands are never scavenged, because nothing here reads the
filesystem to discover work — only the run's recorded units. The execution
engine (:func:`execute_stages`) checkpoints a unit only at its completed
boundary, isolates a failed Part's collapse from its siblings, and turns a
stage-required user decision into a Run decision pause (run ``incomplete`` with
a machine-readable required decision), all under the single-writer run state.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from video_content_pipeline.orchestration import RunLayout
from video_content_pipeline.planning import RunPlan
from video_content_pipeline.run_choices import (
    COLLECTION_SCOPE,
    KEY_ASR_MODE,
    KEY_VISUAL_TEXT_ENABLED,
    STAGE_AUDIO_ANALYSIS,
    STAGE_ENHANCEMENT,
    STAGE_RUN,
    STAGE_SUBTITLES,
    STAGE_TRANSCRIPTION,
    STAGE_VISUAL_TEXT,
    AsrMode,
    RunChoice,
    RunPlanChoices,
)
from video_content_pipeline.run_control import (
    ControlDirective,
    apply_cancel,
    apply_pause,
    observe_controls_at_boundary,
)
from video_content_pipeline.run_state import RunState, RunStateWriter, RunStatus

_KEY_SCHEMA_VERSION = 1


def _sha256_json(value: object) -> str:
    """Hash a value by its canonical JSON form (the repo's digest idiom)."""

    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class StageDagError(ValueError):
    """A stage-DAG failure with an auditable machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class StageName(StrEnum):
    """The stages composed by the evidence DAG, in canonical dependency order."""

    SOURCE_REVALIDATION = "source_revalidation"
    SUBTITLES = "subtitles"
    AUDIO_ANALYSIS = "audio_analysis"
    TRANSCRIPTION = "transcription"
    ENHANCEMENT = "enhancement"
    TEXT_ANALYSIS = "text_analysis"
    VISUAL_TEXT = "visual_text"


class StageScope(StrEnum):
    """Whether a stage runs once for the collection or once per Part."""

    COLLECTION = "collection"
    PER_PART = "per_part"


class UnitStatus(StrEnum):
    """The recorded disposition of a Stage unit in the run state document."""

    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


#: The manually incremented Stage version of each stage (ADR 0052). Any
#: behaviour change in a stage must bump its version here; otherwise a run
#: silently re-uses outputs produced by the old behaviour. This table is the
#: single place that discipline is enforced.
STAGE_VERSIONS: Mapping[StageName, int] = {
    StageName.SOURCE_REVALIDATION: 1,
    StageName.SUBTITLES: 1,
    StageName.AUDIO_ANALYSIS: 1,
    StageName.TRANSCRIPTION: 1,
    StageName.ENHANCEMENT: 1,
    StageName.TEXT_ANALYSIS: 1,
    StageName.VISUAL_TEXT: 1,
}

#: The scope of each stage: only source revalidation is collection-level; every
#: evidence stage after it applies once per Part.
_STAGE_SCOPES: Mapping[StageName, StageScope] = {
    StageName.SOURCE_REVALIDATION: StageScope.COLLECTION,
    StageName.SUBTITLES: StageScope.PER_PART,
    StageName.AUDIO_ANALYSIS: StageScope.PER_PART,
    StageName.TRANSCRIPTION: StageScope.PER_PART,
    StageName.ENHANCEMENT: StageScope.PER_PART,
    StageName.TEXT_ANALYSIS: StageScope.PER_PART,
    StageName.VISUAL_TEXT: StageScope.PER_PART,
}

#: Each stage's configuration-subset extractor, declared as the run-choice
#: ``(stage, key)`` selectors that stage's behaviour depends on. ``None`` for a
#: key means every key of that run-choice stage. A stage reads only the subset
#: named here, so a configuration change re-keys exactly the stages it can
#: affect — the mechanism behind "invalidates only affected stages" (ADR 0052).
_STAGE_CONFIG_SELECTORS: Mapping[StageName, tuple[tuple[str, str | None], ...]] = {
    StageName.SOURCE_REVALIDATION: (),
    StageName.SUBTITLES: ((STAGE_SUBTITLES, None),),
    StageName.AUDIO_ANALYSIS: ((STAGE_AUDIO_ANALYSIS, None),),
    StageName.TRANSCRIPTION: ((STAGE_RUN, KEY_ASR_MODE), (STAGE_TRANSCRIPTION, None)),
    StageName.ENHANCEMENT: ((STAGE_RUN, KEY_ASR_MODE), (STAGE_ENHANCEMENT, None)),
    StageName.TEXT_ANALYSIS: ((STAGE_RUN, KEY_ASR_MODE),),
    StageName.VISUAL_TEXT: ((STAGE_RUN, KEY_VISUAL_TEXT_ENABLED), (STAGE_VISUAL_TEXT, None)),
}


def stage_scope(stage: StageName) -> StageScope:
    """Return whether ``stage`` runs per-collection or per-Part."""

    return _STAGE_SCOPES[stage]


@dataclass(frozen=True)
class StageUnit:
    """One atomic unit: a stage applied to a Part, or a collection-level stage.

    ``scope`` is the Part ``source-id`` the unit covers, or
    :data:`~video_content_pipeline.run_choices.COLLECTION_SCOPE` for the
    collection-level revalidation stage.
    """

    stage: StageName
    scope: str

    @property
    def is_collection(self) -> bool:
        """Whether this is the single collection-level unit rather than a Part."""

        return _STAGE_SCOPES[self.stage] is StageScope.COLLECTION


@dataclass(frozen=True)
class StageInvalidationKey:
    """A Stage unit's invalidation key: input hashes, config hash, and version.

    Two units with equal keys have identical inputs, identical stage-scoped
    configuration, and the same stage behaviour version, so one may stand in for
    the other; any difference re-keys the unit and forces a re-run (ADR 0052).
    """

    stage: StageName
    stage_version: int
    input_hashes: tuple[str, ...]
    config_subset_hash: str

    def digest(self) -> str:
        """Return the stable digest used for equality and adoption checks.

        The digest is taken over the persisted form (:meth:`as_json`), so the
        digest and the on-disk record can never drift apart if a field is added.
        """

        return _sha256_json(self.as_json())

    def as_json(self) -> dict[str, object]:
        return {
            "schema_version": _KEY_SCHEMA_VERSION,
            "stage": self.stage.value,
            "stage_version": self.stage_version,
            "input_hashes": list(self.input_hashes),
            "config_subset_hash": self.config_subset_hash,
        }

    @classmethod
    def from_json(cls, value: object) -> StageInvalidationKey:
        if not isinstance(value, Mapping):
            raise StageDagError("invalidation_key_invalid", "A key must be a JSON object.")
        if value.get("schema_version") != _KEY_SCHEMA_VERSION:
            raise StageDagError(
                "invalidation_key_invalid",
                f"Invalidation key schema_version must be {_KEY_SCHEMA_VERSION}.",
            )
        stage_value = value.get("stage")
        if not isinstance(stage_value, str):
            raise StageDagError("invalidation_key_invalid", "A key needs a string stage.")
        try:
            stage = StageName(stage_value)
        except ValueError as error:
            raise StageDagError(
                "invalidation_key_invalid", f"Unknown stage {stage_value!r}."
            ) from error
        version = value.get("stage_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise StageDagError("invalidation_key_invalid", "stage_version must be an integer.")
        raw_hashes = value.get("input_hashes")
        if not isinstance(raw_hashes, Sequence) or isinstance(raw_hashes, str | bytes):
            raise StageDagError("invalidation_key_invalid", "input_hashes must be a list.")
        hashes = tuple(_required_hash(item) for item in raw_hashes)
        config_hash = value.get("config_subset_hash")
        if not isinstance(config_hash, str):
            raise StageDagError("invalidation_key_invalid", "config_subset_hash must be a string.")
        return cls(
            stage=stage,
            stage_version=version,
            input_hashes=hashes,
            config_subset_hash=config_hash,
        )


def _required_hash(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise StageDagError("invalidation_key_invalid", "Each input hash must be a string.")
    return value


def _present_stages(choices: RunPlanChoices) -> tuple[StageName, ...]:
    """Return the stages a plan's mode selects, in canonical dependency order."""

    mode = choices.asr_mode()
    if mode is None:
        raise StageDagError(
            "missing_asr_mode",
            "A confirmed plan must fix an ASR mode before its DAG can be built.",
        )
    order: list[StageName] = [
        StageName.SOURCE_REVALIDATION,
        StageName.SUBTITLES,
        StageName.AUDIO_ANALYSIS,
    ]
    order.append(StageName.ENHANCEMENT if mode is AsrMode.ENHANCEMENT else StageName.TRANSCRIPTION)
    order.append(StageName.TEXT_ANALYSIS)
    if choices.visual_text_enabled():
        order.append(StageName.VISUAL_TEXT)
    return tuple(order)


def _part_scopes(plan: RunPlan) -> tuple[str, ...]:
    scopes = tuple(artifact.source_id for artifact in plan.source_artifacts)
    if not scopes:
        raise StageDagError("empty_plan", "A run plan needs at least one Part.")
    if len(set(scopes)) != len(scopes):
        raise StageDagError("duplicate_part", "A run plan's Part source-ids must be distinct.")
    return scopes


def plan_stage_units(plan: RunPlan) -> tuple[StageUnit, ...]:
    """Build the plan's Stage units in a valid topological (execution) order.

    Units are emitted stage by stage in dependency order; within a per-Part
    stage they follow the plan's Part order. Because a unit's only dependency is
    the previous present stage (its collection unit or its own Part's unit),
    emitting stage-by-stage guarantees every dependency precedes its dependants.
    """

    stages = _present_stages(plan.run_choices)
    parts = _part_scopes(plan)
    units: list[StageUnit] = []
    for stage in stages:
        if _STAGE_SCOPES[stage] is StageScope.COLLECTION:
            units.append(StageUnit(stage, COLLECTION_SCOPE))
        else:
            units.extend(StageUnit(stage, part) for part in parts)
    return tuple(units)


def _config_subset_choices(
    stage: StageName, choices: RunPlanChoices, scope: str
) -> tuple[RunChoice, ...]:
    """Return the choices in ``stage``'s configuration subset for one scope.

    A collection-scoped choice always applies; a Part-scoped choice applies only
    to its own Part's unit, so re-configuring one Part re-keys only that Part.
    """

    selectors = _STAGE_CONFIG_SELECTORS[stage]
    matched: list[RunChoice] = []
    for choice in choices.choices:
        if choice.scope != COLLECTION_SCOPE and choice.scope != scope:
            continue
        for selector_stage, selector_key in selectors:
            if choice.stage == selector_stage and (
                selector_key is None or choice.key == selector_key
            ):
                matched.append(choice)
                break
    return tuple(matched)


def _config_subset_hash(stage: StageName, choices: RunPlanChoices, scope: str) -> str:
    subset = _config_subset_choices(stage, choices, scope)
    canonical = sorted(choice.config_identity() for choice in subset)
    return _sha256_json([list(identity) for identity in canonical])


def compute_invalidation_keys(plan: RunPlan) -> dict[StageUnit, StageInvalidationKey]:
    """Compute every unit's invalidation key, cascading upstream digests.

    A per-Part unit's input hashes are its Part content hash plus the digest of
    the previous present stage's unit for the same Part (or the collection
    revalidation unit). This is why a re-keyed upstream unit re-keys everything
    downstream of it while leaving sibling Parts and upstream units untouched.
    """

    stages = _present_stages(plan.run_choices)
    parts = _part_scopes(plan)
    part_hash = {artifact.source_id: artifact.sha256 for artifact in plan.source_artifacts}
    collection_hashes = tuple(sorted(artifact.sha256 for artifact in plan.source_artifacts))
    keys: dict[StageUnit, StageInvalidationKey] = {}
    for index, stage in enumerate(stages):
        previous = stages[index - 1] if index > 0 else None
        scopes = (COLLECTION_SCOPE,) if _STAGE_SCOPES[stage] is StageScope.COLLECTION else parts
        for scope in scopes:
            if previous is None:
                input_hashes: tuple[str, ...] = collection_hashes
            else:
                dependency = _dependency_unit(previous, scope)
                input_hashes = (part_hash[scope], keys[dependency].digest())
            keys[StageUnit(stage, scope)] = StageInvalidationKey(
                stage=stage,
                stage_version=STAGE_VERSIONS[stage],
                input_hashes=input_hashes,
                config_subset_hash=_config_subset_hash(stage, plan.run_choices, scope),
            )
    return keys


def _dependency_unit(previous: StageName, scope: str) -> StageUnit:
    if _STAGE_SCOPES[previous] is StageScope.COLLECTION:
        return StageUnit(previous, COLLECTION_SCOPE)
    return StageUnit(previous, scope)


# --- Checkpoints and run-scoped adoption ------------------------------------


@dataclass(frozen=True)
class RecordedUnit:
    """A Stage unit as recorded in this run's state document (read back)."""

    unit: StageUnit
    status: UnitStatus
    key: StageInvalidationKey
    report_id: str | None = None


def unit_record(
    unit: StageUnit,
    status: UnitStatus,
    key: StageInvalidationKey,
    report_id: str | None = None,
) -> dict[str, object]:
    """Render a Stage unit checkpoint for the run state's ``stage_units`` slot.

    A completed unit carries the ``report_id`` its stage function produced so a
    later resume can rebuild the composition's stage-to-report chain for the units
    it adopts (a resumed downstream stage reads its upstream stages' report ids
    from that chain, not by re-running them).
    """

    record: dict[str, object] = {
        "stage": unit.stage.value,
        "scope": unit.scope,
        "status": status.value,
        "invalidation_key": key.as_json(),
    }
    if report_id is not None:
        record["report_id"] = report_id
    return record


def read_recorded_units(state: RunState) -> dict[StageUnit, RecordedUnit]:
    """Parse this run's recorded Stage units from its state document.

    Reads only the run's own state — never the filesystem — so manual per-phase
    workspaces can never be scavenged into a run.
    """

    recorded: dict[StageUnit, RecordedUnit] = {}
    for entry in state.stage_units:
        stage_value = entry.get("stage")
        scope = entry.get("scope")
        status_value = entry.get("status")
        if not isinstance(stage_value, str) or not isinstance(scope, str):
            raise StageDagError(
                "stage_unit_invalid", "A stage unit needs a string stage and scope."
            )
        if not isinstance(status_value, str):
            raise StageDagError("stage_unit_invalid", "A stage unit needs a string status.")
        try:
            stage = StageName(stage_value)
            status = UnitStatus(status_value)
        except ValueError as error:
            raise StageDagError(
                "stage_unit_invalid", f"Unknown stage or status in {entry!r}."
            ) from error
        report_id = entry.get("report_id")
        if report_id is not None and not isinstance(report_id, str):
            raise StageDagError("stage_unit_invalid", "A stage unit report id must be a string.")
        unit = StageUnit(stage, scope)
        recorded[unit] = RecordedUnit(
            unit=unit,
            status=status,
            key=StageInvalidationKey.from_json(entry.get("invalidation_key")),
            report_id=report_id,
        )
    return recorded


def adoptable_units(
    recorded: Mapping[StageUnit, RecordedUnit],
    fresh: Mapping[StageUnit, StageInvalidationKey],
) -> frozenset[StageUnit]:
    """Return the recorded units this run may re-use without re-running them.

    A unit is adoptable only if it is recorded ``completed`` in this run's state
    and its recorded invalidation key still equals the freshly recomputed one
    (the revalidation-before-use pattern). Because a re-keyed upstream unit
    cascades into its dependants' fresh keys, a stale downstream unit fails this
    equality automatically — adoption is downstream-aware without any extra
    bookkeeping.
    """

    adoptable: set[StageUnit] = set()
    for unit, record in recorded.items():
        if record.status is not UnitStatus.COMPLETED:
            continue
        fresh_key = fresh.get(unit)
        if fresh_key is None:
            continue
        if record.key.digest() == fresh_key.digest():
            adoptable.add(unit)
    return frozenset(adoptable)


# --- Execution engine -------------------------------------------------------


class StageResultKind(StrEnum):
    """What a stage executor reports for one unit."""

    COMPLETED = "completed"
    FAILED = "failed"
    DECISION_REQUIRED = "decision_required"


@dataclass(frozen=True)
class StageResult:
    """The outcome an executor returns for one Stage unit.

    A raised exception is *not* a result: it is a mid-unit interruption that
    leaves no checkpoint. ``FAILED`` is an explicit, recorded per-Part failure.
    ``DECISION_REQUIRED`` carries the stage's own retained pause payload verbatim
    in ``required_decision`` — its ``reason`` and expected ``decision`` token
    (for example ``{"reason": "resource_envelope_exceeded", "decision":
    "resource_configuration_changed"}``). The engine does not normalize or
    reinvent that vocabulary; the per-phase stages own it, so ``vcp resume``
    can later match ``--decision`` against the recorded token.
    """

    kind: StageResultKind
    detail: Mapping[str, object] = field(default_factory=dict)
    required_decision: Mapping[str, object] | None = None

    @classmethod
    def completed(cls, detail: Mapping[str, object] | None = None) -> StageResult:
        return cls(StageResultKind.COMPLETED, detail=dict(detail or {}))

    @classmethod
    def failed(cls, detail: Mapping[str, object] | None = None) -> StageResult:
        return cls(StageResultKind.FAILED, detail=dict(detail or {}))

    @classmethod
    def decision_required(cls, required_decision: Mapping[str, object]) -> StageResult:
        """Report a stage's retained pause, carrying its payload unchanged."""

        if not required_decision:
            raise StageDagError(
                "empty_required_decision",
                "A decision-required result must carry the stage's pause payload.",
            )
        return cls(StageResultKind.DECISION_REQUIRED, required_decision=dict(required_decision))


#: A stage executor invokes one stage for one unit in process and reports the
#: outcome. The production executor composes the existing per-phase functions;
#: offline tests substitute a controlled one. It never spawns a subprocess.
StageExecutor = Callable[[StageUnit, StageInvalidationKey], StageResult]


class StageRunDisposition(StrEnum):
    """How an :func:`execute_stages` pass ended."""

    COMPLETED_ALL = "completed_all"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    DECISION_REQUIRED = "decision_required"


@dataclass(frozen=True)
class StageRunResult:
    """The result of one execution pass over the DAG.

    ``COMPLETED_ALL`` means every unit was processed (some Parts may have
    failed — see ``failed_scopes``); the caller classifies the terminal run
    status from the gate outcomes. The pause, cancel, and decision dispositions
    have already driven their state transitions.
    """

    disposition: StageRunDisposition
    failed_scopes: frozenset[str]
    required_decision: Mapping[str, object] | None = None


def execute_stages(
    *,
    writer: RunStateWriter,
    layout: RunLayout,
    plan: RunPlan,
    executor: StageExecutor,
    on_boundary: Callable[[], ControlDirective] | None = None,
) -> StageRunResult:
    """Execute the plan's DAG in process, checkpointing at each unit boundary.

    The run must already be ``running`` (the caller holds the Heavy-task lock
    and drove ``planned -> queued -> running`` or ``paused -> running``). Units
    already adoptable from this run's recorded state are skipped. Before each
    remaining unit the engine observes control requests at the boundary and
    pauses or cancels if asked. A ``COMPLETED`` unit is checkpointed to the run
    state; a ``FAILED`` per-Part unit collapses only its own Part's downstream
    (recorded ``blocked``), leaving sibling Parts to continue; a
    ``DECISION_REQUIRED`` unit records a Run decision pause and stops.
    """

    if writer.state.status is not RunStatus.RUNNING:
        raise StageDagError("run_not_running", "The DAG runs only from a running run state.")
    observe = on_boundary or (lambda: observe_controls_at_boundary(writer, layout))
    units = plan_stage_units(plan)
    fresh = compute_invalidation_keys(plan)
    recorded = read_recorded_units(writer.state)
    adoptable = adoptable_units(recorded, fresh)
    all_part_scopes = {unit.scope for unit in units if not unit.is_collection}

    progress: dict[StageUnit, dict[str, object]] = {
        unit: unit_record(unit, UnitStatus.COMPLETED, fresh[unit], recorded[unit].report_id)
        for unit in units
        if unit in adoptable
    }

    def persist() -> None:
        writer.set_progress(stage_units=[progress[unit] for unit in units if unit in progress])

    if progress:
        persist()

    failed_scopes: set[str] = set()
    blocked_scopes: set[str] = set()
    for unit in units:
        if unit in adoptable:
            continue
        if not unit.is_collection and unit.scope in blocked_scopes:
            progress[unit] = unit_record(unit, UnitStatus.BLOCKED, fresh[unit])
            persist()
            continue
        directive = observe()
        if directive is ControlDirective.PAUSE:
            apply_pause(writer)
            return StageRunResult(StageRunDisposition.PAUSED, frozenset(failed_scopes))
        if directive is ControlDirective.CANCEL:
            apply_cancel(writer)
            return StageRunResult(StageRunDisposition.CANCELLED, frozenset(failed_scopes))
        result = executor(unit, fresh[unit])
        if result.kind is StageResultKind.COMPLETED:
            report_id = result.detail.get("report_id")
            progress[unit] = unit_record(
                unit,
                UnitStatus.COMPLETED,
                fresh[unit],
                report_id if isinstance(report_id, str) else None,
            )
            persist()
        elif result.kind is StageResultKind.FAILED:
            progress[unit] = unit_record(unit, UnitStatus.FAILED, fresh[unit])
            persist()
            failed_scopes.add(unit.scope)
            if unit.is_collection:
                blocked_scopes.update(all_part_scopes)
            else:
                blocked_scopes.add(unit.scope)
        else:
            required = _required_decision(unit, result)
            writer.record_decision_pause(required)
            return StageRunResult(
                StageRunDisposition.DECISION_REQUIRED, frozenset(failed_scopes), required
            )
    return StageRunResult(StageRunDisposition.COMPLETED_ALL, frozenset(failed_scopes))


def _required_decision(unit: StageUnit, result: StageResult) -> dict[str, object]:
    if result.required_decision is None:  # pragma: no cover - guarded by the factory
        raise StageDagError("decision_missing", "A decision-required result must name a decision.")
    # Carry the stage's retained pause payload unchanged and annotate which unit
    # raised it, so the recorded required decision is both the stage's own
    # vocabulary and locatable to a (stage, Part).
    required: dict[str, object] = dict(result.required_decision)
    required["stage"] = unit.stage.value
    required["scope"] = unit.scope
    return required
