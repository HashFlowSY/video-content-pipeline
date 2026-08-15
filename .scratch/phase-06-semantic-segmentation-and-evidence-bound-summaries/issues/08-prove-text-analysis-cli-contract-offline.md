# 08 -- Prove text-analysis CLI contract offline

**What to build:** Maintainers have repeatable proof that the complete Phase 6
public contract retains correct evidence and makes no external side effects.

**Blocked by:** 04 -- Adjudicate cue-bound semantic segments; 05 -- Validate evidence-bound segment content; 06 -- Aggregate Part-local summaries; 07 -- Retain text-attempt state and diagnostics.

**Status:** resolved
**Labels:** ready-for-agent

- [ ] Cover the approved offline fixture matrix, including mixed-language,
  overlap, multi-Part, invalid projection/citation, fallback, unavailable,
  drift, pause, and diagnostic states.
- [ ] Prove JSON authority, deterministic Markdown rendering, immutable
  workspaces, exactly-once ownership, and no mutation or `outputs/` writing.
- [ ] Run the project environment gate, full test suite, Ruff, formatter, and
  Mypy; record only `passed_offline` and leave production validation false.
