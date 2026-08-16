# 11 — Implement improvement runs

**What to build:** `vcp improve --from-run <run-id> --asr <part|range|all>`:
the Improvement run — a new plan and new run id derived from a named
published RunBundle, carrying forward published artifacts by recorded source
run id and hash (ADR 0046 pattern at run level; the sanctioned exception in
ADR 0052), routing through the retained enhancement and affected-Part
re-analysis contracts, then projecting and publishing as a normal run.

**Blocked by:** 02, 09, 10

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Improve reads only from the named published bundle — never from
  workspaces — and revalidates the bundle's hashes before use.
- [ ] A new plan and run id are created; the prior bundle and its
  `latest.json` remain byte-identical throughout.
- [ ] Carried-forward artifacts are recorded in the new run's manifest and
  reports with source run id and artifact hashes.
- [ ] The scope grammar (`part`, `range`, `all`) maps to the retained
  enhancement scoping semantics; enhanced artifacts keep
  `audio_completeness=not_verified` and per-cue provenance.
- [ ] The new run publishes through the standard staging/atomic-publish path
  and may advance `latest.json` only under the standard eligibility rule.
