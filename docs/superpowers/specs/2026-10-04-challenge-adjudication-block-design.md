# Web 决策层 · 挑战裁决区块设计（S4 前增量 · 详情页区块）

> 上游：[2026-10-03-web-portal-decision-layer-design.md](2026-10-03-web-portal-decision-layer-design.md)
> §5.2（挑战裁决不进列表列、最终形态为详情页区块，owner 裁定 5）、§8（增量行：消费端点立项时定验收）、
> §10 裁定 5。挑战子系统本身：
> [2026-09-09-one-time-strategy-challenge.md](../plans/2026-09-09-one-time-strategy-challenge.md)。
> 本文件是 S3a 之后、S4（默认缓）之前的关键路径增量，按 brainstorm → spec → plan 流程立项，
> 不跳步（first-batch 计划文末触发点明示）。

## 1. 目标与边界

在策略详情页 `/strategies/:id` 增加只读**挑战裁决区块**（challenge adjudication block），
把一次性的预注册策略挑战（one-time strategy challenge）的既有裁决作为可审计证据呈现给决策者。

- **形态已定（owner 裁定 5，2026-10-03）**：详情页**区块**，不是独立页——一次性配对比较，独立页是过度建设。
- **挂载面（2026-10-04 裁定）**：**baseline 与 challenger 两侧实验的详情页都挂**。一个挑战是把 baseline
  实验与 challenger 策略配对的一次性比较，两侧视角不同，各取所需；challenger 侧尤其要能证明"它被挑战过、被裁过"。
  不设全局挑战页（守裁定 5）。
- **零写面**：区块不提供任何触发、重跑或删除挑战的入口。挑战仍由 CLI
  `python -m stock_quant research challenge --declaration <path> --root <root>` 发起（`cli.py:966`，
  一次性消费 holdout）。本区块只把**已发布产物**里的既有裁决读出来展示，与 S3a 注册台同一信任设计：
  发布型/消费型命令由人负责，web 只做只读证据面。
- **不 import `research`（ADR-021）**：只读服务直读 `data/strategy_challenges/` 下的已发布 JSON，
  不引入 `stock_quant.research` 依赖面（同 `service/experiments.py`、`service/datasets.py` 的做法）。

### 1.1 不在范围（YAGNI）

- **family 级 holdout 名额概览**（"这个 family 的 one-shot 是否还留着"）：需要服务端聚合全量消费记录
  （含不涉及本实验的挑战），否则声称不成立——**不做**（2026-10-04 裁定：只展示"本挑战消费了哪个 holdout"）。
- 任何写端点（发起挑战 / 重跑）：**方向性否决**，同决策层 spec §10 裁定 2 的信任模型。
- 全局挑战列表页、挑战对比图、跨挑战聚合：不做。
- S4（universe 浏览器 / 标的档案页 / 个股曲线）：默认缓，待真实使用诉求另立设计小节。

## 2. 数据源与身份匹配

### 2.1 唯一读取单元

每个挑战在 `data/strategy_challenges/results/<challenge_id>/` 下有一份不可变的结果目录，
其中 **`strategy_comparison.json`** 是唯一需要的读取单元——它一次性包含三块证据
（`service.py::_comparison_payload`）：

- `declaration`：`ChallengeDeclaration.model_dump(mode="json")`——预注册的全部身份字段；
- `holdout_consumption`：`HoldoutConsumption` 或 `null`（`null` 的两种含义不同，见下段表格）；
- `result`：`ChallengeResult.model_dump(mode="json")`——裁决、逐情景阈值格、理由、错误码。

**所有终态都写这份文件**：`COMPLETED`（`PROMOTED`/`REJECTED`/`INCONCLUSIVE_RESEARCH_ONLY`）与 `FAILED`。

**关键：`holdout_consumption == null` 不等于"未消费"。** FAILED 有三种形态（`service.py:284-317`、
`compare.py` 的 `_ERROR_CODES`），消费语义各不相同：

