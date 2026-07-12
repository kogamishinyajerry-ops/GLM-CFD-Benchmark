"""Self-contained single-file showcase HTML for the trust platform (P4-F).

Composes the v4 pillar modules (provenance / trust / failures / regression /
agentbench) into one zero-external-reference HTML page:

- Every number on the page is read or recomputed from real artifact files
  under the repository root (``cases/``, ``runs/``, ``failures/``,
  ``baselines/``, ``agentbench/``). Self-reported values are never accepted:
  the regression gate is re-evaluated and the frozen contracts are re-hashed
  at render time.
- Sections without data render an explicit empty state; example data is
  never fabricated (fail-closed rendering).
- The rendered HTML must be fully self-contained: no external ``src``/
  ``href``/``url()`` references, no ``<link>``, no external scripts. This is
  enforced at render time by :func:`assert_self_contained` (the render fails
  instead of shipping a page with external references).
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from cfdb.agentbench.contract import load_contract, verify_frozen
from cfdb.agentbench.scorer import ranked, read_ledger
from cfdb.failures.library import FailureLibrary
from cfdb.failures.taxonomy import FAILURE_MODES
from cfdb.provenance.audit import audit_all
from cfdb.provenance.records import ProvenanceRecord
from cfdb.registry import CaseRegistry
from cfdb.regression.baseline import BaselineStore
from cfdb.regression.gate import evaluate
from cfdb.storage.json_repo import JsonManifestRepository
from cfdb.trust import radar_svg
from cfdb.trust.profile import build_profile
from cfdb.version import __version__

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_NAME = "showcase.html.j2"

EMPTY_STATE: dict[str, str] = {
    "provenance": "尚无 case 出处记录——为 cases/ 补 provenance.yaml 后此处点亮",
    "trust": "尚无 run——cfdb run 后此处点亮",
    "failures": "失败库为空——cfdb failures ingest 后此处点亮（空库也是事实，绝不渲假数据）",
    "regression": "尚无 baseline——cfdb baseline promote 加 --engineer 人签后此处点亮",
    "agentbench": "尚无 scoring contract——cfdb agent-eval init 冻结尺子后此处点亮",
}
"""Per-section empty-state copy (single source, asserted by tests)."""

HONESTY_FOOTER = (
    "诚实边界：本页所有数字取自本仓真实产物文件"
    "（cases/ · runs/ · failures/ · baselines/ · agentbench/），渲染时重读/重算，"
    "绝不接受自报值；SURROGATE / MOCK 级出处如实标注，绝不冒充 REAL 验证；"
    "无数据版块如实留白。"
)
"""Fixed honesty-boundary statement rendered in the page footer."""

VERIFICATION_BOUNDARY = (
    "验证边界（verification boundary）：agent-eval 分数只度量与冻结参考的一致性，"
    "不证明提交真的产自一次 CFD 计算（submission authenticity 不在验证范围内）；"
    "wall_time_sec 为提交方自报值，默认不进排名权重；冻结契约与 baseline 锚"
    "假设仓库写边界在 cfdb 之外被强制（有仓库写权限者可重锚，建议 out-of-band "
    "记录 ruler_id / baseline sha）；ledger.jsonl / library.json 完整性为"
    "进程级 append-only 纪律，非密码学哈希链。"
)
"""Fixed verification-boundary statement rendered in the page footer
(mirrors the README "Verification boundary" section)."""

NO_CANDIDATE_COPY = (
    "尚无 baseline 之外的候选 run（需 status==success、非 dry-run、非 baseline "
    "自身）——门未评估，绝不渲染 run 对自身的必绿 PASS"
)
"""Honest empty-state copy for a baseline with no independent candidate run."""

_HONESTY_BADGE_CLASS: dict[str, str] = {
    "REAL": "h-real",
    "ANALYTIC": "h-mid",
    "MANUFACTURED": "h-mid",
    "PREVIOUS_RUN": "h-warn",
    "SURROGATE": "h-warn",
    "DECLARED-NOT-VERIFIED": "h-risk",
}

_VERDICT_BADGE_CLASS: dict[str, str] = {
    "PASS": "v-pass",
    "REGRESSION": "v-risk",
    "TAMPERED": "v-risk",
    "NO_BASELINE": "v-warn",
    "INVALID_RUN": "v-warn",
}

# External-reference patterns that would break self-containment. The SVG
# namespace declaration (xmlns="http://www.w3.org/2000/svg") is an
# identifier, not a fetch, and deliberately does not match any of these.
_EXTERNAL_REF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"""\b(?:src|href)\s*=\s*["']\s*(?:https?:)?//""", re.IGNORECASE),
    re.compile(r"""url\(\s*["']?\s*(?:https?:)?//""", re.IGNORECASE),
    re.compile(r"@import\b", re.IGNORECASE),
    re.compile(r"<link\b", re.IGNORECASE),
    re.compile(r"<script\b[^>]*\bsrc\s*=", re.IGNORECASE),
)


