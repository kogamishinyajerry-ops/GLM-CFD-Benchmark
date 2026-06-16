# CFD-Benchmark 系统架构设计 v2.2 — P2-c 增量（NACA0012 多攻角 + 多 solver 对比 + cfdb compare）

| 项 | 内容 |
|---|---|
| 版本 | v2.2（增量） |
| 日期 | 2026-06-17 |
| 作者 | 高见远（Gao）· 架构师 |
| 范围 | **P2-c（F NACA0012 α=0/5/10/15° 多攻角扫描 + G 多 solver 对比报告 + H cfdb compare 命令）** |
| 上游依赖 | `docs/prd/PRD-v2.2-P2c.md`（P2-c PRD）、`docs/architecture/Architecture-v2.1-P2b.md`（P2-b 基线） |
| 基线 | P0+P1-a+P1-b+P2-a+P2-b（commit `41fbeaa`，**335 测试 / 89.74% cov**） |

---

## 1. 概述

P2-c 在 P2-b 基线之上实现 3 项增量功能：①**NACA0012 多攻角扫描**——4 个攻角（α=0/5/10/15°）× 2 solver = 8 子 run，生成 Cl/Cd 极曲线对比 Ladson 1988；②**多 solver 对比报告**——HTML report 加 multi-solver section（Cp 叠加 SVG + Cl/Cd 表格 + 极曲线 SVG）；③**cfdb compare 命令**——任意两 run 的 QoI diff 表 + 对比 SVG。

本增量严格遵守 5 条铁律：

| 铁律 | 说明 |
|---|---|
| #1 | 不破坏 P0+P1-a+P1-b+P2-a+P2-b 的 **335** 个测试 |
| #2 | Schema **完全不改**（P2-c 不动 CaseSpec / RunManifest；4 攻角用 4 独立 case 实现） |
| #3 | `SolverAdapter` / `ExecutionBackend` / `ResultRepository` Protocol 不动 |
| #4 | JSON 存储与 local backend 保持兼容 |
| #5 | 真实 solver / 真实 Docker 测试不进 CI |

**转总已确认决策**（见 PRD-v2.2 §3）：主范围=NACA0012 多攻角扫描；辅助项=多 solver 对比报告 + cfdb compare；4 攻角用 4 独立 case（不引入 sweep 字段，守住铁律 #2）。

---

## 2. 关键设计决策

### 2.1 为什么用 4 独立 case 而非 sweep 字段？

**方案对比**：

| 方案 | 优点 | 缺点 |
|---|---|---|
| A. 4 独立 case（采纳） | case.yaml 结构零改动；Runner 零改动；Pydantic 验证不变；测试零破坏 | 4 个 case.yaml 有重复（90% 相同） |
| B. sweep 字段 | 单 case.yaml 描述所有攻角 | CaseSpec.conditions.alpha_deg 从标量改 list；破坏所有现有测试；铁律 #2 违反 |

**方案 A 的复用策略**：4 个 case 共用：
- 同一 `geometry/naca0012.stl`（P2-b 已生成）
- 同一 `gen_geometry.py`
- 同一 OpenFOAM 模板（controlDict.naca.j2 / snappyHexMeshDict.j2 / blockMeshDict.naca.j2）
- 同一 SU2 CFG 模板（naca0012.cfg.j2）
- 仅 α、参考 Cp 路径、Cli 参数（α_deg）不同

每个 case.yaml ~50 行，4 个共 ~200 行，可接受。

### 2.2 为什么 Cl/Cd 提取走 forces object 而非 probes？

P2-b 已在 controlDict.naca.j2 配置了 `forces` function object（patches=airfoil, liftDir/dragDir 已定义）。Cl/Cd 从 forces 输出文件提取，无需新增 probes 配置。SU2 的 surface_flow.csv 已经包含 Pressure_Coefficient 列，可以从压力积分反推 Cl/Cd（或直接读 SU2 history 输出的 CL/CD）。

### 2.3 为什么 multi-solver report 是新命令而非扩展 `cfdb report`？

`cfdb report --run-dir <path>` 当前语义是"给单个 run 生成 HTML 报告"。multi-solver report 需要多个 run 作为输入，语义不同。新建 `cfdb report-sweep` 保持命令清晰。

### 2.4 cfdb compare 跨 case 怎么办？

两 run 来自不同 case → QoI 集合可能不同 → 容差列跳过（容差是 case-specific），只显示绝对/相对差。同 case 两 run → 显示容差判断列。

---

