# CFD-Benchmark (`cfdb`)

Open-source CFD solver benchmark platform. Standardized cases + solver adapter abstraction layer lets any CFD solution (traditional solver or ML surrogate) be evaluated, compared, and regression-tracked on the same reproducible cases.

## Quick Start

### Installation

```bash
# Clone the repository
git clone <repo-url>
cd GLM-CFD-Benchmark

# Install in development mode (includes dev dependencies)
pip install -e ".[dev]"
```

Requirements: Python 3.11+, Git Bash (Windows) or bash (Linux/macOS).

### Verify Installation

```bash
cfdb --version
# Output: cfdb 0.1.0
```

## Commands

The CLI provides the core pipeline commands (`list-cases`, `validate-case`,
`run`, `report`), data/aggregation commands (`data`, `compare`,
`report-sweep`, `serve`), and the v4 trust-platform command set
(`provenance`, `trust`, `failures`, `baseline`, `gate`, `agent-eval`,
`showcase`). The most-used ones:

### 1. `list-cases` — List all registered cases

```bash
cfdb list-cases
cfdb list-cases --cases-dir /path/to/cases
```

Output shows case ID, category, solvers, and name.

### 2. `validate-case` — Validate a case.yaml file

```bash
cfdb validate-case cases/smoke/mock_success/case.yaml
```

Checks the YAML against the `CaseSpec` schema (Pydantic v2 validation).

### 3. `run` — Execute a case

```bash
cfdb run --case mock_success --solver generic --backend local
cfdb run -c mock_success -s generic -b local --report
```

Options:
- `--case` / `-c`: Case ID (required)
- `--solver` / `-s`: Solver/adapter name (default: `generic`)
- `--backend` / `-b`: Execution backend (default: `local`)
- `--cases-dir`: Cases directory (default: `cases`)
- `--runs-dir`: Run output directory (default: `runs`)
- `--report`: Generate HTML report after run

Exit code: `0` = success, `1` = failed/timeout.

### 4. `report` — Generate HTML report for a completed run

```bash
cfdb report --run-dir runs/20260616T120000Z_mock_success_generic_abcd1234
```

### 5. `provenance` — Audit reference-data provenance (v4)

```bash
cfdb provenance
cfdb provenance --cases-dir /path/to/cases
```

Prints one row per case: reference type, mechanically derived honesty level
(`REAL` / `ANALYTIC` / `MANUFACTURED` / `PREVIOUS_RUN` / `SURROGATE` /
`DECLARED-NOT-VERIFIED`), sha256 verification state of the reference files,
and the citation. Fail-closed: hash drift, missing files, or a claimed
experimental reference without a citation downgrade to
`DECLARED-NOT-VERIFIED`.

### 6. `trust` — Build a TrustProfile for a (case, solver) pair (v4)

```bash
cfdb trust -c naca0012 -s openfoam
cfdb trust -c naca0012 -s openfoam --json
cfdb trust -c naca0012 -s openfoam --svg radar.svg
```

Five-dimension capability profile (accuracy, robustness, efficiency,
completeness, reproducibility) computed from recorded runs, plus a
provenance honesty banner. There is deliberately no aggregate score;
a dimension without enough data reports `n/a` instead of a fabricated 0.

Options: `--json` prints the profile as JSON; `--svg PATH` writes a
five-axis radar SVG; `--cases-dir` / `--runs-dir` as usual.

### 7. `failures` — Failure mode library (v4)

```bash
cfdb failures ingest                              # scan runs/ into the library
cfdb failures list                                # show all failure records
cfdb failures list --mode MESH_FAILURE            # filter by mode
cfdb failures annotate <fingerprint> --guard "..."  # attach a guard note
```

The library (`failures/library.json` by default, override with `--library`)
is append-only: records are deduplicated by fingerprint, recurrences
increment `count`, and history is never deleted. `annotate` attaches a
human-written guard note describing how to prevent the failure next time.

`ingest` exits `1` when any run could not be ingested (missing runs
directory, missing/corrupt `manifest.json`) — partial results are still
persisted and summarized first. Dry runs are reported separately as
"dry-run skipped" and never counted as verified passes (a dry run executed
nothing and verified nothing).

### 8. `baseline` — Baseline governance (v4)

```bash
cfdb baseline list
cfdb baseline promote <run_id> --engineer "Your Name"
```

Promotion is human-signed: `--engineer` is required with no default, and
only runs whose recomputed `overall_status == "pass"` can be promoted.
Each baseline anchors the sha256 of the promoted run's `metrics.json`.
Storage: `baselines/baselines.json` (override with `--baselines`); the
public regression margin lives at the top of that file.

### 9. `gate` — Regression gate against a promoted baseline (v4)

```bash
cfdb gate <run_id>
```

Re-reads everything from disk (no self-reported values) and compares the
candidate run's QoI errors against the re-read baseline. Exit codes:

| Exit | Verdict |
|------|---------|
| 0 | `PASS` |
| 1 | `REGRESSION` or `INVALID_RUN` |
| 2 | `NO_BASELINE` (a missing baseline is never a pass) |
| 3 | `TAMPERED` (baseline artifacts no longer match the anchored hash) — also used when `baselines.json` itself is corrupt/unreadable (fail-closed) |

A corrupt or unreadable candidate run is `INVALID_RUN` (exit 1), never a
crash and never a pass.

### 10. `agent-eval` — Frozen-ruler agent submission scoring (v4)

