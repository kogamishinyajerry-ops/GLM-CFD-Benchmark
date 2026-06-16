"""DVC (Data Version Control) wrapper utilities.

This subpackage provides a thin Python wrapper around the DVC CLI for managing
large files (meshes, reference datasets). It does NOT depend on DVC being
installed at import time — all functions gracefully handle missing DVC.

P2-b feature. Used by CLI `cfdb data pull` / `cfdb data status` commands.
"""

from __future__ import annotations

from cfdb.data.dvc import (
    DVC_AVAILABLE,
    DVCError,
    dvc_available,
    dvc_pull,
    dvc_status,
)

__all__ = [
    "DVC_AVAILABLE",
    "DVCError",
    "dvc_available",
    "dvc_pull",
    "dvc_status",
]
