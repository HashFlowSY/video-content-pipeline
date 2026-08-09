# Phase 3 Specification: Source Intake, Planning, and Resource Estimation

## Status

Completed and verified in the project-local offline environment. This phase is
an engineering capability only; `real_world_testing` and
`production_validated` remain false.

## Objective

Build an auditable, dependency-free source-intake and planning boundary that
creates immutable plans from an explicitly authorized local media file or
public URL. It must snapshot input bytes, preserve Phase 2 probe and coverage
evidence, estimate complete decode validation before it starts, and never infer
authority, access a network host implicitly, or download models.

## In Scope

- `vcp plan <local-file>`, `vcp plan <public-url> --url-mode`, and interactive
  `vcp plan --collect --url-mode` entry points.
- Regular-file validation, pre-copy hashing, project-local snapshotting,
  post-copy hashing, SHA-256 content addressing, and duplicate reuse.
- Public URL policy enforcement, redacted provenance, controlled external tool
  invocation, and project-local cache and temporary paths.
- Strict structural and packet-level FFprobe evidence retained for each
  SourceArtifact, typed projection, exact coverage, and metadata-only subtitle
  track enumeration.
- Estimated and user-confirmed full FFmpeg decode validation to null output.
- Immutable PlanReports, Report revalidation, Plan confirmation, RunPlans,
  deterministic disk headroom checks, and Phase-bounded estimates.
- Offline automated tests using retained synthetic media and controlled tool
  substitutes only.

## Explicitly Out Of Scope

- ASR, forced alignment, VAD, diarization, OCR, LLMs, model downloads, or
  model-management implementation.
- Subtitle text acquisition, parsing, selection, normalization, or output.
- Stage 4 processing, `vcp run`, RunBundles, or production validation.
- Browser cookies, credentials, login state, private links, paid APIs,
  playlist discovery, automatic URL fallback, or real-network test fixtures.
- New Python runtime dependencies, lockfile changes, global installation,
  automatic cleanup, or deletion of media, cache, diagnostics, or reports.

## Domain Contracts

### Source Authority And Identity

- A Source access authorization covers exactly one explicit local path or one
  explicit public URL in one planning attempt.
- Local input must be a regular file. Directories, symlinks, devices, named
  pipes, and standard input are rejected.
- A SourceArtifact is copied below `input/<source-id>/` only after a source
  hash, a copy, and a matching destination hash. The original is never moved
  or modified. A changed source rejects the attempt.
- `source-id` is derived from a SHA-256 digest. Identical content reuses the
  existing SourceArtifact. A Duplicate Part blocks a collection rather than
  being silently retained or collapsed.
- Only a SourceArtifact with a usable audio or video stream and determinate
  required StreamCoverage is Media-qualified.

### URL And Collection Authority

- Every URL uses explicit `filtered` or `direct` mode. Omitted, incompatible,
  or failed mode is rejected and never falls back.
- HTTPS is required unless `--allow-insecure-http` explicitly authorizes an
  initial HTTP URL; the PlanReport then records unverified transport integrity.
- The initial host is the sole authorized host. A redirect, discovered media
  host, or HTTPS downgrade is a Host escalation requiring fresh confirmation.
- Raw URLs are process-local only. Persistent provenance stores scheme, host,
  and path without query or fragment plus the acquired SourceArtifact hash.
- `--collect` asks users to submit links strictly in presentation order and
  closes only on `结束`. It performs local syntax and duplicate checks while
  collecting, then accesses the batch only after closure.

### Evidence And Full Decode Validation

- FFprobe and FFmpeg are Pinned external tools. Path, version, and content
  hash are captured before use and revalidated before confirmation.
- Structural ProbeDocuments and packet-level Coverage ProbeDocuments are
  retained unchanged. Typed projections retain the Phase 2 no-guessing rule.
- SubtitleTrackCandidate records only identity, language, origin, container
  format, and availability. It does not contain subtitle text.
- Full decode validation decodes every audio and video stream linearly to null
  output, writes no derived media, and begins only after Decode preflight
  confirmation of a displayed three-point estimate.
- Decode estimates use matching observed history where available; otherwise a
  versioned Decode throughput profile marks all three values `low` confidence.

### Planning Artifacts

- Every attempt writes `plans/reports/<report-id>/plan-report.json` and its
  diagnostics. A blocked report is audit evidence, not an executable plan.
- Only a successful final report may receive Plan confirmation. Confirmation
  creates `plans/<plan-id>/run-plan.json` without mutating the report.
- Confirmation revalidates SourceArtifact hashes, external tool identities,
  Disk headroom, collection and URL configuration, and profile versions.
  Any mismatch invalidates the report and requires a fresh attempt.
- Required free space is deterministic planned growth plus
  `max(1 GiB, growth * 5%)`. Failure writes a blocked report before acquisition.
- Phase 3 estimates cover source acquisition, hashing, probing, packet
  evidence, full decode, and disk growth. Later model stages are explicitly
  `unavailable/not_estimated` rather than fabricated values.

## CLI Contract

```text
vcp plan <local-file>
vcp plan <public-url> --url-mode filtered|direct [--allow-insecure-http]
vcp plan --collect --url-mode filtered|direct [--allow-insecure-http]
vcp plan decode <report-id>
vcp plan confirm <report-id>
```

Each command supports machine-readable `--json` output. `decode` authorizes
only the pre-announced full decode validation; `confirm` creates a plan ID only
after all final evidence is still valid.

## Implementation Shape

The implementation remains standard-library-only. Proposed modules are:

| Path | Responsibility |
| --- | --- |
| `source.py` | Local candidate validation, hashing, snapshot creation, SourceArtifact persistence |
| `external_tools.py` | Pinned external tool identity and argv-only invocation |
| `inspection.py` | Structural and packet ProbeDocuments, projections, coverage, subtitle metadata |
| `planning.py` | PlanReport, estimates, disk checks, decode and confirmation transitions |
| `url_policy.py` | URL mode, HTTPS/HTTP, redaction, host escalation, collection validation |
| `cli.py` | Phase 3 parser, JSON rendering, and interactive collection workflow |
| `config/decode-throughput-profiles.json` | Versioned low-confidence initial estimates |

## Test Matrix

| Area | Required Proof |
| --- | --- |
| Local candidates | Reject non-regular inputs; detect copy-time change; duplicate content reuse |
| Source artifacts | SHA-256 identity, immutable persistence, no original mutation |
| URL policy | Explicit mode, HTTP opt-in, redaction, host escalation, no fallback |
| Collections | Ordered manual input, `结束` closure, duplicate URL and content rejection |
| Tool identity | Captured path/version/hash; mismatch blocks revalidation |
| Inspection | Structural and packet documents, typed projection, exact coverage, subtitle metadata only |
| Decode preflight | Low-confidence profile, confirmation gate, null-output command construction |
| Planning | Disk headroom, blocked reports, separate report/plan IDs, stale-report rejection |
| CLI | Entry modes, JSON output, decode and final confirmation command boundaries |
| Regression | Existing Phase 1 and Phase 2 tests remain green |

## Approved Command Envelope

Before each Python command, activate `.venv` and pass
`scripts/require-project-venv.sh`. Expected checks are:

```text
pytest -q tests/unit tests/acceptance
pytest -q
ruff check src tests
ruff format --check src tests
mypy src
```

Phase 3 tests use controlled substitutes by default. A separately announced,
bounded fixture-backed integration check may invoke the already approved
project-external FFprobe/FFmpeg binaries only on retained synthetic fixtures;
it never reads user media or accesses a network URL. No package installation,
model download, yt-dlp invocation, or live network request is part of normal
verification.

## Retention And Rollback

All reports, plans, source snapshots, diagnostics, retained packet evidence,
and failed outputs are retained. No cleanup is part of Phase 3. Source and
documentation changes are reversible through normal version control; generated
artifacts remain until explicitly authorized for deletion.
