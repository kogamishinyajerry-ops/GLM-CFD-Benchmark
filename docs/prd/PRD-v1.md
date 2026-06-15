# CFD-Benchmark PRD v1

## 1. 文档信息

| 项 | 内容 |
|---|---|
| 版本 | v1.0 |
| 日期 | 2026-06-16 |
| 作者 | 许清楚（Xu）· 产品经理 |
| 状态 | **Confirmed**（转总已确认 11 个待确认问题，2026-06-16） |
| 工作目录 | `D:\GLM-CFD-Benchmark` |
| 原始需求来源 | 转总提供的需求文档 + 分阶段提示词 |

---

## 2. 产品目标

CFD-Benchmark 是一个**开源的 CFD 求解器/仿真方案 benchmark 平台**，致力于解决当前 CFD 验证与确认（Verification & Validation, V&V）领域长期存在的四个痛点：

1. **V&V 标准化缺失**：参照 NASA / ASME V&V 10/20 的方法论，`verification`（数值实现是否正确，"求解了正确的方程吗？"）与 `validation`（模型是否匹配物理现实，"求解的是正确的方程吗？"）必须分层评估。平台通过五类 case 分层（smoke / verification / validation / performance / surrogate）强制规范化。
2. **可复现性差**：典型 CFD 论文/报告难以复现（求解器版本、网格、边界条件、求解参数不可追溯）。平台用 `RunManifest` 绑定 solver version + container digest + git commit + mesh hash，保证每次运行可唯一标识与复现。
3. **多 solver 横向对比无统一框架**：OpenFOAM / SU2 / Fluent / 自研 / ML surrogate 各自为政，同一 case 在不同 solver 上无法公平对比。平台通过 Solver Adapter 抽象层（Protocol）统一 `run → post-process → metric` 流水线。
4. **ML surrogate CFD 模型缺乏可信评测**：近年来 AirfRANS 等 neural surrogate 涌现，但缺乏与经典 CFD 在同一 case 集上的可复现对比（准确度 / 物理一致性 / 泛化 / 推理速度）。平台 `surrogate` case 层专门承接此需求。

**平台一句话定位**：让任何 CFD 方案（传统求解器或 ML surrogate）在同一组标准化 case 上被可复现地评测、对比、回归。

---

## 3. 用户故事

### 3.1 CFD 工程师（日常使用求解器做工程项目）
- 作为 CFD 工程师，我希望 `cfdb run` 一键跑通一组标准 case，以便快速评估我正在评估的求解器在我关心的流动类型上的表现。
- 作为 CFD 工程师，我希望报告自动给出 QoI 相对误差与残差下降曲线，以便我不必手工后处理每个 case。
- 作为 CFD 工程师，我希望 case manifest 明确记录网格、边界条件、求解参数，以便我能向客户/审稿人证明可复现性。

### 3.2 求解器开发者（OpenFOAM/SU2/自研 CFD 代码维护者）
- 作为求解器开发者，我希望写一个 Adapter 就能把我的 solver 接入平台，以便我的用户能用标准 case 验证我的代码。
- 作为求解器开发者，我希望平台在 CI 里跑 smoke + verification case 作为回归测试，以便我每次提交不会引入数值回归。
- 作为求解器开发者，我希望 `dry_run` 模式能在不真正跑求解器的情况下验证 case 配置与 manifest 生成逻辑，以便在没有 HPC/容器环境的开发机上也能调试。

### 3.3 研究者（发表论文、需要权威对比）
- 作为研究者，我希望平台内置 NACA0012 / backward-facing step / FDA nozzle 等经典验证 case 及其参考实验/DNS 数据，以便我的论文对比有权威依据。
- 作为研究者，我希望报告导出包含 GCI（网格收敛指数）与网格收敛率，以便我能支撑网格无关性论证。

