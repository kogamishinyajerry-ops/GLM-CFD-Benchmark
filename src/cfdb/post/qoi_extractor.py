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
        # Skip comments / headers (e.g. '# Probe 0 (0.5 0.05 0)') — the
        # parenthesised probe position would otherwise be parsed as a data
        # vector. Same guard as extract_cl_cd_openfoam.
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
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


# === P2-b: NACA0012 Cp distribution extractors ===

def extract_naca0012_cp_su2(
    surface_flow_csv: Path,
) -> tuple[list[float], list[float]] | None:
    """Extract Cp distribution from SU2 surface_flow.csv along NACA0012 airfoil.

    SU2 surface_flow.csv format for wall markers::

        "Point_ID","x","y","Pressure","Pressure_Coefficient"
        0,0.0001,0.0001,101325.0,0.85
        ...

    The airfoil is parameterized by x/c (chord-wise coordinate). We extract
    (x, Cp) pairs and return them for comparison with Ladson 1988 reference.

    Args:
        surface_flow_csv: Path to SU2 surface_flow.csv.

    Returns:
        Tuple (x_over_c_list, cp_list), or None if parsing fails.
    """
    if not surface_flow_csv.exists():
        logger.warning("SU2 CSV not found: %s", surface_flow_csv)
        return None

    try:
        content = surface_flow_csv.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("failed to read CSV %s: %s", surface_flow_csv, e)
        return None

    reader = csv.reader(content.splitlines())
    header = next(reader, None)
    if header is None:
        logger.warning("empty CSV: %s", surface_flow_csv)
        return None

    # Find column indices (SU2 column names are case-insensitive, often quoted)
    header_clean = [h.strip().strip('"').lower() for h in header]
    try:
        x_idx = header_clean.index("x")
    except ValueError:
        logger.warning("x column not found in SU2 CSV: %s", header_clean)
        return None

    # Pressure_Coefficient may be named Cp, Pressure_Coefficient, or Pressure_Coeff
    cp_idx: int | None = None
    for cp_name in ("pressure_coefficient", "cp", "pressure_coeff"):
        if cp_name in header_clean:
            cp_idx = header_clean.index(cp_name)
            break
    if cp_idx is None:
        logger.warning("Cp column not found in SU2 CSV header: %s", header_clean)
        return None

    x_list: list[float] = []
    cp_list: list[float] = []
    for row in reader:
        if len(row) <= max(x_idx, cp_idx):
            continue
        try:
            x_val = float(row[x_idx])
            cp_val = float(row[cp_idx])
            # Filter out clearly invalid points (x/c outside [−0.1, 1.1] for airfoil)
            if -0.1 <= x_val <= 1.1:
                x_list.append(x_val)
                cp_list.append(cp_val)
        except (ValueError, IndexError):
            continue

    if not x_list:
        logger.warning("no Cp values parsed from %s", surface_flow_csv)
        return None

    return x_list, cp_list


