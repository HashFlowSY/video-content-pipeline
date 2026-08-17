# Phase 11 ticket 13 — maintainer sample review

Retained record of the maintainer's quality-gate review of the seven capability
prototypes run on the ticket-12 Voice of America material (both Public Domain):

- `f6fd0cd7…` — zh Mandarin, VOA 时事大家谈 two-way (anchor + commentator 陈杰人), 4:02
- `104eeec2…` — en, VOA On Assignment (princelings), 4:53

Every run was offline (hub-offline guards proven in each record), from the pinned
registry assets. Every measured peak is ≤ 5.1 GiB, well within the 12 GiB
envelope. Full per-run evidence: `docs/phase-11-prototypes/<capability>/<source>-<lang>.record.json`
and `…​.md`; device baselines (real-time factor + peak): `device-baselines.json`.

Reviewed and decided 2026-08-17.

## Per-capability confirmation

| Capability | zh | en | Decision |
|---|---|---|---|
| vad | ✅ | ✅ | **Confirmed** — clean gap-free partition; 53 speech intervals (zh). |
| asr_primary (Qwen3-ASR-1.7B-8bit) | ✅ | ✅ | **Confirmed** — accurate transcription both languages. |
| asr_review (whisper-large-v3) | ✅ | ✅ | **Confirmed** — independent second model; VAD-trimmed. |
| ocr_primary (RapidOCR) | ✅ | ✅ | **Confirmed** — on-screen zh text read verbatim (`VOA卫视`, `陈杰人（电话连线）`, `独立时评人`, `时事大家谈`). |
| forced_alignment (Qwen3-ForcedAligner-0.6B-8bit) | ✅ | ✅ | **Confirmed**, with recorded granularity follow-up (see below). |
| diarization (sherpa-onnx pyannote-seg + CAM++) | ✅ | ⚠️ | **Confirmed as-is**, with a recorded over-clustering note (see below). |
| text_semantics (Qwen3-4B-Instruct-2507-8bit) | 🔴 | 🔴 | **Not confirmed** — diagnosed adapter gap; follow-up ticket (see below). |

No sample was bounced to a fallback quant tier / candidate: the two issues below
are adapter/calibration matters, not model-selection quality problems.

## Recorded notes and follow-ups

### diarization over-clustering (accepted as-is)
The engine produces engineering-valid, anonymous, Part-local SpeakerTurns
(`part-NN:speaker-MM`, ADR 0030), but the en clip over-clusters — ~15 clusters
for ~3 speakers — at the current `cluster_threshold` 0.5. The maintainer accepted
the current calibration as-is for ticket 13; real `cluster_threshold` tuning is
deferred to Phase 12 against human reference. Recorded in
`config/audio-analysis/sherpa-diarization-calibration.json`
(`qualification_scope: real_sample_confirmed_with_note`).

### chunk granularity (recorded follow-up)
The whole ~4-minute clip fits in a single ≤5-minute VAD chunk, and ticket-09
asr_primary emits one cue per chunk, so these clips produce a single giant ASR
cue. Alignment then has one proposal spanning the clip, and text_semantics has a
single cue to "segment". Finer cue granularity (sub-chunk cueing) is needed
before alignment adoption and semantic segmentation are meaningfully exercised in
Phase 12.

### text_semantics adapter gap (follow-up ticket)
text_semantics returned `model_output_invalid` (0 segments) on **both** languages.
Root cause is **not** model quality: the versioned prompt rendered by
`render_text_semantics_prompt` (ticket 10) carries only cue *identities* — no
transcript text and no output schema — even though the template states
"PresentationCues … provided verbatim". With no content and no schema, Qwen3-4B
can only echo the cue id (`{"segments":[{"title":"part-1:0", …}]}`), which the
unchanged Text-model output projection correctly rejects. Fixing it means
including cue text + the output schema in the prompt, which bumps
`prompt_template_version` and forces recalibration (ADR 0056), plus the finer-cue
work above. Deferred to a follow-up ticket rather than rewriting the versioned
Phase 6 contract under a prototype ticket. The Qwen3-4B selection itself is not
rejected; its semantic quality remains unproven until the prompt is fixed.

## Calibration provenance
Real-sample qualification landed on the VAD, diarization, and forced-alignment
calibration records (`qualification_scope` moved off `first_device_baseline`;
`real_sample_qualification` provenance block added), per ADRs 0029/0031/0027.
