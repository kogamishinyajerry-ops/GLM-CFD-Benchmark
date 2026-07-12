"""Golden solution for kth_largest (case admission evidence; not scored).

Sort descending and index the (k-1)-th element.
"""


def kth_largest(nums: list[int], k: int) -> int:
    """Return the k-th largest element (k=1 is the maximum)."""
    return sorted(nums, reverse=True)[k - 1]
