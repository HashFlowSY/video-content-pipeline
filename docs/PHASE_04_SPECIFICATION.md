# Phase 4 Specification: Subtitle-Track-Priority Pipeline

## Domain routing

Begin with the [Context Map](../CONTEXT-MAP.md), then read the required
[Media Foundation](contexts/media-foundation/CONTEXT.md), [Source
Planning](contexts/source-planning/CONTEXT.md), and
[Subtitles](contexts/subtitles/CONTEXT.md) Contexts. Subtitle terms are owned
by Subtitles; shared clocks, coverage, and plans are linked from their owners.

## Status

`completed_and_verified_offline`. Implementation and the Phase 4 CLI contract
are complete. Verification remained restricted to project-owned synthetic
fixtures and controlled tools; it did not access user media, use a network,
download models or dependencies, or mark the project `production_validated`.

## Objective

From a confirmed RunPlan, create auditable source and readable subtitle
candidate artifacts without performing ASR. The phase accepts only embedded
text subtitle payloads and never claims audio or transcript completeness.

## Processing Contract

- Start only after SourceArtifact hashes, pinned FFmpeg identity, and the
  versioned subtitle rules configuration revalidate exactly. Drift blocks the
  attempt and requires a fresh plan; RunPlans are never mutated.
- Extract all embedded candidate payloads into unique, hash-recorded attempts
  below `work/<source-id>/<subtitle-run-id>/`. Complete attempts are immutable;
  interrupted, timed-out, and size-limited attempts remain `incomplete`
  diagnostics and cannot be parsed or selected.
- Support only SRT, WebVTT, and `mov_text`. Image subtitles (including PGS and
  VobSub) and ASS/SSA are retained as unavailable evidence without OCR or
  approximate conversion.
- Decode only BOM-marked UTF-8/UTF-16 or strictly valid UTF-8 automatically.
  Other byte sequences are `encoding_ambiguous` until the user records an
  explicit decoder selection. No character replacement or guessed decoding is
  permitted.
- Map extracted cue time from PartRelativeTime to RawPtsTime solely by adding
  the Part coverage start. Validate against Part playback coverage, the union
  of usable audio and video DecodedIntervals. No scaling, drift correction,
  metadata-duration fallback, or gap filling is permitted.
- A candidate is atomic: one invalid cue invalidates its track. Extract and
  validate candidates independently. Select the highest-ranked valid track
  only when retained evidence makes it unique; otherwise report
  `awaiting_subtitle_selection` and require `part-id=stream-index` input.
- A collection can be partial. Completed Parts keep their existing
  CollectionVirtualTime; unavailable Parts contribute no invented cue,
  silence marker, or placeholder text and report
  `subtitle_unavailable_requires_asr_plan`.

## Artifact Contract

- Preserve each raw payload as audit evidence. `subtitles.source.vtt` is the
  authoritative normalized export: all supported WebVTT settings and tags are
  retained. `subtitles.source.srt` is a compatibility projection and records
  every unrepresentable layout setting as `format_projection_loss`.
- Normalization changes decoded line endings to LF only. It preserves every
  other code point, whitespace, punctuation, case, cue time, and cue order.
- `readable` can remove only closed `b`, `i`, `u`, and `font` tags and only
  token ranges proven to be rolling-display overlap. All other markup is
  retained and diagnosed. Every readable change is recorded against source cue
  and token provenance.
- Do not write `outputs/` in this phase. Store raw payloads, extraction logs,
  reports, candidate artifacts, diagnostics, and correction logs only in the
  Subtitle candidate workspace. A future, separately authorized publication
  stage may promote, but not recreate or rewrite, these artifacts.

## Reporting And Resource Contract

- Before extraction, estimate candidate-workspace growth from retained packet
  evidence and require free space for estimated growth plus the greater of
  1 GiB or five percent. Limit each candidate extraction to 256 MiB; a breach
  is retained as `extraction_size_limit`.
- Record subtitle origin, source and output hashes, codec, encoding decision,
  selected stream or ambiguity, coverage, markup diagnostics, projection loss,
  correction provenance, and risks.
- Report `caption_time_coverage` as the union of valid cue duration divided by
  Part playback coverage duration, with overlap counted once. Always report
  `audio_completeness=not_verified`; do not infer silence, missed speech,
  transcript completeness, or accuracy.
- When no valid Primary subtitle track remains, report
  `subtitle_unavailable_requires_asr_plan` without estimating, configuring,
  downloading, or running ASR.

## Test Contract

Write failing tests before each behavior. Use retained synthetic media and
controlled FFmpeg/tool substitutes to cover text and unavailable codecs,
encoding ambiguity, candidate atomicity, choice ambiguity, negative PTS,
multi-stream coverage gaps, rolling overlap, readable markup, format loss,
capacity limits, interrupted attempts, and partial collections. Run the
project environment gate, test suite, Ruff checks, formatter check, and Mypy
after implementation. No live URL, user media, model, or network action is a
test input.
