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
- ~~hash-chain 账本~~（R7 批已落地：行链+verify-ledger 命令+append 拒绝断链账本，
  诚实边界=整链重写/尾截断需 git 外部锚，见 §8）；NACA y+/GCI 网格研究（需长时真跑，另立批次）；
- registry/audit 双扫描器合并重构（本波只统一可见性语义，不合代码）；
- taxonomy/gates 的 if/elif 链插件化重构（观察点，扩两域后再评估）；
- checker 进沙箱执行（v5.0 信任模型=人签受信材料，见 §4）；
- ~~golden 准入 3 次复跑的系统化留痕~~（R8 批已落地：`agent-eval admit` 真沙箱
  N 连跑，admission.json 机写落 case 根（冻结树外不漂尺），单次 flaky 即判失败
  但记录照写=诚实纸痕）；
- ~~checker 读产物目录的防护性编码契约~~（R8 批已落地平台侧：提交树 64MiB 上限
  判卷前拒收（哈希/挂载/checker 全线性于树大小=DoS 面）+ checker stdout 1M 字符
  上限超限即 CHECKER_ERROR 绝不截断硬解析）；
- ~~showcase/报告层显式披露各提交者 INVALID 率~~（R6 批已落地，见本节顶部注记）。

## 8. 风险与残差（如实）

- **trust completeness 维度对新域语义错配**（侦察 P1 级警告）：fields/curves/qoi 三分法
  是 CFD 语汇，coding/agentic 硬套会产出「表面绿但语义骗人」的 completeness 分——
  v5.0 新域 TrustProfile 的 completeness 维度**置 None（数据不足语义）**并注明
  「新域交付物形状未定义」，宁缺毋滥；域专属 completeness 定义进 backlog。
  **R8 批复核后维持递延（理由留痕）**：TrustProfile 消费的是 runs/（求解器验证跑）
  证据流，agentbench 判卷根本不产 trust profile——今天不存在会消费「coding/agentic
  completeness」的任何呈现或判定面；为无消费者的维度发明定义=装饰性指标（Goodhart
  邀请函）。待 agentbench 结果进入 trust 呈现面时再定义，届时以 admission.json/
  账本链/canary 等真实证据轴为底料。
- 无文件锁现状（write_text 直写）在多域并行跑时的竞写风险：v5.0 沿用路径隔离约定
  （各域独立 --baselines/--library/--agentbench-dir）。**R7 批已落地 save_contract
  原子写（同目录 tmp+os.replace，失败重锚旧尺原样保留）**；账本 append 为单
  write 调用语义；其余写路径的原子化仍按需评估。
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
  安全剧场（假绿）。诚实的强化选项重述为：①canary 哨兵测试（**R7 批已落地**：
  每次判卷生成 secrets 随机命名哨兵测试，judge 专属 ro 挂载注入（提交侧不可删改、
  不碰冻结 case 树），junitxml 按 testcase 名核验「恰一条且通过」后从计数中扣除——
  只知公开 expected_test_count 的空白伪造报告必被咬。**定位=成本抬升器非边界**：
  进程内敌意代码若观测活体 pytest 会话可读到哨兵名并绕伪——该残差维持声明）；
  ②受信重执行 oracle（judge 持外部预期输出复跑对账，改变任务形态）——**R9 批已落地**
  （见下条独立残差）。同进程判卷颠覆残差本身维持声明，README 验证边界不变。
- **受信重执行 IO oracle（R9 批已落地）——第二条独立判卷信号，如实界定其覆盖面**：
  judge 驱动提交声明的 entry 函数在**隔离容器**内跑一组 held-out 输入（**不挂
  hidden_tests，预期输出永不进容器**），结果**宿主侧严格对账**（`_strict_equal`：
  `True≠1`、无 JSON 类型强转、逐元素递归）。因判决在宿主侧、输入与隐藏测试不相交
  （试点 7/-3/0/100/11 vs 2/5，准入期 token 级 disjoint lint 强制），进程内伪造
  report.xml **无法**满足该 gate——提交要判 valid 必须在从未见过的输入上**真算对**。
  真容器 E2E 实证（试点 smoke_add_two_io）：硬编码 `add_two(2)==4` 的伪造提交
  **pytest 全过但 io_oracle_pass 咬红→整体 invalid**（tests_all_pass 与 io_oracle_pass
  AND 合取，任一失败即废）。**覆盖面边界（不 over-claim）**：①只验声明 entry 函数
  的纯 I/O，非通用恶意代码探测器——entry 函数在 held-out 上真对的提交即按此判分，
  其它行为不在本 oracle 职责内；②不追溯加固 tests_all_pass 信号本身（同进程 pytest
  残差对该单信号维持声明），R9 是**新增正交信号**而非改造旧信号；③准入期 disjoint
  lint 是 token 级启发（belt-and-suspenders），真正的抗重放靠「宿主侧对账+隔离容器
  +独立 work zone（不复用 pytest /work，封死跨run 藏答通道）」结构性保证。
  fail-closed：无 backend/驱动异常退出/对账任一不符 → 0.0（gate 判废）。
  **R9 收口审（loop-auditor APPROVE）两条如实上报的非阻塞残差（详 §9 收口审残差处置）**：
  ①**gate-list 自指锚**——`verify_frozen` 从契约自身内存 `validity_gates`/`weights` 重算
  摘要而非外部根锚；契约 JSON 完整性归 git/文件系统信任根（=保护验证机器自身的同一信任
  根），每 gate 同构、非 io oracle 引入，文件内容锚（驱动源/held_out）仍是外部根且已验。
  ②**agentbench 路径 `sandbox_used=1.0` 硬编码**——真判卷路径结构上恒建 Docker 沙箱、提交方
  无法注入非沙箱 backend 故不可利用，但非派生自 `backend.is_sandbox`（Runner 路径已强制
  `is_sandbox is True`）；pre-existing、跨 Runner/agentbench 分裂，递延留裁量（不自主重构无关码）。
