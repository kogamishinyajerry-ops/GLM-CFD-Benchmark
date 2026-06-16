"""DVC CLI wrapper — thin Python interface to `dvc` executable.

All functions check for DVC availability via `dvc_available()` and raise
`DVCError` if DVC is not installed. The `DVC_AVAILABLE` constant is evaluated
once at import time for convenience.

Design notes:
- Uses subprocess to invoke the `dvc` CLI (not the Python SDK) for stability.
- All subprocess calls are mockable via `unittest.mock.patch("cfdb.data.dvc.subprocess.run")`,
  enabling full test coverage without installing DVC.
- Timeouts are generous (dvc pull over slow networks can take minutes).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class DVCError(Exception):
    """DVC operation failed (DVC not installed, pull failed, status failed, etc)."""


def dvc_available() -> bool:
    """Check whether the `dvc` executable is available on PATH.

    Returns:
        True if `dvc --version` exits 0, False otherwise.
    """
    dvc_path = shutil.which("dvc")
    if not dvc_path:
        return False
    try:
        subprocess.run(
            [dvc_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


# Module-level constant: evaluate once at import. Tests can patch
# cfdb.data.dvc.dvc_available to override.
DVC_AVAILABLE: bool = dvc_available()


def dvc_pull(
    targets: list[str] | None = None,
    cwd: Path | None = None,
    timeout: int = 600,
) -> str:
    """Run `dvc pull` to fetch tracked data from remote.

    Args:
        targets: Specific .dvc file targets (relative paths). If None or empty,
            pulls all tracked data.
        cwd: Working directory (defaults to current dir). Should be the project
            root containing `.dvc/` directory.
        timeout: Subprocess timeout in seconds (default 600s = 10 min).

    Returns:
        DVC stdout output (progress messages).

    Raises:
        DVCError: If DVC is not installed or pull fails (network error, remote
            unreachable, missing .dvc cache, etc).
    """
    if not dvc_available():
        raise DVCError(
            "dvc not found on PATH. Install DVC: pip install dvc"
        )
    cmd: list[str] = ["dvc", "pull"]
    if targets:
        cmd.extend(targets)
    logger.info("running dvc pull in %s (targets=%s)", cwd or Path.cwd(), targets)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd or Path.cwd()),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise DVCError(
            f"dvc pull failed (exit {proc.returncode}): {(proc.stderr or '').strip()}"
        )
    return proc.stdout


def dvc_status(cwd: Path | None = None, timeout: int = 60) -> dict[str, object]:
    """Run `dvc status --json` and return the parsed status dict.

    Args:
        cwd: Working directory (defaults to current dir).
        timeout: Subprocess timeout in seconds (default 60s).

    Returns:
        Parsed JSON dict from `dvc status`. Empty dict means workspace is
        up-to-date. Non-empty means some tracked files are missing or changed.

    Raises:
        DVCError: If DVC not installed or status fails.
    """
    if not dvc_available():
        raise DVCError("dvc not found on PATH. Install DVC: pip install dvc")
    import json

    logger.info("running dvc status in %s", cwd or Path.cwd())
    proc = subprocess.run(
        ["dvc", "status", "--json"],
        cwd=str(cwd or Path.cwd()),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise DVCError(
            f"dvc status failed (exit {proc.returncode}): {(proc.stderr or '').strip()}"
        )
    return json.loads(proc.stdout or "{}")