## 3. NACA0012 多攻角 case 设计（F）

### 3.1 CaseSpec 模板（α=5° 示例）

```yaml
# cases/validation/naca0012_a5/case.yaml
id: naca0012_a5
name: NACA0012 Airfoil α=5° (Ladson 1988 Validation)
category: validation
description: >
  NACA0012 at α=5°, Re=6e6, M=0.3, RANS-SA. Part of alpha sweep
  (α=0/5/10/15°) for polar curve validation against Ladson 1988.
physics:
  flow: rans
  turbulence: rans_sa
  dimensionality: 2d
  steady: true
conditions:
  reynolds: 6.0e6
  mach: 0.3
  alpha_deg: 5.0    # ← 唯一与 naca0012_a0 不同的字段（+ 参考数据路径）
geometry:
  type: external
  source: ../naca0012/geometry/naca0012.dat   # ← 复用 P2-b 几何
mesh:
  family: unstructured_hex
  levels: ["coarse"]
  target_y_plus: 1.0
solvers:
  - name: openfoam
    # ... 同 naca0012_a0，parameters.alpha_deg: 5.0
  - name: su2
    # ... 同 naca0012_a0，parameters.aoa_deg: 5.0
outputs:
  fields: ["U", "p", "nuTilda"]
  curves: ["cp_distribution"]
  qoi: ["cl", "cd"]   # P2-c 新增 cl/cd 提取目标
reference:
  type: experimental
  files:
    cp_curve: reference/ladson1988_a5.csv   # α=5° 的 Ladson Cp 数据
  qoi_values:
    cl: 0.456     # Ladson 1988 α=5° Re=6e6
    cd: 0.0095
metrics:
  qoi_relative_tolerance:
    cl: 0.10
    cd: 0.10
budget:
  max_runtime_sec: 600
  max_cells: 100000
```

α=10° / α=15° 类似，仅 `alpha_deg` + `reference` 不同。

### 3.2 Ladson 1988 极曲线参考数据

```csv
# cases/validation/naca0012/reference/ladson_polar.csv
# Ladson 1988 NASA TM-4074, Re=6e6, M=0.3 (low Mach)
# alpha_deg, Cl, Cd
0.0, 0.000, 0.0086
5.0, 0.456, 0.0095
10.0, 0.862, 0.0125
15.0, 1.096, 0.0185
```

### 3.3 各攻角 Ladson Cp 分布（简化版，5 点）

每个攻角的 `reference/ladson1988_a<X>.csv`：x/c vs Cp。α=0° 已有（P2-b）；α=5/10/15° 用 Ladson 报告的 Cp 分布特征点（前驻点 Cp=1.0，吸力峰随 α 增高，后缘恢复）。

---

## 4. Cl/Cd 提取器设计（F）

### 4.1 OpenFOAM forces object 输出格式

OpenFOAM forces function object 输出在 `postProcessing/forces/<time>/forces.dat`：

```
# Forces
# time forces (Fx Fy Fz) moments (Mx My Mz)
0.000 (0.00123 -0.00045 0) (0 0 0.00001)
1.000 (0.00125 -0.00050 0) (0 0 0.00001)
...
```

Cl = Fy / (0.5 * rho * U_inf^2 * A_ref)，Cd = Fx / (0.5 * rho * U_inf^2 * A_ref)。

### 4.2 提取函数

```python
# post/qoi_extractor.py 新增

def extract_cl_cd_openfoam(
    forces_dat: Path,
    rho: float = 1.225,
    u_inf: float = 100.0,
    a_ref: float = 1.0,
) -> tuple[float, float] | None:
    """Extract final Cl/Cd from OpenFOAM forces.dat.

    forces.dat format:
        # time forces (Fx Fy Fz) moments (Mx My Mz)
        0.000 (0.00123 -0.00045 0) (0 0 0.00001)
        ...

    Cl = Fy / q_inf / A_ref; Cd = Fx / q_inf / A_ref
    where q_inf = 0.5 * rho * U_inf^2.

    Args:
        forces_dat: Path to forces.dat.
        rho: Freestream density (kg/m³).
        u_inf: Freestream velocity magnitude (m/s).
        a_ref: Reference area (m²).

    Returns:
        Tuple (cl, cd) from the last time step, or None if parsing fails.
    """
    ...


def extract_cl_cd_su2(
    surface_flow_csv: Path,
    rho: float = 1.225,
    u_inf: float = 100.0,
    a_ref: float = 1.0,
) -> tuple[float, float] | None:
    """Extract Cl/Cd from SU2 surface_flow.csv by pressure integration.

    Alternative: read SU2 history output (CL/CD columns). CSV approach is
    more robust since surface_flow.csv is always written.

    Args:
        surface_flow_csv: Path to surface_flow.csv.
        rho, u_inf, a_ref: Same as extract_cl_cd_openfoam.

    Returns:
        Tuple (cl, cd), or None if parsing fails.
    """
    ...
```

