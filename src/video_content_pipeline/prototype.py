"""Phase 11 ticket 13: the maintainer-invoked capability prototype harness.

Each real model capability (vad, diarization, forced_alignment, asr_primary,
asr_review, ocr_primary, text_semantics) is proven on the ticket-12 material as
*retained evidence*, never as a pytest assertion. A prototype run measures the
device baseline (real-time factor, peak memory), runs the engineering checks
(structurally valid contract output, gates hold, peak memory <= 12 GiB, hub
offline guards proven), retains the measured baseline to seed plan estimation,
and emits a short zh+en sample for maintainer eyeball. Maintainer review is the
quality gate: a rejected sample bounces to the recorded fallback as a new
confirmation, not an argument.

The measured model peaks are retained as evidence rather than written into the
registry's ``resource_estimate``: every registry candidate already carries a
``dependency_plan``, so recording an in-envelope estimate would flip it
``unsupported -> eligible`` and change production capability state -- which
ADR 0037 (real engines slot beside, never replace, the offline adapters) forbids
here. The decode-throughput estimate is the surface that genuinely upgrades from
``low`` to observed, through the existing full-decode-validation path.

This module's deterministic core -- record shape, real-time-factor and envelope
math, sample rendering, and the baseline recording surfaces -- is the
pytest-gated part; the heavy per-capability run orchestration lives in
:mod:`video_content_pipeline.prototype_runs` and is maintainer-invoked.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from video_content_pipeline.capabilities import MAX_MODEL_RESOURCE_BYTES
from video_content_pipeline.model_runtime import HUB_OFFLINE_GUARDS

#: The seven real capabilities each proven by a Phase 11 ticket 13 prototype, in
#: the pipeline's own dependency order (VAD chunking feeds diarization, alignment
#: and ASR; ASR feeds the alignment/text_semantics text per the ticket-13 plan).
PROTOTYPE_CAPABILITIES: tuple[str, ...] = (
    "vad",
    "diarization",
    "forced_alignment",
    "asr_primary",
    "asr_review",
    "ocr_primary",
    "text_semantics",
)

#: The single test machine (Phase 11 hardware truth: Apple M1, 16 GiB).
DEVICE_CLASS = "apple-m1"

#: zh and en must both be represented across the prototype evidence (ticket 13).
PROTOTYPE_LANGUAGES = frozenset({"zh", "en"})


class PrototypeError(ValueError):
    """A typed prototype-harness failure carrying a machine-readable ``reason``."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _fraction_as_json(value: Fraction) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


@dataclass(frozen=True)
class PrototypeTiming:
    """The exact media duration processed and the measured wall-clock elapsed."""

    media_seconds: Fraction
    wall_seconds: Fraction

    def __post_init__(self) -> None:
        if self.media_seconds <= 0 or self.wall_seconds <= 0:
            raise PrototypeError(
                "prototype_timing_invalid",
                "Prototype timing needs positive media and wall-clock seconds.",
            )

    @property
    def real_time_factor(self) -> Fraction:
        """Media seconds processed per wall-clock second (higher is faster)."""

        return self.media_seconds / self.wall_seconds

    def as_json(self) -> dict[str, object]:
        return {
            "media_seconds": _fraction_as_json(self.media_seconds),
            "wall_seconds": _fraction_as_json(self.wall_seconds),
            "real_time_factor": _fraction_as_json(self.real_time_factor),
            "real_time_factor_approx": round(float(self.real_time_factor), 4),
        }


@dataclass(frozen=True)
class EngineeringCheck:
    """One named pass/fail engineering assertion about a prototype run."""

    name: str
    passed: bool
    detail: str

    def as_json(self) -> dict[str, object]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class AssetIdentity:
    """One pinned asset a prototype run loaded, named and hash-bound."""

    name: str
    sha256: str

    def as_json(self) -> dict[str, object]:
        return {"name": self.name, "sha256": self.sha256}