- **NACA cp_curve/CSV 参考映射（Codex R0 P2 递延项，R7 批已落地）**：四个 naca0012
  case.yaml 参考键 cp_curve→cp_distribution 对齐 outputs.curves；engine 增严格 CSV
  装载（csv 标准库，首行可为非数值表头，其余行必须恰两列有限浮点，**一行坏整文件拒**，
  绝不为「能跑」放松校验，Ladson 真数据实载）。**R8 批 adapter 侧采集落地，gate
  已激活**：controlDict.naca 增 cpSurface 采样 FO（airfoil patch 终迭代 raw 采 p），
  openfoam adapter 严格解析（一行坏整文件拒）→ Cp=p/(0.5·u_inf²)（simpleFoam 运动
  学压强，来流参考 0）→ 取上表面（y>0，Ladson 参考单值面约定文档明示）→ 重采样到
  参考 x/c 网格（**参考站点超出采样范围整条拒=绝不外插**；y 值纯仿真，仅借公开
  横坐标=标准 V&V 做法）；装载规格单源化 metrics.curves.load_reference_curve
  （engine 委托+adapter 复用，规格不可分叉）。curve_l2 精确网格契约未动。
  **cp 容差决策留痕（R9 批复核）**：naca0012_a0 现行 curve_l2_tolerance 0.05
  维持不动——当前 10.8k cells 欠分辨网格真 L2≈1.9（a5 实测），配任何「能过」
  的容差=为过而调（Goodhart），紧尺子照红是诚实状态；a5/a10/a15 维持无容差
  =ungated 披露，直到 y+/GCI 网格研究（长跑另批）产出合格网格后再以真收敛
  数据议阈值。绝不以现有欠分辨结果反推容差。
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
- **R6-R1 复审（同日，审 2fc11d8）：3P1（镜像修复获认可，pass@k 三连咬）即修**——
  ①二元信号=冻结 gate 集非 domain 标签（自定义 coding ruler 可省略 tests_all_pass
  →部分失败也 rankable）——修复：PASS_AT_K_BINARY_GATES（coding→tests_all_pass/
  agentic→checker_ok），gate 不在冻结 validity_gates 即拒算，且 pass 要求该 gate
  在行内记录为 True；②submission_id=目录 basename 调用方可控（撞名毒化/换名灌水
  双向可攻）——修复：`attempt_id` 内容身份（提交树 (路径,sha256) 对的 canonical
  digest，判卷前盖章入账本），pass@k 按内容分组，legacy 无身份行永不为样本；
  ③domain 从活体 cases-dir 读、未绑冻结尺——修复：passk 先 verify_frozen+
  missing_required_anchors（漂移/缺锚→exit 3）再信 spec.domain。
  scorer.py 属未锚编排层，契约无需重锚（抽取批红利首次兑现）。
  实况：真账本 attempt_id 盖章、legacy 行如实排除播报、篡改 case.yaml
  domain→exit 3 点名 case.yaml、还原即恢复。见证 20 条（本文件），1171 绿。
