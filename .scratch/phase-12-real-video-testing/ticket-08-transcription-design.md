# Completed-transcription contract — design proposal (ticket 08)

## The gap (why this needs design, not wiring)
- Offline `transcribe()` never completes: it stops at `model_acquisition_required`
  / resource pauses (ADR 0037). `TranscriptionReport` has NO completed status and
  NO cue/transcript field.
- Downstream stages (enhancement, text-analysis, visual-text) all bind to the
  **subtitle report** and read a Part's `source-candidate.json`
  (schema_version 1; `cues:[{source_ordinal, text, raw_pts_interval}]`) via
  `enhancement.load_retained_subtitle_cues`. In the full-ASR branch that candidate
  does NOT exist (the Part is `subtitle_unavailable_requires_asr_plan`).
- **None of the downstream stage invokers receive a transcription_report_id.** So
  today there is no path from the ASR transcript to the stages that consume
  subtitles. This is the real architectural gap ticket 08 must close.

## Building blocks that already exist
- `asr_engine.transcribe_derivative(...) -> PrimaryTranscriptionResult` (cues:
  tuple[ProjectedAsrCue], peak_memory_bytes) — self-isolated per chunk, real peak.
- `ProjectedAsrCue{ordinal, interval, text, tokens, language_spans}` + as_json.
- Suspicion/review/arbitration/gates live in the **enhancement** stage, not here
  (review is a targeted enhancement, ADR 0045). So transcription's job is the
  PRIMARY transcript only; asr_review is exercised by enhancement.

## Proposed contract
1. **New `TranscriptionReportStatus.COMPLETED`** plus completed-only fields on
   `TranscriptionReport`: `transcript` (per-Part published cue evidence pointers)
   and `stage_execution` (asr_primary record with measured peak + released/0).
2. **Publish an ASR subtitle source-candidate per full-ASR Part**: transcription
   writes a `source-candidate.json` (the SAME schema downstream already reads —
   `schema_version:1`, `cues:[{source_ordinal, text, raw_pts_interval}]`) into a
   transcription-owned, content-addressed location, from the ProjectedAsrCues
   (ordinal←cue.ordinal, text←cue.text, raw_pts_interval←cue.interval). Provenance
   `asr`. The completed report records each Part's published candidate as
   InputEvidence.
3. **Downstream discovery (the decision).** Two options:
   - **(A) Thread transcription_report_id into enhancement/text-analysis/
     visual-text.** When a Part is full-ASR, they resolve its subtitle cues from the
     completed transcription report's published candidate instead of the (absent)
     subtitle-report candidate. Explicit, honest provenance; touches 3 stage
     signatures + run_composition invokers. RECOMMENDED.
   - (B) Have transcription register its published candidate back into the subtitle
     report so the existing subtitle binding transparently finds it. Fewer
     signature changes but overloads the subtitle report's meaning and blurs
     provenance. Not recommended.
4. **stage_execution**: asr_primary via `_real_runtime_controls`-equivalent (child
   peak from PrimaryTranscriptionResult.peak_memory_bytes; released/0 on exit),
   validated against the promoted asr_primary envelope. No new child module (ASR
   already self-isolates).
5. **Real gate**: replace `dispatch_real_stage` short-circuit; when real, run the
   primary ASR completed path; offline stays byte-identical (still stops at
   model_acquisition_required). Language per Part from the audio-stream selection
   (as in audio-stage alignment).

## Scope note
Run #1 is full-ASR, so this completed path IS exercised (it produces run #1's
subtitles). Chunking: default 5-min VAD chunks pack a short clip into one giant
cue; the prototype re-chunks at SEMANTIC_CUE_WINDOW for finer cues (ticket 15).
Decide whether run #1 transcribes at the coarse or semantic-cue window.

## What I'd build (TDD), pending approval
- Extend transcription_contracts / TranscriptionReport with COMPLETED + transcript
  + stage_execution + published-candidate publication (unit-tested serialization).
- `_transcribe_real(...)` composing transcribe_derivative → publish candidates →
  completed report; monkeypatched-engine unit tests (no model).
- Option A plumbing: transcription_report_id through the 3 downstream invokers +
  full-ASR cue resolution (with its own tests).
- Full suite green; offline byte-identical.

## APPROVED 2026-08-18
- D1 = **A** (thread transcription_report_id into enhancement/text-analysis/
  visual-text; full-ASR Parts resolve cues from the completed transcription report).
- D2 = **semantic (finer) cues** (re-chunk at SEMANTIC_CUE_WINDOW).
- D3 = **asr_review with enhancement**; transcription = primary transcript only.

## Open decisions for the maintainer
- D1: downstream discovery = **Option A (thread transcription_report_id)** — OK?
- D2: run #1 transcription window = coarse VAD chunks vs SEMANTIC_CUE_WINDOW finer
  cues (finer is what the prototype/text-semantics expect). Recommend semantic.
- D3: does enhancement's asr_review belong in ticket 08 too, or only primary now?
  (Enhancement is a later stage in this ticket's staged build; review lands there.)
