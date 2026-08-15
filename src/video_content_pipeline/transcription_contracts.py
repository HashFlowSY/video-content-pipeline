"""Phase 7's versioned Controlled offline ASR adapter and output projection (ticket 03).

ASR text enters the evidence system through exactly one auditable entry point: the
versioned ASR output projection. This module mirrors the Phase 6 text adapter
(``text_contracts``/``text_generation``) for the Transcription Context:

* it revalidates the two project-managed versioned identities -- the ASR
  projection schema and the Controlled offline ASR adapter -- against
  ``config/transcription/transcription-rules.json`` and binds each to hash
  evidence;
* it loads the optional bound synthetic output fixture, hash-verifying its bytes
  and confining it to project-relative, non-escaping paths, and carries the
  symmetric input-manifest hash the fixture was authored for so a caller can
  prove the fixture matches its revalidated inputs;
* it projects a raw ASR model output through the versioned projection into typed
  cues with exact rational times, text, optional per-token confidence, and
  language spans, rejecting any incomplete or schema-invalid output whole as
  ``model_output_invalid`` without inventing defaults or emitting a partial
  projection; and
* it retains raw model output as restricted local audit evidence, marked
  audit-only and kept apart from the formal report tree.

Contract artifacts are our own revalidated ground truth, so a malformed one
raises ``TranscriptionContractError``; a malformed *model output* is untrusted, so
it is retained as a ``model_output_invalid`` state rather than raised. No model is
downloaded or executed. See ``docs/PHASE_07_SPECIFICATION.md`` and the
Transcription Context; the projection is governed by ADR 0036/0043.
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
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval, TimeValidationError

# The two provider-neutral roles a Controlled offline ASR adapter output may fill;
# a fixture or a projection naming any other capability is invalid.
ASR_CAPABILITIES = ("asr_primary", "asr_review")

_RULES_RELATIVE_PATH = ("config", "transcription", "transcription-rules.json")
_CONTRACT_DIRECTORY = ("config", "transcription")

_MODEL_OUTPUT_INVALID = "model_output_invalid"


class TranscriptionContractError(ValueError):
    """A rejected Phase 7 transcription contract artifact with a stable reason."""

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
class AsrProjectionRuleset:
    """The declarative cue rules the versioned projection schema governs.

    These are read from the bound ``asr-projection-schema.json`` document rather
    than hardcoded, so the projection-schema version is meaningful: widening the
    token confidence range or changing a required-field list is a versioned schema
    edit, not a code change. Structural invariants that cannot be a tunable --
    half-open positive intervals, token-index bounds, the nesting shape -- stay in
    the typed projector.
    """

    cue_required_fields: tuple[str, ...]
    token_required_fields: tuple[str, ...]
    token_confidence_range: tuple[float, float]
    language_span_required_fields: tuple[str, ...]


@dataclass(frozen=True)
class AsrGenerationContracts:
    """The two bound versioned identities for one transcription attempt."""

    projection_schema: VersionedContractArtifact
    controlled_adapter: VersionedContractArtifact
    projection_ruleset: AsrProjectionRuleset

    def as_json(self) -> dict[str, object]:
        return {
            "projection_schema": self.projection_schema.as_json(),
            "controlled_adapter": self.controlled_adapter.as_json(),
        }


def revalidate_asr_contracts(project_root: Path) -> AsrGenerationContracts:
    """Revalidate and bind the two versioned Phase 7 transcription contracts.

    Each artifact must exist, declare ``schema_version`` 1, and carry the exact
    version named by ``transcription-rules.json``. The Controlled offline ASR
    adapter identity must additionally name the same projection-schema version, so
    a drifted or internally inconsistent contract set blocks the attempt rather
    than silently mixing identities. The projection schema's ``cue`` ruleset is
    parsed here -- it is our own config, so a malformed block raises
    ``asr_projection_schema_invalid`` -- and drives the versioned projection.
    """

    rules = _load_rules(project_root)
    contract_directory = project_root.joinpath(*_CONTRACT_DIRECTORY)
    projection_schema = _load_artifact(
        contract_directory / "asr-projection-schema.json",
        kind="projection_schema",
        expected_version=_rule_version(
            rules, "projection_schema_version", "asr_projection_schema_invalid"
        ),
        invalid_reason="asr_projection_schema_invalid",
    )
    controlled_adapter = _load_artifact(
        contract_directory / "controlled-adapter.json",
        kind="controlled_adapter",
        expected_version=_rule_version(
            rules, "controlled_adapter_identity", "controlled_asr_adapter_invalid"
        ),
        invalid_reason="controlled_asr_adapter_invalid",
    )
    if controlled_adapter.document.get("projection_schema_version") != projection_schema.version:
        raise TranscriptionContractError(
            "controlled_asr_adapter_invalid",
            "Controlled offline ASR adapter identity names a stale projection-schema version.",
        )
    return AsrGenerationContracts(
        projection_schema=projection_schema,
        controlled_adapter=controlled_adapter,
        projection_ruleset=_parse_projection_ruleset(projection_schema.document),
    )


def _parse_projection_ruleset(document: Mapping[str, object]) -> AsrProjectionRuleset:
    """Read the governed cue ruleset from the bound projection-schema document."""

    cue = document.get("cue")
    if not isinstance(cue, Mapping):
        raise TranscriptionContractError(
            "asr_projection_schema_invalid", "Projection schema omits a cue ruleset."
        )
    token = cue.get("token")
    language_span = cue.get("language_span")
    if not isinstance(token, Mapping) or not isinstance(language_span, Mapping):
        raise TranscriptionContractError(
            "asr_projection_schema_invalid", "Projection schema omits a token or span ruleset."
        )
    return AsrProjectionRuleset(
        cue_required_fields=_schema_string_list(cue.get("required_fields"), "cue.required_fields"),
        token_required_fields=_schema_string_list(
            token.get("required_fields"), "cue.token.required_fields"
        ),
        token_confidence_range=_schema_confidence_range(token.get("confidence_range")),
        language_span_required_fields=_schema_string_list(
            language_span.get("required_fields"), "cue.language_span.required_fields"
        ),
    )


def _schema_string_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise TranscriptionContractError(
            "asr_projection_schema_invalid", f"Projection schema {field} must be a non-empty list."
        )
    return tuple(item for item in value if isinstance(item, str))


def _schema_confidence_range(value: object) -> tuple[float, float]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(_is_real(bound) for bound in value)
    ):
        raise TranscriptionContractError(
            "asr_projection_schema_invalid",
            "Projection schema cue.token.confidence_range must be a [low, high] pair.",
        )
    low, high = float(value[0]), float(value[1])
    if low > high:
        raise TranscriptionContractError(
            "asr_projection_schema_invalid",
            "Projection schema cue.token.confidence_range low bound exceeds its high bound.",
        )
    return low, high


# --- Symmetric input hashing ------------------------------------------------


def asr_input_manifest_document(
    audio_report_id: str,
    sources: Sequence[tuple[str, int, str]],
) -> dict[str, object]:
    """Build the canonical input manifest bound to one controlled ASR generation.

    ``sources`` are the revalidated audio inputs as ``(source_id, stream_index,
    source_artifact_sha256)`` and ``audio_report_id`` is the required Audio
    analysis report identity. The manifest is canonically ordered, so binding a
    controlled fixture to the manifest hash transitively binds it to the exact
    revalidated inputs regardless of caller order.
    """

    ordered = sorted(sources, key=lambda item: (item[0], item[1]))
    return {
        "schema_version": 1,
        "audio_report_id": audio_report_id,
        "source_count": len(ordered),
        "sources": [
            {"source_id": source_id, "stream_index": stream_index, "sha256": sha256_hex}
            for source_id, stream_index, sha256_hex in ordered
        ],
    }


def asr_input_manifest_sha256(document: Mapping[str, object]) -> str:
    """Return the canonical content identity of an ASR input manifest document."""

    return sha256(json.dumps(document, sort_keys=True).encode("utf-8")).hexdigest()


# --- Bound synthetic fixture ------------------------------------------------


@dataclass(frozen=True)
class ControlledAsrFixture:
    """A hash-pinned synthetic ASR output fixture bound to an input manifest.

    The Controlled offline ASR adapter is not a model asset. It returns exactly the
    retained ``raw_output`` when the actual input-manifest identity matches
    ``input_fixture_sha256``; both hashes are recorded so a future real-model
    boundary can prove precisely which fixed input produced which fixed output.
    """

    capability: str
    raw_output: bytes
    output_fixture: InputEvidence
    input_fixture_sha256: str

    def as_json(self) -> dict[str, object]:
        return {
            "capability": self.capability,
            "input_fixture_sha256": self.input_fixture_sha256,
            "output_fixture": self.output_fixture.as_json(),
        }


def load_controlled_asr_fixture(
    adapter_document: Mapping[str, object], project_root: Path
) -> ControlledAsrFixture | None:
    """Load the Controlled offline ASR adapter's bound synthetic output fixture.

    A ``fixture`` block is optional: without it, no controlled fixture generates
    and the caller proceeds unchanged. With it, the block must name a valid ASR
    capability, the project-relative non-escaping output-fixture path and its hash,
    and the input-manifest hash it was authored for. The fixture bytes are
    hash-verified and returned verbatim; the caller compares ``input_fixture_sha256``
    to the actual input manifest to prove the fixture matches its revalidated inputs.
    """

    fixture = adapter_document.get("fixture")
    if fixture is None:
        return None
    if not isinstance(fixture, Mapping):
        raise TranscriptionContractError(
            "controlled_asr_fixture_invalid", "Controlled ASR adapter fixture block is malformed."
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
        raise TranscriptionContractError(
            "controlled_asr_fixture_invalid",
            "Controlled ASR adapter fixture block omits a capability, fixture path, or hash.",
        )
    if capability not in ASR_CAPABILITIES:
        raise TranscriptionContractError(
            "controlled_asr_fixture_invalid",
            f"Controlled ASR adapter fixture names an unknown capability {capability!r}.",
        )
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise TranscriptionContractError(
            "controlled_asr_fixture_invalid",
            "Controlled ASR adapter output fixture path must be project-relative.",
        )
    fixture_path = project_root / relative
    try:
        raw_output = fixture_path.read_bytes()
    except OSError as error:
        raise TranscriptionContractError(
            "controlled_asr_fixture_invalid",
            "Controlled ASR adapter output fixture cannot be read.",
        ) from error
    if sha256(raw_output).hexdigest() != expected_output_sha:
        raise TranscriptionContractError(
            "controlled_asr_fixture_invalid",
            "Controlled ASR adapter output fixture hash no longer matches its identity.",
        )
    return ControlledAsrFixture(
        capability=capability,
        raw_output=raw_output,
        output_fixture=input_evidence(fixture_path),
        input_fixture_sha256=bound_input_sha,
    )


# --- Versioned output projection --------------------------------------------


@dataclass(frozen=True)
class ProjectedAsrToken:
    """One projected ASR token with optional confidence in ``[0, 1]``."""

    text: str
    confidence: float | None

    def as_json(self) -> dict[str, object]:
        return {"text": self.text, "confidence": self.confidence}


@dataclass(frozen=True)
class AsrLanguageSpan:
    """A half-open token-index range attributed to one source language.

    Spans reference token indices, so a cue carrying language spans must carry the
    tokens they index; mixed Chinese/English is expressed as adjacent spans and
    never rewritten into one language.
    """

    language: str
    start_token: int
    end_token: int

    def as_json(self) -> dict[str, object]:
        return {
            "language": self.language,
            "start_token": self.start_token,
            "end_token": self.end_token,
        }


@dataclass(frozen=True)
class ProjectedAsrCue:
    """One projected ASR cue: an exact interval, its text, tokens, and language spans."""

    ordinal: int
    interval: HalfOpenInterval
    text: str
    tokens: tuple[ProjectedAsrToken, ...]
    language_spans: tuple[AsrLanguageSpan, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "start": _exact_time_as_json(self.interval.start),
            "end": _exact_time_as_json(self.interval.end),
            "text": self.text,
            "tokens": [token.as_json() for token in self.tokens],
            "language_spans": [span.as_json() for span in self.language_spans],
        }


@dataclass(frozen=True)
class AsrOutputProjection:
    """The versioned interpretation outcome of one raw ASR model output.

    A ``projected`` outcome carries the typed cues; a ``model_output_invalid``
    outcome carries no cues so the raw output is retained only as restricted audit
    evidence by the caller.
    """

    state: str
    capability: str | None
    adapter_version: str | None
    projection_schema_version: str | None
    cues: tuple[ProjectedAsrCue, ...]
    diagnostic: PlanningDiagnostic | None

    def as_json(self) -> dict[str, object]:
        return {
            "state": self.state,
            "capability": self.capability,
            "adapter_version": self.adapter_version,
            "projection_schema_version": self.projection_schema_version,
            "cues": [cue.as_json() for cue in self.cues],
            "diagnostic": self.diagnostic.as_json() if self.diagnostic is not None else None,
        }


class _ProjectionRejected(Exception):
    """Internal signal that an untrusted model output failed a projection rule."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def project_asr_output(
    raw_output: object, contracts: AsrGenerationContracts
) -> AsrOutputProjection:
    """Project a raw ASR model output through the versioned projection schema.

    The whole envelope must match the schema version, the bound projection-schema
    and controlled-adapter identities, and a valid ASR capability, and carry a
    well-formed ``result`` whose cues satisfy the bound projection ruleset: the
    schema-declared required fields, a non-negative ordinal, an exact half-open
    interval, text, and -- for optional tokens and language spans -- the schema's
    token confidence range and consistent token-index spans. Any drift rejects the
    complete output as ``model_output_invalid`` with no defaults, guesses, or
    partial projection.
    """

    try:
        capability, cues = _project_envelope(raw_output, contracts, contracts.projection_ruleset)
    except _ProjectionRejected as rejected:
        return AsrOutputProjection(
            state=_MODEL_OUTPUT_INVALID,
            capability=None,
            adapter_version=None,
            projection_schema_version=None,
            cues=(),
            diagnostic=PlanningDiagnostic(_MODEL_OUTPUT_INVALID, rejected.message),
        )
    return AsrOutputProjection(
        state="projected",
        capability=capability,
        adapter_version=contracts.controlled_adapter.version,
        projection_schema_version=contracts.projection_schema.version,
        cues=cues,
        diagnostic=None,
    )


def _project_envelope(
    raw_output: object,
    contracts: AsrGenerationContracts,
    ruleset: AsrProjectionRuleset,
) -> tuple[str, tuple[ProjectedAsrCue, ...]]:
    if not isinstance(raw_output, Mapping):
        raise _ProjectionRejected("ASR model output is not a JSON object.")
    if raw_output.get("schema_version") != 1:
        raise _ProjectionRejected("ASR model output has an unexpected schema version.")
    if raw_output.get("projection_schema_version") != contracts.projection_schema.version:
        raise _ProjectionRejected(
            "ASR model output does not name the bound projection-schema identity."
        )
    if raw_output.get("adapter_identity") != contracts.controlled_adapter.version:
        raise _ProjectionRejected(
            "ASR model output does not name the bound controlled-adapter identity."
        )
    capability = raw_output.get("capability")
    if capability not in ASR_CAPABILITIES:
        raise _ProjectionRejected("ASR model output names an unknown capability.")
    result = raw_output.get("result")
    if not isinstance(result, Mapping):
        raise _ProjectionRejected("ASR model output result container is missing or malformed.")
    raw_cues = result.get("cues")
    if not isinstance(raw_cues, list):
        raise _ProjectionRejected("ASR model output result field 'cues' is not a list.")
    cues = tuple(_project_cue(raw_cue, ruleset) for raw_cue in raw_cues)
    return str(capability), cues


