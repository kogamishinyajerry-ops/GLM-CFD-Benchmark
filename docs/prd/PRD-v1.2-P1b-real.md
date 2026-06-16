# CFD-Benchmark PRD v1.2 — P1-b 增量（真实 Solver 接入）

## 1. 文档信息

| 项 | 内容 |
|---|---|
| 版本 | v1.2（增量） |
| 日期 | 2026-06-16 |
| 作者 | 许清楚（Xu）· 产品经理 |
| 状态 | **Confirmed**（转总已确认全部 10 个待确认问题，2026-06-16） |
| 基于 | PRD-v1（v1.0，已确认）+ PRD-v1.1（v1.1，已确认）+ Architecture-v1（v1.0）+ Architecture-v1.1（v1.1） |
| 决策来源 | PRD-v1 §4.2 P1 范围定义 + Q1 三步走节奏（P0 → P1-a dry_run → P1-b 真实执行） |
| 基线 | P0 已交付（commit `5c9948e`，112 测试）+ P1-a 已交付（commit `4d67403`，测试 ≥130） |
| 工作目录 | `D:\GLM-CFD-Benchmark` |

> **本文档只描述 P1-a 之上的变更部分（delta）。** 架构师/工程师在实现前应先通读 PRD-v1、PRD-v1.1、Architecture-v1、Architecture-v1.1。

---

## 2. 范围声明（P1-b 边界 — CRITICAL）

> P1-b 是真实 solver 接入阶段，涉及大量架构方向决策。以下三段式划分需转总拍板。

### 2.1 确定做（高确定性，除非转总否决）

| # | 范围项 | 说明 |
|---|---|---|
| 1 | **OpenFOAM adapter 真实执行** | 去掉 `run()` 的 `NotImplementedError`，实现真实 subprocess 调用（按 `SolverConfig.steps` 顺序执行 blockMesh → simpleFoam/icoFoam），通过 `LocalExecutionBackend` 执行 |
| 2 | **SU2 adapter 真实执行** | 去掉 `run()` 的 `NotImplementedError`，实现真实 subprocess 调用（SU2_CFD），通过 `LocalExecutionBackend` 执行 |
| 3 | **lid-driven cavity 真实 case 跑通** | 在装了 OpenFOAM 的环境下，`blockMesh` → `icoFoam` 端到端跑通 Re=100 层流，status=success |
| 4 | **残差日志解析（最小）** | 从 OpenFOAM/SU2 log 文件中正则提取 final residual，写入 manifest |
| 5 | **QoI 后处理（最小）** | lid-driven cavity 的 centerline umax 提取（从 OpenFOAM 输出读取），与 Ghia 1982 参考数据对比 |
| 6 | **RunManifest schema 扩展** | 新增 `final_residuals: dict[str, float] \| None` + `solver_version: str \| None` 字段 |
| 7 | **CommandStep.critical 生效** | P1-a 声明但未生效的 `critical` 字段在真实执行时生效：critical=True 的 step 失败则整个 run 失败 |

### 2.2 待确认（需转总决策 — 见 §6 待确认问题）

| # | 范围项 | 关联问题 | 默认建议 |
|---|---|---|---|
| A | Docker backend | Q1 | 建议推 P2，MVP 只本机 solver |
| B | DVC 大文件管理 | Q3 | 建议小网格手工放置，P2 启 DVC |
| C | flat plate 真实 case | Q2 | 建议纳入（2 个 case 覆盖内流+外流） |
| D | 残差曲线 SVG 绘制 | Q4 | 建议推 P2 |
| E | OpenFOAM 版本锁定 | Q7 | 建议 OpenCFD v2406 |
| F | CI 真实 solver 测试策略 | Q8 | 建议 CI 保持轻量，真实 run 本地手测 |
| G | Windows 用户体验 | Q9 | 建议不支持 Windows 原生 |

### 2.3 推迟 P2（明确不做）

| # | 推迟项 | 推迟原因 |
|---|---|---|
| 1 | NACA0012 真实 case | 需翼型几何生成 + 网格生成，工作量大 |
| 2 | 场数据对比（VTK 读取 + RMSE） | 依赖 pyvista，后处理复杂度高 |
| 3 | 并行执行（decomposePar / SU2 MPI） | 需 MPI 环境配置，调试复杂 |
| 4 | GCI 网格收敛指数 | 需多级网格 + Richardson 外推 |
| 5 | Web Dashboard | P2 独立模块 |
| 6 | SQLite Repository | P2 存储层切换 |

### 2.4 铁律（不可违反）

| # | 铁律 |
|---|---|
| 1 | **不破坏 P0 + P1-a 的测试**——P1-b 合并后 `pytest` 全部通过（≥130 测试 + 新增测试） |
| 2 | **不改 P0/P1-a schema 已有字段**——只新增 Optional 字段（详见 §4 变更矩阵） |
| 3 | **复用现有 `LocalExecutionBackend`**——真实 subprocess 执行走 P0 已有的 backend，不重新设计 |
| 4 | **dry_run 模式仍然可用且不执行 subprocess**——P1-b 不破坏 P1-a 的 dry_run 路径 |
| 5 | **真实 solver 测试不进入 CI 的 default job**——CI 仍保持无 OpenFOAM/SU2 通过（dry_run + mock 路径），真实 run 测试用 marker 标记，本地手测或可选 CI job |

