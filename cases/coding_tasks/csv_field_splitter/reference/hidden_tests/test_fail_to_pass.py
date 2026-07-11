"""FAIL_TO_PASS: fail against visible/solution.py's naive-split stub, must
pass against a correct submission that respects quoting."""

from solution import split_csv_line


def test_quoted_field_with_comma() -> None:
    assert split_csv_line('"a,b",c') == ["a,b", "c"]


def test_quoted_field_with_escaped_quote() -> None:
    assert split_csv_line('"say ""hi""",ok') == ['say "hi"', "ok"]


def test_quoted_field_at_end() -> None:
    assert split_csv_line('x,"y,z"') == ["x", "y,z"]
