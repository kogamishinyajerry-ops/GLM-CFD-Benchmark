# CFD-Benchmark PRD v1.1 — P1-a 增量（dry_run 模式）

## 1. 文档信息

| 项 | 内容 |
|---|---|
| 版本 | v1.1（增量） |
| 日期 | 2026-06-16 |
| 作者 | 许清楚（Xu）· 产品经理 |
| 状态 | **Confirmed**（转总已确认 5 个待确认问题，2026-06-16） |
| 基于 | PRD-v1（v1.0，已确认）+ Architecture-v1（v1.0，已确认） |
| 决策来源 | 转总"P0→P1-a(dry_run)→P1-b(真实)"三步节奏决策（PRD-v1 Q1） |
| 工作目录 | `D:\GLM-CFD-Benchmark` |

> **本文档只描述 P0 之上的变更部分（delta），不重复 P0 已有功能。** 架构师/工程师在实现前应先通读 PRD-v1 与 Architecture-v1。

---

## 2. 范围声明（CRITICAL）

### 2.1 做（P1-a 交付范围）

| # | 范围项 |
|---|---|
| 1 | OpenFOAM adapter 的 `dry_run` 模式（生成完整 case 目录结构但不调用 simpleFoam/blockMesh） |
| 2 | SU2 adapter 的 `dry_run` 模式（生成 SU2 CFG 配置文件但不调用 SU2_CFD） |
| 3 | 至少 1 个 lid-driven cavity 的简化 case（用 dry_run 验证 OpenFOAM 目录结构生成正确） |
| 4 | 1 个 SU2 简化 case（`flat_plate_su2`，用 dry_run 验证 CFG 文件生成正确） |
| 5 | CLI `run` 命令增加 `--dry-run` flag |
| 6 | `SolverConfig` schema 扩展：新增 Optional 字段支持"多步命令序列"（`List[CommandStep]`） |
| 7 | `RunManifest` schema 扩展：`status` 枚举增加 `"dry_run"`，新增 `dry_run_skipped_commands` Optional 字段 |

### 2.2 不做（推到 P1-b）

| # | 推迟项 | 推迟原因 |
|---|---|---|
| 1 | 真实调用 OpenFOAM/SU2 可执行文件 | 三步节奏：P1-b 才接真实 solver |
| 2 | Docker backend | P1-b 范围 |
| 3 | 真实网格数据（DVC 管理） | P1-b 才接 DVC + 真实几何 |
| 4 | 真实后处理（场数据读取、残差曲线解析） | dry_run 无真实输出，后处理在 P1-b |
| 5 | 模板渲染后的文件语法校验（如 controlDict 是否能被 OpenFOAM 解析） | P1-b 接真实 solver 时自然校验 |

### 2.3 铁律（不可违反）

| # | 铁律 |
|---|---|
| 1 | **不破坏 P0 的 112 个测试**——P1-a 合并后 `pytest` 全部通过 |
| 2 | **不改 P0 schema 公共字段**——只能新增 Optional 字段或新模型（详见 §3.1 变更矩阵） |
| 3 | **复用现有 `SolverAdapter` Protocol**——不重新设计抽象，dry_run 通过构造参数注入 |
| 4 | **dry_run 必须在无 OpenFOAM/SU2 的环境通过**——GitHub Actions ubuntu-latest 是验收基准 |

---

## 3. dry_run 语义定义（最关键）

### 3.1 核心定义

> **dry_run 模式**：adapter 的 `prepare` 阶段**完整执行**（渲染模板 + 写入 case 目录结构 + 生成所有配置文件），但 `run` 阶段**跳过实际 subprocess 调用**，直接构造一个"模拟" RunResult（`exit_code=0`, `stdout="[dry-run] command not executed"`, `wall_time_sec≈0`），让 manifest / metrics / report 流水线继续走完。

**设计意图**：dry_run 既能验证 case 配置/模板渲染的正确性，又能验证 manifest 生成/目录结构的正确性，**且完全不依赖真实 solver 安装**。这是 P1-a 的核心价值——在 CI 环境中持续保障 case 配置不退化。