- **R6-R2 终轮（同日，审 6019fb7）：2P1+2P2——周期 cap（3 轮）用尽仍有 P1，
  交用户裁决③「R6d 修复批，修完直接 push」（先例=裁决②），按审查方
  Suggested fix 方向逐字落地不再送第 5 轮**——
  ①P1 账本混入提交树（`--submission .` 类路径下判卷后 append 改变树内容
  →同内容每次重评换新 attempt_id→重评灌样本）——修复：ledger_path 落在
  submission_dir 内直接拒收（ValueError，零落账）；②P1 空目录/symlink 不入
  内容身份（`is_file()` 过滤丢目录条目，而 dir_organize checker 恰判目录布局
  →实质不同的提交合并成一个 attempt；symlink 静默按目标哈希）——修复：
  manifest 增目录条目（`relpath/`+"dir"，纯文件平铺树 digest 不变=已落账行
  身份不受扰，实况验真 golden 重评仍 e6677e4b 且被 passk 折叠），symlink
  一律拒收；③P2 判卷窗口内宿主侧改树→判决对新字节身份记旧字节——修复：
  判后重算 digest 不一致即拒绝落账；④P2 digest 途中 I/O 失败裸抛 OSError——
  修复：转结构化 ValueError（CLI [FAIL] exit 1）。
  见证 7 条+四点 tamper 逐一翻红实证；实况：账本入树→[FAIL] exit 1 全文
  点名两路径、symlink→[FAIL] exit 1、golden 重评身份不变。1178 绿。
- **R7 批（用户指令「继续优化，完成剩余优化建议」，backlog 五项收口）**：
  ①save_contract 原子写（同目录 tmp+os.replace，失败重锚旧尺字节原样）；
  ②hash-chain 账本（行 `chain`=sha256(前链+本行去链字段规范 JSON)，genesis=64 零；
  legacy 无链行仅容忍为文件前缀且披露；链起后无链行/改行/中删皆按行号点名；
  append 先核链、断链拒绝续写；CLI `verify-ledger` + passk 前置核链；
  诚实边界=整链重写与尾截断文件内不可测，git 提交账本为外部锚——文档声明）；
  ③showcase 唯一提交改按 attempt_id 内容身份去重，legacy 无身份行单列披露；
  ④NACA cp 参考键对齐+严格 CSV 装载（一行坏整文件拒，Ladson 真数据实载）；
  ⑤canary 哨兵落地（原「另立批次」项）：每卷 secrets 随机命名哨兵测试经
  judge 专属 ro 挂载注入（不碰冻结树），junitxml 按名核验恰一条且通过后
  从计数扣除——空白伪造报告必被咬；定位=成本抬升器非边界，进程内残差维持声明。
  见证 25 条（test_backlog_r7_batch.py）+六点 tamper 翻红实证；实况：判卷源改动
  旧尺 exit 3 点名 judge_source:sandbox_scorer→重锚 #3cb313ec、golden 真容器
  canary 注入下 1.0、真账本 21 legacy 前缀披露+链上行篡改 verify/passk 双拒
  点名 line 22、showcase 渲出 data-no-identity。1203 绿。
- **R7-R0 治理审（审 74d34f6）：1P1+3P2 即修（R7-R1 批）**——①P1 捆绑尺锚过期
  （根因=重锚后又跑 ruff format 改了 sandbox_scorer 字节→提交的锚对不上提交的源，
  clean checkout 必 exit 3；**流程教训：重锚必须是字节定稿后的最后一步**）——修复：
  字节定稿后重锚 #3cb313ec→#0a67252b + golden 真容器重评入账 + **防回归守卫见证**
  （agentbench/ 捆绑契约必须对提交字节 verify_frozen+missing_required_anchors 全干净，
  先红后绿）；②P2 链值非字符串（JSON 数字/对象）致 TypeError——修复：64 字符
  字符串前置校验，按行点名不崩；③P2 无表头 CSV 首行坏数据被静默当表头截断曲线——
  修复：表头正面验证（恰两列且皆非数值）才跳过；④P2 csv.Error/UnicodeDecodeError
  逃逸（审查方实证 field>131072 崩溃）——修复：与 OSError 同拒。
  见证 +5（守卫+链崩溃+表头歧义+超长字段+坏编码），1208 绿。