### 3.4 ML surrogate 开发者（训练 neural CFD 模型）
- 作为 ML surrogate 开发者，我希望 `surrogate_model` adapter 能加载 PyTorch/JAX/ONNX 模型并按统一接口产出场预测，以便我的模型与经典 CFD 在同一 case 上对比。
- 作为 ML surrogate 开发者，我希望平台额外报告 divergence error / boundary violation / OOD 泛化 / inference latency，以便我全面评估模型的物理可信度而非仅看拟合误差。

### 3.5 CI/CD 集成者（把平台嵌入求解器项目的 CI 流水线）
- 作为 CI/CD 集成者，我希望 `generic_command` adapter 能包装任意 shell 命令并以非零退出码表示失败，以便我无需写 Python adapter 就能快速接入既有求解脚本。
- 作为 CI/CD 集成者，我希望 `cfdb run` 退出码语义清晰（0=全过 / 非 0=有失败），以便 CI 能直接据此 gate 合并。
- 作为 CI/CD 集成者，我希望单元测试不依赖真实 OpenFOAM/SU2 安装（用 mock case），以便 CI 镜像保持轻量。

---

## 4. 需求池

### 阶段边界定义（关键澄清）

| 阶段 | 对应用户输入 | 范围 | 目标 |
|---|---|---|---|
| **P0** | 分阶段提示词的"第一阶段交付" | 仅 mock case 闭环 | 搭好骨架，证明流水线打通 |
| **P1** | "MVP 6 件事" | 接入真实 OpenFOAM/SU2 + 真实 case | 可对外发布的第一个可用版本 |
| **P2** | 后续增强 | surrogate / 商业 solver / HPC / DB / Web | 扩展生态 |

> ⚠️ **关键边界**：P0 不接任何真实求解器，只用 `generic_command` + 2 个 mock case 验证平台自身流水线（schema → registry → CLI → adapter → metrics → report）。真实 solver 接入从 P1 开始。

---

### 4.1 P0 —— MVP 第一阶段（Mock Case 闭环）

**目标**：在完全不依赖真实 CFD 求解器的前提下，跑通"配置 → 注册 → 运行 → 度量 → 报告"全链路。

| ID | 需求 | 优先级 |
|---|---|---|
| P0-1 | 标准项目结构 + `pyproject.toml`（src layout，ruff/pytest 配置） | P0 |
| P0-2 | 核心 schema：`CaseSpec` / `SolverSpec` / `MeshSpec` / `ReferenceSpec` / `MetricSpec` / `RunManifest`（Pydantic v2） | P0 |
| P0-3 | Case registry：`list-cases` / `validate-case`（扫描 `cases/` 下 `case.yaml`） | P0 |
| P0-4 | CLI 4 命令：`list-cases` / `validate-case` / `run` / `report`（Typer） | P0 |
| P0-5 | `generic_command` adapter：执行任意 shell 命令，捕获成功/失败、退出码、stdout/stderr、wall time，生成 `RunManifest` | P0 |
| P0-6 | 基础 metrics：QoI 相对误差、曲线 L2、成功/失败、wall time | P0 |
| P0-7 | 2 个 mock case：`mock_success`（退出码 0 + 恒定 QoI 输出）、`mock_failure`（退出码 1） | P0 |
| P0-8 | pytest 覆盖（目标 80%，详见待确认问题 Q8） | P0 |
| P0-9 | README（安装、4 命令用法、mock case 示例） | P0 |
| P0-10 | HTML 报告：run_id + status + QoI 误差表 + artifact 列表 + 环境元数据 | P0 |

### 4.2 P1 —— MVP 完整版（真实 Solver 接入）

**目标**：接入至少一个真实开源 CFD 求解器，能在真实物理 case 上产出可对比结果。

