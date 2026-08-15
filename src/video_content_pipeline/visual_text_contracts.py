"""Phase 8's versioned Controlled offline OCR adapter and output projection (ticket 05).

OCR text enters the visual-text evidence system through exactly one auditable
entry point: the versioned OCR output projection. This module mirrors the Phase 7
transcription adapter (``transcription_contracts``) for the Visual-Text Context:

* it revalidates the two project-managed versioned identities -- the OCR
  projection schema and the Controlled offline OCR adapter -- against
  ``config/visual-text/rules.json`` and binds each to hash evidence;
* it loads the optional bound synthetic output fixture, hash-verifying its bytes
  and confining it to project-relative, non-escaping paths, and carries the
  symmetric input-manifest hash the fixture was authored for so a caller can
  prove the fixture matches exactly the frames detection and sampling selected;
* it projects a raw OCR model output through the versioned projection into typed
  items with a Part, an exact PTS, a Part-local ``visual_page_id``, verbatim text
  (its source language preserved), a confidence, and optional character-indexed
  language spans, rejecting any incomplete or schema-invalid output whole as
  ``model_output_invalid`` without inventing defaults or emitting a partial
  projection; and
* it retains raw model output as restricted local audit evidence, marked
  audit-only and kept apart from the formal report tree.

The Controlled offline OCR adapter is not a model asset: it returns a fixed
output fixture bound to a fixed input identity, so it can never earn a real-model
quality qualification. Contract artifacts are our own revalidated ground truth,
so a malformed one raises ``VisualTextContractError``; a malformed *model output*
is untrusted, so it is retained as a ``model_output_invalid`` state rather than
raised. No model is downloaded or executed. See ``docs/PHASE_08_SPECIFICATION.md``
and the Visual-Text Context; the projection inherits ADR 0036/0037's offline
boundary and ADR 0047's single ``ocr_primary`` capability.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TypeGuard

from video_content_pipeline.evidence import (
    InputEvidence,
    input_evidence,
    write_bytes_once,
)
from video_content_pipeline.planning import PlanningDiagnostic
from video_content_pipeline.timecode import ExactTime

# The single provider-neutral role a Controlled offline OCR adapter output may
# fill (ADR 0047); a fixture or a projection naming any other capability is
# invalid.
OCR_CAPABILITIES = ("ocr_primary",)

_RULES_RELATIVE_PATH = ("config", "visual-text", "rules.json")
_CONTRACT_DIRECTORY = ("config", "visual-text")

_MODEL_OUTPUT_INVALID = "model_output_invalid"


class VisualTextContractError(ValueError):
    """A rejected Phase 8 visual-text contract artifact with a stable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


# --- Versioned contract identities ------------------------------------------


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
class OcrProjectionRuleset:
    """The declarative item rules the versioned projection schema governs.

    These are read from the bound ``ocr-projection-schema.json`` document rather
    than hardcoded, so the projection-schema version is meaningful: widening the
    confidence range or changing a required-field list is a versioned schema edit,
    not a code change. Structural invariants that cannot be a tunable -- exact
    times, non-negative character-index spans, the nesting shape -- stay in the
    typed projector.
    """

    item_required_fields: tuple[str, ...]
    confidence_range: tuple[float, float]
    language_span_required_fields: tuple[str, ...]


@dataclass(frozen=True)
class OcrGenerationContracts:
    """The two bound versioned identities for one OCR generation attempt."""

    projection_schema: VersionedContractArtifact
    controlled_adapter: VersionedContractArtifact
    projection_ruleset: OcrProjectionRuleset

    @property
    def implementation_version(self) -> str | None:
        value = self.controlled_adapter.document.get("implementation_version")
        return value if isinstance(value, str) else None

    def as_json(self) -> dict[str, object]:
        return {
            "projection_schema": self.projection_schema.as_json(),
            "controlled_adapter": self.controlled_adapter.as_json(),
            "implementation_version": self.implementation_version,
        }


