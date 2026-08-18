# Ticket 08 remaining stages — design for clean continuation

Pattern (all): real engine self-isolates OR gets a child; replace the offline
`dispatch_real_stage` short-circuit with a real branch that SHARES the offline
spine's revalidation/report/stage_execution and only swaps evidence derivation +
measured runtime controls (mirror audio + transcription). Real-aware qualification
= eligibility alone. Real calibration profiles already exist under config/.

## TEXT-ANALYSIS DONE (`d5925b1` composition, `18b6675` wiring)
build_text_semantics_analysis + load_part_with_cue_texts + TextAnalysisReport
stage_execution field; analyze_text real branch + _real_text_parts full-ASR
resolution (transcription_report_id, Option-A) + run_composition threading. Offline
byte-identical; full suite 1702 green. Tests test_text_analysis_real_bridge.py.

## (below: original design, done)
## TEXT-ANALYSIS (critical path for run #1 deliverables) — text_analysis.py
Offline is a full completed structure (unlike transcription): `_run_controlled_generation`
(text_analysis.py:1002) loads cues from `selected_primary_tracks` and projects a
controlled fixture into segments/chapters/collection_summary. Real bridge:
- Engine: `text_semantics_engine.generate_text_semantics(project_root, workspace,
  contracts, *, source_id, stream_index, available: Sequence[LoadedPart],
  cue_texts: Mapping[str,str], unavailable=...)` -> `Qwen3TextSemanticsResult`
  (segments/chapters/collection_summary — SAME report types — + status +
  peak_memory_bytes). Self-isolated (ADR 0055), no new child.
- Inputs: reuse `_load_parts` (text_analysis.py:~1102, returns (LoadedPart,
  UnavailablePartInfo)) which builds available + cue_texts from selected primary
  tracks. FULL-ASR: selected_primary_tracks is EMPTY (no embedded subs) => resolve
  the Primary track from the completed transcription report's published
  source-candidate (Option-A: thread transcription_report_id into analyze_text +
  _invoke_text_analysis). Build a SelectedPrimaryTrack/LoadedPart from that
  candidate (enhancement.load_retained_subtitle_cues reads the same file; reuse or
  mirror its cue load to get cue_texts). cue_texts = {cue_id/ordinal: text}.
- Seam: at the offline `else:` that calls `_run_controlled_generation`
  (text_analysis.py:~779), when real & "text_semantics" in real_engines.capabilities,
  call generate_text_semantics instead; map result.status -> COMPLETE/model_output_invalid,
  segments/chapters/collection_summary straight through, + stage_execution record
  (text_semantics candidate, result.peak_memory_bytes, envelope gate — reuse the
  audio `_real_runtime_controls` pattern / add a text stage_execution field if the
  report lacks one; TextAnalysisReport currently has no stage_execution field ->
  ADD one like transcription did).
- Standalone helper first (like build_asr_transcript): `build_text_semantics_analysis(...)`
  -> (segments, chapters, collection_summary, stage_execution, status), unit-tested
  with generate_text_semantics monkeypatched. Then wire + full-ASR resolution.

## ENHANCEMENT — enhancement.py (asr_primary + asr_review, ADR 0045)
User-scoped (runs only with --part/--range/--cue); may be a no-op in run #1.
Already uses arbitration/suspicion/gates in production. Real bridge: re-transcribe
named intervals with asr_primary + independent asr_review, arbitrate. Both engines
self-isolate. Also needs Option-A transcript resolution for full-ASR named Parts
(load_retained_subtitle_cues from the transcription candidate). Confirm whether
run #1 even invokes enhancement (run choices) — if not selected, wiring can be
minimal + a guard.

## OCR SEAM DONE (`d6a8731`) + VISUAL-TEXT WIRING FINDING
ocr_child.py + ocr_engine.run_isolated_ocr / IsolatedOcrResult / default_ocr_command
built + tested (mirror vad/diarization). BUT run_visual_text's real bridge ALSO needs
REAL FRAME EXTRACTION: offline _execute_ocr uses a controlled fixture + a
frame-metric FIXTURE (_frame_metric_fixture_path "stands in for pinned-ffmpeg
extraction"); production has NO real ffmpeg frame extractor (only prototype
_extract_frames). So the real visual-text bridge = frame extraction (ffmpeg at
selected page PTS -> images) + run_isolated_ocr + gate + stage_execution + a
ProjectedOcrItem.from_json to feed the gate. Substantial.
DECISION POINT: OCR is NOT among run #1's full-ASR deliverables (subtitles/speakers/
summary/detailed-content). run #1 can proceed WITHOUT promoting ocr_primary
(visual-text stays offline/acquisition-gated); the visual-text real bridge + frame
extraction is a follow-up not on run #1's critical path. Enhancement is user-scoped
(optional for run #1) too.

## VISUAL-TEXT (original design below) — visual_text_command.py (ocr_primary) — ocr_child DONE
ONNX-scale RapidOCR runs in-process (ocr_engine.analyze_frames_ocr). Per ADR 0058
build `ocr_child.py` + `run_isolated_ocr` mirroring vad_child/diarization_child
(child runs analyze_frames_ocr, returns recognized regions json + peak). Then real
bridge in run_visual_text swapping the offline OCR derivation + stage_execution.
Independent of the transcript.

## THEN (task 5)
Registry promotion (add resource_estimate.high_bytes per candidate from
device-baselines.json max peak — values in ticket-08-bridge-design.md; leave
3dspeaker unpromoted, its footprint is inside sherpa's 348MB diarization baseline)
-> `vcp plan decode` -> `vcp plan confirm` (4 legal fields) -> `vcp run` on real
engines in background (full-ASR resource confirm via resume;
transcribe at semantic window; multi-hour) -> pause/resume drill at a stage
boundary -> `vcp verify` + `vcp inventory` -> record provenance in
docs/phase-12-download-plans/run-01.md + Coverage ledger -> commit -> /code-review.
