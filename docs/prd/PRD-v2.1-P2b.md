# CFD-Benchmark PRD v2.1 — P2-b（可重现性 + 内容扩展）

## 1. 文档信息

| 项 | 内容 |
|---|---|
| 版本 | v2.1（P2-b 增量） |
| 日期 | 2026-06-17 |
| 作者 | 许清楚（Xu）· 产品经理 |
| 状态 | **Confirmed**（转总已拍板 4 核心决策，见 §3） |
| 基于 | PRD-v2.0-P2-roadmap.md（P2 路线图）、Architecture-v2.0-P2a.md（P2-a 基线） |
| 基线 | P0（5c9948e，112 测试）+ P1-a（4d67403，158 测试）+ P1-b（4e0b857，178 测试）+ P2-a（81f32bb，**250 测试 / 91.08% cov**） |
| 工作目录 | `D:\GLM-CFD-Benchmark` |

---

## 2. P2-b 战略定位

> **让 CFD benchmark 结果在任何环境下可复现、并在经典外流 case 上展示多 solver 对比价值。**

P2-b 是 PRD-v2.0 §5 推荐分批方案的第二批，三大方向形成协同闭环：

```
Docker (A) ─┐
            ├─→ 一致运行环境 ─┐
DVC (B) ────┘                  ├─→ NACA0012 (E)
                               │   经典外流 + 双 solver 对比
   网格+参考数据版本化 ─────────┘
```

| 方向 | 解决的痛点 |
|---|---|
| **A. Docker backend** | P1-b 遗留痛点 #1：仅本机执行，CI 无法自动验证真实 solver |
| **B. DVC 大文件管理** | P1-b 遗留痛点 #2：大网格手工 git（>10MB 不可行），参考数据无版本 |
| **E. NACA0012** | P1-b 遗留痛点 #6：无经典外流验证（仅内流 cavity + 边界层 flat plate） |

---

## 3. 转总拍板的核心决策（4 项，全部确认）

> 对应 PRD-v2.0 §6 的 Q2 / Q3 / Q6 + 架构师补充的镜像选型问题。

### 3.1 ⭐⭐⭐ Q2 → **A. Docker 完整支持**

实现 `DockerBackend(ExecutionBackend)`，用户可 `--backend docker --image openfoam/openfoam:v2406`，`container_digest` 写入 manifest。

**理由**：Architecture-v1 §14 已预留 `ExecutionBackend` 扩展点；完整支持让本地用户也能用 Docker 保证可复现性；container_digest 是可复现性的核心承诺。

### 3.2 ⭐⭐ Q3 → **B. DVC 管理网格 + 参考数据**

DVC track 范围：
- **网格文件**（NACA0012 snappyHexMesh 输出 / `.su2` mesh / `blockMeshDict` 生成的 mesh）
- **参考数据**（Ladson 1988 实验数据 CSV / 后续 AirfRANS 数据集）

**不纳入 DVC**：日志、场数据等 run 产物（由 manifest 管理，本就在 `runs/` 隔离）。

**理由**：网格和参考数据是"输入"，需版本化保证可复现；run 产物是"输出"，由 manifest 管理；全仓 track 是过度工程化。

### 3.3 ⭐⭐ Q6 → **B. OpenFOAM + SU2 单攻角对比（α=0°）**

两 solver 各跑 α=0°，Cp 分布对比 Ladson 1988 实验数据。

**理由**：多 solver 对比是项目核心价值（PRD-v1 §2 痛点 3）；多攻角扫描工作量翻 4 倍（4× 网格 + 4× 求解 + Cl/Cd 后处理），推 P2-c 或 P3；单 solver 缺乏对比价值。

### 3.4 ⭐⭐ 镜像选型 → **A. 官方镜像**

直接用 `openfoam/openfoam:v2406` 和 `su2code/su2` 等官方镜像。

**理由**：零维护成本；版本由上游保证；manifest 记录 `image:tag@digest` 保证可复现；自建镜像维护成本高，CI 需自建 registry。

---

## 4. 范围明细

### 4.1 A. Docker backend

