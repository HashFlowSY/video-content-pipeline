"""Visual-Text Context: the deterministic Part-local page index (Phase 8, ticket 03).

Detection and sampling are fully deterministic and hold no model capability
(ADR 0047). From a sequence of per-frame metrics -- stability, a Text-value proxy
metric (edge density), and a region-scoped frame difference -- plus a
deterministic content fingerprint standing in for the on-screen text state, this
module derives:

* Part-local Visual pages: a ``visual_page_id`` scoped to exactly one Part
  (ADR 0048), keyed by the content fingerprint, with ordinals assigned in first-
  appearance order. Cross-Part correlation is never asserted here.
* Page appearance records: the first appearance and every reappearance of each
  page, with exact Part-relative times.
* A complete Retained frame inventory: every frame is retained with the reason it
  was or was not selected for OCR; nothing is discarded pipeline-side, and frames
  are Unpublished internal frames (they never appear in formal outputs).

The frame metrics are supplied by a hash-pinned synthetic frame-metric fixture:
no detection-stage OCR is consulted and no frame is extracted from user media
(the future real path substitutes pinned-ffmpeg extraction plus deterministic
metric computation for the fixture, changing nothing downstream). The same input
and the same rule versions always select the same frames, the same pages, and the
same appearance records; the rule versions are recorded in the index provenance.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from video_content_pipeline.evidence import InputEvidence, input_evidence
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.visual_text import VisualTextError

# Selection reasons recorded for every Retained frame. A page's representative is
# the single frame handed to the OCR stage; every other frame records why it was
# not selected, so the inventory is complete and auditable.
SELECTED_PAGE_REPRESENTATIVE = "selected_page_representative"
UNSELECTED_TRANSITION_FRAME = "unselected_transition_frame"
UNSELECTED_BELOW_TEXT_VALUE = "unselected_below_text_value"
UNSELECTED_DUPLICATE_OF_SELECTED = "unselected_duplicate_of_selected"

_METRIC_MIN = 0
_METRIC_MAX = 100


# --- Frame metrics and rules ------------------------------------------------


@dataclass(frozen=True)
class FrameMetric:
    """One extracted frame's deterministic detection metrics on the Part clock.

    ``content_fingerprint`` is a deterministic proxy for the on-screen text state
    (never recovered by OCR); ``stability``, ``edge_density`` (the Text-value proxy
    metric), and ``region_diff`` (region-scoped frame difference) are integer
    signals on ``[0, 100]``.
    """

    pts: ExactTime
    content_fingerprint: str
    stability: int
    edge_density: int
    region_diff: int


@dataclass(frozen=True)
class PageIndexRules:
    """The versioned deterministic thresholds for detection and sampling."""

    detection_version: str
    sampling_version: str
    stability_min: int
    region_diff_change: int
    text_value_min: int


# --- Pages, appearances, and retained frames --------------------------------


@dataclass(frozen=True)
class PageAppearance:
    """One contiguous appearance of a Visual page with exact Part-relative times.

    ``start`` and ``end`` are the first and last settled-frame times of the run and
    may be equal for a single-frame appearance; they are closed observed sample
    times, not a half-open interval.
    """

    start: ExactTime
    end: ExactTime
    frame_count: int

    def as_json(self) -> dict[str, object]:
        return {
            "start": _time_as_json(self.start),
            "end": _time_as_json(self.end),
            "frame_count": self.frame_count,
        }


@dataclass(frozen=True)
class VisualPage:
    """A Part-local stable on-screen text state identified by a deterministic fingerprint."""

    visual_page_id: str
    content_fingerprint: str
    appearances: tuple[PageAppearance, ...]
    selected_frame_pts: ExactTime | None

    def as_json(self) -> dict[str, object]:
        return {
            "visual_page_id": self.visual_page_id,
            "content_fingerprint": self.content_fingerprint,
            "appearances": [appearance.as_json() for appearance in self.appearances],
            "selected_frame_pts": (
                _time_as_json(self.selected_frame_pts)
                if self.selected_frame_pts is not None
                else None
            ),
        }


@dataclass(frozen=True)
class RetainedFrame:
    """One frame's inventory record: its metrics, its page, and its selection reason.

    Every extracted frame becomes exactly one record; ``published`` is always false
    because frames are Unpublished internal frames.
    """

    pts: ExactTime
    content_fingerprint: str
    stability: int
    edge_density: int
    region_diff: int
    visual_page_id: str | None
    selected: bool
    selection_reason: str
    published: bool = False

    def as_json(self) -> dict[str, object]:
        return {
            "pts": _time_as_json(self.pts),
            "content_fingerprint": self.content_fingerprint,
            "stability": self.stability,
            "edge_density": self.edge_density,
            "region_diff": self.region_diff,
            "visual_page_id": self.visual_page_id,
            "selected": self.selected,
            "selection_reason": self.selection_reason,
            "published": self.published,
        }


@dataclass(frozen=True)
class PartPageIndex:
    """The deterministic page index for one Part: its pages and full frame inventory."""

    part_id: str
    detection_version: str
    sampling_version: str
    pages: tuple[VisualPage, ...]
    retained_frames: tuple[RetainedFrame, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "detection_version": self.detection_version,
            "sampling_version": self.sampling_version,
            "pages": [page.as_json() for page in self.pages],
            "retained_frames": [frame.as_json() for frame in self.retained_frames],
        }


# --- OCR resource plan ------------------------------------------------------


@dataclass(frozen=True)
class OcrResourcePolicy:
    """The versioned deterministic OCR resource estimate and approved envelope.

    OCR runs one selected representative frame per Visual page; the per-frame
    constants turn a selected-frame count into conservative (high) time, memory,
    and disk estimates, and the ceilings define the approved envelope a planned
    attempt may not silently exceed. The version is recorded in provenance.
    """

    version: str
    seconds_per_selected_frame: int
    disk_bytes_per_selected_frame: int
    peak_working_set_bytes: int
    max_selected_frames: int
    max_disk_bytes: int
    max_peak_bytes: int


@dataclass(frozen=True)
class OcrResourcePlan:
    """The conservative OCR resource estimate for one attempt against its envelope.

    ``selected_frame_count`` is the number of Visual pages that selected an OCR
    representative across every Part in scope; the estimates are conservative highs
    and ``within_envelope`` is false when any estimate exceeds an approved ceiling,
    which is what makes the attempt pause rather than silently shrink its plan.
    ``serialized_execution`` records that OCR shares the single heavy-task queue and
    releases its evidence before any other heavy model may load.
    """

    policy_version: str
    selected_frame_count: int
    total_frame_count: int
    estimated_seconds: int
    estimated_peak_bytes: int
    estimated_disk_bytes: int
    max_selected_frames: int
    max_disk_bytes: int
    max_peak_bytes: int
    within_envelope: bool
    serialized_execution: bool = True

    def as_json(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "selected_frame_count": self.selected_frame_count,
            "total_frame_count": self.total_frame_count,
            "estimates": {
                "seconds": self.estimated_seconds,
                "peak_bytes": self.estimated_peak_bytes,
                "disk_bytes": self.estimated_disk_bytes,
            },
            "envelope": {
                "max_selected_frames": self.max_selected_frames,
                "max_disk_bytes": self.max_disk_bytes,
                "max_peak_bytes": self.max_peak_bytes,
            },
            "within_envelope": self.within_envelope,
            "serialized_execution": self.serialized_execution,
        }


def plan_ocr_resources(
    indices: Sequence[PartPageIndex], policy: OcrResourcePolicy
) -> OcrResourcePlan:
    """Derive the conservative OCR resource plan from the selected representatives.

    Exactly one frame per Visual page is handed to OCR -- the page's selected
    representative -- so ``selected_frame_count`` sums the pages that selected one
    across every Part; a page with no text-bearing frame selects none and costs
    nothing. Estimates are conservative highs and the plan is within the envelope
    only when every estimate is at or under its approved ceiling. The same page
    indices and policy always yield the same plan.
    """

    selected = sum(
        1 for index in indices for page in index.pages if page.selected_frame_pts is not None
    )
    total = sum(len(index.retained_frames) for index in indices)
    estimated_seconds = selected * policy.seconds_per_selected_frame
    estimated_disk = selected * policy.disk_bytes_per_selected_frame
    # Peak memory is frame-count-independent by design: OCR is a Serialized OCR
    # execution over one representative frame at a time, so the working set is the
    # single-frame model footprint regardless of how many frames are queued. The
    # ceiling therefore guards a policy misconfiguration (a working set that alone
    # overflows the envelope), not the per-run frame count, which the selected-frame
    # and disk ceilings bound.
    estimated_peak = policy.peak_working_set_bytes
    within = (
        selected <= policy.max_selected_frames
        and estimated_disk <= policy.max_disk_bytes
        and estimated_peak <= policy.max_peak_bytes
    )
    return OcrResourcePlan(
        policy_version=policy.version,
        selected_frame_count=selected,
        total_frame_count=total,
        estimated_seconds=estimated_seconds,
        estimated_peak_bytes=estimated_peak,
        estimated_disk_bytes=estimated_disk,
        max_selected_frames=policy.max_selected_frames,
        max_disk_bytes=policy.max_disk_bytes,
        max_peak_bytes=policy.max_peak_bytes,
        within_envelope=within,
    )


# --- Detection and sampling engine ------------------------------------------


@dataclass
class _SettledRun:
    """A maximal run of consecutive settled frames sharing one content fingerprint."""

    fingerprint: str
    indices: list[int]


def build_part_page_index(
    part_id: str, frames: Sequence[FrameMetric], rules: PageIndexRules
) -> PartPageIndex:
    """Derive the Part-local page index deterministically from frame metrics.

    Frames are ordered by exact time before detection. A frame is a transition
    frame when its region-scoped difference reaches ``region_diff_change`` or its
    stability falls below ``stability_min``; it belongs to no page. Maximal runs of
    consecutive settled frames sharing a content fingerprint are grouped into Page
    appearances. Each distinct fingerprint is a Part-local Visual page, its ordinal
    fixed by first-appearance order, and a fingerprint that returns reappears under
    the same ``visual_page_id``. The sampling rule selects one representative per
    page -- the earliest settled frame whose edge density reaches ``text_value_min``
    -- and every frame is retained with the reason it was or was not selected.
    """

    order = sorted(range(len(frames)), key=lambda index: (frames[index].pts.as_fraction(), index))
    ordered_frames = [frames[index] for index in order]
    runs = _settled_runs(ordered_frames, rules)

    fingerprint_ids: dict[str, str] = {}
    fingerprint_by_id: dict[str, str] = {}
    appearances_by_id: dict[str, list[PageAppearance]] = {}
    id_order: list[str] = []
    frame_page_id: dict[int, str] = {}
    for run in runs:
        page_id = fingerprint_ids.get(run.fingerprint)
        if page_id is None:
            page_id = f"page-{len(id_order) + 1:02d}"
            fingerprint_ids[run.fingerprint] = page_id
            fingerprint_by_id[page_id] = run.fingerprint
            appearances_by_id[page_id] = []
            id_order.append(page_id)
        run_frames = [ordered_frames[position] for position in run.indices]
        appearances_by_id[page_id].append(
            PageAppearance(
                start=run_frames[0].pts,
                end=run_frames[-1].pts,
                frame_count=len(run_frames),
            )
        )
        for position in run.indices:
            frame_page_id[position] = page_id

    representative_by_id = _representatives(ordered_frames, runs, fingerprint_ids, rules)
    pages = tuple(
        VisualPage(
            visual_page_id=page_id,
            content_fingerprint=fingerprint_by_id[page_id],
            appearances=tuple(appearances_by_id[page_id]),
            selected_frame_pts=_selected_pts(ordered_frames, representative_by_id.get(page_id)),
        )
        for page_id in id_order
    )
    retained = tuple(
        _retained_frame(
            position,
            ordered_frames[position],
            frame_page_id.get(position),
            representative_by_id,
            rules,
        )
        for position in range(len(ordered_frames))
    )
    return PartPageIndex(
        part_id=part_id,
        detection_version=rules.detection_version,
        sampling_version=rules.sampling_version,
        pages=pages,
        retained_frames=retained,
    )


def _settled_runs(frames: Sequence[FrameMetric], rules: PageIndexRules) -> list[_SettledRun]:
    """Group time-ordered frames into maximal settled runs, dropping transition frames.

    ``frames`` is already time-ordered; the returned indices are positions in that
    ordered sequence, which the caller maps back to original frames.
    """

    runs: list[_SettledRun] = []
    current: _SettledRun | None = None
    for index, frame in enumerate(frames):
        if _is_transition(frame, rules):
            current = None
            continue
        if current is None or current.fingerprint != frame.content_fingerprint:
            current = _SettledRun(frame.content_fingerprint, [index])
            runs.append(current)
        else:
            current.indices.append(index)
    return runs


def _is_transition(frame: FrameMetric, rules: PageIndexRules) -> bool:
    return frame.region_diff >= rules.region_diff_change or frame.stability < rules.stability_min


def _representatives(
    frames: Sequence[FrameMetric],
    runs: Sequence[_SettledRun],
    fingerprint_ids: Mapping[str, str],
    rules: PageIndexRules,
) -> dict[str, int | None]:
    """Pick each page's OCR representative: the earliest settled text-bearing frame.

    Runs arrive in time order, so the first qualifying frame encountered for a page
    is its earliest appearance's earliest text-bearing frame across every
    reappearance. A page with no frame reaching ``text_value_min`` selects none.
    """

    selected: dict[str, int | None] = {page_id: None for page_id in fingerprint_ids.values()}
    for run in runs:
        page_id = fingerprint_ids[run.fingerprint]
        if selected[page_id] is not None:
            continue
        for index in run.indices:
            if frames[index].edge_density >= rules.text_value_min:
                selected[page_id] = index
                break
    return selected


def _retained_frame(
    position: int,
    frame: FrameMetric,
    page_id: str | None,
    representative_by_id: Mapping[str, int | None],
    rules: PageIndexRules,
) -> RetainedFrame:
    """Record one frame with the reason it was or was not selected for OCR.

    The reason names why *this* frame was not chosen: a transition frame belongs to
    no page; a settled frame below the Text-value threshold is Below-text-value even
    when its page has a representative elsewhere; a text-bearing frame that simply
    was not the earliest is a duplicate of the selected representative.
    """

    if page_id is None:
        return _frame_record(frame, None, False, UNSELECTED_TRANSITION_FRAME)
    if representative_by_id.get(page_id) == position:
        return _frame_record(frame, page_id, True, SELECTED_PAGE_REPRESENTATIVE)
    if frame.edge_density < rules.text_value_min:
        return _frame_record(frame, page_id, False, UNSELECTED_BELOW_TEXT_VALUE)
    return _frame_record(frame, page_id, False, UNSELECTED_DUPLICATE_OF_SELECTED)


def _frame_record(
    frame: FrameMetric, page_id: str | None, selected: bool, reason: str
) -> RetainedFrame:
    return RetainedFrame(
        pts=frame.pts,
        content_fingerprint=frame.content_fingerprint,
        stability=frame.stability,
        edge_density=frame.edge_density,
        region_diff=frame.region_diff,
        visual_page_id=page_id,
        selected=selected,
        selection_reason=reason,
    )


def _selected_pts(
    ordered_frames: Sequence[FrameMetric], representative: int | None
) -> ExactTime | None:
    return None if representative is None else ordered_frames[representative].pts


def frames_in_scope(
    frames: Sequence[FrameMetric], intervals: Sequence[HalfOpenInterval]
) -> tuple[FrameMetric, ...]:
    """Keep only frames whose time falls inside one of the scope's half-open intervals."""

    return tuple(
        frame
        for frame in frames
        if any(interval.start <= frame.pts and frame.pts < interval.end for interval in intervals)
    )


