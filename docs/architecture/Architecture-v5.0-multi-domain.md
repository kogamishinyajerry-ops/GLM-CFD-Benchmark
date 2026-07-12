# Architecture v5.0 — 多域可信度平台（CFD + Coding + Agentic）

> 状态：DESIGN（本文件 = v5 升级波的实现契约 SSOT）
> 上承：Architecture-v4.0-trust-platform.md（六柱信任机械不动，§0 诚实地板全文继承）
> 立论：v4 证明了「truth set + failure library + 冻结尺子」在 CFD 域可运转；v5 的命题是
> **信任机械是域无关的，域只是插件**——同一套出处锚定/人签 baseline/fail-closed 门/
> append-only 账本，给 CFD 数值、coding 提交、agentic 任务产出打同一等级的可信分。
> 侦察依据：六路并行侦察（2026-07-11，4 仓内 + 2 外部判定学，锚点见各节引注）。

## 0. 宪法增补（v4 §0 全文继承，新增三条）

- **§0.7 判定器完整性入锚**：checker 脚本、hidden tests、normalize 规则等一切「判定材料」
  与 reference 数据同等待遇——sha256 进 frozen map，漂移=exit 3 拒判。评测材料对被评对象
  **不可写**；可见性按域声明（coding hidden tests 生成期不可见）。
  依据：BenchJack（arXiv:2605.12673）实证 8-10 个主流 benchmark 被攻破的共同根因 =
  评测代码对 agent 可写/可注入；RewardHackingAgents（arXiv:2603.11337）唯 evaluator
  hash-locking + 访问阻断组合才能同时挡住两条攻击向量。
- **§0.8 LLM 永不进判决链**：LLM-as-judge 有实证的非零残余不可复现率（T=0 + 多 judge
  投票 + 固定版本全套缓解后仍存在）+ self-preference/position bias。其输出**最多**作为
  trust 画像的显式标注弱信号（档位语义 = SURROGATE 级，UI/CLI 强制视觉区隔），
  绝不驱动任何 pass/verdict/score/rank。v5.0 只留槽位不实现。
- **§0.9 exit 3 = 尺子/锚漂移**：v4 中 regression gate 与 agentbench 已各自约定 exit 3
  （TAMPERED / FROZEN_DRIFT），升格为跨域宪法约定——一切新域 gate 的「判定材料被动过」
  一律 exit 3，绝不复用其他语义。

## 1. 域模型（改动总闸门 = schema.py 一处）

侦察结论：核心引擎链（metrics/trust/agentbench/provenance/regression/failures）只消费
结构性字段（id/category/solvers/outputs/reference/metrics/budget）；physics 全仓**零消费**
（grep 实证），conditions 仅 3 个 CFD adapter 读取；category 字面量全仓仅 schema.py:231 一处。

```python
# schema.py 改动（全部向后兼容，既有 case.yaml 零修改）：
domain: Literal["cfd", "coding", "agentic"] = "cfd"     # 新增，默认 cfd
physics: PhysicsSpec | None = None                        # 必填 → Optional（零消费实证）
conditions: ConditionsSpec = Field(default_factory=ConditionsSpec)  # 必填 → 默认工厂
category: ...   # 不动。V&V 语义天然跨域：smoke=管线通/verification=机械正确性/validation=对照真值
```

- **domain 与 category 正交**：coding validation case = 对照黄金解；agentic smoke = mock 终态断言。
- 目录布局不改层级（registry._scan 已 domain-agnostic）：新增 `cases/coding_tasks/`、
  `cases/agentic_tasks/` 顶层目录即可被扫描（注意避开 category 同名目录混淆，目录名
  仅是组织习惯，registry 不校验）。
- **safe-refactor 硬要求**：physics 改 Optional 前必须枚举全部 CaseSpec 消费方
  （fan-in 两轮统计口径有差：侦察 23 src+24 test，审计复测 26 src+25 test——
  落地前以当时 grep 重新精确统计为准）——侦察只证了静态零消费，改动 PR 里要附消费者清单。
- ReferenceSpec.type 五值与 HonestyLevel 原样复用（语义已域中立：analytical=已知正确解，
  manufactured=合成用例）；不新增枚举值。

## 2. Wave A — 域无关地基（先修缝再扩域）

侦察抓到的三个**方向性违宪缝**，扩域前必须收口：

