# CFD-Benchmark PRD v3.0 — P3-hotfix（OpenFOAM adapter 真实 run 修复）

## 1. 文档信息

| 项 | 内容 |
|---|---|
| 版本 | v3.0（P3-hotfix 增量） |
| 日期 | 2026-06-17 |
| 作者 | 许清楚（Xu）· 产品经理 |
| 状态 | **Draft**（等架构师细化技术方案） |
| 基于 | PRD-v2.2-P2c.md（P2-c 基线） |
| 基线 | commit `3f688cf`（P2-c 已交付） |
| 性质 | **Hotfix**——不新增功能，仅修复让 P2-c 交付物在真实 Docker solver 下真正可用 |
| 工作目录 | `D:\GLM-CFD-Benchmark` |

---

## 2. 背景与问题陈述

> **P2-c 在 dry_run + local backend 下交付了 4 个 NACA0012 攻角 case（α=0/5/10/15°）、Cl/Cd 提取器、极曲线 SVG 与 `cfdb report-sweep --polar` 命令；但主理人静态预检发现 OpenFOAM adapter 的 `prepare()` 仍是 LDC 层流模板的残留，导致 4 个 case 在真实 Docker `openfoam/openfoam:v2406` 下要么物理错误（4 个攻角跑出来全是 α=0°）、要么直接挂（snappyHexMesh 找不到 stl / 跑完没 forces.dat）。本 PRD 定义"修到什么程度算 NACA0012 能真跑通"，不定义"怎么修"。**

一句话：**P2-c 交付的是"会渲染 case 目录"，不是"会跑真实 CFD"；P3 把这两者之间的 5 个硬伤补上。**

### 2.1 5 个硬伤的根因（静态预检结论，PRD 不重复代码细节）

| # | 硬伤 | 直接后果 | 为什么是 bug |
|---|---|---|---|
| **H1** | `prepare()` 把 `0/U` 写死 `uniform (0 0 0)`（`openfoam.py:160-165`） | 4 个攻角物理上全是 α=0°，`alpha_deg` 参数形同虚设 | case.yaml 的 `alpha_deg` 从未进入 `0/U` 渲染；CFD 翼型验证里攻角是第一性参数 |
| **H2** | `prepare()` 渲染通用 `controlDict.j2` 而非 `controlDict.naca.j2`（`openfoam.py:143-145`） | 跑完无 `forces.dat` → `extract_cl_cd_openfoam()` 拿不到 (Cl, Cd) | `controlDict.naca.j2` 模板已存在却没被调用；forces function object 是 Cl/Cd 的唯一数据源 |
| **H3** | `blockMeshDict.naca.j2` / `snappyHexMeshDict.j2` 没被 prepare 渲染 | NACA 专用网格根本不生成，`blockMesh` 用空 polyMesh / snappyHexMesh 找不到 stl 直接挂 | 两个 `.naca.j2` / `.j2` 模板文件已存在但 prepare() 从不渲染 |
| **H4** | `naca0012.stl` 没被复制到 `constant/triSurface/` | snappyHexMesh 找不到几何文件，报 `Cannot find file "constant/triSurface/naca0012.stl"` | `cases/validation/naca0012/geometry/` 里有 stl，但 prepare() 不搬运 |
| **H5** | `fvSchemes/fvSolution/transportProperties` 仍是 LDC 层流模板 | 跑 RANS-SA 翼型不合适（nu、U_ref、discretization 全是 cavity 参数） | 同模板复用，没有 NACA 专用 scheme/solution |

**H1 是物理正确性的硬伤（最严重：跑通了也是错的）；H2-H5 是能不能跑通的硬伤。**

---

## 3. 范围（IN / OUT scope）

### 3.1 IN scope（本 hotfix 做的事）

- **修复 H1-H5**，让 `cfdb run --backend docker --image openfoam/openfoam:v2406 --case naca0012_a{0,5,10,15}` 全部 exit 0
- 每个 run 产出 `postProcessing/forces/<time>/forces.dat`
- `extract_cl_cd_openfoam()` 能从 forces.dat 提取真实 (Cl, Cd) 并写入 manifest 的 `qoi_values`
- `cfdb report-sweep --case-id naca0012 --polar` 生成的 HTML 含**真实数据**的 `<svg>`（非占位/空）
- 单测覆盖新增 prepare() 渲染分支，覆盖率不下降

### 3.2 OUT scope（明确不做）

