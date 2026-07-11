"""FAIL_TO_PASS: fail against visible/solution.py's opens-vs-closes-count
stub, must pass against a correct stack-based submission."""

from solution import first_unbalanced_index


def test_wrong_nesting_type() -> None:
    assert first_unbalanced_index("([)]") == 2


def test_wrong_close_type() -> None:
    assert first_unbalanced_index("(]") == 1


def test_unclosed_open_at_end() -> None:
    assert first_unbalanced_index("()(") == 2
