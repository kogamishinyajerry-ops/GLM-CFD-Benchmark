# P2-c 交付总结报告

> 生成时间：2026-06-16  
> Commit：`3f688cf`  
> 阶段：P2-c（NACA0012 α-sweep + 多 solver 对比 + cfdb compare）

## TL;DR

一句话：**完成 NACA0012 翼型 4 攻角（α=0/5/10/15°）扫描闭环 + Polar/Cp/残差三类对比 SVG + `cfdb compare` / `cfdb report-sweep` 两个新 CLI 命令；392 测试全绿，QA 独立端到端验收 IS_PASS: YES。**

## 交付概览

| 项目 | 状态 |
|------|------|
| 交付状态 | ✅ 完成 |
| 测试通过率 | 392 / 392（100%，2 deselected = real_solver + real_docker） |
| 覆盖率 | 90.39%（gate ≥ 80%） |
| 已知问题 | 0 |
| 铁律违反 | 0 |
| Commit | `3f688cf` |

## 文件清单（24 文件变更）

### 文档（2 新增）
- `docs/prd/PRD-v2.2-P2c.md` — PRD（2 项确认决策）
- `docs/architecture/Architecture-v2.2-P2c.md` — 架构增量设计

### Case 数据（7 新增）
- `cases/validation/naca0012_a5/case.yaml` + `reference/ladson1988_a5.csv`
- `cases/validation/naca0012_a10/case.yaml` + `reference/ladson1988_a10.csv`
- `cases/validation/naca0012_a15/case.yaml` + `reference/ladson1988_a15.csv`
- `cases/validation/naca0012/reference/ladson_polar.csv` — 4 点 Ladson 1988 极曲线

### 源码（3 新增 + 3 修改）
- `src/cfdb/reporting/svg_polar.py`（新）— Polar 曲线 SVG 渲染器（viewBox 680×800）
- `src/cfdb/reporting/svg_compare.py`（新）— Cp 对比 + 残差对比 SVG
- `src/cfdb/reporting/compare.py`（新）— QoIComparison + compare_runs + render_text/html
- `src/cfdb/post/qoi_extractor.py`（改）— 新增 3 个 Cl/Cd 提取函数
- `src/cfdb/reporting/html.py`（改）— 新增 `generate_multi_solver_report()`
- `src/cfdb/cli.py`（改）— 新增 `compare` + `report-sweep` 两个命令

### 测试（7 新增 + 1 修改）
- `tests/test_cl_cd_extractor.py`（13）
- `tests/test_svg_polar.py`（7）
- `tests/test_svg_compare.py`（11）
- `tests/test_compare_runs.py`（12）
- `tests/test_multi_solver_report.py`（7）
- `tests/test_cli_compare.py`（4）— 新补
- `tests/test_cli_report_sweep.py`（3）— 新补
- `tests/test_e2e.py`（改）— case count 7→10

**P2-c 新增测试合计：57**

## 关键技术决策

1. **4 独立 case 目录**而非引入 sweep 字段（遵守铁律 #2：schema 只增 Optional）
2. **α=15° tolerance 放宽到 0.15**（近失速区，Ladson 数据本身偏差大）
3. **跨 case 对比跳过 tolerance 列**（不同 case 的 tolerance 不可比）
4. **dry_run 不产生 cl/cd QoI**（数据限制非 bug；SVG 渲染逻辑用数据注入验证）

## 用户下一步建议

1. **本地手测 polar SVG**：安装 OpenFOAM/SU2 后跑真实 NACA0012 4 case，再执行
   ```bash
   cfdb report-sweep --case-id naca0012 --polar --out runs/sweep_real.html
   ```
   可以看到真正的 Cl-α / Cd-α 双子图对比 Ladson 1988 参考线。

2. **多 solver 对比**：同一 case 用 openfoam + su2 各跑一次，然后
   ```bash
   cfdb compare <of_run_id> <su2_run_id> --format html --out runs/of_vs_su2.html
   ```
   HTML 内会内嵌残差对比 SVG。

3. **P3 候选优先级建议**：
   - 短期收益最大：**Web Dashboard**（FastAPI + Plotly.js，直接消费 P2-a SQLite + P2-c polar 数据）
   - 中期差异化：**ML surrogate adapter**（AirfRANS 推理 only，凸显平台"传统 CFD vs ML"对比定位）
   - 长期生态：Fluent adapter + Slurm backend

4. **CI 配置**：当前 `addopts = -m 'not real_solver and not real_docker'`，干净环境跑 392 测试无依赖，可放心接入 GitHub Actions（ubuntu-latest + Python 3.11/3.12 矩阵）。

5. **覆盖率监控**：qoi_extractor.py 76% 是当前最低点（forces.dat 真实 I/O 路径未覆盖），若要冲 95%+ 可补 fixture-based 解析测试。
