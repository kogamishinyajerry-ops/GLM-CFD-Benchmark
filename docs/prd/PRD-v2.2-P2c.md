# CFD-Benchmark PRD v2.2 — P2-c（NACA0012 多攻角扫描 + 多 solver 对比 + cfdb compare）

## 1. 文档信息

| 项 | 内容 |
|---|---|
| 版本 | v2.2（P2-c 增量） |
| 日期 | 2026-06-17 |
| 作者 | 许清楚（Xu）· 产品经理 |
| 状态 | **Confirmed**（转总已拍板 2 决策，见 §3） |
| 基于 | PRD-v2.1-P2b.md（P2-b 基线）、Architecture-v2.1-P2b.md（P2-b 架构） |
| 基线 | P0+P1-a+P1-b+P2-a+P2-b（commit `41fbeaa`，**335 测试 / 89.74% cov**） |
| 工作目录 | `D:\GLM-CFD-Benchmark` |

---

## 2. P2-c 战略定位

> **把单攻角的 NACA0012 跑通验证流程，扩展为攻角扫描 + 多 solver 对比，让 CFD benchmark 平台的「对比」核心价值真正兑现。**

P2-b 已交付"单 solver 单攻角"的完整闭环（OpenFOAM/SU2 各跑 α=0° 对比 Ladson）。P2-c 三项互相协同：

```
NACA0012 多攻角 ─┐
                 ├─→ 4 攻角 × 2 solver = 8 子 run
                 ├─→ Cl/Cd 极曲线对比 Ladson 1988
                 │
多 solver 对比报告 ┤
                 │   HTML report 加 multi-solver section
                 │   Cp 叠加图 + Cl/Cd 表格 + 收敛历史
                 │
cfdb compare 命令 ─┘
                     任意两 run 的 QoI diff + 对比 SVG
                     通用工具，不限 NACA0012 场景
```

| 方向 | 解决的痛点 |
|---|---|
| **F. NACA0012 多攻角扫描** | P2-b 只跑了 α=0°，单点对比说服力不足；攻角扫描才能展现极曲线趋势 |
| **G. 多 solver 对比报告** | P2-a HTML report 只支持单 run 内嵌 SVG；缺「同 case 多 solver」横向对比 |
| **H. cfdb compare 命令** | 没有跨 run 对比工具；用户需手工 grep manifest 算 diff |

---

## 3. 转总拍板的核心决策（2 项，全部确认）

### 3.1 ⭐⭐⭐ 主范围 → **NACA0012 多攻角扫描**

在 P2-b 单攻角 α=0° 基础上扩展到 **α=0/5/10/15°** 四个攻角，每个攻角 OpenFOAM + SU2 各跑一次，生成 **Cl/Cd 极曲线** 对比 Ladson 1988 实验数据。

**理由**：①P2-b 已建立 α=0° 完整流程，扩到 4 攻角是参数化拓展不是新基础设施；②极曲线（Cl-α / Cd-α）是外流验证的金标准，比单点 Cp 对比说服力强一个数量级；③8 个子 run 形成有意义的统计样本，体现"benchmark 平台"价值。

**不选 Web Dashboard / ML surrogate 的原因**：前者需要前后端工程量、后者依赖 2GB AirfRANS 数据集，两者都是新基础设施而非 P2-b 的自然扩展。推 P2-d。

### 3.2 ⭐⭐ 辅助项 → **多 solver 对比报告 + cfdb compare 命令**

两个辅助项一起做，形成"产出 → 展示 → 对比"闭环：

- **多 solver 对比报告**：让 8 个子 run 的成果能可视化展示（HTML report 加 multi-solver section，Cp 叠加 SVG + Cl/Cd 表格 + 极曲线 SVG）。
- **cfdb compare**：通用跨 run 对比工具，不限于 NACA0012 场景，未来 Web Dashboard / ML surrogate 都能复用。

**不加 Docker 自建镜像的原因**：P2-b 已能用官方镜像跑通，自建镜像（含 gmsh/salome）维护成本高，不阻塞 P2-c 主线。

---

## 4. 范围明细

### 4.1 F. NACA0012 多攻角扫描

