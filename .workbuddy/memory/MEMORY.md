# CFD-Benchmark 项目长期记忆

> 本仓 `D:\GLM-CFD-Benchmark` 的关键约定与决策。跨会话稳定。

## 项目定位
开源 CFD 求解器/仿真方案 benchmark 平台。统一 case schema + Solver Adapter 抽象层，让传统 CFD（OpenFOAM/SU2/Fluent/自研）和 ML surrogate 在同一组可复现 case 上对比。

## 五类 case 分层
1. `smoke` - 平台自身流水线验证（P0 mock case 层）
2. `verification` - 数值实现正确性（MMS / flat plate / bump-in-channel）
3. `validation` - 物理现实对比（NACA0012 / backward-facing step / FDA nozzle）
4. `performance` - 性能/并行效率
5. `surrogate` - ML/AI CFD 模型评测（AirfRANS / channel flow）

## 三大 Protocol 抽象（核心架构）
- `SolverAdapter`: prepare(case, case_dir, run_dir) + run() + collect_outputs()
- `ExecutionBackend`: execute(command, cwd, timeout) → RunResult
- `ResultRepository`: save_run/load_run/list_runs（P0 JSON, P2 SQLite）

## Schema 扩展点（P1-a + P1-b）
- `SolverConfig.steps: list[CommandStep] | None`（多步命令序列，OpenFOAM/SU2 用）
- `SolverConfig.parameters: dict[str, Any] | None`（模板参数注入）
- `RunManifest.status: Literal["success","failed","timeout","dry_run"]`
- `RunManifest.dry_run_skipped_commands: list[str] | None`
- `RunManifest.solver_version: str | None`（P1-b，从首步 stdout grep）
- `RunManifest.final_residuals: dict[str, float] | None`（P1-b，从末步 stdout 正则解析）
- `RunResult.skipped_commands: list[str] | None`（默认 None，挂 dataclass 上）
- `RunResult.solver_version / final_residuals`（P1-b，Optional）
- `StepResult` dataclass（P1-b，adapter 内部辅助类，Runner 不引用）
- `CommandStep`: name/command/timeout_sec/critical

## dry_run 机制（P1-a）+ 真实执行（P1-b）
- `dry_run` 通过 adapter `__init__` 注入（铁律 #3：不改 SolverAdapter Protocol）
- MetricsEngine 双重判断：检测 `run_result.skipped_commands is not None` → overall=dry_run
- 真实执行（P1-b）：adapter 按 `solver_config.steps` 循环调 `LocalExecutionBackend.execute()`
- `_merge_step_results` 合并多步 → 单 RunResult，末步 stdout 解析 final_residuals
- CommandStep.critical=True 失败 break；critical=False 失败 warning 继续
- solver_version 从首步 stdout 前 10 行 grep `Version:` / `Build:`（零额外 subprocess）
- OpenFOAM/SU2 用 `run_dir/case/` 子目录隔离 system/constant/0 结构
- 模板打包用 hatchling `force-include` + `templates/__init__.py` 空文件
- adapter 文件零直接 subprocess import（铁律 #3：只能走 LocalExecutionBackend）

## cfdb.post 子包（P1-b 新增）
- `post/residuals.py`: parse_openfoam_residuals / parse_su2_residuals / extract_final / extract_*_version
- `post/qoi_extractor.py`: extract_openfoam_centerline_umax（probes）+ extract_su2_skin_friction_coeff（CSV）
- 纯 Python re，无第三方依赖
- 残差正则容忍 OpenFOAM OpenCFD v2312/v2406 + Foundation v11/v12 微差

## pytest marker 分层（P1-b）
- `@pytest.mark.real_solver` — 真实 OpenFOAM/SU2 安装才能跑，CI 默认 deselect
- `pyproject.toml` addopts 含 `-m 'not real_solver'`
- 本地手测: `pytest -m real_solver`
- 单元/集成测试用 mock backend（unittest.mock.patch LocalExecutionBackend.execute）

## 阶段路线图
- **P0（已交付 2026-06-16, commit 5c9948e）**: mock case 闘环，112 测试
- **P1-a（已交付 2026-06-16, commit 4d67403）**: OpenFOAM/SU2 dry_run，158 测试 / 94% cov
- **P1-b（已交付 2026-06-16, commit 4e0b857）**: 真实 OpenFOAM/SU2 subprocess + 残差/QoI 解析，178 测试 / 90.89% cov
- **P2 候选**: Docker backend / DVC 大文件 / SQLite 持久化 / 残差曲线 SVG / 场 RMSE / NACA0012 case / cell_count manifest / Web Dashboard / ML surrogate adapter / Fluent adapter / Slurm backend

## 命名规范
- CLI: `cfdb`
- Python import 包: `cfdb`
- PyPI 发布名: `cfd-benchmark`
- run_id: `YYYYMMDDTHHMMSSZ_<case_id>_<solver>_<hash8>`

## 工程约束
- Python 3.11+
- 单元测试不得依赖真实 OpenFOAM/SU2 安装
- 覆盖率 gate ≥ 80%
- pyright basic mode 进 CI gate
- Windows Git Bash subprocess 必须 `--login` + `encoding="utf-8"`

## License
- 代码: MIT
- Case 数据: CC-BY-4.0

## 测试矩阵
- CI: Python 3.11 + 3.12, ubuntu-latest（干净环境无真实 solver）
- 本地: Windows + Git Bash（用户主开发环境）

## 关键经验教训
- Git Bash 不带 `--login` 时 PATH 不含 `/usr/bin`，cat/sleep 等命令找不到，且内部 `bash` 会被 WSL System32 bash 截获
- Windows 默认 locale GBK 读 UTF-8 文件会爆 → 所有 read_text 必须 `encoding="utf-8"`
- 工程师自测不可信，必须 QA 独立端到端真跑 CLI 命令