---

## 3. 用户故事（增量，针对真实 solver 接入）

### US-P1b-1（CFD 工程师 — 真实运行）
> 作为 CFD 工程师，我希望 `cfdb run --case lid_driven_cavity --solver openfoam` 能在我装了 OpenFOAM 的机器上**真实跑通** lid-driven cavity，自动完成 blockMesh → icoFoam，并告诉我 centerline umax 与 Ghia 1982 参考值的相对误差，以便我不必手动跑 OpenFOAM 再用 ParaView 后处理。

### US-P1b-2（CFD 工程师 — 残差与收敛）
> 作为 CFD 工程师，我希望运行结束后 manifest 里记录 final residual（Ux、Uy、p 各分量），以便我快速判断求解是否真正收敛，而不是只看退出码 0 就认为成功。

### US-P1b-3（求解器开发者 — 版本对比）
> 作为求解器开发者，我希望 manifest 记录 solver_version（如 `OpenFOAM v2406`），以便我在不同 OpenFOAM 版本间做回归对比时能追溯每次运行用的具体版本。

### US-P1b-4（研究者 — 实验参考数据）
> 作为研究者，我希望 lid-driven cavity case 内置 Ghia 1982 的参考数据（centerline velocity profile），以便我的验证结果有权威的实验对比基准，支撑论文引用。

### US-P1b-5（CFD 工程师 — SU2 真实运行）
> 作为 CFD 工程师，我希望 SU2 adapter 也能真实跑通（flat plate 层流），与 Blasius 解析解对比 skin friction coefficient，以便我验证 SU2 在外流边界层问题上的表现。

### US-P1b-6（CI/CD 集成者 — 本机与 Docker 切换）
> 作为 CI/CD 集成者，我希望 `--backend` 参数能在 local（本机 solver）与 docker（容器化 solver）之间切换，以便我在本地开发时用本机 solver 快速验证，在 CI 环境用 Docker 保证可复现性。（**取决于 Q1 决策**）

### US-P1b-7（CFD 工程师 — 失败诊断）
> 作为 CFD 工程师，当真实运行失败时（如 blockMesh 报错、网格质量差、求解发散），我希望 manifest 记录详细的 error 信息（stderr 内容 + 失败发生在哪个 step），以便我快速定位问题而非在满屏日志中翻找。

### US-P1b-8（求解器开发者 — critical step 语义）
> 作为求解器开发者，我希望 `CommandStep.critical` 字段在真实执行时生效——blockMesh 失败应该终止整个 run（critical=True），而某些后处理 step 失败只记录 warning 不终止（critical=False），以便灵活控制多步命令的失败行为。

---

## 4. 需求池（P1-b 候选需求）

> 按确定度排序：P1b-H（高）/ P1b-M（中）/ P1b-L（待确认）。最终范围取决于 §6 待确认问题的决策。

### 4.1 P1b-H（高确定性 — 确定做）

| ID | 需求 | 说明 | 关联 US |
|---|---|---|---|
| P1b-H1 | **OpenFOAM adapter 真实 subprocess 执行** | 去掉 `run()` 第 199-203 行的 `NotImplementedError`，按 `SolverConfig.steps` 顺序调用 `LocalExecutionBackend.execute()` 执行每一步（blockMesh → icoFoam）；critical step 失败终止后续；收集每步 stdout/stderr | US-1, US-7, US-8 |
| P1b-H2 | **SU2 adapter 真实 subprocess 执行** | 同理，去掉 SU2 adapter 的 `NotImplementedError`，调用 `LocalExecutionBackend.execute()` 执行 `SU2_CFD` | US-5 |
| P1b-H3 | **lid_driven_cavity 真实 case 数据** | 提供 blockMeshDict（生成真实网格，非占位）；更新 case.yaml 的 solver 配置（icoFoam 稳态/瞬态参数）；内置 Ghia 1982 centerline velocity 参考 QoI 值 | US-1, US-4 |
| P1b-H4 | **残差日志解析（最小）** | 新增 `post/residual_parser.py`：用正则从 OpenFOAM log 提取 final residual（Ux/Uy/p），从 SU2 log 提取 RMS_DENSITY final value；写入 manifest `final_residuals` 字段 | US-2 |
| P1b-H5 | **QoI 后处理（lid-driven cavity centerline umax）** | 新增 `post/qoi_extractor.py`：从 OpenFOAM postProcessing 输出或场数据提取 centerline umax；计算与参考值的相对误差；写入 metrics | US-1 |
| P1b-H6 | **RunManifest schema 扩展** | 新增 `final_residuals: dict[str, float] \| None = None` + `solver_version: str \| None = None`（详见 §5 schema 变更） | US-2, US-3 |
| P1b-H7 | **solver_version 探测** | adapter 在真实执行前探测 solver 版本（`blockMesh -help` / `SU2_CFD -v`），写入 manifest | US-3 |
| P1b-H8 | **CommandStep.critical 语义生效** | 真实执行时，critical=True 的 step 失败 → 立即终止后续 step，status=failed；critical=False 的 step 失败 → 记录 warning 继续执行 | US-7, US-8 |