1. **A1 generic adapter 沙箱 fail-open**（adapters/__init__.py:43-45 + generic_command.py:34-36）：
   `--backend docker` 对 generic adapter 静默失效、实际本机无隔离执行。修：
   GenericCommandAdapter 接受 backend 注入；CaseSpec 新增 `execution.requires_sandbox: bool = False`；
   requires_sandbox=True 而 backend 非 sandbox profile → **拒跑报错**（fail-closed），绝不静默降级。
   tamper witness：sandbox case + local backend → 必须拒绝执行。
2. **A2 registry fail-open 与 provenance fail-closed 语义分裂**（registry.py:53-61 静默跳过 vs
   audit.py:222-225 降级记录）：registry._scan 记录 skipped 清单（case_id/路径/原因），
   `list-cases` 末尾如实打印 `N skipped (invalid)`，exit code 保持 0 但绝不隐身。
   witness：坏 yaml 入库 → list-cases 必须可见。
3. **A3 metrics/qoi.py 死代码分叉**（ref==0 静默跳过的 P0 旧实现仍存活，仅 test_metrics.py
   引用）：**删除**，测试改指 engine 路径——防止新域 judge 误引用重新引入 v4 已修的假绿洞。
4. A4 失败桶扩域（taxonomy.py）：FailureMode 增 `BUILD_FAILURE`/`TEST_FAILURE`/
   `WRONG_ANSWER`/`RESOURCE_EXCEEDED`/`CHECKER_ERROR`（映射竞赛标准 CE/WA/RE/TLE/MLE 语义）；
   classify() 优先链插入通用探测器（不碰 CFD 探测器）。append-only 守卫是 fingerprint
   超集比较，新 mode 零破坏（侦察实证 library.py:135-163）。
5. A5 collect_outputs 递归收集（generic_command.py:179-182 现仅顶层，pytest 嵌套产物漏收，
   影响 provenance 完整性）。
6. A6 pyproject extras 规范化：lightgbm/scikit-learn 从 dev 迁出为独立 `ml` extra；
   新域**零新依赖**（沙箱走 docker CLI subprocess，判定解析走标准库 junitxml）。

## 3. Wave B — coding 域纵切（第一个新域全链）

### 3.1 case 布局与冻结（复用 _collect_frozen_files 零改动）

```text
cases/coding_tasks/<id>/
  case.yaml                 # domain: coding
  visible/                  # 题面 + 代码 stub（生成期给 agent 看的全部）
  reference/hidden_tests/   # 判定测试（frozen map 锚定；§0.7）
  reference/golden/         # 黄金解（case 准入证据，不参与打分）
  provenance.yaml           # 出处：自研=manufactured / 移植=citation 必填
```

- **case 准入门槛**（仿 SWE-bench Verified，在 init 冻结之前）：golden 解在沙箱连跑 3 次
  结果完全一致才可收录；跑不出确定性 → honesty=DECLARED-NOT-VERIFIED 且不得入 agentbench。
  准入证据（3 次 run 摘要）入 case 目录留痕。
- expected_test_count 写入 case.yaml 并随 contract 冻结（对抗测试收集篡改，见 3.3）。

### 3.2 沙箱执行（新安全面，命中即审：Codex 异源审同步阻塞）

- DockerBackend 增 **sandbox profile**（增量 flag 构造，不动现有路径）：
  `--network none · --memory 2g · --pids-limit 256 · --cap-drop ALL ·
  --security-opt no-new-privileges · --read-only 根 · 容器命名 cfdb-sbx-<run_id> ·
  超时后显式 docker kill + rm`（修侦察发现的孤儿容器缺口）。
- **挂载模型（三区物理分离，P1-1 审计修订——「不可写」必须由挂载层保证，不是事后侦测）**：

  | 区 | 挂载 | 内容 |
  |---|---|---|
  | 判定材料区 | **`:ro` 独立挂载** | hidden_tests/（被评代码物理改不了） |
  | 提交区 | **`:ro` 独立挂载** | submission 代码（防运行期自改造成取证混乱） |
  | 工作区 | rw 独占 | scratch + junitxml 输出（容器内唯一可写点） |

  配 `PYTHONDONTWRITEBYTECODE=1` + `-p no:cacheprovider` 使 ro 挂载下 pytest 可运行；
  **`TMPDIR=<工作区>/tmp` 环境变量 + pytest `--basetemp=<工作区>/pytest-tmp`**（R2 审计补——
  `--read-only` 根下 tmp_path/tempfile 默认写 /tmp 会 EROFS 响亮报错，工作区必须显式接管临时目录）。
  §3.3 的「跑后重验 hidden_tests sha256」由此**降级为纵深防御第二层**（防宿主侧
  打分窗口内的材料漂移/竞态），不再是唯一防线。