| 子项 | 说明 |
|---|---|
| `DockerBackend(ExecutionBackend)` | 新增 `src/cfdb/execution/docker.py`，实现 `execute(command, cwd, timeout, env) → RunResult` |
| CLI flag | `--backend {local,docker}` 默认 local；`--image <name:tag[@digest]>`；`--pull {always,missing,never}` 默认 missing |
| 镜像拉取 | 执行前 `docker pull`（按 `--pull` 策略），失败 raise `BackendError` |
| 挂载方案 | `cwd` 整目录 bind mount 到容器内同名路径（`-v <cwd>:<cwd>`），避免路径转换 |
| Workdir | 容器内 `--workdir <cwd>` 与宿主一致，命令内相对路径无需改写 |
| Container cleanup | `--rm` 自动清理；timeout 时 `docker stop <cid>` |
| digest 提取 | 执行前 `docker inspect --format='{{.Image}}' <image>` 取 image digest，写入 `RunManifest.container_digest`（已有字段，P1-b 保留为 None） |
| 环境变量 | `env` dict 通过 `-e KEY=VAL` 注入 |
| 失败模式 | 镜像不存在 → `BackendError("image not found: ...")`；daemon 未运行 → `BackendError("docker daemon not reachable: ...")`；timeout → `RunResult(timed_out=True)` + 写日志 |

### 4.2 B. DVC 大文件管理

| 子项 | 说明 |
|---|---|
| `.dvc/config` | 配置 DVC remote（默认本地 `runs/dvc-cache/`，可指向 S3/MinIO/Aliyun OSS） |
| `dvc.yaml` | pipeline 定义（NACA0012 网格生成 stage + Ladson 1988 数据 fetch stage） |
| `src/cfdb/data/__init__.py` | DVC 包装辅助函数：`dvc_available()` / `dvc_pull(targets)` / `dvc_status()` |
| Track 范围 | `cases/validation/naca0012/mesh/`（snappyHexMesh 输出）+ `cases/validation/naca0012/reference/ladson1988.csv` |
| CLI 集成 | 新增 `cfdb data pull` / `cfdb data status`（封装 DVC 子命令） |
| 回退机制 | 无 DVC 安装时，`dvc_available()` 返回 False，CLI 提示用户安装 DVC，不阻塞其他命令 |

### 4.3 E. NACA0012 case

| 子项 | 说明 |
|---|---|
| 几何生成 | `cases/validation/naca0012/gen_geometry.py`，纯 numpy 实现 NACA 4-digit 翼型坐标生成（cosine spacing，200 点） |
| OpenFOAM 网格 | `snappyHexMesh` 配置（背景六面体网格 + 翼型 STL + 边界层 + refinement zone） |
| SU2 网格 | `SU2_DEF` + `SU2_MSH` 或外部 GMSH → `.su2` 文件（先用占位 mesh，真实网格生成由 QA 验证） |
| Ladson 1988 数据 | `cases/validation/naca0012/reference/ladson1988.csv`，α=0° 的 Cp 分布（x/c vs Cp），来自 NASA TM-4074 |
| case.yaml | `case_id: naca0012_a0`，`category: validation`，两个 solver_config（openfoam + su2），α=0° 边界条件 |
| QoI 提取 | `src/cfdb/post/qoi_extractor.py` 加 `extract_naca0012_cp_openfoam`（forces object CSV）+ `extract_naca0012_cp_su2`（surface_flow.csv） |
| 验证指标 | `max(|Cp_num - Cp_exp|)` 在 x/c = 0.0/0.25/0.5/0.75/1.0 五点对比 |

---

## 5. Schema 增量设计（草案，架构师细化）

### 5.1 `RunManifest` 复用已有 `container_digest`

P1-b 预留字段 `container_digest: str | None`，P2-b 填充实际值（Docker 模式下）。

### 5.2 新增 `RunManifest.backend_options`

```python
backend_options: dict[str, Any] | None = None
"""Backend-specific options. For Docker: {image, digest, pull_policy, workdir}.
For local backend: None. None for P0/P1-a/P1-b manifests (backwards compatible)."""
```

**铁律 #2 约束**：默认 None，不破坏旧 manifest。

### 5.3 `ExecutionBackend` Protocol 不动（铁律 #3）

`DockerBackend` 通过 structural subtyping 满足 Protocol，与 `LocalExecutionBackend` 同级。

### 5.4 Case schema 新增 `geometry_generator`

```python
# In CaseSpec 或 SolverConfig
mesh: MeshSpec | None = None
"""Mesh specification. If geometry_generator is provided, mesh is generated
dynamically; else static mesh files are expected in case_dir."""

# MeshSpec
class MeshSpec(BaseModel):
    generator: Literal["naca4digit", "snappyhexmesh", "external"] | None = None
    parameters: dict[str, Any] | None = None  # e.g. {"chord": 1.0, "n_points": 200}
    files: list[str] | None = None  # static mesh files (relative to case_dir)
```

---

## 6. 验收铁律（5 条，QA 独立验证）