| FAILED `error_code` | `holdout_consumption` | 该次运行是否真的消费了 |
| --- | --- | --- |
| `HOLDOUT_CONSUMPTION_REFUSED` | `null` | **否**——注册表在消费前拒绝（已消费/身份冲突） |
| `EXPERIMENT_LOAD_ERROR` | `null`（`_publish_failure` 恒传 `consumption=None`） | **是**——消费已成功，其后加载 baseline/challenger 失败 |
| `IDENTITY_MISMATCH` / `MANIFEST_INVALID` / `PAIRING_INCOMPLETE` / `REGISTRY_BINDING_MISMATCH` | **非空**（真实消费记录） | 是——评估期完整性失败，仍带着消费记录发布 |

因此消费记录的有无**不能**单独推断消费是否发生；展示文案必须以 `error_code` × `holdout_consumption`
两轴共同判定（见 §4.2-6/7）。

### 2.2 为什么只读 results 树

results 树是内容寻址、原子发布、按其自身闭集校验过的不可变证据（服务 `_publish_results` 先 staging 后
`os.replace`，已存在结果只在逐字节相同才复用）。**不读** `data/strategy_challenges/holdout_registry.parquet`
与 `.holdout.lock`——它们是加锁的可变运维态，不是已发布证据，且单读它们无法支撑任何跨实验的全称声称。这条纪律
直接决定了 1.1 里"不做 family 级名额概览"。

**声明过的边界（非缺陷）**：若进程在结果发布前死亡（holdout 已消耗、`results/<challenge_id>/` 尚未
`os.replace`），该次消费在 results 树上不可见——本区块也就看不到它。这是只读 results 树的直接后果，
与 §1.1 否决 family 级概览同源；消费记录本身仍留在 `consumptions/` 与 registry 里（运维面），只是不在本
只读证据面上呈现。

### 2.3 匹配"本实验"

一个挑战涉及实验 `X`，当且仅当：

- **baseline 侧**：`declaration.baseline_experiment_id == X`（按 id 直配）；或
- **challenger 侧**：`declaration.challenger_strategy_hash == X 的 manifest.strategy_snapshot_sha256`。

challenger 不能按 id 匹配：声明刻只钉 challenger 的**策略快照哈希**（`ChallengeDeclaration.challenger_strategy_hash`），
运行期才由 `PublishedExperimentLoader.resolve` 扫描 `data/experiments/*/experiment_manifest.json`、
按 `ExperimentManifest.strategy_snapshot_sha256` 解析出唯一实验 id（`service.py:155-165`，`registry.py:111`）。
**服务层用同一等式**：直读各实验的 `experiment_manifest.json` 顶层 `strategy_snapshot_sha256` 字段完成匹配
（同 `service/experiments.py` 读该文件的先例），不 import research。
（注意：`service.py:229` 的 `snapshot_hashes.strategy_hash` 属 `load()` 视图构建，取值同源但文件位置不同；
web 服务层对齐的是 resolve 字面等式的 `experiment_manifest.json`。）

**`role` 判定**：单条挑战内 `X` 只占一个角色——命中 baseline 等式取 `baseline`，否则取 `challenger`。
两侧同时命中在合法挑战里不可能（`assert_comparable_manifests` 要求两侧仅组合规则不同，是两个不同实验），
不为此设分支。

**FAILED 的归属**：`FAILED` 结果里 `challenger_experiment_id` 恒为 `null`（消费被拒或解析失败时压根没有挑战方实验），
`baseline_experiment_id` 仍写声明值。因此一个 FAILED 挑战**总能**出现在其 baseline 实验的详情页；只有当
`challenger_strategy_hash` 恰好等于某个已发布实验的快照哈希时，才一并出现在该挑战方实验页。这是 best-effort 归属，
不是缺陷——挑战方实验未曾成功发布时，本来就没有它的详情页可挂。

## 3. 新只读端点

```
GET /api/v1/experiments/{experiment_id}/challenges
```

### 3.1 响应（类型化只读视图）

`experiment_id` 与 `challenges: list[ChallengeView]`。`ChallengeView`：

