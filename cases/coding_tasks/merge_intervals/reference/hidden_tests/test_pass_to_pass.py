"""PASS_TO_PASS: pass against BOTH the buggy stub and a correct submission.
These use already-sorted input, where the no-sort sweep happens to be right —
regression sentinels that a correct fix must not break."""

from solution import merge_intervals


def test_sorted_disjoint_unchanged() -> None:
    assert merge_intervals([[1, 3], [5, 7]]) == [[1, 3], [5, 7]]


def test_sorted_overlap_merges() -> None:
    assert merge_intervals([[1, 4], [2, 5]]) == [[1, 5]]
