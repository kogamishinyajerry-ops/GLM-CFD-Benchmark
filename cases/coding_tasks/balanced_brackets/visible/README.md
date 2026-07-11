# Task: first_unbalanced_index

Implement `first_unbalanced_index(s)` in `solution.py` for a string
containing `()[]{}` (and possibly other, ignored, characters).

Semantics (stack-based bracket matching):

- Walk the string left to right, tracking open brackets on a stack.
- A closing bracket with no open bracket on the stack, or whose type does
  not match the top of the stack, makes the string invalid **at that
  index** -- return that index immediately.
- If the string never breaks but ends with unclosed opening bracket(s)
  still on the stack, return the index of the first (leftmost) unclosed
  opening bracket.
- Return `-1` only when the stack is empty at the end (everything opened
  was correctly closed, in the correct order).
- Characters other than `()[]{}` are ignored.

Examples:

```python
first_unbalanced_index("()[]{}") == -1
first_unbalanced_index("([)]") == 2   # ')' closes '[' not '('
first_unbalanced_index("(]") == 1     # ']' closes '(' not a '['
first_unbalanced_index("(()") == 0    # index 0 '(' never closes
first_unbalanced_index("()(") == 2    # index 2 '(' never closes
```

The provided stub only compares the *total count* of opens vs. closes, so
it wrongly reports strings like `"([)]"` (2 opens, 2 closes) as balanced
even though the nesting order is wrong.