def revalidate_ocr_contracts(project_root: Path) -> OcrGenerationContracts:
    """Revalidate and bind the two versioned Phase 8 OCR contracts.

    Each artifact must exist, declare ``schema_version`` 1, and carry the exact
    version named by ``config/visual-text/rules.json`` (``ocr_projection.version``
    and ``controlled_ocr_adapter.identity``). The Controlled offline OCR adapter
    identity must additionally name the same projection-schema version, so a
    drifted or internally inconsistent contract set blocks the attempt rather than
    silently mixing identities. The projection schema's ``item`` ruleset is parsed
    here -- it is our own config, so a malformed block raises
    ``ocr_projection_schema_invalid`` -- and drives the versioned projection.
    """

    rules = _load_rules(project_root)
    contract_directory = project_root.joinpath(*_CONTRACT_DIRECTORY)
    projection_schema = _load_artifact(
        contract_directory / "ocr-projection-schema.json",
        kind="projection_schema",
        expected_version=_rule_identity(
            rules, "ocr_projection", "version", "ocr_projection_schema_invalid"
        ),
        invalid_reason="ocr_projection_schema_invalid",
    )
    controlled_adapter = _load_artifact(
        contract_directory / "controlled-ocr-adapter.json",
        kind="controlled_adapter",
        expected_version=_rule_identity(
            rules, "controlled_ocr_adapter", "identity", "controlled_ocr_adapter_invalid"
        ),
        invalid_reason="controlled_ocr_adapter_invalid",
    )
    if controlled_adapter.document.get("projection_schema_version") != projection_schema.version:
        raise VisualTextContractError(
            "controlled_ocr_adapter_invalid",
            "Controlled offline OCR adapter identity names a stale projection-schema version.",
        )
    implementation_version = controlled_adapter.document.get("implementation_version")
    if not isinstance(implementation_version, str) or not implementation_version:
        raise VisualTextContractError(
            "controlled_ocr_adapter_invalid",
            "Controlled offline OCR adapter must declare an implementation version, so the "
            "fixed offline stand-in can never be mistaken for a quality-qualified model.",
        )
    return OcrGenerationContracts(
        projection_schema=projection_schema,
        controlled_adapter=controlled_adapter,
        projection_ruleset=_parse_projection_ruleset(projection_schema.document),
    )


def _parse_projection_ruleset(document: Mapping[str, object]) -> OcrProjectionRuleset:
    """Read the governed item ruleset from the bound projection-schema document."""

    item = document.get("item")
    if not isinstance(item, Mapping):
        raise VisualTextContractError(
            "ocr_projection_schema_invalid", "Projection schema omits an item ruleset."
        )
    language_span = item.get("language_span")
    if not isinstance(language_span, Mapping):
        raise VisualTextContractError(
            "ocr_projection_schema_invalid", "Projection schema omits a language-span ruleset."
        )
    return OcrProjectionRuleset(
        item_required_fields=_schema_string_list(
            item.get("required_fields"), "item.required_fields"
        ),
        confidence_range=_schema_confidence_range(item.get("confidence_range")),
        language_span_required_fields=_schema_string_list(
            language_span.get("required_fields"), "item.language_span.required_fields"
        ),
    )


def _schema_string_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise VisualTextContractError(
            "ocr_projection_schema_invalid", f"Projection schema {field} must be a non-empty list."
        )
    return tuple(item for item in value if isinstance(item, str))


def _schema_confidence_range(value: object) -> tuple[float, float]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(_is_real(bound) for bound in value)
    ):
        raise VisualTextContractError(
            "ocr_projection_schema_invalid",
            "Projection schema item.confidence_range must be a [low, high] pair.",
        )
    low, high = float(value[0]), float(value[1])
    if low > high:
        raise VisualTextContractError(
            "ocr_projection_schema_invalid",
            "Projection schema item.confidence_range low bound exceeds its high bound.",
        )
    return low, high


# --- Symmetric input hashing ------------------------------------------------


