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
| text_semantics (Qwen3-4B-Instruct-2507-8bit) | ✅ | ✅ | **Confirmed** after the ticket-15 prompt-v2 adapter fix (see below); was 🔴 under the ticket-10 v1 prompt. |

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

### chunk granularity — addressed for the text pipeline by ticket 15
The whole ~4-minute clip fits in a single ≤5-minute VAD chunk, and ticket-09
asr_primary emits one cue per chunk, so at chunk granularity these clips produce a
single giant ASR cue — alignment then has one proposal spanning the clip, and
text_semantics has a single cue to "segment". Ticket 15 added a finer
speech-anchored window (`vad_chunking.SEMANTIC_CUE_WINDOW`, 30 s) that re-derives
chunks (cut only in silence) and runs the *unchanged* real primary ASR over them,
yielding many finer cues (9 on the zh clip, 11 on the en clip) on the authoritative
source timeline through the unchanged `ProjectedAsrCue`/gate contracts. The
prototype's alignment and text-semantics prep now consume these finer cues, so
semantic segmentation has boundaries to propose over and forced alignment has
cue-scale proposals. The confirmed chunk-level asr_primary demonstration is left
unchanged. Real segment-count/alignment-adoption tuning against human reference
stays a Phase 12 concern.

### text_semantics adapter gap — RESOLVED by ticket 15
Under the ticket-10 v1 prompt, text_semantics returned `model_output_invalid`
(0 segments) on **both** languages. Root cause was **not** model quality: the
versioned prompt rendered by `render_text_semantics_prompt` carried only cue
*identities* — no transcript text and no output schema — even though the template
stated "PresentationCues … provided verbatim". With no content and no schema,
Qwen3-4B could only echo the cue id (a 173-byte `{"title":"part-1:0"}`-style
response), which the unchanged Text-model output projection correctly rejects.

Ticket 15 fixed the adapter: `render_text_semantics_prompt` now renders each cue's
verbatim recognized text plus the exact output envelope (fixed `schema_version`,
`output_schema_version`, `adapter_identity`, and the per-segment boundary/cited-content
shape). This changed the prompt content, so `prompt_template_version` was bumped to
`phase-06-prompt-template-v2` and the Qwen3 decoding profile recalibrated to it
(ADR 0056). The Controlled offline text adapter (a hash-pinned fixture, not a
prompt-driven path) is unchanged in meaning; only its bound version string moved.

Re-run 2026-08-17 on the same ticket-12 material, both clips now project into
verified, cue-cited SemanticSegments in Chinese prose:
- zh (`f6fd0cd7`): 9 finer cues → one segment "江歌案的舆论引爆与法律争议…" with 6 cited details.
- en (`104eeec2`): 11 finer cues → one segment "中国'皇亲国戚'的财富崛起与社会不平等" with 8 cited details.

Both peaks ~5 GiB (‹12 GiB), RTF ~3.0, offline, raw retained as restricted audit
evidence. **Confirmed** by the maintainer; the calibration record moved to
`qualification_scope: real_sample_confirmed` with a `real_sample_qualification`
block. Recorded observation for Phase 12: the model groups a single-topic clip into
one segment, so multi-segment behaviour is not yet exercised and remains to be tuned
against human reference.

## Calibration provenance
Real-sample qualification landed on the VAD, diarization, and forced-alignment
calibration records (`qualification_scope` moved off `first_device_baseline`;
`real_sample_qualification` provenance block added), per ADRs 0029/0031/0027.
