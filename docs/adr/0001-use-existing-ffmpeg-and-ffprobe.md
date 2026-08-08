# Use existing FFmpeg and FFprobe for Phase 2 fixtures

Phase 2 will use the already available FFmpeg and FFprobe 8.1.2 binaries at
`/opt/homebrew/bin/` only to generate and probe project-local synthetic test
fixtures. This avoids an unapproved tool download or installation while still
making the deterministic media and timeline tests executable; each Phase 2 run
must record the binary paths and versions it actually uses.

## Considered Options

- Use existing binaries: accepted because they are already available and no
  installation or download is needed.
- Download project-local binaries: rejected for now because it would require a
  separately authorized source, checksum, storage plan, and download.
