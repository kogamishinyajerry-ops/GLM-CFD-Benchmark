# Task: split_csv_line

Fix `split_csv_line(line)` in `solution.py` so it correctly handles
RFC4180-style quoted fields: a comma or a doubled quote (`""`) inside a
double-quoted field must not split the field or end the quote.

Examples the correct implementation must satisfy:

```python
split_csv_line('a,b,c') == ['a', 'b', 'c']
split_csv_line('"a,b",c') == ['a,b', 'c']
split_csv_line('"say ""hi""",ok') == ['say "hi"', 'ok']
```

The provided stub splits naively on every comma, which is correct for
plain unquoted fields but breaks as soon as a field is quoted.

Only a single line (no embedded newlines) needs to be handled.
