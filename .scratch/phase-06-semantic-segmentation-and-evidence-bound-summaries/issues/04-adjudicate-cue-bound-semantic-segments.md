# 04 -- Adjudicate cue-bound semantic segments

**What to build:** Valid model-proposed cue-pair boundaries become formal
SemanticSegments with exactly-once PresentationCue ownership.

**Blocked by:** 03 -- Version text generation and rendering contracts.

**Status:** done
**Labels:** ready-for-agent

- [x] Permit final boundaries only between PresentationCues; reject duplicate,
  empty, out-of-range, and coverage-breaking candidates.
- [x] Deduplicate overlapping technical-block candidates by complete cue ID and
  preserve Part boundaries.
- [x] Use only the one-segment-per-Part conservative fallback when no valid
  candidate remains, retaining reason and `partial` status.

Implemented in `src/video_content_pipeline/text_segmentation.py` with unit
coverage in `tests/unit/test_text_segmentation.py`. The deterministic adjudicator
is a pure module consumed by later tickets (content validation in 05, chapter and
collection aggregation in 06); no `analyze_text` wiring exists until a generating
adapter produces candidate boundaries.