- 沙箱不可用（无 docker / Windows 未验）→ requires_sandbox case **拒跑**，
  诚实报错指明缺什么；绝不回落本机执行。POSIX rlimit 进程级软隔离**不做**（HumanEval
  式猴补是 POSIX-only 软边界，做了会制造虚假隔离感——显式不做清单 §7）。

### 3.3 判定（重算值驱动，二元，防 hack 机械化）

- 判定器 = pytest 跑 hidden_tests，`--junitxml` 机械解析（绝不 parse 人类 stdout）；
  `--rootdir/--confcutdir` 钉在 hidden_tests 侧 + `-p no:cacheprovider`，
  submission 目录的 conftest.py/pytest.ini 结构性不生效。
- **失败语义两级制（P2-5 审计消歧——尺子问题与提交问题绝不混淆）**：
  - **尺子级 → exit 3 进程拒判，零落账**：pre-flight verify_frozen 漂移；以及跑后重验
    发现**宿主侧** hidden_tests 漂移（打分窗口内尺子被动过 = 尺子已不可信，中止批量，
    当前提交不打分不入账）。
  - **提交级 → INVALID 单提交作废，照常入账（score=None）**：junitxml 缺失/不可解析、
    测试总数 ≠ 冻结的 expected_test_count（对抗收集篡改——BenchJack 的「9 行 pytest hook
    强制全 pass」在此必咬）、容器内异常退出。尺子完好时提交的失败是合法打分事件。
- **判定三条件（全部满足才 pass，二元）**：①提交级对账全过；
  ②FAIL_TO_PASS 全转绿；③PASS_TO_PASS 全保持绿（语义同 v4 回归门）。
- 计分：`pass_rate` 作为普通 QoI（ref=1.0 + **qoi_relative_tolerance=0.0** → 全过才 pass；
  实施更正：absolute_tolerance 按 v4 P4-G 语义只在 ref==0 触发，ref=1.0 必须走相对容差）——
  零改动复用 engine.py 全部 v4 硬化（NaN 门/ref==0 门/ungated 披露）。
  **拒绝部分分**（MBPP/HumanEval 主流同款，与假绿宪法一致）。
- agentbench 扩展与接线（P2-4 审计补明——现有 scorer 只读数值 JSON 从不执行任何东西，
  coding 域打分引入执行是**架构变化**，接线如下）：score 流程按 case.domain 分派；
  coding 分支新增 `agentbench/sandbox_scorer.py`——**直接消费 ExecutionBackend Protocol**
  （execution/base.py，侦察实证已域中立）构造 sandbox profile 执行，**不复用 Runner 管线**
  （Runner 属 case 执行链路，打分链路保持独立，两条路径互不相交的现状不变）；
  产出 junitxml → 重算 pass_rate → 走既有 gates/ledger 流程。_evaluate_gates 增
  `tests_all_pass`/`sandbox_used` 两个 gate 名（未知名 fail-closed 兜底已有，
  scorer.py else 分支实证）。cfd 域 score 路径**字节级不变**。
- pass@k **不做**（§7）：k/n 冻结语义与聚合层是独立工程，ledger append-only 已为
  未来聚合留数据，v5.0 单提交打分。

### 3.4 case 资产（真数据纪律：跑过才算有）

- 1 个 smoke（mock 型管线验证）+ ≥2 个真实小任务（自研 manufactured，带 hidden tests
  + golden 3 次复跑准入证据）。规模克制：先证机械咬合，题库扩张是运营不是架构。
- **v5.0 coding 域范围显式声明（R2 审计补）**：仅纯 Python、无原地构建步骤的任务
  （无 pip install -e / 无 native 扩展编译——submission ro 挂载下构建写入会被挡死，
  这是设计约束不是缺陷）。

## 4. Wave C — agentic/日常任务域纵切（最小可信版）

- **判定原语 = state-based checker**（WorkArena/OSWorld/tau-bench 范式，与假绿宪法同构）：
  case 自带 `reference/checker.py`（sha256 入 frozen map），由 **cfdb 进程**执行
  （被评对象无法触碰），输入 = 产物目录路径，输出 = `{"success": bool, "evidence": [...]}`
  JSON 到 stdout；checker 崩溃/输出不可解析 = CHECKER_ERROR（fail-closed，不是 pass 也不是
  普通 fail，进失败库单独桶，绝不静默吞掉）。
