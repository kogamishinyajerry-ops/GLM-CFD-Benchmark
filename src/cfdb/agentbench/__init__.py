"""Agentbench (P4-E): frozen scoring contract + agent submission scoring.

Public API for the CLI layer:

- :func:`init_contract` / :func:`save_contract` / :func:`load_contract`
- :func:`score_submission` (raises :class:`FrozenDriftError` on ruler drift;
  map it to exit code :data:`EXIT_FROZEN_DRIFT`)
- :func:`read_ledger` / :func:`ranked` for the ledger table
"""

from cfdb.agentbench.contract import (
    DEFAULT_VALIDITY_GATES,
    DEFAULT_WEIGHTS,
    EXIT_FROZEN_DRIFT,
    FrozenDriftError,
    ScoringContract,
    init_contract,
    load_contract,
    save_contract,
    verify_frozen,
)
from cfdb.agentbench.scorer import (
    SubmissionScore,
    append_ledger,
    ranked,
    read_ledger,
    score_submission,
)

__all__ = [
    "DEFAULT_VALIDITY_GATES",
    "DEFAULT_WEIGHTS",
    "EXIT_FROZEN_DRIFT",
    "FrozenDriftError",
    "ScoringContract",
    "SubmissionScore",
    "append_ledger",
    "init_contract",
    "load_contract",
    "ranked",
    "read_ledger",
    "save_contract",
    "score_submission",
    "verify_frozen",
]
