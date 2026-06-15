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

## 阶段路线图
- **P0（已交付 2026-06-16）**: mock case 闭环
- **P1-a**: openfoam/su2 adapter dry_run（生成 case 目录但不执行）
- **P1-b**: 真实 OpenFOAM/SU2 + Docker backend + lid-driven cavity/flat plate/NACA0012
- **P1**: DVC 管理大网格数据
- **P2**: SQLite + surrogate adapter + fluent + slurm + Web Dashboard + 高级 V&V case

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
