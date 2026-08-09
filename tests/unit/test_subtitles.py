from __future__ import annotations

from dataclasses import FrozenInstanceError

from video_content_pipeline.coverage import DecodedInterval, StreamCoverage, derive_stream_coverage
from video_content_pipeline.subtitles import (
    NormalizedCue,
    PresentationCue,
    RawCue,
    SubtitleTrack,
    SubtitleTrackStatus,
    SubtitleValidationError,
    accept_subtitle_track,
    parse_srt,
    parse_vtt,
    presentation_cues,
    serialize_srt,
    serialize_vtt,
)
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval


def _coverage(start: int = 0, end: int = 10) -> StreamCoverage:
    return derive_stream_coverage((DecodedInterval(ExactTime(start), ExactTime(end)),))


def test_valid_srt_retains_immutable_raw_and_lossless_normalized_evidence() -> None:
    source = "1\n00:00:01,125 --> 00:00:02,500\nHello  world\nSecond line\n"

    result = parse_srt(source, part_id="part-a", track_id="captions", coverage=_coverage())

    assert result.status is SubtitleTrackStatus.VALID
    assert result.raw_source == source
    assert result.raw_cues[0] == RawCue(
        source_text="Hello  world\nSecond line",
        interval=HalfOpenInterval(ExactTime(9, 8), ExactTime(5, 2)),
        source_ordinal=0,
        part_id="part-a",
        track_id="captions",
        source_format="srt",
        identifier="1",
        raw_start="00:00:01,125",
        raw_end="00:00:02,500",
    )
    normalized = result.normalized_cues[0]
    assert isinstance(normalized, NormalizedCue)
    assert normalized.text == "Hello  world\nSecond line"
    assert normalized.tokens == ("Hello", "  ", "world", "\n", "Second", " ", "line")
    assert normalized.raw_cue is result.raw_cues[0]
    try:
        result.raw_cues[0].source_text = "changed"  # type: ignore[misc]
    except FrozenInstanceError:
        pass
    else:  # pragma: no cover - protects the immutability contract
        raise AssertionError("RawCue must be immutable")


def test_valid_vtt_preserves_identifiers_settings_and_source_order() -> None:
    source = (
        "WEBVTT\n\n"
        "STYLE\n00:00:00.000 --> 00:00:01.000\nA cue\n\n"
        "NOTE\nnot a cue --> text\n\n"
        "second\n00:00:01.000 --> 00:00:02.000 align:start\nB cue\n"
    )

    result = parse_vtt(source, part_id="part-v", track_id="vtt", coverage=_coverage())

    assert result.status is SubtitleTrackStatus.VALID
    assert [cue.identifier for cue in result.raw_cues] == ["STYLE", "second"]
    assert result.raw_cues[1].timing_settings == "align:start"
    assert [cue.source_ordinal for cue in result.raw_cues] == [0, 1]


def test_invalid_track_is_atomic_and_retains_no_partial_cues() -> None:
    source = "1\n00:00:00,000 --> 00:00:01,000\nvalid\n\n2\nnot-a-timing-line\npartial\n"

    result = parse_srt(source, part_id="part-a", track_id="captions", coverage=_coverage())

    assert result.status is SubtitleTrackStatus.INVALID
    assert result.raw_source == source
    assert result.raw_cues == ()
    assert result.normalized_cues == ()
    assert result.diagnostics[0].reason == "syntax_invalid"
    assert result.diagnostics[0].source_ordinal == 1


def test_invalid_boundaries_and_duration_are_rejected_atomically() -> None:
    for source in (
        "1\n00:00:02,000 --> 00:00:01,000\nreverse\n",
        "1\n00:00:01,000 --> 00:00:01,000\nempty\n",
        "1\n00:00:00,000 --> 00:00:11,000\noutside\n",
    ):
        result = parse_srt(source, part_id="part-a", track_id="captions", coverage=_coverage())
        assert result.status is SubtitleTrackStatus.INVALID
        assert result.raw_cues == ()
        assert result.normalized_cues == ()
        assert result.diagnostics


def test_indeterminate_coverage_rejects_a_track_without_guessing_duration() -> None:
    coverage = derive_stream_coverage((DecodedInterval(ExactTime(0), None),))
    result = parse_srt(
        "1\n00:00:00,000 --> 00:00:01,000\ntext\n",
        part_id="part-a",
        track_id="captions",
        coverage=coverage,
    )

    assert result.status is SubtitleTrackStatus.INVALID
    assert result.diagnostics[0].reason == "coverage_indeterminate"
    assert result.raw_cues == ()


def test_syntax_only_parsing_can_be_validated_later() -> None:
    result = parse_srt(
        "1\n00:00:00,000 --> 00:00:01,000\ntext\n",
        part_id="part-a",
        track_id="captions",
    )

    assert result.status is SubtitleTrackStatus.INVALID
    assert result.diagnostics[0].reason == "coverage_indeterminate"


def test_format_specific_timestamp_syntax_and_vtt_header_are_fail_closed() -> None:
    assert parse_srt("WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\ntext\n").valid is False
    assert parse_vtt("WEBVTTX\n\n00:00:00.000 --> 00:00:01.000\ntext\n").valid is False
    assert parse_vtt("WEBVTT\n\n00:00:00,000 --> 00:00:01,000\ntext\n").valid is False


