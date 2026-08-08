from __future__ import annotations

from video_content_pipeline.probe import ProbeDocument, project_probe_document
from video_content_pipeline.timecode import ExactTime


def test_projection_retains_raw_json_and_projects_exact_stream_time_base() -> None:
    raw_json = """
    {
      "streams": [
        {"index": 2, "codec_type": "audio", "time_base": "1/48000"}
      ],
      "format": {"duration": "99.500000"}
    }
    """

    document = ProbeDocument(raw_json=raw_json)
    result = project_probe_document(document)

    assert result.document.raw_json == raw_json
    assert result.diagnostics == ()
    assert result.projection is not None
    assert result.projection.streams[0].index == 2
    assert result.projection.streams[0].codec_type == "audio"
    assert result.projection.streams[0].time_base == ExactTime(1, 48_000)


def test_projection_tolerates_unknown_fields_without_adding_them_to_decisions() -> None:
    raw_json = """
    {
      "streams": [
        {
          "index": 0,
          "codec_type": "video",
          "time_base": "1/90000",
          "vendor_extension": {"ignored": true}
        }
      ],
      "unrecognized_top_level": ["retained only in raw evidence"]
    }
    """

    result = project_probe_document(ProbeDocument(raw_json=raw_json))

    assert result.projection is not None
    assert result.projection.streams[0].time_base == ExactTime(1, 90_000)
    assert "vendor_extension" in result.document.raw_json
    assert "unrecognized_top_level" in result.document.raw_json


def test_missing_time_base_is_not_supplied_by_duration_or_human_readable_output() -> None:
    raw_json = """
    {
      "streams": [{"index": 0, "codec_type": "audio", "duration": "123.456"}],
      "format": {"duration": "456.789"},
      "human_readable_output": "time_base=1/48000"
    }
    """

    result = project_probe_document(ProbeDocument(raw_json=raw_json))

    assert result.projection is None
    assert [diagnostic.reason for diagnostic in result.diagnostics] == [
        "probe_invalid",
        "coverage_indeterminate",
    ]
    assert result.diagnostics[0].path == "streams[0].time_base"


def test_invalid_time_base_is_rejected_without_guessing_from_duration_metadata() -> None:
    raw_json = """
    {
      "streams": [
        {"index": 1, "codec_type": "video", "time_base": "not-a-rational", "duration": "9"}
      ]
    }
    """

    result = project_probe_document(ProbeDocument(raw_json=raw_json))

    assert result.projection is None
    assert result.diagnostics[0].reason == "probe_invalid"
    assert result.diagnostics[0].path == "streams[0].time_base"
    assert result.diagnostics[1].reason == "coverage_indeterminate"
