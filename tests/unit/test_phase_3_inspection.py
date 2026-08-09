from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import video_content_pipeline.inspection as inspection
from video_content_pipeline.external_tools import PinnedExternalTool
from video_content_pipeline.inspection import (
    ProbeCaptureError,
    capture_probe_documents,
    derive_packet_coverages,
    enumerate_subtitle_track_candidates,
)
from video_content_pipeline.probe import ProbeDocument, project_probe_document
from video_content_pipeline.source import SourceArtifact


def test_packet_evidence_derives_exact_coverage_and_subtitle_metadata_only() -> None:
    document = ProbeDocument(
        raw_json=json.dumps(
            {
                "format": {"format_name": "matroska,webm"},
                "streams": [
                    {"index": 0, "codec_type": "video", "time_base": "1/1000"},
                    {
                        "index": 1,
                        "codec_type": "subtitle",
                        "time_base": "1/1000",
                        "codec_name": "webvtt",
                        "tags": {"language": "en"},
                    },
                ],
                "packets": [
                    {"stream_index": 0, "pts": 10, "duration": 20},
                    {"stream_index": 0, "pts": 30, "duration": 20},
                ],
            }
        )
    )
    projection = project_probe_document(document).projection
    assert projection is not None

    coverage = derive_packet_coverages(document, projection)
    subtitle_tracks = enumerate_subtitle_track_candidates(document)

    assert coverage[0].coverage is not None
    assert coverage[0].coverage.start.numerator == 1
    assert coverage[0].coverage.start.denominator == 100
    assert coverage[0].coverage.end.numerator == 1
    assert coverage[0].coverage.end.denominator == 20
    assert subtitle_tracks[0].as_json() == {
        "stream_index": 1,
        "language": "en",
        "container_format": "matroska,webm",
        "origin": "embedded",
        "available": True,
    }


def test_failed_coverage_probe_retains_partial_probe_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = PinnedExternalTool("ffprobe", tmp_path / "ffprobe", "test", "a" * 64)
    artifact = SourceArtifact("source", "a" * 64, 0, tmp_path / "media")
    responses = iter(
        (
            subprocess.CompletedProcess([], 0, stdout='{"streams": []}', stderr=""),
            subprocess.CompletedProcess([], 1, stdout='{"packets": []}', stderr="failed"),
        )
    )

    monkeypatch.setattr(inspection, "run_tool", lambda _arguments: next(responses))

    with pytest.raises(ProbeCaptureError) as error:
        capture_probe_documents(tool, artifact, tmp_path / "evidence")

    assert error.value.structural_document == ProbeDocument('{"streams": []}')
    assert error.value.coverage_document == ProbeDocument('{"packets": []}')
    assert (tmp_path / "evidence" / "structural.ffprobe.json").read_text() == '{"streams": []}'
    assert (tmp_path / "evidence" / "coverage.ffprobe.json").read_text() == '{"packets": []}'


def test_probe_evidence_conflict_retains_the_new_structural_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = PinnedExternalTool("ffprobe", tmp_path / "ffprobe", "test", "a" * 64)
    artifact = SourceArtifact("source", "a" * 64, 0, tmp_path / "media")
    evidence_directory = tmp_path / "evidence"
    evidence_directory.mkdir()
    (evidence_directory / "structural.ffprobe.json").write_text('{"streams": ["old"]}')
    monkeypatch.setattr(
        inspection,
        "run_tool",
        lambda _arguments: subprocess.CompletedProcess([], 0, stdout='{"streams": []}', stderr=""),
    )

    with pytest.raises(ProbeCaptureError) as error:
        capture_probe_documents(tool, artifact, evidence_directory)

    assert error.value.reason == "probe_document_conflict"
    assert error.value.structural_document == ProbeDocument('{"streams": []}')
    assert error.value.coverage_document is None
