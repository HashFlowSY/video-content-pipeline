"""Phase 6's evidence-bound structured-content validation (ticket 05).

Ticket 04 turned model-proposed cue-pair boundaries into formal SemanticSegments
with exactly-once PresentationCue ownership. This module validates the *content*
a text model proposes for one adjudicated segment — its title, detailed content,
structured details, question-and-answer pairs, people and roles, source
contradictions, and unresolved questions — against that segment's NormalizedCue
citation basis.

The validation is deterministic and independent per item, following the Phase 6
Structured Content Contract:

* every formal factual item and the semantic-segment title must cite one or more
  in-segment NormalizedCue identities; a missing, malformed, or out-of-segment
  citation removes only that one item as an ``unsupported_generated_claim``
  diagnostic while independently verified items continue, so a valid projection
  may yield a partial verified set;
* Q&A fields exist only where both the cited question and the cited answer
  establish the relationship; a person or role requires cited naming rather than
  an anonymous speaker label; a contradiction keeps every incompatible cited
  claim separately attributed and never lets the pipeline choose which is true;
  and an unresolved question must be cited yet lack an answer within the segment;
  and
* one rejected item never fails the whole segment — a whole invalid or
  schema-invalid *projection* is rejected earlier as ``model_output_invalid``
  (see ``text_contracts``), whereas individual items are pruned to diagnostics
  here.

The identities here are NormalizedCue identities: exactly-once ownership of
PresentationCues is decided in ``text_segmentation``; the caller derives one
segment's allowed NormalizedCue citation basis from the cues that segment owns.
See ``docs/PHASE_06_SPECIFICATION.md`` and the Text Analysis Context.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from video_content_pipeline.planning import PlanningDiagnostic

# The single spec-named reason for a pruned content item. The diagnostic message
# carries the specific cause (missing citation, out-of-segment cue, anonymous
# speaker label, resolved contradiction, answered "unresolved" question, ...) so
# the retained evidence for a removal decision is never lost.
UNSUPPORTED_GENERATED_CLAIM = "unsupported_generated_claim"

_MINIMUM_CITATIONS_PER_FACT = 1

# Projected structured-detail field name -> the verified claim kind it becomes.
# The spec lists these as separate arrays; validating them uniformly keeps the
# rules identical across kinds while preserving each item's kind in the report.
_DETAIL_FIELDS: tuple[tuple[str, str], ...] = (
    ("detailed_content", "detail"),
    ("numeric_values", "numeric_value"),
    ("entities", "entity"),
    ("examples", "example"),
    ("conditions", "condition"),
    ("caveats", "caveat"),
)

# Identity sources that are anonymous speaker labels rather than cited naming.
_ANONYMOUS_IDENTITY_SOURCES = frozenset({"speaker_label", "diarization"})
_ANONYMOUS_LABEL_MESSAGE = "an anonymous speaker label does not establish identity"
_NOT_AN_OBJECT = "item is not an object"


@dataclass(frozen=True)
class SegmentCitationBasis:
    """The NormalizedCue identities one adjudicated segment's items may cite.

    ``normalized_cue_ids`` is the segment-owned, Part-local citation basis derived
    from the PresentationCues the segment owns. A citation to any identity outside
    this set is out-of-segment evidence and rejects the citing item, keeping every
    formal fact evidence-bound to its own segment and Part.
    """

    part_id: str
    segment_ordinal: int
    normalized_cue_ids: frozenset[str]


@dataclass(frozen=True)
class VerifiedClaim:
    """One verified cited claim: text bound to at least one in-segment cue."""

    kind: str
    text: str
    cue_ids: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {"kind": self.kind, "text": self.text, "cue_ids": list(self.cue_ids)}


@dataclass(frozen=True)
class VerifiedQuestionAnswer:
    """A verified Q&A pair whose question and answer are both cue-cited."""

    question: VerifiedClaim
    answer: VerifiedClaim

    def as_json(self) -> dict[str, object]:
        return {"question": self.question.as_json(), "answer": self.answer.as_json()}


@dataclass(frozen=True)
class VerifiedPerson:
    """A person or role established by cited naming, not a speaker label."""

    reference: str
    role: str | None
    cue_ids: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {"reference": self.reference, "role": self.role, "cue_ids": list(self.cue_ids)}


@dataclass(frozen=True)
class VerifiedContradiction:
    """Two or more incompatible cited claims kept separately attributed."""

    sides: tuple[VerifiedClaim, ...]

    def as_json(self) -> dict[str, object]:
        return {"sides": [side.as_json() for side in self.sides]}


@dataclass(frozen=True)
class VerifiedUnresolvedQuestion:
    """A cited question with no answer in the segment's validated evidence."""

    text: str
    cue_ids: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {"text": self.text, "cue_ids": list(self.cue_ids)}


@dataclass(frozen=True)
class VerifiedSegmentContent:
    """The independently validated content of one adjudicated segment.

    Every field holds only items whose cue citations were verified; each pruned
    item is retained in ``diagnostics`` with its cause. ``title`` is ``None`` when
    no cue-supported title survived, which lowers only the segment's own
    completeness rather than failing the attempt.
    """

    part_id: str
    segment_ordinal: int
    title: VerifiedClaim | None
    details: tuple[VerifiedClaim, ...]
    questions_and_answers: tuple[VerifiedQuestionAnswer, ...]
    people: tuple[VerifiedPerson, ...]
    contradictions: tuple[VerifiedContradiction, ...]
    unresolved_questions: tuple[VerifiedUnresolvedQuestion, ...]
    diagnostics: tuple[PlanningDiagnostic, ...]

    @property
    def title_verified(self) -> bool:
        return self.title is not None

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "segment_ordinal": self.segment_ordinal,
            "title": self.title.as_json() if self.title is not None else None,
            "details": [detail.as_json() for detail in self.details],
            "questions_and_answers": [pair.as_json() for pair in self.questions_and_answers],
            "people": [person.as_json() for person in self.people],
            "contradictions": [item.as_json() for item in self.contradictions],
            "unresolved_questions": [item.as_json() for item in self.unresolved_questions],
            "diagnostics": [diagnostic.as_json() for diagnostic in self.diagnostics],
        }


def validate_segment_content(
    raw_segment: object, basis: SegmentCitationBasis
) -> VerifiedSegmentContent:
    """Validate one adjudicated segment's projected content against its cue basis.

    Each item is validated independently: a verified item cites at least one
    in-segment NormalizedCue; any other item is pruned to an
    ``unsupported_generated_claim`` diagnostic. The segment itself never fails on
    a rejected item, so the result may hold a partial verified set alongside the
    evidence for every removal.
    """

    document = raw_segment if isinstance(raw_segment, Mapping) else {}
    diagnostics: list[PlanningDiagnostic] = []

    title = _validate_title(document, basis, diagnostics)
    details = _validate_details(document, basis, diagnostics)
    questions_and_answers = _validate_questions_and_answers(document, basis, diagnostics)
    people = _validate_people(document, basis, diagnostics)
    contradictions = _validate_contradictions(document, basis, diagnostics)
    unresolved_questions = _validate_unresolved_questions(
        document, basis, questions_and_answers, diagnostics
    )

    return VerifiedSegmentContent(
        part_id=basis.part_id,
        segment_ordinal=basis.segment_ordinal,
        title=title,
        details=details,
        questions_and_answers=questions_and_answers,
        people=people,
        contradictions=contradictions,
        unresolved_questions=unresolved_questions,
        diagnostics=tuple(diagnostics),
    )


def _validate_title(
    document: Mapping[str, object],
    basis: SegmentCitationBasis,
    diagnostics: list[PlanningDiagnostic],
) -> VerifiedClaim | None:
    """Validate the mandatory cue-supported segment title, if any survives."""

    claim, failure = _validate_claim(document.get("title"), basis, "title")
    if failure is not None:
        diagnostics.append(_unsupported("title", 0, failure))
        return None
    return claim


