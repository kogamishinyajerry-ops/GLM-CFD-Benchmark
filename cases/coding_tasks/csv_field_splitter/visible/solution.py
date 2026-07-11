"""Task: fix split_csv_line so it correctly handles RFC4180-style quoted
fields. See README.md for full examples.

The stub below is intentionally buggy: it naively splits on every comma,
which breaks as soon as a field is quoted.
"""


def split_csv_line(line: str) -> list[str]:
    """Split a single CSV line into fields (buggy stub -- fix the quoting bug)."""
    return line.split(",")
