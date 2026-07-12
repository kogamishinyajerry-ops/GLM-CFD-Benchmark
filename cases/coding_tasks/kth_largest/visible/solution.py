"""Buggy stub for kth_largest — reimplement correctly.

This returns the k-th SMALLEST element: it sorts ascending and indexes
(k-1). It only coincides with the k-th largest on degenerate inputs (a
single element, or all-equal values).
"""


def kth_largest(nums: list[int], k: int) -> int:
    """Return the k-th largest element (BUG: sorts ascending, so k-th smallest)."""
    return sorted(nums)[k - 1]