def _validate_details(
    document: Mapping[str, object],
    basis: SegmentCitationBasis,
    diagnostics: list[PlanningDiagnostic],
) -> tuple[VerifiedClaim, ...]:
    """Validate detailed content and every structured-detail kind uniformly."""

    verified: list[VerifiedClaim] = []
    for field, kind in _DETAIL_FIELDS:
        for index, raw_item in enumerate(_as_list(document.get(field))):
            claim, failure = _validate_claim(raw_item, basis, kind)
            if failure is not None:
                diagnostics.append(_unsupported(kind, index, failure))
                continue
            assert claim is not None
            verified.append(claim)
    return tuple(verified)


def _validate_questions_and_answers(
    document: Mapping[str, object],
    basis: SegmentCitationBasis,
    diagnostics: list[PlanningDiagnostic],
) -> tuple[VerifiedQuestionAnswer, ...]:
    """Keep a Q&A pair only when both its question and answer are cue-cited."""

    verified: list[VerifiedQuestionAnswer] = []
    for index, raw_pair in _enumerate_objects(
        document.get("questions_and_answers"), "questions_and_answers", diagnostics
    ):
        question, question_failure = _validate_claim(raw_pair.get("question"), basis, "question")
        answer, answer_failure = _validate_claim(raw_pair.get("answer"), basis, "answer")
        failure = _first_failure(("question", question_failure), ("answer", answer_failure))
        if failure is not None:
            diagnostics.append(_unsupported("questions_and_answers", index, failure))
            continue
        assert question is not None and answer is not None
        verified.append(VerifiedQuestionAnswer(question=question, answer=answer))
    return tuple(verified)


def _validate_people(
    document: Mapping[str, object],
    basis: SegmentCitationBasis,
    diagnostics: list[PlanningDiagnostic],
) -> tuple[VerifiedPerson, ...]:
    """Establish a person or role only from cited naming, never a speaker label."""

    verified: list[VerifiedPerson] = []
    for index, raw_person in _enumerate_objects(document.get("people"), "people", diagnostics):
        reference = raw_person.get("reference")
        if not isinstance(reference, str) or not reference.strip():
            diagnostics.append(_unsupported("people", index, "person names no cited reference"))
            continue
        if raw_person.get("identity_source") in _ANONYMOUS_IDENTITY_SOURCES:
            diagnostics.append(_unsupported("people", index, _ANONYMOUS_LABEL_MESSAGE))
            continue
        cue_ids, failure = _validate_citation(raw_person.get("cue_ids"), basis)
        if failure is not None:
            message = failure
            if raw_person.get("speaker_label") is not None:
                message = _ANONYMOUS_LABEL_MESSAGE
            diagnostics.append(_unsupported("people", index, message))
            continue
        assert cue_ids is not None
        raw_role = raw_person.get("role")
        role = raw_role if isinstance(raw_role, str) else None
        verified.append(VerifiedPerson(reference=reference, role=role, cue_ids=cue_ids))
    return tuple(verified)


def _validate_contradictions(
    document: Mapping[str, object],
    basis: SegmentCitationBasis,
    diagnostics: list[PlanningDiagnostic],
) -> tuple[VerifiedContradiction, ...]:
    """Keep every incompatible cited claim separate; never choose which is true."""

    verified: list[VerifiedContradiction] = []
    for index, raw_item in _enumerate_objects(
        document.get("contradictions"), "contradictions", diagnostics
    ):
        if raw_item.get("resolved_to") is not None:
            diagnostics.append(
                _unsupported(
                    "contradictions",
                    index,
                    "the pipeline never chooses which contradictory claim is true",
                )
            )
            continue
        raw_sides = _as_list(raw_item.get("sides"))
        if len(raw_sides) < 2:
            diagnostics.append(
                _unsupported(
                    "contradictions", index, "a contradiction needs at least two cited sides"
                )
            )
            continue
        sides, failure = _validate_sides(raw_sides, basis)
        if failure is not None:
            diagnostics.append(_unsupported("contradictions", index, failure))
            continue
        verified.append(VerifiedContradiction(sides=sides))
    return tuple(verified)


def _validate_sides(
    raw_sides: list[object], basis: SegmentCitationBasis
) -> tuple[tuple[VerifiedClaim, ...], str | None]:
    """Validate every side of a contradiction; reject the whole item on any miss."""

    sides: list[VerifiedClaim] = []
    for position, raw_side in enumerate(raw_sides):
        claim, failure = _validate_claim(raw_side, basis, "contradiction_side")
        if failure is not None:
            return (), f"side {position} {failure}"
        assert claim is not None
        sides.append(claim)
    return tuple(sides), None