| 字段 | 内容 | 来源 |
| --- | --- | --- |
| `challenge_id` | 内容身份（hex64） | 目录名 / `result.challenge_id` |
| `role` | `"baseline" \| "challenger"` | §2.3 匹配结果 |
| `declaration` | `{strategy_family, baseline_experiment_id, challenger_strategy_hash, fold_schedule_hash, comparison_policy_hash, declared_before_run_at, universe_definition{universe_id, universe_version, membership_table_sha256, evidence_summary_sha256}}` | `declaration` 块 |
| `consumption` | `null` 或 `{status, consumption_key, consumed_at, universe_definition{…}}` | `holdout_consumption` 块 |
| `result` | 见下表 | `result` 块 |

`result`：

| 字段 | 内容 |
| --- | --- |
| `status` | `"COMPLETED" \| "FAILED"` |
| `conclusion` | `"PROMOTED" \| "REJECTED" \| "INCONCLUSIVE_RESEARCH_ONLY" \| null` |
| `challenger_stability_conclusion` | `string \| null` |
| `challenger_experiment_id` | `string \| null`（challenger 解析成功时的实验 id；FAILED 恒 null） |
| `executed_fold_count` / `declared_scenario_count` | `int \| null` |
| `skipped_fold_ids` / `failed_scenarios` / `reasons` | `list[str]` |
| `scenario_results` | `list[{scenario, executed_fold_count, passed, cells: list[{metric, baseline, challenger, delta, threshold, passed}]}]` |
| `error_code` | `string \| null`（FAILED 时的稳定脱敏（redacted）码；永不路径/堆栈） |

**投影纪律**：逐字读取，**零派生、零改写、证据零丢失**——本地 pydantic 模型为每个挑战投影一个**声明的子集**，
取值一律原样透传，绝不重算、不改写、不合并。投影**有意省略**的非证据字段仅两处：declaration 的
`identity_scheme_version`（恒为 `strategy-challenge-v1` 的常量）与内嵌的完整 `comparison_policy`（其内容由保留的
`comparison_policy_hash` 钉住）。除此之外，已发布结果里的证据字段全部保留；未知字段丢弃（pydantic 默认
`extra="ignore"`）。任何前端"格式化"（百分比、正负号、千万分位）都只是显示层，不得回写契约。允许的渲染变换仅限
对单值的显示格式化（决策层 spec §7.4 边界）。

### 3.2 失败闭合与护栏

- **fail-closed**：任一 `strategy_comparison.json` 不可读、非 JSON 对象、或结构不满足模型 → 抛稳定错误
  （新增错误类 `ChallengeUnreadable`，`status_code = 500`，`code = "challenge_comparison_unreadable"`），
  经既有错误信封（pin I8）返回。**不静默跳过**——静默跳过会隐藏已消耗 holdout 的挑战，正是本仓库要避免的。
  先例：`list_experiments` 对任一坏 manifest 同样 fail-closed。
- **未知实验**：`experiment_id` 为合法 hex64 但 `data/experiments/<id>/` 不存在 → 抛既有 `UnknownExperiment`
  （404 `experiment_not_found`），镜像 `experiment_report` 等既有实验子资源。不存在的实验上**不得**返回"尚无挑战裁决"
  的空态——那会把"实验不存在"说成"实验没被挑战过"。
- **路径校验与目录筛选**：`experiment_id` 走既有 hex64 路径校验（`^[0-9a-f]{64}$`；失配由 FastAPI 统一拦成
  **422 `invalid_request`**，不是 404，见 `app.py:56-70`）。`results/` 下**只有"hex64 命名的目录"才是候选**：
  非 hex64 名、hex64 名但为普通文件（非目录）、以 `.` 开头的 staging 残留，一律**跳过**（已发布结果恒为
  hex64 命名的目录，其余都不是挑战，跳过不构成隐藏证据）；**hex64 目录但 `strategy_comparison.json` 不可读
  → fail-closed**（见上一条）。只读 `results/<challenge_id>/strategy_comparison.json` 这一个文件，不提供任意路径读取。
- **空态即正常态**：`data/strategy_challenges/` 不存在、`results/` 不存在或为空、或该实验存在但无匹配挑战
  → `200` + 空列表，不报错。
- **排序**：按 `declared_before_run_at` 升序（稳定，可复现）；同刻按 `challenge_id` 字典序兜底。

