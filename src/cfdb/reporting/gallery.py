"""Self-contained "card gallery" HTML: one card per benchmark case, explaining
what capability it probes, the expected result, and the pass/fail criteria.

Companion to the account-summary showcase (:mod:`cfdb.reporting.showcase`).
Where the showcase answers *"what is the current scoring state"*, the gallery
answers *"what tests exist and what does each one check"*.

- Card PROSE (``capability`` / ``expected`` / ``criteria`` / ``what_makes_it_bite``)
  is authored per case in ``<case>/card.yaml`` — a case-root metadata file,
  OUTSIDE the frozen ``reference/``/``visible/`` trees, so writing it never
  drifts a ruler (same discipline as ``provenance.yaml`` / ``admission.md``).
- Structural FACTS (domain, validity gates, honesty level, frozen status) are
  read and recomputed from real artifacts at render time — never self-reported.
- Smoke/mock fixtures (``category == "smoke"``) are internal harness
  scaffolding, not capability tests: they are excluded from the gallery and
  disclosed by count in the footer (never silently hidden).
- The rendered HTML is fully self-contained (enforced by
  :func:`cfdb.reporting.showcase.assert_self_contained`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader

from cfdb.agentbench.contract import load_contract, verify_frozen
from cfdb.provenance.audit import audit_all
from cfdb.registry import CaseRegistry
from cfdb.reporting.showcase import _HONESTY_BADGE_CLASS, assert_self_contained
from cfdb.version import __version__

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_NAME = "gallery.html.j2"

CARD_YAML = "card.yaml"
"""Per-case-root card prose file (outside the frozen trees)."""

DOMAIN_ORDER = ("coding", "agentic", "cfd")

DOMAIN_LABEL: dict[str, str] = {
    "coding": "Coding · 算法实现",
    "agentic": "Agentic · 状态型日常任务",
    "cfd": "CFD · 求解器验证 (V&V)",
}

DOMAIN_BLURB: dict[str, str] = {
    "coding": "给定 buggy stub 与隐藏测试，判 AI 能否把它改对；带 IO oracle 的还要在从未见过的 "
    "held-out 输入上真算对（受信重执行）——两信号 AND，只会写通过报告不算数。",
    "agentic": "读一组文件、产出一个产物（summary.json / config.json / 整理后的目录），"
    "由仅用标准库的 checker 在 cfdb 进程内机械核对；checker 自身出错一律 "
    "fail-closed（判「无法判定」，绝不算过）。",
    "cfd": "真跑 CFD 求解器，把 QoI（阻力系数 / 中心线速度 / cp 分布）对已发表的实验或解析参考，"
    "按相对容差判定；欠分辨的网格如实 FAIL，绝不为「能过」放松尺子。",
}

# Domain-specific judging note for cases WITHOUT a frozen agent-eval contract
# (CFD run-pipeline cases; agentic cases whose checker exists but is not yet
# `agent-eval init`-frozen). Shown in place of the gate tags.
_GATE_NOTE_BY_DOMAIN: dict[str, str] = {
    "cfd": "QoI 相对容差 / 预算门（cfdb run 流水线 · MetricsEngine 逐门重算，"
    "非 agent-eval 冻结契约）",
    "agentic": "checker_ok（agentic 域默认门 · reference/checker.py 真实执行，"
    "但本 case 尚未 init 冻结为签名 contract.json）",
}


def _collect_gallery(repo_root: Path) -> dict[str, Any]:
    """Assemble gallery cards from real artifacts + per-case card.yaml prose.

    Args:
        repo_root: Repository root (``cases/`` + optional ``agentbench/``).

    Returns:
        Dict with ``groups`` (domain-ordered card groups), ``total`` card
        count, ``n_smoke`` excluded-fixture count and ``missing_card`` (cases
        without readable card.yaml — reported, never silently dropped).
    """
    registry = CaseRegistry(repo_root / "cases")
    honesty_by_case = {r.case_id: r.honesty for r in audit_all(repo_root / "cases")}
    agentbench = repo_root / "agentbench"

    buckets: dict[str, list[dict[str, Any]]] = {}
    n_smoke = 0
    missing_card: list[str] = []

    for case in registry.list_all():
        if case.category == "smoke":
            # Harness self-test fixtures, not capability tests — disclosed by
            # count in the footer, never presented as a benchmark test.
            n_smoke += 1
            continue
        case_dir = registry.get_case_dir(case.id)
        card_path = case_dir / CARD_YAML
        if not card_path.is_file():
            missing_card.append(case.id)
            continue
        try:
            prose = yaml.safe_load(card_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:  # fail-closed: report, don't crash
            logger.error("card.yaml unreadable at %s: %s", card_path, exc)
            missing_card.append(f"{case.id} (card.yaml invalid)")
            continue

        gates: list[str] | None = None
        frozen_status: str | None = None
        contract_path = agentbench / case.id / "contract.json"
        if contract_path.is_file():
            try:
                contract = load_contract(contract_path)
                gates = list(contract.validity_gates)
                # Recomputed at render time: a drifted ruler is shown as such.
                frozen_status = (
                    "DRIFTED" if len(verify_frozen(contract, case_dir)) > 0 else "INTACT"
                )
            except Exception as exc:  # noqa: BLE001 — fail-closed per-card degradation
                logger.error("contract unreadable for %s: %s", case.id, exc)

        # No frozen agent-eval contract → the card must still say HOW the case
        # is judged, and the answer is domain-specific (a CFD run-pipeline case
        # and an agentic case not-yet-frozen are judged very differently). A
        # single "CFD tolerance" fallback would mislabel agentic cases.
        gate_note = (
            None
            if gates is not None
            else _GATE_NOTE_BY_DOMAIN.get(case.domain, "run 流水线校验（无 agent-eval 冻结契约）")
        )

        honesty = honesty_by_case.get(case.id, "DECLARED-NOT-VERIFIED")
        card = {
            "case_id": case.id,
            "domain": case.domain,
            "title": prose.get("title") or case.name,
            "capability": prose.get("capability", ""),
            "expected": prose.get("expected", ""),
            "criteria": prose.get("criteria", ""),
            "what_makes_it_bite": prose.get("what_makes_it_bite") or None,
            "gates": gates,
            "gate_note": gate_note,
            "has_io_oracle": bool(gates) and "io_oracle_pass" in (gates or []),
            "honesty": honesty,
            "honesty_class": _HONESTY_BADGE_CLASS.get(honesty, "h-risk"),
            "frozen_status": frozen_status,
        }
        buckets.setdefault(case.domain, []).append(card)

    groups: list[dict[str, Any]] = []
    ordered_domains = list(DOMAIN_ORDER) + sorted(d for d in buckets if d not in DOMAIN_ORDER)
    for domain in ordered_domains:
        cards = sorted(buckets.get(domain, []), key=lambda c: c["case_id"])
        if len(cards) == 0:
            continue
        groups.append(
            {
                "domain": domain,
                "label": DOMAIN_LABEL.get(domain, domain),
                "blurb": DOMAIN_BLURB.get(domain, ""),
                "cards": cards,
            }
        )

    total = sum(len(g["cards"]) for g in groups)
    return {
        "groups": groups,
        "total": total,
        "n_smoke": n_smoke,
        "missing_card": sorted(missing_card),
    }


def render_gallery(repo_root: Path, out: Path) -> Path:
    """Render the self-contained benchmark card-gallery HTML.

    Args:
        repo_root: Repository root containing ``cases/``.
        out: Output HTML path (parent directories are created).

    Returns:
        The written output path.

    Raises:
        ValueError: If the rendered HTML fails the self-containment gate
            (nothing is written in that case).
    """
    context: dict[str, Any] = {
        "version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo_root": str(repo_root),
        "gallery": _collect_gallery(repo_root),
    }
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    html = env.get_template(_TEMPLATE_NAME).render(**context)
    assert_self_contained(html)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    logger.info("gallery HTML written to %s", out)
    return out
