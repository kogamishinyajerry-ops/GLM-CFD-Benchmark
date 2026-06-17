"""Mesh cell count extraction from solver logs.

Extracts total cell/element counts from OpenFOAM blockMesh and SU2 startup output.
No third-party dependencies — pure Python re module.
"""

from __future__ import annotations

import re

_OPENFOAM_CELL_COUNT_PATTERN = re.compile(r"nCells\s*:\s*(\d+)", re.IGNORECASE)

_SU2_CELL_COUNT_PATTERN = re.compile(
    r"(\d[\d,]*)\s+volume\s+elements", re.IGNORECASE
)


def extract_openfoam_cell_count(log_text: str) -> int | None:
    """Extract total cell count from OpenFOAM blockMesh log output.

    OpenFOAM blockMesh prints lines like:
        nCells: 400

    Args:
        log_text: Raw blockMesh log output text (from step stdout).

    Returns:
        Cell count as integer, or None if not found.
    """
    match = _OPENFOAM_CELL_COUNT_PATTERN.search(log_text)
    if match:
        return int(match.group(1))
    return None


def extract_su2_cell_count(log_text: str) -> int | None:
    """Extract total cell count from SU2 startup output.

    SU2 prints mesh statistics like:
        33,024 volume elements.

    Args:
        log_text: Raw SU2 log output text.

    Returns:
        Cell count as integer (commas removed), or None if not found.
    """
    match = _SU2_CELL_COUNT_PATTERN.search(log_text)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


# === StarCCM cell count pattern ===
# StarCCM prints when importing mesh:
#   N cells: 123456
#   Total cells: 123456
_STARCCM_CELL_COUNT_PATTERN = re.compile(
    r"(?:N\s+cells|Total\s+cells?|Cells?)\s*:\s*(\d[\d,]*)",
    re.IGNORECASE,
)


def extract_starccm_cell_count(log_text: str) -> int | None:
    """Extract total cell count from Star-CCM+ mesh import log output.

    StarCCM prints mesh statistics during import:
        N cells: 123456
    or:
        Total cells: 500000

    Args:
        log_text: Raw StarCCM mesh import log output text.

    Returns:
        Cell count as integer (commas removed), or None if not found.
    """
    match = _STARCCM_CELL_COUNT_PATTERN.search(log_text)
    if match:
        return int(match.group(1).replace(",", ""))
    return None