def assert_self_contained(html: str) -> None:
    """Raise if the HTML contains any external-reference construct.

    This is the render-time gate behind the "zero external http(s) links"
    contract (Architecture v4.0 §7): a page that references any external
    resource is refused instead of written.

    Args:
        html: Rendered HTML document.

    Raises:
        ValueError: If any external-reference pattern matches.
    """
    offenders = [p.pattern for p in _EXTERNAL_REF_PATTERNS if p.search(html) is not None]
    if len(offenders) > 0:
        raise ValueError(
            "showcase HTML is not self-contained; matched external-reference "
            "pattern(s): " + " | ".join(offenders)
        )


def _provenance_rows(records: list[ProvenanceRecord]) -> list[dict[str, Any]]:
    """Convert audited provenance records into template rows.

    Args:
        records: Records from :func:`cfdb.provenance.audit.audit_all`.

    Returns:
        Row dicts with badge class and per-status file counts.
    """
    rows: list[dict[str, Any]] = []
    for rec in records:
        counts = {status: 0 for status in ("ok", "drift", "missing", "unanchored")}
        for status in rec.file_status.values():
            counts[status] += 1
        rows.append(
            {
                "case_id": rec.case_id,
                "reference_type": rec.reference_type,
                "honesty": rec.honesty,
                "badge_class": _HONESTY_BADGE_CLASS.get(rec.honesty, "h-risk"),
                "citation": rec.citation,
                "retrieved": rec.retrieved,
                "transcription_verified": rec.transcription_verified,
                "files_ok": counts["ok"],
                "files_drift": counts["drift"],
                "files_missing": counts["missing"],
                "files_unanchored": counts["unanchored"],
                "files_bad": (counts["drift"] + counts["missing"]) > 0,
                "notes": rec.notes,
            }
        )
    return rows


def _collect_trust(repo_root: Path, honesty_by_case: dict[str, str]) -> dict[str, Any]:
    """Build trust profiles (+ radar SVGs) for every (case, solver) with runs.

    Args:
        repo_root: Repository root.
        honesty_by_case: case_id -> audited honesty level (provenance).

    Returns:
        Dict with ``profiles`` (rendered rows) and ``skipped`` (notes for
        pairs whose case spec could not be loaded — reported, not hidden).
    """
    repo = JsonManifestRepository(repo_root / "runs")
    registry = CaseRegistry(repo_root / "cases")
    pairs = sorted({(m.case_id, m.solver) for m in repo.list_runs()})

    profiles: list[dict[str, Any]] = []
    skipped: list[str] = []
    for case_id, solver in pairs:
        try:
            case = registry.load(case_id)
        except KeyError:
            skipped.append(f"{case_id}/{solver}: case spec unavailable, profile skipped")
            continue
        honesty = honesty_by_case.get(case_id, "DECLARED-NOT-VERIFIED")
        profile = build_profile(case, solver, repo, honesty=honesty)
        profiles.append(
            {
                "case_id": case_id,
                "solver": solver,
                "n_runs": profile.n_runs,
                "honesty": profile.honesty,
                "badge_class": _HONESTY_BADGE_CLASS.get(profile.honesty, "h-risk"),
                "svg": radar_svg.render(profile),
                "notes": profile.notes,
            }
        )
    return {"profiles": profiles, "skipped": skipped}