- **checker 信任模型（P1-2 审计修订）**：checker 是 case 作者编写、准入时人签复核、
  sha256 冻结的**受信判定材料**——威胁模型是「粗心」不是「恶意」。准入静态检查因此是
  **补给链卫生而非安全边界**：①零三方依赖（保可移植）；②显式黑名单拒收
  `subprocess/socket/ctypes/importlib/eval/exec`（拦事故性外呼与动态加载）。
  两条都挡不住蓄意绕过——如实声明，蓄意场景由人签准入承担；checker 进沙箱执行记 backlog。
- **quasi-exact-match 判定器**（GAIA 范式）用于标量/短答案子类（数据抽取/信息核对）：
  normalize 规则实现为版本化纯函数并随 contract 冻结（规则漂移=exit 3）——
  对抗「格式取巧假绿」。
- case 资产：≥2 个可机械判定的日常任务 case（如 CSV 抽取→字段精确匹配、
  文档转换→结构断言）。LLM-judge 类「质量评分」任务**显式不收录**（§0.8，槽位留白）。
- 轨迹级判定（GroundEval 范式）记 backlog——一等原语的位置留下，v5.0 不实现。

## 5. Wave D — CFD 域深化（两件性价比最高的）

1. **curve_l2 接线入判定**（v4 遗留缺口，compute_curve_l2 已实现未接线）：
   case 配了 curves + curve_l2_tolerance 的，engine.compute() 纳入判定；未配容差 →
   进 ungated_qoi 同款如实披露。回归影响面：既有 case 无 curves 配置者行为零变化。
2. **held-out reference 机制**（v4 验证边界残差①「提交真实性」的第一刀）：
   ReferenceSpec 增 `held_out_files`（打分用，**不进** case 公开面；agent-eval score
   时才读取 + 其 sha256 单独锚定在 contract）。cavity case 先行试点。
   如实声明：这缓解「抄公开参考值」，不解决「伪造计算过程」（后者仍靠产物抽查重跑，backlog）。

## 6. 验收标准（收口 gate）

1. 全量测试绿（913 + 新增），既有 case.yaml 零修改、行为零回归；
2. 每个新判定面至少一条 tamper witness，**范式统一**（侦察实证三例同款）：
   先证未篡改基线 PASS → 单点篡改 → 断言翻转到指定 fail-closed 态。必咬清单：
   - 改 hidden test 一字节 → score exit 3；
   - submission 带 conftest.py 干预收集 → 测试计数对账 INVALID；
   - requires_sandbox + local backend → 拒跑；
   - 改 checker.py 一字节 → exit 3；
   - **checker 运行时抛异常 / 输出非法 JSON → 必落 CHECKER_ERROR 桶，绝不被判 pass、
     绝不静默吞掉**（P1-3 审计补——文件级篡改 witness 测不到运行时行为，两者都要）；
   - normalize 规则改动 → exit 3；
   - held-out reference 漂移 → exit 3；
   - curve 超容差 → fail（正向咬合）；
   - **沙箱结构钥匙测试**（P2-6 审计补）：断言 sandbox profile 构造出的 docker 命令
     逐 flag 含全清单（--network none/--memory/--pids-limit/--cap-drop ALL/
     no-new-privileges/ro 挂载三区）——拆任一 flag 必红。Codex 异源审是首次验收，
     此测试是长期回归门（一次性人工审 ≠ CI 可回归机制）。
     **证明范围声明（R2 审计补）**：结构测试只证「flag 在命令行里」，不证「docker 真按
     flag 强制执行」——另配至少一条行为级冒烟测试（标 slow/需真 docker，不挂快速门）：
     沙箱容器内写 ro 挂载点必失败、写 /work 必成功、发起网络连接必失败。
     两者证明范围不同，结构测试通过绝不可当作「沙箱安全边界已验证」的证据；
3. 沙箱安全边界过 Codex 异源审（同步阻塞，命中即审清单）；
4. ruff + pyright basic 通过；showcase 对新域空态/实态如实渲染；
5. 收口报告逐条对照本节，未验证项显式标注。

## 7. 显式不做清单（防 scope 漂移）

> R6 批（2026-07-12）已从本清单收走三项：pass@k 聚合层（无偏估计器+`agent-eval
> passk`，仅当前尺样本、n<k 拒算不外推）、INVALID 率展示（showcase ⑥ 表新列
> data-invalid，含占比）、判卷镜像身份入锚（`__judge_image__`：init 时 docker
> inspect 解析、判卷前对活体 daemon 复核、verify 层显式跳过以免拖 Docker 依赖进
> showcase——三点权衡见 contract.py 该键 docstring）。

