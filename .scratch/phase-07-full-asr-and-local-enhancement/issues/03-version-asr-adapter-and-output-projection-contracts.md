# 03 -- Version Controlled offline ASR adapters and output projections

**What to build:** Versioned Controlled offline ASR adapter descriptors
(mirroring the Phase 6 text adapter: hash-pinned input and output fixtures,
implementation and schema versions) and the versioned ASR output projection
that is the only entry point for ASR text.

**Blocked by:** 01

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Define adapter descriptor JSON under `config/transcription/` with
  version, fixture hashes, and projection-schema version; verify hashes on
  load, project-relative non-escaping paths only.
- [ ] Define the ASR output projection: cues with exact rational times, text,
  optional per-token confidence, language spans; incomplete or schema-invalid
  output is `model_output_invalid` and fails the attempt.
- [ ] Retain raw output as restricted local audit evidence, excluded from
  formal reports.
- [ ] Symmetric input hashing (input manifest sha256) proving the fixture
  matches revalidated inputs.
