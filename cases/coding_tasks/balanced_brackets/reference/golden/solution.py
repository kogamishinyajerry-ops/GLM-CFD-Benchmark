"""Golden solution for balanced_brackets (case admission evidence; not
scored). Stack-based bracket matcher."""

_PAIRS = {")": "(", "]": "[", "}": "{"}
_OPENERS = frozenset(_PAIRS.values())
_CLOSERS = frozenset(_PAIRS.keys())


def first_unbalanced_index(s: str) -> int:
    """Return the index of the first bracket breaking balance, -1 if none."""
    stack: list[tuple[int, str]] = []
    for index, char in enumerate(s):
        if char in _OPENERS:
            stack.append((index, char))
        elif char in _CLOSERS:
            if len(stack) == 0 or stack[-1][1] != _PAIRS[char]:
                return index
            stack.pop()
    if len(stack) > 0:
        return stack[0][0]
    return -1
