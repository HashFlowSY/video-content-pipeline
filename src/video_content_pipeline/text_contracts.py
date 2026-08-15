"""Phase 6's versioned text-generation and rendering contracts (ticket 03).

The Controlled offline text adapter, its prompt template, the Text-model output
projection schema, and the evidence-rule record each carry an explicit immutable
identity so a future real-model boundary can prove exactly which contract
produced a candidate. This module:

* revalidates the four project-managed versioned artifacts against the
  ``text-analysis-rules.json`` identities and binds each to hash evidence;
* projects a raw Text-model output through the versioned output schema, rejecting
  a whole invalid or incomplete envelope as ``model_output_invalid`` without
  inventing defaults or emitting a partial formal projection; and
* deterministically renders the authoritative JSON report into a workspace
  Markdown rendition, retaining the renderer version and content hash while the
  JSON remains authoritative.

Individual segment, title, and content validation (ticket 05) and the generating
adapter itself (ticket 04) build on these identities. See
``docs/PHASE_06_SPECIFICATION.md`` and the Text Analysis Context.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from video_content_pipeline.evidence import InputEvidence, input_evidence
from video_content_pipeline.planning import PlanningDiagnostic

TEXT_REPORT_RENDERER_VERSION = "phase-06-text-report-renderer-v1"

_RULES_RELATIVE_PATH = ("config", "text-analysis-rules.json")
_CONTRACT_DIRECTORY = ("config", "text-analysis")


class TextContractError(ValueError):
    """A rejected Phase 6 contract artifact with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class VersionedContractArtifact:
    """One project-managed versioned artifact bound to hash evidence."""

    kind: str
    version: str
    document: Mapping[str, object]
    evidence: InputEvidence

    def as_json(self) -> dict[str, object]:
        return {"version": self.version, **self.evidence.as_json()}


@dataclass(frozen=True)
class TextGenerationContracts:
    """The four bound versioned identities for one text-analysis attempt."""

    prompt_template: VersionedContractArtifact
    output_schema: VersionedContractArtifact
    evidence_rules: VersionedContractArtifact
    controlled_adapter: VersionedContractArtifact

    def as_json(self) -> dict[str, object]:
        return {
            "prompt_template": self.prompt_template.as_json(),
            "output_schema": self.output_schema.as_json(),
            "evidence_rules": self.evidence_rules.as_json(),
            "controlled_adapter": self.controlled_adapter.as_json(),
        }


@dataclass(frozen=True)
class TextModelProjection:
    """The versioned interpretation outcome of one raw Text-model output.

    A ``projected`` outcome carries the verified envelope verbatim; a
    ``model_output_invalid`` outcome carries no formal projection so the raw
    output is retained only as restricted audit evidence by the caller.
    """

    state: str
    projection: dict[str, object] | None
    diagnostic: PlanningDiagnostic | None


@dataclass(frozen=True)
class RenderedTextReport:
    """A deterministic Markdown rendition of a verified JSON report."""

    version: str
    text: str
    sha256: str
    byte_count: int

    def as_json(self) -> dict[str, object]:
        return {
            "version": self.version,
            "sha256": self.sha256,
            "byte_count": self.byte_count,
        }


def revalidate_text_generation_contracts(project_root: Path) -> TextGenerationContracts:
    """Revalidate and bind the four versioned Phase 6 generation contracts.

    Each artifact must exist, declare ``schema_version`` 1, and carry the exact
    version named by ``text-analysis-rules.json``. The Controlled offline text
    adapter identity must additionally name the same prompt-template,
    output-schema, and evidence-rule versions, so a drifted or internally
    inconsistent contract set blocks the attempt rather than silently mixing
    identities.
    """

    rules = _load_rules(project_root)
    contract_directory = project_root.joinpath(*_CONTRACT_DIRECTORY)
    prompt_template = _load_artifact(
        contract_directory / "prompt-template.json",
        kind="prompt_template",
        expected_version=_rule_version(rules, "prompt_template_version", "prompt_template_invalid"),
        invalid_reason="prompt_template_invalid",
    )
    output_schema = _load_artifact(
        contract_directory / "output-schema.json",
        kind="output_schema",
        expected_version=_rule_version(rules, "output_schema_version", "output_schema_invalid"),
        invalid_reason="output_schema_invalid",
    )
    evidence_rules = _load_artifact(
        contract_directory / "evidence-rules.json",
        kind="evidence_rules",
        expected_version=_rule_version(rules, "evidence_rules_version", "evidence_rules_invalid"),
        invalid_reason="evidence_rules_invalid",
    )
    controlled_adapter = _load_artifact(
        contract_directory / "controlled-adapter.json",
        kind="controlled_adapter",
        expected_version=_rule_version(
            rules, "controlled_adapter_identity", "controlled_adapter_invalid"
        ),
        invalid_reason="controlled_adapter_invalid",
    )
    _assert_adapter_consistency(controlled_adapter, prompt_template, output_schema, evidence_rules)
    return TextGenerationContracts(
        prompt_template=prompt_template,
        output_schema=output_schema,
        evidence_rules=evidence_rules,
        controlled_adapter=controlled_adapter,
    )


