# CFD-Benchmark PRD v2.0 — P2 路线图

## 1. 文档信息

| 项 | 内容 |
|---|---|
| 版本 | v2.0（P2 路线图） |
| 日期 | 2026-06-17 |
| 作者 | 许清楚（Xu）· 产品经理 |
| 状态 | **Draft**（待转总确认 10 个待确认问题后转 Confirmed） |
| 基于 | PRD-v1（P0 已交付）/ PRD-v1.1（P1-a 已交付）/ PRD-v1.2（P1-b 已交付）/ Architecture-v1.2 |
| 基线 | P0（commit `5c9948e`，112 测试）+ P1-a（commit `4d67403`，158 测试）+ P1-b（commit `4e0b857`，178 测试 / 90.89% cov） |
| 工作目录 | `D:\GLM-CFD-Benchmark` |

---

## 2. 当前基线回顾

P0 → P1-a → P1-b 三阶段已完整交付，平台具备了**从 mock 到真实 solver 的全链路能力**：

| 阶段 | 交付能力 |
|---|---|
| P0 | schema（Pydantic v2）→ case registry → CLI 4 命令 → generic_command adapter → metrics → HTML report，4 个 mock case 闭环 |
| P1-a | OpenFOAM/SU2 adapter 的 dry_run 模式（生成 case 目录但不执行），Jinja2 模板渲染，CLI `--dry-run` flag |
| P1-b | 真实 subprocess 执行（blockMesh → icoFoam / SU2_CFD），残差日志正则解析（final residuals），QoI 提取（OpenFOAM probes / SU2 CSV），solver_version 探测，CommandStep.critical 生效，Ghia 1982 / Blasius 参考数据 |

**当前限制（P1-b 遗留，P2 要解决）**：

| # | 限制 | 来源 |
|---|---|---|
| 1 | 仅本机执行（LocalExecutionBackend），CI 无法自动跑真实 solver | P1-b Q1 Docker 推迟 |
| 2 | 无大文件管理，网格手工放 git（仅 <1MB 可行） | P1-b Q3 DVC 推迟 |
| 3 | JSON manifest 存储，无法跨 run 查询/筛选/对比 | P0 Q3 SQLite 推迟 P2 |
| 4 | 无残差曲线可视化，报告只有 final residual 数值 | P1-b Q4 SVG 推迟 |
| 5 | 仅 OpenFOAM + SU2 两个 adapter，无商业 solver / ML surrogate | PRD-v1 P2 范围 |
| 6 | 无经典外流验证 case（NACA0012），仅内流（cavity）+ 边界层（flat plate） | P1-b Q2 NACA0012 推迟 |
| 7 | manifest 缺 cell_count（网格规模）和 step_details（每步状态） | P1-b Q10 / §15.1 推迟 |

---

## 3. P2 战略目标

P2 要从三个维度将平台从"可用"推向"可信赖、可扩展、可传播"：

1. **可重现性**：CI/CD 环境下任何人都能一键复现 benchmark 结果（Docker + DVC），消除"在我机器上能跑"的问题。
2. **规模化**：支持大量 run 的持久化查询与多 solver 横向对比（SQLite + NACA0012 多 solver），为 HPC 场景铺路（Slurm）。
3. **可读性**：让非 CFD 专家也能直观理解结果——残差曲线 SVG、Web Dashboard、ML surrogate 对比图。

> **P2 一句话定位**：让 CFD benchmark 结果在任何环境下可复现、可查询、可可视化。

---

## 4. 9 个候选方向深度分析

### A. Docker backend

| 维度 | 分析 |
|---|---|
| **价值** | 解决 P1-b 最大遗留痛点——CI 无法自动验证真实 solver。容器化执行保证跨环境一致，container_digest 写入 manifest 强化可复现性。 |
| **复杂度** | **高**。约 5-8 个新文件（`execution/docker.py` + Dockerfile + 测试），~15 个新测试，3 个决策点（镜像策略 / 挂载方案 / cleanup）。 |
| **依赖** | Docker daemon（CI 与本地均需）。Architecture-v1 §14 已预留 `ExecutionBackend` 扩展点。 |
| **风险** | CI 运行时间增加（镜像拉取 ~30s）；Windows 用户需 Docker Desktop；镜像维护（OpenFOAM 版本升级）。 |
| **协同** | 与 **B（DVC）** 协同：容器内挂载 DVC 管理的网格。与 **E（NACA0012）** 协同：一致环境多 solver 对比。 |

### B. DVC 大文件管理

| 维度 | 分析 |
|---|---|
| **价值** | 解锁真实工程级 case（NACA0012 网格、AirfRANS 数据集均 >10MB）。版本化网格/参考数据，`git commit` + `dvc push` 保证数据可追溯。 |
| **复杂度** | **中**。约 3-5 个文件（`.dvc/config` + `dvc.yaml` + 文档），~8 个新测试，2 个决策点（远程存储选型 / track 范围）。 |
| **依赖** | DVC CLI + 远程存储（MinIO/S3/Aliyun OSS）。PRD-v1 Q2 原决策"MVP 上 DVC"被 P1-b Q3 覆盖为"小网格手工放置"。 |
| **风险** | 远程存储维护成本；新用户需配 DVC remote 才能拉大数据。 |
| **协同** | 与 **E（NACA0012）** 强协同：NACA0012 网格需 DVC 管理。与 **F（surrogate）** 协同：AirfRANS 数据集 ~2GB。 |

### C. SQLite 持久化

| 维度 | 分析 |
|---|---|
| **价值** | 解决 JSON manifest 无法查询的痛点。支持"列出某 case 所有 run""筛选误差 <5% 的 run""跨 solver 对比"等查询。是 Web Dashboard 的数据基础。 |
| **复杂度** | **中**。约 4-6 个文件（`storage/sqlite_repo.py` + schema + migration + 测试），~12 个新测试，2 个决策点（表设计 / migration 机制）。`ResultRepository` Protocol 已预留，零业务代码改动。 |
| **依赖** | 无外部依赖（Python 标准库 `sqlite3`）。 |
| **风险** | 极低。SQLite 单文件、零配置、跨平台。 |
| **协同** | 与 **D（SVG）** 协同：多 case 残差对比图从 SQLite 查询。与 **I（Web Dashboard）** 强协同：数据源。 |

### D. 残差曲线 SVG 报告

| 维度 | 分析 |
|---|---|
| **价值** | 解决 P1-b Q4 推迟的可视化痛点。残差解析逻辑已有（`post/residuals.py`），只需将完整残差历史（非仅 final value）渲染为 SVG 内嵌到 HTML report。用户一眼判断收敛质量。 |
| **复杂度** | **低**（9 个方向中最低）。约 2-3 个文件（`reporting/svg_residuals.py` + 模板修改），~6 个新测试，1 个决策点（单 case vs 多 case）。 |
| **依赖** | 无外部依赖（纯 Python 生成 SVG，或用 `matplotlib` savefig SVG）。残差解析已有。 |
| **风险** | 极低。 |
| **协同** | 与 **C（SQLite）** 协同：多 case 对比需查历史 run。与 **I（Web Dashboard）** 协同：内嵌图表。 |

### E. NACA0012 validation case

| 维度 | 分析 |
|---|---|
| **价值** | CFD 验证领域最经典的外流基准（Ladson 1988 实验数据）。补齐"内流（cavity）+ 边界层（flat plate）+ 外流（翼型）"三维验证覆盖。支持 OpenFOAM vs SU2 横向对比——项目核心价值展示。 |
| **复杂度** | **中**。约 6-10 个文件（case.yaml + 几何生成脚本 + 网格 + 参考数据 + adapter 配置），~10 个新测试，3 个决策点（单 solver vs 多 solver / 攻角范围 / 网格生成方式）。 |
| **依赖** | OpenFOAM/SU2 真实安装；NACA 4-digit 几何生成（Python `numpy` 即可）；网格生成（OpenFOAM snappyHexMesh / SU2 mesh generator）。 |
| **风险** | snappyHexMesh 调试成本高（背景网格 / 边界层 / surface）;多攻角扫描工作量翻倍。 |
| **协同** | 与 **A（Docker）** 协同：一致环境。与 **B（DVC）** 协同：网格文件管理。与 **G（Fluent）** 协同：三 solver 对比。 |

