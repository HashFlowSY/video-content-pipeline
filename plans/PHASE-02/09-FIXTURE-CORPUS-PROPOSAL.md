# Phase 2 Fixture Corpus Proposal: Ticket 09

Status: awaiting explicit fixture-generation approval
Date: 2026-08-09

## Decision Requested

Approve the retained, project-owned synthetic fixture corpus and the exact
command plan below. This proposal authorizes neither the creation nor the
execution of `scripts/generate-phase-02-fixtures.sh`; those actions remain
Ticket 10 work after the approval text at the end of this document is given.

No command in this proposal has run. No media, subtitle fixture, recipe,
directory, hash, probe document, package, download, user media, or cache has
been created or deleted by Ticket 09.

## Corpus And Contract Coverage

All generated media is derived solely from FFmpeg `lavfi` sources. It contains
test bars and sine tones, never user-provided or real-world media. The recipe
manifest will be declarative JSON with `schema_version: 1`, command arguments
as arrays, fixed output paths, and the expected evidence below.

| Fixture ID | Planned retained artifact | Recipe purpose | Contract evidence |
| --- | --- | --- | --- |
| `offset-av-aac` | `tests/fixtures/media/phase-02-offset-av-aac.mkv` | Four-second FFV1 video beginning at zero with AAC shifted by -21 ms. | Different video and audio starts, signed raw PTS, exact stream time bases, and raw `ProbeDocument` projection. |
| `gap-video` | `tests/fixtures/media/phase-02-gap-video.mkv` | Video frames at source intervals `[10, 11)` and `[13, 13.9)` only. | Decoded-interval outer coverage, preserved internal gap `[11, 13)`, nonzero source start, and compact mapping as a later Part. |
| `aac-priming` | `tests/fixtures/media/phase-02-aac-priming.m4a` | Two seconds of deterministic mono AAC. | Packet/frame-level AAC priming or skip-sample evidence without substituting duration metadata for an observed boundary. |
| `rolling-srt` | `tests/fixtures/subtitles/phase-02-rolling.srt` | Literal SRT evidence with rolling accumulation, an exact duplicate, and repeated speech. | Atomic parsing, stable order, exact rolling proof, exact-duplicate rule, and retained ambiguity. |
| `out-of-range-srt` | `tests/fixtures/subtitles/phase-02-out-of-range.srt` | Literal SRT cue whose endpoint exceeds `offset-av-aac` video coverage. | Atomic rejection and source-bound diagnostics. |
| `roundtrip-vtt` | `tests/fixtures/subtitles/phase-02-roundtrip.vtt` | Literal WebVTT with an identifier, setting, multiline text, and a one-millisecond cue. | Lossless parsing plus SRT/VTT outward-millisecond round trip. |

The retained manifest path is `tests/fixtures/recipes/phase-02-fixtures-v1.json`;
raw FFprobe JSON is retained under `tests/fixtures/evidence/`; the final
manifest is `tests/fixtures/phase-02-manifest.json`. Ticket 10 first creates
all new bytes under the fresh work directory
`tmp/phase-02-fixture-generation-20260809/`, verifies them there, then promotes
them to those retained paths without replacement. It will create neither work
nor retained paths until this proposal is explicitly approved.

## Tool Identity

The only permitted media tools are the already recorded pair from ADR 0001:

| Tool | Required path | Required version | Planned evidence path |
| --- | --- | --- | --- |
| FFmpeg | `/opt/homebrew/bin/ffmpeg` | `8.1.2` | `tests/fixtures/evidence/ffmpeg-version.txt` |
| FFprobe | `/opt/homebrew/bin/ffprobe` | `8.1.2` | `tests/fixtures/evidence/ffprobe-version.txt` |

Ticket 10 must abort before generating anything when either path is absent or
its first version line does not identify `8.1.2`. It records the complete
version output after this check; it may not substitute a different binary,
download a tool, or broaden the toolchain.

## Ticket 10 Proposed Writes