def _project_cue(raw_cue: object, ruleset: AsrProjectionRuleset) -> ProjectedAsrCue:
    if not isinstance(raw_cue, Mapping):
        raise _ProjectionRejected("An ASR cue is not an object.")
    _require_fields(raw_cue, ruleset.cue_required_fields, "cue")
    ordinal = raw_cue.get("ordinal")
    if not _is_non_negative_int(ordinal):
        raise _ProjectionRejected("An ASR cue omits a valid non-negative ordinal.")
    interval = _project_interval(raw_cue.get("start"), raw_cue.get("end"))
    text = raw_cue.get("text")
    if not isinstance(text, str):
        raise _ProjectionRejected("An ASR cue omits its text.")
    tokens = _project_tokens(raw_cue.get("tokens"), ruleset)
    language_spans = _project_language_spans(
        raw_cue.get("language_spans"), len(tokens), ruleset
    )
    return ProjectedAsrCue(
        ordinal=ordinal,
        interval=interval,
        text=text,
        tokens=tokens,
        language_spans=language_spans,
    )


def _require_fields(
    raw: Mapping[str, object], required_fields: tuple[str, ...], kind: str
) -> None:
    for field in required_fields:
        if field not in raw:
            raise _ProjectionRejected(f"An ASR {kind} is missing the required field {field!r}.")


