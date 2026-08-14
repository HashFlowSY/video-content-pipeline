# Context Map and Domain-Owned Glossaries

Type: enhancement
Status: resolved
Labels: enhancement
Published: 2026-08-14
Completed: 2026-08-14

## Problem Statement

The project has one large, flat domain glossary that combines deterministic
media foundations, source planning, subtitle processing, audio analysis, and
text analysis. It is difficult for a contributor to know which terms apply to
one proposed change, where a definition may be changed, or which architectural
decisions govern that term. The glossary also contains implementation details
and has a parallel runtime-oriented glossary, creating two potential sources of
truth.

As a result, later documentation and implementation work can read too much,
read too little, duplicate a definition, mutate the wrong boundary, or continue
following the retired single-context rule. The project needs a durable domain
documentation topology that makes ownership, dependency, ADR relevance, and
future read/write behavior explicit without changing media-processing behavior.

## Solution

Replace the monolithic glossary with one root Context Map and five
domain-owned glossaries: media foundation, source planning, subtitles, audio
analysis, and text analysis. The Context Map is the sole canonical entry point;
it states each Context's purpose, dependencies, owned vocabulary, and relevant
global ADRs. Every domain term has exactly one owning Context, while consumers
link to that owner instead of copying its definition.

Move runtime-governance vocabulary outside the domain model, turn the legacy
runtime glossary into an explicit migration pointer, and update active
documentation and agent instructions to follow the new routing protocol. A
repository-level document-layout contract test verifies the objective structure,
and a migration inventory gives every retired glossary term and relocated
constraint a reviewable destination. Existing historical inventories and
archival snapshots remain evidence of their original state.

## User Stories