def extract_naca0012_cp_openfoam(
    forces_csv: Path,
) -> tuple[list[float], list[float]] | None:
    """Extract Cp distribution from OpenFOAM forces object CSV output.

    OpenFOAM forces object writes per-surface data under
    ``postProcessing/forces/<time>/surfaceFields.dat``. For airfoil Cp
    extraction we expect a CSV-like format with columns x, y, Cp.

    If the actual OpenFOAM output uses a non-CSV format, this function
    tries to parse a simplified 2-column or 3-column text format.

    Args:
        forces_csv: Path to forces CSV file.

    Returns:
        Tuple (x_over_c_list, cp_list), or None if parsing fails.
    """
    if not forces_csv.exists():
        logger.warning("forces CSV not found: %s", forces_csv)
        return None

    try:
        content = forces_csv.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("failed to read forces CSV %s: %s", forces_csv, e)
        return None

    # Try CSV parsing first
    reader = csv.reader(content.splitlines())
    header = next(reader, None)
    if header is None:
        return None

    header_clean = [h.strip().strip('"').lower() for h in header]
    x_idx: int | None = None
    cp_idx: int | None = None
    for i, h in enumerate(header_clean):
        if h == "x" and x_idx is None:
            x_idx = i
        if h in ("cp", "pressure_coefficient") and cp_idx is None:
            cp_idx = i

    if x_idx is None or cp_idx is None:
        logger.warning(
            "OpenFOAM forces CSV missing x or Cp column: %s", header_clean
        )
        return None

    x_list: list[float] = []
    cp_list: list[float] = []
    for row in reader:
        if len(row) <= max(x_idx, cp_idx):
            continue
        try:
            x_val = float(row[x_idx])
            cp_val = float(row[cp_idx])
            if -0.1 <= x_val <= 1.1:
                x_list.append(x_val)
                cp_list.append(cp_val)
        except (ValueError, IndexError):
            continue

    if not x_list:
        logger.warning("no Cp values parsed from %s", forces_csv)
        return None

    return x_list, cp_list


def load_ladson_reference(csv_path: Path) -> tuple[list[float], list[float]] | None:
    """Load Ladson 1988 reference Cp data.

    Format (ladson1988.csv)::

        x/c,Cp
        0.0,1.0000
        0.025,-1.2140
        ...

    Args:
        csv_path: Path to ladson1988.csv.

    Returns:
        Tuple (x_over_c_list, cp_list), or None if file missing / parsing fails.
    """
    if not csv_path.exists():
        logger.warning("Ladson reference CSV not found: %s", csv_path)
        return None

    try:
        content = csv_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("failed to read Ladson CSV %s: %s", csv_path, e)
        return None

    reader = csv.reader(content.splitlines())
    header = next(reader, None)
    if header is None:
        return None

    x_list: list[float] = []
    cp_list: list[float] = []
    for row in reader:
        if len(row) < 2:
            continue
        try:
            x_list.append(float(row[0]))
            cp_list.append(float(row[1]))
        except (ValueError, IndexError):
            continue

    if not x_list:
        return None

    return x_list, cp_list


# === P2-c: Cl/Cd extractors for alpha sweep ===