### 4.2 P1b-M（中确定性 — 待确认后决定）

| ID | 需求 | 说明 | 关联问题 |
|---|---|---|---|
| P1b-M1 | **Docker backend** | 新增 `execution/docker.py`，使用 `openfoam/openfoam` 镜像，`container_digest` 写入 manifest；`--backend docker` 切换 | Q1 |
| P1b-M2 | **DVC 大文件管理** | 启用 DVC + `.dvc` 远程存储（MinIO/S3），管理网格与参考数据 | Q3 |
| P1b-M3 | **flat_plate 真实 case** | 层流 flat plate，Blasius 解析解参考 skin friction coefficient，需真实网格 | Q2 |
| P1b-M4 | **残差曲线 SVG** | HTML report 内嵌残差下降曲线（静态 SVG），需解析完整残差历史（非仅 final value） | Q4 |

### 4.3 P1b-L（待确认 — 默认推 P2）

| ID | 需求 | 说明 | 关联问题 |
|---|---|---|---|
| P1b-L1 | **NACA0012 真实 case** | 需翼型几何生成（NACA 4-digit）+ 网格生成（snappyHexMesh / SU2 mesh），工作量大 | Q2 |
| P1b-L2 | **场数据对比（VTK RMSE）** | 依赖 pyvista 读 VTK，计算全场 RMSE，后处理复杂度高 | Q4 |
| P1b-L3 | **并行执行** | OpenFOAM decomposePar / SU2 MPI，需 MPI 环境配置 | — |
| P1b-L4 | **Windows 原生支持** | Windows 装本机 OpenFOAM | Q9 |

---

## 5. Schema 变更矩阵（P1-a → P1-b）

> **铁律 #2 约束**：全部为新增 Optional 字段，不删除/不重命名 P0/P1-a 已有字段。

### 5.1 `RunManifest` 增量

```python
class RunManifest(BaseModel):
    # ... P0/P1-a 已有字段全部不变 ...
    # run_id, case_id, solver, backend, status, timing, host, artifacts,
    # git_commit, container_digest, error, cli_args, dry_run_skipped_commands

    # === P1-b 新增 ===
    final_residuals: dict[str, float] | None = None
    """Final residual values extracted from solver log.
    Keys are field names (e.g. 'Ux', 'Uy', 'p' for OpenFOAM;
    'RMS_DENSITY' for SU2). Values are the final residual magnitudes.
    None for dry_run / mock cases."""

    solver_version: str | None = None
    """Detected solver version string (e.g. 'OpenFOAM v2406', 'SU2 8.0.0').
    None for dry_run / mock cases."""
```

**向后兼容**：两个字段默认 `None`，P0/P1-a 的 manifest 反序列化不受影响。

### 5.2 `ArtifactManifest` 扩展（adapters/base.py）

P1-a 的 `ArtifactManifest` 已有 `qoi_values` 和 `curves` 字段。P1-b 利用这些字段：

- `qoi_values`：真实 case 的 QoI 提取器填充（如 `{"centerline_umax": 0.374}`）
- `curves`：残差历史曲线（P1b-M4 若启用）填充

不需要新增字段，只需在 adapter 的 `collect_outputs` 中实际填充这些字段（P1-a 留了 None 占位）。

### 5.3 `CommandStep.critical` 生效（行为变更，非 schema 变更）

P1-a 的 `CommandStep.critical: bool = True` 字段定义已存在，但 dry_run 模式下不生效。P1-b 真实执行时该字段生效——这不是 schema 变更，是 adapter `run()` 方法的行为变更。

---

## 6. 待确认问题（Top 10 — CRITICAL，本次 PRD 最关键部分）

> 每个问题按重要性排序。转总拍板后，架构师据此做增量设计，工程师据此实现。

### ⭐⭐⭐ Q1. Docker vs 本机 solver：MVP 必须用哪个？

| | |
|---|---|
| **选项 A** | MVP 只支持本机 solver（Linux/macOS/WSL2 装 OpenFOAM），Docker 推 P2 |
| **选项 B** | MVP 双轨支持（本机 + Docker backend），用户通过 `--backend` 切换 |
| **选项 C** | MVP 强制 Docker（保证环境复现，container_digest 写入 manifest） |
| **影响** | 架构是否要在 P1-b 实现 `DockerBackend` 类（Architecture-v1 §14 演进预留点已标注）；CI 策略；用户门槛 |
| **PM 建议** | **A** — MVP 只本机 solver，Docker 推 P2。理由：①P1-b 核心价值是验证"真实 solver 能跑通 + QoI 提取正确"，本机 solver 已足够验证；②Docker backend 需处理镜像拉取/挂载/cleanup，增加复杂度；③Architecture-v1 §14 已预留 Docker 扩展点，P2 接入零改动业务代码；④降低用户首次使用门槛（不需要装 Docker） |

**决策**：**A — MVP 只本机 solver，Docker 推 P2**。

### ⭐⭐⭐ Q2. 真实 case 覆盖范围：MVP 上几个？