def within_envelope(peak_memory_bytes: int) -> bool:
    """True when a measured peak fits the shared 12 GiB resource envelope."""

    return 0 <= peak_memory_bytes <= MAX_MODEL_RESOURCE_BYTES


def envelope_check(peak_memory_bytes: int) -> EngineeringCheck:
    """Grade a measured peak against the shared 12 GiB resource envelope."""

    return EngineeringCheck(
        "peak_within_envelope",
        within_envelope(peak_memory_bytes),
        f"peak {peak_memory_bytes} bytes vs {MAX_MODEL_RESOURCE_BYTES} byte envelope",
    )


def offline_guard_names() -> tuple[str, ...]:
    """The hub-offline environment guard names a real run must run under."""

    return tuple(sorted(HUB_OFFLINE_GUARDS))


@dataclass(frozen=True)
class PrototypeRecord:
    """A retained record of one capability prototype run over real material."""

    capability: str
    candidate_id: str
    language: str
    source_id: str
    device_class: str
    command: tuple[str, ...]
    asset_identities: tuple[AssetIdentity, ...]
    timing: PrototypeTiming
    peak_memory_bytes: int
    checks: tuple[EngineeringCheck, ...]
    offline_guards: tuple[str, ...]
    sample_relpath: str
    created_at: str

    def __post_init__(self) -> None:
        if self.capability not in PROTOTYPE_CAPABILITIES:
            raise PrototypeError(
                "prototype_capability_unknown", f"Unknown prototype capability {self.capability!r}."
            )
        if self.language not in PROTOTYPE_LANGUAGES:
            raise PrototypeError(
                "prototype_language_unknown", f"Unknown prototype language {self.language!r}."
            )
        if self.peak_memory_bytes < 0:
            raise PrototypeError(
                "prototype_peak_invalid", "Prototype peak memory cannot be negative."
            )

    @property
    def peak_within_envelope(self) -> bool:
        return within_envelope(self.peak_memory_bytes)

    @property
    def engineering_passed(self) -> bool:
        """True only when every check passed, the peak fits, and guards ran."""

        return (
            self.peak_within_envelope
            and bool(self.offline_guards)
            and all(check.passed for check in self.checks)
        )

    @property
    def status(self) -> str:
        return "engineering_pass" if self.engineering_passed else "engineering_fail"

    def as_json(self) -> dict[str, object]:
        return {
            "capability": self.capability,
            "candidate_id": self.candidate_id,
            "language": self.language,
            "source_id": self.source_id,
            "device_class": self.device_class,
            "command": list(self.command),
            "asset_identities": [identity.as_json() for identity in self.asset_identities],
            "timing": self.timing.as_json(),
            "peak_memory_bytes": self.peak_memory_bytes,
            "envelope_bytes": MAX_MODEL_RESOURCE_BYTES,
            "peak_within_envelope": self.peak_within_envelope,
            "checks": [check.as_json() for check in self.checks],
            "offline_guards": list(self.offline_guards),
            "sample_relpath": self.sample_relpath,
            "status": self.status,
            "created_at": self.created_at,
        }


