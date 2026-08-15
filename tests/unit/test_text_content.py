"""Unit coverage for Phase 6 ticket 05 evidence-bound content validation.

Ticket 05 independently validates every projected structured item inside one
adjudicated SemanticSegment against that segment's NormalizedCue citation basis.
A verified item cites at least one in-segment NormalizedCue; a missing, invalid,
or out-of-segment citation removes only that one item as an
``unsupported_generated_claim`` diagnostic while independently verified items
continue. Q&A requires both sides cited, people require cited naming rather than
an anonymous speaker label, contradictions stay separately attributed without the
pipeline choosing truth, and an unresolved question must be cited yet unanswered.
The whole segment never fails on one rejected item.
"""

from __future__ import annotations

from video_content_pipeline import text_content as tc


def _basis(
    *,
    cues: tuple[str, ...] = ("n0", "n1", "n2", "n3"),
    part_id: str = "part-a",
    ordinal: int = 0,
) -> tc.SegmentCitationBasis:
    return tc.SegmentCitationBasis(
        part_id=part_id,
        segment_ordinal=ordinal,
        normalized_cue_ids=frozenset(cues),
    )


def _claim(text: str, *cue_ids: str) -> dict[str, object]:
    return {"text": text, "cue_ids": list(cue_ids)}


def _reasons(content: tc.VerifiedSegmentContent) -> set[str]:
    return {diagnostic.reason for diagnostic in content.diagnostics}


def test_fully_supported_segment_verifies_every_item_without_diagnostics() -> None:
    raw = {
        "title": _claim("导论 Introduction", "n0"),
        "detailed_content": [_claim("讲解了缓存策略", "n0", "n1")],
        "numeric_values": [_claim("命中率 95%", "n1")],
        "entities": [_claim("Redis", "n2")],
        "questions_and_answers": [
            {"question": _claim("如何失效?", "n1"), "answer": _claim("按 TTL 过期", "n2")}
        ],
        "people": [{"reference": "讲师 Lin", "role": "presenter", "cue_ids": ["n0"]}],
        "contradictions": [{"sides": [_claim("延迟下降", "n2"), _claim("延迟上升", "n3")]}],
        "unresolved_questions": [_claim("能否横向扩展?", "n3")],
    }

    content = tc.validate_segment_content(raw, _basis())

    assert content.diagnostics == ()
    assert content.title_verified is True
    assert content.title is not None and content.title.text == "导论 Introduction"
    assert [detail.kind for detail in content.details] == [
        "detail",
        "numeric_value",
        "entity",
    ]
    assert len(content.questions_and_answers) == 1
    assert len(content.people) == 1
    assert len(content.contradictions) == 1
    assert len(content.unresolved_questions) == 1


def test_missing_title_is_recorded_as_unsupported_without_failing_segment() -> None:
    content = tc.validate_segment_content({}, _basis())

    assert content.title is None
    assert content.title_verified is False
    assert _reasons(content) == {tc.UNSUPPORTED_GENERATED_CLAIM}


def test_title_citing_an_out_of_segment_cue_is_rejected() -> None:
    raw = {"title": _claim("标题", "n9")}

    content = tc.validate_segment_content(raw, _basis())

    assert content.title is None
    assert content.title_verified is False
    assert any("n9" in diagnostic.message for diagnostic in content.diagnostics)


def test_partial_details_keep_supported_items_and_drop_unsupported() -> None:
    raw = {
        "title": _claim("t", "n0"),
        "detailed_content": [
            _claim("有据可查", "n0"),
            _claim("凭空捏造", "n9"),
            {"text": "缺少引用", "cue_ids": []},
        ],
    }

    content = tc.validate_segment_content(raw, _basis())

    assert [detail.text for detail in content.details] == ["有据可查"]
    # Two dropped details are retained as diagnostics; a valid partial set stays.
    assert sum(1 for _ in content.diagnostics) == 2
    assert _reasons(content) == {tc.UNSUPPORTED_GENERATED_CLAIM}


def test_every_structured_detail_kind_is_validated_independently() -> None:
    raw = {
        "title": _claim("t", "n0"),
        "numeric_values": [_claim("3 项", "n1"), _claim("坏值", "n9")],
        "entities": [_claim("Kafka", "n1")],
        "examples": [_claim("示例", "n2")],
        "conditions": [_claim("当离线时", "n2")],
        "caveats": [_claim("注意边界", "n3")],
    }

    content = tc.validate_segment_content(raw, _basis())

    kinds = [detail.kind for detail in content.details]
    assert kinds == ["numeric_value", "entity", "example", "condition", "caveat"]
    assert _reasons(content) == {tc.UNSUPPORTED_GENERATED_CLAIM}


def test_question_and_answer_requires_both_sides_cited() -> None:
    raw = {
        "title": _claim("t", "n0"),
        "questions_and_answers": [
            {"question": _claim("有据的问题?", "n1"), "answer": _claim("有据的回答", "n2")},
            {"question": _claim("无据的问题?", "n1"), "answer": {"text": "无引用", "cue_ids": []}},
            {"question": {"text": "问题缺引用?", "cue_ids": []}, "answer": _claim("回答", "n2")},
        ],
    }

    content = tc.validate_segment_content(raw, _basis())

    assert len(content.questions_and_answers) == 1
    assert content.questions_and_answers[0].question.text == "有据的问题?"
    assert sum(1 for _ in content.diagnostics) == 2