def extract_cl_cd_openfoam(
    forces_dat: Path,
    rho: float = 1.225,
    u_inf: float = 100.0,
    a_ref: float = 1.0,
) -> tuple[float, float] | None:
    """Extract Cl/Cd from OpenFOAM forces.dat with divergence rollback.

    Parses the force history and returns the (Fx, Fy) from the latest stable
    time step. A step is considered the "stable tail" if no exponential
    divergence is detected: the algorithm scans backward from the last step
    and rolls back to the step immediately before the first 10x magnitude
    jump (if any). With no such jump detected the last step is returned
    (normal convergence, monotonic increase, slow drift, or bounded
    oscillation all fall through to this branch).

    This is more robust than taking the raw last line — when simpleFoam
    diverges late (typical SA behaviour on coarse high-y+ grids), the
    final line can be at e+15 while the physically meaningful value is
    at the pre-divergence step. Linear slow divergence (sub-10x per step)
    is NOT detected; callers needing that guarantee should pre-filter the
    force history.

    Two output formats are supported:
      - Foundation-style parenthesised vectors::

            # Forces
            # time forces (Fx Fy Fz) moments (Mx My Mz)
            0.000 (0.00123 -0.00045 0) (0 0 0.00001)

      - OpenCFD v2312/v2406 9-column space-separated::

            # Forces
            # time total_x total_y total_z pressure_x ... viscous_x ...

    Cl = Fy / q_inf / A_ref, Cd = Fx / q_inf / A_ref
    where q_inf = 0.5 * rho * U_inf^2.

    Args:
        forces_dat: Path to forces.dat (or force.dat — Foundation spelling).
        rho: Freestream density (kg/m³), default 1.225 (sea-level air).
        u_inf: Freestream velocity magnitude (m/s).
        a_ref: Reference area (m²), default 1.0 (chord × span for 2D).

    Returns:
        Tuple (cl, cd) from the latest stable step, or None if parsing fails.
    """
    if not forces_dat.exists():
        logger.warning("forces.dat not found: %s", forces_dat)
        return None

    try:
        content = forces_dat.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("failed to read forces.dat %s: %s", forces_dat, e)
        return None

    # Each data line: "time (Fx Fy Fz) (Mx My Mz)"
    # We extract Fx, Fy from the first parenthesized group
    vector_pattern = re.compile(
        r"\(\s*([0-9.eE+\-]+)\s+([0-9.eE+\-]+)\s+([0-9.eE+\-]+)\s*\)"
    )
    # OpenCFD v2312/v2406 alternative: space/tab-separated 10 columns
    # "time total_x total_y total_z pressure_x pressure_y pressure_z viscous_x viscous_y viscous_z"
    # We capture only total_x/total_y (groups 2/3) — viscous_x/viscous_y are
    # not used because total already includes them.
    opencfd_pattern = re.compile(
        r"^\s*([0-9.eE+\-]+)\s+"  # time (group 1)
        r"([0-9.eE+\-]+)\s+([0-9.eE+\-]+)\s+[0-9.eE+\-]+\s+"  # total x,y,z (groups 2,3)
        r"(?:[0-9.eE+\-]+\s+){3}"  # pressure x,y,z (non-capturing)
        r"(?:[0-9.eE+\-]+\s+){2}[0-9.eE+\-]+"  # viscous x,y,z (non-capturing)
    )

    # Collect all (time, fx, fy) data points
    force_history: list[tuple[float, float, float]] = []
    for line in content.splitlines():
        # Skip comments / headers
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        match = vector_pattern.search(line)
        if match:
            time_val = _safe_float_from_line(stripped)
            fx = float(match.group(1))
            fy = float(match.group(2))
            force_history.append((time_val, fx, fy))
            continue
        # OpenCFD 2406 format fallback
        m2 = opencfd_pattern.search(line)
        if m2:
            time_val = float(m2.group(1))
            total_fx = float(m2.group(2))
            total_fy = float(m2.group(3))
            # total = pressure + viscous; total is what we want for Cl/Cd
            force_history.append((time_val, total_fx, total_fy))

    if not force_history:
        logger.warning("no force vectors parsed from %s", forces_dat)
        return None

    q_inf = 0.5 * rho * u_inf * u_inf
    if q_inf <= 0 or a_ref <= 0:
        logger.warning("invalid q_inf=%s or a_ref=%s", q_inf, a_ref)
        return None

    # Detect divergence: if forces grow by more than 10x from one step to
    # the next, the simulation has diverged. Return the last step *before*
    # the first divergence — once a run diverges, every subsequent step
    # also diverges (cascading 10x+ jumps), so scanning backward would
    # land on an already-diverged step. Forward scan catches the first
    # jump and returns the pre-divergence step.
    # Skip the first data point in divergence detection — it's often near-zero
    # (initialization) and naturally shows a large jump to the second step.
    if len(force_history) <= 2:
        _, best_fx, best_fy = force_history[-1]
    else:
        mags = [(fx * fx + fy * fy) ** 0.5 for _, fx, fy in force_history]
        best_idx = len(force_history) - 1  # default: last step (no divergence)
        # Scan forward, starting from step 2 (index 1). First 10x jump wins.
        for i in range(2, len(mags)):
            if mags[i] > mags[i - 1] * 10:
                # First divergence detected at step i; use step i-1.
                best_idx = i - 1
                break
        _, best_fx, best_fy = force_history[best_idx]

    cd = best_fx / q_inf / a_ref
    cl = best_fy / q_inf / a_ref
    return cl, cd


