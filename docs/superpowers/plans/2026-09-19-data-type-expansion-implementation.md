# 数据类型拓展架构（D1–D6）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按已定稿规格落地六项架构能力：三档证据门禁（D1）、表级数据契约注册表（D2）、财务 PIT 消费层（D3）、端点描述符套件（D4）、增量取数经济学（D5）、官方通道金丝雀（D6）。

**Architecture:** 档位（core/anchored/research_only）只写在两份 `sources.yml` 的 `data_contracts` 段，经新增模块 `src/stock_quant/data_contracts.py` 解析进入运行时；`gates.py` 的发布谓词从 `item.code` 改为 `(code, table)` 查档位，未声明表 fail-closed 阻断。非 core 表的阻断级结构问题不再阻断发布，而是以 `coverage_downgraded`（UNTRUSTED，照 `corporate_action_coverage` 的 per-symbol-window 形状）记录进质量报告，research runner 预检在消费端拦截——本计划不新增任何数据集表（新锚定表的专用 coverage 表属未来战役、按 A4 手写接入时再注册）。验收窗口从 `requested_start_date` 换锚到 `full_history_acceptance_start`（D5.1），随后 B2 把取数窗改为按表契约的增量窗口并把「重取段 vs 结转段 vs NOT_FETCHED」写入 `build_config.table_fetch_coverage`，配调用账本与季度漂移审计。PIT 访问器与描述符套件（B3）为下一张新表战役提供框架，D6 金丝雀并行扩展 `project/verify_update_readiness.py`。

**Tech Stack:** Python ≥3.10（sq312 conda env）、pandas、pyarrow、duckdb、pydantic v2、pytest。无新第三方依赖。

**Spec:** [docs/superpowers/specs/2026-09-19-data-type-expansion-architecture-design.md](../specs/2026-09-19-data-type-expansion-architecture-design.md)

## Global Constraints

- 批序固定：B0 → B1 → B2 → B3；D6（Task 18）与 B0–B3 无依赖，任何批次完成后即可做。B2 的前置是 B1 换锚已落地；B3 的前置是 B0 注册表存在。
- 测试优先：每个行为变更先写失败测试并观察其按预期原因失败，再做最小修复（仓库规则 tests.md）。全程用 sq312 环境：`conda run -n sq312 pytest ...`（本机 `.venv` 损坏，勿用）。
- 提交信息以 `Co-Authored-By: Claude Code <noreply@anthropic.com>` 结尾；每个 Task 独立提交，只 `git add` 本 Task 的文件。
- 保护在途 WIP，绝不覆盖/回退/暂存/重排：`project/configs/sources.yml`、`templates/project-config/sources.yml`（两份均在修改中——B0 回填时**只追加** `data_contracts` 段）、`PROJECT_MEMORY.md`、`docs/adr/DECISIONS_INDEX.md`（只追加 010/011 行）、`src/stock_quant/data_sources/baostock.py`、`src/stock_quant/data_sources/base.py`、`tests/integration/test_source_contracts.py`、`tests/unit/test_source_retry.py`、`说明.md`、`requirements.txt`、`docs/adr/009-*.md`、`docs/operations/2026-09-19-baostock-*.md`、`docs/research/2026-09-19-architecture-diagram.md`、spec 文件本身。
- 不弱化任何发布/验收门禁；被拒发布保持为记录证据。不把失败改造成通过（config-and-operations.md）。
- 凭据零容忍：token/key/代理 URL 凭证段不进代码、配置、日志、fixtures、报告；D6 与漂移审计的运维记录只落端点名、参数形状与结果。
- 已发布数据集内容寻址、不可变（ADR-001）：只发布新版本，绝不就地改历史；漂移审计发现即「新证据版本 + 事件记录」。
- 档位只经 `sources.yml` 加载路径 + ADR 变更进入运行时；`src/` 内档位字面量只允许出现在 `data_contracts.py`（Task 10 静态扫描锁定）。
- `primary_transport` 的 kind 必须是传输规范 token（`relay`/`official`/`proxy`，tushare_transport.py:46-48），不得造并行词汇表。
- A1 矩阵：`REPORT_GENERATION_FAILED`、`QUARANTINE_MISSING_REASON`、`UNREGISTERED_TABLE` 为全局进程码恒阻断；其余 13 个表级码按 `(code, table)` 档位分流。
- 档位是运行时策略：不进冻结规格、不参与实验身份哈希；已冻结/已接受实验不回溯重判（A2）。
- bootstrap 数据集按设计不可验收：换锚后应 FAIL（`full_history_acceptance_start_missing`），不是崩溃（D5.1/§0-12）。
- 治理行宽：新 ADR 文件 ≤ 400 行（tools/check_context_governance.py）；`project/SCRIPTS.md` 与 `project/*.py` 一一对应（tools/check_operational_docs.py）——新增 project 脚本必须同步 SCRIPTS.md。

## File Structure

**新建：**

- `src/stock_quant/data_contracts.py` — D2 契约模型 + 档位词汇表 + `parse_data_contracts`；档位字面量唯一居所（B0/T1）。
- `src/stock_quant/data_model/fetch_coverage.py` — 表级取数覆盖模型（fetched/carried/not_fetched 段）、校验、build_config 载荷（B2/T11）。
- `src/stock_quant/data_model/call_ledger.py` — 调用账本渲染与落盘（B2/T14）。
- `src/stock_quant/research/pit.py` — D3 PIT 访问器（B3/T16）。
- `src/stock_quant/data_sources/descriptors.py` — D4 端点描述符套件（B3/T17）。
- `project/drift_audit.py` — 季度全窗口漂移审计（B2/T15，进 `project/SCRIPTS.md`）。
- `docs/adr/010-evidence-tiered-publication-gating.md`、`docs/adr/011-acceptance-window-anchored-to-acceptance-obligation.md`（B1/T4）。
- 测试：`tests/unit/test_data_contracts.py`、`tests/unit/test_tiered_publication_gate.py`、`tests/integration/test_tiered_publication.py`、`tests/unit/test_factor_inputs.py`、`tests/unit/test_table_tier_preflight.py`、`tests/integration/test_table_tier_preflight.py`、`tests/integration/test_window_anchor_regression.py`、`tests/unit/test_tier_literals.py`、`tests/unit/test_fetch_coverage.py`、`tests/unit/test_call_ledger.py`、`tests/unit/test_drift_audit.py`、`tests/unit/test_pit_as_of.py`、`tests/unit/test_descriptors.py`、`tests/unit/test_official_canary.py`。

**修改：**

- `src/stock_quant/config.py` — `ProjectConfig.data_contracts` 字段 + `load_project_config` 拆分解析（B0/T1）。
- `project/configs/sources.yml`、`templates/project-config/sources.yml` — 追加 `data_contracts` 段（B0/T2，只追加）。
- `src/stock_quant/data_quality/models.py` — 新增 `CODE_UNREGISTERED_TABLE`、`CODE_COVERAGE_DOWNGRADED`（B0/T3、B1/T6）。
- `src/stock_quant/data_quality/gates.py` — `GLOBAL_PROCESS_CODES`、`TABLE_LEVEL_BLOCKING_CODES`、`evaluate_publication(..., table_tiers=...)`（B0/T3、B1/T5）。
- `src/stock_quant/data_model/dataset.py` — `publish(..., table_tiers=None)` 透传；`_normalize_build_config` 接受 `table_fetch_coverage`（B1/T6、B2/T11）。
- `src/stock_quant/data_pipeline.py` — 发布前契约校验、档位透传、降级记录、增量取数窗、账本（B0/T3、B1/T6、B2/T13、B2/T14）。
- `src/stock_quant/research/acceptance/checks.py` — `_window` 换锚 + `FullHistoryAcceptanceStartMissing` + `_check_quality_report` 档位化 + `_check_table_fetch_coverage`（B1/T5、T9、B2/T13）。
- `src/stock_quant/research/acceptance/models.py` — `AUTOMATED_CHECK_CODES` 增 `table_fetch_coverage_evidence`（B2/T13）。
- `src/stock_quant/research/acceptance/evidence.py` — `evidence_window` 区分锚缺失错误（B1/T9）。
- `src/stock_quant/factors/base.py`、`src/stock_quant/factors/momentum.py` — `Factor.inputs` 声明（B1/T7）。
- `src/stock_quant/research/runner.py` — 表档位预检 `_preflight_table_tiers`（B1/T8、B2/T14）。
- `src/stock_quant/cli.py` — 其 `evaluate_publication` 调用点补档位透传（B1/T6）。
- `project/verify_update_readiness.py` — `--official-canary` 模式（D6/T18）。
- `RUNBOOK.md` — :80-84 退役（B1/T9）、:102-105 退役（B2/T14）。
- `docs/adr/DECISIONS_INDEX.md` — 只追加 010/011 两行（B1/T4）。
- `project/SCRIPTS.md` — 登记 `project/drift_audit.py`（B2/T15）。

---

### Task 1（B0）：契约模型 `data_contracts.py` + 配置解析拆分

**Files:**
- Create: `src/stock_quant/data_contracts.py`
- Modify: `src/stock_quant/config.py:14`（新增 import）、`src/stock_quant/config.py:56-75`（新增字段）
- Modify: `src/stock_quant/config.py:96-103`（`load_project_config` 拆分解析）
- Test: `tests/unit/test_data_contracts.py`

**Interfaces:**
- Produces: `parse_data_contracts(payload: object) -> dict[str, DataContract]`；`DataContract`（字段 `table`、`tier`、`primary_transport`、`anchors`、`conflict`、`pit: PitContract | None`、`coverage_shape`、`incremental`）；常量 `TIER_CORE/TIER_ANCHORED/TIER_RESEARCH_ONLY`、`TIERS`、`TIER_BLOCKS_PUBLICATION: dict[str | None, bool]`（Task 5 消费）、`TRANSPORT_KINDS`、`INCREMENTAL_STRATEGIES`、`CONFLICT_MODES`、`FACT_ROW_POLICY_MAX_REPORT_TYPE_V1`、`FACT_ROW_POLICIES`、`COVERAGE_SHAPES`（Task 12/16/17 消费）。`ProjectConfig.data_contracts: dict[str, DataContract]`（Task 2/3/6/8/12 消费）。

- [ ] **Step 0（前置检查，不提交）**：`git status` 确认两份 `sources.yml` 的在途修改仍存在；`git diff --stat` 只读检查，不触碰。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_data_contracts.py
"""D2 contract-shape parsing: sources.yml ``data_contracts`` declarations."""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_quant.config import ProjectConfig
from stock_quant.data_contracts import DataContract, parse_data_contracts


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "table": "income",
        "tier": "anchored",
        "primary_transport": "tushare:relay",
        "anchors": ["akshare_cninfo_announcement"],
        "conflict": "downgrade",
        "pit": {
            "as_of_field": "f_ann_date",
            "fallback": "ann_date",
            "fact_row_policy": "max_report_type_v1",
        },
        "coverage_shape": "per_symbol_window",
        "incremental": "disclosure_calendar",
    }
    row.update(overrides)
    return row


def test_valid_anchored_contract_parses():
    parsed = parse_data_contracts([_row()])
    contract = parsed["income"]
    assert isinstance(contract, DataContract)
    assert contract.tier == "anchored"
    assert contract.pit is not None and contract.pit.fallback == "ann_date"


def test_missing_key_rejected():
    row = _row()
    del row["incremental"]
    with pytest.raises(ValueError):
        parse_data_contracts([row])


def test_unknown_key_rejected():
    with pytest.raises(ValueError):
        parse_data_contracts([_row(invented_key="x")])


def test_tier_vocabulary_closed():
    with pytest.raises(ValueError):
        parse_data_contracts([_row(tier="important")])


def test_transport_kind_must_be_token():
    with pytest.raises(ValueError):
        parse_data_contracts([_row(primary_transport="tushare")])
    with pytest.raises(ValueError):
        parse_data_contracts([_row(primary_transport="tushare:sdksdk")])


def test_anchored_requires_anchor():
    with pytest.raises(ValueError):
        parse_data_contracts([_row(anchors=[])])


def test_core_and_research_only_forbid_anchors():
    with pytest.raises(ValueError):
        parse_data_contracts(
            [_row(tier="core", anchors=["akshare_cninfo_announcement"])]
        )
    with pytest.raises(ValueError):
        parse_data_contracts(
            [_row(tier="research_only", anchors=["akshare_cninfo_announcement"])]
        )


def test_duplicate_table_rejected():
    with pytest.raises(ValueError):
        parse_data_contracts([_row(), _row()])


def test_project_config_carries_contracts():
    contracts = parse_data_contracts([_row()])
    config = ProjectConfig.model_validate(
        {
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "initial_cash": 100000,
            "benchmark_symbols": ["000300.SH"],
            "data_contracts": contracts,
        }
    )
    assert config.data_contracts["income"].tier == "anchored"
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n sq312 pytest tests/unit/test_data_contracts.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stock_quant.data_contracts'`。

- [ ] **Step 3: 最小实现 —— `src/stock_quant/data_contracts.py`**

```python
"""Table-level data-contract declarations (spec D2).

Loaded exclusively from the ``data_contracts`` section of
``configs/sources.yml``.  This module is the ONLY home of tier literals
under ``src/`` — enforced by tests/unit/test_tier_literals.py (spec §5
criterion 3): tier values enter the runtime only through the sources.yml
loading path, never as in-code constants elsewhere.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: The three evidence tiers of spec D1 / §6 B1.
TIER_CORE = "core"
TIER_ANCHORED = "anchored"
TIER_RESEARCH_ONLY = "research_only"
TIERS = frozenset({TIER_CORE, TIER_ANCHORED, TIER_RESEARCH_ONLY})

#: Per-tier publication semantics for table-level blocking codes (spec §6 A1).
#: ``None`` is the fail-closed default: an undeclared table blocks.
TIER_BLOCKS_PUBLICATION: dict[str | None, bool] = {
    None: True,
    TIER_CORE: True,
    TIER_ANCHORED: False,
    TIER_RESEARCH_ONLY: False,
}

#: Transport-kind tokens (tushare_transport RELAY/OFFICIAL/PROXY values) —
#: ``primary_transport`` kind must be one of these, never free text.
TRANSPORT_KINDS = frozenset({"relay", "official", "proxy"})

INCREMENTAL_LAST_COVERED_PLUS_1 = "last_covered_plus_1"
INCREMENTAL_DISCLOSURE_CALENDAR = "disclosure_calendar"
INCREMENTAL_CHANGE_DRIVEN_FULL = "change_driven_full"
INCREMENTAL_STRATEGIES = frozenset(
    {
        INCREMENTAL_LAST_COVERED_PLUS_1,
        INCREMENTAL_DISCLOSURE_CALENDAR,
        INCREMENTAL_CHANGE_DRIVEN_FULL,
    }
)

CONFLICT_BLOCK = "block"
CONFLICT_DOWNGRADE = "downgrade"
CONFLICT_ARBITRATE = "arbitrate"
CONFLICT_MODES = frozenset({CONFLICT_BLOCK, CONFLICT_DOWNGRADE, CONFLICT_ARBITRATE})

#: First-version fact-row policy (spec D2, owner 约束 3: versioned rule name).
FACT_ROW_POLICY_MAX_REPORT_TYPE_V1 = "max_report_type_v1"
FACT_ROW_POLICIES = frozenset({FACT_ROW_POLICY_MAX_REPORT_TYPE_V1})

COVERAGE_SHAPE_PER_SYMBOL_WINDOW = "per_symbol_window"
COVERAGE_SHAPE_NONE = "none"
COVERAGE_SHAPES = frozenset({COVERAGE_SHAPE_PER_SYMBOL_WINDOW, COVERAGE_SHAPE_NONE})


class PitContract(BaseModel):
    """PIT fact-row selection contract (spec D2 ``pit`` block)."""

    model_config = ConfigDict(extra="forbid")

    as_of_field: str = Field(min_length=1)
    fallback: str = Field(min_length=1)
    fact_row_policy: Literal["max_report_type_v1"]


class DataContract(BaseModel):
    """One table's declaration; every field required (spec D2「缺一不得接入」)."""

    model_config = ConfigDict(extra="forbid")

    table: str = Field(min_length=1)
    tier: Literal["core", "anchored", "research_only"]
    primary_transport: str
    anchors: list[str] = Field(default_factory=list)
    conflict: Literal["block", "downgrade", "arbitrate"]
    pit: PitContract | None = None
    coverage_shape: Literal["per_symbol_window", "none"]
    incremental: Literal[
        "last_covered_plus_1", "disclosure_calendar", "change_driven_full"
    ]

    @model_validator(mode="after")
    def _transport_shape(self) -> "DataContract":
        # ``<source-name>:<transport-kind>``; the kind must be a transport
        # vocabulary token, not a source name (spec D2).
        if ":" not in self.primary_transport:
            raise ValueError(
                "primary_transport must be '<source>:<kind>', got "
                f"{self.primary_transport!r}"
            )
        kind = self.primary_transport.rsplit(":", 1)[1]
        if kind not in TRANSPORT_KINDS:
            raise ValueError(
                f"primary_transport kind {kind!r} is not a transport token"
            )
        return self

    @model_validator(mode="after")
    def _anchor_shape(self) -> "DataContract":
        # Anchored tables must name their independent anchors; core and
        # research_only tables must not (research_only means "no anchor").
        if self.tier == TIER_ANCHORED and not self.anchors:
            raise ValueError(
                f"anchored table {self.table!r} requires at least one anchor"
            )
        if self.tier != TIER_ANCHORED and self.anchors:
            raise ValueError(
                f"{self.tier} table {self.table!r} must not declare anchors"
            )
        return self


def parse_data_contracts(payload: object) -> dict[str, DataContract]:
    """Parse a ``data_contracts`` section; duplicate tables are rejected."""
    if payload is None:
        return {}
    if not isinstance(payload, list):
        raise ValueError("data_contracts must be a list")
    contracts: dict[str, DataContract] = {}
    for row in payload:
        contract = DataContract.model_validate(row)
        if contract.table in contracts:
            raise ValueError(f"duplicate data_contract table {contract.table!r}")
        contracts[contract.table] = contract
    return contracts
```

- [ ] **Step 4: 配置解析 —— `src/stock_quant/config.py`**

两处编辑：

（1）import 区（:10-11 之后）加：

```python
from stock_quant.data_contracts import DataContract, parse_data_contracts
```

（2）`ProjectConfig`（:63 之后）加字段：

```python
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    data_contracts: dict[str, DataContract] = Field(default_factory=dict)
```

（3）`load_project_config`（:96-103）改为：

```python
    project = read("project.yml")
    sources = read("sources.yml")
    project["sources"] = {
        name: entry for name, entry in sources.items() if name != "data_contracts"
    }
    project["data_contracts"] = parse_data_contracts(sources.get("data_contracts"))
    project["costs"] = read("costs.yml")
```

（`sources.yml` 顶层其余键仍是源配置；只有 `data_contracts` 一个键被拆出——`ProjectConfig` 是 `extra="forbid"`，list 值不能进 `sources: dict[str, SourceConfig]`，拆分正是为此。）

- [ ] **Step 5: 运行通过**

Run: `conda run -n sq312 pytest tests/unit/test_data_contracts.py tests/unit/test_config.py -q`
Expected: PASS（`test_config.py` 现有用例不受影响——无 `data_contracts` 段时解析结果为空字典，与旧行为等价）。