def ocr_input_manifest_document(
    plan_id: str,
    selections: Sequence[tuple[str, str, ExactTime, str]],
) -> dict[str, object]:
    """Build the canonical input manifest bound to one controlled OCR generation.

    ``selections`` are the deterministic sampling representatives as
    ``(part_id, visual_page_id, selected_pts, content_fingerprint)`` -- the exact
    frames OCR will read. The manifest is canonically ordered, so binding a
    controlled fixture to the manifest hash transitively binds it to precisely the
    frames detection and sampling selected, regardless of caller order.
    """

    ordered = sorted(selections, key=lambda item: (item[0], item[1]))
    return {
        "schema_version": 1,
        "plan_id": plan_id,
        "selection_count": len(ordered),
        "selections": [
            {
                "part_id": part_id,
                "visual_page_id": visual_page_id,
                "pts": _exact_time_as_json(pts),
                "content_fingerprint": fingerprint,
            }
            for part_id, visual_page_id, pts, fingerprint in ordered
        ],
    }


def ocr_input_manifest_sha256(document: Mapping[str, object]) -> str:
    """Return the canonical content identity of an OCR input manifest document."""

    return sha256(json.dumps(document, sort_keys=True).encode("utf-8")).hexdigest()


# --- Bound synthetic fixture ------------------------------------------------


@dataclass(frozen=True)
class ControlledOcrFixture:
    """A hash-pinned synthetic OCR output fixture bound to an input manifest.

    The Controlled offline OCR adapter is not a model asset. It returns exactly the
    retained ``raw_output`` when the actual input-manifest identity matches
    ``input_fixture_sha256``; both hashes plus the adapter implementation version
    are recorded so a future real-model boundary can prove precisely which fixed
    input produced which fixed output, and so this fixed stand-in can never be
    mistaken for a quality-qualified model.
    """

    capability: str
    implementation_version: str
    raw_output: bytes
    output_fixture: InputEvidence
    input_fixture_sha256: str

    def as_json(self) -> dict[str, object]:
        return {
            "capability": self.capability,
            "implementation_version": self.implementation_version,
            "input_fixture_sha256": self.input_fixture_sha256,
            "output_fixture": self.output_fixture.as_json(),
        }


def load_controlled_ocr_fixture(
    contracts: OcrGenerationContracts, project_root: Path
) -> ControlledOcrFixture | None:
    """Load the Controlled offline OCR adapter's bound synthetic output fixture.

    A ``fixture`` block is optional: without it, no controlled fixture generates
    and the caller acquisition-gates unchanged (no eligible model, no controlled
    stand-in). With it, the block must name a valid OCR capability, the
    project-relative non-escaping output-fixture path and its hash, and the
    input-manifest hash it was authored for. The fixture bytes are hash-verified
    and returned verbatim; the caller compares ``input_fixture_sha256`` to the
    actual selected-frame manifest to prove the fixture matches its revalidated
    inputs.
    """

    adapter_document = contracts.controlled_adapter.document
    fixture = adapter_document.get("fixture")
    if fixture is None:
        return None
    if not isinstance(fixture, Mapping):
        raise VisualTextContractError(
            "controlled_ocr_fixture_invalid", "Controlled OCR adapter fixture block is malformed."
        )
    capability = fixture.get("capability")
    relative = fixture.get("output_fixture_path")
    expected_output_sha = fixture.get("output_fixture_sha256")
    bound_input_sha = fixture.get("input_fixture_sha256")
    if (
        not isinstance(capability, str)
        or not isinstance(relative, str)
        or not relative
        or not isinstance(expected_output_sha, str)
        or not isinstance(bound_input_sha, str)
    ):
        raise VisualTextContractError(
            "controlled_ocr_fixture_invalid",
            "Controlled OCR adapter fixture block omits a capability, fixture path, or hash.",
        )
    if capability not in OCR_CAPABILITIES:
        raise VisualTextContractError(
            "controlled_ocr_fixture_invalid",
            f"Controlled OCR adapter fixture names an unknown capability {capability!r}.",
        )
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise VisualTextContractError(
            "controlled_ocr_fixture_invalid",
            "Controlled OCR adapter output fixture path must be project-relative.",
        )
    fixture_path = project_root / relative
    try:
        raw_output = fixture_path.read_bytes()
    except OSError as error:
        raise VisualTextContractError(
            "controlled_ocr_fixture_invalid",
            "Controlled OCR adapter output fixture cannot be read.",
        ) from error
    if sha256(raw_output).hexdigest() != expected_output_sha:
        raise VisualTextContractError(
            "controlled_ocr_fixture_invalid",
            "Controlled OCR adapter output fixture hash no longer matches its identity.",
        )
    return ControlledOcrFixture(
        capability=capability,
        implementation_version=contracts.implementation_version or "",
        raw_output=raw_output,
        output_fixture=input_evidence(fixture_path),
        input_fixture_sha256=bound_input_sha,
    )


