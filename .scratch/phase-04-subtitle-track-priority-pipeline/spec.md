# Phase 4: Subtitle-Track-Priority Pipeline

## Domain routing

Begin with the [Context Map](../../CONTEXT-MAP.md), then read the [Media
Foundation](../../docs/contexts/media-foundation/CONTEXT.md), [Source
Planning](../../docs/contexts/source-planning/CONTEXT.md), and
[Subtitles](../../docs/contexts/subtitles/CONTEXT.md) Contexts.

Type: enhancement
Status: ready-for-agent
Labels: ready-for-agent
Phase: 4
Published: 2026-08-10

## Problem Statement

Confirmed RunPlans can identify embedded SubtitleTrackCandidates but cannot yet
retain subtitle text, choose an auditable primary track, or create a readable
subtitle view. The next capability must preserve source evidence while staying
strictly below ASR, OCR, model, and publishing boundaries.

## Solution

Create a Phase 4 candidate pipeline from a revalidated confirmed RunPlan. It
extracts only embedded text subtitle payloads into immutable workspace attempts,
validates every candidate atomically on the existing exact time model, pauses
for explicit user choice when valid candidates are ambiguous, and produces
source/readable candidate artifacts with complete provenance. A missing valid
track is an ASR-planning handoff, never an automatic model fallback.

The primary user-facing contract is a `vcp subtitles` CLI workflow. Its first
operation reports candidate processing or a terminal handoff state from a
confirmed RunPlan. Its explicit selection operation resumes only a retained
ambiguous candidate report with per-Part stream selections. Both operations
emit machine-readable results and retain their workspace evidence.

## User Stories

1. As a media user, I want subtitle processing to require a confirmed RunPlan, so that it never reads an unplanned source.
2. As an auditor, I want SourceArtifact hashes, FFmpeg identity, and subtitle rules revalidated before processing, so that each candidate artifact has exact provenance.
3. As a media user, I want every embedded subtitle candidate retained separately, so that I can inspect why one track was selected or rejected.
4. As a media user, I want only embedded subtitle streams used in Phase 4, so that the pipeline does not discover sidecars or access external caption URLs.
5. As a Chinese-language user, I want UTF-8 and BOM-marked UTF-16 accepted deterministically, so that supported subtitle text is preserved faithfully.
6. As an auditor, I want ambiguous non-UTF-8 bytes reported rather than guessed, so that an encoding choice cannot silently alter source text.
7. As a media user, I want to explicitly choose a decoder for an ambiguous payload, so that legitimate legacy subtitles can still be used with recorded intent.
8. As a media user, I want image subtitles and unsupported styled subtitle formats reported as unavailable, so that the pipeline does not invent OCR or approximate text.
9. As a media user, I want a failed cue to invalidate its complete track, so that a partial transcript is never presented as a valid source subtitle.
10. As a user of media with negative PTS or edit lists, I want cue time mapped through PartRelativeTime to RawPtsTime without scaling, so that subtitle timing remains evidence-backed.
11. As a media user, I want cue validity checked against all usable audio and video coverage, so that legal audio-only lead-ins and video-only tails are not incorrectly rejected.
12. As an auditor, I want subtitle coverage gaps retained, so that container duration never hides missing media evidence.
13. As a user with one valid subtitle track, I want it selected automatically, so that ordinary cases complete without extra interaction.
14. As a user with several valid but indistinguishable tracks, I want the pipeline to wait for my explicit Part and stream selection, so that it never chooses by stream order or an unsafe default.
15. As an auditor, I want an explicit selection retained without mutating the RunPlan, so that plan authority and later user preference remain distinct evidence.
16. As a reader, I want a source subtitle artifact that preserves source text and supported WebVTT settings, so that I can cite the selected track accurately.
17. As a reader, I want an SRT compatibility projection with recorded layout losses, so that format limits are visible rather than silent.
18. As a reader, I want a readable subtitle artifact that removes only approved visual markup and proven rolling-display overlap, so that readability improvements do not rewrite speech.
19. As an auditor, I want every readable transformation linked to cue and token provenance, so that removed text is reviewable.
20. As a user of mixed Chinese and English captions, I want whitespace, punctuation, case, and Unicode code points preserved, so that normalization does not alter language content.
21. As a user of a multi-Part collection, I want completed Parts published as candidate artifacts when another Part lacks a usable track, so that good evidence is not discarded.
22. As an auditor, I want unavailable Parts to contribute no invented cue or silence label, so that a partial collection remains truthful.
23. As a media user, I want a caption-time coverage metric with an explicit audio-completeness limitation, so that subtitle display duration is not mistaken for a complete transcript.
24. As a media user, I want missing subtitles to produce an ASR-planning handoff rather than automatic ASR, so that model work remains explicitly authorized.
25. As an operator, I want a disk preflight and per-candidate output limit, so that an anomalous subtitle stream cannot fill the workspace.
26. As an auditor, I want interrupted and failed extraction attempts retained but excluded from selection, so that retry behavior cannot mistake partial bytes for source evidence.
27. As a maintainer, I want the `vcp subtitles` CLI contract exercised on synthetic evidence, so that the full observable workflow is repeatable offline.
28. As a product owner, I want Phase 4 to remain unvalidated for production, so that synthetic engineering proof is not confused with real-media quality acceptance.

