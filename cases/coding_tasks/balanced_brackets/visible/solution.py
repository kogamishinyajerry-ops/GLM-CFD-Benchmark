"""Task: implement first_unbalanced_index(s) for a '()[]{}' bracket string.
See README.md for the full semantics and examples.

The stub below is intentionally buggy: it only compares the total count
of opens vs. closes, so it wrongly reports "([)]" (2 opens, 2 closes) as
balanced even though the nesting order is wrong.
"""

_PAIRS = {")": "(", "]": "[", "}": "{"}
_OPENERS = frozenset(_PAIRS.values())
_CLOSERS = frozenset(_PAIRS.keys())


def first_unbalanced_index(s: str) -> int:
    """Return the index of the first bracket breaking balance (buggy stub)."""
    opens = 0
    for char in s:
        if char in _OPENERS:
            opens += 1
        elif char in _CLOSERS:
            opens -= 1
    return -1 if opens == 0 else 0