def _project_interval(raw_start: object, raw_end: object) -> HalfOpenInterval:
    start = _project_exact_time(raw_start)
    end = _project_exact_time(raw_end)
    try:
        return HalfOpenInterval(start, end)
    except TimeValidationError as error:
        raise _ProjectionRejected(
            "An ASR cue interval is not a positive half-open interval."
        ) from error


def _project_exact_time(value: object) -> ExactTime:
    if not isinstance(value, Mapping):
        raise _ProjectionRejected("An ASR cue time is not an object.")
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if not _is_int(numerator) or not _is_int(denominator):
        raise _ProjectionRejected("An ASR cue time omits an integer numerator or denominator.")
    if denominator <= 0:
        raise _ProjectionRejected("An ASR cue time denominator must be positive.")
    return ExactTime(numerator, denominator)


def _project_tokens(value: object, ruleset: AsrProjectionRuleset) -> tuple[ProjectedAsrToken, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _ProjectionRejected("An ASR cue token list is not a list.")
    return tuple(_project_token(raw_token, ruleset) for raw_token in value)


def _project_token(raw_token: object, ruleset: AsrProjectionRuleset) -> ProjectedAsrToken:
    if not isinstance(raw_token, Mapping):
        raise _ProjectionRejected("An ASR token is not an object.")
    _require_fields(raw_token, ruleset.token_required_fields, "token")
    text = raw_token.get("text")
    if not isinstance(text, str):
        raise _ProjectionRejected("An ASR token omits its text.")
    confidence = raw_token.get("confidence")
    if confidence is None:
        return ProjectedAsrToken(text=text, confidence=None)
    low, high = ruleset.token_confidence_range
    if not _is_real(confidence) or not (low <= float(confidence) <= high):
        raise _ProjectionRejected(
            f"An ASR token confidence is outside the schema range [{low}, {high}]."
        )
    return ProjectedAsrToken(text=text, confidence=float(confidence))


def _project_language_spans(
    value: object, token_count: int, ruleset: AsrProjectionRuleset
) -> tuple[AsrLanguageSpan, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _ProjectionRejected("An ASR cue language-span list is not a list.")
    return tuple(_project_language_span(raw_span, token_count, ruleset) for raw_span in value)


def _project_language_span(
    raw_span: object, token_count: int, ruleset: AsrProjectionRuleset
) -> AsrLanguageSpan:
    if not isinstance(raw_span, Mapping):
        raise _ProjectionRejected("An ASR language span is not an object.")
    _require_fields(raw_span, ruleset.language_span_required_fields, "language span")
    language = raw_span.get("language")
    start = raw_span.get("start_token")
    end = raw_span.get("end_token")
    if not isinstance(language, str) or not language:
        raise _ProjectionRejected("An ASR language span omits its language.")
    if not _is_non_negative_int(start) or not _is_non_negative_int(end):
        raise _ProjectionRejected("An ASR language span omits a valid token index.")
    if start >= end:
        raise _ProjectionRejected("An ASR language span must cover a positive token range.")
    if end > token_count:
        raise _ProjectionRejected("An ASR language span indexes beyond the cue's tokens.")
    return AsrLanguageSpan(language=language, start_token=start, end_token=end)


# --- Restricted raw-output retention ----------------------------------------


@dataclass(frozen=True)
class RestrictedRawOutput:
    """Retained raw model output as restricted, audit-only local evidence.

    Raw output is diagnostic evidence for inspecting failures; it is marked
    restricted and audit-only and kept apart from the formal report tree so it can
    never leak into formal artifacts.
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


def retain_restricted_raw_output(
    raw_output: bytes,
    workspace_path: Path,
    *,
    capability: str,
    label: str,
) -> RestrictedRawOutput:
    """Write raw ASR output once into the workspace's restricted audit tree.

    The bytes land under ``restricted/asr/<capability>/<label>-raw-native-output.json``,
    apart from the formal report, and are written immutably: a differing rewrite is
    a conflict. The returned record is marked restricted and audit-only so callers
    keep it out of formal reports.
    """

    raw_path = (
        workspace_path
        / "restricted"
        / "asr"
        / capability
        / f"{label}-raw-native-output.json"
    )
    write_bytes_once(
        raw_path,
        raw_output,
        conflict_error=lambda message: TranscriptionContractError(
            "transcription_raw_output_conflict", message
        ),
    )
    return RestrictedRawOutput(capability=capability, evidence=input_evidence(raw_path))


# --- Contract-artifact helpers ----------------------------------------------


def _read_json_mapping(
    path: Path, *, invalid_reason: str, read_message: str
) -> Mapping[str, object]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TranscriptionContractError(invalid_reason, read_message) from error
    if not isinstance(decoded, Mapping):
        raise TranscriptionContractError(invalid_reason, f"{path.name} is not a JSON object.")
    return decoded


def _load_rules(project_root: Path) -> Mapping[str, object]:
    decoded = _read_json_mapping(
        project_root.joinpath(*_RULES_RELATIVE_PATH),
        invalid_reason="transcription_rules_invalid",
        read_message="Transcription rules cannot be read.",
    )
    if decoded.get("schema_version") != 1:
        raise TranscriptionContractError(
            "transcription_rules_invalid", "Transcription rules have an invalid schema."
        )
    return decoded


def _rule_version(rules: Mapping[str, object], field: str, invalid_reason: str) -> str:
    value = rules.get(field)
    if not isinstance(value, str) or not value:
        raise TranscriptionContractError(
            invalid_reason, f"Transcription rules omit a valid {field}."
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
        raise TranscriptionContractError(
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
    "ASR_CAPABILITIES",
    "AsrGenerationContracts",
    "AsrLanguageSpan",
    "AsrProjectionRuleset",
    "AsrOutputProjection",
    "ControlledAsrFixture",
    "ProjectedAsrCue",
    "ProjectedAsrToken",
    "RestrictedRawOutput",
    "TranscriptionContractError",
    "VersionedContractArtifact",
    "asr_input_manifest_document",
    "asr_input_manifest_sha256",
    "load_controlled_asr_fixture",
    "project_asr_output",
    "retain_restricted_raw_output",
    "revalidate_asr_contracts",
]
