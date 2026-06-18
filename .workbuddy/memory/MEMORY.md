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

## Schema 扩展点（P1-a + P1-b + P2-a）
- `SolverConfig.steps: list[CommandStep] | None`（多步命令序列，OpenFOAM/SU2 用）
- `SolverConfig.parameters: dict[str, Any] | None`（模板参数注入）
- `RunManifest.status: Literal["success","failed","timeout","dry_run"]`
- `RunManifest.dry_run_skipped_commands: list[str] | None`
- `RunManifest.solver_version: str | None`（P1-b，从首步 stdout grep）
- `RunManifest.final_residuals: dict[str, float] | None`（P1-b，从末步 stdout 正则解析）
- `RunManifest.cell_count: int | None`（P2-a，从 blockMesh/SU2 mesh log 提取）
- `RunManifest.step_details: list[dict] | None`（P2-a，StepResult.to_dict() 序列化）
- `RunManifest.residuals_history: dict[str, list[float]] | None`（P2-a，完整残差曲线供 SVG 渲染）
- `RunResult` 加同名 P2-a 3 字段（Optional）
- `RunResult.skipped_commands: list[str] | None`（默认 None，挂 dataclass 上）
- `RunResult.solver_version / final_residuals`（P1-b，Optional）
- `StepResult` dataclass（P1-b，adapter 内部辅助类；P2-a 加 `to_dict()` 方法）
- `CommandStep`: name/command/timeout_sec/critical

## dry_run 机制（P1-a）+ 真实执行（P1-b）
- `dry_run` 通过 adapter `__init__` 注入（铁律 #3：不改 SolverAdapter Protocol）
- MetricsEngine 双重判断：检测 `run_result.skipped_commands is not None` → overall=dry_run
- 真实执行（P1-b）：adapter 按 `solver_config.steps` 循环调 `LocalExecutionBackend.execute()`
- `_merge_step_results` 合并多步 → 单 RunResult，末步 stdout 解析 final_residuals（P2-a 增：填充 cell_count/step_details/residuals_history）
- CommandStep.critical=True 失败 break；critical=False 失败 warning 继续
- solver_version 从首步 stdout 前 10 行 grep `Version:` / `Build:`（零额外 subprocess）
- OpenFOAM/SU2 用 `run_dir/case/` 子目录隔离 system/constant/0 结构
- 模板打包用 hatchling `force-include` + `templates/__init__.py` 空文件
- adapter 文件零直接 subprocess import（铁律 #3：只能走 LocalExecutionBackend）

## cfdb.post 子包（P1-b + P2-a）
- `post/residuals.py`: parse_openfoam_residuals / parse_su2_residuals / extract_final / extract_*_version
- `post/qoi_extractor.py`: extract_openfoam_centerline_umax（probes）+ extract_su2_skin_friction_coeff（CSV）
- `post/mesh_stats.py`（P2-a）: extract_openfoam_cell_count（正则 `nCells:\s*(\d+)`）+ extract_su2_cell_count（`(\d[\d,]*)\s+volume\s+elements`）
- 纯 Python re，无第三方依赖
- 残差正则容忍 OpenFOAM OpenCFD v2312/v2406 + Foundation v11/v12 微差

## cfdb.storage 双实现（P0 JSON + P2-a SQLite）
- `storage/json_repo.py`（P0）：JsonManifestRepository，每 run 一个 manifest.json
- `storage/sqlite_repo.py`（P2-a）：SqliteRepository，5 表关系 schema + migration 机制
  - structural subtyping 满足 ResultRepository Protocol（不继承）
  - 5 表：schema_version / runs / run_metrics / run_residuals / run_steps + 4 索引 + FK CASCADE
  - migration：`schema_version` 表跟踪 + `migrations/v1_initial.sql` + `migrate_v{N}_to_v{N+1}.sql`
  - **双写模式**：`--storage sqlite` 时同时写 SQLite + JSON manifest（runs_root 参数），不破坏旧 JSON 工具链
  - `residuals_history` 不入 SQLite（太大），只 JSON；SQLite `run_residuals` 表只存 final_value
  - `query_metrics` 是 SQLite 独有方法（不在 Protocol，未来供 Web Dashboard 用）
- CLI: `--storage {json,sqlite}` 默认 json + `--db-path PATH` 默认 `runs/cfdb.db`

