"""Tests for P4-B trust/ — TrustProfile five-dimension capability profile.

Covers the honest floor rules (None = insufficient data, never a
fabricated 0; no aggregate score) and the mandatory tamper witnesses
(tampered/removed metrics on disk must bite the profile).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from cfdb.schema import CaseSpec, MetricsResult, RunManifest
from cfdb.storage.json_repo import JsonManifestRepository
from cfdb.trust import DimensionScore, TrustProfile, build_profile, radar_svg

_T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_case(
    *,
    case_id: str = "trust_case",
    tolerances: dict[str, float] | None = None,
    abs_tolerances: dict[str, float] | None = None,
    budget_sec: int | None = 30,
    fields: list[str] | None = None,
    curves: list[str] | None = None,
    qoi: list[str] | None = None,
) -> CaseSpec:
    """Build a minimal CaseSpec for trust-profile tests."""
    data = {
        "id": case_id,
        "name": "Trust Test Case",
        "category": "smoke",
        "physics": {"flow": "incompressible", "dimensionality": "2d", "steady": True},
        "conditions": {"reynolds": 100.0},
        "solvers": [{"name": "generic", "command": "true"}],
        "outputs": {
            "fields": fields if fields is not None else [],
            "curves": curves if curves is not None else [],
            "qoi": qoi if qoi is not None else ["centerline_umax"],
        },
        "metrics": {
            "qoi_relative_tolerance": (
                tolerances if tolerances is not None else {"centerline_umax": 0.05}
            ),
            "qoi_absolute_tolerance": abs_tolerances if abs_tolerances is not None else {},
        },
        "budget": {"max_runtime_sec": budget_sec},
    }
    return CaseSpec.model_validate(data)


def save_run(
    repo: JsonManifestRepository,
    *,
    run_id: str,
    case_id: str = "trust_case",
    solver: str = "generic",
    status: str = "success",
    wall_time_sec: float = 6.0,
    start_offset_sec: int = 0,
    rel_errors: dict[str, float] | None = None,
    abs_errors: dict[str, float] | None = None,
    computed: dict[str, float] | None = None,
    artifacts: dict[str, Path] | None = None,
    budget_exceeded: bool = False,
) -> None:
    """Persist one run (manifest + metrics) through the repository."""
    start = _T0 + timedelta(seconds=start_offset_sec)
    manifest = RunManifest(
        run_id=run_id,
        case_id=case_id,
        solver=solver,
        status=status,  # type: ignore[arg-type]
        timing={
            "wall_time_sec": wall_time_sec,
            "start_time": start,
            "end_time": start + timedelta(seconds=wall_time_sec),
        },
        artifacts=artifacts or {},
    )
    metrics = MetricsResult(
        qoi_relative_errors=rel_errors if rel_errors is not None else {},
        qoi_absolute_errors=abs_errors if abs_errors is not None else {},
        qoi_pass=status == "success",
        overall_status="pass" if status == "success" else "fail",
        qoi_computed_values=computed,
        budget_exceeded=budget_exceeded,
    )
    repo.save_run(manifest, metrics)


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    """Temporary runs/ directory."""
    d = tmp_path / "runs"
    d.mkdir()
    return d


@pytest.fixture
def repo(runs_root: Path) -> JsonManifestRepository:
    """Repository over the temporary runs/ directory."""
    return JsonManifestRepository(runs_root)


def _two_good_runs(repo: JsonManifestRepository) -> None:
    """Two successful runs with QoI errors, computed values and artifacts."""
    save_run(
        repo,
        run_id="run_a",
        rel_errors={"centerline_umax": 0.005},
        computed={"centerline_umax": 0.373},
        artifacts={"U": Path("U.vtk")},
        start_offset_sec=0,
    )
    save_run(
        repo,
        run_id="run_b",
        rel_errors={"centerline_umax": 0.015},
        computed={"centerline_umax": 0.372},
        artifacts={"U": Path("U.vtk")},
        start_offset_sec=100,
    )


# Frozen from a real recorded run:
# runs/20260708T111832Z_lid_driven_cavity_openfoam_bb28984e/manifest.json.
# Artifact keys are run-relative POSIX paths produced by the OpenFOAM
# adapter's collect_outputs(); the declared LDC outputs are fields
# ["U", "p"] + qoi ["centerline_umax"]. Completeness matching MUST work
# against these real keys (regression for the bare-name key mismatch).
_LDC_REAL_ARTIFACT_KEYS: tuple[str, ...] = (
    ("case/0/U", "case/0/p")
    + tuple(
        f"case/{t}/{leaf}"
        for t in range(1, 6)
        for leaf in (
            "U",
            "p",
            "phi",
            "uniform/cumulativeContErr",
            "uniform/functionObjects/functionObjectProperties",
            "uniform/time",
        )
    )
    + (
        "case/constant/polyMesh/boundary",
        "case/constant/polyMesh/faces",
        "case/constant/polyMesh/neighbour",
        "case/constant/polyMesh/owner",
        "case/constant/polyMesh/points",
        "case/constant/transportProperties",
        "case/constant/turbulenceProperties",
        "case/log.block_mesh",
        "case/log.solve",
        "case/postProcessing/probes/0/U",
        "case/stderr.log",
        "case/stdout.log",
        "case/system/blockMeshDict",
        "case/system/controlDict",
        "case/system/fvSchemes",
        "case/system/fvSolution",
    )
)


def _ldc_real_artifacts() -> dict[str, Path]:
    """Real LDC run artifact mapping (key == relative path, as recorded)."""
    return {key: Path(key) for key in _LDC_REAL_ARTIFACT_KEYS}


# ---------------------------------------------------------------------------
# Profile building
# ---------------------------------------------------------------------------


class TestBuildProfile:
    def test_happy_path_all_dimensions_scored(self, repo: JsonManifestRepository) -> None:
        case = make_case(fields=["U"])
        _two_good_runs(repo)
        profile = build_profile(case, "generic", repo, honesty="REAL")

        assert profile.case_id == "trust_case"
        assert profile.solver == "generic"
        assert profile.n_runs == 2
        assert profile.honesty == "REAL"
        # accuracy: mean err 0.01 vs tol 0.05 -> 0.8
        assert profile.accuracy.score == pytest.approx(0.8)
        assert profile.robustness.score == pytest.approx(1.0)
        # efficiency: mean wall 6s vs budget 30s -> 0.8
        assert profile.efficiency.score == pytest.approx(0.8)
        assert profile.completeness.score == pytest.approx(1.0)
        assert profile.reproducibility.score is not None
        assert profile.reproducibility.score > 0.9
        for name in ("accuracy", "robustness", "efficiency", "completeness"):
            assert profile.dimension(name).evidence, f"{name} must carry evidence"

    def test_no_runs_gives_none_not_zero(self, repo: JsonManifestRepository) -> None:
        profile = build_profile(make_case(), "generic", repo, honesty="ANALYTIC")
        assert profile.n_runs == 0
        for name in ("accuracy", "robustness", "efficiency", "completeness", "reproducibility"):
            score = profile.dimension(name).score
            assert score is None, f"{name} must be None (insufficient data), got {score}"

    def test_accuracy_takes_worst_qoi(self, repo: JsonManifestRepository) -> None:
        case = make_case(tolerances={"cl": 0.05, "cd": 0.10}, qoi=["cl", "cd"])
        save_run(
            repo,
            run_id="run_a",
            rel_errors={"cl": 0.005, "cd": 0.09},  # cl -> 0.9, cd -> 0.1
            computed={"cl": 1.0, "cd": 0.02},
        )
        profile = build_profile(case, "generic", repo, honesty="REAL")
        assert profile.accuracy.score == pytest.approx(0.1)

    def test_efficiency_none_without_budget(self, repo: JsonManifestRepository) -> None:
        case = make_case(budget_sec=None)
        _two_good_runs(repo)
        profile = build_profile(case, "generic", repo, honesty="REAL")
        assert profile.efficiency.score is None
        assert any("budget" in line for line in profile.efficiency.evidence)

    def test_reproducibility_needs_two_successes(self, repo: JsonManifestRepository) -> None:
        save_run(
            repo,
            run_id="run_a",
            rel_errors={"centerline_umax": 0.005},
            computed={"centerline_umax": 0.373},
        )
        profile = build_profile(make_case(), "generic", repo, honesty="REAL")
        assert profile.reproducibility.score is None

    def test_robustness_counts_failures(self, repo: JsonManifestRepository) -> None:
        save_run(repo, run_id="run_a", computed={"centerline_umax": 0.373},
                 rel_errors={"centerline_umax": 0.005})
        save_run(repo, run_id="run_b", status="failed", start_offset_sec=100)
        profile = build_profile(make_case(), "generic", repo, honesty="REAL")
        assert profile.n_runs == 2
        assert profile.robustness.score == pytest.approx(0.5)

    def test_completeness_flags_missing_output(self, repo: JsonManifestRepository) -> None:
        case = make_case(fields=["U", "p"])
        save_run(
            repo,
            run_id="run_a",
            rel_errors={"centerline_umax": 0.005},
            computed={"centerline_umax": 0.373},
            artifacts={"U": Path("U.vtk")},  # 'p' missing
        )
        profile = build_profile(case, "generic", repo, honesty="REAL")
        # delivered: qoi + U field of 3 expected
        assert profile.completeness.score == pytest.approx(2.0 / 3.0)
        assert any("missing field 'p'" in line for line in profile.completeness.evidence)

    def test_dry_run_excluded(self, repo: JsonManifestRepository) -> None:
        save_run(repo, run_id="run_a", computed={"centerline_umax": 0.373},
                 rel_errors={"centerline_umax": 0.005})
        save_run(repo, run_id="run_dry", status="dry_run", start_offset_sec=100)
        profile = build_profile(make_case(), "generic", repo, honesty="REAL")
        assert profile.n_runs == 1
        assert profile.robustness.score == pytest.approx(1.0)
        assert any("dry_run" in note for note in profile.notes)

    def test_other_solver_runs_ignored(self, repo: JsonManifestRepository) -> None:
        save_run(repo, run_id="run_a", solver="other",
                 computed={"centerline_umax": 0.373},
                 rel_errors={"centerline_umax": 0.005})
        profile = build_profile(make_case(), "generic", repo, honesty="REAL")
        assert profile.n_runs == 0


# ---------------------------------------------------------------------------
# Completeness against real adapter artifact naming (regression)
# ---------------------------------------------------------------------------


class TestCompletenessRealManifest:
    def test_real_ldc_manifest_paths_deliver_declared_fields(
        self, repo: JsonManifestRepository
    ) -> None:
        """Regression: fields 'U'/'p' live under keys like 'case/1/U'.

        The old bare-name lookup (``'U' in manifest.artifacts``) judged
        both fields missing on every real OpenFOAM run.
        """
        case = make_case(case_id="lid_driven_cavity", fields=["U", "p"])
        save_run(
            repo,
            run_id="run_real",
            case_id="lid_driven_cavity",
            rel_errors={"centerline_umax": 0.0173},
            computed={"centerline_umax": 0.6765},
            artifacts=_ldc_real_artifacts(),
        )
        profile = build_profile(case, "generic", repo, honesty="REAL")
        assert profile.completeness.score == pytest.approx(1.0)
        assert not any("missing" in line for line in profile.completeness.evidence)

    def test_curve_matched_by_file_stem(self, repo: JsonManifestRepository) -> None:
        """A curve 'residuals' delivered as '.../residuals.csv' counts."""
        case = make_case(fields=[], curves=["residuals"])
        save_run(
            repo,
            run_id="run_a",
            rel_errors={"centerline_umax": 0.005},
            computed={"centerline_umax": 0.373},
            artifacts={
                "case/postProcessing/residuals.csv": Path(
                    "case/postProcessing/residuals.csv"
                )
            },
        )
        profile = build_profile(case, "generic", repo, honesty="REAL")
        assert profile.completeness.score == pytest.approx(1.0)

    def test_undelivered_field_still_bites_with_real_paths(
        self, repo: JsonManifestRepository
    ) -> None:
        """Path-aware matching must not degenerate into always-present."""
        case = make_case(case_id="lid_driven_cavity", fields=["U", "p", "T"])
        save_run(
            repo,
            run_id="run_real",
            case_id="lid_driven_cavity",
            rel_errors={"centerline_umax": 0.0173},
            computed={"centerline_umax": 0.6765},
            artifacts=_ldc_real_artifacts(),
        )
        profile = build_profile(case, "generic", repo, honesty="REAL")
        # delivered: U, p, qoi of 4 expected; 'T' genuinely absent
        assert profile.completeness.score == pytest.approx(3.0 / 4.0)
        assert any("missing field 'T'" in line for line in profile.completeness.evidence)

    def test_tamper_stripped_artifacts_bite_completeness(
        self, repo: JsonManifestRepository, runs_root: Path
    ) -> None:
        """Tamper witness: emptying on-disk artifacts must drop completeness."""
        case = make_case(case_id="lid_driven_cavity", fields=["U", "p"])
        save_run(
            repo,
            run_id="run_real",
            case_id="lid_driven_cavity",
            rel_errors={"centerline_umax": 0.0173},
            computed={"centerline_umax": 0.6765},
            artifacts=_ldc_real_artifacts(),
        )
        before = build_profile(case, "generic", repo, honesty="REAL").completeness.score
        assert before == pytest.approx(1.0)

        manifest_path = runs_root / "run_real" / "manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["artifacts"] = {}
        manifest_path.write_text(json.dumps(payload, default=str), encoding="utf-8")

        after = build_profile(case, "generic", repo, honesty="REAL").completeness.score
        assert after is not None
        assert after < before, "stripped artifacts must bite the completeness score"


# ---------------------------------------------------------------------------
# Accuracy channels (zero-tolerance guard + absolute channel)
# ---------------------------------------------------------------------------


class TestAccuracyChannels:
    def test_zero_tolerance_degrades_to_none_not_crash(
        self, repo: JsonManifestRepository
    ) -> None:
        """Attack input: tolerance 0.0 must not divide by zero."""
        case = make_case(tolerances={"cl": 0.0}, qoi=["cl"])
        save_run(repo, run_id="run_a", rel_errors={"cl": 0.1}, computed={"cl": 1.0})
        profile = build_profile(case, "generic", repo, honesty="REAL")
        assert profile.accuracy.score is None
        assert any("non-positive tolerance" in line for line in profile.accuracy.evidence)

    def test_zero_tolerance_qoi_skipped_others_still_scored(
        self, repo: JsonManifestRepository
    ) -> None:
        case = make_case(tolerances={"cl": 0.0, "cd": 0.10}, qoi=["cl", "cd"])
        save_run(
            repo,
            run_id="run_a",
            rel_errors={"cl": 0.1, "cd": 0.05},
            computed={"cl": 1.0, "cd": 0.02},
        )
        profile = build_profile(case, "generic", repo, honesty="REAL")
        assert profile.accuracy.score == pytest.approx(0.5)
        assert any("non-positive tolerance" in line for line in profile.accuracy.evidence)

    def test_absolute_channel_scored_and_labelled(
        self, repo: JsonManifestRepository
    ) -> None:
        """Zero-reference QoI scored via qoi_absolute_errors, channel named."""
        case = make_case(tolerances={}, abs_tolerances={"cl": 0.01}, qoi=["cl"])
        save_run(repo, run_id="run_a", abs_errors={"cl": 0.005}, computed={"cl": 0.005})
        profile = build_profile(case, "generic", repo, honesty="REAL")
        assert profile.accuracy.score == pytest.approx(0.5)
        assert any("absolute channel" in line for line in profile.accuracy.evidence)

    def test_channels_pooled_worst_wins(self, repo: JsonManifestRepository) -> None:
        """Relative and absolute per-QoI scores carry equal weight."""
        case = make_case(
            tolerances={"cd": 0.10}, abs_tolerances={"cl": 0.01}, qoi=["cl", "cd"]
        )
        save_run(
            repo,
            run_id="run_a",
            rel_errors={"cd": 0.01},  # relative -> 0.9
            abs_errors={"cl": 0.009},  # absolute -> 0.1 (worst)
            computed={"cl": 0.009, "cd": 0.02},
        )
        profile = build_profile(case, "generic", repo, honesty="REAL")
        assert profile.accuracy.score == pytest.approx(0.1)

    def test_tamper_inflated_absolute_error_bites(
        self, repo: JsonManifestRepository, runs_root: Path
    ) -> None:
        """Tamper witness: inflating on-disk absolute error must drop accuracy."""
        case = make_case(tolerances={}, abs_tolerances={"cl": 0.01}, qoi=["cl"])
        save_run(repo, run_id="run_a", abs_errors={"cl": 0.001}, computed={"cl": 0.001})
        before = build_profile(case, "generic", repo, honesty="REAL").accuracy.score
        assert before == pytest.approx(0.9)

        metrics_path = runs_root / "run_a" / "metrics.json"
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        payload["qoi_absolute_errors"]["cl"] = 0.5
        metrics_path.write_text(json.dumps(payload), encoding="utf-8")

        after = build_profile(case, "generic", repo, honesty="REAL").accuracy.score
        assert after is not None
        assert after < before, "tampered absolute errors must bite the accuracy score"


# ---------------------------------------------------------------------------
# Efficiency budget_exceeded consumption
# ---------------------------------------------------------------------------


class TestEfficiencyBudgetExceeded:
    def test_budget_exceeded_run_penalised_and_disclosed(
        self, repo: JsonManifestRepository
    ) -> None:
        save_run(
            repo,
            run_id="run_a",
            rel_errors={"centerline_umax": 0.005},
            computed={"centerline_umax": 0.373},
        )
        save_run(
            repo,
            run_id="run_b",
            rel_errors={"centerline_umax": 0.015},
            computed={"centerline_umax": 0.372},
            budget_exceeded=True,
            start_offset_sec=100,
        )
        profile = build_profile(make_case(), "generic", repo, honesty="REAL")
        # base 1 - 6/30 = 0.8; penalty 0.5 * (1/2) = 0.25
        assert profile.efficiency.score == pytest.approx(0.55)
        assert any("budget_exceeded" in line for line in profile.efficiency.evidence)
        assert any("run_b" in line for line in profile.efficiency.evidence)

    def test_no_budget_exceeded_no_penalty_no_line(
        self, repo: JsonManifestRepository
    ) -> None:
        _two_good_runs(repo)
        profile = build_profile(make_case(), "generic", repo, honesty="REAL")
        assert profile.efficiency.score == pytest.approx(0.8)
        assert not any("budget_exceeded" in line for line in profile.efficiency.evidence)

    def test_tamper_flipped_budget_flag_bites(
        self, repo: JsonManifestRepository, runs_root: Path
    ) -> None:
        """Tamper witness: flipping budget_exceeded on disk must drop efficiency."""
        _two_good_runs(repo)
        before = build_profile(make_case(), "generic", repo, honesty="REAL").efficiency.score
        assert before == pytest.approx(0.8)

        metrics_path = runs_root / "run_a" / "metrics.json"
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        payload["budget_exceeded"] = True
        metrics_path.write_text(json.dumps(payload), encoding="utf-8")

        after = build_profile(make_case(), "generic", repo, honesty="REAL").efficiency.score
        assert after is not None
        assert after < before, "tampered budget flag must bite the efficiency score"


# ---------------------------------------------------------------------------
# Tamper witnesses — a tampered input MUST bite the verdict
# ---------------------------------------------------------------------------


class TestTamperWitness:
    def test_worsened_metrics_on_disk_bites_accuracy(
        self, repo: JsonManifestRepository, runs_root: Path
    ) -> None:
        """Tamper witness: inflating the on-disk relative error must drop accuracy."""
        case = make_case()
        _two_good_runs(repo)
        before = build_profile(case, "generic", repo, honesty="REAL").accuracy.score
        assert before == pytest.approx(0.8)

        metrics_path = runs_root / "run_a" / "metrics.json"
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        payload["qoi_relative_errors"]["centerline_umax"] = 0.5  # way past tolerance
        metrics_path.write_text(json.dumps(payload), encoding="utf-8")

        after = build_profile(case, "generic", repo, honesty="REAL").accuracy.score
        assert after is not None
        assert after < before, "tampered metrics must bite the accuracy score"

    def test_deleted_metrics_degrades_explicitly(
        self, repo: JsonManifestRepository, runs_root: Path
    ) -> None:
        """Tamper witness: deleting metrics.json must degrade, never fabricate."""
        case = make_case()
        save_run(repo, run_id="run_a", computed={"centerline_umax": 0.373},
                 rel_errors={"centerline_umax": 0.005})
        (runs_root / "run_a" / "metrics.json").unlink()

        profile = build_profile(case, "generic", repo, honesty="REAL")
        assert profile.accuracy.score is None
        assert any("unreadable" in note for note in profile.notes)
        # the run still exists as a status fact
        assert profile.n_runs == 1

    def test_bogus_honesty_level_rejected(self) -> None:
        """Gate witness: an invented honesty level must be rejected fail-closed."""
        with pytest.raises(ValidationError):
            TrustProfile(case_id="x", solver="s", honesty="TOTALLY_LEGIT")

    def test_score_out_of_bounds_rejected(self) -> None:
        """Gate witness: scores outside [0, 1] must be rejected."""
        with pytest.raises(ValidationError):
            DimensionScore(score=1.5)
        with pytest.raises(ValidationError):
            DimensionScore(score=-0.1)

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            TrustProfile.model_validate({"case_id": "x", "solver": "s", "total_score": 0.9})


# ---------------------------------------------------------------------------
# Model semantics
# ---------------------------------------------------------------------------


class TestModels:
    def test_honesty_defaults_fail_closed(self) -> None:
        profile = TrustProfile(case_id="x", solver="s")
        assert profile.honesty == "DECLARED-NOT-VERIFIED"

    def test_no_aggregate_score_field(self) -> None:
        """The profile deliberately has no aggregate/total score field."""
        field_names = set(TrustProfile.model_fields)
        assert not any("total" in n or "aggregate" in n or "overall" in n for n in field_names)

    def test_dimension_lookup_rejects_unknown(self) -> None:
        profile = TrustProfile(case_id="x", solver="s")
        with pytest.raises(KeyError):
            profile.dimension("velocity")

    def test_roundtrip_json(self) -> None:
        profile = TrustProfile(
            case_id="x", solver="s", n_runs=2, honesty="REAL",
            accuracy=DimensionScore(score=0.8, evidence=["e"]),
        )
        again = TrustProfile.model_validate_json(profile.model_dump_json())
        assert again == profile


# ---------------------------------------------------------------------------
# Radar SVG rendering
# ---------------------------------------------------------------------------


def _full_profile() -> TrustProfile:
    return TrustProfile(
        case_id="naca0012",
        solver="openfoam",
        n_runs=3,
        honesty="REAL",
        accuracy=DimensionScore(score=0.8),
        robustness=DimensionScore(score=1.0),
        efficiency=DimensionScore(score=0.6),
        completeness=DimensionScore(score=0.9),
        reproducibility=DimensionScore(score=0.95),
    )


class TestRadarSvg:
    def test_full_profile_renders_all_axes(self) -> None:
        svg = radar_svg.render(_full_profile())
        assert svg.startswith("<svg")
        assert svg.endswith("</svg>")
        for name in ("accuracy", "robustness", "efficiency", "completeness", "reproducibility"):
            assert name in svg
        assert "naca0012" in svg
        assert "honesty: REAL" in svg
        assert "insufficient data" not in svg
        assert "n_runs = 3" in svg

    def test_none_dimension_dashed_gap_not_zero(self) -> None:
        profile = _full_profile()
        profile = profile.model_copy(update={"reproducibility": DimensionScore()})
        svg = radar_svg.render(profile)
        assert svg.count("insufficient data") == 1
        assert "stroke-dasharray" in svg
        # the missing dimension must not be rendered as a 0.00 vertex label
        assert "0.00" not in svg

    def test_all_none_profile_renders_honestly(self) -> None:
        profile = TrustProfile(case_id="empty", solver="s", honesty="SURROGATE")
        svg = radar_svg.render(profile)
        assert svg.count("insufficient data") == 5
        assert "n_runs = 0" in svg

    def test_no_aggregate_number_by_design(self) -> None:
        svg = radar_svg.render(_full_profile())
        assert "no aggregate score (by design)" in svg

    def test_xml_escaping(self) -> None:
        profile = _full_profile().model_copy(update={"case_id": 'a<b&"c'})
        svg = radar_svg.render(profile)
        assert "a<b" not in svg
        assert "a&lt;b&amp;&quot;c" in svg
