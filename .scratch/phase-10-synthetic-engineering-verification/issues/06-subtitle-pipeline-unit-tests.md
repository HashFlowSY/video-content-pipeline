# 06 — Give subtitle_pipeline.py its dedicated unit test file

**What to build:** `src/video_content_pipeline/subtitle_pipeline.py`
currently has no dedicated unit test file (coverage is indirect). Create
`tests/unit/test_subtitle_pipeline.py` covering its public functions
directly: candidate retention and atomic validation behavior, Primary
track selection rules, source/readable derivation, and edge cases —
empty tracks, overlapping cues, rolling-overlap proof boundaries,
mixed-language cues. Follow the audit before writing: enumerate the
module's public surface, map which behaviors existing tests already pin
indirectly, and target the genuinely unpinned ones (state the mapping in
the test module docstring so the gap-fill is auditable).

**Blocked by:** —
**Status:** open
**Labels:** ready-for-agent

- [ ] Docstring maps public surface → previously-pinned vs newly-pinned
- [ ] Every public function of the module has at least one direct test
- [ ] Edge cases above covered
- [ ] Suite green

## Comments
