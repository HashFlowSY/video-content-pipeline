"""Repository-level contract for the domain documentation topology."""

from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
MAP_PATH = PROJECT_ROOT / "CONTEXT-MAP.md"
CONTEXT_ROOT = PROJECT_ROOT / "docs" / "contexts"
INVENTORY_PATH = PROJECT_ROOT / "docs" / "CONTEXT_LAYOUT_MIGRATION_INVENTORY.json"
RUNTIME_PATH = PROJECT_ROOT / "docs" / "RUNTIME_GOVERNANCE.md"
LEGACY_PATH = PROJECT_ROOT / "docs" / "DOMAIN_GLOSSARY.md"
AGENT_GUIDANCE_PATHS = (PROJECT_ROOT / "AGENTS.md", PROJECT_ROOT / "docs" / "agents" / "domain.md")

REQUIRED_CONTEXTS = {
    "media-foundation": "media-foundation/CONTEXT.md",
    "source-planning": "source-planning/CONTEXT.md",
    "subtitles": "subtitles/CONTEXT.md",
    "audio-analysis": "audio-analysis/CONTEXT.md",
    "text-analysis": "text-analysis/CONTEXT.md",
    "transcription": "transcription/CONTEXT.md",
    "visual-text": "visual-text/CONTEXT.md",
}

REQUIRED_DOMAIN_TERMS = {
    "Part",
    "RunBundle",
    "PresentationCorrection",
    "AlignmentCandidate",
    "SemanticSegment",
    "Chapter",
    "Primary subtitle coverage",
}

EXPECTED_DEPENDENCIES = {
    "media-foundation": set(),
    "source-planning": {"media-foundation"},
    "subtitles": {"media-foundation", "source-planning"},
    "audio-analysis": {"media-foundation", "source-planning", "subtitles"},
    "text-analysis": {"media-foundation", "source-planning", "subtitles"},
    "transcription": {"media-foundation", "source-planning", "subtitles", "audio-analysis"},
    # visual-text depends on source-planning (transitively media-foundation);
    # audio-analysis is an optional informing context and does not count as a
    # hard dependency, and there is no subtitles dependency (ADR 0047).
    "visual-text": {"media-foundation", "source-planning"},
}

EXPECTED_DIRECT_DEPENDENCIES = {
    "media-foundation": set(),
    "source-planning": {"media-foundation"},
    "subtitles": {"source-planning"},
    "audio-analysis": {"subtitles"},
    "text-analysis": {"subtitles"},
    "transcription": {"subtitles", "audio-analysis"},
    "visual-text": {"source-planning"},
}

# Terms introduced by domain work after the Context-layout migration; they are
# owned by their Contexts but intentionally absent from the migration
# inventory's retired monolithic terms.
POST_MIGRATION_TERMS = {
    # Phase 7 (transcription)
    "Transcription capability contract",
    "Independent-model review requirement",
    "Model-acquisition-required transcription result",
    "Controlled offline ASR adapter",
    "Verbatim transcription artifact",
    "Enhanced subtitle artifact",
    "Cue-level transcription provenance",
    "Audio-completeness upgrade",
    "Transcription attempt provenance",
    "Immutable transcription workspace",
    "Suspicious interval",
    "Versioned suspicion detection rules",
    "Deterministic transcription arbitration",
    "Unresolved transcription conflict",
    "Gate-checked interval replacement",
    "Explicit transcription command boundary",
    "Full-ASR resource confirmation pause",
    "Transcription resource-envelope pause",
    "Serialized ASR execution",
    # Phase 7 (text-analysis additions)
    "Affected-Part re-analysis",
    "Carried-forward analysis Part",
    # Phase 7 (media-foundation addition)
    "Phase 7 offline transcription-verification boundary",
    # Phase 8 (visual-text)
    "Visual-text capability contract",
    "Controlled offline OCR adapter",
    "Model-acquisition-required visual-text result",
    "Deterministic page-change detection",
    "Versioned frame-sampling rules",
    "Text-value proxy metric",
    "Visual page",
    "Part-local visual page identity",
    "Page appearance record",
    "OCR evidence item",
    "Versioned OCR-item classification rules",
    "Excluded visual item",
    "Classification-uncertain visual item",
    "Suspected embedded-media interval",
    "Retained frame inventory",
    "Unpublished internal frame",
    "Explicit visual-text command boundary",
    "OCR resource confirmation pause",
    "Visual-text resource-envelope pause",
    "Immutable visual-text workspace",
    "Serialized OCR execution",
    "OCR-not-requested record",
    # Phase 8 (text-analysis additions)
    "Optional visual-text context",
    "Host-read comment upgrade",
    # Phase 8 (media-foundation addition)
    "Phase 8 offline visual-verification boundary",
}