| | |
|---|---|
| **选项 A** | 只 lid-driven cavity（最小，1 个 case 验证全链路） |
| **选项 B** | lid-driven cavity + flat plate（2 个 case，覆盖内流 + 外流） |
| **选项 C** | lid-driven cavity + flat plate + NACA0012（3 个，但 NACA0012 需几何生成 + 网格生成） |
| **影响** | 工作量（NACA0012 的几何/网格生成可能是 lid-driven cavity 的 3-5 倍）；验证维度覆盖（内流 vs 外流） |
| **PM 建议** | **B** — 2 个 case。理由：①lid-driven cavity 验证内流 + 经典验证基准（Ghia 1982）；②flat plate 验证外流 + Blasius 解析解（verification 层）；③两者网格生成都简单（blockMesh / 结构化网格）；④NACA0012 需 snappyHexMesh 或 SU2 mesh generator，调试成本高，推 P2 |

**决策**：**B — lid-driven cavity + flat plate（2 个 case）**。

### ⭐⭐⭐ Q3. DVC 是否在 P1-b 启用？

| | |
|---|---|
| **选项 A** | P1-b 启用 DVC，配 MinIO/S3 远程存储 |
| **选项 B** | P1-b 手工放置网格（仓库内放小网格 <1MB），P2 启 DVC |
| **选项 C** | P1-b 用 Git LFS（GitHub 1GB/月免费额度可能不够） |
| **影响** | PRD-v1 Q2 原决策是"MVP(P1) 直接上 DVC"，但实际 lid-driven cavity 的 blockMeshDict 生成网格 <100KB，是否需要 DVC 值得重新评估 |
| **PM 建议** | **B** — 小网格手工放置。理由：①lid-driven cavity blockMeshDict 生成的网格 <100KB，完全可以直接进 git；②flat plate 结构化网格也 <1MB；③DVC 需要 `.dvc` 远程存储配置（MinIO/S3），增加基础设施依赖；④Architecture-v1 §14 已预留 DVC 扩展点（`CaseSpec.mesh.source` 字段），P2 接入零改动。**注意：这与 PRD-v1 Q2 原决策不同，需转总重新确认** |

**决策**：**B — 小网格手工放置，P2 启 DVC**。（覆盖 PRD-v1 Q2 原决策"MVP 直接上 DVC"，原因：实际网格 <1MB 不值得基础设施开销）

### ⭐⭐ Q4. 后处理范围：MVP 做到哪一步？

| | |
|---|---|
| **选项 A** | 只做 QoI 提取（centerline umax / skin friction coeff）+ 残差 final value |
| **选项 B** | A + 残差曲线 SVG（HTML report 内嵌） |
| **选项 C** | B + 场数据对比（VTK 读取 + RMSE，依赖 pyvista） |
| **影响** | 后处理复杂度；新依赖（pyvista 是重依赖） |
| **PM 建议** | **A** — 最小后处理。理由：①QoI 提取 + final residual 已满足 V&V 的核心需求（"求解了正确的方程吗"+"收敛了吗"）；②残差曲线 SVG 是可视化增强，不影响 benchmark 判定，推 P2；③场数据 RMSE 依赖 pyvista（~200MB），增加安装负担，推 P2 |

**决策**：**A — 只做 QoI 提取 + final residual**（残差曲线 SVG / 场 RMSE 推 P2）。

### ⭐⭐ Q5. 残差解析实现方式？

| | |
|---|---|
| **选项 A** | 用正则从 OpenFOAM/SU2 log 文件 grep（无新依赖） |
| **选项 B** | 用第三方库（如 `fluidfoam` for OpenFOAM） |
| **选项 C** | 调用 solver 自带 postProcess（如 `simpleFoam -postProcess -func residuals`） |
| **影响** | 依赖管理；解析鲁棒性；OpenFOAM 版本兼容性 |
| **PM 建议** | **A** — 正则 grep。理由：①无新依赖，降低安装负担；②OpenFOAM log 格式稳定（`Solving for Ux, Initial residual = ...`），正则可靠；③fluidfoam 是非主流库，维护风险；④postProcess 方式需要额外 subprocess 调用，且不同 solver 参数不同 |

**决策**：**A — 正则 grep**（无新依赖）。

### ⭐⭐ Q6. QoI 提取实现方式？

| | |
|---|---|
| **选项 A** | OpenFOAM 用 `postProcess -func probes`（需 case 配 probes）+ SU2 用 Python 直接读输出 |
| **选项 B** | 用 `fluidfoam` / `meshio` 读场数据后 Python 计算 |
| **选项 C** | 用 SU2 的 `SU2_PY` / Python 直接读 SU2 输出 |
| **影响** | OpenFOAM probes 需在 controlDict 配 probe 位置；Python 读场数据需 meshio/pyvista |
| **PM 建议** | **A（OpenFOAM）+ C（SU2）**。理由：①OpenFOAM probes 是原生功能，在 controlDict 中配 probe 位置（x=0.5 vertical line），自动输出 centerline velocity；②SU2 输出 CSV 格式的 surface/volume 数据，Python 直接读取即可；③避免引入 meshio/pyvista 重依赖 |

**决策**：**A（OpenFOAM probes）+ C（SU2 Python 直读）**。

### ⭐⭐ Q7. OpenFOAM 版本：OpenCFD（openfoam.com）还是 Foundation（openfoam.org）？

