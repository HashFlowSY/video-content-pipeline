"""Deterministic substitute model adapters for the Phase 10 synthetic end to end.

Phase 10 runs ``vcp run`` end to end over synthetic media with the *production*
composition, real per-phase functions, real ffmpeg/ffprobe, and the real
filesystem. The only non-real component is the model layer: every capability the
per-phase functions require (VAD, forced alignment, diarization, and the text
model) is served by a **controlled offline adapter** (ADR 0037 lineage) rather
than a downloaded or executed model.

This module seeds those adapters into a project root as the codebase already
expects them — there is *no* runtime dependency-injection seam and production
gains no test mode. The two mechanisms the per-phase functions read are:

* Audio analysis (``analyze_audio``) reads an inline ``controlled_adapter``
  (``adapter_version``/``raw_output``/``projection``) on each eligible
  ``models/registry.json`` candidate, gated by a ``calibration_evaluation``
  whose reference fixture must equal the projection byte for byte.
* Text analysis (``analyze_text``) reads the four versioned
  ``config/text-analysis/`` contract artifacts plus an output fixture bound by
  the input-cue manifest SHA.

Every value the pipeline binds against is **derived from the run's own real
inputs** — the plan's real ffprobe inspection evidence (the structural and
coverage SHAs) and the real subtitle workspace's cues (the alignment/diarization
citations and the text input manifest). So the adapters are content-derived and
deterministic: the same fixture bytes always seed the same adapter bytes, and
different inputs seed different outputs. Nothing here depends on a per-run random
id, so the whole set can be seeded once before the run.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from video_content_pipeline import audio_analysis, text_generation
from video_content_pipeline.inspection import PlanInspectionEvidence
from video_content_pipeline.subtitle_pipeline import (
    CandidateState,
    SubtitleCandidate,
    SubtitleCandidateReport,
)
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval

# --- Text-analysis contract identities (must agree across the four artifacts) --

_TEXT_OUTPUT_SCHEMA_VERSION = "phase-10-text-output-schema-v1"
_TEXT_PROMPT_TEMPLATE_VERSION = "phase-10-text-prompt-template-v1"
_TEXT_EVIDENCE_RULES_VERSION = "phase-10-text-evidence-rules-v1"
_TEXT_CUE_RULES_VERSION = "phase-10-text-cue-rules-v1"
_TEXT_ADAPTER_IDENTITY = "phase-10-controlled-text-adapter-v1"
_TEXT_FIXTURE_RELATIVE = "config/text-analysis/fixtures/controlled-output.json"

#: The controlled diarization candidate the run plan must select
#: (``diarization_candidate`` collection choice). Exposed so the harness that
#: builds the plan and the seeder agree on one name.
CONTROLLED_DIARIZATION_CANDIDATE = "controlled-diarization"


@dataclass(frozen=True)
class _Cue:
    """One retained source cue, read from a candidate's ``source-candidate.json``."""

    source_ordinal: int
    text: str
    start: Mapping[str, int]
    end: Mapping[str, int]


@dataclass(frozen=True)
class _SourceBinding:
    """Everything one source artifact contributes to the seeded adapters."""

    source_id: str
    subtitle_stream_index: int
    audio_stream_index: int
    structural_raw_json: str
    coverage_json: Mapping[str, object]
    usable_intervals: tuple[HalfOpenInterval, ...]
    source_candidate_sha256: str
    cues: tuple[_Cue, ...]


def seed_offline_model_adapters(
    project_root: Path,
    *,
    inspection_evidence: Sequence[PlanInspectionEvidence],
    subtitle_report: SubtitleCandidateReport,
) -> None:
    """Seed the controlled audio and text adapters for one confirmed plan.

    ``inspection_evidence`` is the confirmed plan's real ffprobe evidence (the
    source of the structural/coverage SHAs the audio projection must match).
    ``subtitle_report`` is the report a real ``process_subtitles`` pass produced
    over the fixture (the source of the cue citations and the text input
    manifest). Both are byte-for-byte reproducible across runs, so calling this
    before the end-to-end run seeds adapters the in-run stages accept.
    """

    bindings = _source_bindings(project_root, inspection_evidence, subtitle_report)
    _seed_audio_registry(project_root, bindings)
    _seed_text_contracts(project_root, bindings)


