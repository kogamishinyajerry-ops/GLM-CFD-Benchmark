"""PASS_TO_PASS: purely additive numerals the summation stub already gets
right, must keep passing after a correct submission (regression sentinel)."""

from solution import roman_to_int


def test_additive_three() -> None:
    assert roman_to_int("III") == 3


def test_additive_fifteen() -> None:
    assert roman_to_int("XV") == 15
