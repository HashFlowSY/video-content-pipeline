# 04 — Implement the OCR resource confirmation pause and resume

**What to build:** The two-gate execution flow: after detection the attempt
stops at the OCR resource confirmation pause, and `vcp resume-visual-text
<report-id> --decision <decision>` is the only way forward — so that a user
approves the heavy OCR step knowingly, keeps the page index at zero cost when
declining, and a run exceeding its approved envelope pauses instead of
silently degrading.

**Blocked by:** 02, 03 — the pause presents detection results and a decline
must retain the page index.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] After detection, the attempt records an immutable OCR resource
  confirmation pause presenting selected frame counts and conservative time,
  memory, and disk estimates; OCR never starts without an explicit
  affirmative decision.
- [ ] `resume-visual-text` requires a retained report ID and an explicit
  decision; it never auto-resumes or changes identity-bound inputs.
- [ ] A declining decision yields a `partial` report with the page index and
  frame inventory retained and zero visual facts.
- [ ] A planned attempt exceeding the approved resource envelope records an
  immutable Visual-text resource-envelope pause and never silently alters
  candidate, resolution, or batch.
- [ ] Serialized OCR execution: the OCR stage completes its evidence record
  and release before any other heavy model may load.