## cfdb.reporting 全套 SVG（P2-a + P2-c）
- `reporting/svg_residuals.py`（P2-a）: render_residual_svg（单 run 残差曲线，viewBox 680×400）
- `reporting/svg_polar.py`（P2-c）: render_polar_svg（Cl-α + Cd-α 双子图，viewBox 680×800，Okabe-Ito 8 色）
- `reporting/svg_compare.py`（P2-c）: render_cp_comparison_svg（多 solver Cp-x/c，Y 轴反转）+ render_residual_comparison_svg（多 run 残差对比，共享 log 轴）
- `reporting/compare.py`（P2-c）: QoIComparison + compare_runs + render_compare_text/html
  - 跨 case 比较（manifest1.case_id != manifest2.case_id）自动跳过 tolerance 列
  - 缺失 QoI 优雅降级（value 为 None，abs_diff/rel_diff 为 None）
  - HTML 报告内嵌 residual_svg + cp_svg（Optional）
- `reporting/html.py`: generate_run_report（P1-b，单 run）+ generate_multi_solver_report（P2-c，多 run sweep）
- 全部纯 Python 零依赖（仅 import math / dataclass / re），HTML 内嵌 SVG

## cfdb.execution.docker（P2-b 新增）
- `execution/docker.py`: DockerBackend(ExecutionBackend) via structural subtyping
- `__init__(image, pull_policy='missing')`：image 必填，pull_policy 三选一
- 执行流：_check_daemon → _pull_image → _resolve_digest（缓存）→ _build_command → subprocess.run
- digest 解析：RepoDigests 优先（带 registry 前缀 `image@sha256:...`），失败回退 .Id
- bind-mount cwd 同绝对路径；Linux/macOS 加 `--user $(id -u):$(id -g)`，Windows 跳过（vxfsd 处理权限）
- 错误分层：`BackendError`（基础设施）抛异常；`RunResult(exit_code != 0)`（命令失败）正常返回
- 27 测试全 mock subprocess；`real_docker` pytest marker 标记真实 Docker 测试，CI 默认 deselect

## cfdb.data 子包（P2-b 新增）
- `data/__init__.py` + `data/dvc.py`: DVC CLI wrapper（dvc_available/dvc_pull/dvc_status）
- **用 CLI 而非 Python SDK**：DVC Python API 不稳定，CLI 更稳；全 mock subprocess
- `DVC_AVAILABLE` 常量在 import 时求值一次；测试用 `patch("cfdb.data.dvc.dvc_available")` 覆盖
- CLI: `cfdb data status` / `cfdb data pull [targets...]`；DVC 未装时 status 优雅 WARN，pull FAIL exit 1

## cfdb.post.cl_cd（P2-c 新增）
- `post/qoi_extractor.py` 扩展：extract_cl_cd_openfoam（forces.dat 解析末时刻 Fx/Fy → Cl/Cd）
- extract_cl_cd_su2（surface_flow.csv 沿上下表面梯形积分 Cp）
- load_ladson_polar（cases/validation/naca0012/reference/ladson_polar.csv → 4 点 α/Cl/Cd）
- Cl/Cd 公式：Cl = Fy / (0.5·ρ·U∞²·A_ref)，Cd = Fx / (0.5·ρ·U∞²·A_ref)

## NACA0012 α-sweep 4 case（P2-b α=0° + P2-c α=5/10/15°）
- `naca0012_a0`（P2-b）+ `naca0012_a5` / `naca0012_a10` / `naca0012_a15`（P2-c）
- 每个 case 独立 case.yaml，仅差 `alpha_deg` + reference 路径 + solver 参数
- α=15° 近失速 → tolerance 放宽到 0.15（其余 0.05-0.10）
- 共享 geometry STL（cosine spacing 100 点，closed TE coeff -0.1015）
- 参考数据：Ladson 1988（NASA TM-4074）4 点 polar + 每攻角 17 点 Cp 分布
- **dry_run 不产生 cl/cd QoI**（数据限制，非 bug），polar SVG 需要 real solver run 才有数据

## adapter backend 注入（P2-b 核心改造）
- **痛点**：P1-b adapter 在 `run()` 内硬编码 `LocalExecutionBackend()`，但 `SolverAdapter` Protocol（铁律 #3）只约束方法签名
- **方案**：具体 adapter `__init__` 加 `backend: ExecutionBackend | None = None`（Protocol 不约束构造函数）
  - 默认 None → lazy import `LocalExecutionBackend`
  - 显式传 DockerBackend → 容器执行