1. As a contributor, I want one canonical Context Map, so that I can begin every domain task from a known documentation entry point.
2. As a contributor, I want the Context Map to name every Context and its purpose, so that I can select the smallest relevant vocabulary scope.
3. As a contributor, I want the Context Map to state direct and transitive dependencies, so that I can read prerequisites before changing a dependent boundary.
4. As a contributor changing deterministic media evidence, I want a media-foundation Context, so that fixture, time, cue, coverage, probe, and publication-boundary language has one home.
5. As a contributor changing source intake, I want a source-planning Context, so that SourceArtifact, Part, MediaCollection, source authorization, inspection, and RunPlan language remains cohesive.
6. As a contributor changing subtitle behavior, I want a subtitles Context, so that SubtitleTrackCandidate, Primary subtitle track, subtitle artifacts, PresentationCorrection, and subtitle coverage rules are owned together.
7. As a contributor changing audio analysis, I want an audio-analysis Context, so that AlignmentCandidate, voice activity, diarization, calibration, analysis-audio, and audio-report language has one owner.
8. As a contributor changing text analysis, I want a text-analysis Context, so that SemanticSegment, Chapter, cue-supported facts, text-model projection, and text-report language has one owner.
9. As a future agent, I want to read the Context Map before exploration or domain work, so that I do not follow the retired single-context workflow.
10. As a future agent, I want to load only each affected Context and its dependencies, so that routine work is precise without requiring a full glossary reread.
11. As a future agent, I want every term to have exactly one owner, so that a definition cannot drift across Contexts.
12. As a future agent, I want dependent Contexts to reference shared terms rather than restate them, so that a changed definition has one authoritative edit point.
13. As a reviewer, I want cross-Context changes to identify every affected owner, so that an update cannot silently contradict a neighboring boundary.
14. As a reviewer, I want each Context to identify relevant global ADRs, so that contributors can find governing decisions without moving historical decision records.
15. As a maintainer, I want the ADR tree to remain global and link-stable, so that existing ADR references and audit history remain valid.
16. As a maintainer, I want a new architectural decision record for Context Map routing and domain-owned glossaries, so that the deliberate trade-off against one monolithic glossary is preserved.
17. As a maintainer, I want the new decision record to explain unique ownership and the retained global ADR tree, so that a future cleanup does not recreate parallel sources of truth.
18. As a documentation author, I want Context definitions to describe domain concepts rather than commands, paths, filenames, or mutable implementation thresholds, so that the glossary stays a stable ubiquitous language.
19. As a documentation author, I want every detailed operational constraint removed from a glossary to have a documented destination in a specification, ADR, or other authoritative record, so that simplification does not weaken a safety rule.
20. As an auditor, I want every term from the retired monolithic glossary mapped to its new owner, so that I can verify that no vocabulary was silently lost.
21. As an auditor, I want every former runtime-only term mapped to a runtime-governance document, so that domain documentation is not polluted by environment setup concepts.
22. As an agent following runtime policy, I want the runtime-governance document to retain Managed Python, project virtual environment, gate, registry, and download vocabulary, so that existing safety language remains discoverable.
23. As a reader of the old runtime glossary, I want an explicit migration pointer, so that an old link leads to the canonical Context Map or runtime guidance rather than a stale competing definition.
24. As a reader of active Phase 4 documentation, I want direct links to the Context Map and its media-foundation, source-planning, and subtitles dependencies, so that subtitle work uses the correct vocabulary.
25. As a reader of active Phase 5 documentation, I want direct links to the Context Map and its media-foundation, source-planning, subtitles, and audio-analysis dependencies, so that audio analysis uses the correct boundaries.
26. As a reader of active Phase 6 documentation, I want direct links to the Context Map and text-analysis Context, with audio analysis identified as optional, so that semantic work can distinguish required subtitle evidence from optional audio context.
27. As a records custodian, I want historical inventories, completed reports, and archived work snapshots left unchanged, so that records continue to describe the files and hashes that existed at the time.
28. As a records custodian, I want a dedicated Context-layout migration inventory, so that new documentation work has its own provenance without rewriting a completed phase inventory.
29. As a maintainer, I want the migration inventory to list created, modified, and retired documentation artifacts, so that the documentation topology change is independently auditable.
30. As a maintainer, I want the migration inventory to record external material read for the migration, so that its evidence trail follows project reporting rules.
31. As a contributor, I want missing but already-used domain terms such as Part, RunBundle, PresentationCorrection, AlignmentCandidate, SemanticSegment, and Chapter explicitly defined by their agreed owners, so that implementation and documentation use unambiguous language.
32. As a contributor, I want the former Phase 9 shorthand expressed as a future separately authorized publication stage, so that an undeveloped phase number is not mistaken for a defined domain concept.
33. As a safety reviewer, I want RunBundle and the Publication boundary owned by media foundation, so that deferred publication remains visibly separate from planning and analysis workspaces.
34. As a safety reviewer, I want text analysis to remain optionally informed by audio analysis rather than dependent on it for basic subtitle-derived claims, so that missing audio analysis does not create invented evidence.
35. As a test author, I want one repository-level document-layout contract seam, so that observable documentation topology is checked at the highest useful boundary.
36. As a test author, I want the contract to reject a missing Context Map, a missing required Context, a duplicate owned term, or a legacy monolithic entry point, so that structural drift is caught in the normal test suite.
37. As a test author, I want the contract to verify routing rules, Context dependencies, ADR indices, active-spec navigation, runtime-governance migration, and migration-inventory completeness, so that the new operating standard remains executable.
38. As a reviewer, I want the test to check only objective structure and canonical references, so that it does not falsely claim to infer whether prose preserves a domain rule.
39. As a reviewer, I want semantic simplification and rule relocation to remain reviewable through the migration inventory, so that human judgment remains responsible for meaning.
40. As a project maintainer, I want this work to change documentation architecture only, so that it does not authorize model acquisition, source access, media processing, publication, or production validation.

## Implementation Decisions

