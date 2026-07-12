"""Buggy stub for merge_intervals — reimplement correctly.

This single-pass sweep merges each interval into the running one, but it
NEVER sorts the input first. On unsorted input it produces wrong results:
the running interval keeps the earlier item's start, and an interval whose
true position is earlier gets swallowed with the wrong endpoints.
"""


def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    """Merge overlapping intervals (BUG: assumes input is start-sorted)."""
    if len(intervals) == 0:
        return []
    merged = [list(intervals[0])]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged
