# Admission evidence -- csv_field_splitter

Per Architecture-v5.0-multi-domain.md §3.1/§3.4: the golden solution must run
deterministically (3/3 identical valid results) against the frozen judging
material before a coding-domain case may be admitted.

**This record is a REAL sandboxed admission**, produced by the automated
`cfdb agent-eval admit` workflow: it runs the golden solution N times through
the Docker sandbox profile (§3.2) against the immutable judge image, and — as
an R9-rollout io-oracle case — through **both** judging signals: the hidden-test
suite AND the trusted re-execution IO oracle (`split_csv_line` over a held-out input
set disjoint from the hidden tests, §9). The machine-authored `admission.json`
beside this file is the authoritative evidence; this file is its disclosure.

## Command

```
cfdb agent-eval admit -c csv_field_splitter --runs 3
```

## Result

- **3/3 golden runs valid**, score 1.0 each (both `tests_all_pass` and
  `io_oracle_pass` green on every run).
- Golden content id: `7e28b57aae0050e60ba7a6ca2b9772f17b8267472ebac9dc6a6cf8f8b47bd95c`
- Judge image: `sha256:09e22eb6002adb0f66610dff697cbe5387f44f1b323d66af567705fbc555df09`
- `all_passed: true` (see `admission.json` for per-run timestamps and notes).

The admission record lives outside the frozen tree (case root, not
`reference/`/`visible/`), so writing it never drifts the ruler.