- **R8 批（用户指令「继续优化，完成剩余优化建议」二轮，backlog 再收四项+一递延留痕）**：
  ①adapter 侧 cp 采集落地激活 curve gate（cpSurface FO+严格 raw 解析+上表面
  +无外插重采样，详 §8 NACA 条目）——**真数据两次咬合修形**：首跑（930s 收敛，
  4334 真面元）暴露「参考名义端点 0/1 恒在面元中心范围外」→ 亚分辨率钳制；
  局部间距判据又被真数据反咬（尾缘加密网格间距 1e-5 << 几何缺口 4e-3，
  越好的网格越容易拒=方向反了）→ 终版=端点缺口 ≤ 参考跨度 1% 才钳制且留痕，
  超出整条拒。真曲线实证：17 站点=参考网格，L2=1.94 vs Ladson a5（与 cl
  0.094 vs 0.456 同源=10.8k cells 欠分辨的既有诚实 FAIL，容差一个没调）；
  ②提交树 64MiB 平台上限（哈希/挂载/checker 全线性于树大小=DoS 面，判卷前
  结构化拒收）+ checker stdout 1M 字符上限（超限 CHECKER_ERROR 绝不截断硬解析）；
  ③`agent-eval admit` golden 准入 N 连跑机写留痕（admission.json 落 case 根=
  冻结树外不漂尺，单次 flaky 即失败但记录照写；真容器实况 3/3 绿，golden
  content id 与账本 attempt_id 同 digest 方案交叉一致）；④装载规格单源化
  （metrics.curves.load_reference_curve，engine 委托+adapter 复用）；
  ⑤域专属 completeness 复核后维持递延（无消费者的维度=装饰性指标，理由 §8）。
  防回归守卫本批两度真咬（checker_scorer 变更→csv 尺 #a54b09ae→#80183efd，
  字节定稿后重锚=canon 47 顺序执行）。见证 22+2 条（test_backlog_r8_batch.py），
  五点 tamper 翻红实证。**runner 全程实况（第二次真跑 656s，合流集成缝必真跑）**：
  metrics.json 收 `curve_l2_errors: {cp_distribution: 1.9424}` +
  `ungated_curves: [cp_distribution]` 披露 note，与离线提取 L2 至小数位一致
  （交叉验证）；overall fail 由欠分辨 QoI 驱动（cl 误差 79%/cd 505%），
  curve gate 端到端激活且未影响既有诚实判定。
- **R8-R0 治理审（审 e6d319f）：2P1+1P2 即修（R8-R1 批）**——①P1 cp 采集失败
  返回 None 绕过已配置 gate（engine 只在 curves is not None 时判曲线→naca0012_a0
  配了 0.05 容差却可在采样坏死时靠 Cl/Cd 静默 pass=真 fail-open）——修复：语义
  三分（None=未声明未尝试/`{}`=声明了但采集失败→engine 记 missing→incomplete/
  非空=成功），见证=「QoI 全过+采集失败 → incomplete 非 pass」端到端；
  ②P1 checker 输出上限在 capture_output 全缓冲后才检查=护栏声明失实——修复：
  Popen+双线程增量限读（64KiB chunk 累计，越限即 kill 子进程+排水防管道阻塞），
  stderr 同界（同为敌意产物可影响面），真子进程见证 stdout/stderr 双路超限
  →CHECKER_ERROR；③P2 空文件树绕过字节上限——修复：同一预判扫描内条目数
  ≤10_000（MAX_SUBMISSION_ENTRIES），超限结构化拒。checker_scorer 再变更→
  csv 尺 #80183efd→#772f977a（守卫先红后绿，canon 47 顺序）。
  见证 +5（26 条），三点 tamper 翻红实证。1234 绿。
- **R8-R1 复审（审 7d6b750）：1P1+2P2+1P3——护栏自身被再打穿，即修（R8-R2 批）**：
  ①P1 `rglob` 先物化整目录列表才逐条产出（CPython 实现 list(scandir_it)）
  →百万空文件平铺目录在守卫自身内爆内存——修复：`os.scandir` 流式遍历
  （`_bounded_tree_paths`），途中即断，保留路径数 ≤ 上限=有界物化，完成后
  sorted(Path) 复现原 sorted(rglob) 顺序（**等价性见证**含 `a.b`/`a` 边界名
  多层树，已落账身份零扰动）；②P2 上限独立预扫描与被哈希快照不绑定（并发
  写方可扫描后增文件）——修复：上限内生进 `_submission_digest` 遍历本身，
  判前判后两次 digest 各自强制（见证=判卷中长出超限文件→拒绝落账）；
  ③P2 text read(64KiB) 等满块→cap+1 字符后挂起的 checker 拖到 60s 超时才
  kill——修复：near-cap 只读 `limit-total+1` 字符，越界即杀（见证=cap+1+
  sleep30s 场景 <10s 出局，tamper 回退实测 30.1s vs 修复后 0.5s）；
  ④P3 stderr 超限报错串引用 stdout 常量——修复：cap 随流选择。
  见证 +5（31 条），四点 tamper 翻红（含等价性守恒确认）；踩点记录：tamper
  同秒同大小替换会骗过 pyc 缓存（假红一枚，清缓存复验消除）。
  checker_scorer 又变更→csv 尺 #772f977a→#d46df105（守卫先红后绿）。1239 绿。