- pass@k 聚合层（k/n 冻结语义独立工程）；LLM-judge 弱信号槽位的实现（只留位）；
- POSIX rlimit 进程级沙箱（虚假隔离感）；Windows 沙箱支持（未侦察）；
- 轨迹级 grounding 判定；canary/时间切分防污染（启发式证据不可做硬 gate，
  只可做画像标注——业界实证其本质是概率信号）；
- hash-chain 账本（v4 backlog 继承）；NACA y+/GCI 网格研究（需长时真跑，另立批次）；
- registry/audit 双扫描器合并重构（本波只统一可见性语义，不合代码）；
- taxonomy/gates 的 if/elif 链插件化重构（观察点，扩两域后再评估）；
- checker 进沙箱执行（v5.0 信任模型=人签受信材料，见 §4）；
- golden 准入 3 次复跑的系统化留痕（v5.0 允许 case 作者本地跑+摘要入仓，
  自动化准入 runner 记 backlog——账实对齐弱点如实声明）；
- checker 读产物目录的防护性编码契约（大文件/反序列化上限）；
- ~~showcase/报告层显式披露各提交者 INVALID 率~~（R6 批已落地，见本节顶部注记）。

## 8. 风险与残差（如实）

- **trust completeness 维度对新域语义错配**（侦察 P1 级警告）：fields/curves/qoi 三分法
  是 CFD 语汇，coding/agentic 硬套会产出「表面绿但语义骗人」的 completeness 分——
  v5.0 新域 TrustProfile 的 completeness 维度**置 None（数据不足语义）**并注明
  「新域交付物形状未定义」，宁缺毋滥；域专属 completeness 定义进 backlog。
- 无文件锁现状（write_text 直写）在多域并行跑时的竞写风险：v5.0 沿用路径隔离约定
  （各域独立 --baselines/--library/--agentbench-dir），原子写（tmp+os.replace）进 backlog；
  文档处注明。
- cli.py:550/web routes.py:520 的 'validation' 路径字面量是 NACA 专属巧合非通用机制
  （文件存在性 gate 保护，不误伤新域）——在此显式记录，防止误以为 validation 类目
  自动带极曲线能力。
- 外部调研中 BenchJack/RewardHackingAgents 关键数字为摘要级转述（declared-not-verified），
  正式对外引用前需原文复核；本蓝图只采其机制结论（checker 入锚），不背书具体数字。
- **同进程判卷颠覆残差（Codex R0 P1 族，如实声明；R6 批对抗分析后修订路线）**：
  提交代码被隐藏测试 import 后与报告写出方同进程运行——`python -I` 启动隔离封死
  sitecustomize/PYTHONPATH/pytest 影子劫持，skipped=0 与收集计数对账封死懒作弊，
  但进程内 monkeypatch/伪造 report.xml 在单进程 pytest 模型下无法机械排除（进程内
  无秘密可依）。v5.0 coding 判决的可信范围 = 非对抗提交。**R6 批把原路线「逐测试
  进程隔离」做了对抗分析后降级撤销**：测试必须 import 提交代码，因此任何承载单测
  的进程同样被敌意代码占据、其单测报告片段同样可被进程内伪造——逐测试隔离只缩小
  单点爆炸半径，不改变「每份报告都出自被占领进程」的本质，实现它而宣称加固 =
  安全剧场（假绿）。诚实的强化选项重述为：①canary 哨兵测试（判卷时注入必败
  哨兵，空白伪造「全过」必被咬——只抬成本不成边界，且需重排 coding 测试床，另立
  批次）；②受信重执行 oracle（judge 持外部预期输出复跑对账，改变任务形态）。
  残差维持声明，README 验证边界不变。
- **NACA cp_curve/CSV 参考映射递延（Codex R0 P2）**：naca0012 的 curve 参考键名
  （cp_curve）与 outputs.curves 名（cp_distribution）不一致且为 CSV 格式，engine 的
  curve 判定当前对其保持 fail-closed incomplete（adapters 尚未产 curves 数据，实际 inert）；
  键对齐 + CSV 参考装载进 backlog，绝不为「能跑」而放松装载校验。
- **判卷政策已抽专职锚定模块（R5 批，backlog 项收口，锚面终态）**：共享政策全部
  迁入 `judge_policy.py`（QoI/wall-time 装载语义、held-out 优先、qoi_error 重算、
  gate 评估、agentic verdict→gates/score 组装、分数组装），`judge_source:judge_policy`
  三域通用强制；scorer.py 退为编排+账本（**刻意不入锚**——ledger/ranked 改进不再
  全量漂移契约）。边界声明：编排层只接线锚定原语并抄录其输出入账，其完整性由测试
  套件+git 保护（锚校验器无法自锚，回归到与 contract.py 自身相同的信任根）。双向
  实况见证：scorer.py 加注释→照常判卷零漂移；judge_policy.py 加注释→exit 3 精确
  点名。pre-extraction 契约（携 `judge_source:scorer` 键）load 即拒须重锚。
