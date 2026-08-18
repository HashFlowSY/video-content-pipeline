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

## PROGRESS
- `d7af970` completed-transcription publication contract (TranscriptionReport
  transcript/stage_execution fields + publish_asr_subtitle_candidate; round-trip
  test vs enhancement.load_retained_subtitle_cues).
- `edd1279` build_asr_transcript(project_root, audio_report_document,
  full_asr_source_ids, asr_candidate, workspace, *, command=None) ->
  (transcript_entries, stage_execution). Recovers derivative + speech runs from
  VAD speech_likely intervals (exact via mapping.sample_for_source_time),
  re-chunks at SEMANTIC_CUE_WINDOW, runs transcribe_derivative, publishes candidate,
  asr_primary stage_execution w/ envelope gate. Unit-tested (engine monkeypatched).

## TRANSCRIPTION COMPLETED PATH DONE (`73bf34c`)
transcribe() real path wired: shares all gates, then runs build_asr_transcript ->
status=complete + transcript + stage_execution. Offline byte-identical (full suite
1696 green). Integration test in test_phase_7_transcription_cli_contract.py.
asr_review deferred to enhancement (D3). Downstream transcript CONSUMPTION (Option-A
threading + full-ASR cue resolution) folded into task 4 (each downstream stage's
real bridge), since that's where the transcript is read.

## NEXT (transcription wiring — fresh context, edits to transcribe())
1. Remove top short-circuit `if real_engines is not None: return dispatch_real_stage`
   (transcription.py ~483). Remove the now-unused dispatch_real_stage import.
2. Thread real_engines into transcribe(). At the offline `else:` that sets
   MODEL_ACQUISITION_REQUIRED (~line 601, after all resource pauses cleared &
   confirmation_granted), when real_engines is not None: read the audio report
   DOCUMENT (json at work/audio-analysis-reports/<audio_report_id>/), pick the
   eligible asr_primary candidate from `capabilities`, full_asr_source_ids =
   start_precondition.source_ids (the subtitle_unavailable set), call
   build_asr_transcript(...) -> transcript + stage_execution, set
   status=COMPLETE, pass transcript= + stage_execution= into TranscriptionReport.
   Offline path unchanged (real_engines None => model_acquisition_required).
   NOTE run #1 must first clear the full_asr_resource_confirmation pause via resume
   (resumption_decision=full_asr_resource_plan_confirmed) — that's a run-flow step.
3. Tests: a transcribe() real-path test (monkeypatch build_asr_transcript or the
   engine) asserting COMPLETE + transcript + stage_execution; offline unchanged.

## THEN Option-A downstream plumbing (separate commit)
Thread transcription_report_id into enhance()/analyze_text()/run_visual_text() +
their run_composition invokers (_invoke_enhancement/_invoke_text_analysis/
_invoke_visual_text pass state.reports.get(StageName.TRANSCRIPTION)). In each,
when a Part is full-ASR (no subtitle source candidate), resolve its subtitle cues
from the completed transcription report's published source-candidate (transcript
entry -> source_candidate path) instead of the subtitle report. Each with tests.

## Open decisions for the maintainer
- D1: downstream discovery = **Option A (thread transcription_report_id)** — OK?
- D2: run #1 transcription window = coarse VAD chunks vs SEMANTIC_CUE_WINDOW finer
  cues (finer is what the prototype/text-semantics expect). Recommend semantic.
- D3: does enhancement's asr_review belong in ticket 08 too, or only primary now?
  (Enhancement is a later stage in this ticket's staged build; review lands there.)
