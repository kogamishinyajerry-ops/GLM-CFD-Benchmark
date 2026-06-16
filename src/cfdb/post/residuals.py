"""Residual log parsing for OpenFOAM and SU2 solvers.

Uses regex to extract residual histories from solver log output.
No third-party dependencies — pure Python re module.
"""

from __future__ import annotations

import re

# === OpenFOAM residual pattern ===
# OpenFOAM log line format:
#   "Solving for Ux, Initial residual = 0.001234, Final residual = 0.000123, ..."
#   "Solving for p, Initial residual = ..."
# Works with v2312 and v2406 (format stable since v2006)
_OPENFOAM_RESIDUAL_PATTERN = re.compile(
    r"Solving for (\w+),\s*Initial residual\s*=\s*([0-9.eE+\-]+)"
)

# === SU2 residual patterns ===
# SU2 convergence history output (TABULAR_FORMAT= CSV):
#   "iter","RMS_DENSITY","RMS_MOMENTUM-X",...
#   "0","-2.5","-3.1",...
# Or older keyword style:
#   RMS_DENSITY: -2.5

# Alternative SU2 residual format (keyword style)
_SU2_KEYWORD_PATTERN = re.compile(
    r"(RMS_[A-Z_]+|rms_[a-z_]+)\s*[:=]\s*(-?\d+\.?\d*(?:[eE][+\-]?\d+)?)"
)


def parse_openfoam_residuals(log_text: str) -> dict[str, list[float]]:
    """Extract all Initial residual values from an OpenFOAM log.

    OpenFOAM prints lines like:
        Solving for Ux, Initial residual = 0.001234, ...
        Solving for Uy, Initial residual = 0.002345, ...
        Solving for p, Initial residual = 0.000567, ...

    Args:
        log_text: Raw OpenFOAM log output text.

    Returns:
        Dict mapping field name to list of residual values over iterations.
        Example: ``{'Ux': [0.1, 0.05, ..., 1.2e-6], 'Uy': [...], 'p': [...]}``
        Empty dict if no residuals found.
    """
    residuals: dict[str, list[float]] = {}
    for match in _OPENFOAM_RESIDUAL_PATTERN.finditer(log_text):
        field_name = match.group(1)  # e.g. "Ux", "Uy", "p"
        value_str = match.group(2)  # e.g. "1.234e-4"
        try:
            value = float(value_str)
        except ValueError:
            continue
        residuals.setdefault(field_name, []).append(value)
    return residuals


def parse_su2_residuals(log_text: str) -> dict[str, list[float]]:
    """Extract residual values from SU2 convergence output.

    Handles two formats:

    1. CSV format (TABULAR_FORMAT= CSV)::

         "iter","RMS_DENSITY","RMS_MOMENTUM-X",...
         "0","-2.5","-3.1",...

    2. Keyword format (older SU2 or redirected)::

         RMS_DENSITY: -2.5

    Args:
        log_text: Raw SU2 log output text.

    Returns:
        Dict mapping residual name to list of values over iterations.
        Example: ``{'RMS_DENSITY': [-2.5, -2.8, ...], ...}``
    """
    residuals: dict[str, list[float]] = {}

    # Try CSV format first
    lines = log_text.strip().splitlines()
    header_idx = None
    column_names: list[str] = []

    for i, line in enumerate(lines):
        if line.strip().lower().startswith('"iter"'):
            # Parse CSV header
            header_idx = i
            parts = re.findall(r'"([^"]*)"', line)
            column_names = parts[1:]  # skip "iter"
            break

    if header_idx is not None and column_names:
        # Parse CSV data rows
        for line in lines[header_idx + 1 :]:
            line = line.strip()
            if not line or line.startswith("%") or line.startswith("#"):
                continue
            parts = re.findall(r'"?([^",]+?)"?(?:,|$)', line)
            if len(parts) < 2:
                continue
            for col_idx, col_name in enumerate(column_names):
                if col_idx + 1 >= len(parts):
                    break
                val_str = parts[col_idx + 1].strip().strip('"')
                try:
                    val = float(val_str)
                    residuals.setdefault(col_name, []).append(val)
                except ValueError:
                    continue
        if residuals:
            return residuals

    # Fallback: keyword format
    for match in _SU2_KEYWORD_PATTERN.finditer(log_text):
        field_name = match.group(1)
        value_str = match.group(2)
        try:
            value = float(value_str)
        except ValueError:
            continue
        residuals.setdefault(field_name, []).append(value)

    return residuals


def extract_final(residuals: dict[str, list[float]]) -> dict[str, float]:
    """Take the last value of each residual list.

    Args:
        residuals: Output from parse_openfoam_residuals or parse_su2_residuals.

    Returns:
        Dict mapping field name to final (last) residual value.
        Example: ``{'Ux': 1.2e-6, 'Uy': 2.1e-6, 'p': 3.4e-5}``
    """
    return {
        field: values[-1]
        for field, values in residuals.items()
        if values  # non-empty list
    }


# === Version extraction ===

_OPENFOAM_VERSION_PATTERN = re.compile(
    r"Build:\s+(\d+(?:\.\d+)*)|Version:\s+(v?\d+(?:\.\d+)*)"
)


def extract_openfoam_version(log_text: str) -> str | None:
    """Extract OpenFOAM version from the banner at the top of the log.

    Tries 'Version: vX.Y' first (OpenCFD banner), then 'Build: X.Y.Z'.

    Args:
        log_text: Raw OpenFOAM log output text.

    Returns:
        Version string like ``'OpenFOAM v2406'`` or ``None`` if not found.
    """
    for line in log_text.splitlines()[:10]:
        match = _OPENFOAM_VERSION_PATTERN.search(line)
        if match:
            version = match.group(2) or match.group(1)
            return f"OpenFOAM {version}"
    return None


_SU2_VERSION_PATTERN = re.compile(
    r"SU2\s+Code\s+Suite,\s*Version\s+(\d+(?:\.\d+)*)"
)


def extract_su2_version(log_text: str) -> str | None:
    """Extract SU2 version from stdout.

    Args:
        log_text: Raw SU2 log output text.

    Returns:
        Version string like ``'SU2 8.0.0'`` or ``None`` if not found.
    """
    for line in log_text.splitlines()[:15]:
        match = _SU2_VERSION_PATTERN.search(line)
        if match:
            return f"SU2 {match.group(1)}"
    return None
