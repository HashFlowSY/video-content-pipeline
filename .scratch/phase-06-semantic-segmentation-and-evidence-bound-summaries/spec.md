# Phase 6: Evidence-Bound Semantic Segmentation and Summaries

Type: enhancement
Status: ready-for-agent
Labels: ready-for-agent
Phase: 6
Published: 2026-08-13

## Problem Statement

After Phase 5, the pipeline retains subtitle and optional audio-analysis
evidence but cannot produce auditable semantic segments or detailed summaries.
Generated prose must not become an uncited authority, and Phase 6 must prove
its contracts without acquiring or invoking a real text model.

## Solution

Add `vcp analyze-text` and `vcp resume-text-analysis`. The phase creates
immutable Text analysis reports in project workspaces from revalidated confirmed
plans and subtitle reports. A Controlled offline text adapter proves the public
contract with synthetic fixtures. Every formal fact and title remains bound to
NormalizedCue IDs; JSON is authoritative, Markdown is a deterministic workspace
rendition, and Phase 9 remains solely responsible for publication.

## Non-Negotiable Boundaries

- No model/runtime/dependency download, real-model call, user media, network,
  external knowledge, `outputs/` write, or production claim.
- PresentationCues are model input; NormalizedCue IDs are factual citations.
- Segments and chapters stay within one Part; each PresentationCue has exactly
  one segment owner; a cited cue can support multiple claims.
- Invalid overall projection fails the attempt; invalid individual output is
  retained diagnostic evidence while independently verified content continues.
- Subtitle-unavailable Parts have `text_content=unavailable`, not invented
  content. Subtitle-derived reports always retain
  `audio_completeness=not_verified`.

## Acceptance Standard

All Phase 6 tickets are complete only when the controlled offline CLI contract,
synthetic fixture matrix, full tests, Ruff, formatter check, and Mypy pass in
the project virtual environment. The result may only be `passed_offline`.

See [Phase 6 specification](../../docs/PHASE_06_SPECIFICATION.md) and
[atomic task list](../../plans/PHASE-06/ATOMIC_TASK_LIST.md) for the complete
accepted design.
