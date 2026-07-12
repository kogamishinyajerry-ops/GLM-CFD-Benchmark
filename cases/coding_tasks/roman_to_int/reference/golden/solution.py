"""Golden solution for roman_to_int (case admission evidence; not scored).

Left-to-right scan: a symbol whose value is smaller than the symbol
immediately to its right is subtracted (subtractive notation), otherwise
it is added.
"""

_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman_to_int(s: str) -> int:
    """Convert a Roman numeral string to its integer value."""
    total = 0
    for i, ch in enumerate(s):
        value = _VALUES[ch]
        if i + 1 < len(s) and value < _VALUES[s[i + 1]]:
            total -= value
        else:
            total += value
    return total