# --- Rules and fixture loading ----------------------------------------------


def load_page_index_rules(project_root: Path) -> PageIndexRules:
    """Load the versioned detection and sampling thresholds, or reject a malformed file.

    Shares ``config/visual-text/rules.json`` with the command boundary's rule-version
    provenance loader; here the detection/sampling ``version`` strings plus the
    integer thresholds are required, so a stale or incomplete rules file is rejected
    before any detection runs.
    """

    path = project_root / "config" / "visual-text" / "rules.json"
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualTextError(
            "visual_text_rules_invalid", f"Visual-text rules cannot be read: {path}"
        ) from error
    if not isinstance(decoded, Mapping):
        raise VisualTextError("visual_text_rules_invalid", "Visual-text rules must be an object.")
    detection = _rules_section(decoded, "detection")
    sampling = _rules_section(decoded, "sampling")
    return PageIndexRules(
        detection_version=_rules_string(detection, "detection", "version"),
        sampling_version=_rules_string(sampling, "sampling", "version"),
        stability_min=_rules_threshold(detection, "detection", "stability_min"),
        region_diff_change=_rules_threshold(detection, "detection", "region_diff_change"),
        text_value_min=_rules_threshold(sampling, "sampling", "text_value_min"),
    )


def load_ocr_resource_policy(project_root: Path) -> OcrResourcePolicy:
    """Load the versioned OCR resource estimate and approved envelope, or reject it.

    Shares ``config/visual-text/rules.json`` with the other rule loaders; the
    ``ocr_execution`` section carries the version, the conservative per-frame
    estimate constants, and the envelope ceilings. Any missing or non-positive
    field raises ``visual_text_rules_invalid`` before an attempt plans OCR, so the
    resource confirmation pause never presents an estimate derived from a malformed
    policy.
    """

    path = project_root / "config" / "visual-text" / "rules.json"
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualTextError(
            "visual_text_rules_invalid", f"Visual-text rules cannot be read: {path}"
        ) from error
    if not isinstance(decoded, Mapping):
        raise VisualTextError("visual_text_rules_invalid", "Visual-text rules must be an object.")
    section = _rules_section(decoded, "ocr_execution")
    return OcrResourcePolicy(
        version=_rules_string(section, "ocr_execution", "version"),
        seconds_per_selected_frame=_rules_positive(
            section, "ocr_execution", "seconds_per_selected_frame"
        ),
        disk_bytes_per_selected_frame=_rules_positive(
            section, "ocr_execution", "disk_bytes_per_selected_frame"
        ),
        peak_working_set_bytes=_rules_positive(section, "ocr_execution", "peak_working_set_bytes"),
        max_selected_frames=_rules_positive(section, "ocr_execution", "max_selected_frames"),
        max_disk_bytes=_rules_positive(section, "ocr_execution", "max_disk_bytes"),
        max_peak_bytes=_rules_positive(section, "ocr_execution", "max_peak_bytes"),
    )