def _validate_unresolved_questions(
    document: Mapping[str, object],
    basis: SegmentCitationBasis,
    questions_and_answers: tuple[VerifiedQuestionAnswer, ...],
    diagnostics: list[PlanningDiagnostic],
) -> tuple[VerifiedUnresolvedQuestion, ...]:
    """Keep a cited question only when nothing answers it within the segment."""

    answered_texts = {pair.question.text for pair in questions_and_answers}
    verified: list[VerifiedUnresolvedQuestion] = []
    for index, raw_item in enumerate(_as_list(document.get("unresolved_questions"))):
        claim, failure = _validate_claim(raw_item, basis, "unresolved_question")
        if failure is not None:
            diagnostics.append(_unsupported("unresolved_questions", index, failure))
            continue
        assert claim is not None
        if isinstance(raw_item, Mapping) and raw_item.get("answer") is not None:
            diagnostics.append(
                _unsupported(
                    "unresolved_questions", index, "an unresolved question carries an answer"
                )
            )
            continue
        if claim.text in answered_texts:
            diagnostics.append(
                _unsupported(
                    "unresolved_questions",
                    index,
                    "the question is answered elsewhere in the segment",
                )
            )
            continue
        verified.append(VerifiedUnresolvedQuestion(text=claim.text, cue_ids=claim.cue_ids))
    return tuple(verified)


def _validate_claim(
    raw_claim: object, basis: SegmentCitationBasis, kind: str
) -> tuple[VerifiedClaim | None, str | None]:
    """Validate one text-and-citation claim, returning it or a failure message."""

    if not isinstance(raw_claim, Mapping):
        return None, "claim is missing or not an object"
    text = raw_claim.get("text")
    if not isinstance(text, str) or not text.strip():
        return None, "claim has no text"
    cue_ids, failure = _validate_citation(raw_claim.get("cue_ids"), basis)
    if failure is not None:
        return None, failure
    assert cue_ids is not None
    return VerifiedClaim(kind=kind, text=text, cue_ids=cue_ids), None


def _validate_citation(
    value: object, basis: SegmentCitationBasis
) -> tuple[tuple[str, ...] | None, str | None]:
    """Validate a NormalizedCue citation list against the segment's basis."""

    if not isinstance(value, list) or not all(
        isinstance(cue_id, str) and cue_id for cue_id in value
    ):
        return None, "citation is missing or malformed"
    cue_ids = tuple(value)
    if len(cue_ids) < _MINIMUM_CITATIONS_PER_FACT:
        return None, "cites fewer than one NormalizedCue"
    unknown = sorted({cue_id for cue_id in cue_ids if cue_id not in basis.normalized_cue_ids})
    if unknown:
        return None, "cites cue(s) not owned by this segment: " + ", ".join(unknown)
    return cue_ids, None


def _enumerate_objects(
    value: object, field: str, diagnostics: list[PlanningDiagnostic]
) -> Iterator[tuple[int, Mapping[str, object]]]:
    """Yield each list item that is an object, pruning non-objects to diagnostics.

    The three structured list fields whose items must themselves be objects
    (Q&A pairs, people, contradictions) share this guard so a non-object item is
    retained as one ``unsupported_generated_claim`` in exactly one place.
    """

    for index, item in enumerate(_as_list(value)):
        if not isinstance(item, Mapping):
            diagnostics.append(_unsupported(field, index, _NOT_AN_OBJECT))
            continue
        yield index, item


def _first_failure(*labeled: tuple[str, str | None]) -> str | None:
    for label, failure in labeled:
        if failure is not None:
            return f"{label} {failure}"
    return None


def _unsupported(field: str, index: int, message: str) -> PlanningDiagnostic:
    return PlanningDiagnostic(
        UNSUPPORTED_GENERATED_CLAIM,
        f"{field}[{index}] is an unsupported generated claim: {message}.",
    )


def _as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []
