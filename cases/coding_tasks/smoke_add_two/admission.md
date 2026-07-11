# Admission evidence -- smoke_add_two

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
| 1   | `2 passed in 0.01s` |
| 2   | `2 passed in 0.01s` |
| 3   | `2 passed in 0.01s` |

`execution.expected_test_count: 2` matches the collected test count in
every run.

## Stub sanity check (visible/solution.py, not part of admission gate)

Running the same hidden tests against the unmodified `visible/solution.py`
stub confirms the FAIL_TO_PASS/PASS_TO_PASS split is real, not merely
asserted:

```
FAILED reference/hidden_tests/test_fail_to_pass.py::test_add_two_positive
1 failed, 1 passed in 0.02s
```

- FAIL_TO_PASS (`test_fail_to_pass.py::test_add_two_positive`): fails on
  the stub (`NotImplementedError`), as required.
- PASS_TO_PASS (`test_pass_to_pass.py::test_already_works_identity`):
  already passes on the stub, as required.