# --- Shared derivation ------------------------------------------------------


def _source_bindings(
    project_root: Path,
    inspection_evidence: Sequence[PlanInspectionEvidence],
    subtitle_report: SubtitleCandidateReport,
) -> tuple[_SourceBinding, ...]:
    evidence_by_source = {evidence.source_id: evidence for evidence in inspection_evidence}
    candidates_by_source = _valid_candidates_by_source(subtitle_report)
    bindings: list[_SourceBinding] = []
    for source_id, candidate in candidates_by_source.items():
        evidence = evidence_by_source[source_id]
        structural = evidence.structural_document
        assert structural is not None, "Confirmed inspection evidence must carry structural probe."
        audio_index = _audio_stream_index(structural.raw_json)
        coverage = dict(evidence.coverage_by_stream)[audio_index]
        coverage_json = audio_analysis._stream_coverage_as_json(coverage)
        assert coverage.coverage is not None, "Audio stream must have determinate coverage."
        cues = _read_cues(project_root, candidate)
        bindings.append(
            _SourceBinding(
                source_id=source_id,
                subtitle_stream_index=candidate.stream_index,
                audio_stream_index=audio_index,
                structural_raw_json=structural.raw_json,
                coverage_json=coverage_json,
                # The decoded audio's usable intervals (coverage minus its
                # inter-packet gaps): a real VAD adapter partitions exactly these,
                # so the segments the adapter emits are content-derived from them.
                usable_intervals=audio_analysis._usable_audio_intervals(coverage),
                source_candidate_sha256=str(candidate.source_candidate_sha256),
                cues=cues,
            )
        )
    if not bindings:
        raise ValueError("The subtitle report exposes no valid Primary track to bind adapters to.")
    return tuple(bindings)


def _valid_candidates_by_source(
    report: SubtitleCandidateReport,
) -> dict[str, SubtitleCandidate]:
    """Return the first valid candidate per source, in report order."""

    chosen: dict[str, SubtitleCandidate] = {}
    for candidate in report.candidates:
        if candidate.state is not CandidateState.VALID:
            continue
        if candidate.source_candidate_path is None or candidate.source_candidate_sha256 is None:
            continue
        chosen.setdefault(candidate.source_id, candidate)
    return chosen


def _audio_stream_index(structural_raw_json: str) -> int:
    document = json.loads(structural_raw_json)
    streams = document.get("streams") if isinstance(document, Mapping) else None
    if not isinstance(streams, list):
        raise ValueError("Structural probe evidence has no streams list.")
    for stream in streams:
        if (
            isinstance(stream, Mapping)
            and stream.get("codec_type") == "audio"
            and isinstance(stream.get("index"), int)
            and not isinstance(stream.get("index"), bool)
        ):
            return int(stream["index"])
    raise ValueError("Structural probe evidence exposes no audio stream.")


def _read_cues(project_root: Path, candidate: SubtitleCandidate) -> tuple[_Cue, ...]:
    path = _project_path(project_root, str(candidate.source_candidate_path))
    document = json.loads(path.read_text(encoding="utf-8"))
    cues = document.get("cues") if isinstance(document, Mapping) else None
    if not isinstance(cues, list):
        raise ValueError("A source-candidate document must carry a cues list.")
    read: list[_Cue] = []
    for cue in cues:
        interval = cue.get("raw_pts_interval")
        read.append(
            _Cue(
                source_ordinal=int(cue["source_ordinal"]),
                text=str(cue["text"]),
                start=dict(interval["start"]),
                end=dict(interval["end"]),
            )
        )
    return tuple(read)


def _project_path(project_root: Path, recorded: str) -> Path:
    path = Path(recorded)
    return path if path.is_absolute() else project_root / path


def _time_json(time: ExactTime) -> dict[str, int]:
    return {"numerator": time.numerator, "denominator": time.denominator}


# --- Audio adapter ----------------------------------------------------------


