"""PASS_TO_PASS: unquoted-field behavior the naive stub already gets right,
must keep passing after a correct submission (regression sentinel)."""

from solution import split_csv_line


def test_plain_fields() -> None:
    assert split_csv_line("a,b,c") == ["a", "b", "c"]


def test_single_field() -> None:
    assert split_csv_line("onlyone") == ["onlyone"]
