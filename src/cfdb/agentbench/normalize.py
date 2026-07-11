"""Version-frozen answer normalization for agentic quasi-exact-match judging
(Architecture v5.0 §4).

``normalize_answer`` implements a GAIA-style normalization used by the
quasi-exact-match checker family for scalar/short-answer agentic subtasks
(data extraction, fact lookup). Because normalization *is* judging material —
loosening or tightening a rule silently shifts which submissions pass — its
behavior is treated as frozen, versioned contract content, not an
implementation detail:

- :data:`NORMALIZE_VERSION` is anchored (sha256) into the scoring contract's
  frozen map alongside ``case.yaml`` and the reference files. Any change to
  the rules in this module MUST bump the version so drift is caught as a
  fail-closed exit 3 refusal instead of a silently shifted outcome.
- Every branch's behavior is exhaustively pinned by ``tests/test_normalize.py``
  — that test file *is* the behavioral spec for this module.

This module has no side effects and does no I/O; it is pure text
transformation so it can be called both by case-author tooling and by the
checker/scoring path with identical results.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

NORMALIZE_VERSION = "1"
"""Frozen normalization ruleset version (see module docstring)."""

NUMBER_PRECISION = 6
"""Decimal places a numeric answer is rounded to before comparison."""

NormalizeKind = Literal["string", "number", "list"]

_WHITESPACE_RE = re.compile(r"\s+")
_STRIP_CHARS = ".,;:!?\"'()[]{}<>"
_NUMERIC_STRIP_RE = re.compile(r"[,$€£¥%\s]")
_LIST_SPLIT_RE = re.compile(r"[,;]")


def _normalize_string(value: str) -> str:
    """Casefold, Unicode-canonicalize, collapse whitespace, trim edge punctuation.

    Steps (in order): NFKC Unicode normalization -> strip -> casefold ->
    collapse any internal whitespace run to a single space -> strip a fixed
    set of edge punctuation characters (``.,;:!?"'()[]{}<>``). Internal
    punctuation is left untouched — only characters at the very start/end
    of the (already whitespace-collapsed) string are trimmed, repeatedly.
    """
    text = unicodedata.normalize("NFKC", value)
    text = text.strip().casefold()
    text = _WHITESPACE_RE.sub(" ", text)
    text = text.strip(_STRIP_CHARS)
    return text


def _normalize_number(value: str) -> str:
    """Parse a numeric answer and render it in a canonical decimal form.

    Steps: strip whitespace -> drop thousands separators / currency symbols
    / a trailing percent sign / internal whitespace (``,$€£¥%`` and any
    whitespace) -> ``float()`` parse -> round to :data:`NUMBER_PRECISION`
    decimal places -> render fixed-point -> strip trailing fractional
    zeros (and a bare trailing ``.``) -> canonicalize negative zero to
    ``"0"``.

    A percent sign is stripped, not converted (``"50%"`` normalizes the
    same as ``"50"``, not ``"0.5"``) — callers comparing percentages must
    keep both sides in the same convention.

    Raises:
        ValueError: If, after stripping, the remaining text is not a
            valid float literal (fail-closed: never silently coerced to
            ``0`` or dropped).
    """
    stripped = _NUMERIC_STRIP_RE.sub("", value.strip())
    try:
        numeric = float(stripped)
    except ValueError as e:
        raise ValueError(f"cannot normalize non-numeric answer: {value!r}") from e

    rounded = round(numeric, NUMBER_PRECISION)
    text = f"{rounded:.{NUMBER_PRECISION}f}"
    if text.startswith("-") and float(text) == 0.0:
        text = text[1:]
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text == "" or text == "-":
        text = "0"
    return text


def _normalize_list(value: str) -> str:
    """Split a delimited answer into normalized items, sorted for comparison.

    Steps: split on ``,`` or ``;`` -> apply :func:`_normalize_string` to each
    item -> drop items that normalize to the empty string (e.g. a trailing
    delimiter) -> sort lexicographically -> join with ``", "``.

    Sorting makes the result order-independent (``"b, a"`` and ``"a, b"``
    normalize identically); duplicate items are preserved (``"a, a"`` does
    NOT normalize the same as ``"a"``) since a checker may legitimately
    require a specific multiplicity.
    """
    raw_items = _LIST_SPLIT_RE.split(value)
    items = [_normalize_string(item) for item in raw_items]
    items = [item for item in items if item != ""]
    items.sort()
    return ", ".join(items)


def normalize_answer(value: str, kind: NormalizeKind) -> str:
    """Normalize a raw answer string for quasi-exact-match comparison.

    Args:
        value: Raw answer text (from a submission or a case's expected
            answer) to normalize.
        kind: Which normalization ruleset to apply — ``"string"`` for free
            text, ``"number"`` for numeric answers, ``"list"`` for
            delimited multi-item answers.

    Returns:
        The canonical normalized form. Two answers are considered a match
        iff their normalized forms (of the same ``kind``) are equal.

    Raises:
        ValueError: If ``kind`` is not one of the three known values, or
            (for ``kind="number"``) ``value`` is not parseable as a number
            after stripping (fail-closed: never silently coerced).
    """
    if kind == "string":
        return _normalize_string(value)
    if kind == "number":
        return _normalize_number(value)
    if kind == "list":
        return _normalize_list(value)
    raise ValueError(f"unknown normalize kind: {kind!r}")