### 3.2 dry_run 语义的四个关键问题（直接回答给架构师）

#### Q1. dry_run 由谁触发？

**决策**：CLI 全局 flag `cfdb run --case <id> --solver <name> --dry-run`，经 Runner 透传给 Adapter。

**传递路径**：`CLI --dry-run` → `Runner.execute(dry_run=True)` → adapter 构造时注入 `dry_run` 参数 → `adapter.run()` 内部检查 `self._dry_run` flag。

**不选 adapter 内部参数的原因**：dry_run 是用户意图（由 CLI 参数决定），不是 adapter 自身属性。但为了不修改 `SolverAdapter` Protocol 方法签名（铁律 #3），dry_run 通过 adapter **构造函数**（`__init__`）注入，而非 `run()` 方法参数。

#### Q2. dry_run 模式下，`SolverConfig.command` / `steps` 是否仍然渲染？

**决策**：**是，必须渲染但不执行。**

模板渲染本身是 dry_run 的核心价值之一——它能提前发现 Jinja2 模板语法错误、变量缺失等问题。渲染后的命令字符串应记录到 manifest 的 `dry_run_skipped_commands` 字段，便于人工审查"本应执行什么"。

#### Q3. dry_run 的 RunManifest.status 是 `"success"` 还是新增 `"dry_run"`？

**决策**：**新增 `"dry_run"` 枚举值。**

理由：
1. Report 生成可以明确区分真实运行结果与 dry_run 结果（视觉标记、不显示"假"的 QoI 误差表）。
2. CI gating 逻辑清晰：dry_run 永远 exit 0（不因缺少真实输出而判 fail），真实 run 才按 success/fail gate。
3. MetricsEngine 遇到 `status=dry_run` 时跳过 QoI 容差检查（dry_run 无真实输出，检查无意义）。

#### Q4. dry_run 模式下 manifest 是否需要记录"本应执行但跳过的命令列表"？

**决策**：**是，记录到 `RunManifest.dry_run_skipped_commands: list[str] | None`。**

每个元素是渲染后的完整命令字符串（非模板）。这样：
- 人工审查 dry_run 结果时能看到"如果真跑会执行什么命令"
- 排查 case 配置时能确认命令序列是否正确（如 blockMesh → decomposePar → simpleFoam → reconstructPar）
- 非 dry_run 模式下此字段为 `None`

### 3.3 dry_run 的 RunResult 构造规则

当 `self._dry_run == True` 时，adapter 的 `run()` 方法返回以下合成结果：

```python
RunResult(
    exit_code=0,
    stdout="[dry-run] commands not executed",
    stderr="",
    wall_time_sec=0.0,
    timed_out=False,
)
```

Runner 检测到 `dry_run=True` 时，`_determine_status()` 直接返回 `"dry_run"`，不走 success/failed/timeout 逻辑。

---

## 4. Schema 变更矩阵（P0 → P1-a）

> **铁律 #2 约束**：以下变更全部为**新增字段或新增枚举值**，不删除/不重命名任何 P0 已有字段。P0 的 112 个测试不受影响。

### 4.1 新增模型：`CommandStep`

```python
class CommandStep(BaseModel):
    """单步执行命令（多步序列中的一个环节）。

    用于 OpenFOAM 等需要多步命令的 solver：
    blockMesh → decomposePar → simpleFoam → reconstructPar
    """
    model_config = ConfigDict(extra="forbid")

    name: str
    """步骤名称（如 'block_mesh', 'solve', 'reconstruct'），用于日志与 manifest。"""

    command: str
    """Jinja2 命令模板。可用变量与 SolverConfig.command 相同：
    {{ case_id }}, {{ solver }}, {{ mesh_level }}, {{ case_dir }}, {{ run_dir }}。"""

    timeout_sec: int | None = Field(None, gt=0)
    """本步骤超时（秒）。None = 不超时。"""

    critical: bool = True
    """是否关键步骤。True = 本步失败则整个 run 失败；
    False = 本步失败只记录 warning，继续后续步骤。
    （P1-a dry_run 模式下此字段不影响行为，全部跳过。P1-b 真实运行时生效。）"""
```

