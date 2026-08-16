"""Improvement runs: ``vcp improve`` over a named published RunBundle.

An Improvement run re-enhances an already-published run without touching it. It
is a *new plan and a new run id* derived from a named published RunBundle: the
scope grammar (``part`` / ``range`` / ``all``) fixes the affected Parts, the run
routes through the retained enhancement and affected-Part re-analysis contracts
unchanged, and the outputs of the *unaffected* Parts are carried forward from the
prior bundle by recorded source run id and artifact hash — the ADR 0046
carry-forward pattern applied at run level, the single sanctioned exception to
run-scoped adoption (ADR 0052).

This module owns only the improvement-specific orchestration:

* **Read the published bundle, never a workspace.** The source bundle's hashes
  are re-verified (``verify_published_bundle``) before any byte is used; a bundle
  that fails reverification is refused. The carried-forward artifacts are read
  only from that bundle.
* **Derive a new confirmed plan.** The source plan (located through the bundle's
  recorded ``plan_id``) is carried forward verbatim except that the ASR mode
  becomes ``enhancement`` and the enhancement scope becomes the requested Parts;
  a fresh content-addressed plan id is persisted. The prior bundle and its
  ``latest.json`` are never modified here.
* **Carry forward the unaffected Parts.** Each per-Part content artifact of a
  Part *not* in the affected set is turned into a projected artifact tagged with
  its source run id and hash, then handed to the standard run loop, which folds
  it into the new run's projection so it appears in the new manifest and reports.

The heavy re-analysis rides the same :class:`~video_content_pipeline.run_loop.RunComposition`
seam as ``vcp run`` — the per-phase functions cannot run offline — so the
end-to-end enhancement is exercised in a real environment, while this module's
own contract (revalidation, new identity, scope grammar, carry-forward
provenance, and the standard publish path) is proven offline over a controlled
composition.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from video_content_pipeline.durable_io import utc_now
from video_content_pipeline.heavy_task_lock import ProcessProbe, heavy_task_lock_path
from video_content_pipeline.orchestration import (
    RunLayout,
    find_run_layout,
    initialize_run_workspace,
    run_id_from_run_plan,
    source_id_from_run_plan,
)
from video_content_pipeline.planning import (
    RunPlan,
    persist_confirmed_run_plan,
)
from video_content_pipeline.publication import (
    DOCUMENT_KIND,
    RunBundleManifest,
    read_run_bundle_manifest,
    verify_published_bundle,
)
from video_content_pipeline.publication_projection import (
    ArtifactKind,
    ArtifactStatus,
    ProjectedArtifact,
)
from video_content_pipeline.run_choices import (
    COLLECTION_SCOPE,
    KEY_ASR_MODE,
    KEY_ENHANCEMENT_PART,
    KEY_ENHANCEMENT_RANGE,
    STAGE_ENHANCEMENT,
    STAGE_RUN,
    AsrMode,
    ChoiceProvenance,
    RunChoice,
    RunPlanChoices,
)
from video_content_pipeline.run_loop import (
    CompositionFactory,
    RunOutcome,
    execute_confirmed_run,
    load_confirmed_plan,
)

#: The literal ``--asr`` value that re-enhances every Part.
ASR_SCOPE_ALL = "all"

#: The prefix of a per-Part publication path (``parts/<part-id>/...``). Only
#: per-Part artifacts are carried forward; collection-level artifacts (transcript,
#: content report, segments, correction log, collection subtitles) are recomputed
#: from the combined set on the new run (ADR 0046).
_PART_PATH_PREFIX = "parts/"

#: Projected-artifact statuses that name a real published content file, so they
#: are the only ones eligible to be carried forward.
_CARRIABLE_STATUSES: frozenset[ArtifactStatus] = frozenset(
    {ArtifactStatus.VALID, ArtifactStatus.PARTIAL}
)


class ImproveError(ValueError):
    """An improvement-run failure with an auditable machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


# --- Scope grammar ----------------------------------------------------------


def parse_asr_scope(
    scope: str, part_ids: tuple[str, ...]
) -> tuple[tuple[RunChoice, ...], frozenset[str]]:
    """Map the ``--asr <part|range|all>`` grammar to enhancement scope choices.

    ``all`` re-enhances every Part; ``<part-id>`` re-enhances one whole Part;
    ``<part-id>:<start>-<end>`` re-enhances one range within a Part. Returns the
    enhancement :class:`RunChoice`\\ s (in the shape the retained ``enhance``
    contract already accepts, via ``enhancement_stage_parameters``) and the set of
    affected Part ids — every Part *not* in that set is carried forward.
    """

    if not scope:
        raise ImproveError("improve_scope_missing", "vcp improve requires an --asr scope.")
    if scope == ASR_SCOPE_ALL:
        choices = tuple(
            RunChoice(
                STAGE_ENHANCEMENT,
                KEY_ENHANCEMENT_PART,
                COLLECTION_SCOPE,
                part_id,
                ChoiceProvenance.USER_CHOSEN,
            )
            for part_id in part_ids
        )
        return choices, frozenset(part_ids)
    if ":" in scope:
        part_id, _, span = scope.partition(":")
        _require_known_part(part_id, part_ids)
        if not span or "-" not in span:
            raise ImproveError(
                "improve_scope_invalid",
                "A range --asr scope must be <part-id>:<start>-<end> in seconds.",
            )
        choice = RunChoice(
            STAGE_ENHANCEMENT,
            KEY_ENHANCEMENT_RANGE,
            part_id,
            span,
            ChoiceProvenance.USER_CHOSEN,
        )
        return (choice,), frozenset({part_id})
    _require_known_part(scope, part_ids)
    choice = RunChoice(
        STAGE_ENHANCEMENT,
        KEY_ENHANCEMENT_PART,
        COLLECTION_SCOPE,
        scope,
        ChoiceProvenance.USER_CHOSEN,
    )
    return (choice,), frozenset({scope})