def _rules_section(document: Mapping[str, object], key: str) -> Mapping[str, object]:
    section = document.get(key)
    if not isinstance(section, Mapping):
        raise VisualTextError(
            "visual_text_rules_invalid", f"Visual-text rules need a {key!r} object."
        )
    return section


def _rules_string(section: Mapping[str, object], key: str, field: str) -> str:
    value = section.get(field)
    if not isinstance(value, str) or not value:
        raise VisualTextError(
            "visual_text_rules_invalid", f"Visual-text {key!r} rules need a {field!r} string."
        )
    return value


def _is_metric_int(value: object) -> bool:
    """Return whether ``value`` is an integer metric in ``[0, 100]`` (a ``bool`` is not)."""

    if not isinstance(value, int) or isinstance(value, bool):
        return False
    return _METRIC_MIN <= value <= _METRIC_MAX


def _rules_threshold(section: Mapping[str, object], key: str, field: str) -> int:
    value = section.get(field)
    if not _is_metric_int(value):
        raise VisualTextError(
            "visual_text_rules_invalid",
            f"Visual-text {key!r} rules need an integer {field!r} in [0, 100].",
        )
    assert isinstance(value, int)
    return value


def _rules_positive(section: Mapping[str, object], key: str, field: str) -> int:
    """Read a positive integer resource constant (a ``bool`` is rejected)."""

    value = section.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise VisualTextError(
            "visual_text_rules_invalid",
            f"Visual-text {key!r} rules need a positive integer {field!r}.",
        )
    return value