| ID | 需求 | 优先级 |
|---|---|---|
| P1-1 | `openfoam` adapter：支持 `dry_run`（仅生成 case 目录 + manifest，不执行）与真实运行（调用 `blockMesh` / `simpleFoam` 等） | P1 |
| P1-2 | `su2` adapter：支持 `dry_run` 与真实运行（调用 `SU2_CFD`） | P1 |
| P1-3 | 三个真实 case：`lid-driven cavity`（Re=100/1000/10000）、`flat plate`（层流/湍流）、`NACA0012`（多个攻角） | P1 |
| P1-4 | 上述 case 的 `case.yaml` 完整定义（几何参数、边界条件、求解器参数、QoI 定义、参考数据来源） | P1 |
| P1-5 | 几何/网格数据：可用占位/最小网格（P0 不含真实几何，P1 提供） | P1 |
| P1-6 | Docker backend：通过容器镜像运行求解器，镜像 digest 写入 manifest | P1 |
| P1-7 | Artifact store 规范化：定义 run 产物的目录结构（残差日志、场数据、后处理图、manifest） | P1 |
| P1-8 | `no_run_reference` adapter：只校验参考数据存在性与格式，不执行求解 | P1 |

### 4.3 P2 —— 后续增强

| 类别 | 需求 |
|---|---|
| Adapter | `surrogate_model`（PyTorch/JAX/ONNX 加载与推理）、`fluent`（商业，需 license 管理） |
| Backend | Slurm backend（HPC 队列提交）、Kubernetes backend |
| 存储 | DuckDB/SQLite 替代 JSON manifest（支持历史查询/对比） |
| 可视化 | Web Dashboard、排行榜、跨 run 对比视图 |
| V&V case | MMS（method of manufactured solutions）、bump-in-channel、FDA nozzle、backward-facing step |
| Surrogate case | AirfRANS、channel flow |
| 高级指标 | GCI（网格收敛指数）、网格收敛率、守恒误差检查、场 RMSE、OOD 泛化评测 |
| 报告 | PDF 导出、LaTeX 导出（论文用） |

---

## 5. 非功能性需求

| 类别 | 要求 |
|---|---|
| 可复现性 | `RunManifest` 必须包含：solver name + version、container digest（若有）、git commit hash、mesh hash（sha256）、Python 环境、CLI 参数、wall/CPU time |
| 跨平台 | Linux 优先（HPC/CI 主战场）；Windows/macOS 须能跑 P0 mock case 与单元测试 |
| 测试独立性 | 单元测试 **不得**依赖真实 OpenFOAM/SU2/Fluent 安装；全部用 mock case 或 fixture |
| 类型标注 | 公共 API 100% 类型标注；`mypy --strict`（或 pyright）通过（详见 Q11） |
| 配置规范 | 不得硬编码绝对路径；所有路径走配置或相对仓库根 |
| 异常处理 | 不得吞异常（no bare `except:`、no `pass` on error）；失败必须写入 manifest `status=failed` + error trace |
| 日志 | 结构化日志（含 run_id 贯穿），不依赖 print 调试 |
| 许可证 | 代码 license（MIT/Apache-2.0，详见 Q6）+ case 数据 CC-BY-4.0（用户已指定） |

---

## 6. 待确认问题（CRITICAL —— 需转总决策）

> ✅ **2026-06-16 转总已全部确认**。下文每条均补"**决策**"行作为下游依据。

### ⭐ Q1. OpenFOAM 真实运行的接入节奏
用户分阶段提示词里"第一阶段"只用 mock，"MVP 6 件事"才接真实 solver。
**决策**：**P0（mock）→ P1-a（`dry_run`，生成 case 目录但不执行）→ P1-b（真实执行）三步走**。dry_run 用于在无 OpenFOAM 的 CI 环境验证 adapter 逻辑。

### ⭐ Q2. 案例数据（几何/网格/参考数据）的版本管理方式
真实 case 的网格文件通常 >10MB，不适合直接进 git。
**决策**：**MVP（P1）直接上 DVC**，并配 `.dvc` 远程存储（MinIO/S3 二选一，后续确定）。网格小到 <1MB 的 smoke case 可不进 DVC 直接入 git。

### ⭐ Q3. 数据库选型与切换时点
用户已确认 MVP 用 JSON manifest。
**决策**：**P2 上 SQLite**，**预留 `ResultRepository` 抽象层（Protocol）**。P0/P1 用 `JsonManifestRepository` 实现该接口，P2 切 SQLite 实现零业务代码改动。