- **R9 批：受信重执行 IO oracle（backlog 收口，第二条独立 coding 判卷信号）**——
  §8 长挂 backlog 的「受信重执行 oracle」落地。机制：judge 驱动提交声明 entry 函数
  在隔离容器跑 held-out 输入（**不挂 hidden_tests、预期永不进容器**），宿主侧
  `_strict_equal` 严格对账（`True≠1`/无类型强转/递归）；与 tests_all_pass **AND
  合取**（任一废即整体 invalid）。**设计先行审（loop-auditor Mode A→BLOCK）**：
  4P1+4P2 全 grounded 复核为真后折叠入设计，另自查追加 2 条更深加固——①对账
  严格类型（堵 `True==1`/`1==1.0` JSON 强转漏判）②oracle 用独立 work zone（不复用
  pytest `/work`，封死「pytest 跑时藏 held-out 答案、oracle 跑时重放」的跨run 通道）。
  P1 落地：①准入拒空/微 oracle（空 oracle 什么都不 gate）②driver 先 import 依赖
  再 insert 提交路径（防提交 shadow importlib/json）③oracle 容器用已校验不可变
  image-ID 构建④oracle 容器**永不挂 hidden_tests**。**试点 case 隔离决策（外科）**：
  io_oracle 若加在 canonical smoke_add_two 上会逼 canary/admission/image-anchor 等
  6 个无关安全测试串入 io 管线（run_admission/score_coding 无 io_factory→fail-closed
  连锁红）——遂立**专属 bundled case `smoke_add_two_io`**（smoke 保持原样零扰动，
  io oracle 仍进真 bundled 契约受 canon 47 守卫+dogfood）。**真容器 E2E 三场实证**
  （cfdb-judge:py312，image-ID 锚合）：①golden→Valid=True 双信号全过；②**伪造
  提交硬编码 `add_two(2)==4` 骗过 pytest 但 io_oracle 咬红→invalid**（R9 核心价值
  =把伪造从「写通过报告」逼到「在没见过的输入上真算对」）；③篡改被锚 held-out_io.json
  →exit 3 精确点名 `reference/held_out_io.json`（重锚后字节级还原、链完好）。
  见证 28 条（test_io_oracle.py），**五点 tamper 全翻红实证**（mount 隔离/import 序/
  独立 work zone〔构造真跨 run 共享篡改〕/strict-equal 伪造/空拒）。judge_policy.py
  增 io_oracle_pass 分支（三域通用锚）→smoke/csv/cavity **三尺全漂重锚**
  （#0a67252b→#bc9dca2b / #d46df105→#ee7951d0 / #7c7e2408→#eacc001d）+新契约
  smoke_add_two_io #923cd295（validity_gates 由反向耦合自动补 io_oracle_pass）；
  字节定稿（ruff）在前、重锚在后=canon 47 顺序。canon-47 守卫复绿，全套 1267 绿。
- **R9-R0 治理审（审 87d029c，86gs sol ultra）：3P1+4P2 全 grounded 坐实即修（本批）**——
  异源审咬中 io oracle 实现层的宿主侧与准入盲区，全部复核为真后逐条落地：
  ①**P1 结果文件非常规/超大伪造**：结果文件落在容器可写 `/work`，恶意提交可预建
  `io_results.json` 为 FIFO（宿主 `read_text` 永久阻塞，逃逸容器超时）/symlink（指
  `/dev/zero` 爆内存）/超大文件——修复：读前 `os.lstat`（不跟随 symlink）强制常规
  文件 + ≤8MiB 上限，否则 0.0（见证：FIFO/symlink/超大三红 + 常规文件仍收）。
  ②**P1 cases_file 未锚**：`cases_file` 为绝对路径/`..` 逃逸/案根文件时准入能读但
  `_collect_frozen_files` 不锚→held-out 答案可漂而 verify_frozen 清白（且可能泄给
  agent）——修复：准入强制 `cases_file` resolve 进冻结且保密的 `reference/` 树内
  （见证：案根文件 + `..` 逃逸双红）。③**P1 oracle 后未重验尺**：oracle 是第二次
  容器跑，把判分窗口拉长过 pytest 后的 verify_frozen；host 在 oracle 运行中篡改冻结
  料不会被逮——修复：`_run_io_oracle` 后再 verify_frozen 一次，漂即 exit 3（见证：
  stub 在 oracle 跑中改 held_out→FrozenDriftError 点名）。④**P2 序列化类型坍缩**：
  `json.dump` 把 tuple→list、int 键→str，宿主 `_strict_equal` 看不到原类型，「类型
  严格」名不副实——修复：driver 序列化前 `_native` 校验，非 JSON-native 返回（tuple/
  set/非 str 键 dict）判 ok:False（见证：in-proc exec driver 源，tuple/int 键红、
  list 收；**真容器 E2E**：tuple-on-held-out 过 pytest 但 driver 拒→io_oracle 红）。
  ⑤**P2 null 缺键伪造**：held-out 期望为 JSON null 时，缺 `result` 键的伪造行因
  `row.get("result")` 也返 None 而蒙混——修复：比较前强制 `result` 键在位（见证：
  缺键+null 期望红、在位 null 值仍收）。⑥**P2 非 coding 域惰性 oracle**：CFD/agentic
  声明 io_oracle 能过 init 但 gate 从不自动补→oracle 被静默忽略=装饰信号——修复：
  init 早断非 coding 域的 io_oracle（见证：cfd 案带 io_oracle→拒）。⑦**P2 缺准入
  证据**：新 case 无 admission.json/md（§3.1 要 3/3 golden sandbox 证据）——修复：
  跑 `agent-eval admit -c smoke_add_two_io --runs 3` 真容器 3/3 全绿（双信号），
  机写 admission.json（冻结树外不漂尺）+ 人读 admission.md 披露（真沙箱，非本地）。
  sandbox_scorer.py 改（判卷源锚）→smoke/smoke_io 双 coding 尺重锚
  （#bc9dca2b→#1b5aaf53 / #923cd295→#26a85b2a；csv/cavity 锚 judge_policy 未动不漂）。
  +13 见证（41 条）六点 tamper 全翻红实证；字节定稿在前重锚在后=canon 47。全套 1280 绿。