| # | 铁律 | 验证方法 |
|---|---|---|
| **#1** | 不破坏 P0+P1-a+P1-b+P2-a 的 **250** 个测试 | `pytest` 全跑，确认 250 个全过 |
| **#2** | Schema 只新增 Optional 字段，不改已有字段 | grep `RunManifest` 已有字段定义，确认未修改 |
| **#3** | `ExecutionBackend` Protocol 不动 | grep `class ExecutionBackend`，确认 Protocol 定义未改 |
| **#4** | JSON 存储保持兼容（`--storage json` 默认） | grep CLI `--storage` 默认值仍为 json |
| **#5** | DockerBackend 测试不依赖真实 Docker daemon | 全部测试用 mock（`unittest.mock.patch` subprocess），`-m real_docker` marker 标记真实 Docker 测试 |

---

## 7. P2-b 完成定义（DoD）

| 维度 | 完成标准 |
|---|---|
| **功能** | ①`DockerBackend` 实现 `ExecutionBackend` Protocol，`--backend docker --image ...` 可切换；②`container_digest` 在 Docker 模式写入 manifest；③DVC 配置就绪，`cases/validation/naca0012/mesh/` 和 `reference/ladson1988.csv` 纳入版本化；④`cfdb data pull/status` CLI 可用；⑤NACA0012 case 配置完整（case.yaml + 几何生成 + 模板 + Ladson 参考数据）；⑥Cp 提取器对比 Ladson 1988 |
| **测试** | pytest 总数 ≥ **235**（250 + ~30 新测试 - 不 deselected）；覆盖率 ≥ **88%**；P0+P1-a+P1-b+P2-a 回归全过 |
| **兼容性** | JSON manifest 仍可用；local backend 仍默认；无 Docker 安装时 DockerBackend 抛清晰错误 |
| **文档** | 更新 README：Docker backend 用法 + DVC 用法 + NACA0012 示例；新增 Architecture-v2.1-P2b.md |
| **CI** | CI 默认 local backend + JSON 存储（保持轻量）；`-m real_docker` 真实 Docker 测试 CI 跳过 |
| **里程碑** | git commit `P2-b: Docker backend + DVC large files + NACA0012 OF/SU2 validation` |

---

## 8. 协同与风险

### 8.1 协同依赖

```
A Docker ─┐
          ├─→ E NACA0012（一致环境多 solver）
B DVC ────┘    （网格版本化）
```

- **A → E**：NACA0012 在 Docker 内跑保证可复现
- **B → E**：NACA0012 snappyHexMesh 网格（>10MB）需 DVC 管理

### 8.2 风险

| 风险 | 缓解 |
|---|---|
| snappyHexMesh 调试成本高 | 先用粗网格（~50K cells）跑通流程，refinement zone 逐步细化 |
| Windows 用户 Docker Desktop 依赖 | 文档明确说明，提供 local backend 回退路径 |
| DVC remote 维护 | 默认本地 cache，CI 文档说明如何配 S3/MinIO |
| NACA0012 SU2 网格生成工具链复杂 | 第一版 SU2 用占位 mesh（同 P1-b flat_plate 模式），QA 真实验证 |
| Docker daemon 检测在 CI/Windows 行为差异 | 提供清晰的 BackendError 消息，列出诊断步骤 |

---

## 9. 不在 P2-b 范围（明确推迟）

| # | 事项 | 推迟到 |
|---|---|---|
| 1 | Docker 自建镜像（含额外工具如 gmsh） | P3（按需） |
| 2 | NACA0012 多攻角扫描（α=0/5/10/15°） | P2-c / P3 |
| 3 | Cl/Cd 极曲线对比 | P2-c / P3 |
| 4 | Fluent / STAR-CCM+ adapter | P3（按需，需商业许可证） |
| 5 | Slurm backend | P3（按需，需 HPC 集群） |
| 6 | Web Dashboard 多 case 对比 | P2-c（依赖 P2-a SQLite） |
| 7 | ML surrogate adapter（AirfRANS） | P2-c（依赖 DVC） |

---

## 10. 未决事项（实施中架构师定夺）

| # | 事项 | 默认建议 |
|---|---|---|
| 1 | Docker user/uid 映射（容器内 root 写宿主目录权限问题） | 用 `--user $(id -u):$(id -g)`，Windows 跳过 |
| 2 | DVC remote 默认位置 | `runs/dvc-cache/`（项目内，gitignore） |
| 3 | NACA0012 网格规模 | 第一版 ~50K cells（验证流程），后续可细化 |
| 4 | Ladson 1988 数据获取 | 手工录入 x/c=0,0.25,0.5,0.75,1.0 五点 Cp 值（数据公开） |
| 5 | SU2 NACA0012 mesh 生成 | 第一版用占位 mesh（架构 §15.4 同 P1-b 决策） |

---

*文档结束。转总已拍板 §3 四决策。架构师据此做 P2-b 增量设计（Architecture-v2.1-P2b.md），工程师据此实施。*