| # | 事项 | 推迟/原因 |
|---|---|---|
| 1 | SU2 adapter 修复 | 用户决策：本 hotfix 只跑 OpenFOAM |
| 2 | 精度调优（网格细化、y+ 收敛、湍流模型对比） | 先跑通，精度 P3+ |
| 3 | 网格收敛研究（Richardson extrapolation） | 学术课题，非 hotfix 范围 |
| 4 | 新增 case（其他翼型/攻角） | 本 hotfix 只修现有 4 个 |
| 5 | ML surrogate / AirfRANS | P2-d / P3 |
| 6 | Web Dashboard | P2-d / P3 |
| 7 | 新 schema 字段 / 新 Protocol | hotfix 不动架构（守铁律 #2/#3） |
| 8 | 残差/力的格式解析大改 | 仅做容错增强（见 §6 风险） |

---

## 4. 用户故事

### US-1（核心）—— 研究员跑极曲线
> **As a** CFD 研究员，
> **I want** 在装有 Docker 的机器上执行
> `cfdb run --backend docker --image openfoam/openfoam:v2406 --case naca0012_a0`
> （以及 a5/a10/a15）**so that** 4 个攻角各自产出真实的 Cl/Cd，且不同攻角的 Cl/Cd 真的随 α 变化（不是 4 个 α=0°）。

**验收**：4 个 run 全 exit 0；4 个 Cl 值随 α 单调递增（趋势对，绝对值可不精确）。

### US-2（核心）—— 研究员看极曲线对比
> **As a** CFD 研究员，
> **I want** 执行 `cfdb report-sweep --case-id naca0012 --polar` **so that** 得到一张含真实 Cl-α / Cd-α 双子图的 HTML，能和 Ladson 1988 实验数据肉眼对比趋势。

**验收**：HTML 中 `<svg>` 段含 ≥ 4 个数据点（4 攻角），非 `<text>No polar data to display</text>` 占位。

---

## 5. 优先级矩阵（5 修复项的 P0/P1）

| 修复项 | 优先级 | 理由 |
|---|---|---|
| **H1** — `0/U` 按 α 渲染远场速度 `U_inf=(cosα·U, sinα·U, 0)` | **P0** | 物理正确性硬伤；不修则"跑通也是错的"，极曲线无意义 |
| **H2** — `prepare()` 渲染 `controlDict.naca.j2`（含 forces function object） | **P0** | 不修则无 forces.dat → 无 Cl/Cd → 极曲线空 |
| **H3** — `prepare()` 渲染 `blockMeshDict.naca.j2` / `snappyHexMeshDict.j2` | **P0** | 不修则 snappyHexMesh 挂，run 不 exit 0 |
| **H4** — `naca0012.stl` 复制到 `constant/triSurface/` | **P0** | 不修则 snappyHexMesh 挂 |
| **H5** — `fvSchemes/fvSolution/transportProperties` 改用 NACA RANS-SA 模板 | **P1** | 跑不挂但物理不对；可与 H1 同批改（都是模板复用问题） |

**关键判断**：H1-H4 全 P0（任何一个不修都跑不通或物理错）；H5 是 P1（能跑但精度差）。建议工程师一次性把 5 个都改（都在 `prepare()` + 模板目录，改动集中）。

---

## 6. 风险与缓解

| # | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| R1 | OpenFOAM v2406 forces function object 输出格式与 `extract_cl_cd_openfoam` 的 regex 不匹配（列数/括号写法版本差异） | 中 | 高（拿不到 Cl/Cd） | post extractor 增加容错：①同时识别 `forces.dat` 与 `force.dat`（Foundation 拼法）；②正则多 pattern fallback；③拿不到时返回 None + warning，不抛异常阻塞 run |
| R2 | Docker 镜像内 PATH 不含 OpenFOAM bin（`simpleFoam` / `snappyHexMesh` not found） | 低 | 高（run 直接挂） | DockerBackend 用镜像默认 entrypoint（`openfoam/openfoam:v2406` 已 source bashrc）；若仍缺，文档说明用 `--entrypoint bash -c "source /opt/openfoam/... && cmd"`；不阻塞 hotfix 主体 |
| R3 | 2D `empty` 边界 + snappyHexMesh 在 v2406 报 warning（non-orthogonality / 2D extrusion） | 高 | 低（warning 不致命） | 先跑通；warning 记录到日志，不作为 exit≠0 判据；精度问题留 P3+ |
| R4 | `forces.dat` 路径 `postProcessing/forces/<time>/forces.dat` 的 `<time>` 目录名随求解时间变（如 `0/` vs `1000/`） | 中 | 中（collect_outputs 找不到） | collect 时用 `rglob("forces.dat")` 取最新（按目录名数值排序），不写死 time 目录 |
| R5 | 4 个 case 共用 geometry/STL，但 a5/a10/a15 的 `geometry.source` 指向 `../naca0012/geometry/...` 相对路径，Docker bind mount 下可能解析异常 | 中 | 中（prepare 阶段挂） | prepare() 在宿主侧渲染，解析为绝对路径再写入；STL 复制到 `constant/triSurface/` 后 snappyHexMesh 不再依赖原始相对路径 |

---