@dataclass(frozen=True)
class FrameMetricFixture:
    """A hash-pinned synthetic frame-metric fixture bound to one Part.

    It declares the detection and sampling rule versions it was authored for so a
    later attempt can detect a stale fixture (rule drift) before building an index.
    """

    part_id: str
    detection_version: str
    sampling_version: str
    frames: tuple[FrameMetric, ...]
    evidence: InputEvidence


def load_frame_metric_fixture(path: Path, part_id: str) -> FrameMetricFixture:
    """Load and hash-pin a Part's synthetic frame-metric fixture.

    The fixture stands in for pinned-ffmpeg frame extraction plus deterministic
    metric computation; no frame of user media is read here. The file is hash-pinned
    as read-only evidence, its ``part_id`` must match the requested Part, and every
    frame must carry a fingerprint plus integer ``stability``/``edge_density``/
    ``region_diff`` metrics in ``[0, 100]``. Any malformed field raises
    ``visual_text_frame_metrics_invalid`` before detection proceeds.
    """

    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualTextError(
            "visual_text_frame_metrics_invalid",
            f"Frame-metric fixture cannot be read: {path}",
        ) from error
    if not isinstance(decoded, Mapping):
        raise VisualTextError(
            "visual_text_frame_metrics_invalid", "Frame-metric fixture must be an object."
        )
    if decoded.get("part_id") != part_id:
        raise VisualTextError(
            "visual_text_frame_metrics_invalid",
            f"Frame-metric fixture Part identity does not match {part_id!r}.",
        )
    detection_version = _fixture_string(decoded, "detection_rule_version")
    sampling_version = _fixture_string(decoded, "sampling_rule_version")
    raw_frames = decoded.get("frames")
    if not isinstance(raw_frames, list):
        raise VisualTextError(
            "visual_text_frame_metrics_invalid", "Frame-metric fixture needs a frames array."
        )
    frames = tuple(_fixture_frame(entry) for entry in raw_frames)
    return FrameMetricFixture(
        part_id=part_id,
        detection_version=detection_version,
        sampling_version=sampling_version,
        frames=frames,
        evidence=input_evidence(path),
    )