def test_person_requires_cited_naming_not_an_anonymous_speaker_label() -> None:
    raw = {
        "title": _claim("t", "n0"),
        "people": [
            {"reference": "命名的人 Wang", "role": "host", "cue_ids": ["n0"]},
            {"reference": "Speaker 2", "speaker_label": "spk-2", "cue_ids": []},
            {"reference": "推断的人", "identity_source": "speaker_label", "cue_ids": ["n1"]},
        ],
    }

    content = tc.validate_segment_content(raw, _basis())

    assert [person.reference for person in content.people] == ["命名的人 Wang"]
    assert content.people[0].role == "host"
    assert sum(1 for _ in content.diagnostics) == 2
    assert any("speaker label" in diagnostic.message.lower() for diagnostic in content.diagnostics)


def test_contradiction_stays_attributed_and_the_pipeline_never_chooses_truth() -> None:
    raw = {
        "title": _claim("t", "n0"),
        "contradictions": [
            {"sides": [_claim("A 说涨", "n1"), _claim("B 说跌", "n2")]},
            {"sides": [_claim("A 说涨", "n1"), _claim("B 说跌", "n2")], "resolved_to": 0},
            {"sides": [_claim("只有一面", "n1")]},
            {"sides": [_claim("有据", "n1"), _claim("无据", "n9")]},
        ],
    }

    content = tc.validate_segment_content(raw, _basis())

    assert len(content.contradictions) == 1
    assert [side.text for side in content.contradictions[0].sides] == ["A 说涨", "B 说跌"]
    assert sum(1 for _ in content.diagnostics) == 3
    assert any("true" in diagnostic.message.lower() for diagnostic in content.diagnostics)


def test_unresolved_question_must_be_cited_and_unanswered() -> None:
    raw = {
        "title": _claim("t", "n0"),
        "questions_and_answers": [
            {"question": _claim("已解答的问题?", "n1"), "answer": _claim("答案", "n2")}
        ],
        "unresolved_questions": [
            _claim("真正悬而未决?", "n3"),
            {"text": "自带答案?", "cue_ids": ["n3"], "answer": "其实有答案"},
            _claim("已解答的问题?", "n1"),
            {"text": "无引用悬问?", "cue_ids": []},
        ],
    }

    content = tc.validate_segment_content(raw, _basis())

    assert [question.text for question in content.unresolved_questions] == ["真正悬而未决?"]
    assert sum(1 for _ in content.diagnostics) == 3


def test_malformed_items_are_dropped_as_diagnostics_not_crashes() -> None:
    raw = {
        "title": "not-an-object",
        "detailed_content": ["not-an-object", 7, None],
        "questions_and_answers": ["nope"],
        "people": [42],
        "contradictions": ["x"],
        "unresolved_questions": [None],
    }

    content = tc.validate_segment_content(raw, _basis())

    assert content.title is None
    assert content.details == ()
    assert content.questions_and_answers == ()
    assert content.people == ()
    assert content.contradictions == ()
    assert content.unresolved_questions == ()
    assert _reasons(content) == {tc.UNSUPPORTED_GENERATED_CLAIM}


def test_as_json_is_deterministic_and_complete() -> None:
    raw = {
        "title": _claim("标题", "n0"),
        "detailed_content": [_claim("细节", "n1")],
        "questions_and_answers": [{"question": _claim("问?", "n1"), "answer": _claim("答", "n2")}],
        "people": [{"reference": "Wang", "role": None, "cue_ids": ["n0"]}],
        "contradictions": [{"sides": [_claim("涨", "n1"), _claim("跌", "n2")]}],
        "unresolved_questions": [_claim("悬?", "n3")],
    }

    document = tc.validate_segment_content(raw, _basis()).as_json()

    assert document == {
        "part_id": "part-a",
        "segment_ordinal": 0,
        "title": {"kind": "title", "text": "标题", "cue_ids": ["n0"]},
        "details": [{"kind": "detail", "text": "细节", "cue_ids": ["n1"]}],
        "questions_and_answers": [
            {
                "question": {"kind": "question", "text": "问?", "cue_ids": ["n1"]},
                "answer": {"kind": "answer", "text": "答", "cue_ids": ["n2"]},
            }
        ],
        "people": [{"reference": "Wang", "role": None, "cue_ids": ["n0"]}],
        "contradictions": [
            {
                "sides": [
                    {"kind": "contradiction_side", "text": "涨", "cue_ids": ["n1"]},
                    {"kind": "contradiction_side", "text": "跌", "cue_ids": ["n2"]},
                ]
            }
        ],
        "unresolved_questions": [{"text": "悬?", "cue_ids": ["n3"]}],
        "diagnostics": [],
    }


def test_every_removal_reason_is_the_spec_named_unsupported_reason() -> None:
    raw = {
        "detailed_content": [_claim("bad", "n9")],
        "numeric_values": [{"text": "no cue", "cue_ids": []}],
    }

    content = tc.validate_segment_content(raw, _basis())

    assert content.diagnostics != ()
    assert all(
        diagnostic.reason == tc.UNSUPPORTED_GENERATED_CLAIM for diagnostic in content.diagnostics
    )