| Path | Planned write | Guard |
| --- | --- | --- |
| `scripts/generate-phase-02-fixtures.sh` | A project-local generator that reads the approved JSON recipe and invokes only the listed argument arrays. | Create and execute only after this proposal is approved; announce the script before execution. |
| `tests/fixtures/recipes/phase-02-fixtures-v1.json` | Versioned declarative recipes, literal subtitle payloads, command arrays, expected evidence, and output limits. | Fail if it or any retained output already exists. |
| `tmp/phase-02-fixture-generation-20260809/` | Fresh work tree containing generation log, recipe, artifacts, probe documents, and manifest before promotion. | Fail if it already exists; retain it and every failure without cleanup. |
| `tests/fixtures/media/` | The three listed project-owned media artifacts promoted from the verified work tree. | FFmpeg uses `-n`; no source path outside the project is an input. |
| `tests/fixtures/subtitles/` | The three literal subtitle artifacts promoted from the verified work tree. | Write exact UTF-8 LF bytes once; retain every result. |
| `tests/fixtures/evidence/` | Complete tool-version text and one raw FFprobe JSON document per media artifact, promoted from the work tree. | Capture unchanged stdout into a new path. |
| `tests/fixtures/phase-02-manifest.json` | SHA-256, byte counts, recipe version, and provenance for every retained artifact. | Promote only after every expected work-tree artifact and evidence has been verified. |

## Planned Commands

The following are commands for Ticket 10 only. They are shown exactly for
review; they have not been invoked. The generator must use argument arrays,
write only below the project root, fail on an existing retained output, and
never remove a work directory or failed output.

```text
/opt/homebrew/bin/ffmpeg -hide_banner -version
/opt/homebrew/bin/ffprobe -hide_banner -version
```

```text
/opt/homebrew/bin/ffmpeg -hide_banner -nostdin -n -f lavfi -i testsrc2=size=160x90:rate=10:duration=4 -f lavfi -i sine=frequency=1000:sample_rate=48000:duration=4 -filter_complex "[0:v]setpts=PTS-STARTPTS[v];[1:a]asetpts=PTS-STARTPTS-0.021/TB[a]" -map "[v]" -map "[a]" -c:v ffv1 -level:v 3 -c:a aac -b:a 64k -map_metadata -1 -metadata title=phase-02-offset-av-aac -metadata creation_time=1970-01-01T00:00:00Z -fflags +bitexact -flags:v +bitexact -flags:a +bitexact -avoid_negative_ts disabled tmp/phase-02-fixture-generation-20260809/media/phase-02-offset-av-aac.mkv

/opt/homebrew/bin/ffmpeg -hide_banner -nostdin -n -f lavfi -i testsrc2=size=160x90:rate=10:duration=4 -vf "select='between(t,0,0.9)+between(t,3,3.9)',setpts=PTS+10/TB" -fps_mode passthrough -c:v ffv1 -level:v 3 -map_metadata -1 -metadata title=phase-02-gap-video -metadata creation_time=1970-01-01T00:00:00Z -fflags +bitexact -flags:v +bitexact -avoid_negative_ts disabled tmp/phase-02-fixture-generation-20260809/media/phase-02-gap-video.mkv

/opt/homebrew/bin/ffmpeg -hide_banner -nostdin -n -f lavfi -i sine=frequency=440:sample_rate=48000:duration=2 -c:a aac -b:a 64k -map_metadata -1 -metadata title=phase-02-aac-priming -metadata creation_time=1970-01-01T00:00:00Z -fflags +bitexact -flags:a +bitexact -avoid_negative_ts disabled tmp/phase-02-fixture-generation-20260809/media/phase-02-aac-priming.m4a
```

The FFmpeg block is a shell-runnable transcription for reviewers. The JSON
recipe stores the corresponding argument arrays without the command-line quote
delimiters. The generator invokes FFprobe once per media artifact with this
exact argument shape and captures stdout byte-for-byte at the fixed work-tree
raw `ProbeDocument` path before promoting it:

| Media input | FFprobe argument array | Captured stdout path |
| --- | --- | --- |
| `tmp/phase-02-fixture-generation-20260809/media/phase-02-offset-av-aac.mkv` | `/opt/homebrew/bin/ffprobe -v error -of json -show_format -show_streams -show_packets -show_frames tmp/phase-02-fixture-generation-20260809/media/phase-02-offset-av-aac.mkv` | `tmp/phase-02-fixture-generation-20260809/evidence/phase-02-offset-av-aac.ffprobe.json` |
| `tmp/phase-02-fixture-generation-20260809/media/phase-02-gap-video.mkv` | `/opt/homebrew/bin/ffprobe -v error -of json -show_format -show_streams -show_packets -show_frames tmp/phase-02-fixture-generation-20260809/media/phase-02-gap-video.mkv` | `tmp/phase-02-fixture-generation-20260809/evidence/phase-02-gap-video.ffprobe.json` |
| `tmp/phase-02-fixture-generation-20260809/media/phase-02-aac-priming.m4a` | `/opt/homebrew/bin/ffprobe -v error -of json -show_format -show_streams -show_packets -show_frames tmp/phase-02-fixture-generation-20260809/media/phase-02-aac-priming.m4a` | `tmp/phase-02-fixture-generation-20260809/evidence/phase-02-aac-priming.ffprobe.json` |

No command parses human-readable output or passes container or stream duration
metadata as a coverage substitute.

## Literal Subtitle Inputs

Ticket 10 writes these exact UTF-8, LF-terminated inputs without invoking a
media tool:

```text
# phase-02-rolling.srt
1
00:00:00,000 --> 00:00:01,000
we need

2
00:00:00,900 --> 00:00:02,000
we need proof

3
00:00:02,000 --> 00:00:03,000
repeat

4
00:00:02,000 --> 00:00:03,000
repeat

5
00:00:03,000 --> 00:00:04,000
repeat
```

```text
# phase-02-out-of-range.srt
1
00:00:03,900 --> 00:00:04,100
outside observed video coverage
```

```text
# phase-02-roundtrip.vtt
WEBVTT

cue-1
00:00:00.000 --> 00:00:00.001 align:start
Line one
Line two

cue-2
00:00:01.125 --> 00:00:02.500
Second cue
```

## Required Retained Evidence

`phase-02-manifest.json` must contain one entry for every retained artifact,
including the three subtitle files, three media files, three raw probe
documents, recipe, and tool-version records. Each entry records its relative
path, byte count, SHA-256 digest, retention class, and producing fixture ID.
Each media entry additionally records the complete FFmpeg version text and
command argument list; each probe entry records the complete FFprobe version
text and command argument list.

The exact SHA-256 digests and byte counts are deliberately unassigned now:
the Phase 2 boundary forbids generating the bytes from which they would be
derived. Ticket 10 must calculate them from the retained outputs immediately
after generation and write them into the manifest. Normal integration tests
must fail on a missing entry, missing artifact, or hash mismatch; they must
never recreate it. Pretending to know a digest before the artifact exists
would make this evidence unauditable.

The approved generator records the byte counts and SHA-256 values from the
fresh work tree before promotion with these exact non-media commands (one
argument per listed path, in this order):

```text
wc -c tmp/phase-02-fixture-generation-20260809/recipes/phase-02-fixtures-v1.json tmp/phase-02-fixture-generation-20260809/media/phase-02-offset-av-aac.mkv tmp/phase-02-fixture-generation-20260809/media/phase-02-gap-video.mkv tmp/phase-02-fixture-generation-20260809/media/phase-02-aac-priming.m4a tmp/phase-02-fixture-generation-20260809/subtitles/phase-02-rolling.srt tmp/phase-02-fixture-generation-20260809/subtitles/phase-02-out-of-range.srt tmp/phase-02-fixture-generation-20260809/subtitles/phase-02-roundtrip.vtt tmp/phase-02-fixture-generation-20260809/evidence/ffmpeg-version.txt tmp/phase-02-fixture-generation-20260809/evidence/ffprobe-version.txt tmp/phase-02-fixture-generation-20260809/evidence/phase-02-offset-av-aac.ffprobe.json tmp/phase-02-fixture-generation-20260809/evidence/phase-02-gap-video.ffprobe.json tmp/phase-02-fixture-generation-20260809/evidence/phase-02-aac-priming.ffprobe.json
shasum -a 256 tmp/phase-02-fixture-generation-20260809/recipes/phase-02-fixtures-v1.json tmp/phase-02-fixture-generation-20260809/media/phase-02-offset-av-aac.mkv tmp/phase-02-fixture-generation-20260809/media/phase-02-gap-video.mkv tmp/phase-02-fixture-generation-20260809/media/phase-02-aac-priming.m4a tmp/phase-02-fixture-generation-20260809/subtitles/phase-02-rolling.srt tmp/phase-02-fixture-generation-20260809/subtitles/phase-02-out-of-range.srt tmp/phase-02-fixture-generation-20260809/subtitles/phase-02-roundtrip.vtt tmp/phase-02-fixture-generation-20260809/evidence/ffmpeg-version.txt tmp/phase-02-fixture-generation-20260809/evidence/ffprobe-version.txt tmp/phase-02-fixture-generation-20260809/evidence/phase-02-offset-av-aac.ffprobe.json tmp/phase-02-fixture-generation-20260809/evidence/phase-02-gap-video.ffprobe.json tmp/phase-02-fixture-generation-20260809/evidence/phase-02-aac-priming.ffprobe.json
```

After the manifest contains those results, the generator promotes each new
file only to its corresponding nonexistent retained path. It records every
promotion in the retained manifest and keeps the work-tree command log; a
failed verification or promotion leaves all existing and new bytes in place.

| Artifact class | Maximum individual size | Required expected evidence |
| --- | ---: | --- |
| `offset-av-aac` media | 6 MiB | Both stream projections; video starts at zero; audio has a negative source start; packet/frame evidence is retained unchanged. |
| `gap-video` media | 4 MiB | Retained video packet/frame evidence yields outer coverage `[10, 13.9)` and internal gap `[11, 13)`. |
| `aac-priming` media | 2 MiB | AAC stream projection and packet/frame priming or skip-sample evidence. |
| Each FFprobe JSON document | 1 MiB | Valid JSON retained unchanged, including unknown FFprobe fields. |
| Recipe, manifest, version records, and each subtitle file | 64 KiB | Schema/version, provenance, SHA-256, size, and literal source evidence. |

The total retained corpus is capped at 20 MiB. Expected peak memory is below
512 MiB and expected generation duration is below 10 minutes. Network,
downloads, package actions, models, paid APIs, user media, and public CLI
changes are prohibited.

## Retention And Follow-On Verification

All generated media, recipes, manifests, probe documents, tool records, work
outputs, and failed outputs are retained. The generator must not overwrite an
existing retained path and no cleanup command is authorized. Ticket 11, not
this ticket, will submit its own explicit Python-command and test-seam plan
before adding read-only integration tests that verify hashes then use the
existing library seams: `ProbeDocument` projection, decoded interval coverage,
`PartRelativeTime` and `CollectionVirtualTime` mapping, and SRT/VTT round
trips. Ticket 09 authorizes no Python execution and no test command. Ticket 11
may not invoke FFmpeg or FFprobe.

## Approval Text

Approve this exact scope to unblock Ticket 10: "Approve Ticket 09's Phase 2
fixture corpus proposal. Ticket 10 may create and execute only the listed
project-local fixture recipe and FFmpeg/FFprobe commands, retain every output
and failure, and record actual hashes and evidence. It may not download,
overwrite, delete, access user media, or add a media-facing CLI."
