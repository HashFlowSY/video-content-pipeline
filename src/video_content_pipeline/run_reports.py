"""The always-published audit layer: run reports, inventory, and cleanup plan.

Every RunBundle — a full success or a Minimal RunBundle published on an ordinary
failure — carries an audit floor beyond the projected content artifacts and the
:mod:`~video_content_pipeline.publication` manifest:

* ``quality-report.md`` / ``quality-report.json`` — the per-stage gate outcomes
  aggregated *from the retained stage reports* (plan §17 gate families) together
  with the Publication projection's recorded timing-view selections and bases.
  This layer never re-runs a gate; it only reads what the stages already
  recorded (ADR 0034/0041 immutable-workspace lineage).
* ``processing-report.md`` — the plan §18.1 readable report, in Chinese (the
  Phase 6 report-language boundary), including the fixed project-stage line, the
  environment and lockfile identity, tools and models, network and external
  reads, the created/modified/published paths, measured resources, warnings and
  review-needed intervals, and the cleanup section.
* ``run-inventory.json`` — the plan §18.2 machine inventory: one eleven-field
  record per used, created, modified, downloaded, or published path, each with a
  ``deletion_class`` and its ``deletion_consequence``, spanning models, caches,
  workspaces, staging, and published files.
* ``diagnostics/events.json`` — a snapshot of the run events journal.

This module is a *pure, deterministic renderer*: it turns already-recorded
values (supplied through typed seams) into byte-identical documents. It reads no
filesystem, re-runs no gate, and performs no analysis. The run loop (a later
ticket) gathers the recorded values from the in-process composition and calls
:func:`assemble_minimal_run_bundle`, whose result — together with the manifest
that :func:`~video_content_pipeline.publication.publish_run_bundle` writes — is
the six-piece Minimal RunBundle floor guaranteed on every ordinary failure path.

The cleanup plan is declaration only: deletion classes and consequences are
recorded here and in the processing report's cleanup section, and *no code path
in this module deletes anything* — deletion is always an explicit user action
(plan §18.2), and there is deliberately no cleanup command.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from video_content_pipeline.publication import BundleDocument
from video_content_pipeline.publication_projection import ArtifactStatus, ProjectionResult
from video_content_pipeline.run_state import RunEvent, RunStatus

#: Projected-artifact statuses that represent a real published content file (the
#: same floor publication uses for latest-pointer eligibility). ``invalid`` and
#: ``unavailable`` artifacts are never recorded as published formal outputs
#: (plan §17.5: "失败和不可用制品有明确状态").
_PUBLISHED_CONTENT_STATUSES: frozenset[ArtifactStatus] = frozenset(
    {ArtifactStatus.VALID, ArtifactStatus.PARTIAL}
)

_QUALITY_SCHEMA_VERSION = 1
_INVENTORY_SCHEMA_VERSION = 1
_DIAGNOSTICS_SCHEMA_VERSION = 1

#: The fixed project-stage line every report must display after development
#: (plan §18): "current stage: real-world testing, production acceptance not yet
#: complete". It is a verbatim constant so the discipline cannot drift.
PROJECT_STAGE_LINE = "当前阶段：真实测试，尚未完成生产验收"

#: The bundle-relative paths of the audit documents assembled here. Together with
#: ``manifest.json`` (written by :mod:`~video_content_pipeline.publication`) these
#: are the six-piece Minimal RunBundle floor (plan §4, spec Publication
#: Contract). Kept as the single source of truth so the floor's completeness is
#: assertable.
QUALITY_REPORT_MARKDOWN_PATH = "quality-report.md"
QUALITY_REPORT_JSON_PATH = "quality-report.json"
PROCESSING_REPORT_PATH = "processing-report.md"
RUN_INVENTORY_PATH = "run-inventory.json"
DIAGNOSTICS_EVENTS_PATH = "diagnostics/events.json"

MINIMAL_RUN_BUNDLE_DOCUMENTS: tuple[str, ...] = (
    QUALITY_REPORT_MARKDOWN_PATH,
    QUALITY_REPORT_JSON_PATH,
    PROCESSING_REPORT_PATH,
    RUN_INVENTORY_PATH,
    DIAGNOSTICS_EVENTS_PATH,
)


class RunReportError(ValueError):
    """A run-report failure with an auditable machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _canonical_json(payload: Mapping[str, object]) -> str:
    """Render a published JSON document deterministically, with trailing newline.

    Mirrors the persisted-document idiom of the publication module (``sort_keys``
    + ``indent=2``) so an audit document is byte-identical across equal runs.
    """

    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _markdown(lines: Sequence[str]) -> str:
    """Join readable-report lines with a single trailing newline."""

    return "\n".join(lines).rstrip() + "\n"


