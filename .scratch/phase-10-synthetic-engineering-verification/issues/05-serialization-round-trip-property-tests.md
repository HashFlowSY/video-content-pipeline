# 05 — Property-test serialization round-trips across contexts

**What to build:** For every `as_json`/`from_json` pair in `src/` (run
state, journal events, stage invalidation keys, run choices, plans,
publication manifest, latest pointer, projection results, … — enumerate by
grep and list them in the test module), hypothesis-generated valid objects
must satisfy object → `as_json` → `from_json` → equality. Add rejection
properties: structured mutations of valid JSON (dropped required field,
wrong type, unknown enum token) fail with the module's typed reasons —
never an unhandled exception. This ticket deliberately replaces building a
generic JSON-schema framework (grilling Q17). Write-only documents without
`from_json` (audit reports) are listed as exclusions with one line of
rationale each, so the enumeration is checkable.

**Blocked by:** 01
**Status:** open
**Labels:** ready-for-agent

- [ ] The test module contains the explicit pair inventory + exclusions list
- [ ] Round-trip equality property per pair, deterministic profile
- [ ] Mutation/rejection properties raise typed errors only
- [ ] No new schema framework introduced
- [ ] Suite green within budget

## Comments
