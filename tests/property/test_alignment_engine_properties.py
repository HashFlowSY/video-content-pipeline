"""Property tests for the real forced-alignment engine mapping (Phase 11 ticket 08).

Over generated derivatives, cue windows, and window-local aligner items, these
prove the ticket's mapping invariant: a cue's window-local ``{start, end}`` seconds
project back onto the authoritative source timeline *exactly*, with the proposed
interval's bounds landing on the derivative sample boundaries the offset implies
and always inside the derivative coverage. They run under the deterministic gate
profile.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from tests.support import hypothesis_profiles  # noqa: F401  (registers the gate profile)
from video_content_pipeline.alignment_engine import (
    ALIGNER_SAMPLE_RATE,
    proposal_from_alignment_items,
)
from video_content_pipeline.audio_analysis import AlignmentCue
from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval

SR = ALIGNER_SAMPLE_RATE


@st.composite
def _case(draw: st.DrawFn) -> tuple[DerivativeTimeMapping, int, float, float]:
    """A derivative mapping, a window start sample, and one item's second bounds."""

    seconds = draw(st.integers(min_value=1, max_value=30))
    sample_count = seconds * SR
    start_offset = draw(st.integers(min_value=0, max_value=5)) * SR
    mapping = DerivativeTimeMapping(
        HalfOpenInterval(ExactTime(start_offset, SR), ExactTime(start_offset + sample_count, SR)),
        SR,
        sample_count,
    )
    window_start = draw(st.integers(min_value=0, max_value=sample_count))
    item_start = draw(st.floats(min_value=0.0, max_value=float(seconds), allow_nan=False))
    item_end = draw(
        st.floats(min_value=item_start + 0.01, max_value=float(seconds) + 1.0, allow_nan=False)
    )
    return mapping, window_start, item_start, item_end


@given(_case())
def test_item_seconds_project_exactly_onto_derivative_sample_boundaries(
    case: tuple[DerivativeTimeMapping, int, float, float],
) -> None:
    mapping, window_start, item_start, item_end = case
    cue = AlignmentCue(1, "x", HalfOpenInterval(ExactTime(0), ExactTime(1)))

    proposal = proposal_from_alignment_items(
        cue,
        window_start,
        [{"text": "x", "start": item_start, "end": item_end}],
        mapping,
        1.0,
    )
    if proposal is None:
        return  # a span that snaps to zero length after clamping is dropped, by design

    expected_start_sample = max(0, min(window_start + round(item_start * SR), mapping.sample_count))
    expected_end_sample = max(0, min(window_start + round(item_end * SR), mapping.sample_count))

    # The interval bounds are exactly the mapping of those sample boundaries: no
    # drift, and always inside the derivative coverage.
    assert proposal.interval.start == mapping.source_time_for_sample(expected_start_sample)
    assert proposal.interval.end == mapping.source_time_for_sample(expected_end_sample)
    assert mapping.source_interval.start <= proposal.interval.start
    assert proposal.interval.end <= mapping.source_interval.end
