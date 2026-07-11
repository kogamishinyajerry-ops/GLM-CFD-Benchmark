# Admission evidence -- balanced_brackets

Per Architecture-v5.0-multi-domain.md §3.4: the golden solution must run
deterministically (3/3 identical results) against `reference/hidden_tests/`
before a coding-domain case may be admitted.

**Honest disclosure**: this is a **local, non-sandboxed** pre-run on the
developer machine (`.venv/bin/python -m pytest`), not a run through the
Docker sandbox profile (§3.2). Sandboxed admission verification is not yet
automated -- see Architecture §7 backlog item "golden 准入 3 次复跑的系统化
留痕". This file is the manual admission record the architecture explicitly
allows for v5.0 ("case 作者本地跑+摘要入仓").

## Command

```bash
PYTHONPATH=<scratch-submission-dir-with-golden/solution.py-copied-in> \
  .venv/bin/python -m pytest reference/hidden_tests \
  --no-cov -p no:cacheprovider -q --rootdir=reference/hidden_tests
```

## Result (run 2026-07-11, 3/3 consistent)

| Run | Result |
|-----|--------|
| 1   | `5 passed in 0.01s` |
| 2   | `5 passed in 0.01s` |
| 3   | `5 passed in 0.01s` |

`execution.expected_test_count: 5` matches the collected test count in
every run (3 FAIL_TO_PASS + 2 PASS_TO_PASS).

## Stub sanity check (visible/solution.py, not part of admission gate)

Running the same hidden tests against the unmodified `visible/solution.py`
count-only stub confirms the FAIL_TO_PASS/PASS_TO_PASS split is real:

```
FAILED reference/hidden_tests/test_fail_to_pass.py::test_wrong_nesting_type
FAILED reference/hidden_tests/test_fail_to_pass.py::test_wrong_close_type
FAILED reference/hidden_tests/test_fail_to_pass.py::test_unclosed_open_at_end
3 failed, 2 passed in 0.02s
```

- All 3 FAIL_TO_PASS tests fail on the count-only stub, as required.
- Both PASS_TO_PASS tests (fully-balanced / empty string, where the
  count-only heuristic coincidentally agrees with the correct answer)
  already pass on the stub, as required.
