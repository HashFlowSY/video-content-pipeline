# 08 — Implement the host-read comment upgrade

**What to build:** The Host-read comment upgrade (ADR 0049), owned by
text-analysis — so that a background-UI comment the host explicitly selected
or read aloud can become formal evidence with its page time and selection
basis recorded, while ordinary chat and danmaku stay non-evidence.

**Blocked by:** 07 — the upgrade runs inside affected-Part re-analysis where
cue and OCR evidence are both available.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] A background-UI OCR item is upgraded to formal evidence only when
  cross-modal comparison with cue text shows the host explicitly selected or
  read it; the upgrade record carries the page time and the selection basis.
- [ ] Items that fail the comparison remain background UI and never enter
  formal content; the decision is deterministic under versioned rules
  recorded in provenance.
- [ ] Visual-text itself performs no upgrade: the visual-side report is
  unchanged by this ticket, and the upgrade record lives in the text-analysis
  report.
- [ ] Upgraded comments carry citations to both the OCR evidence item and the
  supporting cues.