def test_crlf_source_text_is_retained_while_normalized_tokens_use_lf() -> None:
    result = parse_srt(
        "1\r\n00:00:00,000 --> 00:00:01,000\r\nfirst\r\nsecond\r\n",
        part_id="part-a",
        track_id="captions",
        coverage=_coverage(),
    )

    assert result.valid
    assert result.raw_cues[0].source_text == "first\r\nsecond"
    assert result.normalized_cues[0].text == "first\nsecond"
    assert result.normalized_cues[0].tokens == ("first", "\n", "second")


def test_crlf_vtt_directives_are_skipped_without_becoming_cues() -> None:
    result = parse_vtt(
        "WEBVTT\r\n\r\nNOTE\r\nnot a cue --> text\r\n\r\n"
        "id\r\n00:00:00.000 --> 00:00:01.000\r\ntext\r\n",
        part_id="part-a",
        track_id="captions",
        coverage=_coverage(),
    )

    assert result.valid
    assert [cue.identifier for cue in result.raw_cues] == ["id"]


def test_accept_subtitle_track_requires_coverage_at_the_atomic_boundary() -> None:
    result = accept_subtitle_track(
        "1\n00:00:00,000 --> 00:00:01,000\ntext\n",
        "srt",
        part_id="part-a",
        track_id="captions",
        coverage=_coverage(),
    )

    assert result.valid
    assert result.raw_cues[0].part_id == "part-a"
    assert result.raw_cues[0].track_id == "captions"


def test_presentation_cues_are_immutable_and_preserve_stable_overlapping_order() -> None:
    track = parse_srt(
        "1\n00:00:02,000 --> 00:00:03,000\nlater\n\n"
        "2\n00:00:01,000 --> 00:00:02,500\nfirst overlap\n\n"
        "3\n00:00:01,000 --> 00:00:01,500\nfirst short\n",
        part_id="part-a",
        track_id="captions",
        coverage=_coverage(),
    )

    cues = presentation_cues(track)

    assert [cue.text for cue in cues] == ["first short", "first overlap", "later"]
    assert [cue.source_ordinal for cue in cues] == [2, 1, 0]
    assert cues[0].interval.overlaps(cues[1].interval)
    assert cues[0].normalized_cue is track.normalized_cues[0]
    assert cues[0].source_token_indexes == (0, 1, 2)
    assert isinstance(cues[0], PresentationCue)
    try:
        cues[0].text = "changed"  # type: ignore[misc]
    except FrozenInstanceError:
        pass
    else:  # pragma: no cover - protects the immutability contract
        raise AssertionError("PresentationCue must be immutable")

    try:
        PresentationCue(cues[0].normalized_cue, (0, 2))
    except SubtitleValidationError:
        pass
    else:  # pragma: no cover - protects source-token provenance
        raise AssertionError("PresentationCue must retain source-token provenance")


def test_presentation_cues_use_source_ordinal_as_the_final_stable_order_key() -> None:
    interval = HalfOpenInterval(ExactTime(1), ExactTime(2))
    later_raw = RawCue("later", interval, 1, "part-a", "captions", "srt")
    first_raw = RawCue("first", interval, 0, "part-a", "captions", "srt")
    later = NormalizedCue(later_raw, "later", ("later",))
    first = NormalizedCue(first_raw, "first", ("first",))
    track = SubtitleTrack(
        track_id="captions",
        part_id="part-a",
        source_format="srt",
        raw_source="",
        status=SubtitleTrackStatus.VALID,
        raw_cues=(later_raw, first_raw),
        normalized_cues=(later, first),
    )

    cues = presentation_cues(track)

    assert [cue.source_ordinal for cue in cues] == [0, 1]


def test_exports_floor_and_ceil_a_positive_submillisecond_presentation_interval() -> None:
    raw = RawCue(
        source_text="short",
        interval=HalfOpenInterval(ExactTime(1, 2_000), ExactTime(3, 4_000)),
        source_ordinal=0,
        part_id="part-a",
        track_id="captions",
        source_format="srt",
    )
    cue = PresentationCue(NormalizedCue(raw, "short", ("short",)), (0,))

    srt = serialize_srt((cue,))
    vtt = serialize_vtt((cue,))

    assert srt == "1\n00:00:00,000 --> 00:00:00,001\nshort\n"
    assert vtt == "WEBVTT\n\n1\n00:00:00.000 --> 00:00:00.001\nshort\n"
    assert parse_srt(srt, part_id="part-a", track_id="export", coverage=_coverage()).valid
    assert parse_vtt(vtt, part_id="part-a", track_id="export", coverage=_coverage()).valid


def test_multi_cue_exports_keep_stable_overlap_order_and_parse_as_separate_cues() -> None:
    track = parse_srt(
        "1\n00:00:02,000 --> 00:00:03,000\nlater\n\n"
        "2\n00:00:01,000 --> 00:00:02,500\nfirst overlap\n\n"
        "3\n00:00:01,000 --> 00:00:01,500\nfirst short\n",
        part_id="part-a",
        track_id="captions",
        coverage=_coverage(),
    )
    cues = presentation_cues(track)

    srt_result = parse_srt(
        serialize_srt(cues), part_id="part-a", track_id="srt-export", coverage=_coverage()
    )
    vtt_result = parse_vtt(
        serialize_vtt(cues), part_id="part-a", track_id="vtt-export", coverage=_coverage()
    )

    assert [cue.text for cue in srt_result.raw_cues] == ["first short", "first overlap", "later"]
    assert [cue.text for cue in vtt_result.raw_cues] == ["first short", "first overlap", "later"]
    assert srt_result.raw_cues[0].interval.overlaps(srt_result.raw_cues[1].interval)
