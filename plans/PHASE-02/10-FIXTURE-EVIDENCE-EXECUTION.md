# Ticket 10 Fixture Evidence Execution

Status: completed with a recorded manifest-repair exception
Date: 2026-08-09

## Authorized Scope

Ticket 09 authorized the approved local FFmpeg and FFprobe 8.1.2 commands for
the three project-owned `lavfi` media fixtures. This ticket added the generator
at `scripts/generate-phase-02-fixtures.sh`, retained fixture evidence below
`tests/fixtures/`, and did not access user media, network resources, models,
paid services, or a media-facing CLI.

The user subsequently authorized a narrow repair exception after the first
retained manifest serialized each SHA-256 digest with an unwanted terminal
newline. The exception archives that manifest unchanged at
`tests/fixtures/evidence/phase-02-manifest-rerun-03-invalid.json`, creates a
corrected manifest from the already-retained bytes, and replaces only the
canonical `tests/fixtures/phase-02-manifest.json`. It does not run FFmpeg or
FFprobe again and retains all prior work directories.

## Attempts And Retention

| Work directory | Result | Retention |
| --- | --- | --- |
| `tmp/phase-02-fixture-generation-20260809/` | Stopped before media generation because macOS Bash 3.2 lacks `mapfile`. | Retained with recipe, literal subtitles, tool records, and `generation.log`. |
| `tmp/phase-02-fixture-generation-20260809-rerun-01/` | Generated work artifacts but stopped before promotion because the generator incorrectly applied a 64 KiB limit to a 100,039-byte raw ProbeDocument. | Retained in full. |
| `tmp/phase-02-fixture-generation-20260809-rerun-02/` | Generated work artifacts but stopped before promotion because the generated manifest was invalid JSON. | Retained in full. |
| `tmp/phase-02-fixture-generation-20260809-rerun-03/` | Promoted the approved corpus, then discovered the manifest SHA-256 strings each included `\\n`. | Retained in full; its original manifest is also archived before repair. |

The generator now uses `/opt/homebrew/bin/bash` 5.3.15 and does not rely on
macOS's system Bash 3.2. It applies the approved 1 MiB limit to raw ProbeDocuments,
the approved 64 KiB limit to recipe, subtitles, tool records, and manifest, and
the approved 20 MiB total cap. Before promotion it checks JSON syntax, each
manifest byte count, and each 64-character lowercase SHA-256 digest.
`.gitattributes` marks both `tests/fixtures/**` and the retained `tmp/**`
work trees as `-text`, preventing checkout-time LF conversion from changing
the hash-pinned bytes on environments that enable `core.autocrlf`.

The first manifest-repair attempt stopped before replacement because its JQ
expression emitted one entry rather than the complete manifest. The archived
invalid source and that repair work directory remain retained. The corrected
repair script updates one matching entry within the full document, verifies
that all 12 entries remain present after every update, and uses a fresh repair
work directory.

## Operational Assessment

The newline defect does not change any fixture byte. It does make the retained
manifest unsuitable for every real workflow that treats the digest as an exact
identifier:

- A correct read-only integration test recomputing a SHA-256 digest rejects the
  manifest entry, so Ticket 11 would expose the defect immediately.
- A build or release verifier comparing a shell-derived digest to the manifest
  rejects every entry even though the generated media is intact.
- An auditor copying a manifest digest into a verifier has an invisible extra
  character, weakening the audit trail and making mismatch reports ambiguous.
- A consumer that only checks file presence would miss the defect; that is why
  file existence is not sufficient fixture validation.

The repair retains the defective manifest as evidence, reconstructs the
canonical record from the retained artifact bytes, and verifies all entries
before replacement. The long-term guard is the generator's pre-promotion
validation plus Ticket 11's ordinary read-only integration test: validate the
manifest schema and entry set, require exactly 64 lowercase hexadecimal digest
characters, recompute each hash, and never invoke the generator or FFmpeg.