- [ ] **Step 6: Commit**

```bash
git add src/stock_quant/data_contracts.py src/stock_quant/config.py tests/unit/test_data_contracts.py
git commit -m "feat(data_contracts): add D2 table-contract registry model and config parsing

sources.yml gains a data_contracts section parsed into ProjectConfig;
tier values live only in data_contracts.py and enter the runtime solely
through the sources.yml loading path (spec D2, criteria 3).

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2（B0）：回填两份 `sources.yml` + 完整性测试

**Files:**
- Modify: `project/configs/sources.yml`（**只追加**，不触碰在途修改的行）
- Modify: `templates/project-config/sources.yml`（**只追加**）
- Test: `tests/unit/test_data_contracts.py`（追加一个测试）

**Interfaces:**
- Consumes: `parse_data_contracts`（Task 1）、`STANDARDIZED_SCHEMAS`（dataset.py:54）。
- Produces: 9 张在册表的契约声明 + 各表 `incremental` 初值（Task 3/12 消费）。B1 前生效的赋值即规格 §6 B1：全部 core。

- [ ] **Step 1: 写失败测试（追加到 tests/unit/test_data_contracts.py）**

```python
_LAYOUTS = (
    Path(__file__).resolve().parents[2]
    / "project"
    / "configs"
    / "sources.yml",
    Path(__file__).resolve().parents[2]
    / "templates"
    / "project-config"
    / "sources.yml",
)


@pytest.mark.parametrize("layout", _LAYOUTS, ids=str)
def test_backfill_covers_every_registered_table(layout: Path):
    from stock_quant.data_model.dataset import STANDARDIZED_SCHEMAS
    from stock_quant.safe_yaml import read_yaml

    document = read_yaml(layout)
    declared = set(parse_data_contracts(document.get("data_contracts")))
    missing = sorted(set(STANDARDIZED_SCHEMAS) - declared)
    assert not missing, f"registered tables without a contract: {missing}"
```

（`parents[2]` 从 `tests/unit/` 上溯到仓库根。）

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n sq312 pytest tests/unit/test_data_contracts.py::test_backfill_covers_every_registered_table -q`
Expected: FAIL — 9 张表全部 missing。

- [ ] **Step 3: 回填**

先 `git diff project/configs/sources.yml templates/project-config/sources.yml` 确认在途 hunks 原样。然后在**每个文件末尾追加**（前面内容一字不动）：

```yaml
# ---- Table-level data contracts (spec D2 / ADR-010) ----------------------- #
# One declaration per published table.  The data_update publish path rejects
# any published table without a declaration (unregistered_table FATAL).
# tier: core / anchored / research_only (D1).  Tier changes happen ONLY via
# this file + an ADR, never in code.
# primary_transport: "<source>:<kind>" where kind is a transport token
# (relay / official / proxy) - a transport reference, not a source name.
# incremental: the per-table fetch-window strategy (D5/A3).
data_contracts:
  - table: daily_bar
    tier: core
    primary_transport: tushare:relay
    anchors: []
    conflict: downgrade
    pit: null
    coverage_shape: none
    incremental: last_covered_plus_1
  - table: adjusted_bar
    tier: core
    # Derived each update from daily_bar + corporate_action, full rebuild.
    primary_transport: tushare:relay
    anchors: []
    conflict: block
    pit: null
    coverage_shape: none
    incremental: change_driven_full
  - table: security_master
    tier: core
    primary_transport: tushare:relay
    anchors: []
    conflict: block
    pit: null
    coverage_shape: none
    incremental: last_covered_plus_1
  - table: security_master_coverage
    tier: core
    primary_transport: tushare:relay
    anchors: []
    conflict: block
    pit: null
    coverage_shape: none
    incremental: last_covered_plus_1
  - table: corporate_action
    tier: core
    primary_transport: akshare:official
    anchors: []
    conflict: arbitrate
    pit: null
    coverage_shape: per_symbol_window
    incremental: disclosure_calendar
  - table: corporate_action_quarantine
    tier: core
    primary_transport: akshare:official
    anchors: []
    conflict: block
    pit: null
    coverage_shape: none
    incremental: disclosure_calendar
  - table: corporate_action_coverage
    tier: core
    primary_transport: akshare:official
    anchors: []
    conflict: block
    pit: null
    coverage_shape: none
    incremental: disclosure_calendar
  - table: trading_calendar
    tier: core
    primary_transport: tushare:relay
    anchors: []
    conflict: block
    pit: null
    coverage_shape: none
    incremental: last_covered_plus_1
  - table: universe_membership
    tier: core
    # Imported offline from sealed CSI snapshots; carried verbatim by updates.
    primary_transport: csi:official
    anchors: []
    conflict: block
    pit: null
    coverage_shape: none
    incremental: change_driven_full
```

（`daily_bar` 的 `conflict: downgrade` 对应现有 cross-source 比较语义：baostock 冲突降级不阻断；`corporate_action` 的 `arbitrate` 对应 ADR-007 TDX 仲裁。）

- [ ] **Step 4: 运行通过**

Run: `conda run -n sq312 pytest tests/unit/test_data_contracts.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add project/configs/sources.yml templates/project-config/sources.yml tests/unit/test_data_contracts.py
git commit -m "feat(data_contracts): backfill contracts for all nine registered tables

All existing tables are core (spec B1); incremental initial values per
table rhythm so B2 window planning has a start (spec D2, F3).

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3（B0）：发布时 `unregistered_table` 校验（作用域 `origin: data_update`）

**Files:**
- Modify: `src/stock_quant/data_quality/models.py:45-74`（码词汇区，追加常量）
- Modify: `src/stock_quant/data_quality/gates.py:14-31`（import）、`gates.py:39-57`（阻断集合）
- Modify: `src/stock_quant/data_pipeline.py`（update() 内 publish 前校验）
- Test: `tests/unit/test_tiered_publication_gate.py`

**Interfaces:**
- Consumes: `parse_data_contracts`/`ProjectConfig.data_contracts`（Task 1-2）、`_issue` 助手（data_pipeline.py 既有）。
- Produces: `CODE_UNREGISTERED_TABLE = "unregistered_table"`（models.py，Task 5/6 消费）；`_contract_issues(tables, contracts)`（data_pipeline.py，Task 6 扩展）。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_tiered_publication_gate.py
"""Publish-path contract validation: unregistered tables are FATAL (D2)."""

from __future__ import annotations

from stock_quant.data_contracts import parse_data_contracts
from stock_quant.data_pipeline import _contract_issues
from stock_quant.data_quality.models import CODE_UNREGISTERED_TABLE, Severity

CONTRACTS = parse_data_contracts(
    [
        {
            "table": "daily_bar",
            "tier": "core",
            "primary_transport": "tushare:relay",
            "anchors": [],
            "conflict": "downgrade",
            "pit": None,
            "coverage_shape": "none",
            "incremental": "last_covered_plus_1",
        }
    ]
)


def test_registered_table_emits_nothing():
    assert _contract_issues({"daily_bar": object()}, CONTRACTS) == []


def test_unregistered_table_is_fatal():
    issues = _contract_issues({"daily_bar": object(), "novel_table": object()}, CONTRACTS)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity is Severity.FATAL
    assert issue.code == CODE_UNREGISTERED_TABLE
    assert issue.table == "novel_table"
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n sq312 pytest tests/unit/test_tiered_publication_gate.py -q`
Expected: FAIL — `ImportError`（`CODE_UNREGISTERED_TABLE` / `_contract_issues` 不存在）。

- [ ] **Step 3: 码常量 + 阻断集合**

`src/stock_quant/data_quality/models.py`，在 :74 `CODE_QUARANTINE_OUT_OF_WINDOW` 块之后追加：

```python
# A published table without a data_contracts declaration (spec D2): the
# publish path rejects it before staging, so any report carrying this code
# names a dataset that must never reach the immutable store.
CODE_UNREGISTERED_TABLE = "unregistered_table"
```

`src/stock_quant/data_quality/gates.py`：

- import 列表（:14-31）加 `CODE_UNREGISTERED_TABLE`（按字母序插入 `CODE_UNKNOWN_SOURCE` 之后）；
- `PUBLICATION_BLOCKING_CODES` 集合（:39-57）内加一行 `CODE_UNREGISTERED_TABLE,`（含注释说明：发布路径契约校验，恒阻断——B1 起归入全局进程码集合）。

- [ ] **Step 4: `_contract_issues` + update() 接线**

`src/stock_quant/data_pipeline.py` 模块级（放在 `_issue` 助手附近）新增：

```python
def _contract_issues(
    tables: Mapping[str, object], contracts: Mapping[str, object]
) -> list[QualityIssue]:
    """FATAL ``unregistered_table`` for every table missing a D2 declaration."""
    issues: list[QualityIssue] = []
    for name in sorted(tables):
        if name not in contracts:
            issues.append(
                _issue(Severity.FATAL, CODE_UNREGISTERED_TABLE, table=name)
            )
    return issues
```

（如 `_issue` 签名与猜测不符，以文件内实际签名为准——`update()` :914-920 已有调用样例。）

`update()` 接线：当前顺序是 :855 构造 report → 门控 → :871 构造 `tables` 字典 → :897 publish。把 :871-895 的 `tables = {...}` 块**上移**到 :855 `report = QualityReport(issues=tuple(issues))` 之前，并在两者之间插入：

```python
        # Publish-path contract gate (spec D2): scoped to data_update by
        # construction — bootstrap publishes through DatasetPublisher
        # directly and never reaches update().
        issues.extend(
            _contract_issues(tables, self._project_config.data_contracts)
        )
```

（`self._project_config` 在 update() 中已被 :652 `self._project_config.start_date` 使用，字段可用；若名称不同以文件为准。）移动后门控与 publish 位置不变：未声明表产生 FATAL → `fatal_present` 分支阻断发布。

- [ ] **Step 5: 运行通过**

Run: `conda run -n sq312 pytest tests/unit/test_tiered_publication_gate.py tests/unit/test_quality_checks.py -q`
Expected: PASS（`test_quality_checks.py` 现有阻断集合断言若按字面集比较需同步更新——运行后如失败，按失败信息把新码加进该测试的期望集合，不删不改其它断言）。

- [ ] **Step 6: Commit**

```bash
git add src/stock_quant/data_quality/models.py src/stock_quant/data_quality/gates.py src/stock_quant/data_pipeline.py tests/unit/test_tiered_publication_gate.py
git commit -m "feat(data_contracts): reject unregistered published tables in data_update

Publish-path contract validation emits unregistered_table FATAL for
every published table without a D2 declaration; the check lives in
update() so bootstrap stays untouched (spec D2, criterion 9).

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4（B1）：ADR-010 + ADR-011 + 决策索引

**Files:**
- Create: `docs/adr/010-evidence-tiered-publication-gating.md`
- Create: `docs/adr/011-acceptance-window-anchored-to-acceptance-obligation.md`
- Modify: `docs/adr/DECISIONS_INDEX.md`（**只追加两行**，在途 008/009 行不触碰）

**Interfaces:**
- Produces: 两份决策记录——Task 5/6（档位门禁）与 Task 9（换锚）的治理依据；B1 生效。

- [ ] **Step 1: 读既有 ADR 格式**

Read `docs/adr/009-corporate-action-absent-ex-date.md`（工作区未跟踪文件，只读）与 `docs/adr/DECISIONS_INDEX.md` 尾部 20 行，采用同款头部（编号/标题/日期/状态/相关 ADR）与索引行格式。注意治理行宽：ADR 文件 ≤ 400 行。

- [ ] **Step 2: 写 ADR-010**

```markdown
# ADR-010: Evidence-Tiered Publication Gating

日期：2026-09-19
状态：accepted
相关：ADR-011（验收窗口锚定验收义务，同批 B1）、spec
2026-09-19-data-type-expansion-architecture-design §2 D1 / §6 A1

## 背景

`PUBLICATION_BLOCKING_CODES`（gates.py）是一个扁平 frozenset，15 个阻断码
对任何表一视同仁。新增数据类型（财务、行业等）的核验成熟度低于存量七表：
一表结构问题拖垮整轮发布，会把新数据永久挡在门外；但直接放行又违背
「不得弱化发布门禁」的仓库纪律。

## 决策

发布门禁按「证据强度」分三档（spec D1），档位写进 sources.yml 的
`data_contracts` 段，判定谓词从 `item.code` 改为 `(item.code, item.table)`
查声明映射：

1. `core`：15 码任一命中即整轮不发布（现状不变）。存量七主表与全部
   coverage 表一律 core（spec §6 B1）。
2. `anchored`：声明过独立锚点的表；表级结构码命中时不阻断发布，改为该表
   coverage `UNTRUSTED` 记录（照 corporate_action_coverage 的
   per-symbol-window 形状），research run 预检在消费端拦截。
3. `research_only`：无独立锚点的表；发布语义同 anchored，但无论 coverage
   多干净，正式 research run 恒拒绝消费；只能被工程诊断规格引用并标注
   RESEARCH-ONLY。

`(code × tier)` 首版矩阵（spec §6 A1）：`REPORT_GENERATION_FAILED`、
`QUARANTINE_MISSING_REASON` 为全局进程码恒阻断；其余 13 个表级码按档位
分流。未声明表按 core 阻断（fail-closed）——「忘记声明」不得等于绕过开关。

## 后果

- 档位变更 = 配置变更 + 本 ADR 同批留痕；开发者不得即席决定档位。
- 档位是运行时策略：不进冻结规格、不参与实验身份哈希；已冻结/已接受
  实验不回溯重判，新 run 立即生效。
- 非 core 表的降级记录以 `coverage_downgraded`（WARNING，不进阻断码表）
  落质量报告，消费端预检按钉住版本的质量报告裁决。
```

- [ ] **Step 3: 写 ADR-011**

```markdown
# ADR-011: Acceptance Window Anchored to the Acceptance Obligation

日期：2026-09-19
状态：accepted
相关：ADR-010（同批 B1）、spec
2026-09-19-data-type-expansion-architecture-design §2 D5 第 1 条 / §6 B3

## 背景

验收检查 `_window`（acceptance/checks.py）把审查窗口锚在
`build_config.requested_start_date` 上：1d6e43b4… 版本以
`--start 2015-01-01` 发布（早于首个开市日 2015-01-05），按构造
`window_not_calendar_complete` FAIL；RUNBOOK 不得不记录「--start 必须落
开市日」的操作坑。更根本的问题是：审查窗口锚在「取数请求」而非「验收
义务」（universe coverage 起点 `full_history_acceptance_start`，发布时
固定）。B2 增量化落地后，取数窗口每轮缩小，锚在请求上会让审查窗口与
证据包随每次更新静默缩小——evidence_window 的 docstring 明写要防的
正是这件事；requested=null 时 `_window` 抛裸 ValueError 更让默认路径
无法验收。

## 决策

`_window` 起点候选序列（不取 min——min 会放宽到日历证据之前，
checks.py 的 start < 首个开市日硬 FAIL 使 min 窗口保证失败，该提案已
撤回，spec §0 第 11 条）：

1. `full_history_acceptance_start`（非空 str）；
2. 回退 `requested_start_date`（legacy / 非 data_update 来源）；
3. 否则抛专用异常 `full_history_acceptance_start_missing`
   （ValueError 子类：run_automated_checks 捕获表覆盖它 → 验收 FAIL
   而非崩溃；专用类型使 evidence_window 能区分于 `window_missing`）。

bootstrap 数据集按设计不可验收：其 manifest 两字段皆无，验收 FAIL
（`full_history_acceptance_start_missing`），不是崩溃（spec §0 第 12 条）。

## 后果

- `--start 2015-01-01` 发布的版本 `date_window_completeness` 由 FAIL 转
  PASS；requested=acceptance_start 的既有版本行为不变（spec §5 判据 7）。
- data_update 默认路径（requested=null）可被验收（判据 8）。
- RUNBOOK「--start 必须落开市日」条目退役。
- `date_window_completeness` 与 `calendar_coverage_evidence` 首次共用同一
  时钟：每次验收复扫全历史 bar，与 B2 漂移审计互为加强。
```

- [ ] **Step 4: 追加 DECISIONS_INDEX.md 两行**

只追加（格式照索引现有行；读取后对齐列数）：

```markdown
| [010](010-evidence-tiered-publication-gating.md) | evidence-tiered publication gating（D1 三档门禁） | 2026-09-19 | accepted |
| [011](011-acceptance-window-anchored-to-acceptance-obligation.md) | 验收窗口锚定验收义务（D5.1 换锚） | 2026-09-19 | accepted |
```

- [ ] **Step 5: 治理校验**

Run: `conda run -n sq312 pytest tests/unit/test_context_governance_docs.py -v`
Expected: PASS（ADR 行宽、索引链接、根协议指引全过）。

- [ ] **Step 6: Commit**

```bash
git add docs/adr/010-evidence-tiered-publication-gating.md docs/adr/011-acceptance-window-anchored-to-acceptance-obligation.md docs/adr/DECISIONS_INDEX.md
git commit -m "docs(adr): adopt ADR-010 tiered gating and ADR-011 acceptance-window anchor

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5（B1）：三档门禁谓词 + 验收 `_check_quality_report` 档位化

**Files:**
- Modify: `src/stock_quant/data_quality/gates.py`（谓词改造；模块级常量）
- Modify: `src/stock_quant/research/acceptance/checks.py:250-260`（`_check_quality_report` 档位透传）
- Test: `tests/unit/test_tiered_publication_gate.py`（追加门禁用例）

**Interfaces:**
- Consumes: `TIER_BLOCKS_PUBLICATION`（Task 1）、`CODE_*`（models.py）。
- Produces: `evaluate_publication(report, *, table_tiers: Mapping[str, str] | None = None) -> GateDecision`；`GLOBAL_PROCESS_CODES`、`TABLE_LEVEL_BLOCKING_CODES`（Task 6 消费）。

- [ ] **Step 1: 写失败测试（追加到 tests/unit/test_tiered_publication_gate.py）**

```python
"""Tier-aware gate predicate (D1): global codes always block; table codes route."""

from stock_quant.data_quality.gates import (
    GLOBAL_PROCESS_CODES,
    TABLE_LEVEL_BLOCKING_CODES,
    evaluate_publication,
)
from stock_quant.data_quality.models import (
    CODE_QUARANTINE_MISSING_REASON,
    CODE_REPORT_GENERATION_FAILED,
    CODE_SCHEMA_MISMATCH,
    CODE_UNREGISTERED_TABLE,
    QualityIssue,
    QualityReport,
    Severity,
)


def _report(code: str, table: str = "income") -> QualityReport:
    return QualityReport(
        issues=(QualityIssue(Severity.ERROR, code, table=table),)
    )


def test_global_codes_block_even_on_anchored_table():
    for code in (CODE_REPORT_GENERATION_FAILED, CODE_QUARANTINE_MISSING_REASON):
        decision = evaluate_publication(
            _report(code), table_tiers={"income": "anchored"}
        )
        assert not decision.passed, code


def test_table_code_downgrades_anchored_table():
    decision = evaluate_publication(
        _report(CODE_SCHEMA_MISMATCH), table_tiers={"income": "anchored"}
    )
    assert decision.passed


def test_table_code_downgrades_research_only_table():
    decision = evaluate_publication(
        _report(CODE_SCHEMA_MISMATCH), table_tiers={"income": "research_only"}
    )
    assert decision.passed


def test_table_code_blocks_core_table():
    decision = evaluate_publication(
        _report(CODE_SCHEMA_MISMATCH), table_tiers={"income": "core"}
    )
    assert not decision.passed


def test_undeclared_table_blocks_fail_closed():
    decision = evaluate_publication(_report(CODE_SCHEMA_MISMATCH))
    assert not decision.passed
    with_tiers = evaluate_publication(
        _report(CODE_SCHEMA_MISMATCH), table_tiers={"daily_bar": "anchored"}
    )
    assert not with_tiers.passed


def test_tierless_default_matches_legacy_behavior():
    decision = evaluate_publication(_report(CODE_SCHEMA_MISMATCH))
    assert not decision.passed


def test_unregistered_table_is_global_process_code():
    assert CODE_UNREGISTERED_TABLE in GLOBAL_PROCESS_CODES


def test_blocking_codes_partition():
    from stock_quant.data_quality.gates import PUBLICATION_BLOCKING_CODES

    assert TABLE_LEVEL_BLOCKING_CODES | GLOBAL_PROCESS_CODES == (
        PUBLICATION_BLOCKING_CODES
    )
    assert not (TABLE_LEVEL_BLOCKING_CODES & GLOBAL_PROCESS_CODES)
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n sq312 pytest tests/unit/test_tiered_publication_gate.py -q`
Expected: FAIL — `TypeError: evaluate_publication() got an unexpected keyword argument 'table_tiers'`（及常量不存在）。