| | |
|---|---|
| **选项 A** | OpenCFD v2406（openfoam.com，Docker 镜像 `openfoam/openfoam`） |
| **选项 B** | Foundation 11/12（openfoam.org，Docker 镜像 `openfoam/openfoam7-11`） |
| **影响** | controlDict / fvSchemes 语法差异（如 `application` 字段值不同）；probes 配置语法差异；Docker 镜像选择 |
| **PM 建议** | **A** — OpenCFD v2406。理由：①Docker 镜像 `openfoam/openfoam` 更主流、维护更积极；②Architecture-v1.1 模板中已硬编码 `v2312` 版本头（OpenCFD 风格），兼容性更好；③icoFoam/simpleFoam 在两版间 API 差异小，但 OpenCFD 更新更快 |

**决策**：**A — OpenCFD v2406（openfoam.com）**。

### ⭐ Q8. CI 环境：P1-b 测试如何在 GitHub Actions 上跑？

| | |
|---|---|
| **选项 A** | CI 只跑 P0+P1-a（mock + dry_run），P1-b 真实 run 用本地手测 + 文档说明 |
| **选项 B** | CI 用 Docker backend 跑真实 case（GitHub Actions 支持 docker） |
| **选项 C** | CI 加 self-hosted runner（用户自己装 OpenFOAM） |
| **影响** | CI 运行时间（OpenFOAM case 可能跑几分钟）；CI 镜像大小；QA 流程 |
| **PM 建议** | **A** — CI 保持轻量。理由：①P0+P1-a 的 mock + dry_run 已覆盖平台逻辑回归；②真实 solver 测试关注的是"solver 输出格式正确 + QoI 提取正确"，用 pytest fixture（模拟 log 文件 + 模拟 probes 输出）即可单元测试；③CI 装 OpenFOAM 会大幅增加运行时间（apt install openfoam ~5min）和镜像大小；④真实端到端测试通过文档说明 + 本地手测 checklist 保证（见 §7 验收） |

**决策**：**A — CI 保持轻量**（真实 run 用 pytest fixture 单测 + 本地手测）。

### ⭐ Q9. Windows 用户体验：本机 solver 怎么搞？

| | |
|---|---|
| **选项 A** | 强制 Windows 用户用 WSL2 + Linux OpenFOAM |
| **选项 B** | Windows 用户用 Docker Desktop |
| **选项 C** | P1-b 不支持 Windows 本机，只 Linux/macOS/WSL2/Docker |
| **影响** | 用户文档；安装指南；支持矩阵 |
| **PM 建议** | **C** — 明确不支持 Windows 原生。理由：①OpenFOAM 官方不支持 Windows 原生（只有 WSL2/Docker）；②Architecture-v1 §5 已声明"Linux 优先"；③文档明确建议 WSL2 或 Docker，降低支持成本 |

**决策**：**C — P1-b 不支持 Windows 原生**（建议 WSL2/Docker）。

### ⭐ Q10. manifest schema 是否需要扩展真实运行的字段？

| | |
|---|---|
| **候选新增字段** | `final_residuals: dict[str, float] \| None` / `solver_version: str \| None` / `cell_count: int \| None` |
| **影响** | manifest 信息完整度；可复现性追溯 |
| **PM 建议** | **加 `solver_version` + `final_residuals`，`cell_count` 推 P2**。理由：①solver_version 对可复现性至关重要（不同版本结果可能不同）；②final_residuals 让用户判断收敛质量；③cell_count 需解析 blockMesh 输出或 mesh 文件，额外工作量，推 P2。§5.1 已按此设计 |

**决策**：**加 `solver_version` + `final_residuals`，`cell_count` 推 P2**。

---

## 7. UI 设计稿（增量）

### 7.1 CLI 真实运行输出示意（lid-driven cavity + OpenFOAM + local）

```
$ cfdb run --case lid_driven_cavity --solver openfoam --backend local

[run:20260616T150000Z_lid_driven_cavity_openfoam_a1b2c3d4] backend=local solver=openfoam
[run:...] detecting solver version... OpenFOAM v2406
[run:...] step 1/2: blockMesh
[run:...]   blockMesh: 1240 cells, 0.8s, exit_code=0
[run:...] step 2/2: icoFoam
[run:...]   icoFoam: 1000 iterations, converged at iter 856
[run:...] final residual: Ux=1.2e-6, Uy=2.1e-6, p=3.4e-5
[run:...] QoI: centerline_umax=0.374 (ref=0.371, err=0.81%)
[run:...] status=success, wall=14.2s
[run:...] manifest → runs/20260616T150000Z_lid_driven_cavity_openfoam_a1b2c3d4/manifest.json
============================================================
Run ID:    20260616T150000Z_lid_driven_cavity_openfoam_a1b2c3d4
Case:      lid_driven_cavity
Solver:    openfoam (v2406)
Backend:   local
Status:    success
Wall Time: 14.2s
Residuals: Ux=1.2e-6, Uy=2.1e-6, p=3.4e-5
QoI:       centerline_umax=0.374 (ref=0.371, err=0.81%) [PASS ≤5%]
============================================================
```

### 7.2 CLI 真实运行失败示意（blockMesh 失败）

