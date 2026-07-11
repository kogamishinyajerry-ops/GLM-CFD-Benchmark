"""Checker for dir_organize (Architecture v5.0 §4).

State-based checker (WorkArena/OSWorld/tau-bench style): asserts the
submission directory's final file-tree state exactly matches the expected
by-extension reorganization of ``visible/messy/`` -- same subdirectory
set, same filename set per subdirectory, byte-identical content.

Standard library only, by construction and by ``validate_checker``'s
admission-time scan (no ``subprocess``/``socket``/``ctypes``/``importlib``,
no ``eval``/``exec``).

Outputs ``{"success": bool, "evidence": [...]}`` as the trailing stdout
line, per the score_agentic() contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _expected_layout(messy_dir: Path) -> dict[str, dict[str, bytes]]:
    """Build ext (no dot, lowercase) -> {filename: content_bytes} from messy_dir."""
    layout: dict[str, dict[str, bytes]] = {}
    for path in sorted(messy_dir.iterdir()):
        if not path.is_file():
            continue
        ext = path.suffix.lstrip(".").lower()
        layout.setdefault(ext, {})[path.name] = path.read_bytes()
    return layout


def main() -> None:
    submission_dir = Path(sys.argv[1])
    case_dir = Path(__file__).resolve().parent.parent
    messy_dir = case_dir / "visible" / "messy"
    expected = _expected_layout(messy_dir)

    evidence: list[str] = []
    ok = True

    actual_top_level_dirs = (
        sorted(p.name for p in submission_dir.iterdir() if p.is_dir())
        if submission_dir.is_dir()
        else []
    )
    expected_dirs = sorted(expected.keys())
    if actual_top_level_dirs != expected_dirs:
        ok = False
        evidence.append(
            f"top-level subdirectory set mismatch: got {actual_top_level_dirs!r}, "
            f"expected {expected_dirs!r}"
        )
    else:
        evidence.append(f"top-level subdirectories matched: {expected_dirs!r}")

    for ext, files in sorted(expected.items()):
        ext_dir = submission_dir / ext
        if not ext_dir.is_dir():
            ok = False
            evidence.append(f"missing expected subdirectory '{ext}/'")
            continue
        actual_names = sorted(p.name for p in ext_dir.iterdir() if p.is_file())
        expected_names = sorted(files.keys())
        if actual_names != expected_names:
            ok = False
            evidence.append(
                f"'{ext}/' filename set mismatch: got {actual_names!r}, "
                f"expected {expected_names!r}"
            )
            continue
        for filename, expected_bytes in files.items():
            actual_bytes = (ext_dir / filename).read_bytes()
            if actual_bytes != expected_bytes:
                ok = False
                evidence.append(f"'{ext}/{filename}' content mismatch")
            else:
                evidence.append(f"'{ext}/{filename}' content matched byte-for-byte")

    print(json.dumps({"success": ok, "evidence": evidence}))


if __name__ == "__main__":
    main()
