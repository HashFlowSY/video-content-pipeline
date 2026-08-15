# 08 -- Load retained text-analysis reports and select affected Parts

**What to build:** The two missing text-analysis capabilities that ADR 0046
needs: a loader that deserializes a retained `text-analysis-report.json` back
into domain objects, and an affected-Part selector keyed on changed cue
identities.

**Blocked by:** None -- can start immediately (pure text-analysis work,
independent of ASR machinery).

**Status:** done
**Labels:** ready-for-agent

- [ ] Deserialize retained reports into `AvailablePart` / segment / chapter /
  aggregation domain objects with hash verification; loading never mutates
  the retained report.
- [ ] Given a prior report and a new cue basis, deterministically classify
  each Part as affected (cue identities changed) or unaffected.
- [ ] Structured diagnostics for unloadable or drifted reports; no silent
  best-effort loading.
