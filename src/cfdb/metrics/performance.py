"""Performance budget checking."""

from __future__ import annotations

from cfdb.schema import BudgetSpec, TimingSpec


def check_budget(timing: TimingSpec, budget: BudgetSpec) -> list[str]:
    """Check if timing exceeds budget constraints.

    Args:
        timing: Actual run timing.
        budget: Resource budget constraints.

    Returns:
        List of warning messages (empty if within budget).
    """
    notes: list[str] = []
    if (
        budget.max_runtime_sec is not None
        and timing.wall_time_sec > budget.max_runtime_sec
    ):
        notes.append(
            f"budget exceeded: wall_time {timing.wall_time_sec:.2f}s > "
            f"max_runtime_sec {budget.max_runtime_sec}s"
        )
    return notes