- **R9-R1 复审（审 083b6bb，86gs sol ultra）：1P1+2P2——R0 修复的更深绕过，全坐实即修（本批）**——
  异源审对 R0 的加固再施压，三条均为真绕过路径：①**P1 cases_file 仅查 resolve 目标、
  未禁 symlink 组件**：`reference/` 下的目录-symlink 组件，`_collect_frozen_files` 的
  rglob 锚的是 symlink 目标、不下探/记录目录 symlink 本身，而 `_run_io_oracle` 按字面
  路径重新跟随——init 后把 link 重指向 reference/ 外即换掉可信答案而 verify_frozen
  不报漂——修复：准入强制 cases_file 为 reference/ 下**逐组件无 symlink**的规范相对
  路径（case_dir 起逐级 `is_symlink` 检查），保证运行时读的就是被锚的那个真实文件
  （见证：目录-symlink + 文件-symlink 双拒）。②**P2 `_native` 用 isinstance 认子类且
  可被提交替换**：`isinstance(_v,(int,...))` 收 `class MyInt(int)` 子类而 json.dump
  塌成基类→骗过；且被 import 的提交可在 driver 循环前替换 `builtins.isinstance` 让
  tuple 走标量分支——修复：driver 在提交 import **之前**捕获可信类型原语（`_type=type`
  + 各类型对象绑定），改用精确 `_type(_v) is _int` 恒等（拒子类、不碰 isinstance），
  提交无法触及本地绑定（见证：int 子类拒 + 源序断言原语在 import 前捕获且全程无
  isinstance）。③**P2 固定 8MiB cap 可造不可满足契约**：held-out 集若够大（大字符串/
  大列表/够多 case），正确解的结果文件超 8MiB→运行时 cap 反把 golden 与一切正确提交
  判废=冻结了没人能过的尺——修复：cap 常量移入 contract.py（SSOT，sandbox_scorer 反
  向 import），准入按正确解结果行形状投影序列化，超 cap 即拒 + case 数 ≤MAX_IO_ORACLE_CASES
  （1万），init 绝不冻结不可满足尺（见证：case 数超限 + 超大结果双拒）。sandbox_scorer 改
  →smoke/smoke_io 双 coding 尺再重锚（#1b5aaf53→#254377fc / #26a85b2a→#383f8d36），
  admission 在新尺下重跑 3/3。+6 见证（47 条）三点 tamper 全翻红；真容器 E2E golden
  新尺双信号仍过。字节定稿在前重锚在后=canon 47。全套 1286 绿。
- **R9-R2 终审（审 d8c98e6，86gs sol ultra；第 3/终轮，用户裁决「修两条直接 push」）：
  1P1+1P2 全坐实即修**——终轮咬中 R1 修复的两处更深自伤/残留：①**P1 运行时 cap
  移出锚面（R1 自伤回归）**：R1 为破环把 `IO_RESULTS_MAX_BYTES` 挪进 contract.py，
  但 contract.py **刻意不入锚**——`_reconcile_io` 用它判 accept/reject=运行时判卷常量
  却离开了 ruler 血统，后续改它可静默改判而 verify_frozen 清白——修复：常量移回锚定
  judge 模块 sandbox_scorer.py（judge_source:sandbox_scorer，改它即改 ruler id），
  准入侧 `_validate_io_oracle` **函数内局部 import**取值（模块级 import 会成环：
  sandbox_scorer→contract）；MAX_IO_ORACLE_CASES 属纯准入策略（不再判运行时）留
  contract.py（见证：常量在锚模块不在 contract + 改常量必改源哈希）。②**P2 类型原语
  在 `__main__` 全局可被提交改写**：driver.py 以 `python -I driver.py` 跑=`__main__`，
  R1 的 `_type/_list=...` 是**模块级**绑定→提交可 `import __main__; __main__._list=tuple`
  令 `_native` 认 tuple 再被 json.dump 塌成 list（**注：仅破类型严格性，值仍须真算对
  =期望保密，不让错值提交蒙混**）——修复：整个 driver 体包进 `def _drive()`，类型原语
  +open/json/traceback 全captured 为**函数局部**（不在任何提交可见命名空间），配精确
  `is` 恒等双重堵死（见证：源顶层仅 imports+`_drive()`，无模块级原语可被 __main__ 抓）。
  sandbox_scorer 改→smoke/smoke_io 双 coding 尺三度重锚（#254377fc→#b1d9816b /
  #383f8d36→#b4f71e03），admission 三跑 3/3。+3 见证（50 条）两点 tamper 全翻红；
  真容器 E2E 三场（golden 过/伪造咬红/tuple 咬红）新尺全对。字节定稿在前重锚在后=
  canon 47。全套 1289 绿。**用户裁决直接 push（不再加审轮）**——R9 三轮审同主题（判卷
  完整性+宿主侧攻击面）逐层收窄，从 R0 的 3 宽 P1 收敛到 R2 的 1 自伤回归+1 类型残留，
  条条为真且修复非争议，收敛可宣称。