### 4.2 变更：`SolverConfig`（新增 Optional 字段）

```python
class SolverConfig(BaseModel):
    # ... P0 已有字段保持不变 ...
    name: str
    command: str            # ← 保持不变（generic_command adapter 仍用此字段）
    timeout_sec: int | None = Field(None, gt=0)

    # === P1-a 新增 ===
    steps: list[CommandStep] | None = None
    """多步命令序列（P1-a 新增）。

    - 若提供，adapter 按 steps 顺序执行（真实模式）或按顺序记录到 skipped_commands（dry_run 模式）。
    - 若为 None，adapter 回退到单步 command 模式（P0 行为不变）。
    - OpenFOAM/SU2 adapter 使用 steps；generic_command adapter 继续使用 command。
    """
```

**向后兼容性**：`steps` 默认 `None`，P0 的 4 个 mock case 的 `case.yaml` 不含 `steps` 字段，加载行为完全不变。`extra='forbid'` 不会拒绝 `steps`（因为它是已定义字段）。

### 4.3 变更：`RunManifest`（status 枚举扩展 + 新增字段）

```python
class RunManifest(BaseModel):
    # ... P0 已有字段保持不变 ...
    run_id: str
    case_id: str
    solver: str
    backend: Literal["local", "docker", "slurm"] = "local"

    # === 变更：status 枚举增加 dry_run ===
    status: Literal["success", "failed", "timeout", "dry_run"]
    """运行状态。P1-a 新增 'dry_run' 枚举值。"""

    timing: TimingSpec
    host: str | None = None
    artifacts: dict[str, Path] = Field(default_factory=dict)
    git_commit: str | None = None
    container_digest: str | None = None
    error: str | None = None
    cli_args: dict[str, str] | None = None

    # === P1-a 新增 ===
    dry_run_skipped_commands: list[str] | None = None
    """dry_run 模式下本应执行但被跳过的命令列表（渲染后的完整命令字符串）。
    非 dry_run 模式下为 None。"""
```

**向后兼容性**：`status` 新增 `"dry_run"` 枚举值不影响已有值（success/failed/timeout 仍可正常使用）。`dry_run_skipped_commands` 默认 `None`，P0 的 manifest 反序列化不受影响。

> ⚠️ **注意**：`status` 字段类型从 `Literal["success", "failed", "timeout"]` 改为 `Literal["success", "failed", "timeout", "dry_run"]`。这**严格来说是修改了已有字段的类型标注**，但属于**纯增量扩展**（只加枚举值，不删不改名）。P0 已有的所有 status 赋值（`"success"` / `"failed"` / `"timeout"`）在新类型下仍然合法，112 个测试不受影响。请架构师确认此变更是否符合铁律 #2 的精神。如需更保守的做法，见 §8 待确认问题 Q1。

---

## 5. 用户故事（增量，针对 dry_run）

### US-P1a-1（CFD 工程师）
> 作为 CFD 工程师，我希望 `cfdb run --case lid_driven_cavity --solver openfoam --dry-run` 能在**没装 OpenFOAM** 的开发机上验证 case 目录结构是否正确，以便提前发现配置错误（模板变量缺失、目录结构错误），而不必等到 P1-b 真实运行时才暴露。

### US-P1a-2（求解器开发者）
> 作为求解器开发者，我希望 dry_run 在 manifest 中明确标记 `status=dry_run`，以便我区分真实运行结果与 dry_run 结果，避免把 dry_run 的"假成功"误认为真实求解通过。

### US-P1a-3（CI/CD 集成者）
> 作为 CI/CD 集成者，我希望 dry_run 能在 GitHub Actions ubuntu-latest（无 OpenFOAM/SU2）上跑通，以便每次 PR 都自动验证 case 配置（YAML + 模板）没有破坏，且 CI 镜像不需要安装昂贵的 CFD 求解器。

