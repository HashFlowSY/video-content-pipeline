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
**Status:** done
**Labels:** ready-for-agent

- [x] The test module contains the explicit pair inventory + exclusions list
- [x] Round-trip equality property per pair, deterministic profile
- [x] Mutation/rejection properties raise typed errors only
- [x] No new schema framework introduced
- [x] Suite green within budget

## Comments

Done in `d39dcae` (2026-08-16). New `tests/property/`
`test_serialization_roundtrip_properties.py` drives a `PAIRS` registry of 16
serialize/deserialize contracts under the deterministic gate profile: the 12
`as_json`/`from_json` classes, the four path-based loaders (`read_run_state`,
`read_journal`, `load_plan_report`, `load_run_plan`), and the
`heavy_task_lock.LockHolder` `to_document`/`from_document` pair. Each pair proves
round-trip equality through real JSON text and three rejection properties
(dropped/retyped field, non-object top level, bogus enum token) that assert only
the owning module's typed reason class ever escapes — never an unhandled
`KeyError`/`TypeError`/`AttributeError`. The docstring holds the checkable pair
inventory plus the write-only-document exclusions (Minimal RunBundle reports,
`ProjectionResult`, per-phase audit fragments, `run_recovery`/`run_loop`
`to_document` outputs). No generic schema framework (grilling Q17): per-pair
hand-written strategies only.

No production bug surfaced — every loader already rejects structured corruption
with its typed reason. Suite 1153 green; ruff + mypy(src) clean; two-axis code
review clean (standards: dead `# type: ignore` removed, trivial loader adapters
collapsed to `functools.partial`, `_ident` renamed `_nonempty_text`; spec: the
missed `LockHolder` pair added and the write-only `to_document` outputs listed
as exclusions).
