# Architecture v4.0 — 从跑分器到可信度平台（Trust Platform）

> 状态：DESIGN（本文件 = v4 升级波的实现契约 SSOT）
> 上承：Architecture-v3.0-P3hotfix.md（P0→P3 的分层 Protocol 架构不动）
> 立论：benchmark 不是排行榜，是课题组自己的 **truth set + failure mode library +
> 冻结尺子**。v4 把这三样从「理念」变成本仓的一等公民，并让平台能给
> AI agent 的 CFD 产出打分（agent-eval）——benchmark 成为 agentic CFD 科研的裁判所。

## 0. 不可动地板（诚实宪法，全模块共守）

- **假绿是死罪**：任何 pass/verdict 只能由重算值驱动，绝不接受自报值。
- **fail-closed**：缺 baseline ≠ pass；缺出处 = DECLARED-NOT-VERIFIED；frozen 漂移 = 拒绝打分（exit 3）。
- **出处分级**：REAL（experimental/dns 且引文齐）> ANALYTIC/MANUFACTURED > PREVIOUS_RUN > SURROGATE。
  低级出处的「验证通过」必须带级别标注，绝不冒充 REAL 验证。
- **画像 ≠ 判决**：TrustProfile 是能力画像，ReleaseGate/agent-eval 是判决；两者不可互充。
- **人签专属**：baseline promote 只能人显式执行（CLI 必填 --engineer），自动晋升结构性不可达。
- 每个新 gate 必须有 **tamper witness 测试**（篡改必咬），「全绿」无咬合实证 = 假信心。

## 1. 新模块总览（six pillars，全部挂在既有分层之下）

```
src/cfdb/
├── provenance/    # P4-A 出处与诚实分级
├── trust/         # P4-B TrustProfile 多维画像（VVUQ）
├── failures/      # P4-C 失败模式库（失败资产化）
├── regression/    # P4-D baseline 治理 + 回归门（fail-closed）
├── agentbench/    # P4-E 冻结尺子 + agent 提交打分
└── reporting/showcase.py  # P4-F 单文件 showcase HTML（workshop 配套）
```

各模块只依赖：`schema.py` 既有模型、`storage/` Repository 读、标准库。
模块之间 v4 波内**互不 import**（CLI 层组合），保证并行实现零冲突。

## 2. P4-A provenance/ — 出处与诚实分级

文件：`src/cfdb/provenance/__init__.py`, `records.py`, `audit.py`
测试：`tests/test_provenance.py`

```python
HonestyLevel = Literal["REAL", "ANALYTIC", "MANUFACTURED", "PREVIOUS_RUN",
                       "SURROGATE", "DECLARED-NOT-VERIFIED"]

class ProvenanceRecord(BaseModel):   # extra='forbid'
    case_id: str
    reference_type: str              # 镜像 ReferenceSpec.type
    citation: str | None             # 例 "Ladson, NASA TM-4074, 1988"
    source_url: str | None
    retrieved: str | None            # ISO date
    file_hashes: dict[str, str]      # 相对路径 -> sha256（reference files 逐个哈希）
    honesty: HonestyLevel            # 由 derive_honesty() 机械派生，不许手填覆盖
```

- `derive_honesty(reference_type, citation) -> HonestyLevel`：
  experimental/dns + citation 非空 → REAL；experimental/dns 缺 citation → **DECLARED-NOT-VERIFIED**
  （fail-closed：声称是实验数据但给不出引文 = 不可核）；analytical → ANALYTIC；
  manufactured → MANUFACTURED；previous_run → PREVIOUS_RUN；无 reference → SURROGATE。
- 出处声明文件：`cases/<cat>/<id>/provenance.yaml`（citation/source_url/retrieved 三字段，人写）。
- `audit_all(cases_dir) -> list[ProvenanceRecord]`：扫全部 case，验 reference 文件 sha256
  是否与 provenance.yaml 中锚定值一致（漂移 → honesty 降为 DECLARED-NOT-VERIFIED + note）。
- CLI：`cfdb provenance` 打表：case · type · honesty · citation · hash 校验状态。
- **NACA0012 系列必须补 provenance.yaml**（Ladson 1988, NASA TM-4074），这是 REAL 样板。
  侦察事实：4 个 `ladson1988*.csv` 均无出处头（仅 ladson_polar.csv 首行有引注）——
  provenance.yaml 补引文 + 锚定现文件 sha256；**绝不改 csv 数据内容**。转录未与原表
  核对这一点如实写进 record 的 note（`transcription_verified: false`），诚实边界。
