# 07 — Real diarization adapter (sherpa-onnx pipeline)

**What to build:** Fill the Diarization capability vacancy with the
sherpa-onnx offline pipeline: pyannote-segmentation-3.0 ONNX (MIT) +
3D-Speaker CAM++ zh-en advanced embedding (Apache-2.0) + clustering,
running in-process (ONNX-scale). Output implements the existing
SpeakerTurn / Part-local anonymous speaker label contracts (ADR 0030
unchanged: labels anonymous, Part-local, never true names). Overlap-aware
segments map onto the existing overlapping-speech representation.
Diarization calibration record per ADR 0031;
Diarization-VAD conflict evidence keeps its existing meaning against the
real partition from ticket 06.

**Blocked by:** 03, 04
**Status:** done
**Labels:** ready-for-agent

- [x] Real pipeline over a fixture-derived wav yields contract-valid
      SpeakerTurns with anonymous Part-local labels (integration test,
      offline, registry-path models)
- [x] Missing/mismatched assets yield the typed acquisition failure
- [x] Overlapping speech in input produces overlap-marked turns, not
      silent merging
- [x] Diarization calibration record produced and gate-checked per ADR
      0031
- [x] Full suite green within budget
