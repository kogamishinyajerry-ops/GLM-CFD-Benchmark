"""Common utility functions used across the cfdb package."""

from __future__ import annotations

import logging
import re
import secrets
import subprocess
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def generate_run_id(case_id: str, solver: str) -> str:
    """Generate a unique run identifier.

    Format: YYYYMMDDTHHMMSSZ_<case_id>_<solver>_<hash8>
    where <hash8> is 8 hex characters from secrets.token_hex(4).

    Args:
        case_id: The case identifier.
        solver: The solver name.

    Returns:
        A unique run_id string.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    random_hash = secrets.token_hex(4)
    return f"{timestamp}_{case_id}_{solver}_{random_hash}"


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Returns:
        ISO 8601 formatted UTC timestamp string.
    """
    return datetime.now(timezone.utc).isoformat()


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime.

    Returns:
        Timezone-aware datetime in UTC.
    """
    return datetime.now(timezone.utc)


def get_git_commit() -> str | None:
    """Get the current git commit hash.

    Returns:
        The commit hash string, or None if git is unavailable or not a git repo.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        logger.debug("git not available or not a git repo")
    return None


def validate_case_id(case_id: str) -> bool:
    """Check if a case_id matches the required pattern ^[a-z][a-z0-9_]*$.

    Args:
        case_id: The case identifier to validate.

    Returns:
        True if valid, False otherwise.
    """
    return bool(re.match(r"^[a-z][a-z0-9_]*$", case_id))