def _require_known_part(part_id: str, part_ids: tuple[str, ...]) -> None:
    if part_id not in part_ids:
        raise ImproveError(
            "improve_unknown_part",
            f"--asr names Part {part_id!r}, which the source run does not contain.",
        )


def improvement_run_choices(
    source_choices: RunPlanChoices, scope_choices: tuple[RunChoice, ...]
) -> RunPlanChoices:
    """Derive the improvement plan's choices from the source plan's choices.

    Every source choice is carried forward verbatim except the ASR mode (forced to
    ``enhancement``) and any enhancement-stage choice (replaced by the requested
    scope), so the subtitle-track, audio-stream, and visual-text selections the
    re-analysis still needs are preserved rather than re-prompted.
    """

    kept = tuple(
        choice
        for choice in source_choices.choices
        if not (choice.stage == STAGE_RUN and choice.key == KEY_ASR_MODE)
        and choice.stage != STAGE_ENHANCEMENT
    )
    mode = RunChoice(
        STAGE_RUN,
        KEY_ASR_MODE,
        COLLECTION_SCOPE,
        AsrMode.ENHANCEMENT.value,
        ChoiceProvenance.USER_CHOSEN,
    )
    return RunPlanChoices.build((*kept, mode, *scope_choices))


# --- Improvement plan -------------------------------------------------------


