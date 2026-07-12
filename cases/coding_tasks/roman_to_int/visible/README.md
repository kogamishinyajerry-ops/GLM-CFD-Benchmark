# Task: roman_to_int

Implement `roman_to_int(s)` in `solution.py`, converting an uppercase Roman
numeral string (symbols `I V X L C D M`) to its integer value.

Semantics (subtractive notation):

- Scan left to right. Each symbol has a value:
  `I=1, V=5, X=10, L=50, C=100, D=500, M=1000`.
- When a symbol is immediately followed by a symbol of **larger** value, it
  is **subtracted** rather than added (e.g. `IV` = 5 − 1 = 4, `XL` = 40).
- Otherwise the symbol's value is added.

Examples:

```python
roman_to_int("III") == 3
roman_to_int("IV") == 4       # subtractive: not 6
roman_to_int("IX") == 9       # subtractive: not 11
roman_to_int("XV") == 15
roman_to_int("XLII") == 42    # XL = 40, then II = 2
```

The provided stub sums each symbol independently, so it ignores subtractive
notation and wrongly reports `"IV"` as 6.