- [ ] **Step 3: 谓词改造 —— `src/stock_quant/data_quality/gates.py`**

import 区（:10-31）加：

```python
from stock_quant.data_contracts import TIER_BLOCKS_PUBLICATION
```

`PUBLICATION_BLOCKING_CODES` 之后加两个派生常量：

```python
#: Global process codes (spec §6 A1): these describe the build itself, not
#: any table's data, so no tier downgrades them — they always block.
GLOBAL_PROCESS_CODES = frozenset(
    {
        CODE_REPORT_GENERATION_FAILED,
        CODE_QUARANTINE_MISSING_REASON,
        CODE_UNREGISTERED_TABLE,
    }
)

#: The remaining blocking codes route by (code, table) tier (spec §6 A1).
TABLE_LEVEL_BLOCKING_CODES = PUBLICATION_BLOCKING_CODES - GLOBAL_PROCESS_CODES
```

替换 `evaluate_publication`（:72-81）为：

```python
def evaluate_publication(
    report: QualityReport,
    *,
    table_tiers: Mapping[str, str] | None = None,
) -> GateDecision:
    """Return ``PASS`` unless the report contains a publication-blocking issue.

    Global process codes always block.  Table-level codes block when the
    issue's table is core or has no declaration in ``table_tiers``
    (fail-closed, spec D1); anchored and research_only tables downgrade
    instead — the publish path turns those into coverage evidence (Task 6).
    With ``table_tiers=None`` every table is treated as undeclared, which
    preserves the legacy all-blocking behavior.
    """
    tiers = dict(table_tiers or {})
    reasons: set[str] = set()
    for item in report.issues:
        if item.code not in PUBLICATION_BLOCKING_CODES:
            continue
        if item.code in GLOBAL_PROCESS_CODES:
            reasons.add(_describe(item))
            continue
        tier = tiers.get(item.table)
        if TIER_BLOCKS_PUBLICATION.get(tier, True):
            reasons.add(_describe(item))
    if not reasons:
        return GateDecision(passed=True)
    return GateDecision(passed=False, reasons=tuple(sorted(reasons)))
```

（`Mapping` 需要 import：文件头部加 `from collections.abc import Mapping`。）

- [ ] **Step 4: 验收 `_check_quality_report` 档位化 —— checks.py:250-260**

`_check_quality_report` 的 :254 行改为：

```python
    config = load_project_config(value.project_root)
    tiers = {name: contract.tier for name, contract in config.data_contracts.items()}
    decision = evaluate_publication(report, table_tiers=tiers)
```

（`load_project_config` 需要 import：checks.py 头部加
`from stock_quant.config import load_project_config`。档位是运行时策略——
验收按当前配置重判钉住版本，与 A2「新 run 立即生效」一致。）

- [ ] **Step 5: 运行通过**

Run: `conda run -n sq312 pytest tests/unit/test_tiered_publication_gate.py tests/unit/test_quality_checks.py tests/unit/test_acceptance_checks.py -q`
Expected: PASS（`test_quality_checks.py` 中按旧签名调用的用例应为位置参数、不受影响；如有断言阻断集合的用例，确认已含 `unregistered_table`）。

- [ ] **Step 6: Commit**

```bash
git add src/stock_quant/data_quality/gates.py src/stock_quant/research/acceptance/checks.py tests/unit/test_tiered_publication_gate.py
git commit -m "feat(gates): tier-aware publication predicate with fail-closed default

Global process codes always block; table-level codes route by
(code, table) tier from sources.yml declarations; undeclared tables
block as core (ADR-010).

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6（B1）：降级记录 + 管线/发布/CLI 接线（判据 1 的发布侧）

**Files:**
- Modify: `src/stock_quant/data_quality/models.py`（`CODE_COVERAGE_DOWNGRADED`）
- Modify: `src/stock_quant/data_model/dataset.py:101-113`（`publish` 增加 `table_tiers` 透传）
- Modify: `src/stock_quant/data_pipeline.py`（`_downgrade_issues` + update() 接线 + publish 调用透传）
- Modify: `src/stock_quant/cli.py`（其 `evaluate_publication` 调用点透传档位）
- Test: `tests/integration/test_tiered_publication.py`

**Interfaces:**
- Consumes: `GLOBAL_PROCESS_CODES`/`TABLE_LEVEL_BLOCKING_CODES`/`evaluate_publication`（Task 5）、`_contract_issues`（Task 3）、`DatasetPublisher.publish`（:101）。
- Produces: `CODE_COVERAGE_DOWNGRADED = "coverage_downgraded"`；`_downgrade_issues(issues, contracts) -> list[QualityIssue]`；质量报告中的 UNTRUSTED 降级记录——Task 8 消费端预检读取的依据。

- [ ] **Step 1: 写失败测试**

```python
# tests/integration/test_tiered_publication.py
"""Criterion 1 (publish side): a downgraded anchored table publishes, a core
one blocks; the published quality report carries the UNTRUSTED record."""

from __future__ import annotations

import json

import pytest

from stock_quant.data_model.dataset import DatasetPublisher, PublicationBlocked
from stock_quant.data_pipeline import _downgrade_issues
from stock_quant.data_quality.gates import evaluate_publication
from stock_quant.data_quality.models import (
    CODE_COVERAGE_DOWNGRADED,
    CODE_SCHEMA_MISMATCH,
    QualityIssue,
    QualityReport,
    Severity,
)


def _report() -> QualityReport:
    return QualityReport(
        issues=(
            QualityIssue(
                Severity.ERROR, CODE_SCHEMA_MISMATCH, table="corporate_action"
            ),
        )
    )


def _downgrade_record(issues):
    assert len(issues) == 1
    issue = issues[0]
    assert issue.code == CODE_COVERAGE_DOWNGRADED
    assert issue.table == "corporate_action"
    assert issue.severity is Severity.WARNING
    details = issue.details
    assert details["status"] == "UNTRUSTED"
    assert details["reason_codes"] == [CODE_SCHEMA_MISMATCH]
    assert details["window_start"] is None and details["window_end"] is None
    assert details["symbols"] is None  # table-level issue: whole table


def test_downgrade_emitted_for_anchored_table():
    contracts = {
        "corporate_action": type("C", (), {"tier": "anchored"}),  # tier only
    }
    _downgrade_record(_downgrade_issues(_report().issues, contracts))


def test_no_downgrade_for_core_table():
    contracts = {"corporate_action": type("C", (), {"tier": "core"})}
    assert _downgrade_issues(_report().issues, contracts) == []


def test_publish_downgraded_anchored_table_and_block_core(tmp_path):
    from tests.unit.test_quality_checks import (  # reuse existing frame builders
        make_daily_frame,  # noqa: F401 - adjust to the real helper names
    )

    # Build two minimal valid frames via the helpers used by the existing
    # publication tests (read tests/integration/test_dataset_publish.py and
    # tests/unit/test_quality_checks.py for the real builder names; the
    # names below must be matched to them before running).
    daily = _make_daily_frame()
    actions = _make_action_frame()
    tables = {"daily_bar": daily, "corporate_action": actions}

    tiers = {"daily_bar": "core", "corporate_action": "anchored"}
    publisher = DatasetPublisher(tmp_path)
    dataset_ref = publisher.publish(
        tables,
        _report_with_downgrade(_report(), tiers),
        build_config={"requested_start_date": "2024-01-02",
                      "resolved_end_date": "2024-01-31",
                      "full_history_acceptance_start": "2024-01-02"},
        table_tiers=tiers,
    )
    stored = json.loads(
        (dataset_ref.path / "quality_report.json").read_text(encoding="utf-8")
    )
    codes = {item["code"] for item in stored["issues"]}
    assert CODE_COVERAGE_DOWNGRADED in codes

    blocked = DatasetPublisher(tmp_path / "blocked")
    with pytest.raises(PublicationBlocked):
        blocked.publish(
            tables,
            _report_with_downgrade(_report(), {"daily_bar": "core",
                                               "corporate_action": "core"}),
            build_config={"requested_start_date": "2024-01-02",
                          "resolved_end_date": "2024-01-31"},
            table_tiers={"daily_bar": "core", "corporate_action": "core"},
        )


def _report_with_downgrade(report, contracts):
    issues = list(report.issues) + _downgrade_issues(report.issues, contracts)
    return QualityReport(issues=tuple(issues))
```

说明（实现者必读）：本测试的帧构造器不发明——先读 `tests/integration/test_dataset_publish.py` 与 `tests/unit/test_quality_checks.py`，取它们现成的 daily_bar / corporate_action 合法帧 builder（或直接从其 fixture 复制最小帧构造），替换 `_make_daily_frame`/`_make_action_frame` 占位调用；`build_config` 的三个窗口键按 dataset.py `_normalize_build_config` 的实际要求补齐。

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n sq312 pytest tests/integration/test_tiered_publication.py -q`
Expected: FAIL — `CODE_COVERAGE_DOWNGRADED` / `_downgrade_issues` / `table_tiers` 不存在。

- [ ] **Step 3: `CODE_COVERAGE_DOWNGRADED` + `_downgrade_issues`**

models.py（:74 块后）追加：

```python
# Downgrade evidence (spec D1): a non-core table carrying a table-level
# blocking code publishes with this WARNING record instead of blocking.
# It is deliberately NOT in PUBLICATION_BLOCKING_CODES.
CODE_COVERAGE_DOWNGRADED = "coverage_downgraded"
```

data_pipeline.py 模块级新增（`_contract_issues` 旁）：

```python
def _downgrade_issues(
    issues: Sequence[QualityIssue], contracts: Mapping[str, object]
) -> list[QualityIssue]:
    """UNTRUSTED coverage records for non-core tables with blocking codes.

    Per spec D1 the record follows the corporate_action_coverage shape:
    per-symbol-window coverage evidence.  A table-level issue without a
    symbol covers the whole table (``symbols=None``, window ``None``);
    symbol-scoped issues would carry the same details the source issue has.
    """
    records: list[QualityIssue] = []
    for item in issues:
        if item.code not in TABLE_LEVEL_BLOCKING_CODES:
            continue
        contract = contracts.get(item.table)
        if contract is None or getattr(contract, "tier", None) is None:
            continue
        if getattr(contract, "tier") in TIER_BLOCKS_PUBLICATION:
            if TIER_BLOCKS_PUBLICATION[getattr(contract, "tier")]:
                continue  # core: blocks instead, never a downgrade record
        records.append(
            _issue(
                Severity.WARNING,
                CODE_COVERAGE_DOWNGRADED,
                table=item.table,
                symbol=item.symbol,
                details={
                    "status": "UNTRUSTED",
                    "reason_codes": [item.code],
                    "symbols": None if item.symbol is None else [item.symbol],
                    "window_start": (
                        None
                        if item.trade_date is None
                        else item.trade_date.isoformat()
                    ),
                    "window_end": (
                        None
                        if item.trade_date is None
                        else item.trade_date.isoformat()
                    ),
                },
            )
        )
    return records
```

（`Sequence`/`Mapping` import：data_pipeline.py 顶部按需补 `from collections.abc import Mapping, Sequence`；`TIER_BLOCKS_PUBLICATION` 从 `stock_quant.data_contracts` import。）

update() 接线：在 report 构造与门控之后、`tables` 字典之后（Task 3 已把 tables 上移）、publish 之前：

```python
        downgrades = _downgrade_issues(
            report.issues, self._project_config.data_contracts
        )
        if downgrades:
            issues.extend(downgrades)
            report = QualityReport(issues=tuple(issues))
```

且 publish 调用（:897 起）增加：

```python
            dataset_ref = DatasetPublisher(self._project_root).publish(
                tables,
                report,
                build_config=dataset_build_config(...),  # 原参数不动
                table_tiers={
                    name: contract.tier
                    for name, contract in self._project_config.data_contracts.items()
                },
            )
```

（门控判定用的 `decision = evaluate_publication(report)` 在 :856 也要改为
`evaluate_publication(report, table_tiers=tiers)`，其中
`tiers = {name: c.tier for name, c in self._project_config.data_contracts.items()}`
在门控前算一次复用。）

- [ ] **Step 4: dataset.py publish 透传**

`publish` 签名（:101-107）改为：

```python
    def publish(
        self,
        tables: Mapping[str, pd.DataFrame],
        report: QualityReport,
        *,
        build_config: Mapping[str, Any] | None = None,
        table_tiers: Mapping[str, str] | None = None,
    ) -> DatasetRef:
        """Gate, stage and atomically publish one immutable dataset version."""
        decision = evaluate_publication(report, table_tiers=table_tiers)
```

（`evaluate_publication` 已 import；其余调用方（bootstrap、collect 脚本）不传该参数 → 全 core 语义不变。）

- [ ] **Step 5: cli.py 调用点透传**

`cd src/stock_quant && grep -n "evaluate_publication" cli.py` 定位调用点；在该处用与 Step 3 相同的 `tiers = {name: c.tier for name, c in load_project_config(root).data_contracts.items()}` 透传。若该调用点所在命令已持有 `ProjectConfig`，直接复用。

- [ ] **Step 6: 运行通过**

Run: `conda run -n sq312 pytest tests/integration/test_tiered_publication.py tests/unit/test_tiered_publication_gate.py tests/integration/test_dataset_publish.py tests/integration/test_data_pipeline.py -q`
Expected: PASS（既有发布/管线集成测试不带档位参数，行为不变）。

- [ ] **Step 7: Commit**

```bash
git add src/stock_quant/data_quality/models.py src/stock_quant/data_pipeline.py src/stock_quant/data_model/dataset.py src/stock_quant/cli.py tests/integration/test_tiered_publication.py
git commit -m "feat(gates): publish downgraded anchored tables with UNTRUSTED records

Non-core tables carrying table-level blocking codes publish with a
coverage_downgraded WARNING in the quality report instead of blocking;
the publish path, data_update and CLI pass tier declarations through
(ADR-010, criterion 1 publish side).

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 7（B1）：`Factor.inputs` 表级声明（A2/F4）

**Files:**
- Modify: `src/stock_quant/factors/base.py:73-89`（Factor 协议）
- Modify: `src/stock_quant/factors/momentum.py:74-83`（Momentum60 类属性区）
- Test: `tests/unit/test_factor_inputs.py`

**Interfaces:**
- Consumes: Factor Protocol（base.py:73）。
- Produces: `Factor.inputs: tuple[str, ...]`——Task 8 预检的因子→表解析依据。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_factor_inputs.py
"""Every factor declares its input tables (spec A2/F4)."""

from __future__ import annotations

from stock_quant.factors.base import Factor
from stock_quant.factors.momentum import Momentum60


def test_protocol_requires_inputs_declaration():
    annotations = Factor.__annotations__
    assert "inputs" in annotations
    assert annotations["inputs"] == tuple[str, ...]


def test_momentum60_declares_adjusted_bar():
    assert Momentum60.inputs == ("adjusted_bar",)
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n sq312 pytest tests/unit/test_factor_inputs.py -q`
Expected: FAIL — 协议无 `inputs`；`Momentum60` 无该属性。

- [ ] **Step 3: 协议与实现**

`factors/base.py` Factor 协议（:73-89 的元数据属性区）加：

```python
    #: Canonical table names this factor reads (spec A2).  Every factor must
    #: declare its inputs; the research preflight rejects factors without a
    #: declaration at spec load (fail-closed).
    inputs: tuple[str, ...]
```

`factors/momentum.py` Momentum60（:77-83 属性区，`required_fields` 之后）加：

```python
    inputs = ("adjusted_bar",)
```

（动量 v2 的研究输入只来自 `adjusted_bar`——RUNBOOK 既有事实；`factor_input()` 的动量序列由数据集层解析，其唯一底层表是 adjusted_bar。）

然后 `grep -n "required_fields" src/stock_quant/factors/*.py` 找出所有 Factor 实现类；除 Momentum60 外若还有实现（按 `_factor_provider_default` 至少只有 Momentum60，但逐一确认），每个都按其真实输入表补 `inputs`——值从该因子的 `compute` 实际读取的 `context.dataset` 表名确定，不得猜。

- [ ] **Step 4: 运行通过**

Run: `conda run -n sq312 pytest tests/unit/test_factor_inputs.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/stock_quant/factors/base.py src/stock_quant/factors/momentum.py tests/unit/test_factor_inputs.py
git commit -m "feat(factors): declare per-factor input tables on the Factor protocol

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 8（B1）：research 预检表档位（判据 1/2 的消费侧）

**Files:**
- Modify: `src/stock_quant/research/runner.py`（新增 `_STAGE_TABLE_TIERS`、`TableTierPreflightFailed`、`factor_input_tables`、`table_tier_violations`、`_preflight_table_tiers`、`_write_table_tier_preflight`；run() :658-701 之间接线）
- Test: `tests/unit/test_table_tier_preflight.py`
- Test: `tests/integration/test_table_tier_preflight.py`

**Interfaces:**
- Consumes: `Factor.inputs`（Task 7）、`ProjectConfig.data_contracts`（Task 1）、`CODE_COVERAGE_DOWNGRADED` 记录（Task 6）、runner 既有 `_fail_universe_preflight`/`ResearchRunFailed`/`redact_text` 模式（runner.py:657-668、:930）。
- Produces: `factor_input_tables(factors) -> dict[str, tuple[str, ...]]`；`table_tier_violations(contracts, input_tables, downgrade_records, mode) -> list[str]`（Task 14 扩展 NOT_FETCHED）；runner 方法 `_preflight_table_tiers(spec, dataset_version, mode)`。

- [ ] **Step 1: 写失败测试（单元层，纯函数全覆盖）**

```python
# tests/unit/test_table_tier_preflight.py
"""Tier violations over declared input tables (spec A2 / D1 consumer gate)."""

from __future__ import annotations

import pytest

from stock_quant.data_contracts import parse_data_contracts
from stock_quant.data_quality.models import CODE_SCHEMA_MISMATCH
from stock_quant.research.runner import (
    factor_input_tables,
    table_tier_violations,
)


