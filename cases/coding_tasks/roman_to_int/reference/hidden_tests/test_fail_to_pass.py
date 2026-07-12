"""FAIL_TO_PASS: fail against visible/solution.py's pure-summation stub,
must pass against a correct submission that honors subtractive notation."""

from solution import roman_to_int


def test_subtractive_four() -> None:
    assert roman_to_int("IV") == 4


def test_subtractive_nine() -> None:
    assert roman_to_int("IX") == 9


def test_subtractive_inside_larger() -> None:
    assert roman_to_int("XLII") == 42