def project_text_model_output(
    raw_output: object, contracts: TextGenerationContracts
) -> TextModelProjection:
    """Project a raw Text-model output through the versioned output schema.

    The whole envelope must match the schema version, the bound output-schema and
    controlled-adapter identities, and carry a well-formed ``result`` container.
    Any drift rejects the complete output as ``model_output_invalid`` with no
    defaults, guesses, or partial projection. Individual segment and content
    validation happens later against a projected envelope.
    """

    envelope = contracts.output_schema.document.get("envelope")
    if not isinstance(envelope, Mapping):
        return _invalid_projection("The bound output schema declares no envelope.")
    if not isinstance(raw_output, Mapping):
        return _invalid_projection("Text-model output is not a JSON object.")
    reason = _envelope_violation(raw_output, envelope, contracts)
    if reason is not None:
        return _invalid_projection(reason)
    return TextModelProjection(state="projected", projection=dict(raw_output), diagnostic=None)


def render_text_analysis_markdown(report: Mapping[str, object]) -> RenderedTextReport:
    """Deterministically render a verified JSON report into workspace Markdown.

    The rendition summarizes the report status, plan and subtitle identities,
    verified segment and chapter counts, the collection-summary entry count with
    its declared subtitle-unavailable Parts and limitation reasons (ticket 06), the
    mandatory ``audio_completeness=not_verified`` notice, a diagnostic-reason
    summary, and any pending decision (ticket 07). It never includes raw generated
    text or item-level validation dumps; the JSON report remains authoritative.
    Later tickets extend the summary with unsupported-item counts once those
    fields exist on the report.
    """

    lines: list[str] = []
    lines.append("# 文本分析报告")
    lines.append("")
    lines.append(f"- 状态 status: `{_scalar(report.get('status'))}`")
    lines.append(f"- 计划 plan_id: `{_scalar(report.get('plan_id'))}`")
    lines.append(f"- 字幕报告 subtitle_report_id: `{_scalar(report.get('subtitle_report_id'))}`")
    lines.append(f"- 音频完整性 audio_completeness: `{_scalar(report.get('audio_completeness'))}`")

    segments = _as_list(report.get("segments"))
    chapters = _as_list(report.get("chapters"))
    lines.append("")
    lines.append("## 语义段 Segments")
    lines.append(f"已验证语义段数量 verified segments: {len(segments)}")
    fallback_segments = sum(
        1
        for segment in segments
        if isinstance(segment, Mapping) and segment.get("origin") == "conservative_fallback"
    )
    if fallback_segments:
        lines.append(f"保守回退语义段 conservative-fallback segments: {fallback_segments}")
    unsupported = report.get("unsupported_item_count")
    if isinstance(unsupported, int):
        lines.append(f"未获支持的生成条目 unsupported generated items: {unsupported}")
    lines.append("")
    lines.append("## 章节 Chapters")
    lines.append(f"章节数量 chapters: {len(chapters)}")
    # Every chapter summary declares audio_completeness regardless of any evidence.
    lines.append("音频完整性 audio_completeness: `not_verified`")
    lines.append("")
    lines.append("## 合集摘要 Collection summary")
    _render_collection_summary(lines, report.get("collection_summary"))

    lines.append("")
    lines.append("## 限制与诊断 Limitations and diagnostics")
    diagnostics = _as_list(report.get("diagnostics"))
    lines.append(f"诊断条目数量 diagnostic count: {len(diagnostics)}")
    reasons = sorted(
        {_scalar(item.get("reason")) for item in diagnostics if isinstance(item, Mapping)}
    )
    if reasons:
        lines.append("诊断原因 reasons: " + ", ".join(f"`{reason}`" for reason in reasons))
    required_decision = report.get("required_decision")
    if isinstance(required_decision, Mapping):
        lines.append(
            "待定决策 pending decision: "
            f"`{_scalar(required_decision.get('reason'))}` "
            f"→ `{_scalar(required_decision.get('decision'))}`"
        )
    lines.append("")
    lines.append("> JSON 报告是权威来源；本 Markdown 为工作区确定性再现，不包含原始生成文本。")
    lines.append("")

    text = "\n".join(lines)
    encoded = text.encode("utf-8")
    return RenderedTextReport(
        version=TEXT_REPORT_RENDERER_VERSION,
        text=text,
        sha256=sha256(encoded).hexdigest(),
        byte_count=len(encoded),
    )


