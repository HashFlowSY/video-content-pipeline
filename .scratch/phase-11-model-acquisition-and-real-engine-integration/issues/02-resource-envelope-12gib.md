# 02 — Shrink the shared resource envelope to 12 GiB

**What to build:** Change `MAX_MODEL_RESOURCE_BYTES` (capabilities module)
from 24 GiB to 12 GiB and move every dependent surface with it: docstrings
and pause messages in text-analysis, transcription, visual-text, and
audio-analysis; plan-estimation comparisons; and every test that asserts
the number or its rendered messages. The `resource_envelope_exceeded`
retained-pause behavior itself is contract-stable — only the threshold
moves.

**Blocked by:** —
**Status:** open
**Labels:** ready-for-agent

- [ ] The constant is 12 GiB and is the single source of truth (no other
      literal 24-GiB remnants in src)
- [ ] All pause/status messages and docstrings state 12 GiB
- [ ] A conservative estimate between 12 and 24 GiB now pauses (new test
      proves the tightened boundary)
- [ ] Full suite green
