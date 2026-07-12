"""Golden solution for merge_intervals (case admission evidence; not scored).

Sort the intervals by start, then sweep once merging any interval that
overlaps (or touches) the running interval.
"""


def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    """Merge all overlapping/touching intervals; result is start-sorted."""
    if len(intervals) == 0:
        return []
    ordered = sorted(intervals, key=lambda iv: iv[0])
    merged = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged
