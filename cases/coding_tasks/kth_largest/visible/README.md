# Task: kth_largest

Implement `kth_largest(nums, k)` in `solution.py`. Return the **k-th largest**
element of the integer list `nums`, where `k = 1` is the maximum, `k = 2` the
second largest, and so on. Duplicates count by position: in `[7, 7, 2]` the
1st and 2nd largest are both `7`.

You may assume `1 <= k <= len(nums)`.

Examples:

```python
kth_largest([3, 1, 4, 1, 5, 9, 2, 6], 2) == 6   # sorted desc: 9, 6, 5, ...
kth_largest([10, 20, 30], 1)              == 30
kth_largest([7, 2, 7, 2], 2)              == 7   # 7, 7, 2, 2 -> 2nd is 7
kth_largest([42], 1)                      == 42
```

The provided stub sorts **ascending** and indexes `k-1`, so it returns the
k-th *smallest* — wrong except on degenerate inputs (a single element or
all-equal values).