- **manifest 锚形状、逐文件哈希锚内容，合围钉死判卷树**：`__file_manifest__` 咬
  「reference/+visible/ 内文件增/删/改名」；已存在文件的内容改动由逐文件 sha256 咬。
  R1 批次起 visible/ 逐文件锚从 agentic-only 扩到全域（coding 的起始 solution.py 是
  任务面，改它=改被测对象，不同任务面的分数不得同血统）。
- **字节码=可执行判卷材料，拒锚而非隐身（R4 批，纠正 R3 批的排除法）**：`-B` 只禁
  写不禁读——裸 `helper.pyc` 与 checker 同目录照常 import（本机 3.12 探针实证）。
  语义：init 见判卷树内任何 `__pycache__`/`*.pyc` 即拒锚（loud，点名路径）；manifest
  对缓存**可见**——`-B` 保证合法运行不产缓存，事后出现的缓存=真漂移必咬。
- **锚完整性三层闭合（R4 批终态）**：①verify_frozen 重哈希现存键（内容漂移/文件
  丢失，精确点名）→②missing_required_anchors 按 case 重推**期望键全集**（通用特殊键
  +域 judge/normalize 键+全部判卷文件键+held_out 键），被剥离的键在判分前显式点名
  （exit 3 零落账；枚举本身失败也如实上报绝不静默降级）→③load 层强制五条通用锚
  （含 judge_source:scorer，三域通用），showcase 等非判分消费者不再把判分会拒的尺
  显示为 INTACT。顺序=verify 先行（诊断更准），完整性检查殿后（补盲区）。

## 9. 设计审查记录

- **R0（2026-07-11）loop-auditor Mode A：BLOCK**——3P1（hidden_tests「不可写」无挂载层机制
  只靠事后侦测 / checker 标准库白名单挡不住 subprocess·socket·ctypes / checker 运行时崩溃
  缺 witness）+ 3P2（打分链路引入执行的接线未说明 / exit3 与 INVALID 语义边界未画清 /
  沙箱 flag 无机制化回归 witness）+ 3P3。审计员同时逐条核验了六路侦察的关键事实全部为真。
- **R1（同日）**：9 findings 全落地本版——三区 ro/rw 挂载模型（§3.2）、checker 信任模型
  与黑名单（§4）、失败语义两级制（§3.3）、sandbox_scorer 接线（§3.3）、
  checker 运行时 witness + 沙箱结构钥匙测试（§6）、fan-in 口径注记（§1）、backlog 三项（§7）。
- **R3（同日）实现合流注记（主控真容器 E2E 逮到的三个集成缝，单测 stub 均测不到）**：
  ①判卷镜像：默认 `python:3.12-slim` 无 pytest 而 `--network none` 下不可能运行时安装——
  改为 `CFDB_JUDGE_IMAGE` 环境变量可配（默认 `cfdb-judge:py312`）；镜像 digest 入 contract
  冻结记 backlog；②`agent-eval init` 默认 weights/gates 是 CFD 味的→按 domain 分表
  （DOMAIN_DEFAULT_WEIGHTS/GATES，与各域 scorer 实际发射的名字对齐，回归钉在
  tests/test_domain_defaults.py）；③CLI score 的 void-input 预检硬找 qoi.json→域感知
  （cfd 保持原语义；coding/agentic 只要求提交目录非空）。E2E 实况另证：
  篡改 hidden test 一字节→exit 3 零落账；破坏收集的提交被计数对账逮住判 INVALID。
- **R2（同日）loop-auditor 复审：APPROVE**——9/9 核验实质落地；ExecutionBackend Protocol
  接线声明经源码核验站得住；两级失败语义压力测试无新漏洞（跑后重验与容器 ro 层
  防不同威胁不冗余）。三条增补已落本版：TMPDIR/--basetemp（§3.2）、纯 Python 范围
  声明（§3.4）、INVALID 率展示 backlog（§7）。

### 治理审查记录（Codex 86gs gpt-5.6-sol ultra，异源）