- **R9 rollout：IO oracle 从试点推广到两个真 coding case（数据批，机制已三轮审定，无边界
  码改动）**——试点 smoke_add_two_io 证完机制后，把受信重执行推到实际任务
  `balanced_brackets`（entry `first_unbalanced_index`，held-out 5 例 int）+
  `csv_field_splitter`（entry `split_csv_line`，held-out 5 例 list[str]），held-out 输入
  全经准入 disjoint lint 核实与 hidden 不相交（脚本先算 golden 真值+子串核验再落地；
  csv `single` 撞 hidden 测试名 `test_single_field` 被 lint 咬→换 ` pad ` 空格保留边界）。
  两 case 首次 init 契约（#7274ee8c/#7af65fbc，io_oracle_pass gate 自动补）+ admit 真容器
  3/3 双信号全绿。**真容器 E2E 铁证**：各造「硬编码 harvested hidden 答案」伪造提交→
  **pytest 全过但 io_oracle 咬红→invalid**（bb 咬 input2 `((()`→期望 0 返 -1；csv 咬
  input0 `p,q,r`→期望三段返整串）。回归守卫 TestShippedIoOracleCases 枚举全部 shipped
  io case 逐个跑真准入校验+断言 gate（tamper：把 bb held-out 改成与 hidden 重叠→守卫翻红）。
  bundled 契约从 4 增至 6，canon-47 守卫全覆盖；全套 1290 绿。

- **R9 广度扩充：两个新 coding case（各独立 commit/push，数据批走已审准入门，无判卷边界码改动）**——
  在 io oracle 机制审定后，扩 coding 域覆盖面与 I/O 形态多样性：
  - `roman_to_int`（commit 5bcbe74，entry `roman_to_int`，scalar `str→int`）——naive stub 逐符号
    求和忽略减法记数（`IV`→6/`IX`→11/`XLII`→62 皆错），golden 扫描时前小后大即减。3 个
    FAIL_TO_PASS + 2 PASS_TO_PASS 证判别力；held-out 5 例（`MCMXCIV`→1994 等）经 disjoint lint。
    init 契约 #aa6ab1b5（io_oracle_pass 自动补）+ admit 真容器 3/3 双信号。
  - `merge_intervals`（commit 574cf8a，entry `merge_intervals`，**首个嵌套 `list[list[int]]` I/O**）——
    naive stub 单趟扫描但不先排序（未排序输入产错区间），golden 按 start 排序后再合。此 case
    在真数据上驱动 oracle 的 `_native` 结构守卫与 `_strict_equal` 递归比对（此前 scalar/flat 案例
    未覆盖）。3 FAIL_TO_PASS（均未排序输入）+ 2 PASS_TO_PASS（已排序回归哨兵）；held-out 5 例
    含未排序 `[[15,18],[12,14],[13,16]]`→`[[12,18]]`。init 契约 #5205cfed + admit 3/3。
  - `kth_largest`（commit a3fa733，entry `kth_largest`，**首个多参数 entry** `list+int`）——naive
    stub 升序排序取 `[k-1]`（即第 k 小，退化输入外皆错），golden 降序排序取 `[k-1]`。驱动 oracle
    的 `*args` 路径于异构位置参（此前均单参）。init 契约 #b587f11f + admit 3/3。
  - **三 coding case 各跑真容器 E2E 三态**：golden 双信号过／naive stub `tests_all_pass` 咬红
    ／**harvested 伪造（硬编码 hidden 答案）pytest 全过但 io_oracle 咬红**（roman input0
    `MCMXCIV`→期望 1994 返 0；merge input0 `[[20,30],[25,35]]` 原样返回；kth input0
    `[100,50,75,25],3`→naive 返 75≠期望 50）——两信号 AND 的价值反复实证。
  - **同期 agentic 域第三 case `ini_to_json`（commit a42fbb7，格式转换型）**：与既有两个刻意区隔
    （csv_field_extract=聚合／dir_organize=文件操作／ini_to_json=INI→嵌套 JSON 转换）。判卷走
    checker_scorer（正交于 io oracle），checker stdlib-only 过 validate_checker 准入扫描。真 CLI
    E2E：correct→Valid=True；**int 强转伪造（`port:8080`≠`"8080"`）→checker_ok 过但
    checker_success=0→Valid=False**。终尺 #9645f95e（init 后 ruff 改字节→重锚定稿,canon-47）。
  - 回归守卫 TestShippedIoOracleCases 纳入全部新 io case；case 数 15→20；bundled 契约动态 glob
    自动覆盖（canon-47 committed-bytes 守卫全绿）；全套 1290 绿/ruff 干净。**coding 域现 7 case
    （6 带 io oracle，scalar/str/flat-list/nested-list/multi-arg 五种 I/O 形全覆盖）；agentic 域现
    3 case（聚合/文件操作/格式转换三型）。**