```
$ cfdb run --case lid_driven_cavity --solver openfoam --backend local

[run:20260616T150100Z_lid_driven_cavity_openfoam_e5f6g7h8] backend=local solver=openfoam
[run:...] step 1/2: blockMesh
[run:...]   blockMesh: FAILED (exit_code=1, 0.3s)
[run:...]   stderr: --> FOAM FATAL IO ERROR: cannot find file.../system/blockMeshDict
[run:...] step 2/2: SKIPPED (previous critical step failed)
[run:...] status=failed, wall=0.4s
[run:...] manifest → runs/20260616T150100Z_lid_driven_cavity_openfoam_e5f6g7h8/manifest.json
============================================================
Run ID:    20260616T150100Z_lid_driven_cavity_openfoam_e5f6g7h8
Case:      lid_driven_cavity
Solver:    openfoam
Backend:   local
Status:    FAILED
Error:     step 'block_mesh' failed with exit_code=1
============================================================
[exit code: 1]
```

### 7.3 Docker backend 输出示意（若 Q1 选 B/C）

```
$ cfdb run --case lid_driven_cavity --solver openfoam --backend docker --image openfoam/openfoam:v2406

[run:...] pulling openfoam/openfoam:v2406 (digest: sha256:abc123...)
[run:...] container started, mounting run_dir at /tmp/cfdb_run
[run:...] step 1/2: blockMesh (inside container)
[run:...]   blockMesh: 1240 cells, 1.2s, exit_code=0
[run:...] step 2/2: icoFoam (inside container)
[run:...]   icoFoam: 1000 iterations, converged
[run:...] manifest.container_digest=sha256:abc123...
[run:...] status=success, wall=18.5s
```

### 7.4 真实运行 manifest.json 示例（关键字段增量）

```json
{
  "run_id": "20260616T150000Z_lid_driven_cavity_openfoam_a1b2c3d4",
  "case_id": "lid_driven_cavity",
  "solver": "openfoam",
  "backend": "local",
  "status": "success",
  "timing": {
    "wall_time_sec": 14.2,
    "start_time": "2026-06-16T15:00:00.000000+00:00",
    "end_time": "2026-06-16T15:00:14.200000+00:00"
  },
  "solver_version": "OpenFOAM v2406 (build 7cf83b7)",
  "final_residuals": {
    "Ux": 1.2e-6,
    "Uy": 2.1e-6,
    "p": 3.4e-5
  },
  "artifacts": {
    "case/system/controlDict": "case/system/controlDict",
    "case/0.5/U": "case/0.5/U",
    "case/0.5/p": "case/0.5/p",
    "case/postProcessing/probes/0/U": "case/postProcessing/probes/0/U",
    "case/log.blockMesh": "case/log.blockMesh",
    "case/log.icoFoam": "case/log.icoFoam"
  },
  "error": null
}
```

### 7.5 HTML Report 增量（真实运行模式）

在 P0/P1-a 的 HTML report 基础上增加以下模块（真实运行 status=success 时才显示）：

```
┌─────────────────────────────────────────────────────────────┐
│ Solver Info                                                 │
│  • solver_version: OpenFOAM v2406                           │
│  • cell_count: 1240 cells (from blockMesh log)             │
├─────────────────────────────────────────────────────────────┤
│ Convergence (Final Residuals)                               │
│  • Ux: 1.2e-06   • Uy: 2.1e-06   • p: 3.4e-05             │
├─────────────────────────────────────────────────────────────┤
│ QoI 误差表（真实数据，非 dry_run 占位）                     │
│ ┌─────────────────┬──────────┬──────────┬────────────┐      │
│ │ QoI             │ 计算值   │ 参考值   │ 相对误差   │      │
│ ├─────────────────┼──────────┼──────────┼────────────┤      │
│ │ centerline_umax │ 0.374    │ 0.371    │ 0.81% ✓    │      │
│ └─────────────────┴──────────┴──────────┴────────────┘      │
│ 参考: Ghia, Ghia & Shin (1982), Re=100                      │
├─────────────────────────────────────────────────────────────┤
│ Artifacts                                                   │
│  • manifest.json  • case/log.icoFoam  • case/0.5/U  • ...   │
└─────────────────────────────────────────────────────────────┘
```

---

## 8. 验收 Checklist（P1-b 候选）

> 根据转总确认的范围动态调整。以下为 P1b-H（高确定性）范围的验收项。

### 8.1 功能验收（真实运行 — 需要装 OpenFOAM 的环境）

- [ ] `cfdb run --case lid_driven_cavity --solver openfoam --backend local` 在装了 OpenFOAM 的环境跑通，`status=success`
- [ ] 上述运行的 manifest 含 `solver_version`（非 None）+ `final_residuals`（含 Ux/Uy/p 三个 key，值均 < 1e-3）
- [ ] 上述运行的 metrics 含 `centerline_umax` 的相对误差，且 < 5%（容差内 PASS）
- [ ] blockMesh → icoFoam 两步均执行（log.blockMesh + log.icoFoam 存在）
- [ ] `cfdb run --case lid_driven_cavity --solver openfoam --dry-run` 仍然正常工作（P1-a dry_run 路径不被破坏）
- [ ] critical step 失败场景：模拟 blockMesh 失败（如删 blockMeshDict），status=failed，error 含 step 名称
- [ ] （若 Q2 选 B）`cfdb run --case flat_plate --solver openfoam`（或 su2）跑通，skin friction coeff 与 Blasius 解对比