| 子项 | 说明 |
|---|---|
| 3 个新 case | `cases/validation/naca0012_a5/`、`naca0012_a10/`、`naca0012_a15/`，各含 case.yaml + Ladson 参考 Cp |
| Ladson 极曲线参考 | `cases/validation/naca0012/reference/ladson_polar.csv`，Cl/Cd vs α（α=0/5/10/15°，Re=6e6） |
| QoI 提取扩展 | `qoi_extractor.py` 加 `extract_cl_cd_openfoam(forces_dat)` + `extract_cl_cd_su2(surface_flow.csv)` |
| 极曲线 SVG 生成器 | `reporting/svg_polar.py`：Cl-α / Cd-α 双图（参考 P2-a 残差 SVG 模式，纯 Python 零依赖） |
| 网格复用 | 4 个攻角共用同一 NACA0012 几何 + 同一背景网格（P2-b 已生成的 `geometry/naca0012.stl` + blockMesh） |
| 边界条件差异 | 远场速度向量按攻角旋转：`U_inf = (cos α, sin α, 0) * magUInf`（OpenFOAM）/ `AOA=<deg>`（SU2 CFG） |

**为什么不用 sweep manifest 字段，而是建 4 个独立 case？**

架构师判断：sweep 字段会让 CaseSpec 复杂化（conditions.alpha_deg 从标量改 list，影响 Pydantic 验证 + 所有现有测试）。4 个独立 case 更简单：每个 case 就是 P2-b 已有的单攻角结构，只是 α 不同。Runner 无需改造，复用 P2-b 完整 pipeline。**铁律 #2（schema 只加 Optional）守住**。

### 4.2 G. 多 solver 对比报告

| 子项 | 说明 |
|---|---|
| HTML report multi-solver section | `reporting/html.py` 加 `generate_multi_solver_report(runs: list[RunManifest], metrics_list, ...)` |
| Cp 叠加 SVG | `reporting/svg_compare.py`：多 solver 的 Cp-x/c 曲线叠加在同一坐标系，Okabe-Ito 8 色，含 Ladson 参考点 |
| Cl/Cd 表格 | HTML 表格：solver × α，每格 Cl/Cd 数值 + 与 Ladson 的相对误差（红/绿配色） |
| 极曲线 SVG | 同 §4.1 的 svg_polar，但叠加多 solver 曲线 |
| 调用入口 | CLI `cfdb report-sweep --case-id naca0012 --runs-dir runs/` 自动找该 case 下所有 run |

### 4.3 H. cfdb compare 命令

| 子项 | 说明 |
|---|---|
| CLI 命令 | `cfdb compare <run_id1> <run_id2> [--out PATH] [--format {html,text}]` |
| 输入 | 两个 run_id（必须存在于同一 repo，JSON 或 SQLite 均可） |
| QoI diff 表 | 文本/HTML 表格：QoI 名 / run1 值 / run2 值 / 绝对差 / 相对差 / 是否超容差 |
| 对比 SVG | 若两 run 都有 residuals_history → 双曲线叠加 SVG；若都有 Cp 数据 → Cp 叠加 SVG |
| 输出位置 | `--out PATH` 默认 `<runs_dir>/compare_<run1>_<run2>.html` |
| 容差判断 | 用 case.yaml 的 metrics.qoi_relative_tolerance；若两 run 不同 case 则跳过容差列 |

---

## 5. Schema 增量设计（草案，架构师细化）

### 5.1 不改 CaseSpec（铁律 #2）

P2-c **不改** CaseSpec / ConditionsSpec。4 个攻角通过 4 个独立 case 实现。

### 5.2 不改 RunManifest（铁律 #2）

P2-b 已有 `final_residuals` / `qoi_values` / `step_details` 字段足够支撑 Cl/Cd 提取和对比。无需新增字段。

### 5.3 新增独立数据结构（不进 Pydantic schema）

```python
# reporting/svg_polar.py
@dataclass
class PolarPoint:
    """Single point on a polar curve."""
    alpha_deg: float
    cl: float
    cd: float

@dataclass
class PolarCurve:
    """Polar curve for one solver: list of PolarPoint."""
    solver: str
    points: list[PolarPoint]
    color: str | None = None  # Okabe-Ito hex
```

这些是 reporting 内部 dataclass，不进 schema.py，不影响向后兼容。

---

## 6. 验收铁律（5 条，QA 独立验证）

| # | 铁律 | 验证方法 |
|---|---|---|
| **#1** | 不破坏 P0+P1-a+P1-b+P2-a+P2-b 的 **335** 个测试 | `pytest` 全跑，确认 335 个全过 |
| **#2** | Schema 只新增 Optional 字段或不改（P2-c 倾向不改 schema） | grep `class RunManifest` / `class CaseSpec`，确认未改已有字段 |
| **#3** | `SolverAdapter` / `ExecutionBackend` / `ResultRepository` Protocol 不动 | grep 三个 Protocol 定义，确认未改 |
| **#4** | JSON 存储与 local backend 保持兼容（`--storage json` / `--backend local` 默认） | grep CLI 默认值，确认未改 |
| **#5** | 真实 solver / 真实 Docker 测试不进 CI | grep pyproject.toml addopts，确认 `-m 'not real_solver and not real_docker'` |