- `get_adapter(name, dry_run, backend)`：generic 走 P0 路径（不传），openfoam/su2 接受注入
- `Runner._build_backend(name, options)` 替换 `get_backend(name)` 简单工厂；docker lazy import 避免无谓加载
- `RunManifest.backend_options: dict[str, Any] | None = None`（铁律 #2：默认 None）
- `RunManifest.container_digest` 复用 P1-b 预留字段，P2-b 填充 Docker 模式实际值

## pytest marker 分层（P1-b + P2-b）
- `@pytest.mark.real_solver` — 真实 OpenFOAM/SU2 安装才能跑，CI 默认 deselect
- `@pytest.mark.real_docker` — 真实 Docker daemon 才能跑，CI 默认 deselect
- `pyproject.toml` addopts 含 `-m 'not real_solver and not real_docker'`
- 本地手测: `pytest -m real_solver` / `pytest -m real_docker`
- 单元/集成测试用 mock backend（unittest.mock.patch LocalExecutionBackend.execute / subprocess.run）

## 阶段路线图
- **P0（已交付 2026-06-16, commit 5c9948e）**: mock case 闭环，112 测试
- **P1-a（已交付 2026-06-16, commit 4d67403）**: OpenFOAM/SU2 dry_run，158 测试 / 94% cov
- **P1-b（已交付 2026-06-16, commit 4e0b857）**: 真实 OpenFOAM/SU2 subprocess + 残差/QoI 解析，178 测试 / 90.89% cov
- **P2-a（已交付 2026-06-16, commit 81f32bb）**: SQLite 持久化 + 残差 SVG 报告 + P1-b 遗留小项，250 测试 / 91.08% cov
- **P2-b（已交付 2026-06-16, commit 41fbeaa）**: Docker backend + DVC 大文件 + NACA0012 α=0° 单攻角
- **P2-c（已交付 2026-06-16）**: NACA0012 α=0/5/10/15° 多攻角扫描 + Polar/Cp/残差对比 SVG + cfdb compare + cfdb report-sweep，392 测试 / 90.39% cov
- **P3-hotfix（已交付 2026-06-16）**: NACA adapter 6 项修复（nut wall BC / SA freestream init / fvSchemes 等）
- **P3-tail（进行中 2026-06-17, 详见 2026-06-17.md）**: Docker 真跑闭环 + QoI Critical bug 修复（级联发散反向扫描错误）+ qoi_extractor 测试覆盖 77%→92% + SA 发散缓解（div(phi,nuTilda) upwind）。4 个 α-sweep case 在 opencfd/openfoam-run:2406 镜像下全部真跑成功，α=0 对 Ladson 误差 Cl 0.2% / Cd 13%
- **P3 候选**: ML surrogate adapter（AirfRANS 推理 only）+ Web Dashboard（FastAPI + Jinja2 + Plotly.js）+ Fluent adapter + Slurm backend

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
- **真实数据是最好的测试**：合成 forces.dat 单测全过，真实 OpenCFD forces.dat 立刻暴露级联发散回滚逻辑的反向扫描错误（P3-tail Critical bug）
- **OpenCFD image env 已 baked-in**：`opencfd/openfoam-run:2406` PATH 含 OpenFOAM bin，无需 `source bashrc`，但残留文档假设要 source
- **SA 在粗网格 high-y+ 易发散**：`div(phi,nuTilda)=linearUpwind` 不稳定，改为 `upwind`（airFoil2D tutorial 默认）；QoI rollback 是安全网而非根治
- **P3.1-SST Phase 8 (Plan C-1, 2026-06-18) — Cl 偏低 4 倍的根因**：
  - **两个叠加 bug**：(1) STL z 错位（z∈[0,0.1] vs blockMesh z∈[-0.05,+0.05]）；(2) snappyHexMesh curvature refinement 从未触发（fan-triangulated STL 无 sharp feature edges），LE 周向仅 ~12 cells，无法解析 Cp suction peak
  - **修复**：(1) `gen_geometry.py:write_stl` 加 `z_center=0.0` 参数让 STL z 对称于 0；(2) snappyHexMeshDict 加 `leRefinementBox`（x∈[0,0.05], y∈[-0.02,0.02], level 7）+ maxLocalCells 200k→300k
  - **效果**：a5 Cl 从 0.107 → **0.439**（Ladson 0.456，误差 3.8%）；Cd 从 0.053 → 0.016（仍偏高 69%，怀疑未完全收敛或远场 BC 反射）
  - **教训**：先验证网格生成器输出（STL z 范围、cells per refinement level、layer coverage）再调湍流模型参数；之前 7 轮迭代都在改 SA/SST，根因却在几何/网格
