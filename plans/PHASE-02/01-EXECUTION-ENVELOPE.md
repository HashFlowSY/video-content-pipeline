# Phase 2 Execution Envelope: Ticket 01

Status: awaiting explicit approval
Date: 2026-08-08

## Decision Requested

Approve only the initial unit-work increment in this document. That increment
implements exact time primitives for ticket 02 and runs the listed project-
local checks. It does not authorize fixture generation, FFmpeg, FFprobe,
package actions, downloads, user-media access, source intake, or public CLI
changes.

Approval of the initial increment does not approve the separately gated
fixture work described below. Ticket 09 must receive its own approval before
any fixture recipe, FFmpeg, FFprobe, or fixture-backed integration command is
run.

## Initial Unit-Work Increment

### Intended Changes

| Path | Change | Boundary |
| --- | --- | --- |
| `src/video_content_pipeline/timecode.py` | Create internal exact rational-time values, signed `RawPtsTime`, half-open intervals, and exact comparisons needed by ticket 02. | No floats as canonical values; no media I/O or CLI entry point. |
| `tests/unit/test_timecode.py` | Create failing-first unit tests for rational arithmetic, negative raw PTS preservation, interval validity, and exact ordering. | Synthetic in-memory values only; no fixtures or subprocesses. |
| `docs/PHASE_02_INVENTORY.json` | Record the resulting source, test, command, and resource evidence after execution. | Audit record only. |
| `.scratch/phase-02-deterministic-media-core-and-timeline-prototype/issues/02-establish-exact-source-and-part-time.md` | Record the ticket result after its tests and quality checks pass. | Local tracker only. |

The existing public `vcp check-environment` behavior, package manifest,
lockfile, tool configuration, and all source-intake behavior remain unchanged.

### Approved-Only-If-Requested Commands

All Python-invoking commands use the already existing project virtual
environment. The setup block is required before each command invocation and
does not install or download anything.

```sh
export VCP_PROJECT_ROOT="/Users/shangyang/Desktop/workspace/projects/video-content-pipeline"
export UV_INSTALL_DIR="$VCP_PROJECT_ROOT/tools/uv"
export UV_CACHE_DIR="$VCP_PROJECT_ROOT/cache/uv"
export UV_PYTHON_INSTALL_DIR="$VCP_PROJECT_ROOT/runtime/python"
export UV_PROJECT_ENVIRONMENT="$VCP_PROJECT_ROOT/.venv"
export PIP_CACHE_DIR="$VCP_PROJECT_ROOT/cache/pip"
export TMPDIR="$VCP_PROJECT_ROOT/tmp"
source "$VCP_PROJECT_ROOT/.venv/bin/activate"
"$VCP_PROJECT_ROOT/scripts/require-project-venv.sh"
pytest tests/unit/test_timecode.py
ruff check src/video_content_pipeline/timecode.py tests/unit/test_timecode.py
ruff format --check src/video_content_pipeline/timecode.py tests/unit/test_timecode.py
mypy src
```

`pytest`, `ruff`, and `mypy` invoke Python from `.venv`; no system Python,
`uv run`, `pip install`, package resolution, or dependency update is allowed.
The Phase 2 full suite remains a later ticket-12 quality gate and is not part
of this increment: `pytest`, `ruff check .`, `ruff format --check .`, and
`mypy` will be submitted for approval before that gate runs.

### Resources, Duration, And Retention

- Estimated engineering time: 45-90 minutes.
- Peak memory: less than 256 MiB.
- Additional disk use: less than 10 MiB, limited to existing project-local
  Python-tool caches and test cache entries.
- Network, downloads, models, paid APIs, FFmpeg, FFprobe, and user media: zero.
- Source and test edits are reversible through version control. No cache,
  temporary data, failed test output, archive, or other artifact will be
  deleted by this increment.

## Separately Gated Fixture Plan

This section is a bounded proposal only. It grants no fixture-generation
authority and leaves the concrete corpus decision to ticket 09.

### Tool Identity And Command Shape

The proposed fixture toolchain is the already recorded, not-yet-used pair:

| Tool | Path | Recorded Version | Use |
| --- | --- | --- | --- |
| FFmpeg | `/opt/homebrew/bin/ffmpeg` | `8.1.2` | Generate project-owned synthetic media only. |
| FFprobe | `/opt/homebrew/bin/ffprobe` | `8.1.2` | Emit raw JSON evidence for the generated media only. |

No tool download or installation is proposed. Before any execution, ticket 09
must approve the final versioned recipe and ticket 10 must approve the exact
commands. The planned command form is a dedicated project-local script at
`scripts/generate-phase-02-fixtures.sh`, whose creation and execution are both
deferred. That script will invoke only the paths above, generate to a fresh
project-local work directory, and fail rather than overwrite a retained path.
For each accepted artifact it will use FFprobe JSON output (`-of json`) to
write the immutable `ProbeDocument` alongside the fixture.

### Proposed Retained Artifacts And Limits

| Path | Purpose | Maximum retained size |
| --- | --- | --- |
| `tests/fixtures/recipes/phase-02-offset-av-aac.json` | Versioned declarative audio/video offset recipe. | 64 KiB |
| `tests/fixtures/recipes/phase-02-aac-priming.json` | Versioned audio priming-boundary recipe. | 64 KiB |
| `tests/fixtures/phase-02-offset-av-aac.mp4` | Project-owned synthetic A/V fixture for differing stream starts. | 6 MiB |
| `tests/fixtures/phase-02-aac-priming.m4a` | Project-owned synthetic audio fixture for exact decoded intervals. | 2 MiB |
| `tests/fixtures/phase-02-offset-av-aac.ffprobe.json` | Raw FFprobe `ProbeDocument` for the A/V fixture. | 1 MiB |
| `tests/fixtures/phase-02-aac-priming.ffprobe.json` | Raw FFprobe `ProbeDocument` for the audio fixture. | 1 MiB |
| `tests/fixtures/phase-02-expected.json` | Fixture hashes, tool provenance, and expected typed evidence. | 256 KiB |
| `tests/fixtures/phase-02-rolling.srt`, `tests/fixtures/phase-02-out-of-range.srt`, `tests/fixtures/phase-02-roundtrip.vtt` | Project-owned text evidence for subtitle validation and serialization. | 64 KiB each |

The total retained fixture corpus is capped at 20 MiB. Fixture generation has
an estimated peak memory below 512 MiB and estimated duration below 10
minutes. Generated assets, probe documents, recipes, hashes, work outputs,
and failed outputs are retained; no cleanup command is authorized. Normal unit
and integration tests consume retained fixtures read-only, verify their hashes,
and never regenerate or delete them.

## Explicit Prohibitions Until Further Approval

- Do not run any command in the initial-unit-work block.
- Do not run FFmpeg, FFprobe, a fixture script, a package command, or a
  download.
- Do not create the proposed fixture script, recipes, fixture directory, or
  fixture artifacts.
- Do not access local files provided as media, URLs, browser data, credentials,
  models, or paid services.
- Do not introduce a user-media CLI command or mark the project
  `production_validated`.

## Approval Text

To authorize the next step, approve this exact scope: "Approve Ticket 01's
initial unit-work increment and its listed project-local Python checks only.
FFmpeg, FFprobe, fixture generation, packages, downloads, and user media stay
unapproved."