- **Codex R0（2026-07-11，审 e2c0a5c）：CHANGES_REQUIRED**——6P1+10P2，核心为
  同进程判卷可颠覆面（bootstrap 劫持 / PYTHONPATH / skipped 懒作弊 / 退出码宽容）。
  13 条修复落 1f699d8（`python -I -c` bootstrap、skipped==0 强制、退出码白名单 {0,1}、
  收集计数对账、visible/ 冻结、normalize 源锚、checker 准入、17 条 witness）；
  同进程 monkeypatch 残差如实声明（§8），路线=逐测试进程隔离。
- **Codex R1（同日，审 1f699d8）：CHANGES_REQUIRED**——2P1+2P2，全部 grounded 坐实：
  ①判卷语义变了但 ruler 血统未变（ledger 新旧行同 `#8d9e98eb` 可同榜）——修复：
  `judge_source:<module>` 锚（coding→sandbox_scorer / agentic→checker_scorer 源码
  sha256 入 frozen map），判卷器任何改动必然改变 contract 字节→新 ruler_id；
  ②visible/ 只锚初始化时存在的文件，事后**新增**文件不咬——修复：`__file_manifest__`
  锚（reference/+visible/ 全树排序清单的 canonical digest，init 与 verify 同法重算，
  增/删/改名必咬）；③legacy 契约无声豁免加固——修复：contract_version 升 "2"，
  v1 契约 load 即拒并给重锚指引；④checker 准入失败在 CLI 裸抛 ValueError——修复：
  init 命令捕获为结构化 [FAIL] exit 1。三仓契约已重锚（smoke_add_two
  `#8d9e98eb→#ff191738` / csv_field_extract `#bcc90fd1→#81020c17` /
  lid_driven_cavity `#d1955288→#342203d3`），旧 ledger 行按 ruler 过滤不再与新分同榜。
  witness：tests/test_codex_r1_witnesses.py（14 条）+ 真 CLI 实况（visible/ 塞文件
  →exit 3 指名 `__file_manifest__`；v1 契约→[FAIL] exit 1）。
  注：cfd 域判卷逻辑住在共享 scorer.py，R1 批刻意不做源锚（避免无关重构全量漂移），
  当时记为已声明残差——被 Codex R2 P1 收紧后于 R3 批全域入锚（见下）。
- **Codex R2（2026-07-12，审 5acb8da）：CHANGES_REQUIRED**——1P1+2P2，round cap 3 用尽
  →交用户裁决，**用户授权 R3 修复批+终审**。三条全 grounded 坐实并落地：
  ①P1 判卷政策半锚（sandbox_scorer 从 scorer.py import `_assemble_score`/`_evaluate_gates`，
  agentic verdict→gates/score 组装整个在 scorer.py）——修复：`judge_source:scorer`
  对**三域**强制入锚（cfd 的 R1 残差一并关闭，语义统一「判卷政策变=全域尺变」）；
  ②P2 版本标签非迁移证明（手改 "2" 或截断 payload 照样过）——修复：load_contract
  强制四条通用锚（case.yaml/__weights__/__validity_gates__/__file_manifest__），
  score_submission 按域强制 judge_source/normalize 锚，缺锚=FrozenDriftError exit 3
  零落账（缺失的锚不会漂移，必须显式点名）；③P2 checker import 兄弟模块生成
  __pycache__ 误触 manifest 假漂移——修复：checker 子进程加 `-B` + manifest/冻结
  枚举排除 `__pycache__`/`*.pyc`（含反向守卫：真文件新增仍必咬）。
  三契约再重锚（smoke_add_two #17070483→#17efa751 等，各 +1 frozen 项）。
  witness：tests/test_codex_r2_witnesses.py（10 条，含 helper-import checker 真跑
  不自漂移 + 缺锚拒判零落账 + 缓存排除不放过真新增）。真 CLI 实况：scorer.py
  追加一行注释→exit 3 指名 `judge_source:scorer`，还原→golden 1.0 @ #17efa751。
- **Codex R3（2026-07-12，审 4121785）：CHANGES_REQUIRED**——1P1+2P2，加轮已用尽
  →二次交用户裁决，**用户裁决=R4 修复批修完直接 push（不再走第五轮审）**。三条全
  grounded 坐实（P1 本机探针实证 `-B` 下裸 .pyc 照常 import 输出 poisoned）：
  ①P1 缓存排除造盲区——修复：init 拒锚存量字节码+manifest 对缓存可见（见 §8）；
  ②P2 判卷文件键不被强制（剥离 checker.py/held_out 键→verify 清白照判）——修复：
  期望键全集重推强制齐全，verify 先行保诊断精度；③P2 load 通用锚漏
  judge_source:scorer——修复：入 REQUIRED_UNIVERSAL_ANCHORS（五条）。
  witness：tests/test_codex_r3_witnesses.py（9 条）+ R2 文件两处语义更新。
  真 CLI 实况：走私 .pyc 入 reference/→exit 3 指名 `__file_manifest__`；存量
  helper.pyc→init 结构化拒锚点名路径；golden 1.0 @ 终尺 #6942c968。
  治理留痕：四轮审同一主题（锚完整性）逐层收紧且条条为真；R3 三条的攻击面均需
  case 目录/契约文件写权限（与重锚同权限级），边际安全价值递减是二次裁决
  「修完即 push」的依据，如实记录。