### F. ML surrogate adapter

| 维度 | 分析 |
|---|---|
| **价值** | **项目最大差异化卖点**。PRD-v1 §2 痛点 4 明确指出"ML surrogate CFD 模型缺乏可信评测"。AirfRANS（NeurIPS 2022）是标准 benchmark 数据集。接入后平台从"传统 CFD V&V"升级为"传统 vs ML 统一评测"。 |
| **复杂度** | **高**。约 8-12 个文件（`adapters/surrogate.py` + 模型加载 + 推理 + AirfRANS case + 测试），~15 个新测试，4 个决策点（框架 PyTorch/JAX/ONNX / 训练 vs 推理 / 单模型 vs 多模型 / 物理一致性指标）。 |
| **依赖** | PyTorch（~2GB）；AirfRANS 数据集（~2GB，需 DVC）；`SolverAdapter` Protocol 扩展（surrogate 无 subprocess，推理逻辑不同）。 |
| **风险** | 重依赖（PyTorch）；模型版本兼容性；物理一致性指标（divergence error / boundary violation）需额外实现。 |
| **协同** | 与 **B（DVC）** 强协同：数据集管理。与 **I（Web Dashboard）** 协同：ML vs 传统对比可视化。 |

### G. Fluent adapter

| 维度 | 分析 |
|---|---|
| **价值** | 接入工业级商业 solver，覆盖"开源（OpenFOAM/SU2）+ 商业（Fluent/STAR-CCM+）+ ML（surrogate）"全谱系。转总主用 STAR-CCM+，Fluent 是最接近的商业替代。 |
| **复杂度** | **中-高**。约 5-8 个文件（`adapters/fluent.py` + journal 脚本模板 + 许可证管理 + 测试），~10 个新测试，3 个决策点（PyFluent API vs journal 脚本 / 许可证检测 / 输出解析）。 |
| **依赖** | Fluent 许可证（商业，非所有用户可得）；PyFluent（`ansys-fluent-core`）或 journal 脚本方式。 |
| **风险** | 许可证限制导致 CI 无法测试；商业软件版本差异大；受众受限（需 Fluent 授权用户）。 |
| **协同** | 与 **E（NACA0012）** 协同：三 solver 对比。 |

### H. Slurm backend

| 维度 | 分析 |
|---|---|
| **价值** | HPC 场景大规模 case 并行执行。真实工程级 CFD（千万级网格）需 HPC，Slurm 是 HPC 事实标准。 |
| **复杂度** | **中**。约 4-6 个文件（`execution/slurm.py` + batch script 模板 + 状态轮询 + 测试），~10 个新测试，2 个决策点（完整提交 vs 仅脚本生成 / 状态轮询机制）。`ExecutionBackend` Protocol 已预留。 |
| **依赖** | Slurm 集群（非所有用户可得）。 |
| **风险** | HPC 环境依赖导致 CI/本地无法测试；Slurm 版本/配置差异。 |
| **协同** | 与 **A（Docker）** 协同：HPC 上 Singularity/Apptainer 容器。与 **E（NACA0012）** 协同：大规模网格。 |

### I. Web Dashboard

| 维度 | 分析 |
|---|---|
| **价值** | 可视化对比平台，非 CFD 专家（管理者/客户）也能看懂结果。跨 run 排行榜、多 solver 对比图、残差/收敛历史。项目对外展示的"门面"。 |
| **复杂度** | **高**。约 15-25 个文件（后端 API + 前端页面 + 图表组件 + 测试），~20 个新测试，4 个决策点（静态 HTML vs 动态站 / 前端框架 / 图表库 / 部署方式）。 |
| **依赖** | 前端栈（推荐 React + MUI 或纯 Plotly）；后端（FastAPI）；数据源（SQLite，依赖 C）。 |
| **风险** | 维护成本高（前端 + 后端双栈）；技术栈选择争议；部署复杂度。 |
| **协同** | 与 **C（SQLite）** 强依赖：数据源。与 **D（SVG）** 协同：内嵌图表复用。 |

---

## 5. P2 分批策略建议（核心产出）

### 推荐分批方案

```
P2-a（夯实基础 + 可视化）    D残差SVG + C SQLite + P1-b遗留小项
P2-b（可重现性 + 内容扩展）  A Docker + B DVC + E NACA0012
P2-c（前沿卖点 + 平台化）    F surrogate + I Web Dashboard
P3（按需 / 受众受限）        G Fluent + H Slurm
```

### P2-a：夯实基础 + 可视化（推荐首批）

| 方向 | 理由 |
|---|---|
| **D 残差曲线 SVG** | 9 方向中**复杂度最低**（残差解析已有），**用户感知最强**（报告从"数字表格"变"收敛曲线"）。P1-b Q4 明确推迟项，性价比最高。 |
| **C SQLite 持久化** | 中复杂度但**零外部依赖**，解决 JSON 无法查询的痛点。是后续 Web Dashboard（P2-c）和多 case 对比的数据基础。`ResultRepository` Protocol 已预留，实现切换零业务代码改动。 |
| **P1-b 遗留小项** | cell_count（从 blockMesh log 提取网格规模）+ step_details（每步状态记录）。工作量极小（各 ~1 文件），顺手清掉 P1-b 的两个 TODO。 |

**P2-a 预估工作量**：~10-14 个文件变更/新增，~25-30 个新测试，总测试数 200-210。

**为什么 Docker 不放 P2-a？** Docker 复杂度高（镜像管理/挂载/cleanup），且 P2-a 的核心目标是"用最低成本交付最高用户感知"。Docker 的价值更多在 CI 自动化（开发者/CI 受益），而 SVG + SQLite 直接提升所有用户的报告体验。Docker 放 P2-b 与 DVC + NACA0012 一起做更合理。

### P2-b：可重现性 + 内容扩展

| 方向 | 理由 |
|---|---|
| **A Docker backend** | P1-b 最大遗留痛点（Q1）。解锁 CI 自动化——CI 可跑真实 solver 做回归。Architecture-v1 §14 已预留扩展点。 |
| **B DVC 大文件管理** | 配合 NACA0012 的大网格（snappyHexMesh 生成 >10MB）和后续 surrogate 数据集。 |
| **E NACA0012** | 经典外流验证，补齐验证维度。OpenFOAM + SU2 多 solver 对比是项目核心价值展示。需 Docker（一致环境）+ DVC（网格管理）就绪后高效开发。 |

**P2-b 预估工作量**：~20-28 个文件变更/新增，~35-45 个新测试，总测试数 235-255。

**协同逻辑**：Docker 提供一致运行环境 → DVC 管理大网格 → NACA0012 在此基础上做真实外流验证。三者形成"可重现外流 benchmark"闭环。

### P2-c：前沿卖点 + 平台化

| 方向 | 理由 |
|---|---|
| **F ML surrogate adapter** | **项目最大差异化卖点**。PRD-v1 核心目标之一。AirfRANS 数据集接入后，平台从"传统 CFD V&V"升级为"传统 vs ML 统一评测"。需 DVC（P2-b）管理 ~2GB 数据集。 |
| **I Web Dashboard** | 可视化对比平台，项目"门面"。依赖 SQLite（P2-a）作为数据源。展示 surrogate vs 传统 CFD 对比是最佳 demo 场景。 |

**P2-c 预估工作量**：~25-40 个文件变更/新增，~40-50 个新测试，总测试数 275-305。

### P3：按需 / 受众受限（不建议近期做）

| 方向 | 理由 |
|---|---|
| **G Fluent adapter** | 需商业许可证，CI 无法测试，受众仅限 Fluent 授权用户。建议等平台有足够用户基础后再做，或用户明确需求驱动。 |
| **H Slurm backend** | 需 HPC 集群，CI/本地无法测试。建议等有真实 HPC 使用场景（如转总团队上集群）再做。 |

> **PM 立场**：G 和 H 的 ROI 取决于是否有真实用户驱动。如果转总团队有 Fluent 许可证或 HPC 需求，可提前插入 P2-b 或 P2-c。否则建议 P3 按需启动。

---

## 6. 待确认问题（10 个，需转总拍板 P2 范围）

### ⭐⭐⭐ Q1. P2-a 优先级：先做什么？

| | |
|---|---|
| **背景** | P2 有 9 个方向，P2-a 应该是"高价值 + 低复杂度 + 解决 P1-b 痛点"。需确定首批范围。 |
| **选项 A** | 先做基础设施（Docker + DVC + SQLite）——解决可重现性与存储 |
| **选项 B** | 先做可视化（残差 SVG + Web Dashboard）——解决可读性 |
| **选项 C** | 先做内容（NACA0012 + surrogate）——解决验证覆盖与卖点 |
| **选项 D** | 混合：SVG + SQLite + P1-b 遗留小项（PM 推荐） |
| **PM 推荐** | **D**。理由：①SVG 复杂度最低（9 方向最低）且用户感知最强；②SQLite 零依赖且解锁后续 Web Dashboard；③P1-b 遗留小项（cell_count + step_details）成本极低；④Docker 复杂度高，放 P2-b 与 DVC/NACA0012 协同更合理。 |
| **影响** | 选 A → P2-a 工作量大（Docker 高复杂度）；选 B → 缺乏存储基础，Web Dashboard 无数据源；选 C → 缺乏 Docker/DVC 基础，NACA0012/surrogate 开发困难。 |

### ⭐⭐⭐ Q2. Docker backend 范围：完整支持 vs 仅 CI 镜像？

| | |
|---|---|
| **背景** | Docker backend 可做"完整支持"（用户 `--backend docker`）或"仅 CI 镜像"（CI 内部用 Docker 跑真实 solver，用户侧不暴露）。 |
| **选项 A** | 完整支持：实现 `DockerBackend(ExecutionBackend)`，用户可 `--backend docker --image openfoam/openfoam:v2406`，container_digest 写入 manifest |
| **选项 B** | 仅 CI 镜像：提供 Dockerfile + CI workflow，CI 内部用 Docker 跑真实 solver，CLI 不暴露 `--backend docker` |
| **选项 C** | 先 B 后 A：P2-b 先做 CI 镜像（快速解锁 CI 自动化），P2-c 补完整 `DockerBackend` |
| **PM 推荐** | **A**。理由：①Architecture-v1 §14 已预留 `ExecutionBackend` 扩展点，完整实现与仅 CI 的代码量差异不大（~多 2 文件）；②完整支持让本地用户也能用 Docker 保证可复现性；③container_digest 写入 manifest 是可复现性的核心承诺。 |
| **影响** | 选 B → 用户本机仍需手装 solver，可复现性打折扣；选 C → 分两批增加协调成本。 |

### ⭐⭐ Q3. DVC 范围：只管网格 vs 也管参考数据 vs 全仓？

| | |
|---|---|
| **背景** | DVC 可管理不同范围的大文件。范围越大管理越统一，但配置越复杂。 |
| **选项 A** | 只管网格文件（`.su2` / `blockMeshDict` 生成的 mesh / snappyHexMesh 输出） |
| **选项 B** | 网格 + 参考数据（实验数据 CSV / AirfRANS 数据集） |
| **选项 C** | 全仓 `dvc track`（所有 >1MB 文件，包括日志、场数据） |
| **PM 推荐** | **B**。理由：①网格和参考数据是"输入"，需版本化保证可复现；②日志/场数据是"输出"（run 产物），由 manifest 管理，不需要 DVC；③全仓 track 增加复杂度且 run 产物本就在 `runs/` 目录隔离。 |
| **影响** | 选 A → 参考数据仍手工管理，AirfRANS 数据集（~2GB）无法纳入；选 C → 过度工程化。 |

### ⭐⭐ Q4. SQLite schema：镜像 JSON vs 关系表？是否要 migration？

| | |
|---|---|
| **背景** | `ResultRepository` Protocol 已预留 SQLite 实现。schema 设计直接影响查询能力。 |
| **选项 A** | 镜像 JSON：一张表，每行存完整 manifest JSON blob（`json_extract` 查询） |
| **选项 B** | 关系表：`runs` 表（run_id/case_id/solver/status/timestamp）+ `metrics` 表 + `residuals` 表，支持高效 SQL 查询 |
| **选项 C** | B + migration 机制（版本表 + alembic 或手写 migration 脚本） |
| **PM 推荐** | **C**。理由：①关系表查询效率远高于 JSON blob（"筛选误差<5%的 run"在 blob 上需全表扫描）；②migration 机制为后续 schema 演进（P2-c surrogate 字段）预留空间；③SQLite migration 可用轻量手写方案（版本表 + `migrate_v1_to_v2.sql`），不需 alembic。 |
| **影响** | 选 A → 查询能力受限，Web Dashboard 复杂查询困难；选 B → 无 migration，后续加字段需手动 ALTER。 |

### ⭐⭐ Q5. SVG 报告范围：单 case 残差 vs 多 case 对比图？

| | |
|---|---|
| **背景** | 残差 SVG 可做单 case（一个 run 的收敛曲线）或多 case（同一 case 多个 solver/run 的残差叠加对比）。 |
| **选项 A** | 单 case：HTML report 内嵌当前 run 的残差下降曲线 SVG |
| **选项 B** | 单 case + 多 case：A + 独立的"对比报告"命令（`cfdb compare --runs r1,r2,r3`）生成多 run 叠加 SVG |
| **PM 推荐** | **P2-a 先做 A，P2-b 补 B**。理由：①单 case SVG 复杂度最低（已有残差数据，只需渲染）；②多 case 对比依赖 SQLite（查询历史 run），逻辑上属 P2-a 后期或 P2-b；③分两步交付，快速见效。 |
| **影响** | 选 B 一次性做 → P2-a 工作量增加 ~3 文件 + 需 SQLite 先就绪。 |

### ⭐⭐ Q6. NACA0012 范围：单 solver vs 多 solver 对比 vs 多攻角扫描？

| | |
|---|---|
| **背景** | NACA0012 可做单一验证（1 solver 1 攻角）、多 solver 对比（OpenFOAM vs SU2）、或多攻角扫描（α=0/5/10/15°，Cl/Cd 曲线 vs Ladson 1988）。 |
| **选项 A** | 单 solver 验证：OpenFOAM 跑 α=0°，Cp 分布 vs Ladson 1988 |
| **选项 B** | OpenFOAM + SU2 对比：两 solver 各跑 α=0°，Cp 对比 |
| **选项 C** | 多 solver + 多攻角：A/B 基础上加 α=0/5/10/15° 扫描，Cl/Cd 曲线 |
| **PM 推荐** | **B**。理由：①多 solver 对比是项目核心价值（PRD-v1 §2 痛点 3），NACA0012 是最佳展示 case；②多攻角扫描工作量翻 4 倍（4× 网格 + 4× 求解 + Cl/Cd 后处理），推 P2-c 或 P3；③单 solver 缺乏对比价值，不如直接用 cavity。 |
| **影响** | 选 A → 缺乏对比卖点；选 C → P2-b 工作量过大。 |