---

## 5. 极曲线 SVG 生成器（F + G）

### 5.1 设计

```python
# reporting/svg_polar.py
"""Polar curve SVG generator (Cl-α and Cd-α plots).

Pure Python, zero dependencies (only `math`). Reuses P2-a svg_residuals
aesthetic: Okabe-Ito 8-color palette, viewBox, axis labels.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field


@dataclass
class PolarPoint:
    alpha_deg: float
    cl: float
    cd: float


@dataclass
class PolarCurve:
    solver: str
    points: list[PolarPoint]
    color: str | None = None  # auto-assigned from Okabe-Ito if None


def render_polar_svg(
    curves: list[PolarCurve],
    reference: PolarCurve | None = None,
    title: str = "Lift/Drag Polar — NACA0012",
) -> str:
    """Render Cl-α + Cd-α dual subplot as a single SVG (viewBox 680x800).

    Args:
        curves: One PolarCurve per solver (OpenFOAM / SU2).
        reference: Optional Ladson 1988 reference curve (dashed black).

    Returns:
        SVG string.
    """
    ...
```

### 5.2 视觉规范

- viewBox: `0 0 680 800`（上 subplot Cl-α 380 高 + 下 subplot Cd-α 380 高 + 40 间距）
- Okabe-Ito 8 色（与 P2-a svg_residuals 一致）：solver 曲线实心圆点 + 线，reference 虚线
- X 轴：α（度），范围 [0, 15]，5° 一格
- Y 轴上：Cl，范围自动扩展
- Y 轴下：Cd，范围自动扩展
- 图例：右上角
- 空数据返回占位 SVG（与 P2-a 一致）

---

## 6. 多 solver 对比 SVG（G）

### 6.1 Cp 叠加图

```python
# reporting/svg_compare.py
"""Multi-solver comparison SVG generators.

- render_cp_comparison_svg(solver_data: dict[str, tuple[list[float], list[float]]],
                            reference_data: tuple[list[float], list[float]] | None) -> str
    Cp vs x/c 叠加图，多 solver 曲线 + Ladson 参考点。

- render_residual_comparison_svg(solver_data: dict[str, dict[str, list[float]]]) -> str
    残差历史叠加图（复用 P2-a render_residual_svg 的配色逻辑）。
"""
```

### 6.2 HTML multi-solver report

```python
# reporting/html.py 新增
def generate_multi_solver_report(
    manifests: list[RunManifest],
    metrics_list: list[MetricsResult],
    output_dir: Path,
    cp_svg: str | None = None,
    polar_svg: str | None = None,
) -> Path:
    """Generate HTML report comparing multiple runs of the same case.

    Sections:
    1. Run summary table (run_id, solver, α, status, wall_time)
    2. Cp distribution comparison (cp_svg if provided)
    3. Cl/Cd table (solver × α, with Ladson error coloring)
    4. Polar curves (polar_svg if provided)

    Args:
        manifests: List of RunManifest from the same case (or related cases).
        metrics_list: Corresponding metrics for each manifest.
        output_dir: Where to write the HTML file.
        cp_svg: Pre-rendered Cp comparison SVG (optional).
        polar_svg: Pre-rendered polar SVG (optional).

    Returns:
        Path to the generated HTML file.
    """
    ...
```

---

## 7. cfdb compare 命令设计（H）

### 7.1 CLI 接口

```bash
cfdb compare <run_id1> <run_id2> \
    [--runs-dir PATH] \
    [--storage {json,sqlite}] \
    [--format {html,text}] \
    [--out PATH]
```

### 7.2 输出

**HTML 格式**（默认）：
- QoI diff 表格（QoI 名 / run1 值 / run2 值 / 绝对差 / 相对差 / 容差判断）
- 若两 run 都有 residuals_history → 残差叠加 SVG
- 若两 run 都有 Cp 数据 → Cp 叠加 SVG

