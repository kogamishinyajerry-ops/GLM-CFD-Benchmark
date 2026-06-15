"""Case Registry: scan, load, validate, and cache CaseSpec objects."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from cfdb.schema import CaseSpec

logger = logging.getLogger(__name__)


class CaseRegistry:
    """Case registry: scan, load, validate, and cache CaseSpec objects.

    Scans cases/<category>/<case_id>/case.yaml structure.
    Caches loaded CaseSpecs to avoid repeated I/O.
    """

    def __init__(self, cases_root: Path) -> None:
        """Initialize the registry.

        Args:
            cases_root: Root directory containing case categories.
        """
        self._root: Path = cases_root
        self._cache: dict[str, CaseSpec] = {}
        self._case_dirs: dict[str, Path] = {}
        self._scanned: bool = False

    def _scan(self) -> None:
        """Scan cases/<category>/*/case.yaml and load all valid CaseSpecs."""
        if self._scanned:
            return

        if not self._root.exists():
            logger.warning("cases_root does not exist: %s", self._root)
            self._scanned = True
            return

        for category_dir in sorted(self._root.iterdir()):
            if not category_dir.is_dir():
                continue
            for case_dir in sorted(category_dir.iterdir()):
                if not case_dir.is_dir():
                    continue
                yaml_path = case_dir / "case.yaml"
                if not yaml_path.exists():
                    continue
                try:
                    with yaml_path.open(encoding="utf-8") as f:
                        raw = yaml.safe_load(f)
                    spec = CaseSpec.model_validate(raw)
                    self._cache[spec.id] = spec
                    self._case_dirs[spec.id] = case_dir
                    logger.debug("loaded case '%s' from %s", spec.id, yaml_path)
                except (ValidationError, yaml.YAMLError) as e:
                    logger.error("failed to load case from %s: %s", yaml_path, e)

        self._scanned = True

    def _ensure_scanned(self) -> None:
        """Ensure the registry has been scanned."""
        if not self._scanned:
            self._scan()

    def load(self, case_id: str) -> CaseSpec:
        """Load a single CaseSpec by ID.

        Args:
            case_id: The case identifier.

        Returns:
            The CaseSpec for the given id.

        Raises:
            KeyError: If case_id is not found.
        """
        self._ensure_scanned()
        if case_id not in self._cache:
            available = sorted(self._cache.keys())
            raise KeyError(f"case '{case_id}' not found. Available: {available}")
        return self._cache[case_id]

    def get_case_dir(self, case_id: str) -> Path:
        """Get the directory path of a case by ID.

        Args:
            case_id: The case identifier.

        Returns:
            Path to the case directory (containing case.yaml).

        Raises:
            KeyError: If case_id is not found.
        """
        self._ensure_scanned()
        if case_id not in self._case_dirs:
            available = sorted(self._case_dirs.keys())
            raise KeyError(f"case '{case_id}' not found. Available: {available}")
        return self._case_dirs[case_id]

    def list_all(self) -> list[CaseSpec]:
        """Return all registered CaseSpecs, sorted by id.

        Returns:
            List of CaseSpec objects sorted by id.
        """
        self._ensure_scanned()
        return sorted(self._cache.values(), key=lambda c: c.id)

    def validate(self, yaml_path: Path) -> CaseSpec:
        """Validate a single case.yaml file (not cached).

        Args:
            yaml_path: Path to the case.yaml file.

        Returns:
            Validated CaseSpec.

        Raises:
            ValidationError: If validation fails.
            yaml.YAMLError: If YAML parsing fails.
            FileNotFoundError: If file does not exist.
        """
        with yaml_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return CaseSpec.model_validate(raw)

    def clear_cache(self) -> None:
        """Clear the cache, forcing a re-scan on next access."""
        self._cache.clear()
        self._case_dirs.clear()
        self._scanned = False