### 3.3 契约纪律

- **零新发布产物**：不改 `research/` 产物契约、不改 `_assert_artifact_tree` 闭集、不改实验结果树。
- 服务响应与已发布 `strategy_comparison.json` **逐字段对账测试**（决策层 spec §7.2 的"契约是权威、web 是消费者"）。
- `web/src/api/types.ts` **只增不改**；`client.ts` 加一个只读方法；pin I5/I6/I8 原测试不变绿。

## 4. 前端区块

### 4.1 位置与渲染条件

`StrategyDetailPage.vue` 在页面主内容之后追加 `<Card title="挑战裁决" testid="challenge-block">`。
**即使该实验没有已发布的 walk-forward 产物（现页 `report === null` 分支）也要渲染本区块**——挑战匹配只看
身份哈希，不依赖详情页主数据是否可读；区块自带加载/错误/空态。

### 4.2 单条挑战的呈现

1. **角色行**：`本实验为 baseline` / `本实验为 challenger`，附 `challenge_id`（悬浮全哈希 + 复制，作 provenance）。
2. **结论徽章**：`StateBadge` 新增 `challenge` 语义映射——
   `PROMOTED`（绿）/ `REJECTED`（红）/ `INCONCLUSIVE_RESEARCH_ONLY`（黄）/ `FAILED`（红 + `error_code`）。
3. **身份摘要**：`strategy_family`、`declared_before_run_at`、`comparison_policy_hash` 前 8 位、`fold_schedule_hash` 前 8 位。
4. **理由**：`reasons` 逐条列出；`failed_scenarios` / `skipped_fold_ids` 非空时标注。
5. **逐情景阈值表**：`DataTable`，每情景一块，列 `metric / baseline / challenger / delta / threshold / passed`，
   数据逐字读 `scenario_results[].cells[]`。**不做任何跨格/跨情景聚合**。
6. **消费事实（三分支——按 `error_code` × `consumption` 判定，不得只看 `consumption == null`）**：
   - `consumption` 非 null → 展示 `consumption_key`、`consumed_at` 与 `universe_definition` 四字段
     （`universe_id`、`universe_version`、`membership_table_sha256`、`evidence_summary_sha256`）；
     文案"**本次消耗了 holdout**"。
   - `consumption == null` 且 `error_code == "HOLDOUT_CONSUMPTION_REFUSED"` → 文案
     "**消费被拒：本挑战未取得 holdout 消费**"。
   - `consumption == null` 且其它 `error_code`（当前即 `EXPERIMENT_LOAD_ERROR`）→ 文案
     "**holdout 已被本次挑战消耗，但失败结果未附消费记录**"。
7. **FAILED 特别标注**：显式渲染 `error_code` 与 `reasons`。只有在**真正发生了消费**的两支
   （`consumption` 非空，或 `null` + 非 `REFUSED`）才加一句"**该挑战仍已消耗 holdout**"
   （计划 §"Crash, FAILED, REJECTED, and INCONCLUSIVE runs still consume the holdout"）；
   `HOLDOUT_CONSUMPTION_REFUSED` 一支**不得**出现该句——该次运行恰恰没有消费，写出来就是假话。

### 4.3 强制诚实文案

区块固定显示（与 S3a 注册台的诚实边界同一纪律）：

> 挑战是一次性持有集（holdout）的预注册配对比较：一笔 holdout 一经消耗即不可恢复，改变数据集版本、参数、
> 成本情景或失败结果都不会恢复它。本区块是既有裁决的证据展示——web 不发起挑战、不能重跑
> （对已消费的 holdout 重复声明会被 `HOLDOUT_CONSUMPTION_REFUSED` 拒绝）。

**不得**出现任何"运行/重跑"按钮或可复制命令作为**动作**；`challenge_id`/哈希只作 provenance 展示。

### 4.4 空态与加载