### ⭐ Q4. CLI 命令名与 Python 包名
**决策**：**CLI=`cfdb`、Python import 包=`cfdb`、PyPI 发布名=`cfd-benchmark`**。

### ⭐ Q5. 代码 License
用户已指定 case 数据用 CC-BY-4.0。
**决策**：**代码 license = MIT**（最宽松，最大化采纳度）。

### Q6. Python 版本支持下限
**决策**：**Python 3.11+**（3.10 不支持）。CI 矩阵跑 3.11 与 3.12。

### Q7. 测试覆盖率目标
**决策**：**P0 阶段覆盖率门槛 = 80%（CI gate）**，未达 80% CI 失败。

### Q8. Mock case 的边界覆盖
**决策**：**新增 2 个边界 mock case**：
- `mock_missing_reference`：`ReferenceSpec` 指向不存在的文件，验证错误路径
- `mock_missing_qoi`：命令执行成功但 qoi.json 缺少字段，验证 metric 路径
合计 P0 共 4 个 mock case：mock_success / mock_failure / mock_missing_reference / mock_missing_qoi。

### Q9. HTML Report 的导出格式
**决策**：**MVP 不做 PDF，P2 再做**。MVP 用浏览器打印。

### Q10. RunManifest 的存储位置
**决策**：**`runs/<run_id>/manifest.json`**（按 run 隔离，artifact 同目录）。

### Q11. 类型检查严格度
**决策**：**pyright basic mode 进 CI gate（P0/P1）**，pyright strict 作为 P1 收尾目标。

---

## 7. UI 设计稿（CLI + HTML Report）

### 7.1 CLI 交互示意（P0）

```
$ cfdb --help

 Usage: cfdb [OPTIONS] COMMAND [ARGS]...

 CFD-Benchmark: 标准化 CFD V&V 与多 solver 对比平台

╭─ Options ───────────────────────────────────────────────╮
│ --version             -V        显示版本                 │
│ --cases-dir           PATH      case 根目录（默认 cases/）│
│ --verbose             -v        详细日志                 │
│ --help                -h        显示帮助                 │
╰──────────────────────────────────────────────────────────╯
╭─ Commands ──────────────────────────────────────────────╮
│ list-cases      列出所有已注册 case                      │
│ validate-case   校验单个 case.yaml                       │
│ run             运行指定 case + solver                   │
│ report          为已完成的 run 生成 HTML 报告            │
╰──────────────────────────────────────────────────────────╯
```

```
$ cfdb list-cases

ID                        TYPE          SOLVERS                STATUS
mock_success              smoke         generic                ok
mock_failure              smoke         generic                ok
lid_driven_cavity         validation    openfoam, su2          pending
naca0012                  validation    openfoam, su2          pending
flat_plate                verification  openfoam, su2          pending

共 5 个 case（2 个可用，3 个 pending：缺少 solver adapter）
```

```
$ cfdb validate-case cases/smoke/mock_success/case.yaml

✓ CaseSpec     格式正确
✓ SolverSpec   generic_command 配置完整
✓ MeshSpec     （smoke case 无需网格）
✓ ReferenceSpec 引用文件存在
✓ MetricSpec   qoi_relative_error 配置正确
→ 校验通过
```

```
$ cfdb run --case mock_success --solver generic --backend local

[run:20250616-143052-a1b2c3] 启动...
[run:20250616-143052-a1b2c3] backend=local solver=generic
[run:20250616-143052-a1b2c3] 执行: bash mock_success/run.sh
[run:20250616-143052-a1b2c3] 退出码=0  wall=0.42s
[run:20250616-143052-a1b2c3] metrics: qoi_relative_error=0.0123  status=success
[run:20250616-143052-a1b2c3] manifest → runs/20250616-143052-a1b2c3/manifest.json
✓ 完成（status=success）
```

```
$ cfdb report --run-dir runs/20250616-143052-a1b2c3

生成报告 → runs/20250616-143052-a1b2c3/report.html
浏览器打开: file:///D:/GLM-CFD-Benchmark/runs/20250616-143052-a1b2c3/report.html
```