- tamper witness：改 reference 文件一个字节 → audit 必降级；experimental 删 citation → 必 DNV。

## 3. P4-B trust/ — TrustProfile 多维画像

文件：`src/cfdb/trust/__init__.py`, `profile.py`, `radar_svg.py`
测试：`tests/test_trust_profile.py`

```python
class DimensionScore(BaseModel):     # extra='forbid'
    score: float | None              # 0..1；None = 数据不足（绝不编 0 冒充差）
    evidence: list[str]              # 人可读的依据行（含数字）
class TrustProfile(BaseModel):
    case_id: str; solver: str
    n_runs: int
    honesty: HonestyLevel            # 直取 provenance（画像顶栏常驻）
    accuracy: DimensionScore         # 1 - clamp(mean_rel_err / tolerance)，多 QoI 取最差
    robustness: DimensionScore       # success 数 / 总 run 数
    efficiency: DimensionScore       # 1 - clamp(wall_time / budget)；无 budget → None
    completeness: DimensionScore     # 期望产物（fields/curves/qoi）实到率，取 run 最新
    reproducibility: DimensionScore  # 同 (case,solver) ≥2 次 success 的 QoI 变异系数映射；<2 次 → None
    notes: list[str]
```

- 输入 = 既有 `runs/` 目录的 manifest.json + metrics.json（Repository 读，不新增存储）。
- **无聚合总分**（刻意）：五维雷达 + honesty 顶栏；总分诱导排行榜心智，与立论冲突。
- `radar_svg.render(profile) -> str`：五轴雷达 SVG（同 reporting/ 现有 SVG 生成器风格，无外部依赖）；
  None 维度画虚线缺口 + 「数据不足」标注，**绝不画成 0**。
- CLI：`cfdb trust -c <case> -s <solver>` → 终端表 + `--json` + `--svg <path>`。

## 4. P4-C failures/ — 失败模式库

文件：`src/cfdb/failures/__init__.py`, `taxonomy.py`, `library.py`
测试：`tests/test_failure_library.py`

```python
FailureMode = Literal["MESH_FAILURE", "DIVERGENCE", "TIMEOUT", "MISSING_ARTIFACT",
                      "MISSING_REFERENCE", "TOLERANCE_EXCEEDED", "SETUP_ERROR",
                      "ENV_MISSING", "UNKNOWN"]
class FailureRecord(BaseModel):      # extra='forbid'
    fingerprint: str                 # sha256(case_id|solver|mode|signature)[:16]
    case_id: str; solver: str
    mode: FailureMode
    signature: str                   # 稳定摘要（如 "step=snappy_mesh exit=1"）
    first_seen: str; last_seen: str  # run_id
    count: int
    evidence: list[str]              # 指向 run 产物的相对路径
    guard: str | None                # 人写的护栏注记（下轮怎么防）
```

- `classify(manifest, metrics) -> FailureMode | None`（success+pass → None）：
  timed_out→TIMEOUT；step_details 里 mesh 类 step 非零→MESH_FAILURE；
  final_residuals 存在且超发散阈→DIVERGENCE；notes 含 missing computed→MISSING_ARTIFACT、
  missing reference→MISSING_REFERENCE；qoi 超容差→TOLERANCE_EXCEEDED；
  命令 not found/ENOENT→ENV_MISSING；其余非零退出→SETUP_ERROR；兜底 UNKNOWN。
- `FailureLibrary`（`failures/library.json`，append-only + fingerprint 去重计数）：
  `ingest(runs_dir)` 扫全部 run 增量入库；`annotate(fingerprint, guard=...)` 人写护栏。
- CLI：`cfdb failures ingest` / `cfdb failures list [--mode X]` / `cfdb failures annotate <fp> --guard "..."`。
- 红线：库文件只增不删（教训是资产）；同一 fingerprint 重复失败 count++ 并更新 last_seen。

## 5. P4-D regression/ — baseline 治理 + 回归门

文件：`src/cfdb/regression/__init__.py`, `baseline.py`, `gate.py`
测试：`tests/test_regression_gate.py`

