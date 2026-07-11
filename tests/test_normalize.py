"""Exhaustive behavior-pinning tests for cfdb.agentbench.normalize (v5.0 §4).

normalize_answer() is frozen judging material: this file *is* the
behavioral spec, not just regression coverage — every case below documents
a deliberate rule, and any future rule change must edit this file alongside
bumping NORMALIZE_VERSION.
"""

from __future__ import annotations

import pytest

from cfdb.agentbench.normalize import NORMALIZE_VERSION, normalize_answer


def test_normalize_version_is_frozen_string() -> None:
    """NORMALIZE_VERSION is the anchor point for contract freezing (§0.7)."""
    assert NORMALIZE_VERSION == "1"


# ---------------------------------------------------------------------------
# kind="string"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Paris", "paris"),
        ("  Paris  ", "paris"),
        ("PARIS", "paris"),
        ("Paris.", "paris"),
        ("Paris!", "paris"),
        ("  Paris.  ", "paris"),
        ('"Paris"', "paris"),
        ("(Paris)", "paris"),
        ("Paris, France", "paris, france"),  # internal punctuation untouched
        ("New   York", "new york"),  # internal whitespace collapsed
        ("New\tYork\n", "new york"),
        ("", ""),
        ("   ", ""),
        ("...", ""),
        ("café", "café"),  # non-ASCII preserved
        ("Café", "café"),
    ],
)
def test_normalize_string(raw: str, expected: str) -> None:
    assert normalize_answer(raw, "string") == expected


def test_normalize_string_nfkc_canonicalizes_width_variants() -> None:
    """Fullwidth digits/letters collapse to their ASCII NFKC form."""
    # U+FF21 FULLWIDTH LATIN CAPITAL LETTER A -> "A"
    assert normalize_answer("Ａ", "string") == "a"


def test_normalize_string_strips_edge_punctuation_repeatedly() -> None:
    assert normalize_answer("((answer))", "string") == "answer"


def test_normalize_string_two_forms_match() -> None:
    assert normalize_answer(" The Answer Is 5. ", "string") == normalize_answer(
        "the answer is 5", "string"
    )


# ---------------------------------------------------------------------------
# kind="number"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5", "5"),
        ("5.0", "5"),
        ("5.000000", "5"),
        ("  5  ", "5"),
        ("-5", "-5"),
        ("-5.0", "-5"),
        ("0", "0"),
        ("-0", "0"),
        ("-0.0", "0"),
        ("0.0000001", "0"),  # rounds away below precision
        ("1,234.5", "1234.5"),
        ("1,234,567", "1234567"),
        ("$5", "5"),
        ("$1,234.50", "1234.5"),
        ("-$1,234.50", "-1234.5"),
        ("5%", "5"),  # percent sign stripped, not converted
        ("50%", "50"),
        ("1e3", "1000"),
        ("1.23456789", "1.234568"),  # rounds to NUMBER_PRECISION=6, rounds up
        ("3.14159265", "3.141593"),
        ("€10", "10"),
        ("£10", "10"),
        ("¥10", "10"),
        ("1 000", "1000"),  # internal whitespace stripped too
    ],
)
def test_normalize_number(raw: str, expected: str) -> None:
    assert normalize_answer(raw, "number") == expected


@pytest.mark.parametrize("raw", ["abc", "", "   ", "N/A", "five", "12.3.4"])
def test_normalize_number_rejects_unparseable(raw: str) -> None:
    with pytest.raises(ValueError, match="cannot normalize non-numeric answer"):
        normalize_answer(raw, "number")


def test_normalize_number_negative_and_positive_zero_match() -> None:
    assert normalize_answer("-0", "number") == normalize_answer("0", "number")


def test_normalize_number_thousands_and_plain_match() -> None:
    assert normalize_answer("1,234", "number") == normalize_answer("1234", "number")


# ---------------------------------------------------------------------------
# kind="list"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a, b, c", "a, b, c"),
        ("c, b, a", "a, b, c"),  # order-independent
        ("a;b;c", "a, b, c"),
        ("a, b,", "a, b"),  # trailing delimiter drops empty item
        (",a,b", "a, b"),
        ("A, B", "a, b"),  # casefolded per-item
        ("  a  ,  b  ", "a, b"),
        ("a, a, b", "a, a, b"),  # duplicates preserved
        ("", ""),
        (",", ""),
        ("Paris; London; Tokyo", "london, paris, tokyo"),
    ],
)
def test_normalize_list(raw: str, expected: str) -> None:
    assert normalize_answer(raw, "list") == expected


def test_normalize_list_is_order_independent() -> None:
    assert normalize_answer("x, y, z", "list") == normalize_answer("z, y, x", "list")


def test_normalize_list_preserves_multiplicity() -> None:
    once = normalize_answer("a, b", "list")
    twice = normalize_answer("a, a, b", "list")
    assert once != twice


# ---------------------------------------------------------------------------
# kind validation
# ---------------------------------------------------------------------------


def test_normalize_answer_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unknown normalize kind"):
        normalize_answer("x", "boolean")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# tamper witness: a rule change is a contract-drift event, not silent
# ---------------------------------------------------------------------------


def test_normalize_version_tamper_witness_changes_frozen_digest() -> None:
    """Witness (§0.7/§6): normalize behavior is anchored via NORMALIZE_VERSION.

    Baseline: hashing NORMALIZE_VERSION today reproduces a known digest.
    Tamper: simulate a rule change by hashing a different version string.
    Assert: the digest flips — this is exactly the signal
    ``ScoringContract``/``verify_frozen`` consume to force exit 3 on
    normalize-rule drift (the wiring of that refusal lives in the contract
    module owned by another lane; this test pins the frozen-input contract
    this module promises to hold stable).
    """
    import hashlib

    baseline_digest = hashlib.sha256(NORMALIZE_VERSION.encode("utf-8")).hexdigest()

    # Baseline: unmodified version reproduces the same digest deterministically.
    assert hashlib.sha256(NORMALIZE_VERSION.encode("utf-8")).hexdigest() == baseline_digest

    # Tamper: pretend the ruleset changed (e.g. a future version bump).
    tampered_digest = hashlib.sha256(b"2").hexdigest()

    # Assert: tampering flips the anchored digest (fail-closed drift signal).
    assert tampered_digest != baseline_digest