# --- Quality report ---------------------------------------------------------


class GateStatus(StrEnum):
    """The recorded disposition of one quality gate (plan §17)."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class VerificationLevel(StrEnum):
    """The plan §17.6 verification level a run may claim.

    An automated ``complete`` run states ``model_audited`` at most and never
    claims human verification; ``human_verified`` requires recorded human review
    (out of scope here) and ``review_needed`` marks low-confidence intervals.
    """

    MODEL_AUDITED = "model_audited"
    HUMAN_VERIFIED = "human_verified"
    REVIEW_NEEDED = "review_needed"


@dataclass(frozen=True)
class GateOutcome:
    """One recorded gate check read back from a retained stage report.

    ``gate`` is the stage's own gate identifier (plan §17 vocabulary, kept
    verbatim); this layer never invents or re-evaluates it.
    """

    gate: str
    status: GateStatus
    detail: str = ""

    def as_json(self) -> dict[str, object]:
        return {"gate": self.gate, "status": self.status.value, "detail": self.detail}


@dataclass(frozen=True)
class StageGateReport:
    """The gate outcomes one stage unit recorded, aggregated without re-running."""

    stage: str
    scope: str
    outcomes: tuple[GateOutcome, ...] = ()

    def as_json(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "scope": self.scope,
            "outcomes": [outcome.as_json() for outcome in self.outcomes],
        }


@dataclass(frozen=True)
class ReviewNeededInterval:
    """A low-confidence, conflicting, or evidence-thin interval (plan §17.6).

    ``review-needed`` never blocks a RunBundle from publishing; it is recorded so
    a human reviewer can find it.
    """

    scope: str
    reason: str
    detail: str = ""

    def as_json(self) -> dict[str, object]:
        return {"scope": self.scope, "reason": self.reason, "detail": self.detail}


def _sorted_review_needed(
    intervals: Sequence[ReviewNeededInterval],
) -> list[ReviewNeededInterval]:
    """Sort review-needed intervals into a stable order for deterministic output."""

    return sorted(
        intervals, key=lambda interval: (interval.scope, interval.reason, interval.detail)
    )


def _review_needed_line(interval: ReviewNeededInterval, *, prefix: str = "") -> str:
    """Render one review-needed interval as a readable list line (shared idiom)."""

    detail = f"：{interval.detail}" if interval.detail else ""
    return f"- {prefix}`{interval.scope}` {interval.reason}{detail}"


@dataclass(frozen=True)
class TimingSelection:
    """The Publication projection's recorded timing view and basis for one path.

    Derived directly from the projection result (ADR 0026) — a coordinate space
    (``part_relative`` / ``collection_virtual``) and, for alignment-governed
    artifacts, the ``original`` / ``adopted_alignment`` basis.
    """

    path: str
    timing_view: str
    timing_basis: str | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "path": self.path,
            "timing_view": self.timing_view,
            "timing_basis": self.timing_basis,
        }


def timing_selections_from_projection(projection: ProjectionResult) -> tuple[TimingSelection, ...]:
    """Read the projection's timing-view selections without re-running it.

    Only coordinate-bearing artifacts carry a timing view; the rest are omitted.
    Entries follow the projection's sorted-path order for determinism.
    """

    selections: list[TimingSelection] = []
    for artifact in projection.artifacts:
        if artifact.timing_view is None:
            continue
        selections.append(
            TimingSelection(
                path=artifact.path,
                timing_view=artifact.timing_view.value,
                timing_basis=artifact.timing_basis.value if artifact.timing_basis else None,
            )
        )
    return tuple(selections)


@dataclass(frozen=True)
class QualityReport:
    """The aggregated quality report published as ``quality-report.{md,json}``.

    It holds only recorded values: the per-stage gate outcomes read back from the
    retained stage reports, the projection's timing selections, the review-needed
    intervals, and the verification level. Rendering re-runs nothing.
    """

    source_id: str
    run_id: str
    run_status: RunStatus
    verification_level: VerificationLevel
    stage_reports: tuple[StageGateReport, ...] = ()
    timing_selections: tuple[TimingSelection, ...] = ()
    review_needed: tuple[ReviewNeededInterval, ...] = ()

    def __post_init__(self) -> None:
        if self.verification_level is VerificationLevel.HUMAN_VERIFIED:
            # Human verification requires recorded reviewer evidence the audit
            # layer does not carry; an automated run must never claim it (§17.6).
            raise RunReportError(
                "human_verification_unsupported",
                "The audit layer never records human_verified; it states "
                "model_audited at most (plan §17.6).",
            )

    def _sorted_stage_reports(self) -> list[StageGateReport]:
        return sorted(self.stage_reports, key=lambda report: (report.stage, report.scope))

    def as_json(self) -> dict[str, object]:
        return {
            "schema_version": _QUALITY_SCHEMA_VERSION,
            "source_id": self.source_id,
            "run_id": self.run_id,
            "run_status": self.run_status.value,
            "verification_level": self.verification_level.value,
            "stage_reports": [report.as_json() for report in self._sorted_stage_reports()],
            "timing_selections": [selection.as_json() for selection in self.timing_selections],
            "review_needed": [
                interval.as_json() for interval in _sorted_review_needed(self.review_needed)
            ],
        }

    def to_json_text(self) -> str:
        return _canonical_json(self.as_json())

    def to_markdown(self) -> str:
        lines = [
            "# 质量报告",
            "",
            PROJECT_STAGE_LINE,
            "",
            f"- 来源：`{self.source_id}`",
            f"- 运行：`{self.run_id}`",
            f"- 运行状态：{self.run_status.value}",
            f"- 验证级别：{self.verification_level.value}",
            "",
            "## 各阶段门禁结果",
            "",
        ]
        stage_reports = self._sorted_stage_reports()
        if not stage_reports:
            lines.append("- 无已记录的门禁结果。")
        else:
            for report in stage_reports:
                lines.append(f"### {report.stage} / {report.scope}")
                if not report.outcomes:
                    lines.append("- 无门禁记录。")
                else:
                    for outcome in report.outcomes:
                        suffix = f"（{outcome.detail}）" if outcome.detail else ""
                        lines.append(f"- {outcome.gate}：{outcome.status.value}{suffix}")
                lines.append("")
        lines.append("## 时间视图选择")
        lines.append("")
        if not self.timing_selections:
            lines.append("- 无时间视图记录。")
        else:
            for selection in self.timing_selections:
                basis = selection.timing_basis if selection.timing_basis else "不适用"
                lines.append(f"- `{selection.path}`：{selection.timing_view}（基准：{basis}）")
        lines.append("")
        lines.append("## 待复核区间")
        lines.append("")
        review_needed = _sorted_review_needed(self.review_needed)
        if not review_needed:
            lines.append("- 无待复核区间。")
        else:
            lines.extend(_review_needed_line(interval) for interval in review_needed)
        return _markdown(lines)


#: The run statuses whose automated gates all passed, so a bundle may claim
#: ``model_audited`` (plan §17.6: "通过全部自动化质量门禁"). Any other status —
#: a failed, incomplete, or cancelled run — did not clear every gate, so the
#: honest default level is ``review_needed``, never a pass claim.
_MODEL_AUDITED_STATUSES: frozenset[RunStatus] = frozenset(
    {RunStatus.COMPLETE, RunStatus.COMPLETE_WITH_WARNINGS}
)


def default_verification_level(run_status: RunStatus) -> VerificationLevel:
    """Return the verification level an automated run of this status may claim.

    Only a ``complete`` / ``complete_with_warnings`` run passed every automated
    gate and may state ``model_audited`` (plan §17.6); every other status
    defaults to ``review_needed`` rather than falsely claiming a clean audit.
    Human verification is never claimed automatically.
    """

    if run_status in _MODEL_AUDITED_STATUSES:
        return VerificationLevel.MODEL_AUDITED
    return VerificationLevel.REVIEW_NEEDED


def build_quality_report(
    *,
    source_id: str,
    run_id: str,
    run_status: RunStatus,
    projection: ProjectionResult,
    stage_reports: Sequence[StageGateReport] = (),
    review_needed: Sequence[ReviewNeededInterval] = (),
    verification_level: VerificationLevel | None = None,
) -> QualityReport:
    """Aggregate recorded gate outcomes and the projection's timing selections.

    The timing selections are read from ``projection`` (no re-projection); the
    gate outcomes and review-needed intervals are passed through verbatim from
    the retained stage reports. No gate is re-executed. When
    ``verification_level`` is omitted it is derived from ``run_status`` by
    :func:`default_verification_level`, so a non-completed run never defaults to
    a ``model_audited`` pass claim.
    """

    level = verification_level or default_verification_level(run_status)
    return QualityReport(
        source_id=source_id,
        run_id=run_id,
        run_status=run_status,
        verification_level=level,
        stage_reports=tuple(stage_reports),
        timing_selections=timing_selections_from_projection(projection),
        review_needed=tuple(review_needed),
    )


# --- Run inventory ----------------------------------------------------------


class InventoryKind(StrEnum):
    """The kind of thing an inventory record covers (plan §18.2)."""

    FILE = "file"
    DIRECTORY = "directory"
    MODEL = "model"
    CACHE = "cache"
    EXTERNAL_SOURCE = "external_source"


class InventoryAction(StrEnum):
    """What the run did with a path (plan §18.2)."""

    READ = "read"
    CREATED = "created"
    MODIFIED = "modified"
    PUBLISHED = "published"
    DOWNLOADED = "downloaded"


#: The actions that write a path (as opposed to only reading it), for the plan
#: §18.1 "created/modified/published paths" section.
_WRITTEN_ACTIONS: frozenset[InventoryAction] = frozenset(
    {
        InventoryAction.CREATED,
        InventoryAction.MODIFIED,
        InventoryAction.PUBLISHED,
        InventoryAction.DOWNLOADED,
    }
)


class DeletionClass(StrEnum):
    """A path's cleanup classification (plan §18.2).

    ``safe_to_delete`` — no audit or rebuild value; ``rebuildable`` — deleting it
    forces a recompute; ``keep_for_audit`` — evidence that should be retained;
    ``published`` — a formal output. Source media, raw ASR, alignment evidence,
    and formal outputs default to retained (never ``safe_to_delete``).
    """

    SAFE_TO_DELETE = "safe_to_delete"
    REBUILDABLE = "rebuildable"
    KEEP_FOR_AUDIT = "keep_for_audit"
    PUBLISHED = "published"


@dataclass(frozen=True)
class InventoryEntry:
    """One eleven-field inventory record for a single path (plan §18.2)."""

    path: str
    kind: InventoryKind
    action: InventoryAction
    purpose: str
    size_bytes: int
    sha256: str | None
    stage: str
    used_by: tuple[str, ...]
    rebuildable: bool
    deletion_class: DeletionClass
    deletion_consequence: str

    def as_json(self) -> dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind.value,
            "action": self.action.value,
            "purpose": self.purpose,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "stage": self.stage,
            "used_by": list(self.used_by),
            "rebuildable": self.rebuildable,
            "deletion_class": self.deletion_class.value,
            "deletion_consequence": self.deletion_consequence,
        }


def published_content_entries(
    projection: ProjectionResult, *, stage: str = "publication"
) -> tuple[InventoryEntry, ...]:
    """Derive ``published`` inventory records from the projection's content files.

    Only a ``valid`` or ``partial`` artifact is a published formal output;
    ``invalid`` and ``unavailable`` artifacts are omitted (plan §17.5: failed and
    unavailable artifacts carry an explicit status but are not formal outputs). A
    Minimal RunBundle still lists those in the manifest, while the inventory
    records only the paths that are genuinely published files. Entries follow the
    projection's sorted-path order.
    """

    entries: list[InventoryEntry] = []
    for artifact in projection.artifacts:
        if artifact.status not in _PUBLISHED_CONTENT_STATUSES or artifact.content is None:
            continue
        assert artifact.sha256 is not None  # a valid/partial artifact always has bytes
        entries.append(
            InventoryEntry(
                path=artifact.path,
                kind=InventoryKind.FILE,
                action=InventoryAction.PUBLISHED,
                purpose=f"发布的 {artifact.kind.value} 制品",
                size_bytes=len(artifact.content.encode("utf-8")),
                sha256=artifact.sha256,
                stage=stage,
                used_by=(),
                rebuildable=True,
                deletion_class=DeletionClass.PUBLISHED,
                deletion_consequence="删除后需要重新发布该正式输出。",
            )
        )
    return tuple(entries)


@dataclass(frozen=True)
class RunInventory:
    """The machine inventory published as ``run-inventory.json`` (plan §18.2)."""

    source_id: str
    run_id: str
    entries: tuple[InventoryEntry, ...] = ()

    def sorted_entries(self) -> tuple[InventoryEntry, ...]:
        """Return the records in a stable order for deterministic output."""

        return tuple(
            sorted(
                self.entries, key=lambda entry: (entry.path, entry.action.value, entry.kind.value)
            )
        )

    def as_json(self) -> dict[str, object]:
        return {
            "schema_version": _INVENTORY_SCHEMA_VERSION,
            "source_id": self.source_id,
            "run_id": self.run_id,
            "entries": [entry.as_json() for entry in self.sorted_entries()],
        }

    def to_json_text(self) -> str:
        return _canonical_json(self.as_json())

    def read_entries(self) -> tuple[InventoryEntry, ...]:
        """Return the read-only external files (plan §18.1 external-reads section)."""

        return tuple(
            entry for entry in self.sorted_entries() if entry.action is InventoryAction.READ
        )

    def written_entries(self) -> tuple[InventoryEntry, ...]:
        """Return the created, modified, downloaded, and published paths, sorted."""

        return tuple(entry for entry in self.sorted_entries() if entry.action in _WRITTEN_ACTIONS)

    def deletable_entries(self) -> tuple[InventoryEntry, ...]:
        """Return the entries a user *could* delete, sorted, for the cleanup plan.

        Everything except formally ``published`` outputs and ``keep_for_audit``
        evidence — i.e. the ``safe_to_delete`` and ``rebuildable`` paths. This is
        a read-only classification; nothing is deleted.
        """

        deletable = {DeletionClass.SAFE_TO_DELETE, DeletionClass.REBUILDABLE}
        return tuple(entry for entry in self.sorted_entries() if entry.deletion_class in deletable)


def build_run_inventory(
    *,
    source_id: str,
    run_id: str,
    entries: Sequence[InventoryEntry] = (),
) -> RunInventory:
    """Assemble the run inventory from recorded path records.

    Paths must be unique per ``(path, action)`` so the same path used twice in
    different ways is legible while an accidental duplicate is rejected.
    """

    seen: set[tuple[str, str]] = set()
    for entry in entries:
        marker = (entry.path, entry.action.value)
        if marker in seen:
            raise RunReportError(
                "duplicate_inventory_entry",
                f"The inventory records {entry.path!r} for action "
                f"{entry.action.value!r} more than once.",
            )
        seen.add(marker)
    return RunInventory(source_id=source_id, run_id=run_id, entries=tuple(entries))


# --- Processing report (plan §18.1) -----------------------------------------


@dataclass(frozen=True)
class InputRecord:
    """One input source, its Part, and its content hash (plan §18.1)."""

    part_id: str
    source: str
    sha256: str


@dataclass(frozen=True)
class ToolRecord:
    """A tool used by the run: its path, version, and purpose (plan §18.1)."""

    name: str
    path: str
    version: str
    purpose: str


@dataclass(frozen=True)
class EnvironmentInfo:
    """The Python environment identity of the run (plan §18.1)."""

    python_version: str
    virtualenv_path: str
    lockfile_sha256: str


@dataclass(frozen=True)
class ModelRecord:
    """A model used by the run, summarized for the readable report (plan §18.1).

    The actual model *files* are listed in the run inventory (kind ``model``);
    this record is the readable summary (plan line: models are summarized in the
    readable report and enumerated as files in the JSON inventory).
    """

    name: str
    revision: str
    sha256: str
    path: str
    size_bytes: int
    purpose: str


@dataclass(frozen=True)
class ParameterRecord:
    """A key parameter, prompt version, language, or quality-config value."""

    name: str
    value: str


@dataclass(frozen=True)
class NetworkAccess:
    """A network request or download with a redacted target (plan §18.1)."""

    action: str
    target: str
    source: str
    purpose: str


@dataclass(frozen=True)
class ResourceUsage:
    """Measured resource usage; a field is ``None`` when not measured."""

    elapsed_seconds: float | None = None
    peak_memory_bytes: int | None = None
    disk_delta_bytes: int | None = None


#: The plan §18.1 section headers. Each is a named constant referenced directly
#: by its render method (never by tuple position, so reordering can never
#: mislabel a section), and :data:`PROCESSING_REPORT_SECTIONS` is derived from
#: them as the single source of truth the offline contract test checks against.
SECTION_STATUS = "## 项目阶段与运行状态"
SECTION_INPUTS = "## 输入、来源、Part 与哈希"
SECTION_TOOLS = "## 工具"
SECTION_ENVIRONMENT = "## 运行环境"
SECTION_MODELS = "## 模型"
SECTION_PARAMETERS = "## 关键参数、提示词、语言与质量配置"
SECTION_NETWORK = "## 网络请求与下载"
SECTION_EXTERNAL_READS = "## 读取但未修改的外部文件"
SECTION_WRITTEN_PATHS = "## 创建、修改与发布的路径"
SECTION_RESOURCES = "## 实际耗时、峰值内存与磁盘变化"
SECTION_RESULTS = "## 成功、不完整、警告与待复核区间"
SECTION_CLEANUP = "## 清理计划（可删除项目及删除后果）"

PROCESSING_REPORT_SECTIONS: tuple[str, ...] = (
    SECTION_STATUS,
    SECTION_INPUTS,
    SECTION_TOOLS,
    SECTION_ENVIRONMENT,
    SECTION_MODELS,
    SECTION_PARAMETERS,
    SECTION_NETWORK,
    SECTION_EXTERNAL_READS,
    SECTION_WRITTEN_PATHS,
    SECTION_RESOURCES,
    SECTION_RESULTS,
    SECTION_CLEANUP,
)


def _optional_bytes(value: int | None) -> str:
    return f"{value} 字节" if value is not None else "未测量"


def _optional_seconds(value: float | None) -> str:
    return f"{value:.3f} 秒" if value is not None else "未测量"


@dataclass(frozen=True)
class ProcessingReport:
    """The plan §18.1 readable report published as ``processing-report.md``.

    The created/modified/published paths section and the cleanup section are
    rendered from the run inventory, so the readable report and the machine
    inventory never disagree about what the run touched or what may be deleted.
    Every section header in :data:`PROCESSING_REPORT_SECTIONS` is always present,
    with a placeholder line when the run has nothing to report for it.
    """

    source_id: str
    run_id: str
    plan_id: str
    run_status: RunStatus
    inventory: RunInventory
    inputs: tuple[InputRecord, ...] = ()
    tools: tuple[ToolRecord, ...] = ()
    environment: EnvironmentInfo | None = None
    models: tuple[ModelRecord, ...] = ()
    parameters: tuple[ParameterRecord, ...] = ()
    network: tuple[NetworkAccess, ...] = ()
    resource_usage: ResourceUsage = field(default_factory=ResourceUsage)
    warnings: tuple[str, ...] = ()
    review_needed: tuple[ReviewNeededInterval, ...] = ()

    def to_markdown(self) -> str:
        lines: list[str] = ["# 处理报告", ""]
        self._render_status(lines)
        self._render_inputs(lines)
        self._render_tools(lines)
        self._render_environment(lines)
        self._render_models(lines)
        self._render_parameters(lines)
        self._render_network(lines)
        self._render_external_reads(lines)
        self._render_written_paths(lines)
        self._render_resources(lines)
        self._render_results(lines)
        self._render_cleanup(lines)
        return _markdown(lines)

    def _render_status(self, lines: list[str]) -> None:
        lines.append(SECTION_STATUS)
        lines.append("")
        lines.append(PROJECT_STAGE_LINE)
        lines.append("")
        lines.append(f"- 来源：`{self.source_id}`")
        lines.append(f"- 计划：`{self.plan_id}`")
        lines.append(f"- 运行：`{self.run_id}`")
        lines.append(f"- 运行状态：{self.run_status.value}")
        lines.append("")

    def _render_inputs(self, lines: list[str]) -> None:
        lines.append(SECTION_INPUTS)
        lines.append("")
        if not self.inputs:
            lines.append("- 无输入记录。")
        else:
            for record in self.inputs:
                lines.append(
                    f"- Part `{record.part_id}`：来源 {record.source}，哈希 `{record.sha256}`"
                )
        lines.append("")

    def _render_tools(self, lines: list[str]) -> None:
        lines.append(SECTION_TOOLS)
        lines.append("")
        if not self.tools:
            lines.append("- 未使用额外工具。")
        else:
            for tool in self.tools:
                lines.append(
                    f"- {tool.name} `{tool.version}`（路径 `{tool.path}`）：{tool.purpose}"
                )
        lines.append("")

    def _render_environment(self, lines: list[str]) -> None:
        lines.append(SECTION_ENVIRONMENT)
        lines.append("")
        if self.environment is None:
            lines.append("- 运行环境未记录。")
        else:
            lines.append(f"- Python 版本：{self.environment.python_version}")
            lines.append(f"- 虚拟环境：`{self.environment.virtualenv_path}`")
            lines.append(f"- 锁文件哈希：`{self.environment.lockfile_sha256}`")
        lines.append("")

    def _render_models(self, lines: list[str]) -> None:
        lines.append(SECTION_MODELS)
        lines.append("")
        if not self.models:
            lines.append("- 未使用模型。")
        else:
            for model in self.models:
                lines.append(
                    f"- {model.name} revision `{model.revision}` "
                    f"哈希 `{model.sha256}`（路径 `{model.path}`，"
                    f"{model.size_bytes} 字节）：{model.purpose}"
                )
        lines.append("")

    def _render_parameters(self, lines: list[str]) -> None:
        lines.append(SECTION_PARAMETERS)
        lines.append("")
        if not self.parameters:
            lines.append("- 无关键参数记录。")
        else:
            for parameter in self.parameters:
                lines.append(f"- {parameter.name}：{parameter.value}")
        lines.append("")

    def _render_network(self, lines: list[str]) -> None:
        lines.append(SECTION_NETWORK)
        lines.append("")
        if not self.network:
            lines.append("- 无网络请求或下载。")
        else:
            for access in self.network:
                lines.append(
                    f"- {access.action} {access.target}（来源 {access.source}）：{access.purpose}"
                )
        lines.append("")

    def _render_external_reads(self, lines: list[str]) -> None:
        lines.append(SECTION_EXTERNAL_READS)
        lines.append("")
        reads = self.inventory.read_entries()
        if not reads:
            lines.append("- 无读取但未修改的外部文件。")
        else:
            for entry in reads:
                lines.append(f"- `{entry.path}`（{entry.kind.value}）：{entry.purpose}")
        lines.append("")

    def _render_written_paths(self, lines: list[str]) -> None:
        lines.append(SECTION_WRITTEN_PATHS)
        lines.append("")
        written = self.inventory.written_entries()
        if not written:
            lines.append("- 本次未创建、修改或发布任何路径。")
        else:
            for entry in written:
                lines.append(f"- [{entry.action.value}] `{entry.path}`：{entry.purpose}")
        lines.append("")

    def _render_resources(self, lines: list[str]) -> None:
        lines.append(SECTION_RESOURCES)
        lines.append("")
        usage = self.resource_usage
        lines.append(f"- 实际耗时：{_optional_seconds(usage.elapsed_seconds)}")
        lines.append(f"- 峰值内存：{_optional_bytes(usage.peak_memory_bytes)}")
        lines.append(f"- 磁盘变化：{_optional_bytes(usage.disk_delta_bytes)}")
        lines.append("")

    def _render_results(self, lines: list[str]) -> None:
        lines.append(SECTION_RESULTS)
        lines.append("")
        lines.append(f"- 运行状态：{self.run_status.value}")
        if self.warnings:
            for warning in self.warnings:
                lines.append(f"- 警告：{warning}")
        else:
            lines.append("- 无警告。")
        review_needed = _sorted_review_needed(self.review_needed)
        if review_needed:
            lines.extend(
                _review_needed_line(interval, prefix="待复核 ") for interval in review_needed
            )
        else:
            lines.append("- 无待复核区间。")
        lines.append("")

    def _render_cleanup(self, lines: list[str]) -> None:
        lines.append(SECTION_CLEANUP)
        lines.append("")
        lines.append(
            "本项目没有 cleanup 命令，也不会自动删除任何文件；以下为可删除项目及其"
            "后果，删除始终由用户显式执行。源媒体、原始 ASR、对齐证据与正式输出默认保留。"
        )
        lines.append("")
        deletable = self.inventory.deletable_entries()
        if not deletable:
            lines.append("- 无建议删除的项目。")
        else:
            for entry in deletable:
                klass = entry.deletion_class.value
                lines.append(f"- `{entry.path}`（{klass}）：{entry.deletion_consequence}")
        lines.append("")


# --- Diagnostics ------------------------------------------------------------


def render_events_snapshot(events: Sequence[RunEvent]) -> str:
    """Render the run events journal as a diagnostics snapshot document.

    A deterministic JSON snapshot of the append-only journal, in sequence order,
    so a published (or Minimal) RunBundle carries the audit trail even after the
    working directory is gone. Reading only; the journal itself is untouched.
    """

    ordered = sorted(events, key=lambda event: event.sequence)
    payload = {
        "schema_version": _DIAGNOSTICS_SCHEMA_VERSION,
        "events": [
            {
                "sequence": event.sequence,
                "at": event.at,
                "kind": event.kind.value,
                "data": dict(event.data),
            }
            for event in ordered
        ],
    }
    return _canonical_json(payload)


# --- Minimal RunBundle floor ------------------------------------------------


def assemble_minimal_run_bundle(
    *,
    quality_report: QualityReport,
    processing_report: ProcessingReport,
    inventory: RunInventory,
    events: Sequence[RunEvent],
) -> tuple[BundleDocument, ...]:
    """Assemble the audit documents of the Minimal RunBundle floor.

    Returns the five audit documents — both quality reports, the processing
    report, the run inventory, and the diagnostics events snapshot — as
    :class:`~video_content_pipeline.publication.BundleDocument`\\ s ready for
    :func:`~video_content_pipeline.publication.publish_run_bundle`, which adds the
    sixth piece, ``manifest.json``. This single entry point is what every
    ordinary failure path calls, so the floor can never be published incomplete.
    """

    documents = (
        BundleDocument(QUALITY_REPORT_MARKDOWN_PATH, quality_report.to_markdown()),
        BundleDocument(QUALITY_REPORT_JSON_PATH, quality_report.to_json_text()),
        BundleDocument(PROCESSING_REPORT_PATH, processing_report.to_markdown()),
        BundleDocument(RUN_INVENTORY_PATH, inventory.to_json_text()),
        BundleDocument(DIAGNOSTICS_EVENTS_PATH, render_events_snapshot(events)),
    )
    produced = {document.path for document in documents}
    missing = set(MINIMAL_RUN_BUNDLE_DOCUMENTS) - produced
    if missing:  # pragma: no cover - guards the floor against a future edit
        raise RunReportError(
            "incomplete_minimal_bundle",
            f"The Minimal RunBundle floor is missing {sorted(missing)}.",
        )
    return documents
