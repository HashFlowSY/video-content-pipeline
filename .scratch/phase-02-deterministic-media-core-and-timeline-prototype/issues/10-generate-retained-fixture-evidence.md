# 10 -- Generate retained fixture evidence

Category: enhancement
Status: ready-for-agent
Labels: enhancement, ready-for-agent

**What to build:** The approved project-owned synthetic media corpus and its
auditable evidence package, ready for read-only use by normal integration
tests.

**Blocked by:** 09 -- Approve synthetic fixture corpus.

- [ ] Generate only the explicitly approved fixtures with the approved FFmpeg
  and FFprobe binaries, and record actual tool identity and version.
- [ ] Retain each fixture recipe, content hash, expected raw `ProbeDocument`,
  generated size, and inventory entry without overwriting or deleting prior
  evidence.
- [ ] Ensure ordinary tests have no fixture-generation or cleanup behavior.
