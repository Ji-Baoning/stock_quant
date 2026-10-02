# Panda 嫁接 · P2b membership 切片哈希与 refresh 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地总规格 §7.0 十条硬前置（schema-v2 切片哈希、切片化验收、v1 兼容、定义版本注册表、mixed-lineage 迁移、显式 refresh 与崩溃一致性）与 §7.1 attested-boundary 事实生成，使第二个 `universe_id` 进入 `universe_membership` 而不破坏既有定义、门禁或冻结身份。

**Architecture:** 定义层集中在 `research/universe.py`（schema-v2 + 段/gap 模型 + 注册表解析）；切片哈希是 `data_model/universe_membership.py` 纯函数；验收侧只改 `evaluate_index_membership_evidence`（先选 slice 再检查）与 runner `_preflight_universe`（同一 slice 喂三个消费者）；`data update`/`data validate` 的整表校验一行不动；refresh 崩溃一致性放新模块 `data_model/membership_refresh.py`，commit 协议由 membership-hash ADR 裁定（双替换状态机或单指针），二者不得混用。

**Tech Stack:** Python 3.12（`/home/ji/miniconda3/envs/sq312/bin/python`）、pandas、pydantic v2、pytest。无新依赖、零联网。

**Spec:** [2026-09-29-panda-data-loop-grafting-design.md](../specs/2026-09-29-panda-data-loop-grafting-design.md) §7.0（十条）、§7.1、§2.2、§11、§5.2 membership-hash ADR 段。本计划是[总路线图](2026-10-01-panda-data-loop-grafting-implementation.md) Batch P2b（Task 14–18）的阶段计划。

## Global Constraints

- 解释器 `/home/ji/miniconda3/envs/sq312/bin/python`；跑点名测试文件，不跑裸 `pytest`（integration 全量 ≈18.5 分钟）。
- **`pipeline_contract_version` 保持 `1`**（§7.3）：加 key/加列、旧形态兼容读取、处置方式是重发布，不升版。
- **不弱化任何门禁**：`data update`/`data validate` 对整表的 schema 与事实校验保持原样；slice 化只改变*哪个* universe 的哈希被验收比对（§7.0.2）。
- **provenance 列不进身份哈希**（§2.2）：`collected_at` 不进 `membership_content_hash`（结构排除）与定义 `version`；dataset version 的确定性由"首落固定、重放不更新"保障。
- **v1 身份字节级不变**：`UniverseDefinition.version` 与 `membership_content_hash` 既有值不得因加字段漂移（排除法序列化保证，Task 1 有钉死测试）。
- **§7.0.10 owner 裁定门**：裁定取在 **G0**（owner 2026-10-01 裁定 (a)，ADR-023 与 ADR-021 同在 G0 成文；见总路线图"G0 与批次顺序的张力"）——协议未定时 ADR-023 定不了稿，G0 挂起。本批能拿到的是一份**已定稿的 ADR-023**，Task 4/5 据其 `COMMIT_PROTOCOL` 单值落地（Task 1–3 不依赖协议，可先行）；双替换状态机与 Iceberg 式单指针不得混用，ADR 内删未选段与未选代码路径。
- 保护在途 WIP（2026-10-01 `git status` 实况）：`src/stock_quant/cli.py`、`src/stock_quant/data_model/fetch_coverage.py`、`src/stock_quant/research/runner.py`、`src/stock_quant/reporting/html.py`、`src/stock_quant/reporting/templates/experiment.html.j2`、`RUNBOOK.md`、`docs/superpowers/specs/2026-09-29-panda-data-loop-grafting-design.md` 与**五个**测试文件（`tests/unit/test_fetch_coverage.py`、`tests/unit/test_table_tier_preflight.py`、`tests/integration/test_cli.py`、`tests/integration/test_reports.py`、`tests/integration/test_table_tier_preflight.py`）——不覆盖、不回退、不暂存；只 `git add` 本任务文件。**本计划写的 `runner.py` 行号按 `HEAD` 计**；工作区 `runner.py` 有 P2a 在途改动（+34/-1），`_preflight_universe` 各消费点实测偏移 +3（:962→:965、:967→:970、:974→:977、:989-990→:992-993，`UniversePreflightFailed` :269→:272），执行时以工作区实际行号为准。**`cli.py` 在途 WIP：Task 4 只描述新增命令的完整代码，执行时以工作区当前形态为基准追加**；RUNBOOK 只追加新节。
- ADR 编号：`docs/adr/` 现有两个 020；**021 空闲，022 不空闲**——它已被 `docs/adr/022-panda-graft-source-scope-and-basic-factor.md`（P0 计划产出，2026-10-01 accepted，已登记在 `docs/adr/DECISIONS_INDEX.md:33`）占用。本计划占位 **023**（本会话已核：`docs/adr/` 无 023 文件、索引无 023 行）；若编制时已被占用，以 `docs/adr/DECISIONS_INDEX.md` 下一空闲替代（§5.2）。
- 提交信息英文，结尾 `Co-Authored-By: Claude Code <noreply@anthropic.com>`；ADR ≤400 行；改 `project/*.py` 同步 `project/SCRIPTS.md`。
- 已发布 dataset 版本、实验与 schema-v1 定义不可回算改写（§2.3）。

## 开工前必须知道的实现形态（先读再接）

1. **`UniverseDefinition` 在 `src/stock_quant/research/universe.py:99`**，不在 `research/models.py`（那是 RunState/工件契约）。`version = canonical_json_sha256(self.model_dump(mode="json"))`（:91/:146）。加字段后 dump 会多 null 键、改变全部 v1 版本号——Task 1 用 `exclude_none=True` 规避；v1 无 null 字段故输出逐字节不变。
2. 整表哈希链三处消费同一 `membership_content_hash`（universe_membership.py:388，全 facts 排序后整模型 dump）：`UniverseResolver._validate_pinned_facts`（universe.py:205）、`evaluate_index_membership_evidence`（acceptance/checks.py:528 起，:580 比对）。`MembershipFact` 是 `extra="forbid"` frozen 模型，加 `collected_at` 必须同步改哈希排除。
3. runner 消费点全在 `_preflight_universe`（research/runner.py:945-1005）：`load_universe_definition`(:962) → `read_membership_table`(:967) → `evaluate_index_membership_evidence`(:974) → `_facts_from_membership_frame(frame)`(:989) → `UniverseResolver(definition, resolve_memberships(facts, boundaries), facts=facts)`(:990)。frame 只在这里进一次，切一次即三处同源。
4. 整表校验点：`data_pipeline._membership_issues`（def :1589，其中 `validate_membership_facts(expected_sizes={})` 调用在 :1607）；`data validate` → `pipeline.validate`(:776)；`data update` carry 整表（`_read_baseline` def :1641，membership 读出处约 :1685）。不改语义。
5. `project/configs/universes/` 四个 v1 定义（coverage_start 全 `'2015-01-05'`）+ `archive/` 先例；`load_universe_coverage_criterion`（universe.py:310）非递归 `glob("*.yml")` + `universe_id` 去重，`versions/` 子目录天然不可见。
6. `MembershipReason` 现有 6 词（无 `snapshot_observed_change`），且 `tests/unit/test_universe_membership.py:75-84`（`test_status_and_reason_vocabularies_are_exact`）钉死精确集合——Task 5 加词须同步改该测试。
7. dataset version = 各表 parquet 文件 sha256 + schema_versions + build_config 的 descriptor 哈希（dataset.py:276）：`collected_at` 字节会随文件哈希进 dataset version，§2.2 确定性靠"首落固定、重放不更新"执行；定义身份哈希结构排除。`DatasetPublisher.publish`（dataset.py:101）尾部恒 `_replace_current`（调用在 :135，`def` 在 :157）——prepare 需要"发布不提升"。
8. `cli.py` 的 `index_membership_app` 在 :105、`prepare` 命令在 :399，`publish`/`recover` 追加到同组；`project/collect_index_weight_membership.py:250-300` 是"整表 carry + 换 membership 表 + 重发布 + 写定义"既有先例；`UniversePreflightFailed(message, *, error_codes, universe_id)`（runner.py:269）复用于 gap 窗口。

## 文件结构

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `docs/adr/023-universe-membership-slice-hash.md` | 新增 | membership-hash ADR（§7.0.10 两协议模板，裁定后删未选段）——**在 G0 成文**，见 Task 0 |
| `src/stock_quant/research/universe.py` | 修改 | schema-v2、段/gap 模型、coverage/gap 纯函数、注册表解析 |
| `src/stock_quant/data_model/universe_membership.py` | 修改 | `membership_slice_hash`、`collected_at`、`SNAPSHOT_OBSERVED_CHANGE` |
| `src/stock_quant/research/acceptance/checks.py`、`research/runner.py` | 修改 | slice 化验收、runner 切片/gap 预检、显式 version 走注册表 |
| `src/stock_quant/data_model/schemas.py`、`data_quality/raw_checks.py`、`data_pipeline.py` | 修改 | `collected_at` 尾列、两形态兼容、carry 补列 |
| `src/stock_quant/data_model/dataset.py`、`membership_refresh.py`（新）、`index_membership_import.py` | 修改/新增 | `promote=False`、refresh 状态机、快照差分/gap/差异报告 |
| `src/stock_quant/cli.py`（WIP 只追加）、`project/collect_index_weight_membership.py`、`project/SCRIPTS.md`、`RUNBOOK.md`（只追加节） | 修改 | publish/recover 命令、薄封装、恢复步骤 |
| `project/configs/universes/versions/` | 新增 | 不可变定义注册表（**Task 3 Step 4** 先归档现有四个 v1 定义，Task 4 首次 refresh 再追加 v2 条目） |
| `tests/unit/test_membership_definition_v2.py`（新）等 | 新增/修改 | 失败测试先行（详各任务） |

---

### Task 0: membership-hash ADR（G2 硬前置；owner 裁定门）——**在 G0 执行，不在本批**

> **时点（owner 2026-10-01 裁定，见总路线图"G0 与批次顺序的张力"）**：spec §13 的 G0 退出条件同样点名本 ADR，而 G2 的可开始条件是 G1/G0；若本任务在 P2b 批内执行，循环只比 ADR-021 那一半晚一站，并未消解。裁定取 (a)，且**两份 ADR 一并**：ADR-023 与 ADR-021 同在 G0/P0 批内单独成文（纯文档、无代码依赖），本任务卡即为该文档的任务文本。
>
> 对本批的含义：**Task 0 不再在 P2b 开工时产出 ADR，而是引用一份已经存在的 ADR-023**——开工第一步改为核对其已成文且 `COMMIT_PROTOCOL` 已取单值，再据 Task 4/5 承接落地。
>
> **Step 1 的裁定门随之从 P2b 的开工门变为 G0 的输入**：ADR-023 的正文要在"双替换状态机 / Iceberg 式单指针"之间定稿并删未选段，协议没定就定不了稿，G0 因此挂起。这不改变"两协议不得混用、`COMMIT_PROTOCOL` 取单值"的约束，只是把取裁定的时点提到 G0。
>
> **owner 裁定已到(2026-10-02 会话)**：§7.0.10 选 **B(Iceberg 式单指针)**——ADR 定稿时保留协议 B 段、删除协议 A 段与对应代码路径,`COMMIT_PROTOCOL = "single_pointer"`。**编号更正**：本 ADR 原锚 023 已被真实窗口事故催生的 `023-suspension-carry-forward-for-proved-tails.md`(accepted,已推送)占用;按总路线图全局约束的占用复查规则,本 ADR 改用 **024**(`024-universe-membership-slice-hash.md`),本计划与任务卡中全部 "ADR-023" 字样执行时统一替换为 "ADR-024"。

**Files:** Create `docs/adr/023-universe-membership-slice-hash.md`；Modify `docs/adr/DECISIONS_INDEX.md`（只追加一行）

**Interfaces:** Produces：Task 1 段/gap 语义依据；Task 3 注册表与重钉依据；Task 4 的 `COMMIT_PROTOCOL` 取值（`"dual_replace"` 或 `"single_pointer"`，二选一）。

- [ ] **Step 1: owner 裁定门——§7.0.10 协议二选一**

呈报 owner 两选项并取得书面裁定（记入 ADR 状态行）：**A 双替换状态机**（generation 状态文件先行，`CURRENT` 与顶层定义两次 `os.replace`，三中断点由一致性校验捕获）或 **B Iceberg 式单指针**（generation 文件是唯一权威，`CURRENT`/顶层定义为派生缓存，中断点退化为缓存落后自愈）。未裁定时停在本步，Task 4/5 不得开工。

- [ ] **Step 2: 写 ADR 正文（两协议模板齐全，裁定后删未选段）**

```markdown
---
status: accepted
date: <date>
decision: "§7.0.10 采用 <A 双替换状态机 / B Iceberg 式单指针>，两条路径不共存。schema-v2 固定 membership_hash_scope: universe_id，membership_table_sha256 只哈希目标 slice 的 canonical facts；coverage_segments 与带证据哈希的 gap 进入定义内容，version 为 canonical JSON SHA-256（exclude_none 序列化，v1 字节不变）；v2 下验收与 runner 先选 slice 再检查，空 slice 以 UNIVERSE_SLICE_EMPTY 稳定失败，跨 gap 窗口以 universe_gap_in_window fail closed；data update/data validate 仍对整表跑 schema 与事实校验。"
affects:
  - src/stock_quant/research/universe.py
  - src/stock_quant/data_model/universe_membership.py
  - src/stock_quant/research/acceptance/checks.py
  - src/stock_quant/research/runner.py
  - src/stock_quant/data_model/membership_refresh.py
  - src/stock_quant/data_model/dataset.py
  - src/stock_quant/cli.py
---

# ADR-023: universe membership 切片哈希与 schema-v2 定义

日期:<date>
相关：spec §7.0/§7.1/§2.2/§11；ADR-003；ADR-022（attested-boundary 近似契约）

## 决策
1. schema-v2 固定 `membership_hash_scope: universe_id`：`membership_table_sha256`
   只哈希 `frame[frame.universe_id == definition.universe_id]` 的 canonical
   facts（`membership_slice_hash`）。v1 整表语义原样保留、仅用于旧重放，
   无隐式 fallback：v1 文档携带 v2 键即拒绝加载。
2. `coverage_segments`（各带 evidence_sha256）与 gap 列表（reason 固定
   `membership_observation_gap` + 证据哈希）进入定义内容；`version` = canonical
   JSON SHA-256（exclude_none 序列化，v1 字节不变）。`coverage_start/end` =
   段包络；段重叠、事实落段外、gap 与段不互补 → 拒绝加载/拒绝验收。
3. v2 下验收与 runner 先选 slice 再做 schema/事实/coverage/cardinality/hash
   检查；空 slice 稳定失败（UNIVERSE_SLICE_EMPTY）；runner 传给
   _facts_from_membership_frame/resolve_memberships/UniverseResolver 的必须是
   同一 slice；跨 gap 窗口以 `universe_gap_in_window` fail closed。
   `data update`/`data validate` 仍对整表跑 schema 与事实校验。
4. 注册表 `configs/universes/versions/<definition_version>.yml` 不可变、
   append-only；显式 `universe_version` 从注册表解析并复核内容哈希，只有
   `CURRENT` 规格解析顶层文件；顶层仍是完整定义（非递归扫描、universe_id
   不得重复、指针文件阻断发布）。
5. mixed-lineage 迁移（§7.0.5/6）：首次发布前，启用且在目标数据集有非空
   slice 的顶层定义一次性升 v2 并重算 slice hash（定义 version 必变，已发布
   实验不变，CURRENT 源规格下次冻结新 version，显式旧 version 走注册表）；
   无 slice 的启用定义保持 v1 并移入 archive/，且只许在剩余定义最小
   coverage_start 不变时进行。
6. refresh 崩溃一致性：prepare（新 dataset + 新定义不提升；注册表先追加）→
   commit（见裁定段）→ recovery（`data/.membership_generation.json` 与磁盘
   不一致时不猜、稳定错误码，`data index-membership recover --to
   <generation>` 由 operator 显式选择）。崩溃注入覆盖 prepare 后 / CURRENT
   替换后 / definition 替换后（或单指针的派生缓存落后），各收敛到自洽
   generation；重跑幂等；回滚 = 注册表文件替换。
7. `collected_at` 是 provenance 列：不进 `membership_content_hash` 与定义
   version，首次落盘固定、重放不更新（§2.2）。

## 协议 A（双替换状态机）【裁定为 A 则保留本段，否则删除】
commit 三步：先原子写 generation 状态（记录 active 与 history 两代），再
os.replace 提升 CURRENT，再整写替换顶层定义文件。一致性校验（refresh 入口）：
active dataset 目录存在；注册表条目存在且内容哈希 = active
definition_version；顶层定义 version = active definition_version；CURRENT 的
membership slice 哈希 = 顶层定义钉住的哈希（data update carry 同表不动它）。
任一不符 → `membership_generation_inconsistent`，不猜，recover 重放两次替换。

## 协议 B（Iceberg 式单指针）【裁定为 B 则保留本段，否则删除】
generation 文件是唯一权威指针：读取路径先解析它（单文件 os.replace 原子），
CURRENT 与顶层定义是 refresh 替换 generation 后尽力重写的派生缓存，必须整写
完整定义以满足装载器约束。commit 一步：原子替换 generation 文件。三中断点
退化为"派生缓存落后于 generation"的自愈：校验只判 generation 可解析且其
dataset 目录/注册表条目存在；缓存落后时重写而非报错；读 CURRENT 一律经
`current_dataset_version()` 间接。

## 后果
- 未选协议段与对应代码路径在裁定时删除；`COMMIT_PROTOCOL` 常量固定为被选
  值，两条路径不得共存。
- P4 常驻服务启动时的 generation 校验挂钩由 P4 阶段计划接线（本 ADR 记义务）。
```

- [ ] **Step 3: 追加索引行并提交**

`DECISIONS_INDEX.md` 尾部追加一行。**表头是六列** `| ADR | Status | Date | Affected paths | Keywords | Read when |`（不是 4 列），照 `:33` 的 022 行写——链接文本含标题、再是状态、日期、受影响路径、关键词、触发句：

```
| [023 Universe membership slice hash and schema-v2 definitions](023-universe-membership-slice-hash.md) | accepted | <date> | `src/stock_quant/research/universe.py`, `src/stock_quant/data_model/universe_membership.py`, `src/stock_quant/research/acceptance/checks.py`, `src/stock_quant/research/runner.py`, `src/stock_quant/data_model/membership_refresh.py` | membership slice hash, schema-v2, coverage_segments, coverage_gaps, membership_observation_gap, definition registry, generation, dual_replace, single_pointer, snapshot_observed_change | You change how a universe definition scopes its membership hash, how continuous coverage and observation gaps are declared, or how a membership refresh commits and recovers. |
```

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_context_governance_docs.py -q` → PASS

```bash
git add docs/adr/023-universe-membership-slice-hash.md docs/adr/DECISIONS_INDEX.md
git commit -m "docs(adr): adopt ADR-023 membership slice hash and schema-v2 definitions"
```

---

### Task 1: `UniverseDefinition` schema-v2 与切片哈希

**Files:** Modify `src/stock_quant/research/universe.py:99-148`、`src/stock_quant/data_model/universe_membership.py:68-75,149,388-406`；Test `tests/unit/test_membership_definition_v2.py`（新建）

**Interfaces:** Produces（后续任务引用的精确名字）：`MEMBERSHIP_HASH_SCOPE_UNIVERSE_ID = "universe_id"`、`MEMBERSHIP_OBSERVATION_GAP = "membership_observation_gap"`、`MembershipCoverageSegment(start=, end=, evidence_sha256=)`、`MembershipCoverageGap(start=, end=, reason=, evidence_sha256=)`（均 frozen + extra="forbid"，`BaseModel` 故**只接受关键字构造**）；`UniverseDefinition` 新键 `schema_version: Literal[1, 2]`、`membership_hash_scope`、`coverage_segments`、`coverage_gaps`（默认 None，v1 不得携带）；`membership_slice_hash(facts, universe_id)`；`MembershipFact.collected_at: datetime | None = None`。

- [ ] **Step 1: 写失败测试**（新建文件，头部如下；`make_fact`/`fact` 借自 `tests/unit/test_universe_membership.py`）

```python
"""Schema-v2 universe definitions: slice scope, coverage segments and gaps."""
from datetime import date, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from stock_quant.data_model.universe_membership import (
    membership_content_hash, membership_slice_hash)
from stock_quant.research.universe import (
    MEMBERSHIP_HASH_SCOPE_UNIVERSE_ID, MEMBERSHIP_OBSERVATION_GAP,
    MembershipCoverageGap, MembershipCoverageSegment, UniverseDefinition,
    canonical_json_sha256)
from stock_quant.safe_yaml import read_yaml

from tests.unit.test_universe_membership import fact, make_fact

_REPO = Path(__file__).resolve().parents[2]


def _v2(**overrides):
    values = dict(
        schema_version=2, universe_id="custom_csi500_tw",
        rules_version="tushare-index-weight-monthly-v1",
        membership_table_sha256="a" * 64,
        coverage_start=date(2015, 1, 5), coverage_end=date(2026, 8, 28),
        evidence_summary_sha256="b" * 64,
        membership_hash_scope=MEMBERSHIP_HASH_SCOPE_UNIVERSE_ID,
        coverage_segments=[MembershipCoverageSegment(
            start=date(2015, 1, 5), end=date(2026, 8, 28),
            evidence_sha256="b" * 64)])
    values.update(overrides)
    return values


def test_v1_rejects_v2_keys_with_no_implicit_fallback():
    base = dict(schema_version=1, universe_id="csi300", rules_version="r1",
                membership_table_sha256="a" * 64,
                coverage_start=date(2019, 1, 1), coverage_end=date(2021, 12, 31),
                evidence_summary_sha256="b" * 64)
    with pytest.raises(ValidationError, match="schema-v1"):
        UniverseDefinition.model_validate(
            {**base, "membership_hash_scope": MEMBERSHIP_HASH_SCOPE_UNIVERSE_ID})
    assert UniverseDefinition.model_validate(base).schema_version == 1


def test_gaps_must_exactly_complement_the_segments():
    tiled = _v2(coverage_segments=[
        MembershipCoverageSegment(start=date(2015, 1, 5), end=date(2020, 12, 31),
                                  evidence_sha256="b" * 64),
        MembershipCoverageSegment(start=date(2021, 2, 1), end=date(2026, 8, 28),
                                  evidence_sha256="b" * 64)])
    gap = lambda s, e: MembershipCoverageGap(  # noqa: E731
        start=s, end=e, reason=MEMBERSHIP_OBSERVATION_GAP,
        evidence_sha256="c" * 64)
    with pytest.raises(ValidationError, match="complement"):
        UniverseDefinition.model_validate(  # 留洞：gap 不在段缝里
            {**tiled, "coverage_gaps": [gap(date(2021, 2, 1), date(2021, 2, 28))]})
    with pytest.raises(ValidationError, match="complement"):
        UniverseDefinition.model_validate(  # 落在整段覆盖内
            {**_v2(), "coverage_gaps": [gap(date(2020, 6, 1), date(2020, 6, 30))]})
    ok = UniverseDefinition.model_validate(
        {**tiled, "coverage_gaps": [gap(date(2021, 1, 1), date(2021, 1, 31))]})
    assert ok.coverage_gaps is not None


def test_segments_must_match_the_declared_envelope():
    with pytest.raises(ValidationError, match="envelope"):
        UniverseDefinition.model_validate(_v2(coverage_start=date(2014, 1, 1)))
    with pytest.raises(ValidationError, match="overlap"):
        UniverseDefinition.model_validate(_v2(coverage_segments=[
            MembershipCoverageSegment(start=date(2015, 1, 5), end=date(2020, 1, 1),
                                      evidence_sha256="b" * 64),
            MembershipCoverageSegment(start=date(2020, 1, 1), end=date(2026, 8, 28),
                                      evidence_sha256="b" * 64)]))


def test_v1_version_hashes_are_byte_stable_after_the_change():
    root = _REPO / "project" / "configs" / "universes"
    for name in ("custom_csi300_ic", "custom_csi300_ic_tradable",
                 "custom_csi300_tw", "custom_csi300_tw_tradable"):
        doc = read_yaml(root / f"{name}.yml")
        legacy = canonical_json_sha256({key: doc[key] for key in (
            "coverage_end", "coverage_start", "evidence_summary_sha256",
            "membership_table_sha256", "rules_version", "schema_version",
            "universe_id")})
        assert UniverseDefinition.model_validate(doc).version == legacy


def test_the_slice_hash_scopes_to_one_universe_id():
    mine = make_fact(universe_id="custom_csi500_tw")
    theirs = make_fact(universe_id="custom_csi300_tw", symbol="000001.SZ")
    assert membership_slice_hash([mine, theirs], "custom_csi500_tw") == \
        membership_content_hash([mine])
    assert membership_slice_hash([mine, theirs], "custom_csi500_tw") != \
        membership_content_hash([mine, theirs])
    assert fact().collected_at is None  # 新字段默认不出现


def test_collected_at_never_changes_the_content_hash():
    bare = fact()
    stamped = fact(collected_at=datetime(2026, 9, 30, 12, 0))
    assert membership_content_hash([bare]) == membership_content_hash([stamped])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_membership_definition_v2.py -q`
Expected: FAIL — `ImportError`（`MembershipCoverageSegment`/`membership_slice_hash` 不存在）；`collected_at` 因 `extra="forbid"` 被拒。

- [ ] **Step 3: 最小实现**

`research/universe.py` 常量区加段/gap 模型：

```python
MEMBERSHIP_HASH_SCOPE_UNIVERSE_ID = "universe_id"
MEMBERSHIP_OBSERVATION_GAP = "membership_observation_gap"
_GAP_REASONS = frozenset({MEMBERSHIP_OBSERVATION_GAP})


