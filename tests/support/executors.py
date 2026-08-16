"""Stage executors that stand in for a killed run.

Extracted from Phase 9's in-file crash executor so every recovery and
fault-matrix test drives the same kill behaviour through one implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from video_content_pipeline.stage_dag import (
    StageInvalidationKey,
    StageResult,
    StageUnit,
)


@dataclass
class KillingExecutor:
    """A :data:`StageExecutor` that completes ``survive`` units then dies mid-unit.

    The first ``survive`` units are checkpointed as completed; the next one raises
    ``RuntimeError``, standing in for a process killed part-way through a unit
    (a power loss or SIGKILL). ``executed`` records the units that finished before
    the kill, so a test can assert exactly what was checkpointed on disk.
    """

    survive: int
    executed: list[StageUnit] = field(default_factory=list)

    def __call__(self, unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        if len(self.executed) >= self.survive:
            raise RuntimeError("process killed mid-unit")
        self.executed.append(unit)
        return StageResult.completed()
