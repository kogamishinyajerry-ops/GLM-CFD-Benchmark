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

The CLI provides 4 commands:

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
│   ├── cli.py          # Typer CLI (4 commands)
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
