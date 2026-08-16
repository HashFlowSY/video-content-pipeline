# 06 — Give subtitle_pipeline.py its dedicated unit test file

**What to build:** `src/video_content_pipeline/subtitle_pipeline.py`
currently has no dedicated unit test file (coverage is indirect). Create
`tests/unit/test_subtitle_pipeline.py` covering its public functions
directly: candidate retention and atomic validation behavior, Primary
track selection rules, source/readable derivation, and edge cases —
empty tracks, overlapping cues, rolling-overlap proof boundaries,
mixed-language cues. Follow the audit before writing: enumerate the
module's public surface, map which behaviors existing tests already pin
indirectly, and target the genuinely unpinned ones (state the mapping in
the test module docstring so the gap-fill is auditable).

**Blocked by:** —
**Status:** done
**Labels:** ready-for-agent

- [x] Docstring maps public surface → previously-pinned vs newly-pinned
- [x] Every public function of the module has at least one direct test
- [x] Edge cases above covered
- [x] Suite green

## Comments

Done in `f1acf4b` (2026-08-16). New `tests/unit/test_subtitle_pipeline.py`
(58 tests). Its module docstring is the audit: it enumerates the public
surface (three functions — `process_subtitles`, `resume_subtitles`,
`subtitle_rules_fingerprint` — plus the dataclass serializers), records where
each behavior is already pinned indirectly (`test_phase_4_cli_contract.py` end
to end; the ticket-05 `test_serialization_roundtrip_properties.py` for the two
`as_json`/`from_json` round-trips), and targets only the unpinned residue.

Newly pinned directly: `subtitle_rules_fingerprint` rejection matrix (missing /
non-JSON / non-object / wrong `schema_version`) + byte-content determinism;
`process_subtitles`/`resume_subtitles` exception→persisted-BLOCKED contract
without host ffmpeg; the isolated serializer edges (`CaptionTimeCoverage`
ratio reduction incl. `0/4 → 0/1`, `SubtitlePartReport` null branches,
`SubtitleReportError.reason`); Primary-track selection (`_selected_candidate`,
`_ambiguous_source_ids`, `_unresolved_ambiguous_source_ids`,
`_requires_asr_planning`); interval/coverage derivation (`_union_intervals`,
`_interval_duration`, `_playback_coverage`) and `_collection_reporting` over
all four named edge cases — empty tracks, overlapping cues, rolling-overlap
proof boundaries (half-open touching pair merges), and the mixed-language
two-valid-track ambiguity (reinterpreted from "mixed-language cues" since the
module carries no language field; disclosed in the docstring); source/readable
derivation (`_source_format` codec→format matrix, `_candidate_codec`,
`_write_candidate_artifacts` path derivation + idempotency); atomic immutable
writes; and the input parsers (`_parse_selection`, `_parse_decoders`,
`_validated_report_id`). The load-bearing helpers are pinned directly because
they are otherwise reachable only through the full host-ffmpeg CLI path, which
the integration layer already exercises.

No production bug surfaced. Full suite 1211 green (~9s, within the ≤5-min
budget); ruff + ruff format + mypy(src) clean. Two-axis code review clean —
standards: 0 documented-standard violations, judgement-call cleanups applied
(`_evidence` → `_coverage_evidence`, the meaningless `| object` annotation on
`_structural_evidence` narrowed to `list | str | None`); spec: 0 findings, the
only soft note (mixed-language reinterpretation) already disclosed.
