"""PASS_TO_PASS: cases the count-only stub already gets right (fully
balanced / empty), must keep passing after a correct submission."""

from solution import first_unbalanced_index


def test_fully_balanced() -> None:
    assert first_unbalanced_index("()[]{}") == -1


def test_empty_string() -> None:
    assert first_unbalanced_index("") == -1
