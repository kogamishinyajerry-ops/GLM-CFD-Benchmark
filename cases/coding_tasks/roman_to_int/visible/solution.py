"""Task: implement roman_to_int(s) for an uppercase Roman numeral string.
See README.md for the full semantics and examples.

The stub below is intentionally buggy: it sums each symbol's value
independently, so it ignores SUBTRACTIVE notation and wrongly reports
"IV" as 6 (1 + 5) instead of 4.
"""

_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman_to_int(s: str) -> int:
    """Convert a Roman numeral to an integer (buggy: pure summation)."""
    return sum(_VALUES[ch] for ch in s)