def render_sample_markdown(
    *,
    capability: str,
    candidate_id: str,
    language: str,
    source_id: str,
    timing: PrototypeTiming,
    peak_memory_bytes: int,
    entries: Sequence[str],
    truncated: bool,
) -> str:
    """Render a short, human-eyeball sample document for maintainer review.

    ``entries`` are the capability-specific sample lines (a transcript excerpt,
    aligned cues, speaker turns, OCR items, or a segment summary); the caller
    formats them so this renderer stays capability-agnostic.
    """

    peak_gib = round(peak_memory_bytes / 1024**3, 3)
    lines = [
        f"# Prototype sample — {capability} ({language})",
        "",
        f"- Candidate: `{candidate_id}`",
        f"- Source: `{source_id}`",
        f"- Real-time factor: {round(float(timing.real_time_factor), 3)}x "
        f"({timing.media_seconds} media s / {timing.wall_seconds} wall s)",
        f"- Peak memory: {peak_gib} GiB",
        "",
        "## Sample",
        "",
    ]
    if entries:
        lines.extend(str(entry) for entry in entries)
    else:
        lines.append("_(no sample entries produced)_")
    if truncated:
        lines.extend(["", "_(sample truncated for eyeball review)_"])
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class DeviceBaseline:
    """One measured per-capability device baseline for plan estimation seeding."""

    capability: str
    candidate_id: str
    device_class: str
    real_time_factor: Fraction
    peak_memory_bytes: int
    basis: str
    confidence: str = "measured"

    def as_json(self) -> dict[str, object]:
        return {
            "capability": self.capability,
            "candidate_id": self.candidate_id,
            "device_class": self.device_class,
            "real_time_factor": _fraction_as_json(self.real_time_factor),
            "real_time_factor_approx": round(float(self.real_time_factor), 4),
            "peak_memory_bytes": self.peak_memory_bytes,
            "basis": self.basis,
            "confidence": self.confidence,
        }

    @classmethod
    def from_json(cls, entry: Mapping[str, object]) -> DeviceBaseline:
        factor = entry.get("real_time_factor")
        if not isinstance(factor, Mapping):
            raise PrototypeError(
                "prototype_baselines_invalid", "A device baseline needs a real_time_factor."
            )
        return cls(
            capability=_string(entry, "capability"),
            candidate_id=_string(entry, "candidate_id"),
            device_class=_string(entry, "device_class"),
            real_time_factor=Fraction(_int(factor, "numerator"), _int(factor, "denominator")),
            peak_memory_bytes=_int(entry, "peak_memory_bytes"),
            basis=_string(entry, "basis"),
            confidence=_string(entry, "confidence"),
        )

    @property
    def identity(self) -> tuple[str, str, str]:
        """The (capability, candidate, basis) key a later write replaces."""

        return (self.capability, self.candidate_id, self.basis)


def load_device_baselines(path: Path) -> tuple[DeviceBaseline, ...]:
    """Load the retained device-baseline history, or an empty history if absent."""

    if not path.exists():
        return ()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PrototypeError(
            "prototype_baselines_invalid", "Device-baseline history cannot be read."
        ) from error
    entries = document.get("baselines") if isinstance(document, Mapping) else None
    if not isinstance(entries, list):
        raise PrototypeError(
            "prototype_baselines_invalid", "Device-baseline history has no baselines list."
        )
    return tuple(DeviceBaseline.from_json(entry) for entry in entries)


def write_device_baselines(path: Path, baselines: Sequence[DeviceBaseline]) -> None:
    """Merge ``baselines`` into the retained history, replacing matched identities.

    A later measurement for the same (capability, candidate, basis) supersedes an
    earlier one so re-running a prototype refreshes rather than duplicates its
    baseline; every other prior baseline is retained.
    """

    merged: dict[tuple[str, str, str], DeviceBaseline] = {
        baseline.identity: baseline for baseline in load_device_baselines(path)
    }
    for baseline in baselines:
        merged[baseline.identity] = baseline
    ordered = sorted(merged.values(), key=lambda item: item.identity)
    payload = {
        "schema_version": 1,
        "device_class": DEVICE_CLASS,
        "baselines": [baseline.as_json() for baseline in ordered],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _required(entry: Mapping[str, object], key: str) -> object:
    if key not in entry:
        raise PrototypeError("prototype_baselines_invalid", f"A device baseline needs {key!r}.")
    return entry[key]


def _string(entry: Mapping[str, object], key: str) -> str:
    value = _required(entry, key)
    if not isinstance(value, str):
        raise PrototypeError(
            "prototype_baselines_invalid", f"Device baseline {key!r} must be text."
        )
    return value


def _int(entry: Mapping[str, object], key: str) -> int:
    value = _required(entry, key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise PrototypeError(
            "prototype_baselines_invalid", f"Device baseline {key!r} must be an integer."
        )
    return value
