# 03 — Build the synthetic fixture generator and the five fixture branches

**What to build:** Test-support code (in `tests/support/`, not production)
that turns versioned Fixture recipes into tiny media files via the host
ffmpeg identity-pinned in `config/tools.json`. Identity verification runs
before first use: absent or mismatched binaries are a **test error, never a
skip**. Fixtures are seconds long and low resolution (e.g. lavfi
testsrc+sine, ≤ 8s, ~160x120), generated once per test session into a
session-scoped cache directory (pytest `tmp_path_factory`), never committed
— the repository stays free of media binaries. Five recipe branches
mirroring Phase 11's mandatory real-video branches: (1) subtitle track
muxed in (subtitle-first); (2) audio only, no subtitle track (full-ASR);
(3) anomalous subtitles — rolling repeats and drifting timestamps written
as a crafted subtitle file then muxed; (4) multi-Part collection (2–3
files); (5) text-bearing frames via drawtext (visual-text). Integration
tests (marked `integration`) execute the real ffprobe against each fixture
and verify the probed structure matches the recipe's expectations.

**Blocked by:** 01
**Status:** open
**Labels:** ready-for-agent

- [ ] Five recipes generate successfully via the pinned host toolchain
- [ ] Identity mismatch/absence produces a test error (proven with a fake
      tools.json), not a skip
- [ ] Real ffprobe executes in-test and structure assertions pass per branch
- [ ] Generation is session-cached (second use in one session regenerates
      nothing) and writes only under the session temp root
- [ ] No media binary enters the repository; suite green within budget

## Comments