**Text 格式**（`--format text`）：
```
Comparing: <run_id1> vs <run_id2>
============================================================
QoI                run1         run2       abs_diff   rel_diff   tolerance
cl                 0.456        0.452      +0.004     +0.88%     PASS (10.0%)
cd                 0.0095       0.0098     -0.0003    -3.16%     PASS (10.0%)
centerline_umax    N/A          0.373      N/A        N/A        N/A
============================================================
Overall: 2/2 QoIs within tolerance
```

### 7.3 实现模块

```python
# reporting/compare.py 新增
@dataclass
class QoIComparison:
    name: str
    value1: float | None
    value2: float | None
    abs_diff: float | None
    rel_diff_pct: float | None
    within_tolerance: bool | None  # None if cross-case or missing


def compare_runs(
    manifest1: RunManifest,
    metrics1: MetricsResult,
    manifest2: RunManifest,
    metrics2: MetricsResult,
    case: CaseSpec | None = None,  # for tolerance lookup
) -> list[QoIComparison]:
    """Compare QoIs between two runs.

    Args:
        manifest1, manifest2: Two runs to compare.
        metrics1, metrics2: Their computed metrics.
        case: Optional CaseSpec for tolerance lookup. If None or if runs are
            from different cases, tolerance column is skipped.

    Returns:
        List of QoIComparison, one per QoI in the union of both runs.
    """
    ...


def render_compare_html(
    manifest1, manifest2,
    comparisons: list[QoIComparison],
    residual_svg: str | None = None,
    cp_svg: str | None = None,
) -> str:
    """Render comparison as HTML string."""
    ...


def render_compare_text(
    manifest1, manifest2,
    comparisons: list[QoIComparison],
) -> str:
    """Render comparison as plain text table."""
    ...
```

---

## 8. cfdb report-sweep 命令设计（G）

```bash
cfdb report-sweep \
    --case-id naca0012 \
    --runs-dir runs/ \
    [--solvers openfoam,su2] \
    [--out report.html]
```

**流程**：
1. 扫描 `runs/` 找所有 `run_id` 含 `naca0012` 的子目录
2. 加载每个 manifest + metrics（用 `--storage` 决定走 JSON 还是 SQLite repo）
3. 按 solver 分组，每组装成一条 PolarCurve
4. 渲染 Cp 叠加 SVG + 极曲线 SVG
5. 生成 multi-solver HTML report

---

## 9. 文件清单（P2-c 新增/修改）

### 9.1 新增文件（~18 个）

| 文件 | 用途 |
|---|---|
| `cases/validation/naca0012_a5/case.yaml` | α=5° case |
| `cases/validation/naca0012_a5/reference/ladson1988_a5.csv` | α=5° Ladson Cp 参考 |
| `cases/validation/naca0012_a10/case.yaml` | α=10° case |
| `cases/validation/naca0012_a10/reference/ladson1988_a10.csv` | α=10° Ladson Cp 参考 |
| `cases/validation/naca0012_a15/case.yaml` | α=15° case |
| `cases/validation/naca0012_a15/reference/ladson1988_a15.csv` | α=15° Ladson Cp 参考 |
| `cases/validation/naca0012/reference/ladson_polar.csv` | Ladson 极曲线（Cl/Cd vs α） |
| `src/cfdb/reporting/svg_polar.py` | 极曲线 SVG 生成器 |
| `src/cfdb/reporting/svg_compare.py` | 多 solver Cp / 残差对比 SVG |
| `src/cfdb/reporting/compare.py` | compare_runs 逻辑 + QoIComparison dataclass |
| `tests/test_cl_cd_extractor.py` | Cl/Cd 提取测试 |
| `tests/test_svg_polar.py` | 极曲线 SVG 测试 |
| `tests/test_svg_compare.py` | 对比 SVG 测试 |
| `tests/test_compare_runs.py` | cfdb compare 逻辑测试 |
| `tests/test_multi_solver_report.py` | multi-solver HTML report 测试 |
| `tests/test_cli_compare.py` | cfdb compare CLI 测试 |
| `tests/test_cli_report_sweep.py` | cfdb report-sweep CLI 测试 |

### 9.2 修改文件（~3 个）

| 文件 | 改动 |
|---|---|
| `src/cfdb/post/qoi_extractor.py` | 新增 `extract_cl_cd_openfoam` + `extract_cl_cd_su2` |
| `src/cfdb/reporting/html.py` | 新增 `generate_multi_solver_report` |
| `src/cfdb/cli.py` | 新增 `compare` + `report-sweep` 命令 |

### 9.3 预估测试数