def _seed_audio_registry(project_root: Path, bindings: Sequence[_SourceBinding]) -> None:
    derivative_path = project_root / "work" / "controlled-analysis-audio.derivative"
    derivative_path.parent.mkdir(parents=True, exist_ok=True)
    derivative_path.write_bytes(b"controlled-offline-analysis-audio-derivative")
    derivative_evidence = {
        "path": derivative_path.as_posix(),
        "sha256": sha256(derivative_path.read_bytes()).hexdigest(),
        "byte_count": derivative_path.stat().st_size,
    }

    dependency_plan = project_root / "models" / "plans" / "controlled-audio.md"
    dependency_plan.parent.mkdir(parents=True, exist_ok=True)
    dependency_plan.write_text("# Controlled offline audio dependency plan\n", encoding="utf-8")

    vad_projection = _vad_projection(bindings, derivative_evidence)
    alignment_projection = _alignment_projection(bindings, derivative_evidence)
    diarization_projection = _diarization_projection(bindings, derivative_evidence)

    calibration_dir = project_root / "tests" / "fixtures" / "calibration"
    calibration_dir.mkdir(parents=True, exist_ok=True)
    vad_fixture = _write_calibration(
        calibration_dir / "vad.json",
        vad_projection,
        {
            "uncovered_speech_duration": {"numerator": 3600, "denominator": 1},
            "long_silence_duration": {"numerator": 3600, "denominator": 1},
        },
    )
    alignment_fixture = _write_calibration(
        calibration_dir / "alignment.json",
        alignment_projection,
        {
            "minimum_confidence": 0.5,
            "duration_rules": {
                "en": {
                    "minimum_duration": {"numerator": 1, "denominator": 1000},
                    "maximum_duration": {"numerator": 3600, "denominator": 1},
                }
            },
        },
    )
    diarization_fixture = _write_calibration(
        calibration_dir / "diarization.json",
        diarization_projection,
        {"minimum_confidence": 0.5},
    )

    registry = {
        "schema_version": 2,
        "candidates": [
            _audio_candidate("controlled-vad", "vad", vad_projection, "vad.json", vad_fixture),
            _audio_candidate(
                "controlled-alignment",
                "forced_alignment",
                alignment_projection,
                "alignment.json",
                alignment_fixture,
            ),
            _audio_candidate(
                CONTROLLED_DIARIZATION_CANDIDATE,
                "diarization",
                diarization_projection,
                "diarization.json",
                diarization_fixture,
            ),
        ],
    }
    registry_path = project_root / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(registry, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def _source_time_mapping(
    binding: _SourceBinding, derivative_evidence: Mapping[str, object]
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "coordinate": "raw_pts_identity",
        "structural_evidence_sha256": audio_analysis._sha256_json(binding.structural_raw_json),
        "coverage_evidence_sha256": audio_analysis._sha256_json(binding.coverage_json),
        "derivative_evidence": dict(derivative_evidence),
    }


def _model_identity(capability: str, asset: str) -> dict[str, str]:
    return {
        "asset_sha256": asset,
        "backend": "controlled-offline-adapter",
        "backend_version": "1.0.0",
        "precision": "fixture",
        "device_class": "fixture-cpu",
        "rules_fingerprint": f"{capability}-rules-v1",
    }


def _vad_projection(
    bindings: Sequence[_SourceBinding], derivative_evidence: Mapping[str, object]
) -> dict[str, object]:
    parts = [
        {
            "source_id": binding.source_id,
            "stream_index": binding.audio_stream_index,
            "source_time_mapping": _source_time_mapping(binding, derivative_evidence),
            # One speech segment per usable (decoded, gap-free) interval, so every
            # segment is contained in known usable audio and the partition is
            # complete — a real VAD adapter's shape, derived from the coverage.
            "segments": [
                {
                    "start": _time_json(interval.start),
                    "end": _time_json(interval.end),
                    "state": "speech_likely",
                }
                for interval in binding.usable_intervals
            ],
        }
        for binding in bindings
    ]
    return {
        "schema_version": 1,
        "capability": "vad",
        "model_identity": _model_identity("vad", "a" * 64),
        "result": {"parts": parts},
    }


def _alignment_projection(
    bindings: Sequence[_SourceBinding], derivative_evidence: Mapping[str, object]
) -> dict[str, object]:
    parts = [
        {
            "source_id": binding.source_id,
            "stream_index": binding.audio_stream_index,
            "language": "en",
            "source_time_mapping": _source_time_mapping(binding, derivative_evidence),
            "cues": [
                {
                    "source_ordinal": cue.source_ordinal,
                    "text": cue.text,
                    "start": dict(cue.start),
                    "end": dict(cue.end),
                    "confidence": 0.9,
                }
                for cue in binding.cues
            ],
        }
        for binding in bindings
    ]
    return {
        "schema_version": 1,
        "capability": "forced_alignment",
        "model_identity": _model_identity("alignment", "b" * 64),
        "result": {"parts": parts},
    }


def _diarization_projection(
    bindings: Sequence[_SourceBinding], derivative_evidence: Mapping[str, object]
) -> dict[str, object]:
    parts = []
    for binding in bindings:
        first_cue = binding.cues[0]
        parts.append(
            {
                "source_id": binding.source_id,
                "stream_index": binding.audio_stream_index,
                "source_time_mapping": _source_time_mapping(binding, derivative_evidence),
                # One turn per usable interval, all one speaker — within known
                # usable audio, mirroring the VAD partition.
                "turns": [
                    {
                        "cluster_id": "speaker-0",
                        "start": _time_json(interval.start),
                        "end": _time_json(interval.end),
                        "confidence": 0.9,
                    }
                    for interval in binding.usable_intervals
                ],
                "role_candidates": [
                    {
                        "cluster_id": "speaker-0",
                        "role": "host",
                        "subtitle_text": {
                            "source_ordinal": first_cue.source_ordinal,
                            "text": first_cue.text,
                        },
                    }
                ],
            }
        )
    return {
        "schema_version": 1,
        "capability": "diarization",
        "model_identity": _model_identity("diarization", "c" * 64),
        "result": {"parts": parts},
    }


def _write_calibration(
    path: Path, projection: Mapping[str, object], thresholds: Mapping[str, object]
) -> str:
    # The reference fixture must equal the candidate projection byte for byte
    # (canonical JSON), so both are built from the identical dict.
    path.write_text(
        json.dumps({"expected_projection": projection, "thresholds": thresholds}),
        encoding="utf-8",
    )
    return sha256(path.read_bytes()).hexdigest()


def _audio_candidate(
    candidate_id: str,
    capability: str,
    projection: Mapping[str, object],
    fixture_name: str,
    fixture_sha: str,
) -> dict[str, object]:
    identity = projection["model_identity"]
    assert isinstance(identity, Mapping)
    return {
        "candidate_id": candidate_id,
        "capability": capability,
        "official_source": {
            "url": f"https://offline.invalid/{candidate_id}",
            "approved": True,
        },
        "license_approved": True,
        "revision": "phase-10-fixture-r1",
        "asset_sha256": identity["asset_sha256"],
        "offline_runtime": True,
        "credential_required": False,
        "telemetry": False,
        "dependency_plan": "models/plans/controlled-audio.md",
        "resource_estimate": {"high_bytes": 1024},
        "execution_controls": {
            "resource_measurement": {"peak_bytes": 512},
            "unload_evidence": {"state": "released", "resident_bytes": 0},
        },
        "controlled_adapter": {
            "adapter_version": "phase-10-controlled-audio-adapter-v1",
            "raw_output": {"native": []},
            "projection": projection,
        },
        "calibration_evaluation": {
            "schema_version": 1,
            "reference_fixture": {
                "path": f"tests/fixtures/calibration/{fixture_name}",
                "sha256": fixture_sha,
            },
            "evaluator_version": "phase-10-fixture-evaluator-v1",
        },
    }


# --- Text adapter -----------------------------------------------------------


def _seed_text_contracts(project_root: Path, bindings: Sequence[_SourceBinding]) -> None:
    config = project_root / "config"
    text_dir = config / "text-analysis"
    text_dir.mkdir(parents=True, exist_ok=True)

    (config / "text-analysis-rules.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "phase-10-fixture-rules",
                "cue_rules_version": _TEXT_CUE_RULES_VERSION,
                "prompt_template_version": _TEXT_PROMPT_TEMPLATE_VERSION,
                "output_schema_version": _TEXT_OUTPUT_SCHEMA_VERSION,
                "evidence_rules_version": _TEXT_EVIDENCE_RULES_VERSION,
                "controlled_adapter_identity": _TEXT_ADAPTER_IDENTITY,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    artifacts: Mapping[str, Mapping[str, object]] = {
        "prompt-template.json": {
            "schema_version": 1,
            "version": _TEXT_PROMPT_TEMPLATE_VERSION,
            "sections": [{"id": "task", "role": "system", "text": "Segment the cues."}],
        },
        "output-schema.json": {
            "schema_version": 1,
            "version": _TEXT_OUTPUT_SCHEMA_VERSION,
            "envelope": {
                "expected_schema_version": 1,
                "required_fields": [
                    "schema_version",
                    "output_schema_version",
                    "adapter_identity",
                    "result",
                ],
                "result": {
                    "required_fields": ["parts"],
                    "list_fields": ["parts"],
                    "optional_object_or_null_fields": ["collection_summary"],
                },
            },
        },
        "evidence-rules.json": {
            "schema_version": 1,
            "version": _TEXT_EVIDENCE_RULES_VERSION,
        },
        "controlled-adapter.json": {
            "schema_version": 1,
            "version": _TEXT_ADAPTER_IDENTITY,
            "implementation_version": "phase-10-controlled-text-adapter-impl-v1",
            "prompt_template_version": _TEXT_PROMPT_TEMPLATE_VERSION,
            "output_schema_version": _TEXT_OUTPUT_SCHEMA_VERSION,
            "evidence_rules_version": _TEXT_EVIDENCE_RULES_VERSION,
            "sampling_configuration": {"mode": "deterministic", "temperature": 0, "seed": 0},
        },
    }
    for name, payload in artifacts.items():
        (text_dir / name).write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    tracks = tuple(
        (binding.source_id, binding.subtitle_stream_index, binding.source_candidate_sha256)
        for binding in bindings
    )
    manifest = text_generation.input_cue_manifest_document(tracks)
    manifest_sha = text_generation.input_cue_manifest_sha256(manifest)

    result = _text_result(bindings)
    output = {
        "schema_version": 1,
        "output_schema_version": _TEXT_OUTPUT_SCHEMA_VERSION,
        "adapter_identity": _TEXT_ADAPTER_IDENTITY,
        "result": result,
    }
    raw = json.dumps(output, sort_keys=True).encode("utf-8")
    fixture_path = project_root / _TEXT_FIXTURE_RELATIVE
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_bytes(raw)

    adapter_path = text_dir / "controlled-adapter.json"
    adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
    adapter["generation"] = {
        "output_fixture_path": _TEXT_FIXTURE_RELATIVE,
        "output_fixture_sha256": sha256(raw).hexdigest(),
        "input_fixture_sha256": manifest_sha,
    }
    adapter_path.write_text(json.dumps(adapter, sort_keys=True) + "\n", encoding="utf-8")


def _text_result(bindings: Sequence[_SourceBinding]) -> dict[str, object]:
    """One trivial single-cue segment per cue, tiling the Part exactly once.

    Each cue becomes its own segment (boundary start == end == that cue), so the
    boundaries are contiguous and own every cue exactly once — the shape that
    concludes ``complete`` (no fallback), with content derived from the cue.
    """

    parts = []
    for binding in bindings:
        stream = f"stream-{binding.subtitle_stream_index}"

        def cue_id(ordinal: int, stream: str = stream, source_id: str = binding.source_id) -> str:
            return text_generation.cue_id(source_id, stream, ordinal)

        segments = [
            {
                "boundary": {
                    "start_cue_id": cue_id(cue.source_ordinal),
                    "end_cue_id": cue_id(cue.source_ordinal),
                },
                "content": {
                    "title": {
                        "text": f"段落 {cue.source_ordinal}",
                        "cue_ids": [cue_id(cue.source_ordinal)],
                    }
                },
            }
            for cue in binding.cues
        ]
        parts.append({"part_id": binding.source_id, "segments": segments, "chapters": []})
    return {"parts": parts, "collection_summary": None}