def _safe_float_from_line(line: str) -> float:
    """Extract the first float token from a line (the time value)."""
    tokens = line.split()
    if tokens:
        try:
            return float(tokens[0])
        except ValueError:
            pass
    return 0.0


def extract_cl_cd_su2(
    surface_flow_csv: Path,
    rho: float = 1.225,
    u_inf: float = 100.0,
    a_ref: float = 1.0,
) -> tuple[float, float] | None:
    """Extract Cl/Cd from SU2 surface_flow.csv by integrating pressure + shear.

    SU2 surface_flow.csv (v8.0+) for wall markers contains per-surface-point:
    x, y, Pressure, Pressure_Coefficient, Skin_Friction_Coefficient.

    For Cl/Cd we integrate:
        Cd_pressure = ∮ Cp * n_x dS / A_ref
        Cl_pressure = ∮ Cp * n_y dS / A_ref
    (simplified: assume unit chord and 2D, neglect shear contribution in v1)

    For v1 we use a simpler approach: average Cp on upper vs lower surface and
    multiply by chord. This is approximate but sufficient for polar curve
    trend comparison. A proper integration would require surface normals.

    Args:
        surface_flow_csv: Path to surface_flow.csv.
        rho, u_inf, a_ref: Same as extract_cl_cd_openfoam.

    Returns:
        Tuple (cl, cd), or None if parsing fails.
    """
    if not surface_flow_csv.exists():
        logger.warning("SU2 CSV not found: %s", surface_flow_csv)
        return None

    try:
        content = surface_flow_csv.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("failed to read CSV %s: %s", surface_flow_csv, e)
        return None

    reader = csv.reader(content.splitlines())
    header = next(reader, None)
    if header is None:
        return None

    header_clean = [h.strip().strip('"').lower() for h in header]
    try:
        x_idx = header_clean.index("x")
        y_idx = header_clean.index("y")
    except ValueError:
        logger.warning("x/y columns not found in SU2 CSV: %s", header_clean)
        return None

    cp_idx: int | None = None
    for cp_name in ("pressure_coefficient", "cp", "pressure_coeff"):
        if cp_name in header_clean:
            cp_idx = header_clean.index(cp_name)
            break

    if cp_idx is None:
        logger.warning("Cp column not found in SU2 CSV header: %s", header_clean)
        return None

    # Collect upper (y > 0) and lower (y < 0) surface points
    upper_x: list[float] = []
    upper_cp: list[float] = []
    lower_x: list[float] = []
    lower_cp: list[float] = []
    for row in reader:
        if len(row) <= max(x_idx, y_idx, cp_idx):
            continue
        try:
            x = float(row[x_idx])
            y = float(row[y_idx])
            cp = float(row[cp_idx])
            if y > 1e-6:
                upper_x.append(x)
                upper_cp.append(cp)
            elif y < -1e-6:
                lower_x.append(x)
                lower_cp.append(cp)
        except (ValueError, IndexError):
            continue

    if not upper_x or not lower_x:
        logger.warning("insufficient upper/lower points in %s", surface_flow_csv)
        return None

    # Approximate Cl via trapezoidal integration of (Cp_lower - Cp_upper) dx
    # Cl ≈ ∫₀¹ (Cp_lower - Cp_upper) dx / chord
    # (neglecting angle-of-attack projection; suitable for low α)
    def _trap_integrate(xs: list[float], ys: list[float]) -> float:
        pairs = sorted(zip(xs, ys, strict=False))
        total = 0.0
        for i in range(1, len(pairs)):
            x0, y0 = pairs[i - 1]
            x1, y1 = pairs[i]
            total += 0.5 * (y0 + y1) * (x1 - x0)
        return total

    cl_approx = _trap_integrate(lower_x, lower_cp) - _trap_integrate(upper_x, upper_cp)

    # Cd approximation: form drag from Cp integration along x
    # Very rough — for trend only
    cd_approx = abs(_trap_integrate(upper_x, upper_cp) + _trap_integrate(lower_x, lower_cp)) * 0.01

    return cl_approx, cd_approx


