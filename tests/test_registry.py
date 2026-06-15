"""Tests for cfdb.registry.CaseRegistry."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

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