### US-P1a-4（求解器开发者）
> 作为求解器开发者，我希望 OpenFOAM 的多步命令（blockMesh → decomposePar → simpleFoam → reconstructPar）能在 `SolverConfig` 中以 `steps` 列表形式声明，以便 dry_run 时能在 manifest 中清晰看到完整的命令序列，也方便 P1-b 真实运行时按序执行。

### US-P1a-5（CFD 工程师）
> 作为 CFD 工程师，我希望 SU2 adapter 的 dry_run 能生成完整的 `.cfg` 配置文件，以便我在提交到 HPC 真实运行前，先在本地检查 SU2 的边界条件、数值格式、求解器参数等配置是否正确。

---

## 6. 需求池（P1-a）

| ID | 需求 | 优先级 | 关联 US |
|---|---|---|---|
| P1a-1 | **`CommandStep` 新模型**：name / command / timeout_sec / critical 五字段，支持多步命令序列声明 | P1-a | US-4 |
| P1a-2 | **`SolverConfig.steps` 扩展**：新增 Optional `list[CommandStep]` 字段，默认 None，与 `command` 字段共存（generic 用 command，openfoam/su2 用 steps） | P1-a | US-4 |
| P1a-3 | **`RunManifest.status` 枚举扩展**：增加 `"dry_run"` 值 | P1-a | US-2 |
| P1a-4 | **`RunManifest.dry_run_skipped_commands` 新增**：Optional `list[str]` 字段，记录渲染后跳过的命令 | P1-a | US-2, US-4 |
| P1a-5 | **`SolverAdapter` Protocol 不变**：dry_run 通过 adapter 构造函数注入（`__init__(self, dry_run: bool = False)`），不修改 `prepare`/`run`/`collect_outputs` 签名 | P1-a | — |
| P1a-6 | **Runner 扩展**：`execute()` 新增 `dry_run: bool = False` 参数；dry_run=True 时将 flag 传给 adapter 构造，并在 `_determine_status()` 中直接返回 `"dry_run"` | P1-a | US-2 |
| P1a-7 | **OpenFOAM adapter**（`src/cfdb/adapters/openfoam.py`）：Jinja2 渲染 `system/controlDict`、`system/fvSchemes`、`system/fvSolution`、`constant/transportProperties`、`constant/turbulenceProperties` + `0/` 初始场占位 + `constant/polyMesh/` 占位目录；dry_run 跳过 blockMesh/decomposePar/simpleFoam/reconstructPar | P1-a | US-1, US-4 |
| P1a-8 | **SU2 adapter**（`src/cfdb/adapters/su2.py`）：Jinja2 渲染 SU2 `.cfg` 配置文件 + `mesh_file` 占位；dry_run 跳过 SU2_CFD | P1-a | US-5 |
| P1a-9 | **CLI `--dry-run` flag**：`run` 命令增加 `--dry-run` bool flag，透传给 `Runner.execute(dry_run=...)` | P1-a | US-1, US-3 |
| P1a-10 | **`lid_driven_cavity` case**：OpenFOAM 风格 case 目录 + case.yaml，含 `openfoam` solver 的 steps 配置（blockMesh → simpleFoam）+ `su2` solver 配置 | P1-a | US-1 |
| P1a-11 | **`flat_plate_su2` case**：SU2 风格简化 case，含 `su2` solver 的 CFG 模板参数 | P1-a | US-5 |
| P1a-12 | **Adapter 注册**：`_ADAPTERS` 字典增加 `"openfoam"` 和 `"su2"` 条目 | P1-a | — |
| P1a-13 | **测试**：OpenFOAM adapter dry_run（验证生成的文件清单 = system/* + constant/* + 0/*）+ SU2 adapter dry_run（验证 CFG 文件生成）+ manifest 标记 dry_run + skipped_commands 记录 + CLI `--dry-run` 端到端 | P1-a | US-3 |

---

## 7. UI 设计稿（增量）

### 7.1 CLI 输出示意（dry_run 模式）

```
$ cfdb run --case lid_driven_cavity --solver openfoam --dry-run

============================================================
Run ID:    20260616T143052Z_lid_driven_cavity_openfoam_a1b2c3d4
Case:      lid_driven_cavity
Solver:    openfoam
Backend:   local
Status:    dry_run
Wall Time: 0.031s
[DRY-RUN] Skipped 2 command(s):
  [1] blockMesh
  [2] simpleFoam
============================================================
```

### 7.2 CLI 输出示意（SU2 dry_run）

```
$ cfdb run --case flat_plate_su2 --solver su2 --dry-run

============================================================
Run ID:    20260616T143200Z_flat_plate_su2_su2_e5f6g7h8
Case:      flat_plate_su2
Solver:    su2
Backend:   local
Status:    dry_run
Wall Time: 0.018s
[DRY-RUN] Skipped 1 command(s):
  [1] SU2_CFD flat_plate.cfg
============================================================
```

### 7.3 dry_run 模式下 manifest.json 示例（关键字段）

```json
{
  "run_id": "20260616T143052Z_lid_driven_cavity_openfoam_a1b2c3d4",
  "case_id": "lid_driven_cavity",
  "solver": "openfoam",
  "backend": "local",
  "status": "dry_run",
  "timing": {
    "wall_time_sec": 0.031,
    "start_time": "2026-06-16T14:30:52.123456+00:00",
    "end_time": "2026-06-16T14:30:52.154456+00:00"
  },
  "artifacts": {
    "system/controlDict": "system/controlDict",
    "system/fvSchemes": "system/fvSchemes",
    "system/fvSolution": "system/fvSolution",
    "constant/transportProperties": "constant/transportProperties",
    "constant/turbulenceProperties": "constant/turbulenceProperties"
  },
  "dry_run_skipped_commands": [
    "blockMesh",
    "simpleFoam"
  ],
  "error": null
}
```

### 7.4 dry_run 模式 CLI 退出码规则

| 场景 | 退出码 |
|---|---|
| dry_run 成功（prepare + 模板渲染无错） | **0** |
| dry_run 失败（prepare 阶段模板渲染失败、文件写入失败） | **1** |

dry_run 模式下，**即使没有真实 solver 输出，也不应因缺少 QoI 而返回非零退出码**。QoI 检查在 dry_run 模式下被跳过。

---

## 8. 验收 Checklist（P1-a）

### 8.1 功能验收

- [ ] `cfdb run --case lid_driven_cavity --solver openfoam --dry-run` 成功，`status=dry_run`
- [ ] 上述命令生成的 `run_dir/case/` 目录下含完整 OpenFOAM 结构：`system/`（controlDict + fvSchemes + fvSolution）+ `constant/`（transportProperties + turbulenceProperties）+ `0/`（初始场占位）
- [ ] `cfdb run --case flat_plate_su2 --solver su2 --dry-run` 成功，生成 SU2 `.cfg` 文件
- [ ] `manifest.json` 含 `"status": "dry_run"` + `"dry_run_skipped_commands": [...]` 且列表非空
- [ ] `cfdb run --case mock_success --solver generic --dry-run` 仍能工作（generic adapter 也支持 dry_run，虽然 P0 mock case 用不到，但 Runner 透传逻辑必须通用）
- [ ] CLI `--dry-run` flag 不影响不带该 flag 的正常运行（P0 行为完全不变）

### 8.2 回归验收

- [ ] **P0 回归**：原 112 个测试**全部通过**，零破坏
- [ ] `cfdb list-cases` 列出新 case（lid_driven_cavity + flat_plate_su2）
- [ ] `cfdb validate-case` 对新 case 的 case.yaml 校验通过

### 8.3 质量验收

- [ ] 新增 dry_run 测试覆盖 OpenFOAM + SU2 两个 adapter（文件清单断言 + manifest 字段断言）
- [ ] pytest 总数 ≥ **130**（112 + 至少 18 个新测试）
- [ ] 覆盖率 ≥ **80%**（CI gate 不降）
- [ ] `ruff check .` 无错误
- [ ] `pyright` basic mode 无错误

### 8.4 环境验收（关键）

- [ ] **在未安装 OpenFOAM/SU2 的环境（GitHub Actions ubuntu-latest 模拟）dry_run 全部通过**
- [ ] dry_run 模式不调用任何 subprocess（不尝试执行 blockMesh/SU2_CFD）

---

## 9. 待确认问题（Top 5，需架构师/转总决策）

> ✅ **2026-06-16 转总已全部确认**。下文每条均补"**决策**"行作为下游依据。

### ⭐ Q1. `RunManifest.status` 增加 `"dry_run"` 枚举值是否违反铁律 #2？

**决策**：**方案 A —— 加 `"dry_run"` 枚举值**。`status` 从 `Literal["success", "failed", "timeout"]` 扩展为 `Literal["success", "failed", "timeout", "dry_run"]`。纯增量（只加不删不改名），P0 所有 status 赋值仍合法，112 测试不变。

### ⭐ Q2. OpenFOAM case 模板用最小集（cavity 教程级别）还是支持完整参数化？

**决策**：**教程级最小集**。P1-a 直接参考 OpenFOAM tutorials/cavity 硬编码到 Jinja2 模板，case.yaml 只注入少量参数（Re、网格密度）。完整参数化（离散格式/线性求解器/残差阈值）推到 P1-b。

### ⭐ Q3. SU2 adapter 的 CFG 模板：一个通用模板还是每个 case 一个？

**决策**：**通用模板 + `SolverConfig.parameters` Optional 字段**。P1-a 写一个通用 SU2 CFG 模板（`adapters/templates/su2/base.cfg.j2`），case 差异通过 `SolverConfig.parameters: dict[str, Any] | None` 注入。P1-b 按需拆分专用模板。`SolverConfig.parameters` 同时供 generic adapter 复用。

### ⭐ Q4. `GenericCommandAdapter` 是否也需要支持 dry_run？

**决策**：**支持**。`GenericCommandAdapter.__init__` 接收 `dry_run: bool = False`，`run()` 检查此 flag。保持 Runner 透传逻辑统一（无 per-adapter 特殊处理），且可用于测试 dry_run 机制本身。

### ⭐ Q5. dry_run 模式下是否需要校验渲染后的文件内容？

**决策**：**P1-a 不校验文件语法**。dry_run 只验证文件存在性 + 非空。语法校验（controlDict 是否合法 OpenFOAM 字典、CFG 是否合法 SU2 配置）推到 P1-b 接真实 solver 时自然暴露。

---

## 附录 A：P1-a 涉及的新增/变更文件清单

| 类型 | 文件路径 | 变更说明 |
|---|---|---|
| **变更** | `src/cfdb/schema.py` | 新增 `CommandStep` 模型；`SolverConfig` 加 `steps` + `parameters` Optional；`RunManifest` status 加 `"dry_run"` + 加 `dry_run_skipped_commands` |
| **变更** | `src/cfdb/core/runner.py` | `execute()` 加 `dry_run` 参数；`_determine_status()` 处理 dry_run；构造 adapter 时传入 dry_run |
| **变更** | `src/cfdb/cli.py` | `run` 命令加 `--dry-run` flag |
| **变更** | `src/cfdb/adapters/__init__.py` | `_ADAPTERS` 字典加 `"openfoam"` / `"su2"` |
| **变更** | `src/cfdb/adapters/generic_command.py` | `__init__` 加 `dry_run` 参数（保持一致性） |
| **变更** | `src/cfdb/adapters/base.py` | 无变更（Protocol 不变，仅 docstring 注明 dry_run 约定） |
| **新增** | `src/cfdb/adapters/openfoam.py` | OpenFOAM adapter 实现 |
| **新增** | `src/cfdb/adapters/su2.py` | SU2 adapter 实现 |
| **新增** | `src/cfdb/adapters/templates/` | Jinja2 模板目录 |
| **新增** | `src/cfdb/adapters/templates/openfoam/controlDict.j2` | OpenFOAM controlDict 模板 |
| **新增** | `src/cfdb/adapters/templates/openfoam/fvSchemes.j2` | OpenFOAM fvSchemes 模板 |
| **新增** | `src/cfdb/adapters/templates/openfoam/fvSolution.j2` | OpenFOAM fvSolution 模板 |
| **新增** | `src/cfdb/adapters/templates/openfoam/transportProperties.j2` | OpenFOAM transportProperties 模板 |
| **新增** | `src/cfdb/adapters/templates/openfoam/turbulenceProperties.j2` | OpenFOAM turbulenceProperties 模板 |
| **新增** | `src/cfdb/adapters/templates/su2/base.cfg.j2` | SU2 通用 CFG 模板 |
| **新增** | `cases/validation/lid_driven_cavity/case.yaml` | lid-driven cavity case 配置 |
| **新增** | `cases/verification/flat_plate_su2/case.yaml` | flat plate SU2 case 配置 |
| **新增** | `tests/test_openfoam_adapter.py` | OpenFOAM adapter dry_run 测试 |
| **新增** | `tests/test_su2_adapter.py` | SU2 adapter dry_run 测试 |
| **新增** | `tests/test_dry_run_e2e.py` | dry_run 端到端测试（CLI → manifest） |

---

## 附录 B：dry_run 数据流图

```
CLI: cfdb run --case lid_driven_cavity --solver openfoam --dry-run
  │
  ▼
Runner.execute(case_id, solver="openfoam", dry_run=True)
  │
  ├── adapter = OpenFOAMAdapter(dry_run=True)     ← 构造时注入
  │
  ├── Phase 1: adapter.prepare(case, case_dir, run_dir)
  │     ├── 创建 run_dir/case/system/
  │     ├── 创建 run_dir/case/constant/
  │     ├── 创建 run_dir/case/0/
  │     ├── 渲染 controlDict.j2 → run_dir/case/system/controlDict
  │     ├── 渲染 fvSchemes.j2 → run_dir/case/system/fvSchemes
  │     ├── 渲染 fvSolution.j2 → run_dir/case/system/fvSolution
  │     ├── 渲染 transportProperties.j2 → run_dir/case/constant/transportProperties
  │     ├── 渲染 turbulenceProperties.j2 → run_dir/case/constant/turbulenceProperties
  │     └── 创建 0/U, 0/p 占位文件
  │
  ├── Phase 2: adapter.run(case, case_dir, run_dir, resources=None)
  │     ├── 检查 self._dry_run == True
  │     ├── 渲染 SolverConfig.steps 中的命令模板（但不执行）
  │     ├── 收集 skipped_commands = ["blockMesh", "simpleFoam"]
  │     └── 返回 RunResult(exit_code=0, stdout="[dry-run]...", wall_time_sec≈0)
  │
  ├── Phase 3: adapter.collect_outputs(case, run_dir)
  │     └── 收集生成的文件清单 → ArtifactManifest
  │
  ├── Phase 4: MetricsEngine.compute(...)
  │     └── 检测 dry_run → 跳过 QoI 检查 → overall_status="dry_run"
  │
  ├── Phase 5: 构建 RunManifest
  │     ├── status = "dry_run"
  │     ├── dry_run_skipped_commands = ["blockMesh", "simpleFoam"]
  │     └── artifacts = {生成的文件清单}
  │
  └── Phase 6: repo.save_run(manifest, metrics)
        └── 写入 runs/<run_id>/manifest.json
```

---

*文档结束。架构师请基于此 PRD 进行增量设计 + 任务分解，工程师照任务列表实现。待确认问题（§9）需在架构设计前由转总拍板。*