| 测试文件 | 数量 |
|---|---|
| test_cl_cd_extractor.py | ~8（OF forces 解析 + SU2 CSV 积分 + 错误路径） |
| test_svg_polar.py | ~6（空数据 / 单曲线 / 多曲线 / 参考 / viewBox / 颜色） |
| test_svg_compare.py | ~5（Cp 叠加 / 残差叠加 / 空 / 多 solver / 参考点） |
| test_compare_runs.py | ~6（同 case / 跨 case / 缺 QoI / 容差 PASS/FAIL） |
| test_multi_solver_report.py | ~4（HTML 结构 / 表格 / SVG 嵌入 / 空 manifest） |
| test_cli_compare.py | ~4（HTML 输出 / text 输出 / run 不存在 / 跨 case） |
| test_cli_report_sweep.py | ~3（扫描 run / 渲染 / 空结果） |

总新增：~36 测试。**总测试数预估：335 + 36 ≈ 371**（PRD DoD 要求 ≥ 370）。

---

## 10. 验收铁律映射（QA 独立验证清单）

| # | 铁律 | QA 验证方法 |
|---|---|---|
| #1 | 不破坏 **335** 个测试 | `pytest` 全跑，确认 335 个全过 |
| #2 | Schema **完全不改** | grep `class RunManifest` / `class CaseSpec` / `class ConditionsSpec`，确认无字段修改 |
| #3 | Protocol 不动 | grep 三个 Protocol 定义，确认未改 |
| #4 | JSON/local 兼容 | grep CLI `--storage` 默认 json、`--backend` 默认 local |
| #5 | 真实 solver/Docker 测试分离 | grep pyproject.toml addopts `-m 'not real_solver and not real_docker'` |

---

## 11. P2-c 实施批次

| 批次 | 任务 | 文件数 |
|---|---|---|
| T1 | Cl/Cd 提取器 + 测试 | ~2 |
| T2 | 3 个新攻角 case + Ladson 极曲线参考 | ~7 |
| T3 | svg_polar + svg_compare + 测试 | ~4 |
| T4 | compare.py + multi_solver_report + 测试 | ~4 |
| T5 | CLI compare + report-sweep + 测试 | ~3 |
| T6 | QA 独立验证 + commit | 验证 |

---

## 12. 共享知识（跨文件约定）

### 12.1 Ladson 1988 数据来源

- NASA TM-4074（公开），Table 1（极曲线）+ 各 α 的 Cp 分布
- Re=6e6, M=0.3（低马赫数，可压修正可忽略）
- α=0/5/10/15° 四点足够画极曲线趋势；不做插值

### 12.2 OpenFOAM forces object 输出路径

- `postProcessing/forces/<time>/forces.dat`（OpenFOAM v2312/v2406）
- `postProcessing/forces/<time>/force.dat`（Foundation v11/v12，单数）
- 提取器要兼容两种命名（同 P1-b 残差解析策略）

### 12.3 SU2 surface_flow.csv 列名

- v8.0+: `"Point_ID","x","y","Pressure","Pressure_Coefficient","Skin_Friction_Coefficient"`
- 早期版本可能只有 Pressure，需反推 Cp = (P - P_inf) / q_inf

### 12.4 SVG 配色（Okabe-Ito 8 色，与 P2-a 一致）

```python
OKABE_ITO = [
    "#0072B2",  # blue
    "#D55E00",  # vermillion
    "#009E73",  # bluish green
    "#CC79A7",  # reddish purple
    "#E69F00",  # orange
    "#56B4E9",  # sky blue
    "#F0E442",  # yellow
    "#000000",  # black (reference)
]
```

solver1=blue, solver2=vermillion, reference=black dashed。

---

## 13. 待明确事项

| # | 事项 | 默认建议 |
|---|---|---|
| 1 | Ladson α=5/10/15° 的 Cp 数据精度 | 录入 5 个特征点（x/c=0/0.25/0.5/0.75/1.0），不录完整曲线 |
| 2 | cfdb compare 是否支持 >2 run 对比 | 第一版只支持 2 run；多 run 用 report-sweep |
| 3 | multi-solver report 是否嵌入残差 SVG | 是，从每个 manifest.residuals_history 提取 |
| 4 | α=15° 是否可能失速导致 SU2 不收敛 | 文档说明，QA 真实测试时观察；不收敛则 manifest.status=failed，polar 自动跳过该点 |

---

*文档结束。架构师高见远 2026-06-17 完成。转总已确认 2 核心决策（PRD-v2.2 §3），架构据此设计。工程师据 §11 实施 6 批次（T1-T6），QA 据 §10 验收。*