def _fixture_string(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise VisualTextError(
            "visual_text_frame_metrics_invalid", f"Frame-metric fixture needs a {field!r} string."
        )
    return value


def _fixture_frame(entry: object) -> FrameMetric:
    if not isinstance(entry, Mapping):
        raise VisualTextError(
            "visual_text_frame_metrics_invalid", "A frame-metric entry must be an object."
        )
    fingerprint = entry.get("content_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise VisualTextError(
            "visual_text_frame_metrics_invalid", "A frame-metric entry needs a content_fingerprint."
        )
    return FrameMetric(
        pts=_fixture_time(entry.get("pts")),
        content_fingerprint=fingerprint,
        stability=_fixture_metric(entry, "stability"),
        edge_density=_fixture_metric(entry, "edge_density"),
        region_diff=_fixture_metric(entry, "region_diff"),
    )


def _fixture_time(value: object) -> ExactTime:
    if (
        not isinstance(value, Mapping)
        or not isinstance(value.get("numerator"), int)
        or isinstance(value.get("numerator"), bool)
        or not isinstance(value.get("denominator"), int)
        or isinstance(value.get("denominator"), bool)
        or value.get("denominator") == 0
    ):
        raise VisualTextError(
            "visual_text_frame_metrics_invalid",
            "A frame-metric pts must be a {numerator, denominator} object.",
        )
    numerator = value["numerator"]
    denominator = value["denominator"]
    assert isinstance(numerator, int) and isinstance(denominator, int)
    return ExactTime(numerator, denominator)


def _fixture_metric(entry: Mapping[str, object], field: str) -> int:
    value = entry.get(field)
    if not _is_metric_int(value):
        raise VisualTextError(
            "visual_text_frame_metrics_invalid",
            f"A frame-metric {field!r} must be an integer in [0, 100].",
        )
    assert isinstance(value, int)
    return value


def _time_as_json(time: ExactTime) -> dict[str, int]:
    return {"numerator": time.numerator, "denominator": time.denominator}
