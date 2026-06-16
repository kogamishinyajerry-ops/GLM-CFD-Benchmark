"""QoI extraction for OpenFOAM probes output and SU2 surface CSV output.

OpenFOAM: reads postProcessing/probes/0/U (probe output for U field)
SU2:      reads surface_flow.csv (Cf column)
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def _is_float(s: str) -> bool:
    """Check if a string can be converted to float.

    Args:
        s: String to test.

    Returns:
        True if float(s) succeeds, False otherwise.
    """
    try:
        float(s)
        return True
    except ValueError:
        return False


def extract_openfoam_centerline_umax(
    probes_dir: Path,
    field_name: str = "U",
) -> float | None:
    """Extract centerline umax from OpenFOAM probes output.

    OpenFOAM probes output format (postProcessing/probes/<time>/<field>)::

        # Probe 0 (0.5000 0.0500 0.0000)
        # x y z  Ux Uy Uz  (header may vary)
        0.005  (0.0123 0.00456 0)    <- each line: time  (Ux Uy Uz)
        0.010  (0.0234 0.00567 0)

    We extract U magnitude = sqrt(Ux^2 + Uy^2) for each probe point / time,
    then return the maximum value (centerline umax at the probe line x=0.5).

    Args:
        probes_dir: Path to postProcessing/probes/ directory.
        field_name: Field name to look for (default 'U').

    Returns:
        Maximum U magnitude (centerline_umax), or None if parsing fails.
    """
    # Find the latest time directory under probes/
    if not probes_dir.exists():
        logger.warning("probes directory not found: %s", probes_dir)
        return None

    # Find the field file (e.g., postProcessing/probes/0/U)
    # The probes output has one file per field, with all time steps concatenated
    field_file = probes_dir / field_name
    if not field_file.exists():
        # Try finding under time directories (older OpenFOAM)
        time_dirs = sorted(
            [d for d in probes_dir.iterdir() if d.is_dir()],
            key=lambda d: float(d.name) if _is_float(d.name) else 0.0,
        )
        if not time_dirs:
            logger.warning("no time dirs or field file found under %s", probes_dir)
            return None
        field_file = time_dirs[-1] / field_name

    try:
        content = field_file.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("failed to read probes file %s: %s", field_file, e)
        return None

    umax = 0.0
    found_any = False

    # Each data line: "time  (Ux Uy Uz)"
    # The vector is in parentheses, space-separated components
    vector_pattern = re.compile(
        r"\(\s*([0-9.eE+\-]+)\s+([0-9.eE+\-]+)\s+([0-9.eE+\-]+)\s*\)"
    )

    for line in content.splitlines():
        match = vector_pattern.search(line)
        if match:
            ux = float(match.group(1))
            uy = float(match.group(2))
            uz = float(match.group(3))
            magnitude = (ux * ux + uy * uy + uz * uz) ** 0.5
            if magnitude > umax:
                umax = magnitude
            found_any = True

    if not found_any:
        logger.warning("no probe vectors parsed from %s", field_file)
        return None

    return umax


def extract_su2_skin_friction_coeff(
    csv_path: Path,
    method: str = "average",
) -> float | None:
    """Extract skin friction coefficient from SU2 surface_flow.csv.

    SU2 surface_flow.csv format::

        "Point_ID","x","y","Cf"
        0,0.001,0.0,0.0028
        1,0.002,0.0,0.0027

    Args:
        csv_path: Path to surface_flow.csv file.
        method: Averaging method — 'average' or 'trailing_edge'.
            'average' = mean of all Cf values on the wall.
            'trailing_edge' = Cf at the last point (x_max).

    Returns:
        Skin friction coefficient value, or None if parsing fails.
    """
    if not csv_path.exists():
        logger.warning("SU2 CSV file not found: %s", csv_path)
        return None

    try:
        content = csv_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("failed to read CSV %s: %s", csv_path, e)
        return None

    reader = csv.reader(content.splitlines())

    # Parse header to find column indices
    header = next(reader, None)
    if header is None:
        logger.warning("empty CSV: %s", csv_path)
        return None

    header_clean = [h.strip().strip('"') for h in header]
    cf_col_idx: int | None = None
    x_col_idx: int | None = None
    for i, h in enumerate(header_clean):
        h_lower = h.lower()
        if h_lower in ("cf", "skin_friction_coefficient", "cf_x"):
            cf_col_idx = i
        if h_lower == "x":
            x_col_idx = i

    if cf_col_idx is None:
        logger.warning("Cf column not found in CSV header: %s", header_clean)
        return None

    cf_values: list[float] = []
    x_values: list[float] = []

    for row in reader:
        if len(row) <= cf_col_idx:
            continue
        try:
            cf = float(row[cf_col_idx])
            cf_values.append(cf)
            if x_col_idx is not None and x_col_idx < len(row):
                x_values.append(float(row[x_col_idx]))
        except (ValueError, IndexError):
            continue

    if not cf_values:
        logger.warning("no Cf values parsed from %s", csv_path)
        return None

    if method == "trailing_edge" and x_values:
        # Return Cf at maximum x (trailing edge)
        max_x_idx = x_values.index(max(x_values))
        return cf_values[max_x_idx]
    else:
        # Default: average Cf
        return sum(cf_values) / len(cf_values)