### 8.2 单元测试验收（不依赖真实 solver）

- [ ] `post/residual_parser.py` 单元测试：用 fixture log 文件（模拟 OpenFOAM/SU2 输出）验证正则解析正确
- [ ] `post/qoi_extractor.py` 单元测试：用 fixture probes 输出验证 centerline umax 提取正确
- [ ] `OpenFOAMAdapter.run()` 真实执行路径测试：mock `LocalExecutionBackend.execute()` 验证多步命令序列调用顺序 + critical step 失败处理
- [ ] `SU2Adapter.run()` 真实执行路径测试：同上
- [ ] solver_version 探测测试：mock subprocess 验证版本字符串解析
- [ ] schema 变更测试：`RunManifest(final_residuals=..., solver_version=...)` 构造合法；旧 manifest 反序列化不受影响

### 8.3 回归验收

- [ ] **P0 + P1-a 回归**：全部 ≥130 测试通过，零破坏
- [ ] `cfdb list-cases` 列出所有 case（mock + dry_run case + 真实 case）
- [ ] dry_run 路径完全不受影响（不调用真实 subprocess）

### 8.4 质量验收

- [ ] pytest 总数 ≥ **150**（130 + 至少 20 个新测试）
- [ ] 覆盖率 ≥ **80%**（CI gate 不降）
- [ ] `ruff check .` 无错误
- [ ] `pyright` basic mode 无错误

---

## 9. 风险提示

| # | 风险 | 影响 | 缓解措施 |
|---|---|---|---|
| 1 | **真实 solver 测试无法在 CI 自动化**（除非 Docker backend） | QA 流程需调整；回归依赖本地手测 | 真实 run 逻辑用 mock backend 单元测试覆盖；端到端用 §8.1 手测 checklist + 文档说明；P2 接 Docker backend 后 CI 可自动跑 |
| 2 | **不同 OpenFOAM 版本 API 差异** | controlDict 字段、probes 语法、log 格式可能不同 | 锁定 Q7 推荐版本（OpenCFD v2406）；文档注明测试版本；残差解析正则预留版本兼容注释 |
| 3 | **网格生成参数对结果影响大** | blockMesh 网格密度直接影响 centerline umax 精度 | 提供已验证的 blockMeshDict 参数（教程级 20×20 网格，与 Ghia 1982 对比过的参数）；§8 验收容差 5% 已留裕度 |
| 4 | **QoI 提取依赖 probes 配置** | controlDict 中 probes 位置配错会导致提取失败 | case.yaml 中明确声明 probe 坐标（x=0.5, y 从 0 到 L）；单元测试用 fixture 验证提取逻辑 |
| 5 | **solver 未安装时的用户体验** | 用户没装 OpenFOAM 就跑真实 run → 报错 | adapter 在执行前检查 solver 可执行文件是否存在（`which blockMesh`），不存在时给出友好错误提示（而非 subprocess FileNotFoundError） |
| 6 | **P1-b 范围蔓延** | 如果 Docker + DVC + NACA0012 全做，P1-b 可能膨胀为 2-3 周工作量 | 严格执行 §2.1 确定做范围；待确认项（§2.2）逐个拍板；不确定的推 P2 |

---

## 附录 A：P1-b 涉及的新增/变更文件清单（预估）

> 最终范围取决于 §6 待确认问题决策。以下为 P1b-H（高确定性）范围的文件清单。

| 类型 | 文件路径 | 变更说明 |
|---|---|---|
| **变更** | `src/cfdb/schema.py` | `RunManifest` 加 `final_residuals` + `solver_version` Optional 字段 |
| **变更** | `src/cfdb/adapters/openfoam.py` | `run()` 去掉 NotImplementedError，实现真实 subprocess 执行（按 steps 顺序，critical step 失败终止）；`collect_outputs` 填充 qoi_values；新增 solver_version 探测 |
| **变更** | `src/cfdb/adapters/su2.py` | 同上（SU2 真实执行） |
| **变更** | `src/cfdb/core/runner.py` | manifest 构建写入 `final_residuals` + `solver_version`（从 adapter/RunResult 读取） |
| **变更** | `src/cfdb/adapters/base.py` | `RunResult` 加 `step_results: list[StepResult] \| None`（记录每步执行详情）—— *待架构师确认是否需要* |
| **新增** | `src/cfdb/post/__init__.py` | 后处理子包 |
| **新增** | `src/cfdb/post/residual_parser.py` | 残差日志正则解析（OpenFOAM + SU2） |
| **新增** | `src/cfdb/post/qoi_extractor.py` | QoI 提取（centerline umax / skin friction coeff） |
| **变更** | `cases/validation/lid_driven_cavity/case.yaml` | 更新 solver 配置（真实 blockMeshDict 路径 + probes 配置 + Ghia 1982 参考 QoI） |
| **新增** | `cases/validation/lid_driven_cavity/system/blockMeshDict` | blockMeshDict 模板（或 Jinja2 模板） |
| **新增** | `cases/validation/lid_driven_cavity/reference/ghia1982_centerline.json` | Ghia 1982 参考数据 |
| **变更** | `src/cfdb/reporting/templates/report.html.j2` | HTML report 增加 solver_version + final_residuals 模块 |
| **新增** | `tests/test_residual_parser.py` | 残差解析单元测试（fixture log 文件） |
| **新增** | `tests/test_qoi_extractor.py` | QoI 提取单元测试（fixture probes 输出） |
| **新增** | `tests/test_openfoam_real_run.py` | OpenFOAM 真实执行路径测试（mock backend） |
| **新增** | `tests/test_su2_real_run.py` | SU2 真实执行路径测试（mock backend） |
| **新增** | `tests/fixtures/openfoam_log_sample.txt` | OpenFOAM log 样本（测试 fixture） |
| **新增** | `tests/fixtures/su2_log_sample.txt` | SU2 log 样本（测试 fixture） |