class _Factor:
    def __init__(self, inputs):
        self.inputs = tuple(inputs)


class _NoInputsFactor:
    pass


CONTRACTS = parse_data_contracts(
    [
        {
            "table": "daily_bar",
            "tier": "core",
            "primary_transport": "tushare:relay",
            "anchors": [],
            "conflict": "downgrade",
            "pit": None,
            "coverage_shape": "none",
            "incremental": "last_covered_plus_1",
        },
        {
            "table": "income",
            "tier": "anchored",
            "primary_transport": "tushare:relay",
            "anchors": ["akshare_cninfo_announcement"],
            "conflict": "downgrade",
            "pit": None,
            "coverage_shape": "per_symbol_window",
            "incremental": "disclosure_calendar",
        },
        {
            "table": "industry_classify",
            "tier": "research_only",
            "primary_transport": "tushare:relay",
            "anchors": [],
            "conflict": "block",
            "pit": None,
            "coverage_shape": "none",
            "incremental": "change_driven_full",
        },
    ]
)


def test_input_tables_aggregate_across_factors():
    tables = factor_input_tables({"a": _Factor(["daily_bar"]), "b": _Factor(["income"])})
    assert tables == {"a": ("daily_bar",), "b": ("income",)}


def test_factor_without_inputs_fails_closed():
    with pytest.raises(ValueError, match="inputs"):
        factor_input_tables({"bad": _NoInputsFactor()})


def test_core_inputs_pass_in_research_mode():
    assert table_tier_violations(CONTRACTS, ("daily_bar",), (), "research") == []


def test_research_only_rejected_in_research_mode():
    codes = table_tier_violations(
        CONTRACTS, ("industry_classify",), (), "research"
    )
    assert "table_tier_research_only" in codes


def test_research_only_exempt_in_engineering_mode():
    codes = table_tier_violations(
        CONTRACTS, ("industry_classify",), (), "engineering"
    )
    assert "table_tier_research_only" not in codes


def test_untrusted_anchored_rejected_in_research_mode():
    downgrades = [
        {
            "table": "income",
            "status": "UNTRUSTED",
            "reason_codes": [CODE_SCHEMA_MISMATCH],
            "symbols": None,
            "window_start": None,
            "window_end": None,
        }
    ]
    codes = table_tier_violations(CONTRACTS, ("income",), downgrades, "research")
    assert "table_tier_untrusted" in codes


def test_untrusted_anchored_exempt_in_engineering_mode():
    downgrades = [
        {
            "table": "income",
            "status": "UNTRUSTED",
            "reason_codes": [CODE_SCHEMA_MISMATCH],
            "symbols": None,
            "window_start": None,
            "window_end": None,
        }
    ]
    codes = table_tier_violations(CONTRACTS, ("income",), downgrades, "engineering")
    assert "table_tier_untrusted" not in codes


def test_undeclared_input_table_fails_closed():
    codes = table_tier_violations(CONTRACTS, ("novel_table",), (), "research")
    assert "table_tier_undeclared" in codes


def test_clean_anchored_passes_in_research_mode():
    assert table_tier_violations(CONTRACTS, ("income",), (), "research") == []
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n sq312 pytest tests/unit/test_table_tier_preflight.py -q`
Expected: FAIL — `ImportError`（runner 无这些符号）。

- [ ] **Step 3: 纯函数实现（runner.py 模块级，`_STAGE_UNIVERSE_ACCEPTANCE` 附近）**

```python
#: The pre-factor stage label a table-tier preflight failure is recorded
#: under (spec A2): tier routing happens before identity and factor work.
_STAGE_TABLE_TIERS = "table_tiers"