### ⭐⭐ Q7. Surrogate adapter 范围：AirfRANS 单数据集 vs 多模型？训练 vs 推理？

| | |
|---|---|
| **背景** | ML surrogate adapter 是项目核心卖点。AirfRANS 是标准 benchmark（NeurIPS 2022）。需确定推理 only 还是支持训练。 |
| **选项 A** | AirfRANS 推理 only：加载预训练模型（AirfRANS 官方 checkpoint），在同一 case 上推理，与 OpenFOAM 对比。不训练。 |
| **选项 B** | A + 多模型对比：加载多个 surrogate 模型（AirfRANS / GraphCast / 自研），横向对比 |
| **选项 C** | 训练 + 推理：支持在 AirfRANS 数据集上训练新模型，再推理对比 |
| **PM 推荐** | **A**。理由：①推理 only 复杂度可控（训练涉及超参管理/GPU 资源，复杂度翻倍）；②AirfRANS 官方提供预训练 checkpoint，直接推理即可对比；③多模型对比推 P3（需多个框架 checkpoint 适配）；④物理一致性指标（divergence error / boundary violation）是 surrogate 的核心评测维度，必须实现。 |
| **影响** | 选 B → 需适配多个框架，复杂度高；选 C → 训练流程复杂度极高，偏离 benchmark 定位。 |

### ⭐ Q8. Fluent adapter：真接 Fluent vs STAR-CCM+ vs PyFluent/journal？

| | |
|---|---|
| **背景** | 转总主用 STAR-CCM+。Fluent 是最接近的商业替代。接入方式影响实现复杂度。 |
| **选项 A** | PyFluent API（`ansys-fluent-core`）：Python 直接调 Fluent 求解器 API |
| **选项 B** | Journal 脚本：生成 Fluent journal（`.jou`）脚本，命令行调 `fluent 3ddp -t4 -i case.jou` |
| **选项 C** | 推 P3，P2 不做 Fluent（PM 推荐） |
| **选项 D** | 接 STAR-CCM+（用户主用），用 STAR-CCM+ Java macro 方式 |
| **PM 推荐** | **C**（推 P3）。理由：①Fluent/STAR-CCM+ 均需商业许可证，CI 无法测试，受众受限；②P2 核心价值在开源生态（OpenFOAM/SU2）+ ML（surrogate），商业 solver 接入 ROI 低；③若转总团队有明确 Fluent/STAR-CCM+ 需求驱动，可作为 P2-c 插入项。若做，推荐 **B（journal 脚本）**：不依赖 PyFluent，通用性更好。 |
| **影响** | 选 A/B → P2 增加 ~8 文件 + 许可证管理复杂度；选 D → STAR-CCM+ Java macro 适配复杂。 |

### ⭐ Q9. Slurm backend：完整 Slurm vs 仅 batch script 生成？

| | |
|---|---|
| **背景** | Slurm 可做完整提交（`sbatch` + 状态轮询）或仅生成 batch script（用户手动提交）。 |
| **选项 A** | 完整：`SlurmBackend(ExecutionBackend)` 实现 `sbatch` 提交 + `squeue` 轮询 + `sacct` 取结果 |
| **选项 B** | 仅 batch script 生成：生成 `.sbatch` 文件 + `cfdb run --backend slurm --dry-run` 生成脚本不提交 |
| **选项 C** | 推 P3（PM 推荐） |
| **PM 推荐** | **C**（推 P3）。理由：①Slurm 需 HPC 集群，CI/本地无法测试；②P2 阶段本机 + Docker 已覆盖大部分场景；③若转总团队有 HPC 需求驱动，可做 **B（仅脚本生成）**：复杂度低，用户拿到脚本后自行 `sbatch`。 |
| **影响** | 选 A → 需 HPC 测试环境，质量难保证；选 B → 轻量但用户仍需手动提交。 |