---

## 附录 B：P1-b 真实执行数据流（OpenFOAM lid-driven cavity 示例）

```
CLI: cfdb run --case lid_driven_cavity --solver openfoam --backend local
  │
  ▼
Runner.execute(case_id="lid_driven_cavity", solver="openfoam", dry_run=False)
  │
  ├── adapter = OpenFOAMAdapter(dry_run=False)
  │
  ├── Phase 1: adapter.prepare(case, case_dir, run_dir)
  │     ├── 创建 run_dir/case/system/
  │     ├── 创建 run_dir/case/constant/
  │     ├── 创建 run_dir/case/0/
  │     ├── 渲染 controlDict.j2（含 probes 配置）→ system/controlDict
  │     ├── 渲染 fvSchemes.j2 → system/fvSchemes
  │     ├── 渲染 fvSolution.j2 → system/fvSolution
  │     ├── 生成 blockMeshDict → system/blockMeshDict    ← P1-b 新增
  │     ├── 渲染 transportProperties → constant/transportProperties
  │     └── 渲染 turbulenceProperties → constant/turbulenceProperties
  │
  ├── Phase 1.5: solver_version 探测                            ← P1-b 新增
  │     └── subprocess: blockMesh -help → parse → "OpenFOAM v2406"
  │
  ├── Phase 2: adapter.run(case, case_dir, run_dir, resources)
  │     ├── steps = [block_mesh, solve]
  │     ├── Step 1: block_mesh (critical=True)
  │     │     ├── render: "blockMesh -case {run_dir}/case"
  │     │     ├── LocalExecutionBackend.execute(["blockMesh", "-case", ...])
  │     │     ├── subprocess → exit_code=0, stdout → log.blockMesh
  │     │     └── step result: success
  │     ├── Step 2: solve (critical=True)
  │     │     ├── render: "icoFoam -case {run_dir}/case"
  │     │     ├── LocalExecutionBackend.execute(["icoFoam", "-case", ...])
  │     │     ├── subprocess → exit_code=0, stdout → log.icoFoam
  │     │     └── step result: success
  │     └── return RunResult(exit_code=0, stdout=..., step_results=[...])
  │
  ├── Phase 3: adapter.collect_outputs(case, run_dir)
  │     ├── 扫描 run_dir/case/ 所有文件 → files dict
  │     ├── post/residual_parser.py: 解析 log.icoFoam → final_residuals
  │     │     {Ux: 1.2e-6, Uy: 2.1e-6, p: 3.4e-5}
  │     ├── post/qoi_extractor.py: 读 postProcessing/probes → qoi_values
  │     │     {centerline_umax: 0.374}
  │     └── return ArtifactManifest(files=..., qoi_values={centerline_umax: 0.374})
  │
  ├── Phase 4: MetricsEngine.compute(...)
  │     ├── qoi_relative_errors = {centerline_umax: 0.0081}  (|0.374-0.371|/0.371)
  │     ├── qoi_pass = True (0.81% < 5% tolerance)
  │     └── overall_status = "pass"
  │
  ├── Phase 5: 构建 RunManifest
  │     ├── status = "success"
  │     ├── solver_version = "OpenFOAM v2406"
  │     ├── final_residuals = {Ux: 1.2e-6, Uy: 2.1e-6, p: 3.4e-5}
  │     └── artifacts = {case/log.icoFoam, case/0.5/U, ...}
  │
  └── Phase 6: repo.save_run(manifest, metrics)
        └── 写入 runs/<run_id>/manifest.json
```

---

## 附录 C：Ghia 1982 参考数据（lid-driven cavity Re=100）

| y/H | Ux/U_lid (Ghia 1982) | 说明 |
|---|---|---|
| 1.0000 | 1.00000 | lid（顶盖） |
| 0.9766 | 0.84123 | |
| 0.9688 | 0.78871 | |
| 0.9609 | 0.73722 | |
| 0.9531 | 0.68717 | |
| ... | ... | 完整数据见 reference 文件 |
| 0.5000 | 0.05702 | 中心线 |
| ... | ... | |
| 0.0000 | 0.00000 | 底壁 |

**关键 QoI**：centerline vertical location 的最大水平速度（umax）。
- Ghia 1982 Re=100: **umax ≈ 0.371**（在 y/H ≈ 0.155 附近）
- case.yaml 容差: 5%

---

*文档结束。架构师请基于此 PRD 进行增量设计 + 任务分解，工程师照任务列表实现。待确认问题（§6）需在架构设计前由转总拍板。*