def load_ladson_polar(csv_path: Path) -> list[tuple[float, float, float]] | None:
    """Load Ladson 1988 polar reference data.

    Format (ladson_polar.csv)::

        alpha_deg, Cl, Cd
        0.0, 0.000, 0.0086
        5.0, 0.456, 0.0095
        ...

    Args:
        csv_path: Path to ladson_polar.csv.

    Returns:
        List of (alpha_deg, cl, cd) tuples sorted by alpha, or None if fails.
    """
    if not csv_path.exists():
        logger.warning("Ladson polar CSV not found: %s", csv_path)
        return None

    try:
        content = csv_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("failed to read polar CSV %s: %s", csv_path, e)
        return None

    reader = csv.reader(content.splitlines())
    header = next(reader, None)
    if header is None:
        return None

    points: list[tuple[float, float, float]] = []
    for row in reader:
        if len(row) < 3:
            continue
        try:
            alpha = float(row[0])
            cl = float(row[1])
            cd = float(row[2])
            points.append((alpha, cl, cd))
        except (ValueError, IndexError):
            continue

    if not points:
        return None

    return sorted(points, key=lambda p: p[0])


# === P4.5: StarCCM Cl/Cd extractor ===


def extract_cl_cd_starccm(
    forces_csv: Path,
    rho: float = 1.225,
    u_inf: float = 100.0,
    a_ref: float = 1.0,
) -> tuple[float, float] | None:
    """Extract Cl/Cd from Star-CCM+ forces.csv export.

    The StarCCM macro writes a forces.csv with columns like::

        Iteration, Cd, Cl, Cm
        0, 0.0, 0.0, 0.0
        1, 0.0012, 0.0456, -0.0023
        ...
        500, 0.0086, 0.3240, -0.0150

    This function reads the last data row for the final converged
    Cl and Cd values.

    Args:
        forces_csv: Path to forces.csv file.
        rho: Freestream density (kg/m³), default 1.225 (sea-level air).
        u_inf: Freestream velocity magnitude (m/s).
        a_ref: Reference area (m²), default 1.0 (chord x span for 2D).

    Returns:
        Tuple (cl, cd) from the last row, or None if parsing fails.
    """
    if not forces_csv.exists():
        logger.warning("StarCCM forces CSV not found: %s", forces_csv)
        return None

    try:
        content = forces_csv.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("failed to read forces CSV %s: %s", forces_csv, e)
        return None

    reader = csv.reader(content.splitlines())
    header = next(reader, None)
    if header is None:
        logger.warning("empty forces CSV: %s", forces_csv)
        return None

    # Normalize header names (strip whitespace, quotes, lowercase)
    header_clean = [h.strip().strip('"').lower() for h in header]

    cd_idx: int | None = None
    cl_idx: int | None = None
    for i, h in enumerate(header_clean):
        if h in ("cd", "drag_coefficient", "drag_coeff"):
            cd_idx = i
        if h in ("cl", "lift_coefficient", "lift_coeff"):
            cl_idx = i

    if cd_idx is None or cl_idx is None:
        logger.warning(
            "Cd or Cl column not found in forces CSV header: %s",
            header_clean,
        )
        return None

    # Read data rows; use the last valid row
    last_cd: float | None = None
    last_cl: float | None = None
    for row in reader:
        if len(row) <= max(cd_idx, cl_idx):
            continue
        try:
            last_cd = float(row[cd_idx])
            last_cl = float(row[cl_idx])
        except (ValueError, IndexError):
            continue

    if last_cd is None or last_cl is None:
        logger.warning("no valid Cl/Cd values in %s", forces_csv)
        return None

    # Cl and Cd from StarCCM are already non-dimensionalized by
    # the ReferenceValues set in the macro (rho, u_inf, A_ref).
    # Return them directly — no need to re-compute via q_inf.
    return last_cl, last_cd
