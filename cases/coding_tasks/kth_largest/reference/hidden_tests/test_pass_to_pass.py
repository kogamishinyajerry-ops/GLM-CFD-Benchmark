"""PASS_TO_PASS: pass against BOTH the buggy stub and a correct submission.
Degenerate inputs where k-th largest == k-th smallest — regression sentinels
a correct fix must not break."""

from solution import kth_largest


def test_single_element() -> None:
    assert kth_largest([42], 1) == 42


def test_all_equal() -> None:
    assert kth_largest([5, 5, 5], 2) == 5