```bash
cfdb agent-eval init -c <case>                                # freeze the ruler
cfdb agent-eval score -c <case> --submission <dir>            # score a submission
cfdb agent-eval ledger -c <case>                              # show the ledger
```

`init` hashes the case definition, every reference file, and the scoring
weights into `agentbench/<case>/contract.json`. `score` re-hashes all
frozen material first — any drift refuses scoring with **exit 3** and
reports the drifted paths on stderr (changing the ruler is never allowed).
QoI errors are recomputed against the case reference; self-reported error
fields in the submission are ignored. Every score is appended to
`agentbench/<case>/ledger.jsonl`; invalid submissions get `score=None`
and never rank.

`init` refuses to overwrite an existing contract: re-anchoring the ruler
requires `--force` and prints a loud warning with the old and new ruler
ids (scores taken with different rulers are not comparable).

`score` distinguishes void inputs from invalid samples: a missing
submission directory or an unparsable `qoi.json` exits `1` **without
writing to the ledger**, while a readable submission that fails a validity
gate is a legitimate scoring event ledgered with `score=None`.

### 11. `showcase` — Single-file trust-platform showcase HTML (v4)

```bash
cfdb showcase
cfdb showcase --out showcase.html --repo-root .
```

Renders a self-contained HTML page (inline CSS/SVG, no external links)
from the real repository artifacts under `--repo-root`: provenance audit,
trust profiles, failure library wall, regression gate status, and the
agent-eval ledger. Sections without data render an explicit empty state —
sample data is never faked. The regression section only evaluates the gate
against a candidate run distinct from the baseline itself (`status ==
"success"`, not a dry run); with no such candidate it renders an honest
empty state instead of a vacuous run-vs-itself PASS.

## Verification boundary

What the trust platform verifies — and, just as importantly, what it does
not:

- **`agent-eval` scores measure agreement with the frozen reference, NOT
  that a CFD computation actually produced the submission.** Submission
  authenticity is out of scope: an agent could hand-write a `qoi.json`
  that matches the reference. Mitigation roadmap: held-out references and
  artifact spot-re-runs.
- **The frozen-contract and baseline anchors assume the repository write
  boundary is enforced outside `cfdb`.** An agent with repository write
  access could re-anchor the contract (`agent-eval init --force`) or
  re-promote a baseline. For stronger guarantees, record the `ruler_id`
  and baseline `metrics_sha256` out-of-band.
- **`wall_time_sec` is self-reported by the submitter.** It is never
  recomputed, feeds only the `within_budget` gate, is marked
  `self_reported` in every ledger record, and is excluded from the default
  ranking weights.
- **`ledger.jsonl` / `library.json` integrity is process-level
  (append-only discipline), not cryptographic.** There is no hash chain: a
  process that respects the library/ledger APIs cannot rewrite history,
  but direct file edits are outside this boundary.

## Mock Cases (P0)

The project ships with 4 smoke cases for pipeline validation:

| Case | Path | Behavior |
|------|------|----------|
| `mock_success` | `cases/smoke/mock_success/` | Success path, QoI within tolerance |
| `mock_failure` | `cases/smoke/mock_failure/` | Solver exits with code 1 |
| `mock_missing_reference` | `cases/smoke/mock_missing_reference/` | References nonexistent file |
| `mock_missing_qoi` | `cases/smoke/mock_missing_qoi/` | Success exit but no qoi.json |

Run all four:

```bash
cfdb run -c mock_success
cfdb run -c mock_failure
cfdb run -c mock_missing_reference
cfdb run -c mock_missing_qoi
```

Each run creates `runs/<run_id>/` containing `manifest.json`, `metrics.json`, and optionally `report.html`.

## Development

### Code Quality Checks

```bash
# Lint
ruff check .

# Type check (basic mode)
pyright src/cfdb

# Run tests with coverage
pytest --cov=cfdb --cov-report=term-missing

# Check only (no coverage gate)
pytest
```

All four must pass: ruff, pyright basic, pytest, coverage >= 80%.

### Project Structure

```
GLM-CFD-Benchmark/
├── src/cfdb/           # Source code (src layout)
│   ├── schema.py       # Pydantic v2 data models
│   ├── registry.py     # Case registry
│   ├── cli.py          # Typer CLI (core + data + v4 trust-platform commands)
│   ├── core/           # Runner (pipeline orchestrator)
│   ├── adapters/       # SolverAdapter protocol + implementations
│   ├── execution/      # ExecutionBackend protocol + local backend
│   ├── storage/        # ResultRepository protocol + JSON impl
│   ├── metrics/        # Metrics engine (QoI, curves, budget)
│   └── reporting/      # HTML report generator
├── cases/smoke/        # 4 mock cases for P0 validation
├── tests/              # Test suite (10 files)
└── pyproject.toml      # Project config (single file)
```

### Architecture

The system uses a layered architecture with Protocol-based abstractions:

- **CLI** (Typer) → thin layer, calls Runner
- **Runner** (`core/runner.py`) → orchestrates the full pipeline
- **Adapter** (`adapters/`) → solver abstraction (Protocol)
- **Backend** (`execution/`) → execution environment abstraction (Protocol)
- **Repository** (`storage/`) → result storage abstraction (Protocol)
- **Metrics** (`metrics/`) → QoI error, curve L2, budget checking
- **Reporting** (`reporting/`) → Jinja2 HTML report generation

Adding a new solver or backend requires only implementing the Protocol and registering it — business code remains unchanged.

## License

MIT License. See [LICENSE](LICENSE).