# --- Versioned output projection --------------------------------------------


@dataclass(frozen=True)
class OcrLanguageSpan:
    """A half-open character-index range of an item's text attributed to one language.

    Spans reference character indices into the item's verbatim text, so mixed
    Chinese/English is expressed as adjacent spans and never rewritten into one
    language.
    """

    language: str
    start_char: int
    end_char: int

    def as_json(self) -> dict[str, object]:
        return {
            "language": self.language,
            "start_char": self.start_char,
            "end_char": self.end_char,
        }


@dataclass(frozen=True)
class ProjectedOcrItem:
    """One projected OCR item: a Part, an exact PTS, a page, verbatim text, confidence.

    ``text`` is kept exactly as recognized, in its source language; ``confidence``
    lies inside the schema's confidence range; ``language_spans`` optionally record
    mixed-language structure without ever collapsing the text.
    """

    part_id: str
    visual_page_id: str
    pts: ExactTime
    text: str
    confidence: float
    language_spans: tuple[OcrLanguageSpan, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "visual_page_id": self.visual_page_id,
            "pts": _exact_time_as_json(self.pts),
            "text": self.text,
            "confidence": self.confidence,
            "language_spans": [span.as_json() for span in self.language_spans],
        }


@dataclass(frozen=True)
class OcrOutputProjection:
    """The versioned interpretation outcome of one raw OCR model output.

    A ``projected`` outcome carries the typed items; a ``model_output_invalid``
    outcome carries no items so the raw output is retained only as restricted audit
    evidence by the caller.
    """

    state: str
    capability: str | None
    adapter_version: str | None
    projection_schema_version: str | None
    items: tuple[ProjectedOcrItem, ...]
    diagnostic: PlanningDiagnostic | None

    def as_json(self) -> dict[str, object]:
        return {
            "state": self.state,
            "capability": self.capability,
            "adapter_version": self.adapter_version,
            "projection_schema_version": self.projection_schema_version,
            "items": [item.as_json() for item in self.items],
            "diagnostic": self.diagnostic.as_json() if self.diagnostic is not None else None,
        }