```python
class BaselineEntry(BaseModel):      # extra='forbid'
    case_id: str; solver: str
    run_id: str
    promoted_by: str                 # 工程师名（必填，无默认）
    promoted_at: str                 # ISO UTC
    qoi_values: dict[str, float]     # promote 时从 run 的 metrics.json 抄录锚定
    qoi_relative_errors: dict[str, float]
    metrics_sha256: str              # 该 run metrics.json 的文件哈希（防事后篡改）
class GateVerdict(BaseModel):
    verdict: Literal["PASS", "REGRESSION", "NO_BASELINE", "TAMPERED", "INVALID_RUN"]
    deltas: dict[str, float]         # 新旧相对误差之差（正 = 变差）
    reasons: list[str]
```

- BaselineStore = `baselines/baselines.json`。`promote(run_id, engineer)`：run 必须
  overall_status == "pass" 才可晋升（fail 的 run 结构性不可成为 baseline）。
- `evaluate(run_id) -> GateVerdict`（**重算，不信任何自报**）：
  1. 重读 run 的 metrics.json；status != success/pass 维度先判 INVALID_RUN；
  2. baseline 的 metrics_sha256 与其 run 目录现存文件比对，不符 → **TAMPERED**（fail-closed）；
  3. 无 baseline → NO_BASELINE（**不是 PASS**）；
  4. 每 QoI：new_err > base_err + max(0.005, 0.1·base_err) → REGRESSION（容忍带公开可调，
     配置于 baselines.json 顶层 `regression_margin`，默认如上）。
- CLI：`cfdb baseline list` / `cfdb baseline promote <run_id> --engineer <name>` /
  `cfdb gate <run_id>`（exit 0=PASS，1=REGRESSION/INVALID，2=NO_BASELINE，3=TAMPERED）。
- tamper witness：改 baseline 锚定 run 的 metrics.json 一个数字 → gate 必 TAMPERED；
  改 baselines.json 的 qoi 值绕哈希 → 与重读的 run 文件对不上 → 必咬。

## 6. P4-E agentbench/ — 冻结尺子 + agent 提交打分

文件：`src/cfdb/agentbench/__init__.py`, `contract.py`, `scorer.py`
测试：`tests/test_agentbench.py`

设计忠于 auto-research-loop 引擎的公理（frozen scorer / 改尺子=exit3 / 作废样本 / 透明权重）：

```python
class ScoringContract(BaseModel):    # extra='forbid'
    contract_version: Literal["1"]
    case_id: str
    frozen: dict[str, str]           # 冻结路径 -> sha256（case.yaml、reference/*、本合同的 weights）
    weights: dict[str, float]        # 公开权重，例 {"qoi_error": -1.0, "wall_time_sec": -0.001}
    validity_gates: list[str]        # 例 ["qoi_complete", "within_budget"]
class SubmissionScore(BaseModel):
    submission_id: str
    valid: bool                      # 任一 validity gate 不过 = 作废（不参与排序）
    score: float | None              # 作废 = None（绝不给作废样本编分）
    breakdown: dict[str, float]
    gates: dict[str, bool]
    scored_at: str
```

- `cfdb agent-eval init -c <case>`：生成 `agentbench/<case>/contract.json`（哈希当时的冻结物）。
- `cfdb agent-eval score -c <case> --submission <dir>`：
  1. **先验合同**：重哈希全部 frozen 路径，任一漂移 → stderr 报路径 + **exit 3 拒绝打分**（改尺子）；
  2. submission 目录读 `qoi.json`（+ 可选 `manifest.json`）；validity gates 重算；
  3. score = Σ weights·指标（qoi_error 对 case reference 重算，绝不信 submission 自报误差）；
  4. 追加 `agentbench/<case>/ledger.jsonl`（append-only 打分账本）。
- `cfdb agent-eval ledger -c <case>`：打表历史提交（valid/score/时间）。
- tamper witness：改 reference 一字节 → score 必 exit 3；submission 伪造 qoi_error 字段 → 忽略自报、重算。

## 7. P4-F reporting/showcase.py — 单文件 showcase（workshop 配套）

文件：`src/cfdb/reporting/showcase.py` + `reporting/templates/showcase.html.j2`
测试：`tests/test_showcase.py`

- `cfdb showcase [--out showcase.html]`：产**自包含单文件 HTML**（内联 CSS/SVG，零外链），
  设计语言对齐 workshop：暖纸 #F6F2EA 底 · clay #C15F3C 唯一强调 · serif 标题 + mono 数据 ·
  语义色（evidence 绿只给 REAL 出处，风险红给 failure）。
