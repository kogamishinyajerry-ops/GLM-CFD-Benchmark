"""Tests for cfdb.registry.CaseRegistry."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from cfdb.cli import app
from cfdb.registry import CaseRegistry


class TestCaseRegistry:
    def test_load_existing_case(self, tmp_cases_root: Path) -> None:
        registry = CaseRegistry(tmp_cases_root)
        case = registry.load("test_case")
        assert case.id == "test_case"
        assert case.category == "smoke"

    def test_load_nonexistent_case(self, tmp_cases_root: Path) -> None:
        registry = CaseRegistry(tmp_cases_root)
        with pytest.raises(KeyError, match="not found"):
            registry.load("nonexistent")

    def test_list_all(self, tmp_cases_root: Path) -> None:
        registry = CaseRegistry(tmp_cases_root)
        cases = registry.list_all()
        assert len(cases) == 1
        assert cases[0].id == "test_case"

    def test_list_all_sorted_by_id(self, tmp_path: Path) -> None:
        cases_root = tmp_path / "cases"
        for name in ["zebra", "alpha", "mango"]:
            d = cases_root / "smoke" / name
            d.mkdir(parents=True)
            data = {
                "id": name,
                "name": name.title(),
                "category": "smoke",
                "physics": {"flow": "incompressible"},
                "conditions": {"reynolds": 100.0},
                "solvers": [{"name": "generic", "command": "true"}],
                "outputs": {"qoi": []},
                "metrics": {},
            }
            (d / "case.yaml").write_text(yaml.dump(data), encoding="utf-8")

        registry = CaseRegistry(cases_root)
        cases = registry.list_all()
        ids = [c.id for c in cases]
        assert ids == ["alpha", "mango", "zebra"]

    def test_get_case_dir(self, tmp_cases_root: Path) -> None:
        registry = CaseRegistry(tmp_cases_root)
        case_dir = registry.get_case_dir("test_case")
        assert case_dir.exists()
        assert (case_dir / "case.yaml").exists()

    def test_get_case_dir_nonexistent(self, tmp_cases_root: Path) -> None:
        registry = CaseRegistry(tmp_cases_root)
        with pytest.raises(KeyError):
            registry.get_case_dir("nonexistent")

    def test_validate_valid_yaml(self, tmp_cases_root: Path) -> None:
        registry = CaseRegistry(tmp_cases_root)
        yaml_path = tmp_cases_root / "smoke" / "test_case" / "case.yaml"
        spec = registry.validate(yaml_path)
        assert spec.id == "test_case"

    def test_validate_invalid_yaml(self, tmp_path: Path) -> None:
        bad_dir = tmp_path / "smoke" / "bad_case"
        bad_dir.mkdir(parents=True)
        bad_yaml = bad_dir / "case.yaml"
        bad_yaml.write_text("id: BAD\nname: Bad\n", encoding="utf-8")

        registry = CaseRegistry(tmp_path / "smoke" / "bad_case")
        with pytest.raises(ValidationError):
            registry.validate(bad_yaml)

    def test_caching(self, tmp_cases_root: Path) -> None:
        registry = CaseRegistry(tmp_cases_root)
        _ = registry.load("test_case")
        assert registry._scanned is True
        _ = registry.load("test_case")
        assert "test_case" in registry._cache

    def test_nonexistent_root(self, tmp_path: Path) -> None:
        registry = CaseRegistry(tmp_path / "nonexistent")
        cases = registry.list_all()
        assert cases == []

    def test_clear_cache(self, tmp_cases_root: Path) -> None:
        registry = CaseRegistry(tmp_cases_root)
        _ = registry.load("test_case")
        assert registry._scanned is True
        registry.clear_cache()
        assert registry._scanned is False
        assert registry._cache == {}


class TestCaseRegistrySkipped:
    """A2: registry fail-open scanning must never hide invalid cases (visibility gate)."""

    @staticmethod
    def _write_case(cases_root: Path, case_id: str, data: dict) -> None:
        case_dir = cases_root / "smoke" / case_id
        case_dir.mkdir(parents=True)
        payload = dict(data)
        payload["id"] = case_id
        (case_dir / "case.yaml").write_text(yaml.dump(payload), encoding="utf-8")

    def test_baseline_no_skipped_when_all_valid(
        self, tmp_path: Path, sample_case_spec_data: dict
    ) -> None:
        """Untampered baseline: a registry of only valid cases reports skipped == []."""
        cases_root = tmp_path / "cases"
        self._write_case(cases_root, "good_case", sample_case_spec_data)

        registry = CaseRegistry(cases_root)
        cases = registry.list_all()

        assert [c.id for c in cases] == ["good_case"]
        assert registry.skipped == []

    def test_tampered_yaml_case_is_recorded_and_scan_continues(
        self, tmp_path: Path, sample_case_spec_data: dict
    ) -> None:
        """Single-point tamper: inject a malformed case.yaml (unparsable YAML).

        Fail-open must still list the good case; the bad one must never be
        silently invisible — it must show up in ``registry.skipped``.
        """
        cases_root = tmp_path / "cases"
        self._write_case(cases_root, "good_case", sample_case_spec_data)

        bad_dir = cases_root / "smoke" / "bad_case"
        bad_dir.mkdir(parents=True)
        (bad_dir / "case.yaml").write_text("id: [unterminated\n", encoding="utf-8")

        registry = CaseRegistry(cases_root)
        cases = registry.list_all()

        # fail-open: scan continues, good case unaffected
        assert [c.id for c in cases] == ["good_case"]

        # visibility: the bad case is never silently dropped
        skipped = registry.skipped
        assert len(skipped) == 1
        rel_path, reason = skipped[0]
        assert rel_path == str(Path("smoke") / "bad_case" / "case.yaml")
        assert reason  # non-empty summary
        assert "\n" not in reason  # collapsed to a one-line summary

    def test_tampered_schema_invalid_case_is_recorded(
        self, tmp_path: Path, sample_case_spec_data: dict
    ) -> None:
        """Single-point tamper: syntactically valid YAML that fails CaseSpec schema."""
        cases_root = tmp_path / "cases"
        self._write_case(cases_root, "good_case", sample_case_spec_data)

        bad_dir = cases_root / "smoke" / "bad_case"
        bad_dir.mkdir(parents=True)
        bad_dir_yaml = bad_dir / "case.yaml"
        bad_dir_yaml.write_text("id: BAD\nname: Bad\ncategory: smoke\n", encoding="utf-8")

        registry = CaseRegistry(cases_root)
        cases = registry.list_all()

        assert [c.id for c in cases] == ["good_case"]
        skipped = registry.skipped
        assert len(skipped) == 1
        rel_path, reason = skipped[0]
        assert "bad_case" in rel_path
        assert reason

    def test_clear_cache_rescan_does_not_duplicate_skipped(
        self, tmp_path: Path, sample_case_spec_data: dict
    ) -> None:
        cases_root = tmp_path / "cases"
        self._write_case(cases_root, "good_case", sample_case_spec_data)
        bad_dir = cases_root / "smoke" / "bad_case"
        bad_dir.mkdir(parents=True)
        (bad_dir / "case.yaml").write_text("id: [unterminated\n", encoding="utf-8")

        registry = CaseRegistry(cases_root)
        registry.list_all()
        assert len(registry.skipped) == 1

        registry.clear_cache()
        registry.list_all()
        assert len(registry.skipped) == 1  # re-scanned, not accumulated to 2


class TestListCasesSkippedVisibility:
    """A2: `cfdb list-cases` must surface skipped cases on stderr, exit code 0."""

    def test_untampered_baseline_lists_cleanly(
        self, tmp_path: Path, sample_case_spec_data: dict
    ) -> None:
        cases_root = tmp_path / "cases"
        TestCaseRegistrySkipped._write_case(cases_root, "good_case", sample_case_spec_data)

        runner = CliRunner()
        result = runner.invoke(app, ["list-cases", "--cases-dir", str(cases_root)])

        assert result.exit_code == 0
        assert "good_case" in result.stdout
        assert "skipped" not in result.stderr

    def test_tampered_case_surfaced_on_stderr_exit_code_still_zero(
        self, tmp_path: Path, sample_case_spec_data: dict
    ) -> None:
        cases_root = tmp_path / "cases"
        TestCaseRegistrySkipped._write_case(cases_root, "good_case", sample_case_spec_data)
        bad_dir = cases_root / "smoke" / "bad_case"
        bad_dir.mkdir(parents=True)
        (bad_dir / "case.yaml").write_text("id: [unterminated\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(app, ["list-cases", "--cases-dir", str(cases_root)])

        # exit code contract: visibility, not a hard failure
        assert result.exit_code == 0
        # good case still listed normally on stdout
        assert "good_case" in result.stdout
        # bad case is never invisible: must appear on stderr
        assert "1 case(s) skipped (invalid):" in result.stderr
        assert "bad_case" in result.stderr

    def test_all_cases_invalid_still_reports_skipped(
        self, tmp_path: Path
    ) -> None:
        """Edge case: zero valid cases but one invalid — skip visibility must not
        depend on there being at least one good case to print alongside it."""
        cases_root = tmp_path / "cases"
        bad_dir = cases_root / "smoke" / "bad_case"
        bad_dir.mkdir(parents=True)
        (bad_dir / "case.yaml").write_text("id: [unterminated\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(app, ["list-cases", "--cases-dir", str(cases_root)])

        assert result.exit_code == 0
        assert "No cases found." in result.stdout
        assert "1 case(s) skipped (invalid):" in result.stderr
        assert "bad_case" in result.stderr
