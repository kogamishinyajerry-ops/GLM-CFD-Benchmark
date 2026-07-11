"""v5.0 integration-seam regression: domain-aware contract defaults.

Pinned by the main-controller merge pass after the real-container E2E run
caught `agent-eval init` freezing CFD-flavoured weights/gates for a coding
case (the contract then fail-closed every coding submission at the unknown
qoi_complete gate). These tests keep init_contract's per-domain defaults
aligned with what each domain scorer actually emits.
"""

from __future__ import annotations

from pathlib import Path

from cfdb.agentbench.contract import (
    DOMAIN_DEFAULT_VALIDITY_GATES,
    DOMAIN_DEFAULT_WEIGHTS,
    init_contract,
)
from cfdb.registry import CaseRegistry

PROJECT_CASES = Path(__file__).resolve().parent.parent / "cases"


class TestDomainDefaultContracts:
    def _registry(self) -> CaseRegistry:
        return CaseRegistry(PROJECT_CASES)

    def test_coding_case_gets_coding_defaults(self) -> None:
        contract = init_contract("smoke_add_two", self._registry())
        assert contract.weights == {"pass_rate": 1.0}
        assert contract.validity_gates == ["tests_all_pass", "sandbox_used"]

    def test_agentic_case_gets_agentic_defaults(self) -> None:
        contract = init_contract("csv_field_extract", self._registry())
        assert contract.weights == {"checker_success": 1.0}
        assert contract.validity_gates == ["checker_ok"]

    def test_cfd_case_defaults_unchanged(self) -> None:
        # Historic v4 behavior must stay byte-identical for cfd cases.
        contract = init_contract("mock_success", self._registry())
        assert contract.weights == {"qoi_error": -1.0}
        assert contract.validity_gates == ["qoi_complete", "within_budget"]

    def test_explicit_args_still_override_domain_defaults(self) -> None:
        contract = init_contract(
            "smoke_add_two",
            self._registry(),
            weights={"pass_rate": 2.0},
            validity_gates=["tests_all_pass"],
        )
        assert contract.weights == {"pass_rate": 2.0}
        assert contract.validity_gates == ["tests_all_pass"]

    def test_domain_tables_cover_every_domain_literal(self) -> None:
        # A new domain literal without a defaults entry would silently fall
        # back to cfd defaults — keep the tables total over the enum.
        domains = {"cfd", "coding", "agentic"}
        assert set(DOMAIN_DEFAULT_WEIGHTS) == domains
        assert set(DOMAIN_DEFAULT_VALIDITY_GATES) == domains