- **R5 批（2026-07-12，用户「按建议继续」授权，backlog 项）：判卷政策抽专职模块**。
  纯搬移重构（行为不变由全量 1150 测试钉死）：scorer.py 的政策函数逐字迁入
  `judge_policy.py` 并入锚，agentic verdict→gates/score 组装同步抽出（它是政策，
  之前内联在编排层——正是 R2 P1 的病灶）；scorer.py 退为编排+账本不再入锚。
  锚拓扑终态见 §8。三契约重锚（smoke #6942c968→#064935ae）。witness=
  tests/test_judge_policy_extraction.py（5 条：三域锚齐/policy 漂移必咬/
  pre-extraction 尺 load 必拒/退役 scorer 键 verify fail-closed）+ 双向实况
  （编排改动零漂移/政策改动 exit 3）。判决链结构变更按「命中即审」送 Codex
  异源审后 push。
- **R5 治理审（同日，审 be5f16c）：1P1，verbatim 落地不加轮**——cfd 分支的判定
  组装（valid 规则+metric 准入：qoi_error 仅可重算时进、wall_time 仅存在时进）
  仍留在未入锚的编排层，语义可变而尺号不变。按审查员明示修法逐字落地：
  `assemble_cfd` 整体迁入 judge_policy（与 assemble_agentic 对称），cfd 编排分支
  收缩为纯接线；组装见证补入 extraction witness 文件；三契约再重锚
  （smoke #064935ae→#af3e3a04）。编排零漂移旗舰见证复跑通过。1151 绿。
- **R6 批（同日，用户「完成剩余优化建议」授权，backlog 三项落地+一项诚实撤销）**：
  ①`__judge_image__` 镜像身份入锚（init 解析 docker inspect/CFDB_JUDGE_IMAGE_ID，
  coding 契约必备锚，判卷前对活体 daemon 复核，verify 层显式跳过——权衡入
  docstring）；②INVALID 率入 showcase ⑥ 表（含占比，堵「搞崩容器逃避诚实 FAIL」
  动机面）；③pass@k（Chen et al. 无偏估计器，仅当前尺样本、伪造分不算 pass、
  n<k 拒算，CLI `agent-eval passk`）；④「逐测试进程隔离」经对抗分析降级撤销
  （见 §8 修订——被 import 即被占领，逐测试隔离=剧场）。见证=
  tests/test_backlog_r6_batch.py（12 条）；真环境实况：真 docker id 入锚
  （smoke #af3e3a04→#42638717，12 冻结项）、golden 过活体镜像门 1.0、
  CFDB_JUDGE_IMAGE 指向 ubuntu:22.04→exit 3 双 id 点名、镜像不存在→init/score
  结构化拒、pass@1=1（16 旧尺行如实排除）+pass@99 拒算、showcase 渲出
  data-invalid 列。1163 绿。
- **R6 治理审（同日，审 f93a19e）：3P1，全坐实即修**——①镜像比对与 `docker run`
  之间 tag 可被重打（TOCTOU）——修复：判卷容器一律按**已验证的不可变 ID** 启动
  （_default_backend_factory 加 image 参数，真路径闭包传 live id；账本 note 从此
  记 ID 非 tag），capture 见证钉死构造参数=锚定 id；②pass@k 把 rankable 当
  correctness，cfd 连续分域全错也能报 pass@1=1——修复：PASS_AT_K_DOMAINS 限
  coding/agentic（二元域里 rankable⟹correct），cfd 显式拒算「refusing to
  fabricate a pass rate」；③同一 submission 重复打分灌水 n——修复：按
  submission_id 折叠为 attempt，attempt 的全部行都 rankable 才算 pass
  （复打不一致=fail-closed 不算过），CLI 如实播报 collapsed 行数。
  smoke 再重锚 #42638717→#af8b8d91；golden 按不可变 ID 真容器判卷 1.0。
  1166 绿。
