"""FAIL_TO_PASS: fails against visible/solution.py's stub, must pass against
a correct submission."""

from solution import add_two


def test_add_two_positive() -> None:
    assert add_two(2) == 4