- **R9 收口审计：loop-auditor 审 `io_oracle_pass` 门架构 → APPROVE（新 gate 收口前正交审，独立
  于 3 轮 Codex 码审轴）**——审计对象=验证架构本身（oracle 意图对齐/gate fail-closed/预期输出
  永不进容器/tamper 必咬/门真入锚），非码级 bug。**混合审：读全相关码 + 跑真单测 + 自写 3 个
  tamper 探针打真生产函数 + 真 Docker 容器跑真 `score_coding()` 复现伪造场景**。六条威胁模型
  全闭合并独立复现：①harvested 伪造 pytest 过但 io_oracle 咬（真 Docker 实证：硬编码 5 条 harvested
  断言的 lookup→`tests_all_pass=True` 但 `io_oracle_pass=False`,真实现同跑得 `1.0`=门判别非全拒）
  ②预期输出永不进容器（`_run_io_oracle` 只投 `{index,args}`,mount 只 submission+io_dir）③结果
  文件 FIFO/symlink/超大/null/类型强转全咬 ④fail-open 各路径全落 0.0 ⑤门真入锚（`judge_source:
  sandbox_scorer` 锚整驱动/对账文件,`cases_file` 强制 reference 树无 symlink 组件；独立重哈希 5
  契约锚全 MATCH）⑥非 coding 误用 init 即拒。**单一最强保证**：纯 lookup 无算法的提交在真网络隔离
  容器里被 io_oracle 确定性咬，因为 held-out 期望值结构上从不进容器。
- **收口审残差处置（grounded 复核后）**：审计如实上报 3 条非阻塞残差——
  - **残差 2（`_reconcile_io` 的 TOCTOU 形：`os.lstat` 校验后 `read_text` 重解析路径读取）**：审计
    证不可利用（oracle 容器随 `docker run --rm`+PID-ns SIGKILL 在对账前已退,无对手竞争窗口），
    但属本会话新写判卷边界码，**已硬化**（commit fae171c）：改单次 `os.open(O_RDONLY|O_NOFOLLOW|
    O_NONBLOCK)`→`fstat` 同 inode 判 S_ISREG+大小→读同 fd，校验与读取不跨可换 inode。judge_source
    变→7 coding 契约全重锚。**命中即审送 Codex 治理审**：R0 2P2 全坐实即修（commit d1f1d85）——
    ①O_NOFOLLOW/O_NONBLOCK POSIX 专属→`getattr(...,0)` 回退防 Windows AttributeError 崩溃
    ②`f.read(MAX+1)` 后补 `len(raw)>MAX→拒` 防 fstat 谎报尺寸时截断前缀被当完整结果；R1 复审零
    finding（APPROVE）。2 见证入 tests/test_io_oracle.py（共 53）。
  - **残差 1（`VALIDITY_GATES_KEY`/`WEIGHTS_KEY` 自指锚：`verify_frozen` 从契约自身内存值重算摘要，
    非外部根锚）**：契约 JSON 完整性归 git/文件系统信任根（judge_policy §22-26 已声明,=保护验证机器
    自身的同一信任根），**每 gate 同构,非 io oracle 引入**；文件内容锚（驱动源/held_out）是外部根且已
    验，gate-list 成员仅自洽。递延（消除需外部契约签名=大架构改）。
  - **残差 3（agentbench 路径 `sandbox_used=1.0` 硬编码,非派生自 `backend.is_sandbox`）**：真判卷路径
    结构上恒建 Docker 沙箱（`_default_backend_factory`）,提交方无法注入非沙箱 backend,故不可利用；
    但属 pre-existing 且跨 Runner（已 `is_sandbox is True` 强制）/agentbench 分裂,8 测面。递延留用户
    裁量,不自主重构无关码。