class TableTierPreflightFailed(RuntimeError):
    """The pinned dataset/current tier policy rejected the run's input tables.

    Carries only stable error codes so the redacted preflight manifest can
    name the rejection without leaking data (mirrors
    :class:`UniversePreflightFailed`).
    """

    def __init__(self, message: str, *, error_codes: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.error_codes = tuple(sorted(set(error_codes)))


def factor_input_tables(factors: Mapping[str, object]) -> dict[str, tuple[str, ...]]:
    """Map factor name to its declared ``inputs``; missing declarations fail."""
    resolved: dict[str, tuple[str, ...]] = {}
    for name, factor in sorted(factors.items()):
        inputs = getattr(factor, "inputs", None)
        if inputs is None:
            raise ValueError(
                f"factor {name!r} declares no inputs (spec A2): every factor "
                "must name the canonical tables it reads"
            )
        resolved[name] = tuple(inputs)
    return resolved


def table_tier_violations(
    contracts: Mapping[str, object],
    input_tables: Sequence[str],
    downgrade_records: Sequence[Mapping[str, object]],
    mode: str,
) -> list[str]:
    """Tier violations for one run's input tables (spec A2 consumer gate).

    ``downgrade_records`` are the pinned version's ``coverage_downgraded``
    quality-report details.  RESEARCH mode fails on research_only inputs and
    untrusted anchored inputs; ENGINEERING (diagnostic-only) is exempt and
    must label the report RESEARCH-ONLY (the preflight manifest carries the
    label).  Undeclared tables fail closed.  Tier is runtime policy read
    from the current sources.yml, never from the frozen spec.
    """
    engineering = mode in ("engineering", "ENGINEERING")
    untrusted_tables = {
        str(record["table"])
        for record in downgrade_records
        if record.get("status") == "UNTRUSTED"
    }
    codes: list[str] = []
    for table in sorted(set(input_tables)):
        contract = contracts.get(table)
        if contract is None:
            codes.append("table_tier_undeclared")
            continue
        tier = getattr(contract, "tier", None)
        if tier == TIER_RESEARCH_ONLY and not engineering:
            codes.append("table_tier_research_only")
        elif tier == TIER_ANCHORED and table in untrusted_tables and not engineering:
            codes.append("table_tier_untrusted")
    return codes
```

（runner.py 头部 import：`TIER_ANCHORED`、`TIER_RESEARCH_ONLY` 从 `stock_quant.data_contracts`；`json`/`Path` 已有。）

- [ ] **Step 4: runner 接线 —— `_preflight_table_tiers` + `_write_table_tier_preflight`**

先读 `runner.py` 的 `_fail_universe_preflight`（:930 起）与 `run()`（:629-755，本计划已引用的区间），照其审计落盘模式新增：

```python
    def _preflight_table_tiers(
        self,
        spec: ExperimentSpec,
        dataset_version: str,
        mode: DataTrustMode,
    ) -> dict[str, object]:
        """Route the run's factor input tables through the current tier policy.

        Returns the preflight summary (always written to the run directory):
        declared inputs, research-only usage and engineering exemption label.
        Raises :class:`TableTierPreflightFailed` in RESEARCH mode when any
        input table is research_only or untrusted-anchored (spec A2 / D1).
        """
        contracts = load_project_config(self._project_root).data_contracts
        inputs = factor_input_tables(
            {
                name: self._factor_provider[name]
                for name in spec.factor_versions
            }
        )
        input_tables = sorted({table for tables in inputs.values() for table in tables})
        manifest = json.loads(
            (
                Path(self._project_root)
                / "data"
                / "standardized"
                / dataset_version
                / "dataset_manifest.json"
            ).read_text(encoding="utf-8")
        )
        version_dir = manifest.get("tables", {})
        # The pinned quality report carries the coverage_downgraded records
        # (Task 6); parse only those details, never the report prose.
        quality_path = (
            Path(self._project_root)
            / "data"
            / "standardized"
            / dataset_version
            / "quality_report.json"
        )
        downgrade_records: list[dict[str, object]] = []
        if quality_path.is_file():
            report = json.loads(quality_path.read_text(encoding="utf-8"))
            downgrade_records = [
                item.get("details", {})
                for item in report.get("issues", [])
                if item.get("code") == CODE_COVERAGE_DOWNGRADED
            ]
        violations = table_tier_violations(
            contracts, input_tables, downgrade_records, mode.value
        )
        engineering_exempt = (
            mode is DataTrustMode.ENGINEERING
            and any(
                violations is not None and code in violations
                for code in ("table_tier_research_only", "table_tier_untrusted")
            )
        )
        summary: dict[str, object] = {
            "dataset_version": dataset_version,
            "mode": mode.value,
            "input_tables": input_tables,
            "research_only_used": any(
                getattr(contracts.get(table), "tier", None) == TIER_RESEARCH_ONLY
                for table in input_tables
            ),
            "engineering_exempt": engineering_exempt,
            "label": "RESEARCH-ONLY" if engineering_exempt else None,
            "violations": violations,
        }
        if violations:
            self._write_table_tier_preflight(summary, failed=True)
            raise TableTierPreflightFailed(
                "table-tier preflight rejected input tables: "
                + ", ".join(violations),
                error_codes=violations,
            )
        self._write_table_tier_preflight(summary, failed=False)
        return summary
```

（`self._factor_provider` 与 `self._project_root` 的属性名以 runner.py 实际为准——`_factor_provider_default` 的消费处即其名；`DataTrustMode` 的 `mode.value` 若为 str 枚举直接用。）

`_write_table_tier_preflight`（照 `_fail_universe_preflight` 的 run_preflight 目录模式）：

```python
    def _write_table_tier_preflight(
        self, summary: Mapping[str, object], *, failed: bool
    ) -> None:
        """Persist the (redacted) tier-preflight manifest for audit."""
        payload = dict(summary)
        payload["failed"] = failed
        directory = (
            Path(self._project_root)
            / "data"
            / "runs"
            / (
                f"run_preflight_{self._run_id[-16:]}"
                if failed
                else self._run_id
            )
        )
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "table_tier_preflight.json"
        if path.exists() and json.loads(path.read_text(encoding="utf-8")) != payload:
            raise ValueError(
                f"table_tier_preflight.json already exists with different "
                f"content: {path}"
            )
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
```

（`_run_id` 属性名以 runner.py 实际为准；若失败目录命名与 `_fail_universe_preflight` 的实现不同，以它的实现为准对齐。）

run() 接线：在 :668（universe preflight 的 `except` 块结束）之后、:669 `universe_version = ...` 之前插入：

```python
        # The table-tier preflight runs after universe acceptance and before
        # experiment identity: RESEARCH runs referencing research_only or
        # untrusted-anchored tables fail here with a redacted preflight
        # manifest; ENGINEERING runs are exempt and labeled RESEARCH-ONLY
        # (spec A2 / D1 consumer gate).
        try:
            self._table_tier_preflight = self._preflight_table_tiers(
                spec, dataset_version, mode
            )
        except TableTierPreflightFailed as error:
            raise ResearchRunFailed(
                f"research run failed at stage {_STAGE_TABLE_TIERS}: "
                f"{redact_text(error, self._secrets)}",
                run_id=self._run_id,
                failed_stage=_STAGE_TABLE_TIERS,
                retriable=False,
            ) from error
```

（`_secrets` 与 `redact_text` 在 :664 已有用法，直接复用。）

- [ ] **Step 5: 运行单元测试通过**

Run: `conda run -n sq312 pytest tests/unit/test_table_tier_preflight.py -q`
Expected: PASS。

- [ ] **Step 6: 集成测试（判据 1/2 的 runner 层）**

```python
# tests/integration/test_table_tier_preflight.py
"""Criterion 1/2 (consumer side): RESEARCH runs reject research_only and
untrusted-anchored inputs; ENGINEERING runs proceed with the exemption."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stock_quant.research.models import ResearchRunFailed
from stock_quant.research.runner import ResearchRunner


class _ResearchOnlyFactor:
    name = "industry_probe"
    version = "1.0.0"
    lookback = 30
    frequency = "weekly"
    required_fields = frozenset({"industry_code"})
    inputs = ("industry_classify",)

    def compute(self, context):  # never reached: preflight fails first
        raise NotImplementedError


def _spec_file(env, factor_name: str, factor_version: str) -> Path:
    # Model this YAML on the spec file behind test_research_runner.py's
    # `walk_forward_spec` fixture (read that fixture to copy the required
    # fields); only factor_versions and dataset_version need to change.
    payload = {
        "hypothesis": "tier preflight probe",
        "factor_versions": {factor_name: factor_version},
        "dataset_version": env.dataset_version,
        "universe_version": env.universe_version,
        "universe_definition": None,
        "data_acceptance_id": None,
        "date_range": {"start": "2024-01-02", "end": "2024-06-28"},
        "execution_pipeline": "engineering_single_window",
        "train_validation_holdout_policy": "not_applicable_engineering_mvp",
        "preprocessing": {},
        "portfolio_rule": {},
        "cost_scenarios": ["zero_cost"],
        "random_seed": 1,
    }
    path = env.config_root / "specs" / "tier_probe.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")  # 或按 spec 加载器的 YAML 形状
    return path
```

（说明：本文件不发明 spec 形状——先读 `tests/integration/test_research_runner.py` 的 `walk_forward_spec` fixture 与 conftest 的 `env` fixture（`env.root`/`env.config_root`/`env.dataset_version` 等属性名以 conftest 实际为准），把上面的最小 spec 改成同形状；若 spec 是 YAML 而非 JSON，用 `safe_yaml` 写回。ENV 里 `configs/sources.yml` 需要追加一段 `data_contracts` 声明 `industry_classify: research_only`——在测试的 setup 里读该文件、**测试内临时追加**（tmp env 树），不改仓库文件。）

```python
def test_research_run_rejects_research_only_input(env):
    _append_contract(env, table="industry_classify", tier="research_only")
    runner = ResearchRunner(
        env.root,
        config_root=env.config_root,
        factor_provider={_ResearchOnlyFactor.name: _ResearchOnlyFactor()},
    )
    with pytest.raises(ResearchRunFailed, match="table_tiers"):
        runner.run(_spec_file(env, _ResearchOnlyFactor.name, "1.0.0"),
                   trust_mode="engineering")
    preflight = sorted(
        (env.root / "data" / "runs").glob("run_preflight_*/table_tier_preflight.json")
    )[-1]
    payload = json.loads(preflight.read_text(encoding="utf-8"))
    assert "table_tier_research_only" in payload["violations"]


def test_engineering_run_accepts_research_only_with_label(env):
    _append_contract(env, table="industry_classify", tier="research_only")
    runner = ResearchRunner(
        env.root,
        config_root=env.config_root,
        factor_provider={_ResearchOnlyFactor.name: _ResearchOnlyFactor()},
    )
    published = runner.run(
        _spec_file(env, _ResearchOnlyFactor.name, "1.0.0"),
        trust_mode="engineering",
    )
    preflight = json.loads(
        (published.path / "table_tier_preflight.json").read_text(encoding="utf-8")
    ) if (published.path / "table_tier_preflight.json").is_file() else None
    assert preflight is None or preflight["engineering_exempt"] is True
```

（第一条用 `trust_mode="engineering"` 但输入是 research_only：ENGINEERING 豁免 research_only——因此第一条要测 RESEARCH 拒绝，需 `trust_mode="research"` 并给 spec 配 data_acceptance；若 env 无现成 acceptance，把第一条改为 ENGINEERING + research_only + 断言 preflight 失败……注意：**ENGINEERING 对 research_only 是豁免的**，所以判据 2 的「正式 research run 拒绝」必须用 RESEARCH 模式测。若 conftest env 提供 acceptance 记录（test_research_runner.py 的 RESEARCH 用例已跑通，说明有），照其 spec 配 `data_acceptance_id`；若无，此测试以 `table_tier_violations` 的单元用例 + 第一条的失败清单断言为准，并在提交信息里注明。运行前先读 test_research_runner.py 确认。）

- [ ] **Step 7: 运行集成测试通过**

Run: `conda run -n sq312 pytest tests/integration/test_table_tier_preflight.py tests/integration/test_research_runner.py tests/integration/test_factor_no_lookahead.py -q`
Expected: PASS（既有 runner 用例的输入全是 core 表 → 预检空过，行为不变）。

- [ ] **Step 8: Commit**

```bash
git add src/stock_quant/research/runner.py tests/unit/test_table_tier_preflight.py tests/integration/test_table_tier_preflight.py
git commit -m "feat(research): preflight factor input tables against the tier policy

RESEARCH runs reject research_only and untrusted-anchored input tables
with a redacted table_tier preflight manifest; ENGINEERING runs are
exempt and labeled RESEARCH-ONLY (spec A2, criteria 1-2).

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 9（B1）：`_window` 换锚 + RUNBOOK :80-84 退役（判据 7/8）

**Files:**
- Modify: `src/stock_quant/research/acceptance/checks.py:277-285`（docstring）、`checks.py:669-675`（`_window`）
- Modify: `src/stock_quant/research/acceptance/evidence.py:116-132`（`evidence_window` 错误区分 + docstring）
- Modify: `RUNBOOK.md:80-84`（退役替换）
- Test: `tests/integration/test_window_anchor_regression.py`（含 `_window` 纯函数单元用例）

**Interfaces:**
- Consumes: `run_automated_checks` 捕获表（checks.py:208-214）、`EvidenceBuildError`（evidence.py）。
- Produces: `FullHistoryAcceptanceStartMissing(ValueError)`；换锚后的 `_window`——Task 13 验收 `_check_table_fetch_coverage` 与全部既有 `_window` 消费方（`_check_date_window`、`_check_corporate_actions`、`evidence_window`）自动共用新锚。

- [ ] **Step 1: 写失败测试**

```python
# tests/integration/test_window_anchor_regression.py
"""Criterion 7/8: the review window anchors to full_history_acceptance_start."""

from __future__ import annotations

import pytest

from stock_quant.research.acceptance.checks import (
    FullHistoryAcceptanceStartMissing,
    _window,
)
from stock_quant.research.acceptance.evidence import (
    EvidenceBuildError,
    evidence_window,
)


def test_anchor_prefers_acceptance_start():
    build = {
        "requested_start_date": "2015-01-01",
        "resolved_end_date": "2026-08-28",
        "full_history_acceptance_start": "2015-01-05",
    }
    start, end = _window(build)
    assert (start, end) == (date(2015, 1, 5), date(2026, 8, 28))
```

（`from datetime import date` 加在头部。）

```python
def test_legacy_requested_fallback_unchanged():
    build = {
        "requested_start_date": "2015-01-05",
        "resolved_end_date": "2026-08-28",
    }
    start, end = _window(build)
    assert (start, end) == (date(2015, 1, 5), date(2026, 8, 28))


def test_missing_both_raises_dedicated_error():
    with pytest.raises(FullHistoryAcceptanceStartMissing):
        _window({"resolved_end_date": "2026-08-28"})


def test_dedicated_error_is_caught_as_fail_not_crash():
    # run_automated_checks catches ValueError subclasses into FAIL results
    # (checks.py:208-214) — a non-ValueError would crash the whole run.
    assert issubclass(FullHistoryAcceptanceStartMissing, ValueError)


def test_evidence_window_distinguishes_anchor_missing():
    with pytest.raises(EvidenceBuildError) as captured:
        evidence_window(
            {"build_config": {"resolved_end_date": "2026-08-28"}}
        )
    assert str(captured.value) == "full_history_acceptance_start_missing"


def test_evidence_window_keeps_window_missing_for_malformed():
    with pytest.raises(EvidenceBuildError) as captured:
        evidence_window(
            {"build_config": {"full_history_acceptance_start": "x",
                              "resolved_end_date": None}}
        )
    assert str(captured.value) == "window_missing"
```

再按 `tests/integration/test_acceptance_checks.py` 的既有 fixture 模式补一条集成回归（先读该文件与 tests/integration/conftest.py 的合成数据集 fixture，取能构造「requested=2015-01-01 < 首个开市日 2015-01-05」合成 manifest 的最小 fixture，复制其构造方式；数据集其余内容必须让 `_check_date_window` 的其它子检查通过）：

```python
def test_requested_before_first_open_day_now_accepts(env):
    # Synthesize a dataset whose build_config carries
    # requested_start_date=2015-01-01, full_history_acceptance_start=2015-01-05,
    # a calendar starting 2015-01-05 and complete bars over the window.
    # Model the synthesis on tests/integration/test_acceptance_checks.py.
    results = run_automated_checks(acceptance_input(env, build_config={...}))
    by_code = {result.code: result.status for result in results}
    assert by_code["date_window_completeness"] is CheckStatus.PASS
```

（`acceptance_input`/`env` 以其文件实际 fixture 名为准；若该文件没有可直接复用的合成路径，用其 `tmp_path` 项目根 + `DatasetPublisher` 发布合成数据集的方式构造——tests/integration/test_dataset_publish.py 有先例。）

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n sq312 pytest tests/integration/test_window_anchor_regression.py -q`
Expected: FAIL — `_window` 旧实现取 `requested_start_date`（第一条断言 2015-01-05 实际得 2015-01-01）；`FullHistoryAcceptanceStartMissing` 不存在。

- [ ] **Step 3: 换锚实现**

`checks.py` 模块级（`_window` 前）加：

```python
class FullHistoryAcceptanceStartMissing(ValueError):
    """The build names neither acceptance anchor nor legacy requested start.

    A ``ValueError`` subclass on purpose: ``run_automated_checks`` catches
    it into a FAIL result (never a crash), while its dedicated type lets
    ``evidence_window`` distinguish it from a malformed window (spec §0-12).
    """
```

替换 `_window`（:669-675）为：

```python
def _window(build: Mapping[str, Any]) -> tuple[date, date]:
    """The review window: acceptance obligation through resolved end (ADR-011).

    Anchored to ``full_history_acceptance_start`` alone (never min'd with
    the requested start — a min'd window before the first open day fails by
    construction).  Falls back to ``requested_start_date`` for legacy and
    non-data_update origins; a build with neither is un-reviewable by design
    (bootstrap) and raises :class:`FullHistoryAcceptanceStartMissing`.
    """
    start = build.get("full_history_acceptance_start")
    if not isinstance(start, str):
        start = build.get("requested_start_date")
    end = build.get("resolved_end_date")
    if not isinstance(end, str):
        raise ValueError("dataset build window is missing")
    if not isinstance(start, str):
        raise FullHistoryAcceptanceStartMissing(
            "build_config carries neither full_history_acceptance_start nor "
            "requested_start_date"
        )
    return date.fromisoformat(start), date.fromisoformat(end)
```

同步更新 `_check_date_window` 的 docstring（:277-285）：把「The window spans ``requested_start_date``…」改为「The window spans the acceptance anchor（`full_history_acceptance_start`，ADR-011；legacy 回退 `requested_start_date`）through ``resolved_end_date``…」。

`evidence.py`：

- import（其现有 `from stock_quant.research.acceptance.checks import _window` 行处）改为同时导入：

```python
from stock_quant.research.acceptance.checks import (
    FullHistoryAcceptanceStartMissing,
    _window,
)
```

- `evidence_window`（:116-132）替换为：

```python
def evidence_window(manifest: Mapping[str, object]) -> tuple[date, date]:
    """The window every mechanisable evidence file is computed over.

    Deliberately the *same* window the automated ``date_window_completeness``
    check uses — the acceptance anchor (``full_history_acceptance_start``,
    ADR-011; legacy fallback ``requested_start_date``) through resolved end
    (``checks._window``).  It is not ``build_config.effective_start_date``:
    the evidence a human signs off on and the automated verdict must describe
    one window, and a request that started before the data does has to show
    up as absent bars rather than silently shrink what was reviewed.
    """
    build = manifest.get("build_config")
    if not isinstance(build, dict):
        raise EvidenceBuildError("window_missing")
    try:
        return _window(build)
    except FullHistoryAcceptanceStartMissing as error:
        raise EvidenceBuildError("full_history_acceptance_start_missing") from error
    except (TypeError, ValueError) as error:
        raise EvidenceBuildError("window_missing") from error
```

- [ ] **Step 4: RUNBOOK :80-84 退役**

Read `RUNBOOK.md:75-90` 确认行号（本计划引用自 2026-09-19 快照；若行号漂移按内容定位）。把「`--start` 必须落在开市日上（2015-01-01 → window_not_calendar_complete FAIL）」的条目替换为：

```markdown
- `--start` 只影响取数窗口，不再影响验收窗口：验收窗口锚定
  `full_history_acceptance_start`（ADR-011），早于首个开市日（如
  `--start 2015-01-01`）发布的版本可被验收。B2 起，显式 `--start` 偏离
  某表契约取数窗时该表跳过取数并记 NOT_FETCHED（见阶段 4）。
```

- [ ] **Step 5: 运行通过**

Run: `conda run -n sq312 pytest tests/integration/test_window_anchor_regression.py tests/unit/test_acceptance_evidence.py tests/integration/test_acceptance_checks.py -q`
Expected: PASS（既有 acceptance 测试的合成 manifest 若只带 requested 键，走 legacy 回退行为不变；若其断言依赖旧 docstring 文字，按断言失败信息仅更新文字断言）。

- [ ] **Step 6: Commit**

```bash
git add src/stock_quant/research/acceptance/checks.py src/stock_quant/research/acceptance/evidence.py RUNBOOK.md tests/integration/test_window_anchor_regression.py
git commit -m "feat(acceptance): anchor the review window to the acceptance obligation

_window prefers full_history_acceptance_start with a legacy
requested_start_date fallback and a dedicated
FullHistoryAcceptanceStartMissing ValueError; evidence_window
distinguishes the anchor-missing error; RUNBOOK's open-day trap
entry is retired (ADR-011, criteria 7-8).

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 10（B1）：档位字面量静态扫描（判据 3）

**Files:**
- Test: `tests/unit/test_tier_literals.py`

**Interfaces:**
- Consumes: 无。Produces: 判据 3 的负断言——`src/` 内档位字面量只在 `data_contracts.py`。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_tier_literals.py
"""Criterion 3: tier literals live only in data_contracts.py (static scan).

Tier values enter the runtime exclusively through the sources.yml loading
path; a negative assertion can only be constructed as a source scan.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
TIER_LITERALS = frozenset({"core", "anchored", "research_only"})


def _string_literals(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def test_tier_literals_only_in_data_contracts_module():
    offenders: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "data_contracts.py":
            continue
        for literal in _string_literals(path):
            if literal in TIER_LITERALS:
                offenders.setdefault(str(path.relative_to(SRC)), set()).add(
                    literal
                )
    assert not offenders, f"tier literals outside data_contracts.py: {offenders}"
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n sq312 pytest tests/unit/test_tier_literals.py -q`
Expected: FAIL 或发现既有字面量。预期失败点：`runner.py`/`gates.py` 之外的旧代码若在字符串里写了 `"core"`（例如消息文本）。**修复方式**：把那些字符串改写成不含档位词的形式（如 `"main tables"`）——这是判据 3 的本意；不得给扫描加白名单，不得注释测试。

- [ ] **Step 3: 运行通过**

Run: `conda run -n sq312 pytest tests/unit/test_tier_literals.py -q`
Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_tier_literals.py <任何为通过扫描而改写的 src 文件>
git commit -m "test(tiers): scan src/ for tier literals outside data_contracts

Criterion 3: tier values only enter the runtime via sources.yml loading.

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 11（B2）：表级取数覆盖模型 + build_config 键

**Files:**
- Create: `src/stock_quant/data_model/fetch_coverage.py`
- Modify: `src/stock_quant/data_pipeline.py:320-363`（`dataset_build_config` 加参数）
- Modify: `src/stock_quant/data_model/dataset.py`（`_normalize_build_config` 接受新键——先读其实现，照 `raw_snapshots`/`calendar_coverage` 键的处理方式加透传）
- Test: `tests/unit/test_fetch_coverage.py`

**Interfaces:**
- Consumes: `_window`（Task 9，Task 13 校验用）、`dataset_build_config`（data_pipeline.py:320）。
- Produces: `FetchSegment(table, kind, window_start, window_end, reason)`；常量 `KIND_FETCHED/KIND_CARRIED/KIND_NOT_FETCHED`、`NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW = "operator_explicit_window"`；`to_build_config_payload(coverage) -> dict`、`validate_table_fetch_coverage(payload, *, anchor_start, published_end) -> list[tuple[str, dict]]`（Task 12/13/14 消费）。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_fetch_coverage.py
"""Per-table fetch coverage model and manifest validation (spec D5.3)."""

from __future__ import annotations

from datetime import date

import pytest

from stock_quant.data_model.fetch_coverage import (
    KIND_CARRIED,
    KIND_FETCHED,
    KIND_NOT_FETCHED,
    NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW,
    FetchSegment,
    to_build_config_payload,
    validate_table_fetch_coverage,
)

ANCHOR = date(2021, 1, 4)
END = date(2026, 8, 28)


def _payload():
    return to_build_config_payload(
        {
            "daily_bar": [
                FetchSegment("daily_bar", KIND_CARRIED, date(2021, 1, 4), date(2026, 8, 27)),
                FetchSegment("daily_bar", KIND_FETCHED, date(2026, 8, 28), date(2026, 8, 28)),
            ]
        }
    )


def test_payload_roundtrip():
    payload = _payload()
    assert payload["daily_bar"][0]["kind"] == KIND_CARRIED
    assert payload["daily_bar"][1]["window_start"] == "2026-08-28"


def test_contiguous_coverage_passes():
    assert validate_table_fetch_coverage(_payload(), anchor_start=ANCHOR,
                                         published_end=END) == []


def test_missing_table_fails():
    violations = validate_table_fetch_coverage({}, anchor_start=ANCHOR,
                                               published_end=END)
    assert [code for code, _ in violations] == ["table_fetch_coverage_missing"]


def test_gap_between_carried_and_fetched_fails():
    payload = to_build_config_payload(
        {
            "daily_bar": [
                FetchSegment("daily_bar", KIND_CARRIED, date(2021, 1, 4), date(2026, 8, 25)),
                FetchSegment("daily_bar", KIND_FETCHED, date(2026, 8, 28), date(2026, 8, 28)),
            ]
        }
    )
    codes = [code for code, _ in validate_table_fetch_coverage(
        payload, anchor_start=ANCHOR, published_end=END)]
    assert "fetch_coverage_gap" in codes


def test_not_fetched_requires_operator_reason():
    payload = to_build_config_payload(
        {
            "daily_bar": [
                FetchSegment("daily_bar", KIND_NOT_FETCHED, date(2021, 1, 4), date(2026, 8, 28)),
            ]
        }
    )
    codes = [code for code, _ in validate_table_fetch_coverage(
        payload, anchor_start=ANCHOR, published_end=END)]
    assert "not_fetched_reason_missing" in codes


def test_not_fetched_with_operator_reason_passes():
    payload = to_build_config_payload(
        {
            "daily_bar": [
                FetchSegment("daily_bar", KIND_NOT_FETCHED, date(2021, 1, 4),
                             date(2026, 8, 28),
                             reason=NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW),
            ]
        }
    )
    assert validate_table_fetch_coverage(payload, anchor_start=ANCHOR,
                                         published_end=END) == []


def test_unknown_kind_fails():
    with pytest.raises(ValueError):
        FetchSegment("daily_bar", "fetched_sometimes", date(2021, 1, 4), date(2026, 8, 28))
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n sq312 pytest tests/unit/test_fetch_coverage.py -q`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: 实现 `fetch_coverage.py`**

```python
"""Per-table fetch coverage: which history segments this version re-fetched,
carried from the baseline, or skipped (spec D5.3 / A3).

Recorded into ``build_config.table_fetch_coverage``; consumed by the
``table_fetch_coverage_evidence`` acceptance check and the research
preflight (NOT_FETCHED segments reject runs referencing that table).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

KIND_FETCHED = "fetched"
KIND_CARRIED = "carried"
KIND_NOT_FETCHED = "not_fetched"
_KINDS = frozenset({KIND_FETCHED, KIND_CARRIED, KIND_NOT_FETCHED})

#: The only accepted NOT_FETCHED reason (spec D5.2): an explicit --start/--end
#: window deviated from the table's contract fetch window, so the table
#: skipped fetching this round and the baseline was carried instead.
NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW = "operator_explicit_window"
_REASONS = frozenset({NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW})


@dataclass(frozen=True)
class FetchSegment:
    """One contiguous segment of a table's history in one build."""

    table: str
    kind: str
    window_start: date
    window_end: date
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"unknown fetch-coverage kind {self.kind!r}")
        if self.window_end < self.window_start:
            raise ValueError("fetch segment window is inverted")
        if self.kind == KIND_NOT_FETCHED:
            if self.reason is None:
                raise ValueError("not_fetched segments require a reason")
            if self.reason not in _REASONS:
                raise ValueError(
                    f"unknown not_fetched reason {self.reason!r}"
                )
        elif self.reason is not None:
            raise ValueError(
                f"only not_fetched segments carry a reason, got {self.reason!r}"
            )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "table": self.table,
            "kind": self.kind,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


def to_build_config_payload(
    coverage: Mapping[str, Sequence[FetchSegment]],
) -> dict[str, list[dict[str, object]]]:
    """Serialize per-table segments into the build_config payload shape."""
    return {
        table: [segment.to_dict() for segment in segments]
        for table, segments in sorted(coverage.items())
    }


def validate_table_fetch_coverage(
    payload: object,
    *,
    anchor_start: date,
    published_end: date,
) -> list[tuple[str, dict]]:
    """Structural violations of a recorded ``table_fetch_coverage`` payload.

    Rules: at least one table recorded; segments sorted, inside
    [anchor_start, published_end]; ``fetched``/``carried`` segments cover the
    whole span contiguously; ``not_fetched`` only with the operator-window
    reason (an explicit window made every table skip — the empty-run case).
    """
    if not isinstance(payload, dict) or not payload:
        return [("table_fetch_coverage_missing", {})]
    violations: list[tuple[str, dict]] = []
    for table, raw_segments in sorted(payload.items()):
        if not isinstance(raw_segments, list):
            violations.append(("table_fetch_coverage_malformed", {"table": table}))
            continue
        try:
            segments = [
                FetchSegment(
                    table=str(segment["table"]),
                    kind=str(segment["kind"]),
                    window_start=date.fromisoformat(str(segment["window_start"])),
                    window_end=date.fromisoformat(str(segment["window_end"])),
                    reason=(
                        None
                        if segment.get("reason") is None
                        else str(segment["reason"])
                    ),
                )
                for segment in raw_segments
            ]
        except (KeyError, ValueError, TypeError) as error:
            violations.append(
                (
                    "table_fetch_coverage_malformed",
                    {"table": table, "error_code": type(error).__name__},
                )
            )
            continue
        ordered = sorted(segments, key=lambda item: item.window_start)
        if any(segment.window_end < segment.window_start for segment in ordered):
            violations.append(("fetch_coverage_inverted", {"table": table}))
            continue
        if any(
            segment.window_start < anchor_start
            or segment.window_end > published_end
            for segment in ordered
        ):
            violations.append(("fetch_coverage_out_of_window", {"table": table}))
        not_fetched = [s for s in ordered if s.kind == KIND_NOT_FETCHED]
        if not_fetched:
            if not ordered or any(s.kind != KIND_NOT_FETCHED for s in ordered):
                violations.append(
                    ("not_fetched_mixed_with_fetch", {"table": table})
                )
            continue  # a skipped table covers nothing by design
        cursor = anchor_start
        for segment in ordered:
            if segment.window_start > cursor:
                violations.append(
                    (
                        "fetch_coverage_gap",
                        {"table": table, "gap_start": cursor.isoformat()},
                    )
                )
            cursor = max(cursor, segment.window_end + timedelta(days=1))
        if cursor <= published_end:
            violations.append(
                (
                    "fetch_coverage_gap",
                    {"table": table, "gap_start": cursor.isoformat()},
                )
            )
    return violations
```

（`from datetime import timedelta` 补充进 import。）

- [ ] **Step 4: build_config 接线**

`dataset_build_config`（data_pipeline.py:320-363）签名与返回字典各加一处：

```python
def dataset_build_config(
    ...,
    table_fetch_coverage: Mapping[str, object] | None = None,
):
    ...
    config = {...现有键...}
    if table_fetch_coverage:
        config["table_fetch_coverage"] = dict(table_fetch_coverage)
    return config
```

（按该函数现有组织方式落位；返回值是 dict，直接加键即可。`Mapping` import 补上。）

`dataset.py` 的 `_normalize_build_config`：先读实现，找到它校验/透传 `raw_snapshots` 键的位置，按同一模式加 `table_fetch_coverage`（透传为 dict；无该键时省略，保证旧 manifest 不变）。

- [ ] **Step 5: 运行通过**

Run: `conda run -n sq312 pytest tests/unit/test_fetch_coverage.py tests/unit/test_data_pipeline.py -q`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add src/stock_quant/data_model/fetch_coverage.py src/stock_quant/data_pipeline.py src/stock_quant/data_model/dataset.py tests/unit/test_fetch_coverage.py
git commit -m "feat(fetch_coverage): per-table fetch-coverage model and build_config key

fetched/carried/not_fetched segments with structural validation;
not_fetched requires the operator_explicit_window reason (spec D5.3).

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 12（B2）：取数窗口规划纯函数（A3/F1/F2）

**Files:**
- Create: `src/stock_quant/data_model/fetch_windows.py`
- Test: `tests/unit/test_fetch_windows.py`

**Interfaces:**
- Consumes: `DataContract.incremental`（Task 1）、`FetchSegment`/常量（Task 11）。
- Produces: `FetchWindowPlan(table, kind, window_start, window_end, reason)`；`plan_table_fetch_windows(contracts, baseline_covered, *, request_start, request_end, anchor_start, latest_open_day, lookback_days=90) -> dict[str, FetchWindowPlan]`（Task 13 消费）。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_fetch_windows.py
"""A3/F1/F2: per-table fetch windows from contracts + explicit CLI window."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from stock_quant.data_contracts import parse_data_contracts
from stock_quant.data_model.fetch_coverage import KIND_NOT_FETCHED, NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW
from stock_quant.data_model.fetch_windows import FetchWindowPlan, plan_table_fetch_windows

ANCHOR = date(2015, 1, 5)
LATEST = date(2026, 8, 28)


def _contracts(**overrides):
    return parse_data_contracts(
        [
            {
                "table": "daily_bar",
                "tier": "core",
                "primary_transport": "tushare:relay",
                "anchors": [],
                "conflict": "downgrade",
                "pit": None,
                "coverage_shape": "none",
                "incremental": "last_covered_plus_1",
            },
            {
                "table": "income",
                "tier": "anchored",
                "primary_transport": "tushare:relay",
                "anchors": ["akshare_cninfo_announcement"],
                "conflict": "downgrade",
                "pit": None,
                "coverage_shape": "per_symbol_window",
                "incremental": "disclosure_calendar",
            },
        ]
    )


def test_last_covered_plus_1_starts_after_baseline():
    plans = plan_table_fetch_windows(
        _contracts(),
        {"daily_bar": (date(2026, 8, 27), date(2026, 8, 27))},
        request_start=None,
        request_end=None,
        anchor_start=ANCHOR,
        latest_open_day=LATEST,
    )
    assert plans["daily_bar"].window_start == date(2026, 8, 28)
    assert plans["daily_bar"].window_end == LATEST
    assert plans["daily_bar"].kind != KIND_NOT_FETCHED


def test_disclosure_calendar_looks_back():
    plans = plan_table_fetch_windows(
        _contracts(),
        {},
        request_start=None,
        request_end=None,
        anchor_start=ANCHOR,
        latest_open_day=LATEST,
        lookback_days=90,
    )
    assert plans["income"].window_start == LATEST - timedelta(days=90)


def test_explicit_start_deviating_skips_with_operator_reason():
    plans = plan_table_fetch_windows(
        _contracts(),
        {"daily_bar": (date(2026, 8, 27), date(2026, 8, 27))},
        request_start=date(2021, 1, 1),  # earlier than the contract window
        request_end=None,
        anchor_start=ANCHOR,
        latest_open_day=LATEST,
    )
    plan = plans["daily_bar"]
    assert plan.kind == KIND_NOT_FETCHED
    assert plan.reason == NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW


def test_explicit_start_narrowing_skips_too():
    plans = plan_table_fetch_windows(
        _contracts(),
        {"daily_bar": (date(2026, 8, 27), date(2026, 8, 27))},
        request_start=date(2026, 8, 28),  # later than planned start: narrowing
        request_end=None,
        anchor_start=ANCHOR,
        latest_open_day=LATEST,
    )
    # F1: an explicit window neither overrides nor narrows a contract window;
    # any deviation from the planned start skips the table.
    assert plans["daily_bar"].kind == KIND_NOT_FETCHED


def test_explicit_end_earlier_than_latest_skips():
    plans = plan_table_fetch_windows(
        _contracts(),
        {"daily_bar": (date(2026, 8, 27), date(2026, 8, 27))},
        request_start=None,
        request_end=date(2026, 8, 27),
        anchor_start=ANCHOR,
        latest_open_day=LATEST,
    )
    assert plans["daily_bar"].kind == KIND_NOT_FETCHED
    assert plans["daily_bar"].reason == NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW


def test_matching_explicit_window_fetches():
    plans = plan_table_fetch_windows(
        _contracts(),
        {"daily_bar": (date(2026, 8, 27), date(2026, 8, 27))},
        request_start=date(2026, 8, 28),
        request_end=None,
        anchor_start=ANCHOR,
        latest_open_day=LATEST,
    )
    assert plans["daily_bar"].window_start == date(2026, 8, 28)
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n sq312 pytest tests/unit/test_fetch_windows.py -q`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: 实现 `fetch_windows.py`**

```python
"""Per-table fetch-window planning (spec A3 / D5.2, F1/F2).

The CLI --start/--end is the *minimal common window*, never a per-table
fetch window.  Each table's fetch window comes from its contract's
``incremental`` strategy; an explicit window that deviates from a table's
contract window makes that table skip fetching this round, recorded as
NOT_FETCHED with the operator_explicit_window reason (F1: an explicit
window neither overrides nor narrows a contract window).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from stock_quant.data_contracts import (
    INCREMENTAL_CHANGE_DRIVEN_FULL,
    INCREMENTAL_DISCLOSURE_CALENDAR,
    INCREMENTAL_LAST_COVERED_PLUS_1,
    DataContract,
)
from stock_quant.data_model.fetch_coverage import (
    KIND_NOT_FETCHED,
    NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW,
)


@dataclass(frozen=True)
class FetchWindowPlan:
    """One table's fetch decision for this update round."""

    table: str
    kind: str
    window_start: date | None
    window_end: date | None
    reason: str | None = None


def _contract_start(
    contract: DataContract,
    baseline_covered: tuple[date, date] | None,
    *,
    anchor_start: date,
    latest_open_day: date,
    lookback_days: int,
) -> date:
    """The planned fetch start for one table's ``incremental`` strategy."""
    strategy = contract.incremental
    if strategy == INCREMENTAL_LAST_COVERED_PLUS_1:
        if baseline_covered is None:
            return anchor_start
        return baseline_covered[1] + timedelta(days=1)
    if strategy == INCREMENTAL_DISCLOSURE_CALENDAR:
        return max(anchor_start, latest_open_day - timedelta(days=lookback_days))
    if strategy == INCREMENTAL_CHANGE_DRIVEN_FULL:
        return anchor_start
    raise ValueError(f"unknown incremental strategy {strategy!r}")


def plan_table_fetch_windows(
    contracts: Mapping[str, DataContract],
    baseline_covered: Mapping[str, tuple[date, date]],
    *,
    request_start: date | None,
    request_end: date | None,
    anchor_start: date,
    latest_open_day: date,
    lookback_days: int = 90,
) -> dict[str, FetchWindowPlan]:
    """Fetch plans for every declared table under the current CLI window."""
    plans: dict[str, FetchWindowPlan] = {}
    for table, contract in sorted(contracts.items()):
        start = _contract_start(
            contract,
            baseline_covered.get(table),
            anchor_start=anchor_start,
            latest_open_day=latest_open_day,
            lookback_days=lookback_days,
        )
        if request_start is not None and request_start != start:
            plans[table] = FetchWindowPlan(
                table,
                KIND_NOT_FETCHED,
                None,
                None,
                reason=NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW,
            )
            continue
        if request_end is not None and request_end < latest_open_day:
            plans[table] = FetchWindowPlan(
                table,
                KIND_NOT_FETCHED,
                None,
                None,
                reason=NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW,
            )
            continue
        plans[table] = FetchWindowPlan(
            table, "fetched", start, latest_open_day
        )
    return plans
```

（`request_start != start` 语义：早于契约起点 → 取数窗被显式窗口「覆盖」；晚于 → 被「收窄」；两者都被 F1 禁止 → 跳过留痕。）

- [ ] **Step 4: 运行通过**

Run: `conda run -n sq312 pytest tests/unit/test_fetch_windows.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/stock_quant/data_model/fetch_windows.py tests/unit/test_fetch_windows.py
git commit -m "feat(fetch_windows): plan per-table incremental fetch windows

Explicit CLI windows that deviate from a contract window skip the table
with NOT_FETCHED (operator_explicit_window) per A3/F1/F2.

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 13（B2）：管线增量取数接线 + 验收检查（判据 5 前半）

**Files:**
- Modify: `src/stock_quant/data_pipeline.py`（update() 取数阶段接线 + 覆盖段记录）
- Modify: `src/stock_quant/research/acceptance/checks.py:193-203`（functions dict 加项）+ 新增 `_check_table_fetch_coverage`
- Modify: `src/stock_quant/research/acceptance/models.py:76`（`AUTOMATED_CHECK_CODES` 加项）
- Test: `tests/integration/test_data_pipeline.py`（追加一条集成用例）

**Interfaces:**
- Consumes: `plan_table_fetch_windows`（Task 12）、`to_build_config_payload`/`validate_table_fetch_coverage`（Task 11）、`_window`（Task 9）。
- Produces: `build_config.table_fetch_coverage` 的发布记录——Task 14 预检与判据 5 的消费依据。

- [ ] **Step 1: 先读取数阶段**

Read `src/stock_quant/data_pipeline.py` update() 的取数段（:567-800 区间）：定位 `_refresh_calendar`、`_refresh_security_master`、`_fetch_primary_stock`（及 corporate-action 取数调用）的 start/end 实参来源，与 :632-652 的窗口计算。**语义规则（本任务的全部改动都遵守它）**：

- 校验/合并窗口（`start`/`end` 变量，即 `_missing_issues`、merge、验收锚）**保持现状不动**——它已由 Task 9 的验收层换锚语义覆盖，取数窗与审查窗从此分离；
- 只有**取数调用**的窗口换成 `plan_table_fetch_windows` 的计划值（daily 表 → `plans["daily_bar"]`，corporate-action 取数 → `plans["corporate_action"]`，calendar → `plans["trading_calendar"]`，master → `plans["security_master"]`）；
- `kind == not_fetched` 的表跳过取数调用，其余管线逻辑（基线结转、合并、校验）照跑——跳过的表由 `_read_baseline` 的基线数据完全覆盖；
- 覆盖段记录：`fetched` 段 = 实际取数窗；`carried` 段 = 基线已覆盖区间（每表从 `_read_baseline` 返回帧的 trade_date 范围与既有 span 结构取）；`not_fetched` 段 = 计划跳过的表（整窗一条段，reason 照计划）。

- [ ] **Step 2: 写失败测试（tests/integration/test_data_pipeline.py 追加）**

先读该文件现有的最小管线 fixture（合成的 tmp 项目根 + 打桩 source）。新用例：

```python
def test_update_records_table_fetch_coverage(env):
    # Model on the existing minimal pipeline fixture in this file: a synthetic
    # baseline dataset plus a stubbed daily fetch for one extra session.
    result = env.pipeline.update(DataUpdateRequest())
    manifest = json.loads(
        (env.root / "data" / "standardized" / result.dataset_version
         / "dataset_manifest.json").read_text(encoding="utf-8")
    )
    coverage = manifest["build_config"]["table_fetch_coverage"]
    assert "daily_bar" in coverage
    kinds = {segment["kind"] for segment in coverage["daily_bar"]}
    assert "carried" in kinds and "fetched" in kinds
    # carried segments cover the baseline; fetched covers the new session
    fetched = [s for s in coverage["daily_bar"] if s["kind"] == "fetched"]
    assert fetched[0]["window_start"] == fetched[0]["window_end"]  # one session
```

（`env`/`DataUpdateRequest` 名以该文件实际 fixture 与 data_pipeline.py 的请求类型为准——先读再写。）

- [ ] **Step 3: 运行确认失败**

Run: `conda run -n sq312 pytest tests/integration/test_data_pipeline.py::test_update_records_table_fetch_coverage -q`
Expected: FAIL — manifest 无 `table_fetch_coverage` 键。

- [ ] **Step 4: 管线接线**

update() 内（:652 窗口计算之后、取数调用之前）：

```python
        # Per-table fetch windows (spec A3): the validation window stays as
        # computed above; fetch calls switch to the contract windows below.
        plans = plan_table_fetch_windows(
            self._project_config.data_contracts,
            baseline_covered=self._baseline_covered_by_table(),
            request_start=request.start_date,
            request_end=request.end_date,
            anchor_start=start,  # the validation anchor this round
            latest_open_day=published_open_days[-1],
        )
```

新增辅助方法（把基线覆盖区间集中一处，供计划与记录共用）：

```python
    def _baseline_covered_by_table(self) -> dict[str, tuple[date, date]]:
        """Baseline covered intervals per table, from the carried frames."""
        return {
            "daily_bar": (self._baseline_daily_dates[0], self._baseline_daily_dates[-1])
            if self._baseline_daily_dates else None,
            ...
        }
```

（实现时以 update() 内现有基线帧变量为准——`_read_baseline` 返回的 daily/ca/calendar/master 帧各自取 trade_date（或对应日期列）的 min/max；没有基线的表不记 covered。）

然后逐处替换取数调用：daily 取数处 `start` → `plans["daily_bar"].window_start`（not_fetched 则跳过整段取数调用并记录）；corporate-action、calendar、master 同理。publish 前构造并传入：

```python
        fetch_segments: dict[str, list[FetchSegment]] = {}
        for table, plan in sorted(plans.items()):
            if plan.kind == KIND_NOT_FETCHED:
                fetch_segments[table] = [
                    FetchSegment(table, KIND_NOT_FETCHED, start, end,
                                 reason=plan.reason)
                ]
            else:
                covered = self._baseline_covered_by_table().get(table)
                segments: list[FetchSegment] = []
                if covered is not None and covered[0] <= plan.window_start:
                    segments.append(
                        FetchSegment(table, KIND_CARRIED, covered[0], plan.window_start - timedelta(days=1))
                    )
                segments.append(
                    FetchSegment(table, KIND_FETCHED, plan.window_start, end)
                )
                fetch_segments[table] = segments
```

并在 `dataset_build_config(...)` 调用（:900 起）加：

```python
                    table_fetch_coverage=to_build_config_payload(fetch_segments),
```

（`start`/`end`/`timedelta`/`KIND_*`/`FetchSegment` import 按需补；carried 段用开区间语义按 validate 的连续性要求对齐——若基线日与取数日相邻，carried 段末尾为 `plan.window_start - 1 天`。）

- [ ] **Step 5: 验收检查接线**

`acceptance/models.py:76` 的 `AUTOMATED_CHECK_CODES` 追加 `"table_fetch_coverage_evidence"`（顺序放 `calendar_coverage_evidence` 之后）。`checks.py` functions dict（:193-203）同步加 `"table_fetch_coverage_evidence": _check_table_fetch_coverage,`，并新增：

```python
def _check_table_fetch_coverage(value: AcceptanceCheckInput) -> CheckResult:
    """Require per-table fetch coverage: contiguous fetched+carried segments
    over the review window, NOT_FETCHED only for the operator-explicit
    window (spec D5.3)."""
    evidence = dataset_evidence(value)
    build = _build_config(evidence.manifest)
    if build is None:
        return _result(
            "table_fetch_coverage_evidence",
            [["dataset_build_evidence_missing", "build_config"]],
        )
    start, end = _window(build)
    violations = validate_table_fetch_coverage(
        build.get("table_fetch_coverage", {}),
        anchor_start=start,
        published_end=end,
    )
    failures = [[code, json.dumps(details, sort_keys=True)] for code, details in violations]
    return _result("table_fetch_coverage_evidence", failures)
```

（`json` 已 import；`validate_table_fetch_coverage` 从 `stock_quant.data_model.fetch_coverage` import。）

- [ ] **Step 6: 运行通过**

Run: `conda run -n sq312 pytest tests/integration/test_data_pipeline.py tests/unit/test_acceptance_checks.py tests/unit/test_acceptance_models.py -q`
Expected: PASS（`test_acceptance_models.py` 若按字面枚举 `AUTOMATED_CHECK_CODES`，把新码加进期望；不删改其它断言）。再跑 `conda run -n sq312 pytest tests/integration/test_acceptance_checks.py -q` 确认既有合成数据集用例——其 manifest 无 `table_fetch_coverage` 键时新检查按设计 FAIL：这些用例若整体断言全 PASS，把其合成 manifest 的 build_config 补上最小合法 `table_fetch_coverage`（每表一条 fetched 段覆盖审查窗），不得改检查本身。

- [ ] **Step 7: Commit**

```bash
git add src/stock_quant/data_pipeline.py src/stock_quant/research/acceptance/checks.py src/stock_quant/research/acceptance/models.py tests/integration/test_data_pipeline.py
git commit -m "feat(pipeline): incremental per-table fetch windows with coverage evidence

Fetch calls follow contract windows; carried/fetched/not_fetched
segments land in build_config.table_fetch_coverage and the new
acceptance check validates contiguity (spec D5.2-3, criterion 5).

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 14（B2）：research 预检 NOT_FETCHED + 调用账本 + RUNBOOK :102 退役

**Files:**
- Create: `src/stock_quant/data_model/call_ledger.py`
- Modify: `src/stock_quant/data_model/fetch_coverage.py`（`not_fetched_input_tables`）
- Modify: `src/stock_quant/research/runner.py`（预检扩展）
- Modify: `src/stock_quant/data_pipeline.py`（update() 落账本）
- Modify: `RUNBOOK.md:102-105`（退役替换）
- Test: `tests/unit/test_call_ledger.py`、`tests/unit/test_table_tier_preflight.py`（追加）

**Interfaces:**
- Consumes: `table_tier_violations`（Task 8）、`FetchSegment`（Task 11）。
- Produces: `not_fetched_input_tables(build_config, input_tables) -> list[str]`；`write_call_ledger(project_root, run_id, payload) -> Path`、`render_call_ledger(sources) -> dict`。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_call_ledger.py
"""Call ledger rendering and persistence (spec D5.5)."""

from __future__ import annotations

import json

from stock_quant.data_model.call_ledger import render_call_ledger, write_call_ledger


class _ListCalls:
    calls = [
        {"endpoint": "daily", "rows": 200},
        {"endpoint": "daily", "rows": 300},
        {"endpoint": "trade_cal"},
    ]


class _ScalarCalls:
    calls = 7


def test_render_list_shaped_calls():
    rows = render_call_ledger({"tushare": _ListCalls()})
    assert rows["tushare"]["calls"] == 3
    assert rows["tushare"]["endpoints"] == {"daily": 2, "trade_cal": 1}


def test_render_scalar_calls():
    rows = render_call_ledger({"baostock": _ScalarCalls()})
    assert rows["baostock"]["calls"] == 7
    assert rows["baostock"]["endpoints"] == {}


def test_write_ledger(tmp_path):
    path = write_call_ledger(
        tmp_path, "run-abc", {"tushare": {"calls": 3, "endpoints": {"daily": 3}}}
    )
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["tushare"]["calls"] == 3
```

追加到 `tests/unit/test_table_tier_preflight.py`：

```python
def test_not_fetched_input_tables_detected():
    from stock_quant.data_model.fetch_coverage import not_fetched_input_tables

    build_config = {
        "table_fetch_coverage": {
            "daily_bar": [
                {"table": "daily_bar", "kind": "not_fetched",
                 "window_start": "2021-01-04", "window_end": "2026-08-28",
                 "reason": "operator_explicit_window"},
            ],
            "income": [
                {"table": "income", "kind": "fetched",
                 "window_start": "2026-08-01", "window_end": "2026-08-28"},
            ],
        }
    }
    assert not_fetched_input_tables(build_config, ("income",)) == []
    assert not_fetched_input_tables(build_config, ("daily_bar",)) == ["daily_bar"]
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n sq312 pytest tests/unit/test_call_ledger.py tests/unit/test_table_tier_preflight.py -q`
Expected: FAIL — 符号不存在。

- [ ] **Step 3: 实现**

`fetch_coverage.py` 追加：

```python
def not_fetched_input_tables(
    build_config: Mapping[str, object], input_tables: Sequence[str]
) -> list[str]:
    """Input tables whose pinned version carries NOT_FETCHED segments.

    A version with skipped fetches is not a complete-fetch version; research
    runs referencing such tables fail preflight and should pin a full-update
    version instead (spec D5.3, sixth-round ruling).
    """
    coverage = build_config.get("table_fetch_coverage", {})
    if not isinstance(coverage, dict):
        return []
    skipped: list[str] = []
    for table in input_tables:
        segments = coverage.get(table, [])
        if isinstance(segments, list) and any(
            isinstance(segment, Mapping) and segment.get("kind") == KIND_NOT_FETCHED
            for segment in segments
        ):
            skipped.append(table)
    return skipped
```

`call_ledger.py`：

```python
"""Per-update call ledger: endpoint x count x quota consumption (spec D5.5)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path


def _summarize_calls(calls: object) -> tuple[int, dict[str, int]]:
    """Normalize a source's ``calls`` attribute into (total, by endpoint)."""
    if calls is None:
        return 0, {}
    if isinstance(calls, int):
        return calls, {}
    records = list(calls)
    endpoints: dict[str, int] = {}
    for record in records:
        key = (
            str(record.get("endpoint"))
            if isinstance(record, Mapping)
            else "unknown"
        )
        endpoints[key] = endpoints.get(key, 0) + 1
    return len(records), endpoints


def render_call_ledger(sources: Mapping[str, object]) -> dict[str, object]:
    """Normalized ledger rows: one entry per source."""
    rows: dict[str, object] = {}
    for name, source in sorted(sources.items()):
        total, endpoints = _summarize_calls(getattr(source, "calls", 0))
        rows[name] = {"calls": total, "endpoints": endpoints}
    return rows


def write_call_ledger(
    project_root: Path, run_id: str, payload: Mapping[str, object]
) -> Path:
    """Persist the ledger under ``data/runs/<run_id>/call_ledger.json``."""
    path = Path(project_root) / "data" / "runs" / run_id / "call_ledger.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path
```

- [ ] **Step 4: 接线**

runner 预检：`_preflight_table_tiers` 里（`manifest` 读入后）加：

```python
        not_fetched = not_fetched_input_tables(
            manifest.get("build_config", {}), input_tables
        )
```

并扩 `table_tier_violations` 或就地追加：not_fetched 的表在 RESEARCH 模式失败、ENGINEERING 豁免并标注（与 research_only 同族）：

```python
        if not_fetched and not engineering:
            codes.append("table_not_fetched")
```

（summary 的 violations 字段自然包含；`table_not_fetched` 加入 exempt 判定集合。）

管线：update() 在 publish 成功后、`_result` 返回前落账本：

```python
        ledger = render_call_ledger(
            {name: source for name, source in self._active_sources().items()}
        )
        write_call_ledger(self._project_root, run_id, ledger)
```

（新增 `_active_sources()` 返回 update 内实际构造的 source 实例字典——tushare/akshare/baostock(若启用)；实现时把取数段里创建的实例存到局部字典复用即可。**先读 `data_sources/base.py` 与 tushare.py 的 `calls` 属性真实形状**，若与 `_summarize_calls` 假设（int 或 Mapping 记录列表）不同，按真实形状扩展该函数并同步单测，不改变账本输出契约。）

- [ ] **Step 5: RUNBOOK :102-105 退役**

Read `RUNBOOK.md:95-110` 定位「全区间再跑 `--start 2021-01-01 --end 2026-08-30`」条目，替换为：

```markdown
- 全区间重取不再是例行命令：默认更新即按表契约增量取数（`incremental:`
  字段决定每表取数窗）。需要全窗口重取时用季度漂移审计
  （`python project/drift_audit.py --root .`）或离线战役
  （`extend_history_offline.py` 先例）。不要再跑 `--start 2021-01-01
  --end 2026-08-30`——显式窗口偏离契约取数窗时所有表跳过取数，只留下
  NOT_FETCHED 记录（spec D5.2/F1）。
```

- [ ] **Step 6: 运行通过**

Run: `conda run -n sq312 pytest tests/unit/test_call_ledger.py tests/unit/test_table_tier_preflight.py tests/integration/test_table_tier_preflight.py tests/integration/test_data_pipeline.py -q`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add src/stock_quant/data_model/call_ledger.py src/stock_quant/data_model/fetch_coverage.py src/stock_quant/research/runner.py src/stock_quant/data_pipeline.py RUNBOOK.md tests/unit/test_call_ledger.py tests/unit/test_table_tier_preflight.py
git commit -m "feat(pipeline): NOT_FETCHED consumer gate, call ledger, RUNBOOK rerun retirement

Research runs referencing tables with NOT_FETCHED segments fail
preflight (ENGINEERING exempt); each update writes a call ledger;
the full-range rerun command is retired in favor of the drift audit.

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 15（B2）：季度漂移审计（判据 5 后半）

**Files:**
- Create: `project/drift_audit.py`
- Modify: `project/SCRIPTS.md`（登记新脚本——tools/check_operational_docs.py 要求一一对应）
- Test: `tests/unit/test_drift_audit.py`

**Interfaces:**
- Consumes: `RawStore.verify_evidence`/`RawSnapshotEvidence`（raw_store.py）、`DatasetPublisher.current`、`load_project_config`、`request_parameters` manifest 字段（raw_store.py `_manifest_for`）。
- Produces: 审计命令 + `render_audit_record(version, rows) -> str`（脱敏报告正文）——运维记录模板。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_drift_audit.py
"""Drift-audit report: pure rendering, redaction, verdict classification."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "project"))

from drift_audit import classify_drift, render_audit_record  # noqa: E402


def test_classify_drift():
    assert classify_drift("abc", "abc") == ("stable", None)
    assert classify_drift("abc", "def") == ("drifted", "abc")


def test_redaction_never_leaks_credentials():
    row = {
        "source": "tushare",
        "endpoint": "daily",
        "request_key": "x" * 16,
        "stored_sha256": "a" * 64,
        "fetched_sha256": "b" * 64,
        "note": "TUSHARE_TOKEN=SECRETVALUE123",
    }
    rendered = render_audit_record("version-1", [row])
    assert "SECRETVALUE123" not in rendered
    assert "TUSHARE_TOKEN" not in rendered
    # Endpoint names and hashes are allowed (no credential segments).
    assert "daily" in rendered and "a" * 64 in rendered


def test_render_audit_record_shape():
    rendered = render_audit_record("version-1", [])
    assert "# 漂移审计" in rendered or "drift audit" in rendered.lower()
    assert "version-1" in rendered
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n sq312 pytest tests/unit/test_drift_audit.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'drift_audit'`。

- [ ] **Step 3: 实现 `project/drift_audit.py`**

```python
#!/usr/bin/env python
"""Quarterly full-window drift audit against published raw snapshots (spec D5.4).

Status: diagnostic.

Re-fetches every raw snapshot bound by the published version's
``build_config.raw_snapshots`` and compares the re-fetched bytes against the
stored file hashes.  A drift is never edited in place: the report records it
as evidence for a NEW dataset version plus an operations event record — the
operator decides, this script only reports.  The record never contains
token/key/credential URL segments.

Run with an explicit project root:
    python project/drift_audit.py --root .
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

from stock_quant.config import ProjectConfig, load_project_config
from stock_quant.data_model.dataset import DatasetPublisher
from stock_quant.data_sources.akshare import AkShareSource
from stock_quant.data_sources.baostock import BaoStockSource
from stock_quant.data_sources.base import DataRequest
from stock_quant.data_sources.raw_store import (
    RawSnapshotEvidence,
    RawStore,
)
from stock_quant.data_sources.tushare import TushareSource
from stock_quant.project_root import resolve_project_root


def classify_drift(stored_sha256: str, fetched_sha256: str) -> tuple[str, str | None]:
    """``("stable", None)`` or ``("drifted", stored_sha256)``."""
    if stored_sha256 == fetched_sha256:
        return "stable", None
    return "drifted", stored_sha256


def render_audit_record(version: str, rows: Sequence[Mapping[str, object]]) -> str:
    """Operator-facing ops-record body; endpoint names + hashes only."""
    today = date.today().isoformat()
    lines = [
        f"# 漂移审计 {today}（dataset {version}）",
        "",
        "机制：重取 build_config.raw_snapshots 绑定的原始快照，与已记录哈希逐条比对",
        "（spec D5.4）。发现漂移 = 新证据版本发布 + 事件记录，绝不就地改历史。",
        "本记录不含任何 token/key/凭证 URL 段。",
        "",
    ]
    for row in rows:
        endpoint = str(row.get("endpoint", "unknown"))
        request_key = str(row.get("request_key", ""))[:16]
        stored = str(row.get("stored_sha256", ""))
        fetched = str(row.get("fetched_sha256", "unfetched"))
        lines.append(
            f"- {row.get('source', 'unknown')} {endpoint} "
            f"key={request_key} stored={stored} fetched={fetched}"
        )
    return "\n".join(lines) + "\n"


def load_targets(project_root: Path, version: str) -> list[RawSnapshotEvidence]:
    """The pinned version's bound raw-snapshot evidence rows."""
    import json

    manifest_path = (
        project_root / "data" / "standardized" / version / "dataset_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    build = manifest.get("build_config", {})
    rows = build.get("raw_snapshots", [])
    return [RawSnapshotEvidence(**row) for row in rows]


def _source_for(name: str, config: ProjectConfig):
    if name.startswith("tushare"):
        return TushareSource(config.sources["tushare"])
    if name.startswith("akshare"):
        return AkShareSource(config.sources["akshare"])
    if name.startswith("baostock"):
        return BaoStockSource(config.sources["baostock"])
    raise ValueError(f"no adapter for raw-snapshot source {name!r}")


def run(
    root: Path,
    config: ProjectConfig,
    *,
    version: str | None = None,
    output: Path | None = None,
) -> int:
    """Re-fetch and compare; write the dated ops record; return drift count."""
    publisher = DatasetPublisher(root)
    pinned = version or publisher.current().version
    print(f"drift audit: dataset={pinned}")
    store = RawStore(root)
    targets = load_targets(root, pinned)
    rows: list[dict[str, object]] = []
    drifted = 0
    for evidence in targets:
        try:
            snapshot = store.verify_evidence(evidence)
        except (OSError, ValueError) as error:
            rows.append(
                {
                    "source": evidence.source,
                    "endpoint": evidence.endpoint,
                    "request_key": evidence.request_key,
                    "stored_sha256": evidence.file_sha256,
                    "fetched_sha256": "unverifiable",
                    "note": type(error).__name__,
                }
            )
            continue
        parameters = snapshot.manifest.get("request_parameters", {})
        try:
            source = _source_for(evidence.source, config)
            request = DataRequest(
                str(snapshot.manifest.get("endpoint", evidence.endpoint)),
                tuple(str(symbol) for symbol in parameters.get("symbols", [])),
                date.fromisoformat(str(parameters["start_date"])),
                date.fromisoformat(str(parameters["end_date"])),
                dict(parameters.get("params", {})),
            )
            fetched = store.save(source.fetch(request))
        except Exception as error:  # noqa: BLE001 - audit reports, never raises
            rows.append(
                {
                    "source": evidence.source,
                    "endpoint": evidence.endpoint,
                    "request_key": evidence.request_key,
                    "stored_sha256": evidence.file_sha256,
                    "fetched_sha256": "fetch_failed",
                    "note": type(error).__name__,
                }
            )
            continue
        verdict, _ = classify_drift(evidence.file_sha256, fetched.sha256)
        drifted += int(verdict == "drifted")
        rows.append(
            {
                "source": evidence.source,
                "endpoint": evidence.endpoint,
                "request_key": evidence.request_key,
                "stored_sha256": evidence.file_sha256,
                "fetched_sha256": fetched.sha256,
                "note": verdict,
            }
        )
    if output is None:
        output = Path(root) / "docs" / "operations" / f"{date.today().isoformat()}-drift-audit.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_audit_record(pinned, rows), encoding="utf-8")
    print(f"drift audit: {len(rows)} snapshots, {drifted} drifted -> {output}")
    return drifted


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--version", type=str, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    config = load_project_config(root)
    return run(root, config, version=args.version, output=args.output)


if __name__ == "__main__":
    raise SystemExit(main())
```

（`from datetime import date` 已随顶部导入补上；`parameters` 形状来自 raw_store.py `_manifest_for` 的 `request_parameters` 字典——symbols/start_date/end_date/params 四键，与 base.py `request_metadata` 一致。）

`project/SCRIPTS.md`：按既有行格式追加一行 `- [drift_audit.py](drift_audit.py) — 季度漂移审计……`（先读该文件对齐格式）。

- [ ] **Step 4: 运行通过**

Run: `conda run -n sq312 pytest tests/unit/test_drift_audit.py -q`
Expected: PASS。再跑 `conda run -n sq312 python tools/check_operational_docs.py --root .` 确认脚本登记无遗漏。

- [ ] **Step 5: Commit**

```bash
git add project/drift_audit.py project/SCRIPTS.md tests/unit/test_drift_audit.py
git commit -m "feat(ops): quarterly drift audit over published raw snapshots

Re-fetches every bound raw snapshot and compares hashes; drift is
recorded as evidence for a new version, never edited in place; the
ops record is credential-free (spec D5.4, criterion 5).

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 16（B3）：PIT 访问器 `as_of`（判据 4）

**Files:**
- Create: `src/stock_quant/research/pit.py`
- Test: `tests/unit/test_pit_as_of.py`

**Interfaces:**
- Consumes: `DataContract.pit`/`FACT_ROW_POLICY_MAX_REPORT_TYPE_V1`（Task 1）、`DatasetContext`（dataset.py）。
- Produces: `as_of(context, table, symbol, field, as_of_date, *, contract) -> tuple[object | None, list[dict]]`；`PitConflictError`——未来新表战役的消费接口。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_pit_as_of.py
"""Criterion 4: PIT accessor — no look-ahead, fallback WARN, duplicate ruling."""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from stock_quant.data_contracts import parse_data_contracts
from stock_quant.research.pit import PitConflictError, _fact_row, as_of

CONTRACT = parse_data_contracts(
    [
        {
            "table": "income",
            "tier": "anchored",
            "primary_transport": "tushare:relay",
            "anchors": ["akshare_cninfo_announcement"],
            "conflict": "downgrade",
            "pit": {
                "as_of_field": "f_ann_date",
                "fallback": "ann_date",
                "fact_row_policy": "max_report_type_v1",
            },
            "coverage_shape": "per_symbol_window",
            "incremental": "disclosure_calendar",
        }
    ]
)["income"]


def _frame(rows):
    return pd.DataFrame(rows)


def test_lookahead_rows_are_invisible():
    frame = _frame(
        [
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "1", "revenue": 10.0},
        ]
    )
    value, warnings = _fact_row(
        frame, "600000.SH", "revenue", date(2025, 3, 29), contract=CONTRACT
    )
    assert value is None and warnings == []


def test_fact_row_visible_after_disclosure():
    frame = _frame(
        [
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "1", "revenue": 10.0},
        ]
    )
    value, warnings = _fact_row(
        frame, "600000.SH", "revenue", date(2025, 3, 30), contract=CONTRACT
    )
    assert value == 10.0 and warnings == []


def test_fallback_to_ann_date_emits_warning():
    frame = _frame(
        [
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-28",
             "report_type": "1", "revenue": 10.0},
        ]
    )
    value, warnings = _fact_row(
        frame, "600000.SH", "revenue", date(2025, 3, 29), contract=CONTRACT
    )
    assert value == 10.0
    assert any(w["code"] == "pit_fallback" for w in warnings)


def test_duplicate_identical_rows_deduplicate():
    frame = _frame(
        [
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "1", "revenue": 10.0},
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "1", "revenue": 10.0},
        ]
    )
    value, warnings = _fact_row(
        frame, "600000.SH", "revenue", date(2025, 3, 30), contract=CONTRACT
    )
    assert value == 10.0 and warnings == []


def test_duplicate_conflicting_rows_fail():
    frame = _frame(
        [
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "1", "revenue": 10.0},
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "1", "revenue": 11.0},
        ]
    )
    with pytest.raises(PitConflictError):
        _fact_row(frame, "600000.SH", "revenue", date(2025, 3, 30), contract=CONTRACT)


def test_fact_row_policy_prefers_max_report_type():
    frame = _frame(
        [
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "1", "revenue": 10.0},
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "2", "revenue": 12.0},
        ]
    )
    value, _ = _fact_row(
        frame, "600000.SH", "revenue", date(2025, 3, 30), contract=CONTRACT
    )
    assert value == 12.0


def test_as_of_reads_exact_pinned_context(tmp_path):
    from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
    from stock_quant.data_quality.models import QualityReport

    frame = _frame(
        [
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "1", "revenue": 10.0},
        ]
    )
    # The synthetic table does not need a standardized schema for this test:
    # publish only daily_bar (valid frame from the existing builders) and
    # read the PIT frame from a parquet placed beside it -- if publish
    # rejects unregistered tables, instead open the dataset directory and
    # build a DatasetContext via DatasetReader over a manifest including the
    # table (see tests/integration/test_dataset_publish.py for the minimal
    # publish pattern).
    value, _ = as_of(
        context, "income", "600000.SH", "revenue", date(2025, 3, 30),
        contract=CONTRACT,
    )
    assert value == 10.0


def test_pit_module_never_reads_current():
    source = (Path(__file__).resolve().parents[2] / "src" / "stock_quant"
              / "research" / "pit.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "current":
            pytest.fail("pit.py must never read CURRENT (criterion 4)")
```

（最后一条集成用例若合成 publish 过重，按注释先读 `tests/integration/test_dataset_publish.py` 取最小发布路径；`context` 变量即 `DatasetReader(root).open(version)` 返回的 pinned 上下文。）

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n sq312 pytest tests/unit/test_pit_as_of.py -q`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: 实现 `src/stock_quant/research/pit.py`**

```python
"""PIT (point-in-time) fact access for disclosure-dated tables (spec D3).

Hard constraints: the version is pinned by the caller — ``as_of`` takes an
opened :class:`DatasetContext` for an exact version and NEVER reads CURRENT
(criterion 4); the module lives under ``research/``, not ``data_model/``;
fact-row selection follows the D2 registry's ``pit.fact_row_policy``.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from stock_quant.data_contracts import (
    FACT_ROW_POLICY_MAX_REPORT_TYPE_V1,
    DataContract,
)


class PitConflictError(RuntimeError):
    """Same (as_of, end_date, report_type) carries conflicting values.

    Routes to manual adjudication (reviews.yml semantics) — never a silent
    side pick (spec D3).
    """


def as_of(
    context,
    table: str,
    symbol: str,
    field: str,
    as_of_date: date,
    *,
    contract: DataContract,
) -> tuple[object | None, list[dict]]:
    """Pinned-version PIT read; ``(value, warnings)`` for one fact field."""
    frame = context.read(table)
    return _fact_row(frame, symbol, field, as_of_date, contract=contract)


def _fact_row(
    frame: pd.DataFrame,
    symbol: str,
    field: str,
    as_of_date: date,
    *,
    contract: DataContract,
) -> tuple[object | None, list[dict]]:
    pit = contract.pit
    if pit is None:
        raise ValueError(f"table {contract.table!r} has no pit contract")
    rows = frame[frame["symbol"].astype(str) == symbol].copy()
    if rows.empty:
        return None, []
    point = pd.Timestamp(as_of_date)
    visible = rows[rows[pit.as_of_field] <= point]
    warnings: list[dict] = []
    used_fallback = False
    if visible.empty:
        visible = rows[rows[pit.fallback] <= point]
        used_fallback = True
    if visible.empty:
        return None, []
    key_field = pit.fallback if used_fallback else pit.as_of_field
    latest = visible[key_field].max()
    candidates = visible[visible[key_field] == latest]
    if used_fallback:
        warnings.append(
            {
                "code": "pit_fallback",
                "symbol": symbol,
                "report_period": str(latest.date()),
            }
        )
    if len(candidates) == 1:
        return _value(candidates.iloc[0], field), warnings
    # Same (as_of, end_date, report_type) key: identical values deduplicate,
    # conflicting values fail into manual adjudication (spec D3).
    keyed = candidates.groupby(["end_date", "report_type"], dropna=False)
    winners = []
    for _, group in keyed:
        distinct = group[field].dropna().unique()
        if len(distinct) > 1:
            raise PitConflictError(
                f"{contract.table} {symbol} {field} conflicts for "
                f"(as_of={latest.date()}, end_date, report_type)"
            )
        winners.append(group.iloc[0])
    if len(winners) == 1:
        return _value(winners[0], field), warnings
    winner = _apply_fact_row_policy(pd.DataFrame(winners), field, pit.fact_row_policy)
    return _value(winner, field), warnings


def _value(row, field: str):
    value = row.get(field)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return value


def _apply_fact_row_policy(candidates: pd.DataFrame, field: str, policy: str):
    """Versioned fact-row ruling (spec D2, owner 约束 3)."""
    if policy == FACT_ROW_POLICY_MAX_REPORT_TYPE_V1:
        ordered = candidates.sort_values(
            "report_type", key=lambda series: series.astype(str)
        )
        return ordered.iloc[-1]
    raise ValueError(f"unknown fact_row_policy {policy!r}")
```

（`research/` 层新文件遵循该目录现有风格；`context.read` 即 DatasetContext.read 的既有接口。）

- [ ] **Step 4: 运行通过**

Run: `conda run -n sq312 pytest tests/unit/test_pit_as_of.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/stock_quant/research/pit.py tests/unit/test_pit_as_of.py
git commit -m "feat(research): PIT as_of accessor with no-look-ahead and conflict ruling

Version pinned by the caller (never CURRENT); fallback WARN lands in
warnings for the run quality report; conflicting duplicate rows raise
PitConflictError for manual adjudication (spec D3, criterion 4).

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 17（B3）：端点描述符套件（判据 6）

**Files:**
- Create: `src/stock_quant/data_sources/descriptors.py`
- Test: `tests/unit/test_descriptors.py`

**Interfaces:**
- Consumes: 无（独立框架）。
- Produces: `EndpointDescriptor(endpoint, keying, required_params, auto_slice, retry_categories, truncation_limit, projection, cadence)`；`check_frame(descriptor, frame) -> list[dict]`；`slice_plan(descriptor, frame) -> list[tuple[int, int]]`；`contract_test_skeleton(descriptor) -> str`；`TruncationDetected`——未来新端点战役（A4）的接入套件。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_descriptors.py
"""Criterion 6: descriptor-driven frame checks — hand-written code shrinks
to descriptor + normalize + verification logic only."""

from __future__ import annotations

import pandas as pd
import pytest

from stock_quant.data_sources.descriptors import (
    EndpointDescriptor,
    TruncationDetected,
    check_frame,
    contract_test_skeleton,
    slice_plan,
)


def _descriptor(**overrides):
    values = dict(
        endpoint="daily_basic",
        keying="date_keyed",
        required_params=(),
        auto_slice=False,
        retry_categories=frozenset({"tls_eof"}),
        projection=("ts_code", "trade_date", "close"),
        cadence="daily",
    )
    values.update(overrides)
    return EndpointDescriptor(**values)


def test_6000_row_truncation_fails_closed():
    frame = pd.DataFrame({"trade_date": range(6000)})
    with pytest.raises(TruncationDetected):
        check_frame(_descriptor(), frame)


def test_auto_slice_opted_in_produces_provenance_plan():
    frame = pd.DataFrame({"trade_date": range(12500)})
    issues = check_frame(_descriptor(auto_slice=True), frame)
    assert issues == []
    plan = slice_plan(_descriptor(auto_slice=True), frame)
    assert plan == [(0, 6000), (6000, 12000), (12000, 12500)]


def test_projection_gap_is_flagged():
    frame = pd.DataFrame({"trade_date": range(5)})
    issues = check_frame(_descriptor(), frame)
    assert [issue["code"] for issue in issues] == ["projection_missing"]
    assert "ts_code" in issues[0]["columns"]


def test_unknown_retry_category_rejected():
    with pytest.raises(ValueError):
        _descriptor(retry_categories=frozenset({"dns_timeout"}))


def test_skeleton_is_a_runnable_pytest_shape():
    skeleton = contract_test_skeleton(_descriptor())
    assert "def test_" in skeleton
    assert "daily_basic" in skeleton
    assert "date_keyed" in skeleton
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n sq312 pytest tests/unit/test_descriptors.py -q`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: 实现 `src/stock_quant/data_sources/descriptors.py`**

```python
"""Endpoint descriptors drive frame checks for new data lanes (spec D4).

Every endpoint declares its parameter shape, window-slicing needs, retry
categories, field projection and cadence; the framework then generates the
frame checks, the truncation guard and a contract-test skeleton — what stays
hand-written is normalize + verification logic (A4: the first new table is
hand-written as the generator's reference baseline).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

Keying = Literal["date_keyed", "symbol_keyed", "index_keyed"]
_RETRY_CATEGORIES = frozenset({"tls_eof", "timeout", "rate_limit"})

#: The silent-truncation threshold observed in production (spec D4).
TRUNCATION_ROW_LIMIT = 6000


class TruncationDetected(RuntimeError):
    """A frame hit the truncation limit without ``auto_slice`` declared.

    Fail-closed by default: this endpoint's round fails and leaves evidence
    rather than silently publishing a truncated view (spec D4).
    """


@dataclass(frozen=True)
class EndpointDescriptor:
    """One endpoint's declared shape (spec D4)."""

    endpoint: str
    keying: Keying
    required_params: tuple[str, ...] = ()
    auto_slice: bool = False
    retry_categories: frozenset[str] = frozenset()
    truncation_limit: int = TRUNCATION_ROW_LIMIT
    projection: tuple[str, ...] = ()
    cadence: str = "daily"

    def __post_init__(self) -> None:
        unknown = set(self.retry_categories) - _RETRY_CATEGORIES
        if unknown:
            raise ValueError(
                f"unknown retry categories {sorted(unknown)!r}; "
                f"allowed: {sorted(_RETRY_CATEGORIES)}"
            )
        if self.truncation_limit <= 0:
            raise ValueError("truncation_limit must be positive")


def check_frame(
    descriptor: EndpointDescriptor, frame: pd.DataFrame
) -> list[dict]:
    """Frame-level checks: truncation guard + declared projection."""
    issues: list[dict] = []
    if len(frame) >= descriptor.truncation_limit and not descriptor.auto_slice:
        raise TruncationDetected(
            f"{descriptor.endpoint}: {len(frame)} rows reach the "
            f"truncation limit without auto_slice"
        )
    if descriptor.projection:
        missing = [
            column for column in descriptor.projection if column not in frame.columns
        ]
        if missing:
            issues.append({"code": "projection_missing", "columns": missing})
    return issues


def slice_plan(
    descriptor: EndpointDescriptor, frame: pd.DataFrame
) -> list[tuple[int, int]]:
    """Chunk boundaries for an ``auto_slice`` endpoint (provenance-recorded)."""
    if not descriptor.auto_slice:
        raise ValueError("slice_plan requires auto_slice: true")
    limit = descriptor.truncation_limit
    total = len(frame)
    return [(start, min(start + limit, total)) for start in range(0, total, limit)]


def contract_test_skeleton(descriptor: EndpointDescriptor) -> str:
    """A pytest skeleton asserting the declared shape (spec D4)."""
    return (
        "# Generated from the endpoint descriptor; extend, do not weaken.\n"
        "import pytest\n\n"
        f"from stock_quant.data_sources.descriptors import EndpointDescriptor\n\n"
        f"DESCRIPTOR = EndpointDescriptor(\n"
        f'    endpoint={descriptor.endpoint!r},\n'
        f'    keying={descriptor.keying!r},\n'
        f"    required_params={descriptor.required_params!r},\n"
        f"    auto_slice={descriptor.auto_slice!r},\n"
        f"    retry_categories=frozenset({sorted(descriptor.retry_categories)!r}),\n"
        f"    projection={descriptor.projection!r},\n"
        f"    cadence={descriptor.cadence!r},\n"
        ")\n\n"
        "@pytest.fixture\ndef frame():\n"
        "    ...  # fetch one real response and normalize it\n\n"
        "def test_descriptor_shape(frame):\n"
        "    assert not check_frame(DESCRIPTOR, frame)\n\n"
        "def test_projection_present(frame):\n"
        "    assert set(DESCRIPTOR.projection) <= set(frame.columns)\n"
    )
```

- [ ] **Step 4: 运行通过**

Run: `conda run -n sq312 pytest tests/unit/test_descriptors.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/stock_quant/data_sources/descriptors.py tests/unit/test_descriptors.py
git commit -m "feat(data_sources): endpoint descriptor suite with truncation guard

Descriptors declare keying/retry/projection/slicing; the framework
generates frame checks and a contract-test skeleton so new lanes
hand-write only normalize + verification (spec D4, criterion 6).

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 18（D6，并行）：官方通道金丝雀

**Files:**
- Modify: `project/verify_update_readiness.py`（新增 `--official-canary` 模式与纯函数）
- Test: `tests/unit/test_official_canary.py`

**Interfaces:**
- Consumes: `build_transport`（tushare_transport.py，check_data_sources.py:121 的既有用法）、`resolve_transport`、`TushareSource`、`PROBE_SYMBOL`/`UPDATE_START`/`UPDATE_END`（本文件既有常量）。
- Produces: `official_canary_probes(environ, config) -> dict[str, dict]`、`render_canary_record(probe_date, probes) -> str`——月度运维记录，凭证脱敏，无自动降级。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_official_canary.py
"""D6 canary: official-channel probes, redacted records, no auto-fallback."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "project")
)

from verify_update_readiness import (  # noqa: E402
    OFFICIAL_CANARY_ENDPOINTS,
    render_canary_record,
)


def test_canary_endpoint_list_is_fixed():
    assert "daily" in OFFICIAL_CANARY_ENDPOINTS


def test_record_redacts_credentials():
    probes = {
        "daily": {
            "status": "unusable",
            "note": "AuthenticationError: token TUSHARE_TOKEN=SECRETVALUE999",
        }
    }
    rendered = render_canary_record("2026-09-19", probes)
    assert "SECRETVALUE999" not in rendered
    assert "TUSHARE_TOKEN" not in rendered
    assert "daily" in rendered


def test_record_reports_endpoint_shape_only():
    probes = {"daily": {"status": "reachable", "rows": 200}}
    rendered = render_canary_record("2026-09-19", probes)
    assert "reachable" in rendered and "200" in rendered
```

- [ ] **Step 2: 运行确认失败**

Run: `conda run -n sq312 pytest tests/unit/test_official_canary.py -q`
Expected: FAIL — `OFFICIAL_CANARY_ENDPOINTS` / `render_canary_record` 不存在。

- [ ] **Step 3: 实现**

`project/verify_update_readiness.py` 追加（顶部 import 补 `from datetime import date`、`from stock_quant.data_sources.tushare_transport import build_transport`）：

```python
#: Fixed official-channel probe surface (spec D6): which endpoints answer
#: without the relay.  The list changes only by editing this constant — the
#: canary never auto-switches or auto-degrades anything.
OFFICIAL_CANARY_ENDPOINTS = ("daily",)


def official_canary_probes(
    environ=None, *, config: SourceConfig | None = None
) -> dict[str, dict]:
    """Probe api.waditu.com official transport directly, once per endpoint.

    Forced ``official`` transport — the canary exists to answer "which
    endpoints still work without the relay", so a relay/proxy fallback here
    would be a silent downgrade and is forbidden (spec D6).  Returns one row
    per endpoint: status + row count or an exception class name only.
    """
    source = os.environ if environ is None else environ
    probes: dict[str, dict] = {}
    try:
        transport = build_transport(
            "official", config if config is not None else SourceConfig()
        )
        tushare = TushareSource(
            config if config is not None else SourceConfig(),
            transport=transport,
        )
    except Exception as error:  # noqa: BLE001 - reported, never raised
        note = f"{type(error).__name__}"
        return {
            endpoint: {"status": "unusable", "note": note}
            for endpoint in OFFICIAL_CANARY_ENDPOINTS
        }
    for endpoint in OFFICIAL_CANARY_ENDPOINTS:
        try:
            frame = tushare.fetch(
                DataRequest(endpoint, (PROBE_SYMBOL,), UPDATE_START, UPDATE_END, {})
            ).frame
            probes[endpoint] = {"status": "reachable", "rows": len(frame)}
        except Exception as error:  # noqa: BLE001 - class name only, redacted
            probes[endpoint] = {
                "status": "unreachable",
                "note": type(error).__name__,
            }
    return probes


def render_canary_record(probe_date: str, probes: dict[str, dict]) -> str:
    """Ops-record body: endpoint names, status and counts — no credentials."""
    lines = [
        f"# 官方通道金丝雀 {probe_date}",
        "",
        "月度对 api.waditu.com 官方直连实测「无 relay 时哪些端点仍可得」（spec D6）。",
        "本记录只含端点名、状态与行数；不含 token/key/凭证 URL 段。",
        "本探测只读、不落任何切换：降级永远是操作者显式决定。",
        "",
    ]
    for endpoint in OFFICIAL_CANARY_ENDPOINTS:
        row = probes.get(endpoint, {"status": "not_probed"})
        lines.append(
            f"- {endpoint}: status={row.get('status')} "
            f"rows={row.get('rows', '-')}"
            + (f" note={row['note']}" if row.get("note") else "")
        )
    return "\n".join(lines) + "\n"
```

`main()` 加参数与分支（现有 argparse 之后）：

```python
    parser.add_argument(
        "--official-canary",
        action="store_true",
        help="probe official-channel endpoints and write the dated ops record",
    )
```

`run()` 开头（或 main 内）分支：

```python
    if args.official_canary:
        probes = official_canary_probes(config=config.sources["tushare"])
        record = render_canary_record(date.today().isoformat(), probes)
        destination = (
            root / "docs" / "operations"
            / f"{date.today().isoformat()}-official-channel-canary.md"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(record, encoding="utf-8")
        print(record)
        print(f"canary record -> {destination}")
        return 0
```

（`run(root, config)` 签名若要传 canary 标志，改签名并同步 `main` 调用；以文件现有组织为准，行为不变原则：canary 模式不影响既有 readiness 输出。）

- [ ] **Step 4: 运行通过**

Run: `conda run -n sq312 pytest tests/unit/test_official_canary.py tests/unit/test_tushare_transport.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add project/verify_update_readiness.py tests/unit/test_official_canary.py
git commit -m "feat(ops): monthly official-channel canary in verify_update_readiness

Probes api.waditu.com official transport with the fixed endpoint list,
writes a credential-free dated ops record, never auto-switches or
degrades (spec D6).

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 19：全量回归 + 治理核对 + 判据映射验收

**Files:**
- 无新文件（若有治理失败则修其所属文件，按失败信息最小修复）。

- [ ] **Step 1: 全量测试**

Run: `conda run -n sq312 pytest -q`
Expected: PASS。任何失败按仓库纪律处理：先读失败原因；若是本计划引入的回归，修实现；若是既有用例与规格冲突，停止并报告冲突，不擅自改测试。

- [ ] **Step 2: 治理检查**

Run:

```bash
conda run -n sq312 pytest tests/unit/test_context_governance_docs.py -v
conda run -n sq312 python tools/check_context_governance.py --root .
conda run -n sq312 python tools/check_operational_docs.py --root .
```

Expected: 全过（ADR 行宽、RUNBOOK/SCRIPTS 登记、README 链接）。

- [ ] **Step 3: 判据映射核对（对照规格 §5 逐条自证）**

| 判据 | 落点 | 验证命令 |
| --- | --- | --- |
| 1 发布侧（anchored 注入不阻断 + coverage=UNTRUSTED） | Task 6 | `pytest tests/integration/test_tiered_publication.py -q` |
| 1 消费侧（research 预检失败、工程可跑标注） | Task 8 | `pytest tests/integration/test_table_tier_preflight.py -q` |
| 2 research_only 恒拒正式研究 | Task 8 | `pytest tests/unit/test_table_tier_preflight.py -q` |
| 3 档位静态扫描 | Task 10 | `pytest tests/unit/test_tier_literals.py -q` |
| 4 as_of 前视防护 + 无 CURRENT | Task 16 | `pytest tests/unit/test_pit_as_of.py -q` |
| 5 增量段可读 + 漂移审计报警 | Task 13/15 | `pytest tests/unit/test_fetch_coverage.py tests/unit/test_drift_audit.py tests/integration/test_data_pipeline.py -q` |
| 6 描述符套件只留三样手写 | Task 17 | `pytest tests/unit/test_descriptors.py -q` |
| 7 换锚回归（1d6e43b4 FAIL→PASS、01c74bee/d490c637 不变） | Task 9 | `pytest tests/integration/test_window_anchor_regression.py -q` |
| 8 默认路径可验收 + bootstrap 预期 FAIL 非崩溃 | Task 9 | 同上（`FullHistoryAcceptanceStartMissing` 为 ValueError 子类断言） |
| 9 发布时未声明表 FATAL + 谓词 fail-closed | Task 3/5 | `pytest tests/unit/test_tiered_publication_gate.py -q` |

- [ ] **Step 4: 分批边界核对**

对照规格 §3 批次表逐项确认：B0（契约形态 + 回填）→ Task 1-3；B1（三档门禁 + ADR-010 + 谓词 + 消费端 + Factor.inputs + D5.1 换锚 + ADR-011 + RUNBOOK:80-84）→ Task 4-10；B2（增量窗口 + 表级 fetch-coverage + 漂移审计 + 调用账本 + RUNBOOK:102）→ Task 11-15；B3（D4 套件 + D3 PIT）→ Task 16-17；D6 并行 → Task 18。确认无交叉依赖缺口：B2 的所有任务在 Task 9（换锚）之后提交；B3 的所有任务在 Task 1（注册表）之后提交。

- [ ] **Step 5: 最终提交（仅当 Step 1-2 有修复时）**

```bash
git add <修复的文件>
git commit -m "test: full-suite regression fixes for the data-type expansion

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Self-Review 记录（作者自查，执行前已做）

1. **Spec coverage**：§2 D1→Task 5/6/8；D2→Task 1/2/3；D3→Task 16；D4→Task 17；D5.1→Task 9；D5.2-3→Task 12/13；D5.4→Task 15；D5.5→Task 14；D6→Task 18；§3 批次顺序由 Global Constraints + Task 依赖固化；§5 判据 1-9 映射见 Task 19 Step 3；§6 A1→Task 5 常量、A2→Task 7/8、A3→Task 12、A4→Task 17 说明、B1→Task 2 回填、B3→ADR-011、B4→Task 15 机制。非目标（§4）未被任何任务触碰。
2. **Placeholder scan**：所有实现步骤均含完整代码；唯一允许的「先读再写」点（pipeline 取数段、conftest fixture、`calls` 属性形状、`_normalize_build_config`、`_fail_universe_preflight`、RUNBOOK 行号）都给出了精确锚点、语义规则与失败时的处理路径，不构成 TBD。
3. **Type consistency**：`parse_data_contracts` → `dict[str, DataContract]`（T1 定义、T2-16 消费）；`evaluate_publication(report, *, table_tiers=None)`（T5 定义、T6 三处调用）；`_window` 返回 `tuple[date, date]`（T9 换锚、T11/T13 消费）；`FetchSegment(table, kind, window_start, window_end, reason)`（T11 定义、T12-14 消费）；`plan_table_fetch_windows(...) -> dict[str, FetchWindowPlan]`（T12 定义、T13 消费）；`table_tier_violations(contracts, input_tables, downgrade_records, mode)`（T8 定义、T14 扩展）；`as_of(context, table, symbol, field, as_of_date, *, contract)`（T16 定义）；`check_frame`/`slice_plan`/`contract_test_skeleton`（T17 定义、T17 测试消费）。已核对无漂移。
