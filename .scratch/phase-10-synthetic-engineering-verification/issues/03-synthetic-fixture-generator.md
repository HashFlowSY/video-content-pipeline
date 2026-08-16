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
**Status:** done (`90aadc5`)
**Labels:** ready-for-agent

- [x] Five recipes generate successfully via the pinned host toolchain —
      `FIXTURE_RECIPES` (subtitle-first / full-asr / anomalous-subtitles /
      multi-part / visual-text) built through `resolve_fixture_toolchain` →
      `generate_fixture` (`test_branch_generates_and_probes_as_declared`)
- [x] Identity mismatch/absence produces a test error (proven with a fake
      tools.json), not a skip — four fake-registry tests assert
      `FixtureToolchainError` (`tool_absent`, `tool_identity_mismatch`,
      `tool_entry_missing`, `tool_evidence_incomplete`); the verifier never
      calls `pytest.skip`
- [x] Real ffprobe executes in-test and structure assertions pass per branch —
      `probe_stream_types` runs the pinned ffprobe; each branch is checked
      against its declared `expected_streams`
- [x] Generation is session-cached (second use in one session regenerates
      nothing) and writes only under the session temp root — `.complete` marker
      drives `regenerated=False` (`test_second_generation_reuses_the_cache`);
      `is_relative_to(fixture_cache)` + `test_cache_is_versioned` confine output
- [x] No media binary enters the repository; suite green within budget — output
      is tmp-only and untracked; full suite 1067 passed in ~5s

## Comments

- Delivered as `90aadc5`. The generator is plain importable support code (no
  pytest, no conftest); the session-scoped caching lives in the consuming test,
  matching [[phase-8-grilled-and-specced]]'s zero-conftest convention. It reuses
  `external_tools.identify_external_tool` so the fixture toolchain check and
  production tool-pinning capture identity byte for byte.
- **Toolchain gap surfaced:** the pinned host ffmpeg 9.0.1 is built without
  libfreetype/libass, so the `drawtext` filter the ticket names for the
  visual-text branch (and the `subtitles` burn-in filter) are unavailable. The
  branch instead uses `testsrc`, whose built-in vector font burns a frame
  counter (genuine rendered digit glyphs) into every frame — a real
  text-bearing render from the pinned toolchain. OCR-level verification of the
  text *content* is the downstream visual-text flow's job (E2E tickets), not the
  fixture generator's; the structural assertion plus ffmpeg's fail-on-filter-error
  behaviour is the guard here.
- Two-axis code review (Standards + Spec) ran clean of correctness/standard
  issues; applied fixes were quality-only: a shared `probe_document` argv (was
  duplicated in the test), named unpacking in `resolve_fixture_toolchain`,
  dropped an unused `generate_all_fixtures` (YAGNI), and an added visual-text
  render assertion.
