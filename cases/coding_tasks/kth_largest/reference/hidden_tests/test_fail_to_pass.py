"""FAIL_TO_PASS: fail against visible/solution.py's sort-ascending stub, must
pass against a correct submission that returns the k-th largest."""

from solution import kth_largest


def test_second_largest_distinct() -> None:
    assert kth_largest([3, 1, 4, 1, 5, 9, 2, 6], 2) == 6


def test_first_largest_is_max() -> None:
    assert kth_largest([10, 20, 30], 1) == 30


def test_kth_largest_with_duplicates() -> None:
    assert kth_largest([7, 2, 7, 2], 2) == 7
