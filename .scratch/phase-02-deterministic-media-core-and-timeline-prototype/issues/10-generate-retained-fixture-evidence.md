# 10 -- Generate retained fixture evidence

Category: enhancement
Status: resolved
Labels: enhancement

**What to build:** The approved project-owned synthetic media corpus and its
auditable evidence package, ready for read-only use by normal integration
tests.

**Blocked by:** 09 -- Approve synthetic fixture corpus.

- [x] Generate only the explicitly approved fixtures with the approved FFmpeg
  and FFprobe binaries, and record actual tool identity and version.
- [x] Retain each fixture recipe, content hash, expected raw `ProbeDocument`,
  generated size, and inventory entry without overwriting or deleting prior
  evidence.
- [x] Ensure ordinary tests have no fixture-generation or cleanup behavior.

## Comments

2026-08-09: Generated the approved project-owned synthetic corpus with FFmpeg
and FFprobe 8.1.2, retained 12 canonical fixture-manifest entries and all raw
tool evidence, and verified each canonical byte count and SHA-256 digest.
Three initial generation attempts and one initial manifest-repair attempt
stopped before an unapproved overwrite and remain under `tmp/` with logs and
outputs. A user-authorized repair archived the defective newline-terminated
hash manifest and rebuilt the canonical manifest from the retained bytes. No
user media, network resource, package, model, paid API, or media-facing CLI
was used. Ticket 11 owns all ordinary read-only fixture-backed integration
tests; Ticket 10 ran no Python or ordinary test command.