### 7.2 HTML Report 模块布局

```
┌─────────────────────────────────────────────────────────────┐
│  CFD-Benchmark Report                          [打印] [返回] │
├─────────────────────────────────────────────────────────────┤
│ run_id: 20250616-143052-a1b2c3      status: ● success       │
│ case: mock_success   solver: generic   backend: local        │
│ 时间: 2025-06-16 14:30:52   wall: 0.42s                      │
├─────────────────────────────────────────────────────────────┤
│ 环境元数据                                                   │
│  • git_commit: a1b2c3d   • python: 3.11.9                    │
│  • platform: linux-x86_64  • container_digest: -(local)      │
├─────────────────────────────────────────────────────────────┤
│ QoI 误差表                                                  │
│ ┌─────────────────┬──────────┬──────────┬────────────┐      │
│ │ QoI             │ 计算值   │ 参考值   │ 相对误差   │      │
│ ├─────────────────┼──────────┼──────────┼────────────┤      │
│ │ centerline_umax │ 0.373    │ 0.371    │ 0.54%      │      │
│ │ drag_coeff      │ -        │ -        │ -          │      │
│ └─────────────────┴──────────┴──────────┴────────────┘      │
├─────────────────────────────────────────────────────────────┤
│ 残差 / 收敛曲线（P1 真实 solver 才有；P0 mock 占位）        │
├─────────────────────────────────────────────────────────────┤
│ Artifacts                                                   │
│  • manifest.json   • stdout.log   • stderr.log   • qoi.json │
├─────────────────────────────────────────────────────────────┤
│ [页脚] CFD-Benchmark v0.1.0 · 生成于 2025-06-16 14:31:00     │
└─────────────────────────────────────────────────────────────┘
```

**设计原则**：
- 单文件 HTML（内联 CSS/JS），无外部依赖，便于归档与邮件附件
- P0 不做交互式图表（残差曲线用静态 SVG/表格），P2 再上 Plotly
- 无 Web UI、无服务端

---

## 8. 验收标准（P0 阶段 Checklist）

P0 阶段完成的可勾选验收项：

- [ ] `cfdb --help` 正常输出 4 个子命令
- [ ] `cfdb list-cases` 列出至少 2 个 case（mock_success、mock_failure）
- [ ] `cfdb validate-case cases/smoke/mock_success/case.yaml` 校验通过
- [ ] `cfdb validate-case cases/smoke/mock_failure/case.yaml` 校验通过（配置本身合法，仅运行会失败）
- [ ] `cfdb run --case mock_success --solver generic --backend local` 成功，生成 `manifest.json` + metrics，`status=success`
- [ ] `cfdb run --case mock_failure --solver generic --backend local` 执行失败，但 `manifest.json` 记录 `status=failed` + error trace，CLI 退出码非 0
- [ ] `cfdb report --run-dir <run_id>` 生成 `report.html`，浏览器可打开
- [ ] `pytest` 全绿，覆盖率 ≥ 80%（待 Q7 确认）
- [ ] `ruff check .` 无错误
- [ ] README 包含安装步骤 + 4 命令用法 + mock case 示例
- [ ] 在**未安装 OpenFOAM/SU2** 的干净环境（如 GitHub Actions ubuntu-latest）下 P0 全部验收项通过

---

## 附录：术语表

| 术语 | 含义 |
|---|---|
| V&V | Verification & Validation，验证与确认（NASA / ASME V&V 10/20） |
| Verification | 数值方法实现是否正确（求解了正确的方程吗） |
| Validation | 物理模型是否匹配现实（求解的是正确的方程吗） |
| QoI | Quantity of Interest，关注量（如阻力系数、最大速度） |
| GCI | Grid Convergence Index，网格收敛指数 |
| MMS | Method of Manufactured Solutions，人造解方法（verification 金标准） |
| RunManifest | 单次运行的完整元数据记录（可复现性核心） |
| Adapter | 求解器适配层（Protocol），屏蔽不同 solver 的调用差异 |
| dry_run | 仅生成 case 目录与 manifest，不真正执行求解器 |