---

## 7. P2-c 完成定义（DoD）

| 维度 | 完成标准 |
|---|---|
| **功能** | ①3 个新 NACA0012 攻角 case（α=5/10/15°）+ Ladson 极曲线参考数据；②Cl/Cd 提取器（OF forces + SU2 CSV）；③极曲线 SVG 生成器（Cl-α + Cd-α 双图）；④HTML report multi-solver section（Cp 叠加 + Cl/Cd 表格 + 极曲线）；⑤`cfdb compare` CLI 命令（QoI diff 表 + 对比 SVG） |
| **测试** | pytest 总数 ≥ **370**（335 + ~35 新测试）；覆盖率 ≥ **88%**；P2-b 335 回归全过 |
| **兼容性** | JSON manifest 仍可用；local backend 仍默认；P2-b 命令行为不变 |
| **文档** | 更新 README：NACA0012 多攻角示例 + cfdb compare 用法；新增 Architecture-v2.2-P2c.md |
| **CI** | CI 默认 local + JSON（保持轻量）；real_solver / real_docker marker 仍 deselect |
| **里程碑** | git commit `P2-c: NACA0012 alpha sweep + multi-solver report + cfdb compare` |

---

## 8. 协同与风险

### 8.1 协同依赖

```
F NACA0012 多攻角 ─┐
                   ├─→ 8 子 run（4 α × 2 solver）
G 多 solver 报告 ──┤   G 把这 8 run 汇总成 HTML
                   │   H 对任意两 run 做 diff
H cfdb compare ────┘
```

- **F → G**：多 solver 报告的输入是 F 产出的多 run manifest
- **F → H**：cfdb compare 的输入是 F 产出的单 run manifest
- **G → H**：H 的对比 SVG 渲染逻辑可复用 G 的 svg_compare 模块

### 8.2 风险

| 风险 | 缓解 |
|---|---|
| 4 个 case 重复度高，维护成本 | 共用 `gen_geometry.py` + STL；case.yaml 只改 α 和参考 Cp 路径 |
| Ladson 1988 极曲线数据手工录入误差 | 只录 α=0/5/10/15° 四个点的 Cl/Cd，对照 NASA TM-4074 Table 1（公开数据） |
| OpenFOAM forces object 输出格式版本差异 | 容忍 v2312/v2406 + Foundation v11/v12 的微小格式差（同 P1-b 残差解析策略） |
| 极曲线 SVG 工作量 | 参考 P2-a svg_residuals.py 的纯 Python 模式，复用 Okabe-Ito 色板 + log/线性映射 |
| cfdb compare 跨 case 场景 | 容差列只在同 case 时显示；跨 case 时只显示绝对/相对差 |

---

## 9. 不在 P2-c 范围（明确推迟）

| # | 事项 | 推迟到 |
|---|---|---|
| 1 | Web Dashboard（FastAPI + Plotly.js 实时交互） | P2-d |
| 2 | ML surrogate adapter（AirfRANS 推理 only） | P2-d（依赖 DVC） |
| 3 | Docker 自建镜像（含 gmsh/salome） | P3 按需 |
| 4 | Fluent / STAR-CCM+ adapter | P3 按需（需商业许可证） |
| 5 | Slurm backend | P3 按需 |
| 6 | 自动化网格收敛研究（Richardson extrapolation） | P3 学术合作 |
| 7 | 不确定性量化（UQ） | P3 学术合作 |

---

## 10. 未决事项（实施中架构师定夺）

| # | 事项 | 默认建议 |
|---|---|---|
| 1 | 多 solver 报告是新建 CLI 命令还是扩展 `cfdb report` | 新建 `cfdb report-sweep`，保持 `cfdb report` 单 run 语义 |
| 2 | Cl/Cd 提取从 forces object 还是 surface_flow.csv | OF 用 forces object（已配），SU2 用 surface_flow.csv（已有） |
| 3 | 极曲线 SVG 是单图（Cl-α + Cd-α 双 subplot）还是两图 | 单 SVG 双 subplot（viewBox 680x800，上下排列） |
| 4 | cfdb compare 默认输出格式 | HTML（含 SVG），`--format text` 输出纯文本表格 |
| 5 | Ladson 极曲线数据是否纳入 DVC | 是（与 P2-b Cp 曲线同级，纳入 `cases/validation/naca0012/reference/`） |

---

*文档结束。转总已拍板 §3 两决策。架构师据此做 P2-c 增量设计（Architecture-v2.2-P2c.md），工程师据此实施。*
