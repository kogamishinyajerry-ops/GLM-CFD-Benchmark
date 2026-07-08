"""P4-D regression module: baseline governance + fail-closed regression gate.

Public API:
    BaselineEntry / BaselineFile / RegressionMargin / BaselineStore — baseline
    storage and human-signed promotion (``baselines/baselines.json``).
    GateVerdict / evaluate — recompute-everything regression gate.
"""

from cfdb.regression.baseline import (
    BaselineEntry,
    BaselineFile,
    BaselineStore,
    RegressionMargin,
    baseline_key,
    sha256_of_file,
)
from cfdb.regression.gate import GateVerdict, evaluate

__all__ = [
    "BaselineEntry",
    "BaselineFile",
    "BaselineStore",
    "GateVerdict",
    "RegressionMargin",
    "baseline_key",
    "evaluate",
    "sha256_of_file",
]