- Replace the single root glossary with a root Context Map as the only canonical domain-documentation entry point. Do not leave a root glossary redirect, because a second familiar entry point would undermine unique ownership.
- Create five Contexts named media-foundation, source-planning, subtitles, audio-analysis, and text-analysis. Their dependency route is media foundation to source planning to subtitles to audio analysis to text analysis; text analysis treats audio analysis as optional context, not a prerequisite for subtitle-derived facts.
- Give every domain term one owning Context. A non-owning Context may link to the owner and state why it depends on the term, but may not define a divergent abbreviated version.
- Put deterministic synthetic fixtures, exact time coordinates, cue representations, probe and coverage evidence, RunBundle, and the Publication boundary in media foundation.
- Put Part, MediaCollection, local and public source authorization, SourceArtifact, Pinned external tool, planning evidence, resource estimation, and RunPlan language in source planning.
- Put subtitle candidate, extraction, selection, normalization, presentation correction, source/readable artifact, Primary subtitle coverage, and subtitle-workspace language in subtitles.
- Put AlignmentCandidate, adopted timing, voice activity, speaker turns, model capability and calibration, analysis-audio derivation, audio-analysis workspace, and audio-analysis report language in audio analysis.
- Put SemanticSegment, Chapter, cue-supported facts, text-model output projection, text-analysis workspace, report, review, and segment-derived summary language in text analysis.
- Retain the existing semantic constraints while making definitions concise. Move implementation mechanics and configuration values out of glossary definitions only when an existing or newly designated authoritative specification or ADR retains the exact constraint.
- Create a runtime-governance document for environment and setup vocabulary formerly held only in the parallel glossary, including managed runtime, project virtual environment, shell and in-process gates, registries, and explicit-versus-automatic downloads.
- Convert the legacy runtime glossary into a migration pointer. It must name neither itself nor any retired glossary as canonical and must direct readers to the Context Map and runtime-governance document.
- Update project and domain-agent instructions to require: read the Context Map first; read each affected Context and its dependencies; write a term only in its owner; and include all affected owners and relevant ADRs in a cross-Context change.
- Preserve one global ADR tree. The Context Map indexes existing relevant ADRs per Context, and future ADRs identify the Contexts they govern and are added to the map's index.
- Add the next sequential ADR, currently expected to be ADR 0042, recording the decision to use a Context Map with domain-owned glossaries while retaining global ADRs.
- Update active Phase 4, Phase 5, and Phase 6 specifications to navigate through the Context Map plus their direct Contexts. Do not rewrite completed reports, historical inventories, or archival snapshots merely because they mention the retired layout.
- Create a dedicated Context-layout migration inventory. It must map every former monolithic-glossary term, every formerly parallel runtime term, and every relocated implementation constraint to its canonical destination or documented retirement rationale.
- Treat the migration inventory as provenance for this documentation architecture change, not as a replacement for a phase implementation inventory.

## Testing Decisions

- Add one zero-dependency repository-level document-layout contract test as the primary seam. It should consume the published documentation topology from the repository root, rather than test individual parsing helpers or internal documentation-generation steps.
- A good test observes only repository-visible documentation behavior: canonical entry points, Context existence, declared dependency routing, term ownership uniqueness, navigation targets, ADR indices, and migration completeness. It must not assert a particular prose layout beyond stable structural markers and must not attempt to classify prose semantically with keyword heuristics.
- The contract must fail if the Context Map is missing, a required Context is missing, a required dependency is omitted, a term is defined by more than one Context, the retired root glossary remains canonical, or the legacy runtime glossary retains competing definitions.
- The contract must fail if project/domain-agent instructions still prescribe the single-context read/write workflow, if active Phase 4–6 specifications lack their required navigation, if required ADR routing is absent, or if a migration-inventory entry is incomplete.
- The contract must verify that the runtime-governance terms have one non-domain home and that the map does not treat runtime setup vocabulary as a sixth domain Context.
- Preserve the current test-suite conventions: use the existing pytest suite and project environment gate, add no new runtime dependency, network behavior, model acquisition, or test fixture media.
- Use existing contract-oriented CLI and offline boundary tests as prior art for asserting externally observable, auditable contracts rather than implementation details. This documentation contract is analogous in scope, but operates at the repository-documentation boundary.
- Review migration inventory entries manually for semantic preservation. Automated coverage establishes structural completeness only; it does not certify that a prose rewrite preserves the meaning of a safety constraint.

## Out of Scope

- Changing runtime pipeline behavior, media-processing code, CLI behavior, source-access authorization, model policy, fixture generation, or publication behavior.
- Accessing user media, public URLs, browser state, credentials, cookies, paid APIs, models, model runtimes, or network resources.
- Moving, renumbering, deleting, or rewriting the existing global ADR records.
- Rewriting historical phase inventories, completion reports, file manifests, or archived work snapshots to make old links appear current.
- Creating a new context-scoped ADR directory or duplicating ADRs into Contexts.
- Adding hooks, external documentation generators, package dependencies, or a machine heuristic that purports to validate domain semantics.
- Declaring any work production validated.

## Further Notes

- This specification synthesizes the completed grilling decisions. It is ready for an implementation agent; publication of this specification does not itself perform the Context migration.
- The current glossary vocabulary remains the semantic migration baseline until the new topology is implemented. Where the legacy runtime glossary overlaps it, the current domain glossary takes precedence for domain meanings and the runtime glossary contributes only its runtime-governance-only terms.
- Existing Phase 4, Phase 5, and Phase 6 decision records remain authoritative for their established domain constraints. The Context Map improves their discoverability; it does not supersede them.
- The migration must respect the project's prohibition on production validation and must retain all evidence and historical records unless a later explicit authorization permits deletion.

## Comments

- 2026-08-14: Resolved after the Context Map migration passed the repository-level
  document-layout contract test (6 passed).
