# Task: merge_intervals

Implement `merge_intervals(intervals)` in `solution.py`. Given a list of
`[start, end]` integer intervals (in any order), merge every set of
overlapping or touching intervals and return the merged list **sorted by
start**.

Semantics:

- Two intervals overlap (or touch) when the later one's start is `<=` the
  running interval's end. `[1, 4]` and `[4, 5]` touch and merge to `[1, 5]`.
- The input is **not** guaranteed to be sorted. `[[2, 6], [1, 3]]` must merge
  to `[[1, 6]]`, not `[[2, 6]]`.
- Each returned interval is a two-element `[start, end]` list.

Examples:

```python
merge_intervals([[1, 3], [2, 6], [8, 10]]) == [[1, 6], [8, 10]]
merge_intervals([[2, 6], [1, 3]])          == [[1, 6]]      # unsorted input
merge_intervals([[1, 4], [2, 5]])          == [[1, 5]]
merge_intervals([[1, 3], [5, 7]])          == [[1, 3], [5, 7]]
merge_intervals([])                        == []
```

The provided stub sweeps once **without sorting first**, so it returns wrong
intervals whenever the input is not already ordered by start.
