"""Tests for cfdb.reporting.gallery: the benchmark card-gallery HTML.

Covered:
- Smoke/mock fixtures (category == "smoke") are excluded from the gallery and
  disclosed by count — never presented as capability tests.
- Every substantive (non-smoke) shipped case has authored card.yaml prose.
- A coding io_oracle case surfaces its domain + gates (io_oracle_pass) and its
  frozen ruler verifies INTACT at render time.
- The rendered page is self-contained and emphasizes io_oracle_pass.
- A case without card.yaml is disclosed (fail-closed), never crashes the page.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from cfdb.reporting.gallery import _collect_gallery, render_gallery
from cfdb.reporting.showcase import assert_self_contained

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestGalleryAgainstShippedCases:
    def test_smoke_fixtures_excluded_and_disclosed(self) -> None:
        data = _collect_gallery(PROJECT_ROOT)
        # mock_success/failure/missing_qoi/missing_reference + smoke_add_two(+_io)
        assert data["n_smoke"] == 6
        ids = {c["case_id"] for g in data["groups"] for c in g["cards"]}
        assert "smoke_add_two" not in ids
        assert "mock_success" not in ids

    def test_every_substantive_case_has_card_copy(self) -> None:
        data = _collect_gallery(PROJECT_ROOT)
        assert data["missing_card"] == [], data["missing_card"]
        assert data["total"] == 14
        # every card carries the three required prose fields
        for g in data["groups"]:
            for c in g["cards"]:
                assert c["capability"], c["case_id"]
                assert c["expected"], c["case_id"]
                assert c["criteria"], c["case_id"]

    def test_coding_io_case_surfaces_domain_and_gates(self) -> None:
        data = _collect_gallery(PROJECT_ROOT)
        cards = {c["case_id"]: c for g in data["groups"] for c in g["cards"]}
        assert "kth_largest" in cards, sorted(cards)
        c = cards["kth_largest"]
        assert c["domain"] == "coding"
        assert c["has_io_oracle"] is True
        assert "io_oracle_pass" in c["gates"]
        assert "tests_all_pass" in c["gates"]
        assert c["frozen_status"] == "INTACT"

    def test_legend_explains_only_present_badges(self, tmp_path: Path) -> None:
        data = _collect_gallery(PROJECT_ROOT)
        levels = {h["level"] for h in data["honesty_legend"]}
        # the three levels the shipped cases actually carry are explained
        assert {"REAL", "ANALYTIC", "MANUFACTURED"} <= levels
        # levels no shipped card carries must NOT appear (no phantom legend)
        assert "PREVIOUS_RUN" not in levels
        assert "SURROGATE" not in levels
        gates = {g["gate"] for g in data["gate_legend"]}
        assert "io_oracle_pass" in gates
        assert next(g for g in data["gate_legend"] if g["gate"] == "io_oracle_pass")["io"] is True
        # every legend entry carries an explanatory blurb
        for h in data["honesty_legend"]:
            assert h["blurb"]
        for g in data["gate_legend"]:
            assert g["blurb"]

    def test_render_self_contained_with_all_domains(self, tmp_path: Path) -> None:
        out = render_gallery(PROJECT_ROOT, tmp_path / "gallery.html")
        html = out.read_text(encoding="utf-8")
        assert_self_contained(html)  # no external refs (render already enforces)
        assert 'class="gate io"' in html  # io_oracle_pass emphasized
        assert 'data-domain="coding"' in html
        assert 'data-domain="agentic"' in html
        assert 'data-domain="cfd"' in html
        assert 'data-frozen="INTACT"' in html
        # footer discloses the excluded smoke fixtures
        assert "smoke" in html


def _write_min_case(cases_root: Path, case_id: str, *, category: str, domain: str) -> Path:
    case_dir = cases_root / category / case_id
    case_dir.mkdir(parents=True)
    spec = {
        "id": case_id,
        "name": case_id,
        "category": category,
        "domain": domain,
        "description": "min",
        "solvers": [{"name": "generic", "command": "true"}],
        "outputs": {"qoi": []},
        "reference": {"type": "manufactured"},
        "metrics": {},
        "budget": {"max_runtime_sec": 60},
    }
    (case_dir / "case.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    return case_dir


class TestGalleryFailClosed:
    def test_case_without_card_yaml_disclosed_not_crashed(self, tmp_path: Path) -> None:
        cases = tmp_path / "cases"
        _write_min_case(cases, "no_card", category="verification", domain="coding")
        _write_min_case(cases, "a_smoke", category="smoke", domain="coding")
        data = _collect_gallery(tmp_path)
        assert data["n_smoke"] == 1
        assert data["missing_card"] == ["no_card"]
        assert data["total"] == 0
        # renders (fail-closed) and discloses the missing card
        out = render_gallery(tmp_path, tmp_path / "gallery.html")
        html = out.read_text(encoding="utf-8")
        assert "no_card" in html
        assert_self_contained(html)

    def test_card_yaml_prose_renders(self, tmp_path: Path) -> None:
        cases = tmp_path / "cases"
        cd = _write_min_case(cases, "with_card", category="verification", domain="coding")
        (cd / "card.yaml").write_text(
            yaml.safe_dump(
                {
                    "title": "带卡片的测试",
                    "capability": "考察某能力",
                    "expected": "预期产出 42",
                    "criteria": "隐藏测试全过",
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        data = _collect_gallery(tmp_path)
        assert data["total"] == 1
        assert data["missing_card"] == []
        out = render_gallery(tmp_path, tmp_path / "gallery.html")
        html = out.read_text(encoding="utf-8")
        assert "带卡片的测试" in html
        assert "考察某能力" in html
        assert "预期产出 42" in html
