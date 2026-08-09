"""Read-only integration proof for the retained Phase 2 fixture corpus."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from video_content_pipeline.coverage import DecodedInterval, StreamCoverage, derive_stream_coverage
from video_content_pipeline.probe import ProbeDocument, ProbeProjection, project_probe_document
from video_content_pipeline.subtitles import (
    parse_srt,
    parse_vtt,
    presentation_output,
    serialize_srt,
    serialize_vtt,
)
from video_content_pipeline.timecode import (
    ExactTime,
    HalfOpenInterval,
    PartCoverageStart,
    PartRelativeTime,
    RawPtsTime,
)
from video_content_pipeline.timeline import CollectionTimeline, TimelinePart

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures"
MANIFEST_PATH = FIXTURE_ROOT / "phase-02-manifest.json"
ARCHIVED_INVALID_MANIFEST_PATH = "evidence/phase-02-manifest-rerun-03-invalid.json"
CANONICAL_FIXTURE_PATHS = frozenset(
    {
        "recipes/phase-02-fixtures-v1.json",
        "media/phase-02-offset-av-aac.mkv",
        "media/phase-02-gap-video.mkv",
        "media/phase-02-aac-priming.m4a",
        "subtitles/phase-02-rolling.srt",
        "subtitles/phase-02-out-of-range.srt",
        "subtitles/phase-02-roundtrip.vtt",
        "evidence/ffmpeg-version.txt",
        "evidence/ffprobe-version.txt",
        "evidence/phase-02-offset-av-aac.ffprobe.json",
        "evidence/phase-02-gap-video.ffprobe.json",
        "evidence/phase-02-aac-priming.ffprobe.json",
    }
)
AUXILIARY_FIXTURE_PATHS = frozenset({"phase-02-manifest.json", ARCHIVED_INVALID_MANIFEST_PATH})


def _manifest() -> dict[str, object]:
    decoded = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(decoded, dict), "Fixture manifest must be a JSON object."
    return decoded


def _verify_fixture_manifest(fixture_root: Path, manifest: Mapping[str, object]) -> None:
    entries = manifest.get("entries")
    assert isinstance(entries, list), "Fixture manifest must contain an entries list."
    assert len(entries) == 12, "Fixture manifest must retain exactly 12 canonical entries."

    paths: set[str] = set()
    retained_entries: list[tuple[str, int, str, Mapping[str, object]]] = []
    for ordinal, entry in enumerate(entries):
        assert isinstance(entry, Mapping), f"Fixture manifest entry {ordinal} must be an object."
        path = entry.get("path")
        byte_count = entry.get("byte_count")
        sha256 = entry.get("sha256")
        assert isinstance(path, str) and path and not Path(path).is_absolute(), (
            f"Fixture manifest entry {ordinal} has an invalid relative path."
        )
        assert path not in paths, f"Fixture manifest repeats artifact path: {path}"
        paths.add(path)
        assert isinstance(byte_count, int) and byte_count >= 0, (
            f"Fixture manifest entry {path} has an invalid byte count."
        )
        assert (
            isinstance(sha256, str)
            and len(sha256) == 64
            and all(character in "0123456789abcdef" for character in sha256)
        ), f"Fixture manifest entry {path} has an invalid SHA-256 digest."
        retention_class = entry.get("retention_class")
        fixture_id = entry.get("fixture_id")
        assert isinstance(retention_class, str) and retention_class, (
            f"Fixture manifest entry {path} has an invalid retention class."
        )
        assert isinstance(fixture_id, str) and fixture_id, (
            f"Fixture manifest entry {path} has an invalid fixture ID."
        )
        retained_entries.append((path, byte_count, sha256, entry))

    missing_paths = CANONICAL_FIXTURE_PATHS - paths
    unexpected_paths = paths - CANONICAL_FIXTURE_PATHS
    assert not missing_paths and not unexpected_paths, (
        "Fixture manifest canonical paths differ: "
        f"missing={sorted(missing_paths)}, unexpected={sorted(unexpected_paths)}."
    )

    retained_content: dict[str, bytes] = {}
    for path, byte_count, sha256, _ in retained_entries:
        artifact = fixture_root / path
        assert artifact.is_file(), f"Fixture artifact is missing: {path}"
        content = artifact.read_bytes()
        assert len(content) == byte_count, (
            f"Fixture artifact byte count mismatch for {path}: "
            f"expected {byte_count}, got {len(content)}."
        )
        actual_sha256 = hashlib.sha256(content).hexdigest()
        assert actual_sha256 == sha256, (
            f"Fixture artifact SHA-256 mismatch for {path}: expected {sha256}, got {actual_sha256}."
        )
        retained_content[path] = content

    retained_files = frozenset(
        path.relative_to(fixture_root).as_posix()
        for path in fixture_root.rglob("*")
        if path.is_file()
    )
    expected_files = CANONICAL_FIXTURE_PATHS | AUXILIARY_FIXTURE_PATHS
    assert retained_files == expected_files, (
        "Fixture directory has unexpected or unaccounted retained files: "
        f"missing={sorted(expected_files - retained_files)}, "
        f"unexpected={sorted(retained_files - expected_files)}."
    )

    ffmpeg_version = retained_content["evidence/ffmpeg-version.txt"].decode("utf-8")
    ffprobe_version = retained_content["evidence/ffprobe-version.txt"].decode("utf-8")
    for path, _, _, entry in retained_entries:
        if path.startswith("media/"):
            _verify_tool_metadata(entry, path, "ffmpeg_version", ffmpeg_version, "ffmpeg")
        if path.startswith("evidence/") and path.endswith(".ffprobe.json"):
            _verify_tool_metadata(entry, path, "ffprobe_version", ffprobe_version, "ffprobe")


def _verify_tool_metadata(
    entry: Mapping[str, object], path: str, version_key: str, expected_version: str, tool: str
) -> None:
    version = entry.get(version_key)
    arguments = entry.get("command_arguments")
    assert version == expected_version, f"Fixture manifest entry {path} has invalid {version_key}."
    assert (
        isinstance(arguments, list)
        and arguments
        and all(isinstance(argument, str) for argument in arguments)
    ), f"Fixture manifest entry {path} has invalid command arguments."
    assert arguments[0].endswith(tool), (
        f"Fixture manifest entry {path} command does not identify {tool}."
    )


@pytest.fixture
def verified_fixture_root() -> Path:
    """Verify every retained artifact before any test interprets fixture evidence."""

    _verify_fixture_manifest(FIXTURE_ROOT, _manifest())
    return FIXTURE_ROOT


def _probe_document(verified_fixture_root: Path, filename: str) -> ProbeDocument:
    return ProbeDocument(
        raw_json=(verified_fixture_root / "evidence" / filename).read_text(encoding="utf-8")
    )


def _stream_time_base(projection: ProbeProjection, stream_index: int) -> ExactTime:
    for stream in projection.streams:
        if stream.index == stream_index:
            return stream.time_base
    raise AssertionError(f"Retained ProbeDocument has no stream {stream_index}.")


def _decoded_intervals_from_frames(
    document: ProbeDocument, projection: ProbeProjection, stream_index: int
) -> tuple[DecodedInterval, ...]:
    decoded = json.loads(document.raw_json)
    assert isinstance(decoded, Mapping), "Retained ProbeDocument must be a JSON object."
    frames = decoded.get("packets_and_frames")
    assert isinstance(frames, list), "Retained ProbeDocument must contain packets and frames."

    time_base = _stream_time_base(projection, stream_index)
    observed: list[HalfOpenInterval] = []
    for item in frames:
        if not isinstance(item, Mapping) or item.get("type") != "frame":
            continue
        if item.get("stream_index") != stream_index:
            continue
        pts = item.get("pts")
        duration = item.get("duration")
        assert isinstance(pts, int) and not isinstance(pts, bool), "Frame PTS must be an integer."
        assert isinstance(duration, int) and duration > 0 and not isinstance(duration, bool), (
            "Frame duration must be a positive integer."
        )
        start = RawPtsTime(pts, time_base).time
        end = RawPtsTime(pts + duration, time_base).time
        observed.append(HalfOpenInterval(start, end))

    assert observed, f"Retained ProbeDocument has no decoded frames for stream {stream_index}."
    intervals: list[DecodedInterval] = []
    current_start = observed[0].start
    current_end = observed[0].end
    for frame in observed[1:]:
        assert current_end <= frame.start, "Retained decoded frame evidence must be ordered."
        if current_end == frame.start:
            current_end = frame.end
            continue
        intervals.append(DecodedInterval(current_start, current_end))
        current_start = frame.start
        current_end = frame.end
    intervals.append(DecodedInterval(current_start, current_end))
    return tuple(intervals)


def _coverage_from_frames(
    document: ProbeDocument, projection: ProbeProjection, stream_index: int
) -> StreamCoverage:
    intervals = _decoded_intervals_from_frames(document, projection, stream_index)
    return derive_stream_coverage(intervals)


def test_fixture_manifest_verification_rejects_missing_and_mismatched_artifacts(
    tmp_path: Path,
) -> None:
    with pytest.raises(AssertionError, match="Fixture artifact is missing"):
        _verify_fixture_manifest(tmp_path, _manifest())

    missing_entry_manifest = copy.deepcopy(_manifest())
    entries = missing_entry_manifest["entries"]
    assert isinstance(entries, list)
    final_entry = entries[-1]
    assert isinstance(final_entry, dict)
    final_entry["path"] = "evidence/replaced-entry.json"
    with pytest.raises(AssertionError, match="Fixture manifest canonical paths differ"):
        _verify_fixture_manifest(FIXTURE_ROOT, missing_entry_manifest)

    invalid_metadata_manifest = copy.deepcopy(_manifest())
    entries = invalid_metadata_manifest["entries"]
    assert isinstance(entries, list)
    first_entry = entries[0]
    assert isinstance(first_entry, dict)
    first_entry["retention_class"] = ""
    with pytest.raises(AssertionError, match="invalid retention class"):
        _verify_fixture_manifest(FIXTURE_ROOT, invalid_metadata_manifest)

    mismatched_manifest = copy.deepcopy(_manifest())
    entries = mismatched_manifest["entries"]
    assert isinstance(entries, list)
    first_entry = entries[0]
    assert isinstance(first_entry, dict)
    first_entry["sha256"] = "0" * 64
    with pytest.raises(AssertionError, match="Fixture artifact SHA-256 mismatch"):
        _verify_fixture_manifest(FIXTURE_ROOT, mismatched_manifest)


def test_retained_probe_documents_project_known_stream_evidence(
    verified_fixture_root: Path,
) -> None:
    offset = _probe_document(verified_fixture_root, "phase-02-offset-av-aac.ffprobe.json")
    gap = _probe_document(verified_fixture_root, "phase-02-gap-video.ffprobe.json")
    priming = _probe_document(verified_fixture_root, "phase-02-aac-priming.ffprobe.json")

    offset_result = project_probe_document(offset)
    gap_result = project_probe_document(gap)
    priming_result = project_probe_document(priming)

    assert offset_result.document.raw_json == offset.raw_json
    assert offset_result.diagnostics == ()
    assert offset_result.projection is not None
    offset_streams = [
        (stream.index, stream.codec_type, stream.time_base)
        for stream in offset_result.projection.streams
    ]
    assert offset_streams == [
        (0, "video", ExactTime(1, 1_000)),
        (1, "audio", ExactTime(1, 1_000)),
    ]
    assert gap_result.projection is not None
    gap_streams = [
        (stream.index, stream.codec_type, stream.time_base)
        for stream in gap_result.projection.streams
    ]
    assert gap_streams == [
        (0, "video", ExactTime(1, 1_000)),
    ]
    assert priming_result.projection is not None
    priming_streams = [
        (stream.index, stream.codec_type, stream.time_base)
        for stream in priming_result.projection.streams
    ]
    assert priming_streams == [
        (0, "audio", ExactTime(1, 48_000)),
    ]
    assert '"pts": -21' in offset.raw_json
    assert '"skip_samples": 1024' in priming.raw_json


def test_retained_frames_prove_coverage_and_exact_coordinate_mapping(
    verified_fixture_root: Path,
) -> None:
    offset = _probe_document(verified_fixture_root, "phase-02-offset-av-aac.ffprobe.json")
    gap = _probe_document(verified_fixture_root, "phase-02-gap-video.ffprobe.json")
    offset_projection = project_probe_document(offset).projection
    gap_projection = project_probe_document(gap).projection
    assert offset_projection is not None
    assert gap_projection is not None

    offset_coverage = _coverage_from_frames(offset, offset_projection, stream_index=0)
    gap_coverage = _coverage_from_frames(gap, gap_projection, stream_index=0)
    assert offset_coverage.coverage == HalfOpenInterval(ExactTime(0), ExactTime(4))
    assert offset_coverage.gaps == ()
    assert gap_coverage.coverage == HalfOpenInterval(ExactTime(10), ExactTime(139, 10))
    assert gap_coverage.gaps == (HalfOpenInterval(ExactTime(11), ExactTime(13)),)

    audio_time_base = _stream_time_base(offset_projection, stream_index=1)
    negative_audio_pts = RawPtsTime(raw_pts=-21, time_base=audio_time_base)
    audio_start = PartCoverageStart(negative_audio_pts)
    assert PartRelativeTime.from_raw(negative_audio_pts, audio_start).time == ExactTime(0)

    timeline = CollectionTimeline(
        parts=(
            TimelinePart(part_id="offset-video", coverage=offset_coverage.coverage),
            TimelinePart(part_id="gap-video", coverage=gap_coverage.coverage),
        )
    )
    gap_time_base = _stream_time_base(gap_projection, stream_index=0)
    gap_start = PartCoverageStart(RawPtsTime(raw_pts=10_000, time_base=gap_time_base))
    gap_part_relative = PartRelativeTime.from_raw(
        RawPtsTime(raw_pts=13_000, time_base=gap_time_base), gap_start
    )
    collection_time = timeline.map_part_relative_time("gap-video", gap_part_relative)

    assert gap_part_relative.time == ExactTime(3)
    assert collection_time.time == ExactTime(7)
    assert collection_time.part_relative_time.raw_pts_time.raw_pts == 13_000
    assert collection_time.part_relative_time.coverage_start.time == ExactTime(10)


def test_retained_srt_and_vtt_fixture_tracks_round_trip_against_coverage(
    verified_fixture_root: Path,
) -> None:
    offset = _probe_document(verified_fixture_root, "phase-02-offset-av-aac.ffprobe.json")
    projection = project_probe_document(offset).projection
    assert projection is not None
    coverage = _coverage_from_frames(offset, projection, stream_index=0)

    rolling_source = (verified_fixture_root / "subtitles" / "phase-02-rolling.srt").read_text(
        encoding="utf-8"
    )
    rolling_track = parse_srt(
        rolling_source, part_id="offset-video", track_id="rolling", coverage=coverage
    )
    assert rolling_track.valid
    assert [cue.text for cue in presentation_output(rolling_track).cues] == [
        "we need",
        " proof",
        "repeat",
        "repeat",
    ]

    out_of_range_source = (
        verified_fixture_root / "subtitles" / "phase-02-out-of-range.srt"
    ).read_text(encoding="utf-8")
    out_of_range_track = parse_srt(
        out_of_range_source, part_id="offset-video", track_id="invalid", coverage=coverage
    )
    assert out_of_range_track.valid is False
    assert out_of_range_track.raw_cues == ()
    assert out_of_range_track.diagnostics[0].reason == "cue_out_of_coverage"

    vtt_source = (verified_fixture_root / "subtitles" / "phase-02-roundtrip.vtt").read_text(
        encoding="utf-8"
    )
    vtt_track = parse_vtt(vtt_source, part_id="offset-video", track_id="vtt", coverage=coverage)
    assert vtt_track.valid
    assert vtt_track.raw_cues[0].identifier == "cue-1"
    assert vtt_track.raw_cues[0].interval == HalfOpenInterval(ExactTime(0), ExactTime(1, 1_000))
    presentation = presentation_output(vtt_track).cues
    srt_round_trip = parse_srt(
        serialize_srt(presentation),
        part_id="offset-video",
        track_id="srt-round-trip",
        coverage=coverage,
    )
    vtt_round_trip = parse_vtt(
        serialize_vtt(presentation),
        part_id="offset-video",
        track_id="vtt-round-trip",
        coverage=coverage,
    )

    assert srt_round_trip.valid
    assert vtt_round_trip.valid
    assert [cue.text for cue in srt_round_trip.raw_cues] == ["Line one\nLine two", "Second cue"]
    assert [cue.text for cue in vtt_round_trip.raw_cues] == ["Line one\nLine two", "Second cue"]
    assert srt_round_trip.raw_cues[0].identifier == "cue-1"
    assert vtt_round_trip.raw_cues[0].identifier == "cue-1"
    assert srt_round_trip.raw_cues[0].interval == HalfOpenInterval(
        ExactTime(0), ExactTime(1, 1_000)
    )
    assert vtt_round_trip.raw_cues[0].interval == HalfOpenInterval(
        ExactTime(0), ExactTime(1, 1_000)
    )
    assert vtt_round_trip.raw_cues[0].timing_settings == "align:start"