class _ProjectionRejected(Exception):
    """Internal signal that an untrusted model output failed a projection rule."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def project_ocr_output(
    raw_output: object, contracts: OcrGenerationContracts
) -> OcrOutputProjection:
    """Project a raw OCR model output through the versioned projection schema.

    The whole envelope must match the schema version, the bound projection-schema
    and controlled-adapter identities, and a valid OCR capability, and carry a
    well-formed ``result`` whose items satisfy the bound projection ruleset: the
    schema-declared required fields, a non-empty Part and ``visual_page_id``, an
    exact PTS, verbatim text, a confidence inside the schema range, and -- for
    optional language spans -- consistent non-negative character indices inside the
    text. Any drift rejects the complete output as ``model_output_invalid`` with no
    defaults, guesses, or partial projection.
    """

    try:
        capability, items = _project_envelope(raw_output, contracts, contracts.projection_ruleset)
    except _ProjectionRejected as rejected:
        return OcrOutputProjection(
            state=_MODEL_OUTPUT_INVALID,
            capability=None,
            adapter_version=None,
            projection_schema_version=None,
            items=(),
            diagnostic=PlanningDiagnostic(_MODEL_OUTPUT_INVALID, rejected.message),
        )
    return OcrOutputProjection(
        state="projected",
        capability=capability,
        adapter_version=contracts.controlled_adapter.version,
        projection_schema_version=contracts.projection_schema.version,
        items=items,
        diagnostic=None,
    )


def _project_envelope(
    raw_output: object,
    contracts: OcrGenerationContracts,
    ruleset: OcrProjectionRuleset,
) -> tuple[str, tuple[ProjectedOcrItem, ...]]:
    if not isinstance(raw_output, Mapping):
        raise _ProjectionRejected("OCR model output is not a JSON object.")
    if raw_output.get("schema_version") != 1:
        raise _ProjectionRejected("OCR model output has an unexpected schema version.")
    if raw_output.get("projection_schema_version") != contracts.projection_schema.version:
        raise _ProjectionRejected(
            "OCR model output does not name the bound projection-schema identity."
        )
    if raw_output.get("adapter_identity") != contracts.controlled_adapter.version:
        raise _ProjectionRejected(
            "OCR model output does not name the bound controlled-adapter identity."
        )
    capability = raw_output.get("capability")
    if capability not in OCR_CAPABILITIES:
        raise _ProjectionRejected("OCR model output names an unknown capability.")
    result = raw_output.get("result")
    if not isinstance(result, Mapping):
        raise _ProjectionRejected("OCR model output result container is missing or malformed.")
    raw_items = result.get("items")
    if not isinstance(raw_items, list):
        raise _ProjectionRejected("OCR model output result field 'items' is not a list.")
    items = tuple(_project_item(raw_item, ruleset) for raw_item in raw_items)
    return str(capability), items


def _project_item(raw_item: object, ruleset: OcrProjectionRuleset) -> ProjectedOcrItem:
    if not isinstance(raw_item, Mapping):
        raise _ProjectionRejected("An OCR item is not an object.")
    _require_fields(raw_item, ruleset.item_required_fields, "item")
    part_id = raw_item.get("part_id")
    if not isinstance(part_id, str) or not part_id:
        raise _ProjectionRejected("An OCR item omits its Part identity.")
    visual_page_id = raw_item.get("visual_page_id")
    if not isinstance(visual_page_id, str) or not visual_page_id:
        raise _ProjectionRejected("An OCR item omits its visual_page_id.")
    pts = _project_exact_time(raw_item.get("pts"))
    text = raw_item.get("text")
    if not isinstance(text, str):
        raise _ProjectionRejected("An OCR item omits its text.")
    confidence = _project_confidence(raw_item.get("confidence"), ruleset)
    language_spans = _project_language_spans(raw_item.get("language_spans"), len(text), ruleset)
    return ProjectedOcrItem(
        part_id=part_id,
        visual_page_id=visual_page_id,
        pts=pts,
        text=text,
        confidence=confidence,
        language_spans=language_spans,
    )


def _project_confidence(value: object, ruleset: OcrProjectionRuleset) -> float:
    low, high = ruleset.confidence_range
    if not _is_real(value) or not (low <= float(value) <= high):
        raise _ProjectionRejected(
            f"An OCR item confidence is outside the schema range [{low}, {high}]."
        )
    return float(value)


def _require_fields(raw: Mapping[str, object], required_fields: tuple[str, ...], kind: str) -> None:
    for field in required_fields:
        if field not in raw:
            raise _ProjectionRejected(f"An OCR {kind} is missing the required field {field!r}.")


def _project_exact_time(value: object) -> ExactTime:
    if not isinstance(value, Mapping):
        raise _ProjectionRejected("An OCR item time is not an object.")
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if not _is_int(numerator) or not _is_int(denominator):
        raise _ProjectionRejected("An OCR item time omits an integer numerator or denominator.")
    if denominator <= 0:
        raise _ProjectionRejected("An OCR item time denominator must be positive.")
    return ExactTime(numerator, denominator)


def _project_language_spans(
    value: object, text_length: int, ruleset: OcrProjectionRuleset
) -> tuple[OcrLanguageSpan, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _ProjectionRejected("An OCR item language-span list is not a list.")
    return tuple(_project_language_span(raw_span, text_length, ruleset) for raw_span in value)


def _project_language_span(
    raw_span: object, text_length: int, ruleset: OcrProjectionRuleset
) -> OcrLanguageSpan:
    if not isinstance(raw_span, Mapping):
        raise _ProjectionRejected("An OCR language span is not an object.")
    _require_fields(raw_span, ruleset.language_span_required_fields, "language span")
    language = raw_span.get("language")
    start = raw_span.get("start_char")
    end = raw_span.get("end_char")
    if not isinstance(language, str) or not language:
        raise _ProjectionRejected("An OCR language span omits its language.")
    if not _is_non_negative_int(start) or not _is_non_negative_int(end):
        raise _ProjectionRejected("An OCR language span omits a valid character index.")
    if start >= end:
        raise _ProjectionRejected("An OCR language span must cover a positive character range.")
    if end > text_length:
        raise _ProjectionRejected("An OCR language span indexes beyond the item's text.")
    return OcrLanguageSpan(language=language, start_char=start, end_char=end)


# --- Restricted raw-output retention ----------------------------------------


@dataclass(frozen=True)
class RestrictedRawOcrOutput:
    """Retained raw model output as restricted, audit-only local evidence.

    Raw output is diagnostic evidence for inspecting failures; it is marked
    restricted and audit-only and kept apart from the formal report tree so it can
    never leak into formal artifacts or a published frame.
    """

    capability: str
    evidence: InputEvidence

    def as_json(self) -> dict[str, object]:
        return {
            "capability": self.capability,
            "restricted": True,
            "audit_only": True,
            **self.evidence.as_json(),
        }


def retain_restricted_ocr_output(
    raw_output: bytes,
    workspace_path: Path,
    *,
    capability: str,
    label: str,
) -> RestrictedRawOcrOutput:
    """Write raw OCR output once into the workspace's restricted audit tree.

    The bytes land under ``restricted/ocr/<capability>/<label>-raw-native-output.json``,
    apart from the formal report, and are written immutably: a differing rewrite is
    a conflict. The returned record is marked restricted and audit-only so callers
    keep it out of formal reports.
    """

    raw_path = (
        workspace_path / "restricted" / "ocr" / capability / f"{label}-raw-native-output.json"
    )
    write_bytes_once(
        raw_path,
        raw_output,
        conflict_error=lambda message: VisualTextContractError(
            "visual_text_raw_output_conflict", message
        ),
    )
    return RestrictedRawOcrOutput(capability=capability, evidence=input_evidence(raw_path))


# --- Contract-artifact helpers ----------------------------------------------


def _read_json_mapping(
    path: Path, *, invalid_reason: str, read_message: str
) -> Mapping[str, object]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualTextContractError(invalid_reason, read_message) from error
    if not isinstance(decoded, Mapping):
        raise VisualTextContractError(invalid_reason, f"{path.name} is not a JSON object.")
    return decoded


def _load_rules(project_root: Path) -> Mapping[str, object]:
    decoded = _read_json_mapping(
        project_root.joinpath(*_RULES_RELATIVE_PATH),
        invalid_reason="visual_text_rules_invalid",
        read_message="Visual-text rules cannot be read.",
    )
    if decoded.get("schema_version") != 1:
        raise VisualTextContractError(
            "visual_text_rules_invalid", "Visual-text rules have an invalid schema."
        )
    return decoded


def _rule_identity(
    rules: Mapping[str, object], section: str, field: str, invalid_reason: str
) -> str:
    block = rules.get(section)
    if not isinstance(block, Mapping):
        raise VisualTextContractError(
            invalid_reason, f"Visual-text rules omit a {section!r} object."
        )
    value = block.get(field)
    if not isinstance(value, str) or not value:
        raise VisualTextContractError(
            invalid_reason, f"Visual-text rules omit a valid {section}.{field}."
        )
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
        raise VisualTextContractError(
            invalid_reason,
            f"Contract artifact {path.name} does not match the bound {kind} identity.",
        )
    return VersionedContractArtifact(
        kind=kind,
        version=expected_version,
        document=decoded,
        evidence=input_evidence(path),
    )


def _exact_time_as_json(value: ExactTime) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_non_negative_int(value: object) -> TypeGuard[int]:
    return _is_int(value) and value >= 0


def _is_real(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


__all__ = [
    "OCR_CAPABILITIES",
    "ControlledOcrFixture",
    "OcrGenerationContracts",
    "OcrLanguageSpan",
    "OcrOutputProjection",
    "OcrProjectionRuleset",
    "ProjectedOcrItem",
    "RestrictedRawOcrOutput",
    "VersionedContractArtifact",
    "VisualTextContractError",
    "load_controlled_ocr_fixture",
    "ocr_input_manifest_document",
    "ocr_input_manifest_sha256",
    "project_ocr_output",
    "retain_restricted_ocr_output",
    "revalidate_ocr_contracts",
]
