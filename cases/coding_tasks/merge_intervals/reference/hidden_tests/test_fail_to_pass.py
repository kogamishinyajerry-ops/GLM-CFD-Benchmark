"""FAIL_TO_PASS: fail against visible/solution.py's no-sort sweep, must pass
against a correct submission that sorts by start before merging."""

from solution import merge_intervals


def test_unsorted_pair_overlaps() -> None:
    assert merge_intervals([[2, 6], [1, 3]]) == [[1, 6]]


def test_unsorted_triple_all_merge() -> None:
    assert merge_intervals([[8, 10], [1, 3], [2, 6]]) == [[1, 6], [8, 10]]


def test_unsorted_disjoint_after_sort() -> None:
    assert merge_intervals([[5, 7], [0, 4]]) == [[0, 4], [5, 7]]