class MembershipCoverageSegment(BaseModel):
    """One maximal run of consecutive membership observations (7.0.1)."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    start: date
    end: date
    evidence_sha256: str

    @field_validator("evidence_sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        return _validated_sha256(value)


class MembershipCoverageGap(BaseModel):
    """A missed-observation window; never carries inferred boundaries."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    start: date
    end: date
    reason: str
    evidence_sha256: str

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        if value not in _GAP_REASONS:
            raise ValueError(f"unknown membership gap reason: {value!r}")
        return value

    @field_validator("evidence_sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        return _validated_sha256(value)
```

> **两个模型都是 pydantic `BaseModel`，只能用关键字构造**：`BaseModel.__init__` 不接受位置参数，`MembershipCoverageSegment(date(2015,1,5), date(2026,8,28), "b"*64)` 会 `TypeError: BaseModel.__init__() takes 1 positional argument but 4 were given`，测试在构造 dict 时就崩、根本到不了被测断言。本计划及后续任务里所有 `MembershipCoverageSegment(...)` / `MembershipCoverageGap(...)` 一律写 `start=`/`end=`/`evidence_sha256=`（gap 另有 `reason=`）。

`UniverseDefinition`（:111 起）字段区追加并加校验器；`version` 改排除法序列化：

```python
    schema_version: Literal[1, 2] = 1
    membership_hash_scope: Literal["universe_id"] | None = None
    coverage_segments: tuple[MembershipCoverageSegment, ...] | None = None
    coverage_gaps: tuple[MembershipCoverageGap, ...] | None = None

    @field_validator("coverage_gaps")
    @classmethod
    def _no_empty_gaps(cls, value):
        return value or None  # 空列表归一为 None，保证序列化唯一形

    @model_validator(mode="after")
    def _check_schema_shape(self) -> "UniverseDefinition":
        if self.schema_version == 1:
            if any(value is not None for value in (
                    self.membership_hash_scope, self.coverage_segments,
                    self.coverage_gaps)):
                raise ValueError(
                    "schema-v1 definitions carry no membership_hash_scope/"
                    "coverage_segments/coverage_gaps (spec 7.0.1/7.0.3 keep v1 "
                    "whole-table-only; refusing v2 keys is this plan's guard)")
            return self
        if self.membership_hash_scope != MEMBERSHIP_HASH_SCOPE_UNIVERSE_ID:
            raise ValueError("schema-v2 requires membership_hash_scope: universe_id")
        segments = self.coverage_segments or ()
        if not segments:
            raise ValueError("schema-v2 requires coverage_segments")
        ordered = sorted(segments, key=lambda item: item.start)
        for earlier, later in zip(ordered, ordered[1:]):
            if later.start <= earlier.end:
                raise ValueError(f"coverage segments overlap: {earlier}/{later}")
        if (self.coverage_start != ordered[0].start
                or self.coverage_end != ordered[-1].end):
            raise ValueError(
                "coverage_start/end must equal the segment envelope "
                f"[{ordered[0].start}, {ordered[-1].end}]")
        tiles = sorted(
            [(item.start, item.end) for item in ordered]
            + [(gap.start, gap.end) for gap in self.coverage_gaps or ()])
        cursor = self.coverage_start
        for start, end in tiles:
            if start != cursor:
                raise ValueError(
                    "segments and gaps must complement exactly over the "
                    f"envelope: expected a tile starting {cursor}, got "
                    f"[{start}, {end}]")
            cursor = end + timedelta(days=1)
        if cursor != self.coverage_end + timedelta(days=1):
            raise ValueError(
                "segments and gaps must complement exactly over the envelope: "
                f"tiling stops at {cursor - timedelta(days=1)}")
        return self

    @property
    def version(self) -> str:
        """SHA-256 of the definition's canonical JSON; the universe version.

        ``exclude_none`` keeps schema-v1 documents byte-identical to the
        pre-v2 rendering (v1 has no optional keys), so every frozen v1
        version survives this change (spec 2.3).
        """
        return canonical_json_sha256(
            self.model_dump(mode="json", exclude_none=True))
```

（`timedelta` 从 `datetime` import。）

`data_model/universe_membership.py`：`MembershipFact` 字段区（:149 后）加 `collected_at: datetime | None = None`；`membership_content_hash` 的 payload 排除 provenance 列，文件加切片哈希：

```python
    payload = [
        {key: value for key, value in item.model_dump(mode="json").items()
         if key != "collected_at"}
        for item in sorted(validated, key=_fact_sort_key)
    ]


def membership_slice_hash(
    facts: Sequence[MembershipFact | Mapping[str, Any]], universe_id: str
) -> str:
    """The schema-v2 scoped hash: one universe's slice of the facts (7.0.1)."""
    return membership_content_hash(
        [item for item in facts if _fact_universe_id(item) == universe_id])


def _fact_universe_id(item: MembershipFact | Mapping[str, Any]) -> str:
    return item.universe_id if isinstance(item, MembershipFact) \
        else str(item["universe_id"])
```

- [ ] **Step 4: 跑测试确认通过 + 邻居**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_membership_definition_v2.py tests/unit/test_universe_membership.py tests/unit/test_research_universe.py tests/unit/test_acceptance_models.py tests/unit/test_index_membership_checks.py -q`
Expected: PASS（v1 身份字节不变由 `test_v1_version_hashes_are_byte_stable` 与 `test_collected_at_never_changes_the_content_hash` 双重钉死）。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/research/universe.py src/stock_quant/data_model/universe_membership.py tests/unit/test_membership_definition_v2.py
git commit -m "feat(universe): add schema-v2 slice-scoped definitions with coverage segments"
```

---

### Task 2: 切片化验收与 runner 同源

**Files:** Modify `src/stock_quant/research/acceptance/checks.py:126-128,515-610`、`src/stock_quant/research/universe.py`（coverage/gap 纯函数、resolver scope 哈希）、`src/stock_quant/research/runner.py:962-1005`；Test `tests/unit/test_membership_definition_v2.py`、`tests/integration/test_research_runner.py`

**Interfaces:** Consumes Task 1。Produces：`slice_membership_frame(frame, universe_id)`；`membership_coverage_violations(definition, facts) -> list[str]`；`window_crosses_membership_gap(definition, window_start, window_end) -> MembershipCoverageGap | None`；验收码 `CODE_UNIVERSE_SLICE_EMPTY = "UNIVERSE_SLICE_EMPTY"`、`CODE_FACT_OUTSIDE_COVERAGE = "UNIVERSE_FACT_OUTSIDE_COVERAGE"`；runner 预检码 `"universe_gap_in_window"`。

- [ ] **Step 1: 写失败测试**（追加到 Task 1 的文件；evaluate 的 fixture 风格照 `tests/unit/test_acceptance_models.py:80-110`）

```python
def _mixed_v2():
    import pandas as pd
    from stock_quant.data_model.calendar import TradingCalendar
    from stock_quant.data_model.universe_membership import membership_frame
    from stock_quant.research.acceptance.checks import (
        evaluate_index_membership_evidence)
    mine = make_fact(universe_id="custom_csi500_tw",
                     raw_effective_from=date(2019, 1, 1))
    theirs = make_fact(universe_id="custom_csi300_tw", symbol="000001.SZ")
    frame = membership_frame([mine, theirs])
    definition = UniverseDefinition.model_validate(_v2(
        universe_id="custom_csi500_tw",
        coverage_start=date(2019, 1, 1), coverage_end=date(2021, 12, 31),
        membership_table_sha256=membership_slice_hash(
            [mine, theirs], "custom_csi500_tw"),
        coverage_segments=[MembershipCoverageSegment(
            start=date(2019, 1, 1), end=date(2021, 12, 31),
            evidence_sha256="b" * 64)]))
    days = tuple(pd.bdate_range(date(2019, 1, 1), date(2021, 12, 31)).date)
    return evaluate_index_membership_evidence, frame, definition, \
        TradingCalendar.from_open_days(days), mine, theirs


def test_v2_evidence_selects_the_slice_before_every_check():
    evaluate, frame, definition, calendar, *_ = _mixed_v2()
    assert evaluate(frame, definition=definition, calendar=calendar,
                    expected_sizes={}).status.value == "PASS"


def test_an_empty_slice_fails_with_a_stable_code():
    evaluate, frame, definition, calendar, *_ = _mixed_v2()
    only_theirs = frame[frame["universe_id"] == "custom_csi300_tw"]
    result = evaluate(only_theirs.reset_index(drop=True), definition=definition,
                      calendar=calendar, expected_sizes={})
    assert result.status.value == "FAIL"
    assert "UNIVERSE_SLICE_EMPTY" in result.details["error_codes"]


def test_slice_facts_outside_the_segments_fail():
    from stock_quant.data_model.universe_membership import membership_frame
    evaluate, _, _, calendar, mine, _ = _mixed_v2()
    early = make_fact(universe_id="custom_csi500_tw",
                      raw_effective_from=date(2018, 6, 1),
                      raw_effective_to=date(2018, 12, 31))  # 必须闭合：与 mine 同为
    # custom_csi500_tw/600000.SH，若两条都 open，membership_slice_hash 会先在
    # _ensure_non_overlapping 抛 "membership facts overlap"，测不到越界码
    definition = UniverseDefinition.model_validate(_v2(
        universe_id="custom_csi500_tw",
        coverage_start=date(2019, 1, 1), coverage_end=date(2021, 12, 31),
        membership_table_sha256=membership_slice_hash([early, mine],
                                                      "custom_csi500_tw"),
        coverage_segments=[MembershipCoverageSegment(
            start=date(2019, 1, 1), end=date(2021, 12, 31),
            evidence_sha256="b" * 64)]))
    result = evaluate(membership_frame([early, mine]), definition=definition,
                      calendar=calendar, expected_sizes={})
    assert "UNIVERSE_FACT_OUTSIDE_COVERAGE" in result.details["error_codes"]


def test_a_window_crossing_a_gap_is_reported():
    gap = MembershipCoverageGap(start=date(2020, 6, 1), end=date(2020, 6, 30),
                                reason=MEMBERSHIP_OBSERVATION_GAP,
                                evidence_sha256="c" * 64)
    definition = UniverseDefinition.model_validate(_v2(
        universe_id="custom_csi500_tw",
        coverage_segments=[
            MembershipCoverageSegment(start=date(2015, 1, 5), end=date(2020, 5, 31),
                                      evidence_sha256="b" * 64),
            MembershipCoverageSegment(start=date(2020, 7, 1), end=date(2026, 8, 28),
                                      evidence_sha256="b" * 64)],
        coverage_gaps=[gap]))
    assert window_crosses_membership_gap(
        definition, date(2020, 5, 1), date(2020, 6, 15)) is gap
    assert window_crosses_membership_gap(
        definition, date(2020, 7, 1), date(2020, 8, 31)) is None


def test_the_resolver_rejects_whole_table_forks():
    from stock_quant.data_model.universe_membership import resolve_memberships
    from stock_quant.research.universe import UniverseResolver
    _, _, definition, _, mine, theirs = _mixed_v2()
    with pytest.raises(ValueError, match="belongs to universe_id"):
        UniverseResolver(definition, resolve_memberships([mine, theirs]),
                         facts=[mine, theirs])
```

`tests/integration/test_research_runner.py` 追加两例（fixture 沿用 `tests/integration/conftest.py` 的 `build_fixture_project`，membership 表注入两 universe 行、v2 定义钉 slice 哈希写入 fixture 工程 `configs/universes/`）：RESEARCH run 以 v2 定义通过 preflight 且 `resolver.members_on(day)` 只含本 universe 符号；窗口跨 gap 的 run 以 `UniversePreflightFailed` 且 `error_codes == ("universe_gap_in_window",)` 失败。再钉一例回归：mixed 表中另一 universe 的坏行（symbol 非规范）使 `data validate` 失败——整表校验不因 slice 化放松（手法照 `tests/integration/test_acceptance_cli.py` 既有用例）。

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_membership_definition_v2.py -q -k "slice or gap or fork"`
Expected: FAIL — `ImportError: cannot import name 'slice_membership_frame'`；v2 mixed 用例在现状整表哈希下报 `UNIVERSE_DEFINITION_HASH_MISMATCH`。

- [ ] **Step 3: 实现**

`checks.py` 码表区（:127 后）加两码；`read_membership_table`（:515）下方加切片帮手；`evaluate_index_membership_evidence` 在整表空检查之后、`validate_membership_facts` 之前插 v2 分支，哈希比对行（:580）与 coverage 检查改按 scope：

```python
def slice_membership_frame(frame: pd.DataFrame, universe_id: str) -> pd.DataFrame:
    """The definition's slice of a (possibly mixed) membership table."""
    return frame[frame["universe_id"] == universe_id].reset_index(drop=True)
```

```python
    if definition.schema_version == 2:
        frame = slice_membership_frame(frame, definition.universe_id)
        if frame.empty:
            return _failed(
                summary=(f"universe_membership has no slice for "
                         f"{definition.universe_id!r}"),
                error_codes=(CODE_UNIVERSE_SLICE_EMPTY,),
                hashes=_definition_hashes(definition))
```

```python
    table_hash = (
        membership_slice_hash(facts, definition.universe_id)
        if definition.schema_version == 2
        else membership_content_hash(facts)
    )
    violations = membership_coverage_violations(definition, facts)
    if violations:
        return _failed(
            summary="slice facts fall outside the declared coverage segments",
            error_codes=(CODE_FACT_OUTSIDE_COVERAGE,),
            hashes=_definition_hashes(definition))
```

`research/universe.py` 加两个纯函数（gap 相交按日历日）；`UniverseResolver._validate_pinned_facts`（:208）哈希行同 scope 化：

```python
def membership_coverage_violations(
    definition: "UniverseDefinition",
    facts: Sequence[MembershipFact],
) -> list[str]:
    """Fact intervals must sit inside the segments and never cross a gap."""
    segments = definition.coverage_segments or ()
    gaps = definition.coverage_gaps or ()
    violations: list[str] = []
    for item in facts:
        # An open fact (``raw_effective_to is None``) is still open only as far
        # as this definition's envelope; ``date.max`` would sit beyond every
        # segment and fail ``end <= segment.end``, marking every active fact as
        # outside coverage.
        end = item.raw_effective_to or definition.coverage_end
        inside = any(segment.start <= item.raw_effective_from and end <= segment.end
                     for segment in segments)
        crosses_gap = any(item.raw_effective_from <= gap.end and end >= gap.start
                          for gap in gaps)
        if not inside or crosses_gap:
            violations.append(f"{item.symbol}@{item.raw_effective_from.isoformat()}")
    return violations


def window_crosses_membership_gap(
    definition: "UniverseDefinition", window_start: date, window_end: date
) -> "MembershipCoverageGap | None":
    """The first gap intersecting [window_start, window_end], if any."""
    for gap in definition.coverage_gaps or ():
        if window_start <= gap.end and window_end >= gap.start:
            return gap
    return None
```

runner `_preflight_universe` 在 `frame = read_membership_table(context)`（:967）后插（import 区补两个新名字）：

```python
        if definition.schema_version == 2:
            gap = window_crosses_membership_gap(
                definition, spec.date_range.start_date, spec.date_range.end_date)
            if gap is not None:
                raise UniversePreflightFailed(
                    f"run window crosses membership observation gap "
                    f"[{gap.start.isoformat()}, {gap.end.isoformat()}]",
                    error_codes=("universe_gap_in_window",),
                    universe_id=definition.universe_id)
            frame = slice_membership_frame(frame, definition.universe_id)
```

evaluate/facts/resolver 三处吃的都是这个切过的 frame——"按 slice 验收、按整表运行"的分叉被 `UniverseResolver.__init__` 的外来行检查结构性拒绝（见 fork 测试）。

- [ ] **Step 4: 跑测试确认通过 + 邻居**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_membership_definition_v2.py tests/unit/test_acceptance_models.py tests/unit/test_index_membership_checks.py tests/integration/test_research_runner.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/research/acceptance/checks.py src/stock_quant/research/universe.py src/stock_quant/research/runner.py tests/unit/test_membership_definition_v2.py tests/integration/test_research_runner.py
git commit -m "feat(acceptance): scope membership evidence and runs to the definition slice"
```

---

### Task 3: 定义版本注册表与 v1 归档规则

**Files:** Modify `src/stock_quant/research/universe.py`（`resolve_versioned_universe_definition`）、`src/stock_quant/research/runner.py:960-962`；Create `project/configs/universes/versions/`（**本任务 Step 4 先归档现有四个 v1 定义**，Task 4 首次 refresh 再追加）；Test `tests/unit/test_membership_definition_v2.py`

**Interfaces:** Produces：`resolve_versioned_universe_definition(configs_root, universe_version) -> UniverseDefinition`（读 `configs/universes/versions/<universe_version>.yml`，校验通过且 `definition.version == universe_version`，否则 `UniverseCoverageError`）；runner 只在 `spec.universe_version == CURRENT` 时解析顶层文件。

- [ ] **Step 1: 写失败测试**（追加）

```python
def test_an_explicit_version_resolves_from_the_registry_and_rechecks_hash(
        tmp_path):
    definition = UniverseDefinition.model_validate(_v2())
    registry = tmp_path / "versions"
    registry.mkdir()
    entry = registry / f"{definition.version}.yml"
    entry.write_text(
        yaml.safe_dump(definition.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8")
    resolved = resolve_versioned_universe_definition(tmp_path, definition.version)
    assert resolved.version == definition.version
    entry.write_text(yaml.safe_dump(
        {**definition.model_dump(mode="json"), "rules_version": "evil"},
        sort_keys=True), encoding="utf-8")
    with pytest.raises(UniverseCoverageError, match="content hash"):
        resolve_versioned_universe_definition(tmp_path, definition.version)


def test_a_missing_registry_entry_never_falls_back_to_the_top_level(tmp_path):
    with pytest.raises(UniverseCoverageError, match="registry"):
        resolve_versioned_universe_definition(tmp_path, "f" * 64)
```

另三例（同文件，fixture 造最小 `configs/universes` 树，断言写全）：`test_the_top_level_scan_stays_non_recursive`——`versions/` 放 v2 定义、顶层放一个 v1 定义，`load_universe_coverage_criterion` 只见顶层；顶层再放缺 `universe_id` 的指针文件 → `UniverseCoverageError`（阻断发布）。`test_no_slice_definitions_stay_v1_and_archive`——启用定义在目标数据集无 slice 时，迁移助手拒绝为其计算空 slice 哈希（`ValueError`，不冒充 v2）；移入 `archive/` 仅当剩余启用定义的最小 `coverage_start` 不变（断言移出前后 `acceptance_start` 相等；再造一个"移出会抬高起点"的反例断言拒绝）。`test_current_specs_freeze_the_new_v2_version`——顶层 v2 定义下 runner preflight 冻结的 `universe_version == definition.version`（走 `tests/integration/test_research_runner.py` 的 v2 fixture）。

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_membership_definition_v2.py -q -k "registry or top_level or archive"`
Expected: FAIL — `ImportError: cannot import name 'resolve_versioned_universe_definition'`。

- [ ] **Step 3: 实现**

`research/universe.py`（`load_universe_definition` 旁）：

```python
def resolve_versioned_universe_definition(
    configs_root: str | Path, universe_version: str
) -> UniverseDefinition:
    """Resolve an explicit universe_version from the immutable registry.

    The entry must validate and its canonical-JSON version must equal the
    file name's version (content re-check); only ``CURRENT`` specs resolve
    the top-level file (spec 7.0.4).
    """
    entry = Path(configs_root) / "versions" / f"{universe_version}.yml"
    if not entry.is_file():
        raise UniverseCoverageError(
            f"universe_version {universe_version!r} has no registry entry "
            f"under {entry.parent}; only CURRENT resolves the top-level file")
    definition = load_universe_definition(entry)
    if definition.version != universe_version:
        raise UniverseCoverageError(
            f"registry entry {entry.name} content hash {definition.version} "
            f"does not match its requested version {universe_version!r}")
    return definition
```

runner `_preflight_universe`（:962）定义加载改为：

```python
        if spec.universe_version == _CURRENT:
            definition = load_universe_definition(definition_path)
        else:
            definition = resolve_versioned_universe_definition(
                self._config_root / "configs" / "universes",
                spec.universe_version)
```

- [ ] **Step 4: 把现有 v1 定义按版本归档进注册表**（**本步不可省**）

`project/configs/universes/versions/` 目前**不存在**（已 `ls` 确认）。本步一落地，`spec.universe_version != CURRENT` 的规格就一律走注册表；而注册表首批条目原本要等 Task 4 的首次 refresh 才写，**两者之间任何显式钉旧 v1 `universe_version` 的冻结规格都会以 `UniverseCoverageError: has no registry entry` 失败**——这正是 spec §7.0.4 要求"在同名发布中把现有 v1 定义按版本归档，保证旧冻结运行可解析"的原因，不是可留到 Task 4 的收尾工作。

做法：对 `project/configs/universes/*.yml` 四个 v1 定义，各自 `load_universe_definition(...)` 取 `definition.version`，把**文件字节原样**复制成 `versions/<definition.version>.yml`（字节一致才能通过 `resolve_versioned_universe_definition` 的内容哈希复核）。这四个是纯归档条目，不 inflate、不改写、不重钉。

- [ ] **Step 5: 跑测试确认通过 + 邻居**（Task 2 Step 4 命令加 `-k "registry or top_level or archive or freeze"`）→ PASS

- [ ] **Step 6: 提交**

```bash
git add src/stock_quant/research/universe.py src/stock_quant/research/runner.py tests/unit/test_membership_definition_v2.py project/configs/universes/versions/
git commit -m "feat(universe): resolve explicit universe versions from the registry"
```

---

### Task 4: membership refresh CLI 与崩溃一致性（依赖 G0 的 ADR-023 裁定）

**Files:** Create `src/stock_quant/data_model/membership_refresh.py`；Modify `src/stock_quant/data_model/dataset.py:101-141`、`src/stock_quant/cli.py`（WIP：只追加两命令）、`project/collect_index_weight_membership.py`、`project/SCRIPTS.md`、`RUNBOOK.md`（只追加节）；Test `tests/integration/test_membership_refresh.py`（新）、`tests/unit/test_dataset_publisher.py`（无则新建）

**Interfaces:** Consumes Task 1/3、`DatasetPublisher.publish`、`validate_membership_facts`。Produces：`MembershipRefreshError(message, *, error_code)`（码 `membership_generation_inconsistent` / `membership_generation_state_malformed`）；`Generation(dataset_version, definition_name, definition_version, committed_at)`、`GenerationState(active, history)`；`verify_generation(project_root) -> GenerationState | None`；`prepare_refresh(project_root, *, universe_id, definition_name, prepared_frame, rules_version, evidence_summary_sha256, segments=(), gaps=()) -> Generation`；`commit_steps(project_root, generation) -> Iterator[Callable]`（测试注入用）；`commit_refresh`；`recover_refresh(project_root, definition_version) -> Generation`；`COMMIT_PROTOCOL`；CLI `data index-membership publish` / `recover --to <definition_version>`（退出码 2 + `error_code=` 前缀）。

- [ ] **Step 1: `DatasetPublisher` 的"发布不提升"**

失败测试：`publish(..., promote=False)` 后新版本目录存在、`CURRENT` 仍指旧版本；`promote(version)` 后 `CURRENT` 指该版本。实现：`publish(..., promote: bool = True)`，`:135` 改 `if promote: self._replace_current(dataset_version)`；新增 `def promote(self, version: str) -> None`（校验 `standardized_root / version` 是目录后调 `_replace_current`）。

- [ ] **Step 2: 写状态机失败测试**（新建 `tests/integration/test_membership_refresh.py`；fixture `build_refresh_project(tmp_path)` 造最小工程：含两 universe slice 的已发布 dataset + `CURRENT` + 顶层 v1 定义 + `versions/` 既有条目 + prepare 产物帧）

```python
ORDER = {"dual_replace": ["state", "current", "definition"],
         "single_pointer": ["state"]}
# 只有"提交跑了一半"的中断点才留下不一致态。`prepare` 时一步都没提交
# （`read_generation_state` 返回 None，`verify_generation` 也就返回 None，
# 不会抛错——断言 `pytest.raises` 会 DID NOT RAISE）；`definition` 是最后
# 一步，跑完系统已自洽（`verify_generation` 返回 state，同样不抛）。
# `single_pointer` 把替换 generation 打包成一步，故没有任何撕裂点。
TEARING = {"dual_replace": ["state", "current"], "single_pointer": []}


@pytest.mark.parametrize("stop_after", ["prepare", *ORDER[COMMIT_PROTOCOL], None])
def test_every_crash_point_converges_to_a_self_consistent_generation(
        refresh_project, stop_after):
    generation = prepare_refresh(refresh_project.root, **refresh_project.args)
    steps = commit_steps(refresh_project.root, generation)
    for name in ORDER[COMMIT_PROTOCOL]:
        if stop_after == "prepare":
            break
        next(steps)
        if name == stop_after:
            break
    if stop_after not in TEARING[COMMIT_PROTOCOL]:
        state = verify_generation(refresh_project.root)
        if state is not None:  # prepare：还没有状态文件，属预期
            assert state.active.definition_version \
                == generation.definition_version
        return
    with pytest.raises(MembershipRefreshError) as error:
        verify_generation(refresh_project.root)
    assert error.value.error_code == "membership_generation_inconsistent"
    recovered = recover_refresh(refresh_project.root,
                                generation.definition_version)
    assert verify_generation(refresh_project.root) \
        .active.definition_version == recovered.definition_version
```

（`COMMIT_PROTOCOL` 从被测模块 import；单指针下 `stop_after` 只剩 `prepare`/`state`/`None` 且 `TEARING` 为空，即**该协议下没有任何中断点会撕裂**——所以 `recover_refresh` 的路径只由双替换协议的 `state`/`current` 两个用例覆盖。另写 `test_lagging_derived_caches_self_heal`：走完 state 一步后手动改脏 `CURRENT` → `verify_generation` 通过且缓存被重写。）同文件再写四例（断言写全）：`test_prepare_appends_the_registry_but_promotes_nothing`（CURRENT/顶层定义不变、`versions/` 多一条目）；`test_rerun_is_idempotent`（同参数重跑两次，dataset_version/definition_version/状态文件逐字节相同）；`test_rollback_is_a_file_replace`（`recover --to <旧 definition_version>` 后顶层定义与 `versions/` 条目字节一致、CURRENT 指回旧 dataset）；`test_the_refresh_replaces_one_slice_and_keeps_the_others`（csi300 官方 slice 行数与哈希不变）。

- [ ] **Step 3: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_membership_refresh.py -q`
Expected: FAIL — `ModuleNotFoundError: stock_quant.data_model.membership_refresh`。

- [ ] **Step 4: 实现状态机**（`membership_refresh.py`；表 carry 形态照 `collect_index_weight_membership.py:250-300`）

```python
COMMIT_PROTOCOL = "dual_replace"  # ADR-023 裁定值；备选 "single_pointer"（删未选）
GENERATION_STATE_RELATIVE = Path("data") / ".membership_generation.json"


class MembershipRefreshError(RuntimeError):
    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class Generation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    dataset_version: str
    definition_name: str
    definition_version: str
    committed_at: str


class GenerationState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    active: Generation
    history: tuple[Generation, ...] = ()


def read_generation_state(project_root: Path) -> GenerationState | None:
    path = Path(project_root) / GENERATION_STATE_RELATIVE
    if not path.exists():
        return None
    try:
        return GenerationState.model_validate(
            json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValidationError) as error:
        raise MembershipRefreshError(
            f"{path} is malformed; refusing to guess (spec 7.0.10)",
            error_code="membership_generation_state_malformed") from error


def _write_state(project_root: Path, state: GenerationState) -> None:
    path = Path(project_root) / GENERATION_STATE_RELATIVE
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(state.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8")
    os.replace(temporary, path)


def verify_generation(project_root: Path) -> GenerationState | None:
    """Fail closed when the recorded generation and the disk disagree."""
    state = read_generation_state(project_root)
    if state is None:
        return None
    gen = state.active
    dataset_dir = Path(project_root) / "data" / "standardized" / gen.dataset_version
    entry = (Path(project_root) / "configs" / "universes" / "versions"
             / f"{gen.definition_version}.yml")
    if not dataset_dir.is_dir() or not entry.is_file():
        raise MembershipRefreshError(
            "membership generation state names artifacts missing on disk: "
            f"dataset={gen.dataset_version} entry={entry.name}",
            error_code="membership_generation_inconsistent")
    if load_universe_definition(entry).version != gen.definition_version:
        raise MembershipRefreshError(
            f"registry entry {entry.name} no longer hashes to its version",
            error_code="membership_generation_inconsistent")
    if COMMIT_PROTOCOL == "dual_replace":
        top = (Path(project_root) / "configs" / "universes"
               / f"{gen.definition_name}.yml")
        definition = load_universe_definition(top)
        if definition.version != gen.definition_version:
            raise MembershipRefreshError(
                f"top-level definition {top.name} pins {definition.version}, "
                f"generation expects {gen.definition_version}",
                error_code="membership_generation_inconsistent")
        with DatasetReader(project_root).open(
                DatasetPublisher(project_root).current().version) as context:
            slice_hash = membership_slice_hash(
                _facts_from_membership_frame(slice_membership_frame(
                    read_membership_table(context), definition.universe_id)),
                definition.universe_id)
        if slice_hash != definition.membership_table_sha256:
            raise MembershipRefreshError(
                "CURRENT's membership slice no longer matches the pinned "
                "definition hash (torn refresh?)",
                error_code="membership_generation_inconsistent")
    return state


def commit_steps(project_root: Path, gen: Generation):
    """The visibility promotions of one commit, in order (spec 7.0.10)."""
    previous = read_generation_state(project_root)
    state = GenerationState(
        active=gen,
        history=(previous.active, *previous.history) if previous else ())

    def write_state() -> None:
        _write_state(project_root, state)

    def replace_current() -> None:
        DatasetPublisher(project_root).promote(gen.dataset_version)

    def replace_definition() -> None:
        _install_definition(project_root, gen)

    if COMMIT_PROTOCOL == "dual_replace":
        yield write_state
        yield replace_current
        yield replace_definition
    else:  # single_pointer: one atomic replace, caches rewritten best-effort
        yield write_state
        replace_current()
        replace_definition()


def commit_refresh(project_root: Path, gen: Generation) -> None:
    for step in commit_steps(project_root, gen):
        step()


def recover_refresh(project_root: Path, definition_version: str) -> Generation:
    """Converge to a self-consistent generation chosen by the operator."""
    state = read_generation_state(project_root)
    if state is None:
        raise MembershipRefreshError(
            "no generation state to recover from",
            error_code="membership_generation_state_malformed")
    target = next(
        (gen for gen in (state.active, *state.history)
         if gen.definition_version == definition_version), None)
    if target is None:
        raise MembershipRefreshError(
            f"generation {definition_version!r} is not recorded in "
            f"{GENERATION_STATE_RELATIVE}; refusing to guess",
            error_code="membership_generation_inconsistent")
    commit_refresh(project_root, target)
    return target
```

`_install_definition(project_root, gen)`：读 `versions/<gen.definition_version>.yml` 字节，经临时文件 `os.replace` 整写 `configs/universes/<gen.definition_name>.yml`。`prepare_refresh(...)`：`verify_generation` 先行 → 打开 CURRENT，读全部表 `{name: context.read(name) for name in context.tables}`（沿用 baseline `build_config`）→ `_replace_slice(baseline_membership, prepared_frame, universe_id)`（其他 slice 原样保留）→ `validate_membership_facts(merged, calendar=baseline trading_calendar, expected_sizes={})` 无 FATAL → 构造 v2 定义（`membership_slice_hash`；segments/gaps 缺省时单段 `[slice 最小 raw_effective_from, slice 最大日]`）→ `publisher.publish(tables, QualityReport(), build_config=..., promote=False)` → 注册表追加（存在即跳过）→ 返回 `Generation`。`data update` 的整表 carry 不动（slice 未变时 verify 的哈希检查照常通过）。

- [ ] **Step 5: CLI 与 RUNBOOK 与薄封装**（`cli.py` 只追加，不重排既有行；`_load_coverage_file(path)`：`json.loads` 后 `MembershipCoverageSegment.model_validate` / `MembershipCoverageGap.model_validate` 逐条转模型，无文件返回 `((), ())`）

```python
@index_membership_app.command("publish")
def data_index_membership_publish(
    universe_id: str = typer.Option(..., "--universe-id"),
    definition_name: str = typer.Option(..., "--definition-name"),
    input_path: Path = typer.Option(..., "--input", exists=True),
    rules_version: str = typer.Option(..., "--rules-version"),
    evidence_summary_sha256: str = typer.Option(..., "--evidence-summary-sha256"),
    coverage_file: Path = typer.Option(None, "--coverage-file",
        help="JSON {segments, gaps} from build_snapshot_facts (optional)."),
    root: Path = typer.Option(".", "--root"),
) -> None:
    """Replace one universe's slice and republish (spec 7.0.10)."""
    project_root = _resolved_project_root(root)
    segments, gaps = _load_coverage_file(coverage_file)
    try:
        generation = prepare_refresh(
            project_root, universe_id=universe_id,
            definition_name=definition_name,
            prepared_frame=read_snapshot_rows(input_path),
            rules_version=rules_version,
            evidence_summary_sha256=evidence_summary_sha256,
            segments=segments, gaps=gaps)
        commit_refresh(project_root, generation)
    except MembershipRefreshError as error:
        _echo_failure(f"{error.error_code}: {error}")
        raise typer.Exit(code=2) from None
    typer.echo(f"dataset_version={generation.dataset_version}")
    typer.echo(f"definition_version={generation.definition_version}")


@index_membership_app.command("recover")
def data_index_membership_recover(
    to: str = typer.Option(..., "--to",
        help="definition_version of the target generation."),
    root: Path = typer.Option(".", "--root"),
) -> None:
    """Converge a torn membership refresh to an operator-chosen generation."""
    project_root = _resolved_project_root(root)
    try:
        generation = recover_refresh(project_root, to)
    except MembershipRefreshError as error:
        _echo_failure(f"{error.error_code}: {error}")
        raise typer.Exit(code=2) from None
    typer.echo(f"recovered_to={generation.definition_version}")
```

RUNBOOK 追加"## 阶段 9 · membership refresh 与崩溃恢复"：两命令、两个稳定错误码含义、"不一致不猜、operator `--to` 显式选择"、回滚 = `versions/` 文件替换、重跑幂等。`project/collect_index_weight_membership.py` step 5–6（`:250-300`；step 4 起于 `:233`）替换为对 `prepare_refresh`/`commit_refresh` 的调用（保留 step 1–3 取证与合并），`project/SCRIPTS.md` 同步一句。

- [ ] **Step 6: 跑测试确认通过 + 邻居**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_membership_refresh.py tests/unit/test_dataset_publisher.py -q`；再 `... tests/integration/test_cli.py -q -k "membership or index_membership"`（在途 WIP 文件只跑点名 -k）
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add src/stock_quant/data_model/membership_refresh.py src/stock_quant/data_model/dataset.py src/stock_quant/cli.py project/collect_index_weight_membership.py project/SCRIPTS.md RUNBOOK.md tests/integration/test_membership_refresh.py tests/unit/test_dataset_publisher.py
git commit -m "feat(refresh): crash-consistent membership refresh with prepare/commit/recover"
```

---

### Task 5: attested-boundary 事实生成与差异报告

**Files:** Modify `src/stock_quant/data_model/universe_membership.py:68-75`（reason 词汇）`、:349-378`（`_fact_record` + `membership_frame` 渲染 `collected_at`）、`src/stock_quant/data_model/schemas.py:103`、`src/stock_quant/data_quality/raw_checks.py:389-396`、`src/stock_quant/data_pipeline.py`（carry 补列）、`src/stock_quant/data_model/index_membership_import.py`；Test `tests/unit/test_membership_definition_v2.py`、`tests/unit/test_universe_membership.py`

**Interfaces:** Consumes Task 1/2。Produces：`MembershipReason.SNAPSHOT_OBSERVED_CHANGE = "snapshot_observed_change"`；`build_snapshot_facts(snapshots: Sequence[tuple[date, pd.DataFrame, str]], *, universe_id, source, source_url, collected_at, cadence, manual_confirmation_sha256=None) -> tuple[list[MembershipFact], tuple[MembershipCoverageSegment, ...], tuple[MembershipCoverageGap, ...]]`（每个快照项是 `(快照日, 成分帧, 快照文件 sha256)`——函数纯离线、不读盘，段/gap 的 `evidence_sha256` 与每条事实的 `snapshot_sha256` 都从第三个元素派生，`source_document_sha256` 取 `source_url` 所指文档的哈希，同为 64-hex，见 `MembershipFact` 的校验器）；`membership_difference_report(candidate_facts, official_facts) -> list[dict]`；`merge_collected_at_first_write(new_frame, baseline_frame) -> pd.DataFrame`。

- [ ] **Step 1: 写失败测试**（追加到 `tests/unit/test_membership_definition_v2.py`；`_snap(day, symbols, digest="a"*64)` 返回 `(day, pd.DataFrame({"symbol": symbols}), digest)`——第三个元素是快照文件哈希，`MembershipFact.snapshot_sha256` 是必填 64-hex，测试不能省。

  **本步同时把 `build_snapshot_facts`、`membership_difference_report`、`merge_collected_at_first_write` 三个名字加进文件头的 `from stock_quant.data_model.index_membership_import import (...)`**，`pandas` 按文件内既有风格在函数内 `import pandas as pd`（Task 1 的用例不用它，放文件头会让 Task 1 带 F401）。同一规则适用于 Task 2 追加的 `evaluate_index_membership_evidence` 与 Task 3 追加的 `resolve_versioned_universe_definition`、`UniverseCoverageError`——谁用谁在自己的 Step 1 补 import。）

```python
_KW = dict(universe_id="custom_csi500_tw", source="tushare",
           source_url="https://tushare.pro/document/2?doc_id=95",
           collected_at=datetime(2026, 9, 30, 8, 0), cadence="monthly")


def test_snapshot_diff_produces_attested_boundary_facts():
    facts, segments, gaps = build_snapshot_facts(
        [_snap(date(2024, 1, 31), ["600000.SH"]),
         _snap(date(2024, 2, 28), ["600000.SH", "000001.SZ"]),
         _snap(date(2024, 3, 29), ["000001.SZ"])], **_KW)
    join = next(f for f in facts if f.symbol == "000001.SZ"
                and f.raw_effective_from == date(2024, 2, 28))
    assert join.announcement_date == date(2024, 2, 28)  # = raw_effective_from
    assert join.reason.value == "snapshot_observed_change"
    gone = next(f for f in facts if f.symbol == "600000.SH")
    assert gone.raw_effective_to == date(2024, 3, 28)  # 最后列出快照前一日
    assert gone.status.value == "removed"
    assert gaps == () and len(segments) == 1


def test_a_missing_snapshot_opens_a_gap_and_starts_a_new_segment():
    facts, segments, gaps = build_snapshot_facts(
        [_snap(date(2024, 1, 31), ["600000.SH"]),
         _snap(date(2024, 3, 29), ["600000.SH"])], **_KW)
    assert [g.reason for g in gaps] == [MEMBERSHIP_OBSERVATION_GAP]
    assert len(segments) == 2
    # 跨 gap 不差分：段一闭、段二重开，无任何“精确移除日”声称
    intervals = sorted((f.raw_effective_from, f.raw_effective_to)
                       for f in facts if f.symbol == "600000.SH")
    assert len(intervals) == 2


def test_unknown_cadence_refuses_to_guess_without_manual_confirmation():
    with pytest.raises(ValueError, match="manual confirmation"):
        build_snapshot_facts([_snap(date(2024, 1, 31), ["600000.SH"])],
                             **{**_KW, "cadence": "unknown"})


def test_backfill_keeps_collected_at_provenance_only():
    import pandas as pd
    from stock_quant.data_model.universe_membership import membership_frame
    facts, *_ = build_snapshot_facts(
        [_snap(date(2015, 1, 30), ["600000.SH"])], **_KW)
    first = facts[0]
    assert first.collected_at.date() != first.announcement_date  # 补采 ≠ 快照日
    # merge 的键是 (universe_id, symbol, raw_effective_from)：baseline 必须
    # 落在同一键上，否则左连不命中、拿的是 new 的 collected_at。
    landed = membership_frame([fact(universe_id="custom_csi500_tw",
                                    raw_effective_from=date(2015, 1, 30),
                                    collected_at=datetime(2020, 1, 1))])
    merged = merge_collected_at_first_write(membership_frame([first]), landed)
    assert pd.Timestamp(merged["collected_at"].iloc[0]).date() == date(2020, 1, 1)


def test_official_overlaps_produce_only_a_difference_report():
    # 两侧必须真有区间差异：`fact()` 的默认值相同，照默认构造会得到两条
    # 逐字段相等的事实，正确实现应返回空报告，断言就永远不成立。
    candidate = [fact(universe_id="csi300", symbol="600000.SH",
                      raw_effective_from=date(2019, 1, 1),
                      raw_effective_to=date(2021, 12, 31))]
    official = [fact(universe_id="csi300", symbol="600000.SH",
                     raw_effective_from=date(2019, 1, 1),
                     raw_effective_to=date(2022, 6, 30))]
    report = membership_difference_report(candidate, official)
    assert report and report[0]["kind"] == "interval_disagreement"
    assert report[0]["symbol"] == "600000.SH"
```

另两个失败测试：`test_a_generated_gap_definition_fails_a_crossing_window`——用 `build_snapshot_facts` 的 gaps 构造 v2 定义，断言 Task 2 的 `window_crosses_membership_gap` 命中；`test_schema_accepts_both_membership_shapes`——11 列旧帧与 12 列新帧都过 `validate_membership_facts` 列检查，且 `data update` carry 旧表时补 `collected_at=NaT` 后可重发布（走 `tests/integration/test_data_pipeline.py` 既有 stub 帮手）。同步改 `tests/unit/test_universe_membership.py:75-84` 精确集合断言加 `"snapshot_observed_change"`。

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_membership_definition_v2.py tests/unit/test_universe_membership.py -q -k "snapshot or gap or cadence or backfill or overlap or shapes"`
Expected: FAIL — `ImportError: cannot import name 'build_snapshot_facts'`；`snapshot_observed_change` 不在词汇内。

- [ ] **Step 3: 实现**

`MembershipReason` 加 `SNAPSHOT_OBSERVED_CHANGE = "snapshot_observed_change"`（不进 :194-203 的 reason-status 限制分支）。

`_fact_record`（:349-362）加 `"collected_at": item.collected_at`——**这一步不能漏**：`membership_frame` 是 `pd.DataFrame([_fact_record(...)], columns=UNIVERSE_MEMBERSHIP_COLUMNS)`，只把列名加进 canonical 列表而 `_fact_record` 不产出该键，pandas 会静默补 NaN，`merged["collected_at"]` 恒 NaT，Task 5 的 provenance 用例与 `data update` 的 carry 都会拿到空值。`membership_frame` 里 `collected_at` 不做 `to_datetime` 强转（它本来就是 `datetime | None`，且**不进哈希**）。

`schemas.py`：`UNIVERSE_MEMBERSHIP_COLUMNS` 尾部加 `"collected_at"`，`_universe_membership_fields()` 加 `pa.field("collected_at", pa.timestamp("us"))`（可空）。`raw_checks.py:389` 列检查改两形态：`required = list(UNIVERSE_MEMBER_COLUMNS)`，`list(frame.columns)` 等于 `required` 或 `required + ["collected_at"]`，否则仍 `UNIVERSE_SCHEMA_MISMATCH`。`data_pipeline._read_baseline` 的 membership 读出处（:1660 附近）补：列缺失时 `frame["collected_at"] = pd.NaT`（旧形态兼容读取，处置方式是重发布）。

`index_membership_import.py` 实现三个函数，`build_snapshot_facts` 规则（纯离线）：快照按日期排序；`cadence="monthly"` 时应有快照为每月末——序列中缺失/空/日期键异常的月份即 gap `[上月末+1日, 下个有效快照日-1日]`，两端集合不差分；相邻有效快照差分：新列出的符号 `raw_effective_from=快照日`、active；消失的符号把既有 open 区间闭到 `快照日-1`、removed；两者 reason 均 `snapshot_observed_change`；每条事实 `announcement_date = raw_effective_from`（= 证实该起始边界的快照日，不得写采集当刻）；段 = 极大连续有效快照 run `[首快照日, 末快照日]`，`evidence_sha256` = 该 run 各快照文件哈希的 canonical JSON SHA-256；gap 的 `evidence_sha256` = 缺失月份清单 + 前后快照哈希的 canonical JSON SHA-256；run 首个快照的 open 区间 `reason=initial_constituent`（新段初始采集）；`collected_at` 逐事实写入但不进哈希（Task 1 已排除）。`cadence="unknown"` 且 `manual_confirmation_sha256 is None` → `ValueError("membership cadence unknown; manual confirmation required (spec 7.1)")`。`membership_difference_report(candidate, official)`：对同一 `universe_id/symbol` 的区间差异输出 `[{"kind": "symbol_added"|"symbol_removed"|"interval_disagreement", "symbol", "candidate", "official"}]`，不改任何输入。`merge_collected_at_first_write(new_frame, baseline_frame)`：按 `(universe_id, symbol, raw_effective_from)` 左连 baseline 的 `collected_at`，命中用 baseline 值（首落固定），未命中用 new 值。

- [ ] **Step 4: 跑测试确认通过 + 邻居**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_membership_definition_v2.py tests/unit/test_universe_membership.py tests/unit/test_index_membership_checks.py tests/integration/test_membership_refresh.py tests/integration/test_data_pipeline.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/data_model/universe_membership.py src/stock_quant/data_model/schemas.py src/stock_quant/data_quality/raw_checks.py src/stock_quant/data_pipeline.py src/stock_quant/data_model/index_membership_import.py tests/unit/test_membership_definition_v2.py tests/unit/test_universe_membership.py tests/integration/test_data_pipeline.py
git commit -m "feat(membership): attested-boundary snapshot facts with gap evidence and provenance"
```

---

## Self-Review 记录

- **§7.0 十条勾稽**：7.0.1→Task 1；7.0.2→Task 2（三消费者同 slice、整表校验不动、分叉被 resolver 外来行检查结构性拒绝）；7.0.3→Task 1（v1 拒 v2 键 + `exclude_none` 保 v1 字节）；7.0.4→Task 3（注册表 + 内容哈希复核 + 顶层完整定义/非递归/去重，指针文件阻断有测试）；7.0.5→Task 3 规则 + Task 4 执行（实树迁移经 refresh 一次性升 v2）；7.0.6→Task 3（无 slice 不冒充 v2、archive 只在最小 coverage_start 不变时，正反两用例）；7.0.7→Task 4 prepare（整表 carry + 按 universe_id 换 slice + 重跑门禁）；7.0.8→Task 4（同次 refresh 写新 v2 定义并追加注册表）；7.0.9→Task 3 测试钉住；7.0.10→Task 0 裁定门 + Task 4 状态机（两协议模板齐全，`COMMIT_PROTOCOL` 单值，未选段/路径删除）。
- **§7.1 勾稽**：attested-boundary 映射、announcement=快照日=raw_effective_from、snapshot_observed_change/initial_constituent、漏采→gap 证据+中断 coverage+新段只开不猜、节奏 unknown 人工确认、collected_at provenance 与补采同强度、CSI300 重叠只出差异报告——Task 5；`universe_gap_in_window` 与窗口判定——Task 2。
- **§2.2/§11 勾稽**：collected_at 不进 `membership_content_hash`（结构排除，有测试）与定义 version；dataset version 确定性由首落固定+重放不更新（`merge_collected_at_first_write`）；"refresh 中途崩溃→不猜、稳定错误码、recover --to"→Task 4；"成分与既有事实冲突→只出差异证据"→Task 5。
- **类型一致性**：`membership_slice_hash`、`MembershipCoverageSegment/Gap`、`slice_membership_frame`、`membership_coverage_violations`、`window_crosses_membership_gap`、`resolve_versioned_universe_definition`、`MembershipRefreshError.error_code`、`Generation/GenerationState`、`commit_steps/commit_refresh/recover_refresh`、`build_snapshot_facts`、`membership_difference_report`、`merge_collected_at_first_write` 各任务引用一致；`UNIVERSE_SLICE_EMPTY`/`UNIVERSE_FACT_OUTSIDE_COVERAGE`/`universe_gap_in_window`/`membership_observation_gap` 码名唯一。
- **留白（有意的，非占位）**：(1) `verify_generation` 的 P4 常驻服务启动挂钩按 ADR-023 记为 P4 计划义务；(2) 四个 v1 定义的实树 v2 升级由 Task 4 首次 refresh 对本机数据集离线执行，不在 Task 3 的离线测试里伪造；**但 v1 的按版本归档（Task 3 Step 4）不是留白——它是 Task 3 切换解析路径的前提，必须与 Task 3 同批提交**；(3) Task 5 的段/gap 产物经 `--coverage-file` JSON 进入 Task 4 publish（`_load_coverage_file` 在计划内定义），Task 5 未落地前 publish 以缺省单段形态自测。
- **复核记录（2026-09-30 源码实读）**：`UniverseDefinition`/`UniverseResolver`/criterion 在 `research/universe.py`（非 `research/models.py`）；runner 三消费点 :967/:974/:989-990 已核；`DatasetPublisher.publish` 尾部恒 `_replace_current`（dataset.py:135）故需 `promote=False`；`membership_content_hash` 整模型 dump 故加字段必须排除；`load_universe_coverage_criterion` 的 `glob("*.yml")` 非递归故 `versions/` 不可见；reason 词汇精确集合被 `test_universe_membership.py:75-84` 钉死需同步改。
