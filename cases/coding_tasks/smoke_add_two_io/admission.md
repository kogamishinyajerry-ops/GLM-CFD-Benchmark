# Admission evidence -- smoke_add_two_io

Per Architecture-v5.0-multi-domain.md §3.1/§3.4: the golden solution must run
deterministically (3/3 identical valid results) against the frozen judging
material before a coding-domain case may be admitted.

**This record is a REAL sandboxed admission** (unlike the earlier local-only
manual records): it was produced by the automated `cfdb agent-eval admit`
workflow (R8 batch), which runs the golden solution N times through the Docker
sandbox profile (§3.2) against the immutable judge image, and — for this R9
pilot — through **both** judging signals: the hidden-test suite AND the
trusted re-execution IO oracle (§9). The machine-authored `admission.json`
beside this file is the authoritative evidence; this file is its human-readable
disclosure.

## Command

```
cfdb agent-eval admit -c smoke_add_two_io --runs 3
```

## Result

- **3/3 golden runs valid**, score 1.0 each (both `tests_all_pass` and
  `io_oracle_pass` green on every run).
- Golden content id: `e6677e4b657322575f28f6412901a7a2de091b5d5a66e85c8098056485e5d960`
- Judge image: `sha256:09e22eb6002adb0f66610dff697cbe5387f44f1b323d66af567705fbc555df09`
- `all_passed: true` (see `admission.json` for per-run timestamps and notes).

The admission record lives outside the frozen tree (case root, not
`reference/`/`visible/`), so writing it never drifts the ruler.