def _render_collection_summary(lines: list[str], summary: object) -> None:
    """Summarize collection omissions and limitations without dumping item prose.

    A ``None`` summary renders ``无``. Otherwise the readable report states the
    verified-entry count, declares every subtitle-unavailable Part with its reason,
    and lists the distinct limitation reasons. It never includes the cited entry
    prose or item-level validation detail; the JSON report remains authoritative.
    """

    if not isinstance(summary, Mapping):
        lines.append("无")
        return
    entries = _as_list(summary.get("entries"))
    lines.append(f"合集条目数量 entry count: {len(entries)}")
    lines.append("音频完整性 audio_completeness: `not_verified`")
    omitted = [item for item in _as_list(summary.get("omitted_parts")) if isinstance(item, Mapping)]
    lines.append(f"不可用分卷 unavailable Parts: {len(omitted)}")
    for item in omitted:
        lines.append(
            f"- `{_scalar(item.get('part_id'))}` ({_scalar(item.get('reason'))})"
        )
    limitations = _as_list(summary.get("limitations"))
    reasons = sorted(
        {_scalar(item.get("reason")) for item in limitations if isinstance(item, Mapping)}
    )
    if reasons:
        joined = ", ".join(f"`{reason}`" for reason in reasons)
        lines.append("限制原因 limitation reasons: " + joined)


def _read_json_mapping(
    path: Path, *, invalid_reason: str, read_message: str
) -> Mapping[str, object]:
    """Read a JSON object from ``path`` or raise the caller's contract error."""

    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TextContractError(invalid_reason, read_message) from error
    if not isinstance(decoded, Mapping):
        raise TextContractError(invalid_reason, f"{path.name} is not a JSON object.")
    return decoded


def _load_rules(project_root: Path) -> Mapping[str, object]:
    decoded = _read_json_mapping(
        project_root.joinpath(*_RULES_RELATIVE_PATH),
        invalid_reason="text_analysis_rules_invalid",
        read_message="Text analysis rules cannot be read.",
    )
    if decoded.get("schema_version") != 1:
        raise TextContractError(
            "text_analysis_rules_invalid", "Text analysis rules have an invalid schema."
        )
    return decoded


def _rule_version(rules: Mapping[str, object], field: str, invalid_reason: str) -> str:
    value = rules.get(field)
    if not isinstance(value, str) or not value:
        raise TextContractError(invalid_reason, f"Text analysis rules omit a valid {field}.")
    return value


def _load_artifact(
    path: Path, *, kind: str, expected_version: str, invalid_reason: str
) -> VersionedContractArtifact:
    decoded = _read_json_mapping(
        path,
        invalid_reason=invalid_reason,
        read_message=f"Contract artifact {path.name} cannot be read.",
    )
    if decoded.get("schema_version") != 1 or decoded.get("version") != expected_version:
        raise TextContractError(
            invalid_reason,
            f"Contract artifact {path.name} does not match the bound {kind} identity.",
        )
    return VersionedContractArtifact(
        kind=kind,
        version=expected_version,
        document=decoded,
        evidence=input_evidence(path),
    )


def _assert_adapter_consistency(
    controlled_adapter: VersionedContractArtifact,
    prompt_template: VersionedContractArtifact,
    output_schema: VersionedContractArtifact,
    evidence_rules: VersionedContractArtifact,
) -> None:
    document = controlled_adapter.document
    expected = {
        "prompt_template_version": prompt_template.version,
        "output_schema_version": output_schema.version,
        "evidence_rules_version": evidence_rules.version,
    }
    for field, version in expected.items():
        if document.get(field) != version:
            raise TextContractError(
                "controlled_adapter_invalid",
                f"Controlled offline text adapter identity names a stale {field}.",
            )


def _envelope_violation(
    raw_output: Mapping[str, object],
    envelope: Mapping[str, object],
    contracts: TextGenerationContracts,
) -> str | None:
    for field in _string_sequence(envelope.get("required_fields")):
        if field not in raw_output:
            return f"Text-model output is missing the required field {field!r}."
    expected_schema_version = envelope.get("expected_schema_version")
    if raw_output.get("schema_version") != expected_schema_version:
        return "Text-model output has an unexpected schema version."
    if raw_output.get("output_schema_version") != contracts.output_schema.version:
        return "Text-model output does not name the bound output-schema identity."
    if raw_output.get("adapter_identity") != contracts.controlled_adapter.version:
        return "Text-model output does not name the bound controlled-adapter identity."
    result = raw_output.get("result")
    result_rules = envelope.get("result")
    if not isinstance(result, Mapping) or not isinstance(result_rules, Mapping):
        return "Text-model output result container is missing or malformed."
    for field in _string_sequence(result_rules.get("required_fields")):
        if field not in result:
            return f"Text-model output result is missing the required field {field!r}."
    for field in _string_sequence(result_rules.get("list_fields")):
        if not isinstance(result.get(field), list):
            return f"Text-model output result field {field!r} is not a list."
    for field in _string_sequence(result_rules.get("optional_object_or_null_fields")):
        value = result.get(field)
        if value is not None and not isinstance(value, Mapping):
            return f"Text-model output result field {field!r} is neither an object nor null."
    return None


def _invalid_projection(message: str) -> TextModelProjection:
    return TextModelProjection(
        state="model_output_invalid",
        projection=None,
        diagnostic=PlanningDiagnostic("model_output_invalid", message),
    )


def _string_sequence(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _scalar(value: object) -> str:
    if value is None:
        return "none"
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)