- **空态**：无挑战 → `EmptyState`，标题"本实验尚无挑战裁决"，原因"该实验尚未作为 baseline 或 challenger
  参与任何一次性挑战"，下一步给出等价 CLI 路径（`python -m stock_quant research challenge --declaration
  <declaration.json> --root <root>`）——与 S1 空态引导（注册台/RUNBOOK 路径）同一级场景。
- **加载/错误**：`Skeleton` + `toDisplayError()`（稳定 code + 安全摘要，不渲染 `error.stack`）。
- 前端零金融计算：`Delta`/`passed` 逐字读，不重算阈值判定、不重排情景。

### 4.5 服务与前端接线

- `service/app.py` 注册新 router；`service/errors.py` 加 `ChallengeUnreadable`；新模块承载端点
  （命名随实现计划定，如 `service/strategy_challenges.py`）。
- `web/src/api/client.ts` 加 `listExperimentChallenges(experimentId)`（只读 GET）；
  `types.ts` 增对应接口；`StateBadge` 增 `challenge` 语义组。

## 5. 与既有决策/不变量的关系

| 既有决策 | 本设计 |
| --- | --- |
| §10 裁定 5（挑战页 = 详情页区块） | 落实；不新增独立页、不加列表列 |
| §10 裁定 2（发布型 run 不进浏览器按钮） | 强化：本区块纯只读，无任何触发/重跑入口 |
| pin I5（`/experiments` 契约） | 不动；新端点独立成组 |
| pin I6（前端不重建报告） | 延伸：§3.1 投影纪律 + §4.2 零派生渲染 |
| pin I8（错误信封） | 复用；新增稳定码 `challenge_comparison_unreadable` |
| ADR-021（只读面、不 import research） | 不动；直读已发布 JSON，零新依赖 |
| 发布产物闭集契约 | 零改动——不新增任何产物 |
| 挑战子系统契约（声明/消费/结果三段） | 只作消费者，零改动 |

## 6. 验收标准

- **端点**：
  - 空根（无 `data/strategy_challenges/`）= `200` + 空列表；`results/` 为空/不存在同样空列表；
  - 按 baseline id 匹配命中；按 challenger `strategy_snapshot_sha256`（`experiment_manifest.json`）匹配命中；
  - **FAILED 三种消费形态各自入列并被正确投影**（§2.1 表）：
    `HOLDOUT_CONSUMPTION_REFUSED`（`consumption=null`）、`EXPERIMENT_LOAD_ERROR`（`consumption=null`）、
    评估期失败（`consumption` 非空 + 真实 `consumption_key`）；
  - 坏/缺 `strategy_comparison.json` → fail-closed `challenge_comparison_unreadable`；
  - 非 hex64 的 `experiment_id` → **422 `invalid_request`**（FastAPI 路径校验）；合法 hex64 但实验不存在
    → **404 `experiment_not_found`**；非 hex64 名 / hex64 普通文件 / `.` 开头的 results 子项被跳过而不报错；
  - 排序按 `declared_before_run_at` 升序，同刻按 `challenge_id` 兜底；响应与已发布 JSON **逐字段对账**（快照测试）。
- **前端**：区块渲染（结论徽章/理由/阈值表/消费事实）；**消费文案三分支各有一条断言**（含 FAILED 三形态：
  REFUSED 不得出现"已消耗"、LOAD_ERROR 必须出现"已消耗但未附消费记录"、评估期失败展示消费记录）；
  空态与 CLI 引导；加载骨架与 `toDisplayError()`；`challenge` 语义映射单测；e2e 路由 mock 覆盖。
- **纪律**：`research/` 产物契约无改动（diff 证明）；`types.ts` 只增不改；pin I5/I6/I8 原测试不变绿。

### 6.1 已知缺口：契约 ≠ 实例

当前 `project/data/` 下**不存在** `data/strategy_challenges/`，且 `data/experiments/` 仅有 1 个已发布实验——
**挑战实例为零**。与 S1 的"契约 ≠ 实例"缺口同构：挑战子系统（声明 / 原子消费 / 配对比较 / 不可变发布）
是契约层已核验的，但没有一个真实 `strategy_comparison.json` 可供目视核对。本增量按契约建端点 + 越界护栏，
**首个真实挑战的产物**闭合这条缺口（届时目视对账 §3.1 的逐字段投影）。