- 版块：① 立论头（benchmark = truth set + failure library + frozen scorer）
  ② 真值集出处表（provenance audit 实况：Ladson 1988 REAL 徽 vs 其他级别如实标）
  ③ TrustProfile 雷达（有 run 数据的 case×solver；无数据版块诚实留白「尚无 run」）
  ④ 失败模式库墙（mode 分桶计数 + guard 注记摘录；空库 = 如实空态）
  ⑤ 回归门状态（baseline 数 + 最近 gate 判决）
  ⑥ agent-eval 账本摘要（合同哈希前 8 位常驻展示 = 「尺子编号」）。
- **全部数字取自真实产物文件**（runs/ baselines/ failures/ agentbench/），无数据的版块
  显式空态文案，绝不渲示例假数据。页脚固定一行诚实边界声明。

## 7.5 P4-G metrics/ 诚实硬化（侦察实证的三个静默放行洞，fail-closed 收口）

文件：`src/cfdb/metrics/engine.py`, `src/cfdb/schema.py`（本波**唯二共享文件，仅本任务可改**）
测试：`tests/test_metrics_hardening.py`（+ 保证既有 590 测不回归）

1. **ref==0 豁免洞**（engine.py:84-89）：参考值为 0 的 QoI 现在被静默跳过（naca0012_a0
   的 cl=0.0 + 容差 0.001 实际从不检查）。修：`MetricSpec` 增 `qoi_absolute_tolerance:
   dict[str, float]`（默认空）；ref==0 时若配了绝对容差 → 用 |computed-ref| 判定；
   没配 → note 升级为 `missing absolute tolerance for zero-reference QoI 'x'` 并计入
   missing → **status=incomplete（不再 silent pass）**。同步给 `cases/validation/naca0012/case.yaml`
   的 cl 配 `qoi_absolute_tolerance: {cl: 0.01}`。
2. **未配容差洞**（engine.py:97）：outputs.qoi 里声明、误差算了、但 tolerance 没配的 QoI
   不参与判定。保持不 gate（兼容），但 `MetricsResult` 增 `ungated_qoi: list[str]`
   字段（默认空 list）+ note，让报告/展示层能如实揭示「这些数字没有被门约束」。
3. **budget 永不 fail**：保持警告语义（兼容），但 `MetricsResult` 增 `budget_exceeded: bool`
   （默认 False），showcase/trust 的 efficiency 维度消费它。
- 红线：改动后全套既有测试必须仍绿；每个洞一条回归测试 + 一条 tamper witness
  （例：把 naca0012_a0 的 cl 绝对容差删掉 → 必 incomplete）。

## 8. 平台修缮（随波顺手，独立小项）

- `tests/test_docker_backend.py::TestWindowsPathCompatibility` 3 测在 POSIX 主机因
  `Path("D:/...")` 非绝对而假红 → 加 `@pytest.mark.skipif(os.name != "nt", ...)` 或
  重写为平台无关断言（优先后者，若 30 分钟内可平台无关化）。
- `dvc.yaml` 的 `wdir: ..` 解析到仓外父目录（疑迁移遗留），deps/outs 失配 → 修为仓内正确
  相对路径并本地验证 `dvc status` 不再报路径错（无 dvc 环境则至少路径静态自洽）。
- 侦察额外事实（写进 PRD-v4 backlog，不本波实现）：curve_l2 死代码未入判定、
  registry 坏 yaml 静默跳过、`report` 命令硬编码 json repo、SU2 Cd 积分仅趋势级。

## 9. CLI 接线（各模块完成后统一收口，避免 cli.py 竞写）

新命令组：`provenance` / `trust` / `failures` / `baseline` / `gate` / `agent-eval` / `showcase`。
模块 API 先行、CLI 薄壳后接；每命令一个 e2e 冒烟测试进 `tests/test_cli_v4.py`。

## 10. 验收标准（收口 gate）

1. 全测试绿（含既有 590 + 新模块），POSIX 平台假红清零；
2. 每个新 gate 至少一条 tamper witness 测试（§2/§5/§6 各自的「必咬」清单）；
3. `cfdb showcase` 在本仓真实数据上产出自包含 HTML 并可 open；
4. ruff + pyright basic 通过；
5. workshop `validate.py` 保持绿（scene-17 增量不破冻结门）。