## Implementation Decisions

- Add a `vcp subtitles` CLI boundary with a report-producing processing operation and a separate explicit selection-resume operation. JSON responses expose the phase state, candidate report identity, diagnostics, and retained artifacts without requiring interactive guessing.
- Keep Phase 4 below RunBundle publication. All raw payloads, complete or incomplete extraction attempts, reports, diagnostics, source/readable candidates, and correction records live in a Subtitle candidate workspace under the existing project-owned work area.
- Build Phase 4 persistence around immutable candidate reports and append-only selection records. Candidate state includes valid, invalid, unavailable, encoding ambiguous, awaiting selection, completed, partial, and ASR-required outcomes.
- Reuse the immutable RunPlan, SourceArtifact, Pinned external tool, ProbeDocument, exact time, StreamCoverage, RawCue, NormalizedCue, and PresentationCue concepts. Do not mutate a RunPlan to reflect later selection or processing evidence.
- Revalidate SourceArtifact hashes, the configured FFmpeg identity, and a versioned subtitle rules fingerprint before each extraction or selection-resume transition. Drift blocks the transition and requires a new planning attempt.
- Extract only embedded SRT, WebVTT, and `mov_text` payloads using argv-only FFmpeg. Treat PGS, VobSub, ASS/SSA, and all external or discovered caption sources as unavailable in this phase.
- Create unique Subtitle extraction attempts. Promote only complete, SHA-256-recorded, size-bounded payloads to parseable candidates; retain interrupted, timed-out, and 256 MiB-limited attempts as diagnostics without overwrite or reuse.
- Decode BOM-marked UTF-8/UTF-16 and strict UTF-8 automatically. Record a user-selected decoder for other bytes and reject lossy replacement or charset-guessing behavior.
- Interpret extracted cue time as PartRelativeTime and derive RawPtsTime by exact translation from the Part coverage start. Derive Part playback coverage from the union of usable audio/video DecodedIntervals; do not use duration metadata, scale time, or fill gaps.
- Validate every candidate atomically. Select a unique valid candidate automatically only when evidence supplies a unique outcome; otherwise transition to `awaiting_subtitle_selection` and accept only explicit `part-id=stream-index` choices.
- Make source VTT the normalized authoritative export. Produce SRT as a compatibility projection that records unrepresentable layout settings as `format_projection_loss` while retaining text, time, and cue order.
- Restrict character-preserving normalization to newline conversion. Restrict readable cleanup to closed `b`, `i`, `u`, and `font` tags and existing exact rolling-overlap proof; retain all other markup with `unhandled_markup` diagnostics.
- Support partial collections. Completed Parts retain their CollectionVirtualTime; unavailable Parts have no fabricated subtitle content and transition to `subtitle_unavailable_requires_asr_plan` without ASR estimates or model actions.
- Calculate caption-time coverage from the union of valid cue intervals, counting overlap once. Always emit `audio_completeness=not_verified` and retain risk, codec, decoder, hashes, selection, coverage, markup, projection-loss, and correction evidence.

## Testing Decisions

- Use the new `vcp subtitles` CLI as the highest primary seam. Tests observe only JSON states, workspace artifacts, immutable report transitions, and no-side-effect guarantees, rather than private helper ordering.
- Exercise the full CLI contract from an already confirmed synthetic RunPlan through extraction, validation, automatic completion or `awaiting_subtitle_selection`, explicit selection-resume, partial collection behavior, and ASR-required handoff.
- Use controlled FFmpeg substitutes for command construction, tool drift, timeout, interrupted-output, size-limit, and unsupported-codec behavior. A bounded fixture-backed integration test may use the existing pinned FFmpeg only on retained project-owned synthetic media.
- Add focused unit tests for strict decoding, character preservation, exact PartRelativeTime-to-RawPtsTime translation, playback-coverage union and gaps, atomic candidate rejection, rolling overlap, markup whitelist, and SRT projection loss.
- Follow the repository's existing `vcp plan` CLI-contract tests as the primary precedent for public behavior. Reuse existing Phase 2 timecode, coverage, subtitle parser, timeline, and fixture tests as lower-level precedent.
- Run every Python test command only after activating `.venv` and passing the project environment gate. Finish with the full test suite, Ruff check, formatter check, and Mypy.
- No test may access user media, a live URL, browser state, models, paid APIs, or a network connection. Tests must preserve failed evidence and never perform automatic cleanup.

## Out of Scope

- External subtitle URLs, sidecar discovery, network access, OCR, ASS/SSA,
  image subtitles, ASR, alignment, VAD, diarization, models, RunBundles,
  output publication, user media, and production validation.

## Further Notes

- This PRD synthesizes the completed Phase 4 grilling decisions and uses the
  canonical language routed by the [Context Map](../../CONTEXT-MAP.md).
- ADR 0025 fixes the strict revalidation boundary. The candidate workspace is
  intentionally not a RunBundle; a future separately authorized publication
  stage can promote only
  already verified artifacts.
- The implementation remains dependency-free unless a future, separately
  approved decision changes that boundary.
- Every implementation task remains TDD-first and must update the Phase 4
  inventory. The phase is `specification_ready`, not implemented.