def _collect_failures(repo_root: Path) -> dict[str, Any]:
    """Read the failure library and bucket records by failure mode.

    Args:
        repo_root: Repository root (library at ``failures/library.json``).

    Returns:
        Dict with ``buckets`` (mode-ordered), ``total`` occurrence count and
        ``error`` (unreadable library reported explicitly, never hidden).
    """
    path = repo_root / "failures" / "library.json"
    if not path.exists():
        return {"buckets": [], "total": 0, "error": None}
    try:
        library = FailureLibrary(path)
    except Exception as exc:  # noqa: BLE001 — fail-closed: report, don't crash the page
        logger.error("failure library unreadable at %s: %s", path, exc)
        return {"buckets": [], "total": 0, "error": f"failures/library.json unreadable: {exc}"}

    buckets: list[dict[str, Any]] = []
    total = 0
    for mode in FAILURE_MODES:
        records = library.records(mode=mode)
        if len(records) == 0:
            continue
        count = sum(r.count for r in records)
        total += count
        buckets.append({"mode": mode, "count": count, "records": [r.model_dump() for r in records]})
    return {"buckets": buckets, "total": total, "error": None}


def _collect_regression(repo_root: Path) -> dict[str, Any]:
    """Load baselines and re-evaluate the gate on each latest candidate run.

    The gate verdict shown on the page is recomputed at render time via
    :func:`cfdb.regression.gate.evaluate` — no stored verdict is trusted.

    Args:
        repo_root: Repository root.

    Returns:
        Dict with ``rows``, the public ``margin`` config and ``error``.
    """
    store = BaselineStore(
        baselines_path=repo_root / "baselines" / "baselines.json",
        runs_root=repo_root / "runs",
    )
    try:
        data = store.load()
    except Exception as exc:  # noqa: BLE001 — fail-closed: report, don't crash the page
        logger.error("baselines unreadable at %s: %s", store.path, exc)
        return {"rows": [], "margin": None, "error": f"baselines.json unreadable: {exc}"}

    repo = JsonManifestRepository(repo_root / "runs")
    rows: list[dict[str, Any]] = []
    for key in sorted(data.baselines):
        entry = data.baselines[key]
        row: dict[str, Any] = {
            "case_id": entry.case_id,
            "solver": entry.solver,
            "baseline_run": entry.run_id,
            "promoted_by": entry.promoted_by,
            "promoted_at": entry.promoted_at,
            "candidate_run": None,
            "verdict": None,
            "verdict_class": "",
            "reasons": [],
        }
        # Honest candidate selection: the gate is only meaningful against a
        # run other than the baseline itself. A run-vs-itself comparison is
        # PASS by construction and must never be rendered. Only executed,
        # successful runs qualify (status == "success" excludes dry_run,
        # failed and timeout runs).
        candidates = [
            m
            for m in repo.list_runs(case_id=entry.case_id)
            if m.solver == entry.solver and m.run_id != entry.run_id and m.status == "success"
        ]
        if len(candidates) == 0:
            row["reasons"] = [NO_CANDIDATE_COPY]
        else:
            candidate_id = candidates[0].run_id  # list_runs is newest-first
            row["candidate_run"] = candidate_id
            try:
                verdict = evaluate(candidate_id, store)
            except Exception as exc:  # noqa: BLE001 — fail-closed per-row degradation
                logger.error("gate evaluation failed for %s: %s", candidate_id, exc)
                row["reasons"] = [f"gate evaluation error (reported, not hidden): {exc}"]
            else:
                row["verdict"] = verdict.verdict
                row["verdict_class"] = _VERDICT_BADGE_CLASS.get(verdict.verdict, "v-warn")
                row["reasons"] = verdict.reasons
        rows.append(row)
    return {"rows": rows, "margin": data.regression_margin.model_dump(), "error": None}


