"""Phase 10 Workstream A: serialization round-trip and rejection properties.

This module is the second plan-mandated property target (spec §Workstream A.2):
for every serialize/deserialize pair persisted across the pipeline, a
hypothesis-generated valid object must satisfy

    object -> serialize -> JSON text -> deserialize -> equal object,

and *structured* mutations of a valid document (dropped field, hostile type,
bogus enum token, non-object top level) must fail with the owning module's
typed reason class — never an unhandled ``KeyError`` / ``TypeError`` /
``AttributeError`` escaping the loader. Per grilling Q17 this deliberately
replaces building a generic JSON-schema framework: each pair carries a small
hand-written strategy and its own error contract, enumerated in :data:`PAIRS`.

Explicit pair inventory (serializer ↔ deserializer, enumerated by grepping
``def as_json`` / ``def from_json`` and the path-based ``read_*`` / ``load_*``
loaders in ``src/``):

* ``URLAuthorizationEvidence.as_json`` ↔ ``.from_json`` (url_policy)
* ``StageInvalidationKey.as_json`` ↔ ``.from_json`` (stage_dag)
* ``ProjectionInvalidationKey.as_json`` ↔ ``.from_json`` (publication_projection)
* ``PreprocessingProfile.as_json`` ↔ ``.from_json`` (audio_derivation)
* ``RunChoice.as_json`` ↔ ``.from_json`` (run_choices)
* ``RunPlanChoices.as_json`` ↔ ``.from_json`` (run_choices)
* ``ManifestArtifact.as_json`` ↔ ``.from_json`` (publication)
* ``RunBundleManifest.as_json`` ↔ ``.from_json`` (publication)
* ``LatestPointer.as_json`` ↔ ``.from_json`` (publication)
* ``PlanInspectionEvidence.as_json`` ↔ ``.from_json`` (inspection)
* ``SubtitleCandidate.as_json`` ↔ ``.from_json`` (subtitle_pipeline)
* ``SubtitleCandidateReport.as_json`` ↔ ``.from_json`` (subtitle_pipeline;
  the reader takes the project-owned ``report_path`` out of band, so a fixed
  path is used and the JSON copy is verified to reproduce it)
* ``RunState.to_document`` ↔ ``read_run_state`` (run_state; the run document)
* one journal event line ↔ ``read_journal`` (run_state; the append-only
  ``events.jsonl`` record — the writer's private ``_append_event`` is the
  serializer, mirrored here as :func:`_event_document`, so a shape drift fails
  loudly through this round-trip)
* ``PlanReport.as_json`` ↔ ``load_plan_report`` (planning; the "plans" audit
  record — an aggregate over source artifacts, tools, URL evidence, inspection
  evidence and run choices)
* ``RunPlan.as_json`` ↔ ``load_run_plan`` (planning; the confirmed plan)
* ``LockHolder.to_document`` ↔ ``.from_document`` (heavy_task_lock; the
  ``work/heavy-task.lock`` holder — a ``to_document``/``from_document`` pair, so
  it is reached by grepping the ``*_document`` idiom, not ``as_json``)

Exclusions — ``as_json`` documents with *no* deserializer in ``src/``, i.e.
write-only audit output. Each is a report *rendered* for humans/auditors and
never read back into its object, so it has no round-trip to prove; its nested
value objects are already covered above wherever a loader does read them back:

* ``run_reports`` (processing report, run inventory, quality report) — the
  Minimal RunBundle floor; published, then only re-hashed, never re-parsed.
* ``publication_projection.ProjectionResult`` / ``ProjectedArtifact`` — projected
  into the manifest (whose ``ManifestArtifact`` round-trip *is* covered) and
  published; never loaded back as a projection.
* the per-phase analysis reports (``text_analysis``, ``text_aggregation``,
  ``text_generation``, ``text_reanalysis``, ``transcription*``, ``enhancement``,
  ``visual_*``, ``audio_analysis``, ``capabilities``, ``host_read_upgrade``,
  ``evidence``, ``source``, ``planning`` sub-records) — write-only audit
  fragments; the loaders that exist for a few (``load_text_analysis_report`` …)
  read a purpose-built projection, not a faithful object copy, so they are not
  round-trip pairs.
* ``run_recovery`` (``RunDiagnosis`` / ``RecoveryOutcome`` / ``ArtifactRepair``)
  and ``run_loop.RunOutcome`` ``to_document`` outputs — recovery/outcome audit
  records with no reader; their data lands inside journal events, whose
  round-trip is covered opaquely by ``journal_event`` above.

The properties run under the deterministic gate profile (imported for its
registration side effect), so two runs draw the identical example sequence.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.support import hypothesis_profiles  # noqa: F401  (registers the gate profile)
from video_content_pipeline.audio_derivation import (
    AnalysisAudioDerivationError,
    PreprocessingProfile,
)
from video_content_pipeline.coverage import CoverageDiagnostic, StreamCoverage
from video_content_pipeline.external_tools import PinnedExternalTool
from video_content_pipeline.heavy_task_lock import HeavyTaskLockError, LockHolder
from video_content_pipeline.inspection import PlanInspectionEvidence, SubtitleTrackCandidate
from video_content_pipeline.planning import (
    PlanningDiagnostic,
    PlanningError,
    PlanReport,
    PlanState,
    RunPlan,
    ThreePointEstimate,
    load_plan_report,
    load_run_plan,
)
from video_content_pipeline.probe import ProbeDocument
from video_content_pipeline.publication import (
    LatestPointer,
    ManifestArtifact,
    PublicationError,
    RunBundleManifest,
)
from video_content_pipeline.publication_projection import (
    ArtifactStatus,
    ProjectionInvalidationKey,
    PublicationProjectionError,
    TimingBasis,
    TimingView,
)
from video_content_pipeline.run_choices import (
    _BOOLEAN_KEYS,
    _KNOWN_CHOICES,
    KEY_ASR_MODE,
    AsrMode,
    ChoiceProvenance,
    RunChoice,
    RunChoicesError,
    RunPlanChoices,
)
from video_content_pipeline.run_state import (
    EventKind,
    RunEvent,
    RunState,
    RunStateError,
    RunStatus,
    read_journal,
    read_run_state,
)
from video_content_pipeline.source import DiskHeadroom, SourceArtifact
from video_content_pipeline.stage_dag import StageDagError, StageInvalidationKey, StageName
from video_content_pipeline.subtitle_pipeline import (
    CandidateReportState,
    CandidateState,
    CaptionTimeCoverage,
    SubtitleCandidate,
    SubtitleCandidateReport,
    SubtitlePartReport,
    SubtitlePartState,
    SubtitleReportError,
    SubtitleTrackSelection,
)
from video_content_pipeline.subtitles import FormatProjectionLoss
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.url_policy import (
    RedactedSourceProvenance,
    URLAccessMode,
    URLAuthorizationEvidence,
)

# --- shared leaf strategies -------------------------------------------------

_INT_BOUND = 10**6

#: Non-empty short text — the shape every ``_required_str`` / ``_required_string``
#: loader accepts. Printable ASCII keeps generated JSON keys and values simple.
_nonempty_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=1,
    max_size=6,
)
_optional_nonempty_text = st.none() | _nonempty_text

#: JSON scalars that survive a text round-trip by ``==`` (floats deliberately
#: excluded: their equality after ``json.dumps``/``loads`` is not guaranteed).
_json_scalars = st.none() | st.booleans() | st.integers(-1000, 1000) | _nonempty_text
_json_objects = st.dictionaries(keys=_nonempty_text, values=_json_scalars, max_size=3)

_exact_times = st.builds(
    ExactTime,
    st.integers(-_INT_BOUND, _INT_BOUND),
    st.integers(1, _INT_BOUND),
)


@st.composite
def _intervals(draw: st.DrawFn) -> HalfOpenInterval:
    """A non-empty half-open interval (start strictly before end)."""

    start = draw(_exact_times)
    delta = ExactTime(draw(st.integers(1, _INT_BOUND)), draw(st.integers(1, _INT_BOUND)))
    return HalfOpenInterval(start, start + delta)


_planning_diagnostics = st.builds(PlanningDiagnostic, reason=_nonempty_text, message=_nonempty_text)


# --- per-pair object strategies ---------------------------------------------

_url_authorization_evidence = st.builds(
    URLAuthorizationEvidence,
    mode=st.sampled_from(list(URLAccessMode)),
    provenance=st.builds(
        RedactedSourceProvenance,
        scheme=_nonempty_text,
        host=_nonempty_text,
        path=_nonempty_text,
        transport_integrity_verified=st.booleans(),
    ),
)

_stage_invalidation_keys = st.builds(
    StageInvalidationKey,
    stage=st.sampled_from(list(StageName)),
    stage_version=st.integers(-_INT_BOUND, _INT_BOUND),
    input_hashes=st.tuples() | st.lists(_nonempty_text, max_size=3).map(tuple),
    config_subset_hash=_nonempty_text,
)

_projection_invalidation_keys = st.builds(
    ProjectionInvalidationKey,
    stage_version=st.integers(-_INT_BOUND, _INT_BOUND),
    input_hashes=st.lists(_nonempty_text, max_size=3).map(tuple),
    config_subset_hash=_nonempty_text,
)

_preprocessing_profiles = st.builds(
    PreprocessingProfile,
    profile_id=_nonempty_text,
    sample_rate=st.integers(1, _INT_BOUND),
    channel_count=st.integers(1, 8),
    loudness_mode=st.just("preserve"),
    chunk_samples=st.integers(1, _INT_BOUND),
)

_run_choices = st.builds(
    RunChoice,
    stage=_nonempty_text,
    key=_nonempty_text,
    scope=_nonempty_text,
    value=_nonempty_text,
    provenance=st.sampled_from(list(ChoiceProvenance)),
)


def _choice_value(key: str) -> st.SearchStrategy[str]:
    if key == KEY_ASR_MODE:
        return st.sampled_from([mode.value for mode in AsrMode])
    if key in _BOOLEAN_KEYS:
        return st.sampled_from(["true", "false"])
    return _nonempty_text


@st.composite
def _run_plan_choices(draw: st.DrawFn) -> RunPlanChoices:
    """A valid, conflict-free set of front-loaded choices.

    Each known ``(stage, key)`` is drawn at most once, always at collection
    scope, so ``RunPlanChoices.build`` never hits a duplicate or single-valued
    conflict — the round-trip exercises canonical ordering, not the rejection
    paths (those are covered by the mutation properties).
    """

    included = draw(st.lists(st.sampled_from(sorted(_KNOWN_CHOICES)), unique=True, max_size=6))
    choices = tuple(
        RunChoice(
            stage=stage,
            key=key,
            scope="collection",
            value=draw(_choice_value(key)),
            provenance=draw(st.sampled_from(list(ChoiceProvenance))),
        )
        for stage, key in included
    )
    return RunPlanChoices.build(choices)


_manifest_artifacts = st.builds(
    ManifestArtifact,
    path=_nonempty_text,
    kind=_nonempty_text,
    status=st.sampled_from(list(ArtifactStatus)),
    sha256=st.none() | _nonempty_text,
    timing_view=st.none() | st.sampled_from(list(TimingView)),
    timing_basis=st.none() | st.sampled_from(list(TimingBasis)),
    provenance=_json_objects,
)


@st.composite
def _run_bundle_manifests(draw: st.DrawFn) -> RunBundleManifest:
    artifacts = draw(st.lists(_manifest_artifacts, max_size=3, unique_by=lambda a: a.path))
    return RunBundleManifest(
        source_id=draw(_nonempty_text),
        run_id=draw(_nonempty_text),
        run_status=draw(st.sampled_from(list(RunStatus))),
        projection_stage_version=draw(st.integers(-_INT_BOUND, _INT_BOUND)),
        artifacts=tuple(artifacts),
        plan_id=draw(st.just("") | _nonempty_text),
    )


_latest_pointers = st.builds(
    LatestPointer,
    source_id=_nonempty_text,
    run_id=_nonempty_text,
    run_status=st.sampled_from(list(RunStatus)),
    published_at=_nonempty_text,
)

# --- inspection evidence ----------------------------------------------------

_probe_documents = st.none() | st.builds(ProbeDocument, raw_json=st.text(max_size=12))

_stream_coverages = st.builds(
    StreamCoverage,
    coverage=_intervals(),
    gaps=st.lists(_intervals(), max_size=2).map(tuple),
    diagnostics=st.lists(
        st.builds(
            CoverageDiagnostic, reason=_nonempty_text, path=_nonempty_text, message=_nonempty_text
        ),
        max_size=2,
    ).map(tuple),
)

_subtitle_track_candidates = st.builds(
    SubtitleTrackCandidate,
    stream_index=st.integers(-_INT_BOUND, _INT_BOUND),
    language=_optional_nonempty_text,
    container_format=_optional_nonempty_text,
    origin=_nonempty_text,
    available=st.booleans(),
)


@st.composite
def _plan_inspection_evidence(draw: st.DrawFn) -> PlanInspectionEvidence:
    stream_indexes = draw(st.lists(st.integers(-1000, 1000), unique=True, max_size=3).map(sorted))
    coverage_by_stream = tuple((index, draw(_stream_coverages)) for index in stream_indexes)
    return PlanInspectionEvidence(
        source_id=draw(_nonempty_text),
        structural_document=draw(_probe_documents),
        coverage_document=draw(_probe_documents),
        coverage_by_stream=coverage_by_stream,
        subtitle_tracks=tuple(draw(st.lists(_subtitle_track_candidates, max_size=3))),
    )


# --- subtitle candidate / report --------------------------------------------

_format_projection_losses = st.builds(
    FormatProjectionLoss,
    reason=st.just("format_projection_loss"),
    source_ordinal=st.none() | st.integers(0, 1000),
    setting=_nonempty_text,
)

_coverage_start = st.none() | st.builds(
    lambda n, d: {"numerator": n, "denominator": d},
    st.integers(-1000, 1000),
    st.integers(1, 1000),
)

_subtitle_candidates = st.builds(
    SubtitleCandidate,
    source_id=_nonempty_text,
    stream_index=st.integers(0, 1000),
    state=st.sampled_from(list(CandidateState)),
    source_format=st.none() | st.sampled_from(["srt", "vtt"]),
    raw_payload_path=_optional_nonempty_text,
    raw_payload_sha256=_optional_nonempty_text,
    raw_payload_bytes=st.none() | st.integers(0, _INT_BOUND),
    source_candidate_path=_optional_nonempty_text,
    source_candidate_sha256=_optional_nonempty_text,
    source_vtt_path=_optional_nonempty_text,
    source_srt_path=_optional_nonempty_text,
    readable_vtt_path=_optional_nonempty_text,
    readable_corrections_path=_optional_nonempty_text,
    format_projection_losses=st.lists(_format_projection_losses, max_size=2).map(tuple),
    cue_count=st.none() | st.integers(0, 1000),
    coverage_start=_coverage_start,
    diagnostic=st.none() | _planning_diagnostics,
    attempt_id=_optional_nonempty_text,
    codec=_optional_nonempty_text,
    decoder=_optional_nonempty_text,
    raw_pts_cue_intervals=st.lists(_intervals(), max_size=2).map(tuple),
)


@st.composite
def _caption_time_coverages(draw: st.DrawFn) -> CaptionTimeCoverage:
    covered = ExactTime(draw(st.integers(0, _INT_BOUND)), draw(st.integers(1, _INT_BOUND)))
    playback = ExactTime(draw(st.integers(1, _INT_BOUND)), draw(st.integers(1, _INT_BOUND)))
    return CaptionTimeCoverage(covered, playback)


_subtitle_part_reports = st.builds(
    SubtitlePartReport,
    source_id=_nonempty_text,
    state=st.sampled_from(list(SubtitlePartState)),
    selected_stream_index=st.none() | st.integers(0, 1000),
    collection_virtual_time=st.none() | _intervals(),
    caption_time_coverage=st.none() | _caption_time_coverages(),
    risks=st.lists(_planning_diagnostics, max_size=2).map(tuple),
    asr_planning_handoff=st.none() | _planning_diagnostics,
)

#: The report path is supplied to ``from_json`` out of band, never read from the
#: JSON copy, so the round-trip fixes it to a constant the JSON reproduces.
_REPORT_PATH = Path("work/subtitle-reports/report/report.json")

_subtitle_candidate_reports = st.builds(
    SubtitleCandidateReport,
    report_id=_nonempty_text,
    plan_id=_nonempty_text,
    state=st.sampled_from(list(CandidateReportState)),
    subtitle_rules_fingerprint=_optional_nonempty_text,
    candidates=st.lists(_subtitle_candidates, max_size=2).map(tuple),
    diagnostics=st.lists(_planning_diagnostics, max_size=2).map(tuple),
    report_path=st.just(_REPORT_PATH),
    parent_report_id=_optional_nonempty_text,
    selections=st.lists(
        st.builds(
            SubtitleTrackSelection, source_id=_nonempty_text, stream_index=st.integers(0, 1000)
        ),
        max_size=2,
    ).map(tuple),
    part_reports=st.lists(_subtitle_part_reports, max_size=2).map(tuple),
    caption_time_coverage=st.none() | _caption_time_coverages(),
    risks=st.lists(_planning_diagnostics, max_size=2).map(tuple),
)


# --- run state and journal --------------------------------------------------

_run_states = st.builds(
    RunState,
    source_id=_nonempty_text,
    run_id=_nonempty_text,
    plan_id=_nonempty_text,
    status=st.sampled_from(list(RunStatus)),
    stage_units=st.lists(_json_objects, max_size=2).map(tuple),
    adopted_outputs=st.lists(_json_objects, max_size=2).map(tuple),
    invalidation_keys=_json_objects,
    required_decision=st.none() | _json_objects,
)

_run_events = st.builds(
    RunEvent,
    sequence=st.integers(-_INT_BOUND, _INT_BOUND),
    at=_nonempty_text,
    kind=st.sampled_from(list(EventKind)),
    data=_json_objects,
)


def _event_document(event: RunEvent) -> dict[str, object]:
    """The exact ``events.jsonl`` record shape ``RunStateWriter`` appends.

    Deliberately mirrors the private ``_append_event`` serializer: if that shape
    ever drifts, ``read_journal`` will reject this document and the round-trip
    fails loudly rather than silently diverging from production.
    """

    return {
        "schema_version": event.schema_version,
        "sequence": event.sequence,
        "at": event.at,
        "kind": event.kind.value,
        "data": dict(event.data),
    }


# --- heavy-task lock holder (to_document / from_document) --------------------

_lock_holders = st.builds(
    LockHolder,
    run_id=_nonempty_text,
    pid=st.integers(-_INT_BOUND, _INT_BOUND),
    process_start_time=_nonempty_text,
    acquired_at=_nonempty_text,
)


# --- source artifacts / tools shared by the plan aggregates -----------------

_tools = st.builds(
    PinnedExternalTool,
    tool_id=_nonempty_text,
    path=_nonempty_text.map(Path),
    version=_nonempty_text,
    sha256=_nonempty_text,
)

_disk_headrooms = st.builds(
    DiskHeadroom,
    increment_bytes=st.integers(-_INT_BOUND, _INT_BOUND),
    reserve_bytes=st.integers(-_INT_BOUND, _INT_BOUND),
    required_bytes=st.integers(-_INT_BOUND, _INT_BOUND),
)

_three_point_estimates = st.builds(
    ThreePointEstimate,
    optimistic_seconds=st.integers(-_INT_BOUND, _INT_BOUND),
    likely_seconds=st.integers(-_INT_BOUND, _INT_BOUND),
    conservative_seconds=st.integers(-_INT_BOUND, _INT_BOUND),
    confidence=_nonempty_text,
    basis=_nonempty_text,
)


def _source_artifact(draw: st.DrawFn, source_id: str) -> SourceArtifact:
    return SourceArtifact(
        source_id=source_id,
        sha256=draw(_nonempty_text),
        byte_count=draw(st.integers(-_INT_BOUND, _INT_BOUND)),
        media_path=Path(draw(_nonempty_text)),
        origin_kind=draw(_nonempty_text),
    )


@st.composite
def _plan_reports(draw: st.DrawFn) -> PlanReport:
    # load_plan_report requires one inspection-evidence record per source
    # artifact, in the same order, so both are built from one id list.
    source_ids = draw(st.lists(_nonempty_text, unique=True, min_size=0, max_size=3))
    sources = tuple(_source_artifact(draw, source_id) for source_id in source_ids)
    evidence = tuple(
        PlanInspectionEvidence(
            source_id=source_id,
            structural_document=draw(_probe_documents),
            coverage_document=draw(_probe_documents),
            coverage_by_stream=(),
            subtitle_tracks=(),
        )
        for source_id in source_ids
    )
    return PlanReport(
        report_id=draw(_nonempty_text),
        state=draw(st.sampled_from(list(PlanState))),
        source_artifacts=sources,
        tools=tuple(draw(st.lists(_tools, max_size=2))),
        disk_headroom=draw(_disk_headrooms),
        configuration_fingerprint=draw(_nonempty_text),
        decode_estimate=draw(st.none() | _three_point_estimates),
        diagnostics=tuple(draw(st.lists(_planning_diagnostics, max_size=2))),
        url_authorizations=tuple(draw(st.lists(_url_authorization_evidence, max_size=2))),
        inspection_evidence=evidence,
        parent_report_id=draw(_optional_nonempty_text),
        run_choices=draw(_run_plan_choices()),
    )


@st.composite
def _run_plans(draw: st.DrawFn) -> RunPlan:
    source_ids = draw(st.lists(_nonempty_text, unique=True, max_size=3))
    sources = tuple(_source_artifact(draw, source_id) for source_id in source_ids)
    fingerprint_ids = draw(st.lists(_nonempty_text, unique=True, max_size=3))
    fingerprints = tuple((source_id, draw(_nonempty_text)) for source_id in fingerprint_ids)
    return RunPlan(
        plan_id=draw(_nonempty_text),
        report_id=draw(_nonempty_text),
        source_artifacts=sources,
        tools=tuple(draw(st.lists(_tools, max_size=2))),
        disk_headroom=draw(_disk_headrooms),
        configuration_fingerprint=draw(_nonempty_text),
        url_authorizations=tuple(draw(st.lists(_url_authorization_evidence, max_size=2))),
        inspection_evidence_fingerprints=fingerprints,
        run_choices=draw(_run_plan_choices()),
    )


# --- path-based loader adapters (write JSON text, read it back) --------------


def _via_file(reader: Callable[[Path], object], document: object) -> object:
    """Serialize ``document`` to a temp file and hand it to a path reader."""

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "document.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return reader(path)


_load_run_state = partial(_via_file, read_run_state)
_load_plan_report = partial(_via_file, load_plan_report)
_load_run_plan = partial(_via_file, load_run_plan)


def _load_journal_event(document: object) -> object:
    events = _via_file(read_journal, document)
    return events[0] if events else None


# --- the pair registry ------------------------------------------------------


@dataclass(frozen=True)
class Pair:
    """One serialize/deserialize contract under test."""

    name: str
    make: st.SearchStrategy[object]
    to_doc: Callable[[object], object]
    reload: Callable[[object], object]
    errors: tuple[type[Exception], ...]
    enum_keys: tuple[str, ...] = ()


def _as_json(obj: object) -> object:
    return obj.as_json()


PAIRS: tuple[Pair, ...] = (
    Pair(
        "url_authorization_evidence",
        _url_authorization_evidence,
        _as_json,
        URLAuthorizationEvidence.from_json,
        (ValueError,),
        enum_keys=("mode",),
    ),
    Pair(
        "stage_invalidation_key",
        _stage_invalidation_keys,
        _as_json,
        StageInvalidationKey.from_json,
        (StageDagError,),
        enum_keys=("stage",),
    ),
    Pair(
        "projection_invalidation_key",
        _projection_invalidation_keys,
        _as_json,
        ProjectionInvalidationKey.from_json,
        (PublicationProjectionError,),
    ),
    Pair(
        "preprocessing_profile",
        _preprocessing_profiles,
        _as_json,
        PreprocessingProfile.from_json,
        (AnalysisAudioDerivationError,),
        enum_keys=("loudness_mode",),
    ),
    Pair(
        "run_choice",
        _run_choices,
        _as_json,
        RunChoice.from_json,
        (RunChoicesError,),
        enum_keys=("provenance",),
    ),
    Pair(
        "run_plan_choices",
        _run_plan_choices(),
        _as_json,
        RunPlanChoices.from_json,
        (RunChoicesError,),
    ),
    Pair(
        "manifest_artifact",
        _manifest_artifacts,
        _as_json,
        ManifestArtifact.from_json,
        (PublicationError,),
        enum_keys=("status",),
    ),
    Pair(
        "run_bundle_manifest",
        _run_bundle_manifests(),
        _as_json,
        RunBundleManifest.from_json,
        (PublicationError,),
        enum_keys=("run_status",),
    ),
    Pair(
        "latest_pointer",
        _latest_pointers,
        _as_json,
        LatestPointer.from_json,
        (PublicationError,),
        enum_keys=("run_status",),
    ),
    Pair(
        "plan_inspection_evidence",
        _plan_inspection_evidence(),
        _as_json,
        PlanInspectionEvidence.from_json,
        (ValueError,),
    ),
    Pair(
        "subtitle_candidate",
        _subtitle_candidates,
        _as_json,
        SubtitleCandidate.from_json,
        (SubtitleReportError,),
        enum_keys=("state",),
    ),
    Pair(
        "subtitle_candidate_report",
        _subtitle_candidate_reports,
        _as_json,
        lambda doc: SubtitleCandidateReport.from_json(doc, _REPORT_PATH),
        (SubtitleReportError,),
        enum_keys=("state",),
    ),
    Pair(
        "run_state",
        _run_states,
        lambda obj: obj.to_document(),
        _load_run_state,
        (RunStateError,),
        enum_keys=("status",),
    ),
    Pair(
        "journal_event",
        _run_events,
        _event_document,
        _load_journal_event,
        (RunStateError,),
        enum_keys=("kind",),
    ),
    Pair(
        "plan_report",
        _plan_reports(),
        _as_json,
        _load_plan_report,
        (PlanningError,),
        enum_keys=("state",),
    ),
    Pair(
        "run_plan",
        _run_plans(),
        _as_json,
        _load_run_plan,
        (PlanningError,),
    ),
    Pair(
        "lock_holder",
        _lock_holders,
        lambda obj: obj.to_document(),
        LockHolder.from_document,
        (HeavyTaskLockError,),
    ),
)

_PAIR_IDS = [pair.name for pair in PAIRS]
_ENUM_CASES = [(pair, key) for pair in PAIRS for key in pair.enum_keys]
_ENUM_IDS = [f"{pair.name}.{key}" for pair, key in _ENUM_CASES]

_HOSTILE_VALUES = st.sampled_from(
    [None, [], {}, "🙅 not-a-valid-value", 987654321, True, [1, 2, 3]]
)
_NON_OBJECTS = st.none() | st.integers() | _nonempty_text | st.lists(_json_scalars, max_size=3)


def _jsonify(document: object) -> object:
    """Force a document through real JSON text, as every loader receives it."""

    return json.loads(json.dumps(document))


def _assert_only_typed(pair: Pair, document: object) -> None:
    """A mutated document must reject with a typed reason or parse cleanly.

    The contract the ticket fixes: a loader may accept the mutation (some
    mutations are benign) or reject it with the module's own error class, but it
    must never leak an unhandled ``KeyError`` / ``TypeError`` / ``AttributeError``.
    """

    try:
        pair.reload(document)
    except pair.errors:
        return
    except Exception as exc:  # noqa: BLE001 — the whole point is to catch leaks
        raise AssertionError(
            f"{pair.name}: unhandled {type(exc).__name__} from a mutated document: {exc}"
        ) from exc


# --- properties -------------------------------------------------------------


@pytest.mark.parametrize("pair", PAIRS, ids=_PAIR_IDS)
@given(data=st.data())
def test_round_trip_equality(pair: Pair, data: st.DataObject) -> None:
    """object -> serialize -> JSON text -> deserialize -> equal object."""

    obj = data.draw(pair.make)
    document = _jsonify(pair.to_doc(obj))
    assert pair.reload(document) == obj


@pytest.mark.parametrize("pair", PAIRS, ids=_PAIR_IDS)
@given(data=st.data())
def test_dropped_or_retyped_field_is_typed(pair: Pair, data: st.DataObject) -> None:
    """Dropping a field or replacing it with a hostile value never leaks."""

    obj = data.draw(pair.make)
    document = _jsonify(pair.to_doc(obj))
    assert isinstance(document, Mapping)
    keys = sorted(document)
    if not keys:
        return
    key = data.draw(st.sampled_from(keys))
    mutated = dict(document)
    if data.draw(st.booleans()):
        del mutated[key]
    else:
        mutated[key] = data.draw(_HOSTILE_VALUES)
    _assert_only_typed(pair, mutated)


@pytest.mark.parametrize("pair", PAIRS, ids=_PAIR_IDS)
@given(bad=_NON_OBJECTS)
def test_non_object_document_is_rejected(pair: Pair, bad: object) -> None:
    """A top-level value that is not a JSON object rejects with a typed reason."""

    with pytest.raises(pair.errors):
        pair.reload(bad)


@pytest.mark.parametrize(("pair", "key"), _ENUM_CASES, ids=_ENUM_IDS)
@given(data=st.data())
def test_bogus_enum_token_is_rejected(pair: Pair, key: str, data: st.DataObject) -> None:
    """An unknown token in an enum-valued field rejects with a typed reason."""

    obj = data.draw(pair.make)
    document = _jsonify(pair.to_doc(obj))
    assert isinstance(document, Mapping)
    mutated = dict(document)
    mutated[key] = "__definitely_not_a_valid_enum_token__"
    with pytest.raises(pair.errors):
        pair.reload(mutated)