### ⭐⭐ Q10. Web Dashboard：静态 HTML 生成 vs FastAPI 动态站？

| | |
|---|---|
| **背景** | Web Dashboard 可做静态（`cfdb dashboard --generate` 生成 HTML 站）或动态（FastAPI 服务端 + 前端页面）。 |
| **选项 A** | 静态 HTML 生成：`cfdb dashboard` 扫描所有 run，生成静态 HTML 站（类似 MkDocs/GitHub Pages），可直接部署 |
| **选项 B** | FastAPI 动态站：后端 FastAPI 读 SQLite，前端 React/Vue + Plotly，实时查询交互 |
| **选项 C** | 先 A 后 B：P2-c 先做静态站（快速展示），P3 补动态站 |
| **PM 推荐** | **B**。理由：①动态站交互体验远超静态（筛选/排序/实时对比）；②SQLite（P2-a）+ FastAPI 是成熟技术栈，复杂度可控；③静态站无法做多维度筛选查询（如"OpenFOAM v2406 所有误差<5%的 run"）；④前端推荐轻量方案——直接用 FastAPI + Jinja2 + Plotly.js（不引 React/Vue），降低维护成本。 |
| **影响** | 选 A → 功能受限，无法实时查询；选 C → 两套代码维护。 |

---

## 7. P2-a 完成定义（DoD 候选）

基于推荐的 P2-a 范围（D 残差 SVG + C SQLite + P1-b 遗留小项）：

| 维度 | 完成标准 |
|---|---|
| **功能** | ①HTML report 内嵌残差下降曲线 SVG（单 case）；②`SqliteRepository` 实现 `ResultRepository` Protocol，`--storage sqlite` 可切换；③manifest 含 `cell_count`（从 blockMesh log 提取）；④manifest 含 `step_details`（每步 exit_code/wall_time/status）；⑤SQLite 支持按 case_id/solver/status/时间范围查询 run。 |
| **测试** | pytest 总数 ≥ **205**（178 + ~27 新测试）；覆盖率 ≥ **88%**；P0+P1-a+P1-b 回归全过。 |
| **兼容性** | JSON manifest 仍可用（`--storage json` 默认）；SQLite 与 JSON 可互转（migration 脚本）。 |
| **文档** | 更新 README：SQLite 用法 + SVG 报告示例；更新 Architecture 文档。 |
| **CI** | CI 默认用 JSON 存储（保持轻量）；SVG 生成测试不依赖真实 solver。 |

---

## 8. 待明确事项

| # | 事项 | 说明 |
|---|---|---|
| 1 | **SVG 渲染库选择** | 纯 Python 手写 SVG（零依赖）vs `matplotlib savefig(svg)`（~已有依赖）。PM 倾向 matplotlib（项目可能已装）。待架构师确认依赖现状。 |
| 2 | **SQLite 文件位置** | 默认 `runs/cfdb.db` vs `~/.cfdb/cfdb.db`（用户级）vs 可配置。PM 建议 `runs/cfdb.db`（与 JSON 同目录，便于迁移），CLI `--db-path` 可覆盖。 |
| 3 | **NACA0012 几何生成方式** | Python `numpy` 直接算 NACA 4-digit 坐标（简单）vs 调外部工具（如 `pygeo`）。PM 建议 numpy 直接算，零依赖。 |
| 4 | **AirfRANS 数据集获取** | 官方 HuggingFace 下载 vs 打包到 DVC remote。PM 建议 DVC remote（与网格统一管理）。 |
| 5 | **surrogate 物理一致性指标范围** | PRD-v1 §3.4 提到 divergence error / boundary violation / OOD 泛化 / inference latency。P2-c 是否全做还是选 2-3 个？PM 建议先做 divergence error + inference latency（最核心），其余推 P3。 |

---

*文档结束。§6 的 10 个待确认问题需转总拍板后，架构师据此做 P2-a 增量设计，工程师据此实现。*
