from __future__ import annotations

import json

from video_content_pipeline.inspection import (
    derive_packet_coverages,
    enumerate_subtitle_track_candidates,
)
from video_content_pipeline.probe import ProbeDocument, project_probe_document


def test_packet_evidence_derives_exact_coverage_and_subtitle_metadata_only() -> None:
    document = ProbeDocument(
        raw_json=json.dumps(
            {
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
        "codec_name": "webvtt",
        "origin": "unknown",
        "available": True,
    }
