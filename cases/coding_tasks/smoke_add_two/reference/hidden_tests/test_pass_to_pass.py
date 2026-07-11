"""PASS_TO_PASS: already passes against visible/solution.py's stub, must
keep passing after a correct submission (regression sentinel)."""

from solution import already_works


def test_already_works_identity() -> None:
    assert already_works(5) == 5