RUNTIME_TERMS = {
    "Project root",
    "Managed Python",
    "Project virtual environment",
    "Shell gate",
    "In-process gate",
    "Tool registry",
    "Model registry",
    "Runtime download",
    "Runtime auto-download",
}

RETIRED_MONOLITHIC_TERM_COUNT = 172

RELOCATED_CONSTRAINT_DESTINATIONS = {
    "docs/contexts/media-foundation/CONTEXT.md",
    "docs/adr/0002-compact-coverage-based-virtual-timeline.md",
    "docs/adr/0007-preserve-signed-raw-pts.md",
    "docs/adr/0008-separate-source-part-and-collection-time.md",
    "docs/adr/0009-use-outward-millisecond-serialization.md",
    "docs/adr/0010-derive-stream-coverage-from-decoded-intervals.md",
    "docs/adr/0011-parse-ffprobe-json-without-fallback-guessing.md",
    "docs/adr/0023-retain-packet-level-coverage-evidence.md",
    "docs/contexts/source-planning/CONTEXT.md",
    "docs/adr/0015-require-explicit-url-access-mode.md",
    "docs/adr/0016-snapshot-local-sources-with-double-hash.md",
    "docs/adr/0017-use-user-ordered-manual-collections.md",
    "docs/adr/0018-accept-only-regular-local-source-files.md",
    "docs/adr/0020-revalidate-ffprobe-for-phase-3-preflight.md",
    "docs/adr/0021-require-confirmed-full-decode-validation.md",
    "docs/adr/0022-revalidate-ffmpeg-for-phase-3-decode-validation.md",
    "docs/adr/0024-revalidate-evidence-before-plan-confirmation.md",
    "docs/contexts/subtitles/CONTEXT.md",
    "docs/PHASE_04_SPECIFICATION.md",
    "docs/adr/0003-reject-invalid-subtitle-tracks-atomically.md",
    "docs/adr/0004-separate-subtitle-cue-representations.md",
    "docs/adr/0005-preserve-overlapping-subtitle-cues.md",
    "docs/adr/0006-use-exact-local-proof-for-rolling-deduplication.md",
    "docs/adr/0025-revalidate-before-subtitle-processing.md",
    ".scratch/phase-05-auditable-audio-analysis-prototype/spec.md",
    "docs/adr/0026-keep-adopted-alignment-timing-derived.md",
    "docs/adr/0027-require-model-specific-alignment-calibration.md",
    "docs/adr/0028-separate-voice-activity-from-subtitle-coverage.md",
    "docs/adr/0029-require-model-specific-vad-calibration.md",
    "docs/adr/0030-keep-speaker-labels-part-local-and-anonymous.md",
    "docs/adr/0031-require-model-specific-diarization-calibration.md",
    "docs/adr/0032-serialize-phase-5-heavy-analysis.md",
    "docs/adr/0033-revalidate-all-phase-5-analysis-inputs.md",
    "docs/adr/0034-keep-phase-5-analysis-in-immutable-workspaces.md",
    "docs/adr/0035-expose-phase-5-through-an-explicit-analysis-cli.md",
    "docs/adr/0036-keep-phase-5-model-capabilities-provider-neutral.md",
    "docs/adr/0037-verify-phase-5-with-controlled-offline-adapters.md",
    "docs/adr/0038-require-explicit-analysis-audio-stream-selection.md",
    "docs/adr/0039-require-deterministic-calibration-evaluation-records.md",
    "docs/contexts/text-analysis/CONTEXT.md",
    "docs/PHASE_06_SPECIFICATION.md",
    "docs/adr/0040-require-cue-level-evidence-for-phase-6-facts.md",
    "docs/adr/0041-keep-phase-6-text-analysis-in-immutable-workspaces.md",
    "docs/RUNTIME_GOVERNANCE.md",
    "AGENTS.md",
    "config/runtime-policy.toml",
    "docs/adr/0042-use-context-map-and-domain-owned-glossaries.md",
}