def _collect_agentbench(repo_root: Path) -> dict[str, Any]:
    """Summarize scoring contracts and ledgers under ``agentbench/``.

    The ruler id is the sha256 of the contract.json bytes (first 8 hex
    chars) and the frozen material is re-verified at render time: a drifted
    ruler is displayed as DRIFTED, never as intact.

    Args:
        repo_root: Repository root.

    Returns:
        Dict with one entry per ``agentbench/<case>/contract.json``.
    """
    root = repo_root / "agentbench"
    registry = CaseRegistry(repo_root / "cases")
    contracts: list[dict[str, Any]] = []
    if not root.is_dir():
        return {"contracts": contracts}

    for case_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        contract_path = case_dir / "contract.json"
        if not contract_path.is_file():
            continue
        row: dict[str, Any] = {
            "dir_name": case_dir.name,
            "case_id": None,
            "ruler_id": hashlib.sha256(contract_path.read_bytes()).hexdigest()[:8],
            "frozen_total": None,
            "frozen_status": None,
            "status_class": "",
            "drifted": [],
            "n_events": 0,
            "n_unique_submissions": 0,
            "n_valid": 0,
            "n_invalid": 0,
            "best_score": None,
            "error": None,
            "ledger_error": None,
        }
        try:
            contract = load_contract(contract_path)
        except Exception as exc:  # noqa: BLE001 — fail-closed: report, don't crash the page
            logger.error("contract unreadable at %s: %s", contract_path, exc)
            row["error"] = f"contract.json invalid: {exc}"
            contracts.append(row)
            continue

        row["case_id"] = contract.case_id
        row["frozen_total"] = len(contract.frozen)
        try:
            target_dir = registry.get_case_dir(contract.case_id)
        except KeyError:
            # Fail-closed: an unresolvable case can never display as intact.
            row["frozen_status"] = "UNVERIFIABLE"
            row["status_class"] = "v-warn"
        else:
            drifted = verify_frozen(contract, target_dir)
            row["drifted"] = drifted
            row["frozen_status"] = "DRIFTED" if len(drifted) > 0 else "INTACT"
            row["status_class"] = "v-risk" if len(drifted) > 0 else "v-pass"

        try:
            entries = read_ledger(case_dir / "ledger.jsonl")
        except ValueError as exc:
            row["ledger_error"] = str(exc)
            entries = []
        # Scoring events != unique submissions: re-scoring the same
        # submission appends a new ledger line but is still one submission.
        row["n_events"] = len(entries)
        row["n_unique_submissions"] = len({e.submission_id for e in entries})
        row["n_valid"] = sum(1 for e in entries if e.valid is True)
        # INVALID disclosure (v5.0 §7 backlog, landed R6): invalid samples
        # are ledgered but never ranked — hiding their volume would make a
        # leaderboard of survivors look like a leaderboard of attempts.
        row["n_invalid"] = sum(1 for e in entries if e.valid is not True)
        # Like-with-like only: rows scored under an older/unknown ruler
        # never drive best_score (Codex R1 P2 — stale-ruler leaderboard).
        best = ranked(entries, ruler_id=row["ruler_id"])
        row["n_stale_ruler"] = sum(1 for e in entries if e.ruler_id != row["ruler_id"])
        row["best_score"] = best[0].score if len(best) > 0 else None
        contracts.append(row)
    return {"contracts": contracts}


def render_showcase(repo_root: Path, out: Path) -> Path:
    """Render the self-contained showcase HTML from real repository artifacts.

    Args:
        repo_root: Repository root containing ``cases/`` and, when present,
            ``runs/``, ``failures/``, ``baselines/`` and ``agentbench/``.
        out: Output HTML file path (parent directories are created).

    Returns:
        The written output path.

    Raises:
        ValueError: If the rendered HTML fails the self-containment gate
            (nothing is written in that case).
    """
    records = audit_all(repo_root / "cases")
    honesty_by_case = {r.case_id: r.honesty for r in records}

    context: dict[str, Any] = {
        "version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo_root": str(repo_root),
        "provenance": {"records": _provenance_rows(records)},
        "trust": _collect_trust(repo_root, honesty_by_case),
        "failures": _collect_failures(repo_root),
        "regression": _collect_regression(repo_root),
        "agentbench": _collect_agentbench(repo_root),
        "empty": EMPTY_STATE,
        "footer": HONESTY_FOOTER,
        "boundary": VERIFICATION_BOUNDARY,
    }

    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    html = env.get_template(_TEMPLATE_NAME).render(**context)
    assert_self_contained(html)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    logger.info("showcase HTML written to %s", out)
    return out
