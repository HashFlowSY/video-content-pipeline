# 02 — Shrink the shared resource envelope to 12 GiB

**What to build:** Change `MAX_MODEL_RESOURCE_BYTES` (capabilities module)
from 24 GiB to 12 GiB and move every dependent surface with it: docstrings
and pause messages in text-analysis, transcription, visual-text, and
audio-analysis; plan-estimation comparisons; and every test that asserts
the number or its rendered messages. The `resource_envelope_exceeded`
retained-pause behavior itself is contract-stable — only the threshold
moves.

**Blocked by:** —
**Status:** done
**Labels:** ready-for-agent

- [x] The constant is 12 GiB and is the single source of truth: only
      `capabilities.MAX_MODEL_RESOURCE_BYTES` declares the literal, and
      `text_analysis.TEXT_MODEL_RESOURCE_ENVELOPE_BYTES` now imports it (no
      other literal 24-GiB remnants in src)
- [x] All pause/status messages and docstrings state 12 GiB (audio_analysis,
      transcription, enhancement, text_analysis, capabilities)
- [x] A conservative estimate between 12 and 24 GiB now pauses: new
      `test_estimate_between_twelve_and_twenty_four_gib_pauses` (18 GiB) plus a
      `test_resource_envelope_is_twelve_gib` value lock
- [x] Full suite green (1342 passed); mypy + ruff clean