def _context_terms(path: Path) -> list[str]:
    return re.findall(r"^\*\*([^*]+)\*\*:\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)


def _map_section(name: str) -> str:
    text = MAP_PATH.read_text(encoding="utf-8")
    match = re.search(
        rf"^###\s+{re.escape(name)}\s*$(?P<body>.*?)(?=^###\s+|^##\s+|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"Context Map has no section for {name}"
    return match.group("body")


def _paths_in_destination(destination: str) -> set[str]:
    return {path for path in RELOCATED_CONSTRAINT_DESTINATIONS if path in destination}


def test_context_map_is_the_single_canonical_domain_entry_point() -> None:
    assert MAP_PATH.is_file()
    map_text = MAP_PATH.read_text(encoding="utf-8")
    assert "canonical" in map_text.lower()
    assert "CONTEXT.md" not in map_text.split("## Contexts", 1)[0]
    assert not (PROJECT_ROOT / "CONTEXT.md").exists()
    assert "docs/DOMAIN_GLOSSARY.md" not in map_text


def test_contexts_have_unique_owned_terms_and_declared_dependencies() -> None:
    declared_terms: dict[str, str] = {}
    for context_name, relative_path in REQUIRED_CONTEXTS.items():
        path = CONTEXT_ROOT / relative_path
        assert path.is_file(), f"missing {context_name} Context"
        body = _map_section(context_name)
        declared = re.search(r"^\* Dependencies:\s*(.*?)\s*$", body, re.MULTILINE)
        assert declared, f"{context_name} has no dependency declaration"
        dependencies = {
            item.strip().strip("`")
            for item in declared.group(1).split(",")
            if item.strip() and item.strip().lower() != "none"
        }
        assert dependencies == EXPECTED_DEPENDENCIES[context_name]
        direct = re.search(r"^\* Direct dependencies:\s*(.*?)\s*$", body, re.MULTILINE)
        assert direct, f"{context_name} has no direct dependency declaration"
        direct_dependencies = {
            item.strip().strip("`")
            for item in direct.group(1).split(",")
            if item.strip() and item.strip().lower() != "none"
        }
        assert direct_dependencies == EXPECTED_DIRECT_DEPENDENCIES[context_name]
        assert re.search(r"^\* Transitive dependencies:\s*.+$", body, re.MULTILINE)
        for term in _context_terms(path):
            assert term not in declared_terms, (
                f"{term!r} is owned by both {declared_terms[term]} and {context_name}"
            )
            declared_terms[term] = context_name
        assert f"{relative_path}" in body
        assert re.search(r"^\* Owned vocabulary:\s*", body, re.MULTILINE)
        assert re.search(r"^\* Relevant global ADRs:\s*", body, re.MULTILINE)

    assert declared_terms
    assert REQUIRED_DOMAIN_TERMS <= set(declared_terms)
    map_terms = re.findall(
        r"^\s*- `([^`]+)` → `([^`]+)`\s*$",
        MAP_PATH.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert map_terms
    assert {term for term, _owner in map_terms} == set(declared_terms)
    assert {owner for _term, owner in map_terms} <= set(REQUIRED_CONTEXTS)


def test_map_and_contexts_index_existing_global_adrs_without_rehousing_them() -> None:
    adr_paths = sorted((PROJECT_ROOT / "docs" / "adr").glob("[0-9][0-9][0-9][0-9]-*.md"))
    assert len(adr_paths) >= 42
    map_text = MAP_PATH.read_text(encoding="utf-8")
    for adr_path in adr_paths:
        relative = adr_path.relative_to(PROJECT_ROOT).as_posix()
        assert relative in map_text
    for relative_path in REQUIRED_CONTEXTS.values():
        text = (CONTEXT_ROOT / relative_path).read_text(encoding="utf-8")
        assert re.search(r"(?:docs/adr|\.\./\.\./adr)/\d{4}-[a-z0-9-]+\.md", text)


def test_active_phase_specs_route_through_the_map_and_owned_contexts() -> None:
    required_routes = {
        "docs/PHASE_04_SPECIFICATION.md": {
            "media-foundation",
            "source-planning",
            "subtitles",
        },
        ".scratch/phase-05-auditable-audio-analysis-prototype/spec.md": {
            "media-foundation",
            "source-planning",
            "subtitles",
            "audio-analysis",
        },
        "docs/PHASE_06_SPECIFICATION.md": {
            "media-foundation",
            "source-planning",
            "subtitles",
            "audio-analysis",
            "text-analysis",
        },
        "docs/PHASE_07_SPECIFICATION.md": {
            "media-foundation",
            "source-planning",
            "subtitles",
            "audio-analysis",
            "transcription",
            "text-analysis",
        },
    }
    for relative_path, contexts in required_routes.items():
        path = PROJECT_ROOT / relative_path
        assert path.is_file(), f"missing active specification {relative_path}"
        text = path.read_text(encoding="utf-8")
        map_link = (
            "../CONTEXT-MAP.md" if relative_path.startswith("docs/") else "../../CONTEXT-MAP.md"
        )
        context_prefix = (
            "contexts/" if relative_path.startswith("docs/") else "../../docs/contexts/"
        )
        assert map_link in text
        for context in contexts:
            assert f"{context_prefix}{context}/CONTEXT.md" in text
    phase_6 = (PROJECT_ROOT / "docs/PHASE_06_SPECIFICATION.md").read_text(encoding="utf-8")
    assert "optional" in phase_6.lower() and "audio-analysis" in phase_6


def test_agent_guidance_requires_context_map_routing() -> None:
    for path in AGENT_GUIDANCE_PATHS:
        guidance = path.read_text(encoding="utf-8")
        assert "CONTEXT-MAP.md" in guidance
        assert "owner Context" in guidance or "owning Context" in guidance
        assert "dependency" in guidance
        assert "CONTEXT.md` at the repository root" not in guidance


def test_runtime_migration_pointer_and_inventory_are_complete() -> None:
    assert RUNTIME_PATH.is_file()
    runtime_text = RUNTIME_PATH.read_text(encoding="utf-8")
    for term in RUNTIME_TERMS:
        assert f"**{term}**:" in runtime_text
    legacy_text = LEGACY_PATH.read_text(encoding="utf-8")
    assert "migration pointer" in legacy_text.lower()
    assert "CONTEXT-MAP.md" in legacy_text
    assert "RUNTIME_GOVERNANCE.md" in legacy_text
    assert "canonical" not in legacy_text.lower()
    assert "| Term |" not in legacy_text
    owner_index = MAP_PATH.read_text(encoding="utf-8").split("## Owner index", 1)[1]
    context_text = "\n".join(
        path.read_text(encoding="utf-8") for path in CONTEXT_ROOT.glob("*/CONTEXT.md")
    )
    for term in RUNTIME_TERMS:
        assert f"`{term}` →" not in owner_index
        assert f"**{term}**:" not in context_text

    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    assert inventory["schema_version"] == 1
    assert inventory["kind"] == "context-layout-migration"
    assert inventory["created"]
    assert inventory["modified"]
    assert inventory["retired"]
    assert inventory["external_reads"]
    assert inventory["retired_monolithic_terms"]
    assert inventory["runtime_terms"]
    assert inventory["relocated_constraints"]
    retired_terms = inventory["retired_monolithic_terms"]
    assert len(retired_terms) == RETIRED_MONOLITHIC_TERM_COUNT
    retired_by_term = {entry["term"]: entry["destination"] for entry in retired_terms}
    assert len(retired_by_term) == len(retired_terms)
    assert set(
        re.findall(
            r"^\s*- `([^`]+)` → `[^`]+`\s*$", MAP_PATH.read_text(encoding="utf-8"), re.MULTILINE
        )
    ) - {
        "Part",
        "RunBundle",
        "PresentationCorrection",
        "AlignmentCandidate",
        "SemanticSegment",
        "Chapter",
        "Primary subtitle coverage",
        "Publication boundary",
        "Future publication stage",
    } - POST_MIGRATION_TERMS == set(retired_by_term)
    for entry in retired_terms + inventory["runtime_terms"]:
        assert entry["term"] and entry["destination"]
    assert {entry["term"] for entry in inventory["runtime_terms"]} == RUNTIME_TERMS
    for term, destination in retired_by_term.items():
        assert destination in REQUIRED_CONTEXTS
        assert f"**{term}**:" in (CONTEXT_ROOT / REQUIRED_CONTEXTS[destination]).read_text(
            encoding="utf-8"
        )
    for entry in inventory["relocated_constraints"]:
        assert entry["constraint"] and entry["destination"]
        paths = _paths_in_destination(entry["destination"])
        assert paths
        assert paths <= RELOCATED_CONSTRAINT_DESTINATIONS
        for relative_path in paths:
            assert (PROJECT_ROOT / relative_path).is_file()