def _derive_improvement_plan_id(
    source_plan: RunPlan, source_run_id: str, new_choices: RunPlanChoices
) -> str:
    """Content-address the improvement plan so equal derivations agree.

    The digest folds the source run id and the new choices, so the improvement
    plan id is always distinct from the source plan id (their choices differ) yet
    stable for the same source run and the same scope.
    """

    payload = json.dumps(
        {
            "kind": "improvement",
            "source_run_id": source_run_id,
            "report_id": source_plan.report_id,
            "configuration_fingerprint": source_plan.configuration_fingerprint,
            "source_artifacts": [artifact.as_json() for artifact in source_plan.source_artifacts],
            "run_choices": new_choices.as_json(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def build_improvement_plan(
    source_plan: RunPlan,
    source_run_id: str,
    scope: str,
    plans_root: Path,
) -> tuple[RunPlan, frozenset[str]]:
    """Derive and persist a new confirmed plan for an Improvement run.

    The new plan shares the source plan's revalidated source artifacts, tools,
    disk headroom, configuration fingerprint, and inspection fingerprints — it is
    the same source, only re-enhanced — but carries the enhancement mode and the
    requested scope. It is persisted at ``plans/<new-plan-id>/run-plan.json``; the
    source plan is left untouched. Returns the plan and the affected Part set.
    """

    part_ids = tuple(artifact.source_id for artifact in source_plan.source_artifacts)
    scope_choices, affected = parse_asr_scope(scope, part_ids)
    new_choices = improvement_run_choices(source_plan.run_choices, scope_choices)
    plan_id = _derive_improvement_plan_id(source_plan, source_run_id, new_choices)
    plan = RunPlan(
        plan_id=plan_id,
        report_id=source_plan.report_id,
        source_artifacts=source_plan.source_artifacts,
        tools=source_plan.tools,
        disk_headroom=source_plan.disk_headroom,
        configuration_fingerprint=source_plan.configuration_fingerprint,
        url_authorizations=source_plan.url_authorizations,
        inspection_evidence_fingerprints=source_plan.inspection_evidence_fingerprints,
        run_choices=new_choices,
    )
    persist_confirmed_run_plan(plan, plans_root)
    return plan, affected


# --- Carry-forward ----------------------------------------------------------


def _part_id_of(path: str) -> str | None:
    """Return the Part id a per-Part publication path belongs to, or ``None``."""

    if not path.startswith(_PART_PATH_PREFIX):
        return None
    remainder = path[len(_PART_PATH_PREFIX) :]
    part_id, separator, _ = remainder.partition("/")
    return part_id if separator and part_id else None


def carried_forward_artifacts(
    manifest: RunBundleManifest,
    bundle_dir: Path,
    affected_parts: frozenset[str],
    source_run_id: str,
) -> tuple[ProjectedArtifact, ...]:
    """Read the unaffected Parts' content artifacts from a published bundle.

    Only a per-Part ``valid`` / ``partial`` content artifact of a Part *not* in
    ``affected_parts`` is carried forward; documents, collection-level artifacts,
    and failed/unavailable entries are not. Each carried-forward artifact keeps its
    recorded timing view, basis, and provenance, and records the source run id and
    the source artifact hash so the new run's manifest and reports state where it
    came from. Bytes are read from the bundle the caller has already reverified.
    """

    carried: list[ProjectedArtifact] = []
    for entry in manifest.artifacts:
        if entry.kind == DOCUMENT_KIND or entry.status not in _CARRIABLE_STATUSES:
            continue
        part_id = _part_id_of(entry.path)
        if part_id is None or part_id in affected_parts:
            continue
        if entry.sha256 is None:
            continue
        content = (bundle_dir / entry.path).read_text(encoding="utf-8")
        provenance = dict(entry.provenance)
        provenance["carried_forward_from_run"] = source_run_id
        provenance["carried_forward_sha256"] = entry.sha256
        carried.append(
            ProjectedArtifact(
                path=entry.path,
                kind=ArtifactKind(entry.kind),
                status=entry.status,
                content=content,
                sha256=entry.sha256,
                timing_view=entry.timing_view,
                timing_basis=entry.timing_basis,
                provenance=provenance,
            )
        )
    return tuple(carried)


# --- Source bundle location and revalidation --------------------------------


def _locate_published_run(project_root: Path, source_run_id: str) -> RunLayout:
    """Locate a published run's bundle layout by scanning ``outputs/<source>/``.

    Shares the one run-directory scan with the CLI's published-run lookup through
    ``orchestration.find_run_layout``; only the not-found reason differs, since a
    missing source run is an improvement failure.
    """

    layout = find_run_layout(
        project_root,
        project_root / "outputs",
        source_run_id,
        lambda run_dir: run_dir.is_dir(),
    )
    if layout is None:
        raise ImproveError("source_run_not_found", f"No published bundle for run {source_run_id}.")
    return layout


def verified_source_manifest(source_layout: RunLayout) -> RunBundleManifest:
    """Revalidate a source bundle's hashes and return its manifest.

    The whole bundle is re-hashed against its own manifest in both directions
    before any byte is carried forward; a bundle that fails reverification is
    refused so an Improvement run never builds on a corrupt source. The manifest
    must also name the plan that produced it, so the improvement plan can be
    derived from a published bundle alone.
    """

    verification = verify_published_bundle(source_layout.output_dir)
    if not verification.verified:
        raise ImproveError(
            "source_bundle_unverified",
            "The source RunBundle failed hash reverification: "
            + ", ".join(f"{d.path} ({d.reason})" for d in verification.discrepancies),
        )
    manifest = read_run_bundle_manifest(source_layout.output_dir)
    if not manifest.plan_id:
        raise ImproveError(
            "source_plan_unknown",
            "The source RunBundle manifest records no plan id to derive from.",
        )
    return manifest


# --- Orchestration ----------------------------------------------------------


def start_improvement_run(
    project_root: Path,
    source_run_id: str,
    scope: str,
    *,
    composition_factory: CompositionFactory,
    run_start: datetime,
    lock_path: Path | None = None,
    probe: ProcessProbe | None = None,
    clock: Callable[[], datetime] = utc_now,
    now: datetime | None = None,
) -> RunOutcome:
    """Run ``vcp improve`` end-to-end over a named published RunBundle.

    Locates and reverifies the source bundle, derives and persists a new confirmed
    plan (a new plan id and, once started, a new run id), reads the unaffected
    Parts' artifacts forward from that bundle, and executes the new run through the
    standard non-interactive run loop — the same staging, atomic publish, and
    latest-pointer eligibility as any other run. The source bundle and its
    ``latest.json`` are never modified.
    """

    source_layout = _locate_published_run(project_root, source_run_id)
    manifest = verified_source_manifest(source_layout)
    source_plan = load_confirmed_plan(project_root, manifest.plan_id)
    plan, affected = build_improvement_plan(
        source_plan, source_run_id, scope, project_root / "plans"
    )
    carried = carried_forward_artifacts(manifest, source_layout.output_dir, affected, source_run_id)
    layout = initialize_run_workspace(
        RunLayout(
            project_root,
            source_id_from_run_plan(plan),
            run_id_from_run_plan(plan, run_start),
        )
    )
    composition = composition_factory(layout, plan)
    return execute_confirmed_run(
        layout=layout,
        plan=plan,
        composition=composition,
        lock_path=lock_path if lock_path is not None else heavy_task_lock_path(project_root),
        probe=probe,
        clock=clock,
        now=now if now is not None else run_start,
        carried_forward=carried,
    )