## 7. DoD（验收标准）

| # | 验收项 | 验证方法 |
|---|---|---|
| **D1** | `cfdb run --backend docker --image openfoam/openfoam:v2406 --case naca0012_a0`（及 a5/a10/a15）全部 exit 0 | QA 真跑 4 次，记录 exit code + 日志 |
| **D2** | 每个 run 产出 `postProcessing/forces/<time>/forces.dat`（非空，含 ≥1 个时间步的力向量） | QA 检查文件存在 + 非空 + 含 `(...)` 向量行 |
| **D3** | `extract_cl_cd_openfoam(forces_dat)` 对每个 run 返回非 None 的 (Cl, Cd) | QA 调用提取器，确认 tuple 非 None 且数值在物理合理范围（Cl ∈ [-0.2, 1.5]，Cd ∈ [0, 0.05]） |
| **D4** | 4 个攻角的 Cl 随 α 单调递增（α=0→5→10→15°，Cl 递增；趋势对即可，绝对值可不精确） | QA 比对 4 个 Cl 值序列 |
| **D5** | `cfdb report-sweep --case-id naca0012 --polar` 生成的 HTML 含 `<svg` 且数据点 ≥4（非 `<text>No polar data to display</text>`） | QA 打开 HTML，grep `<svg`，确认含 Cl-α / Cd-α 数据点 |
| **D6** | pytest 总数 ≥ P2-c 基线；覆盖率 ≥ **80%**（`--cov-fail-under=80` gate 保留） | `pytest` 全跑，确认通过 + cov ≥ 80% |
| **D7** | 5 条铁律全部不违反（见 §8） | QA 独立验证 |

---

## 8. 验收铁律（5 条，QA 独立验证，沿用 P2-b/c）

| # | 铁律 | 验证方法 |
|---|---|---|
| **#1** | 不破坏 P0+P1+P2 全部既有测试 | `pytest` 全跑，既有测试全过 |
| **#2** | Schema 只新增 Optional 字段或不改（本 hotfix 倾向不改 schema） | grep `class RunManifest` / `class CaseSpec`，确认未改已有字段 |
| **#3** | `SolverAdapter` / `ExecutionBackend` / `ResultRepository` Protocol 不动 | grep 三个 Protocol 定义，确认未改 |
| **#4** | JSON 存储与 local backend 保持默认兼容（`--storage json` / `--backend local` 默认） | grep CLI 默认值，确认未改 |
| **#5** | 真实 solver / 真实 Docker 测试不进 CI | grep pyproject.toml addopts，确认 `-m 'not real_solver and not real_docker'`；real run 用 `@pytest.mark.real_solver` + `real_docker` 标记 |

---

## 9. 待确认问题（Open Questions）

| # | 问题 | 默认建议 / 备注 |
|---|---|---|
| Q1 | `report-sweep` 的 `--polar` 当前从 `met.qoi_relative_errors` 取 Cl/Cd（`cli.py:521-522`），但这是"相对误差"不是"数值"。极曲线应画真实 Cl/Cd 值。这是否是隐藏的 bug #6？ | **建议架构师审查**：极曲线数据源应为 `manifest.qoi_values["cl"/"cd"]`（真实值），而非 `qoi_relative_errors`。若是 bug 则纳入本 hotfix（P0，否则 D5 数据是错的）；若 manifest 没存真实值则需补提取路径。**此条不在 5 硬伤内，但可能阻塞 D5，留给架构师判定。** |
| Q2 | `forces.dat` 的密度/速度参数（`extract_cl_cd_openfoam` 默认 `rho=1.225, u_inf=100`）是否要与 case.yaml 的条件（Re=6e6, M=0.3）一致？ | 建议：从 case.yaml 的 conditions 推导 rho/u_inf 并传入，而非写死默认；否则 Cl/Cd 绝对值会偏差。架构师定具体传参方式。 |
| Q3 | a0 case 是用 `cases/validation/naca0012/`（base）还是需要新建 `naca0012_a0/`？ | 当前 4 个攻角目录是 `naca0012/`(=a0) + `naca0012_a5/a10/a15`。若 report-sweep 的 `--case-id naca0012` 要匹配全部 4 个，a0 的 case_id 必须以 `naca0012` 开头。**建议确认 base case 的 `id` 字段**（`naca0012` vs `naca0012_a0`），避免 report-sweep 匹配不上。 |

---

## 10. 不做竞品/市场分析

本 PRD 为 **hotfix**，不涉及产品定位/竞品/市场规模。P2 路线图与战略分析见 PRD-v2.0 / v2.1 / v2.2。

---

*文档结束。架构师据此做 P3-hotfix 技术方案（聚焦 prepare() 渲染分发 + 模板补全 + STL 搬运 + extractor 容错），工程师据此实施。PM 不写代码方案。*
