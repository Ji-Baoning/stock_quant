# 校验与仲裁通路的批量会话通道实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"每请求一次会话"的三条通路（xingyao 日线校验车道、xingyao 因子通道、tdx 仲裁器）改为分片批量，使 xingyao 的会话数与 code 查询数从每轮 661 降到 1，同时不改变"一个标的 = 一次逻辑请求 = 一份证据"的取证契约。

**Architecture:** 新增"一次多 code 调用服务一片"的适配器能力（`XingyaoSource.fetch_batch`）：子进程只回批次原始数据与会话级错误，逐标的三态由**适配器父进程**产出，每个 `ok`/`empty` 标的仍由适配器组装自己的 `FetchResult`（`request_key`、`request_metadata`、时间戳只有适配器知道）。批量调用本身作为**独立的 `BatchRequestEvidence`** 写进内容寻址 registry（`data/raw_batch_requests/`），逐标的 raw snapshot 路径一字不改。复用仍是"先复用、后联网"：命中项不进批量。tdx 与因子通道在 reconcile 循环之前用 reconcile 的**输入帧**做一次候选池预取，懒取路径保留兜底。

**Tech Stack:** Python 3.12（pandas 3.0.5）、pytest、multiprocessing（fork）、AmazingData/tgw 私有 SDK、pytdxdata（可选）、pydantic v2。

**Spec:** [docs/superpowers/specs/2026-09-27-batched-validation-channel-design.md](../specs/2026-09-27-batched-validation-channel-design.md)（修订 4）
**ADR:** Task 11 新增 `docs/adr/020-batched-validation-channel.md`（D1–D8 见 spec §7），并以注记方式回写 ADR-016。

## Global Constraints

- 解释器用 `/home/ji/miniconda3/envs/sq312/bin/python`（pandas 3.0.5）；默认 `python` 是 py310/pandas 2.3.3，会让 dtype 类测试假红。
- 跑**点名测试文件**（每个 1 秒级），不跑裸 `pytest`（integration 全量 ≈18.5 分钟）。
- 凭据只从环境变量读（`AD_USERNAME`/`AD_PASSWORD`/`AD_HOST`/`AD_PORT`）；凭据不得进入代码、配置、日志、fixture、快照 metadata、批次证据或子进程回传值。
- 已发布数据集不可改写；`RawSnapshot` 路径布局、`request_key` 算法、`DataRequest`、`REUSABLE_CHANNELS` 一律不变。
- 不弱化任何发布/验收门禁；被拒绝的发布仍是记录在案的证据。
- 格式化只对本任务改动的行跑；**不做**全仓 `ruff format --check`（仓库在装有版本下从不干净）。
- 只 stage 本任务的文件；工作区里 RUNBOOK/cli.py/data_pipeline.py 等其他 WIP 一律不碰、不提交、不整理。
- `xingyao.enabled` 保持 `false`；**不**运行真实更新、**不**联网跑探针——联网命令一律等 owner 明确授权（Task 10）。
- 每个任务结束提交一次；提交信息英文，结尾带 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。

## 开工前必须知道的实现形态

1. **单标的 `fetch` 的语义不变**，空帧仍是 `ContractError`（[test_xingyao_source.py](../../../tests/unit/test_xingyao_source.py) 钉着它）。新语义（空 = 答案）**只属于批量路径**：`fetch_batch` 把"供应商返回零行对象"映射为 `empty`。ADR-009 的 "empty is absence, not an answer" 对单请求继续成立；本次新增的是车道级失败记账。ADR-020 D2 必须写明这条界限。
2. **子进程只回可 pickle 的原始数据，且不做任何逐标的判定。** 今天 `_fetch_kline` 在子进程里 `_to_frame` 并抛单 code 的 `ContractError`；批量 worker 不做这件事——逐 code 的 `refused` 是 spec §4 第 2 层，判定权归父进程。子进程只做**可运输性转换**（帧原样透出，非帧值折成一个标记对象），因为 fork 的返回值要过进程边界，SDK 私有对象未必可序列化。
3. **子进程里的计数器是不可见的。** fork 之后子进程对父进程对象的 `append`/`+=` 不会回到父进程，所以"一次调用服务多个 code"不能靠 `fake.calls` 断言，只能靠**回传内容**或**父进程侧钩子**（`on_attempt`）断言。测试必须按这条写。
4. **`BatchOutcome` 携带适配器组装好的 `FetchResult`**，不是裸帧：spec §5 要求 `request_key`/`request_metadata`/请求与响应时间戳留在适配器内，且同一片内时间戳一致。车道因此能和逐标的路径一样只做 `self._record_raw(outcome.result)`。
5. **批次证据由车道装配**：适配器报告 `BatchTransmission`（这一片实际请求了哪些 code、时间戳、`batch_id`），车道在落完快照后补上每个 symbol 的快照 sha 并写入 `BatchEvidenceStore`。适配器不知道快照 sha，车道不知道 SDK 时间戳，边界按此切。
6. **成本计数器挂在父进程**：`_transport_counts[name][endpoint] = {"sessions": int, "code_queries": int}`，由 `on_attempt` 钩子在**每次启动 worker 之前**累加，所以重试与二分的调用都计入。
7. **账本写在 `_result(...)`**：`update()` 有 12 处 `return self._result(...)`（publish 成功、质量阻塞、源失败、认证失败……），`_result` 是唯一的终止漏斗。spec §4 要求"一次 update 的统一终止路径写出"逐字对应的就是这里。
8. **`tests/unit/test_call_ledger.py` 有三处精确字典相等断言**（`rows["tushare"] == {...}`），新增 `transport` 段会让它们红。按 spec §4"不得放宽"的要求**更新为含新段的期望值**，不要改成子集断言。
9. **批量路径缺配置时回退逐标的**：`batch_size`/`batch_timeout_seconds` 任一为空（未由探针冻结）就**不使用**批量通路。不猜默认片大小。
10. **`docs/adr/019-price-basis-representation.md` 的 frontmatter 是坏的**（未加引号的冒号 → yaml ScannerError），会让 `pytest tests/unit/test_context_governance_docs.py -q` 4 项红。那是另一摊 WIP，本计划不修；Task 11 完成后若治理测试仍红，报告 owner 而不是顺手改它。

---

## 文件结构

**新增**

| 文件 | 职责 |
| --- | --- |
| `src/stock_quant/data_model/batch_evidence.py` | `BatchRequestEvidence` / `BatchOutcomeRecord` 模型、canonical JSON 与 `batch_id` 派生 |
| `src/stock_quant/data_sources/batch_evidence_store.py` | 独立的 `data/raw_batch_requests/**` 内容寻址 registry（save/load/load_by_sha/lookup_by_snapshot） |
| `tests/unit/test_batch_request_evidence.py` | 证据模型与 registry：内容寻址、按 sha 与按 snapshot identity 反查、outcome 绑定 |
| `tests/unit/test_xingyao_batch_lane.py` | 批量车道：复用分片、三态、片级失败、计数器、批次证据落盘、build_config 绑定 |
| `tests/unit/test_source_config_batch.py` | 两对批量字段的成对校验 |
| `project/probe_batch_channel.py` | 四项探针：两端点各自的 code 上限、缺席形态、全片耗时、计数器（Task 10，联网需授权） |

**修改**

| 文件 | 改动 |
| --- | --- |
| `src/stock_quant/data_sources/base.py` | `validate_supplier_frame` 的零行帧规则；新增 `fetch_batch_with_retry` |
| `src/stock_quant/data_sources/xingyao.py` | `fetch_batch` + `BatchOutcome`/`BatchTransmission`/`BatchResult`/`UnreadableFrame` + 批量 worker |
| `src/stock_quant/data_sources/xingyao_factor.py` | `fetch_factor_frames`（多 code 分片）+ 单标的路径委托 |
| `src/stock_quant/config.py` | 两对批量字段与成对校验 |
| `src/stock_quant/data_pipeline.py` | 批量校验车道、候选池预取、`_transport_counts`、批次证据装配与 `build_config` 绑定、`_result` 写账本、`dataset_build_config(batch_request_evidence=...)` |
| `src/stock_quant/data_model/call_ledger.py` | `transport` 段渲染 |
| `project/drift_audit.py` | 按批次原形重放 + 按 code 拆回逐标的比较 |
| `project/configs/sources.yml`、`templates/project-config/sources.yml` | xingyao 段两对批量字段注释 |
| `tests/unit/{test_xingyao_source,test_xingyao_factor,test_tdx_arbiter,test_call_ledger,test_drift_audit}.py` | 既有断言的同步更新（`test_xingyao_source.py` 的假 SDK 改多 code 形状） |
| `tests/integration/test_source_contracts.py` | 零行帧校验规则的契约用例 |
| `RUNBOOK.md`、`docs/architecture/data-flow.md`、`docs/adr/DECISIONS_INDEX.md`、`docs/adr/016-*.md` | Task 11 文档同步 |

---

## Task 1: 零行帧的校验规则（`allow_empty` 的语义补全）

**Files:**
- Modify: `src/stock_quant/data_sources/base.py`（`validate_supplier_frame` 函数体）
- Test: `tests/integration/test_source_contracts.py`

**Interfaces:**
- Consumes: 无（本任务独立）
- Produces: `validate_supplier_frame(..., allow_empty=True)` 的新语义——零行帧仍要求 supplier frame、未标 `truncated`、必需的 symbol/date **列存在**，但跳过"列值推出的标的集合相等"与"日期值落在窗口内"两项。`allow_empty=False` 的行为一字不变。

- [ ] **Step 1: 写失败的契约测试**

追加到 `tests/integration/test_source_contracts.py` 末尾（若缺 `pd`/`date`/`pytest`/`ContractError`/`DataRequest`/`validate_supplier_frame` 的 import，补齐）：

```python
def test_a_zero_row_frame_passes_when_empty_is_allowed():
    """A supplier that answered a zero-row object answered (ADR-020 D7).

    The columns are the supplier's; there are no values, so neither the
    requested-symbol set nor the requested window can be checked against
    anything.  The presence checks still run: a zero-row frame without the
    symbol or date column is still a contract break.
    """
    request = DataRequest("daily", ("000001.SZ",), date(2024, 1, 2), date(2024, 1, 5))
    empty = pd.DataFrame(columns=["code", "kline_time", "close"])
    validate_supplier_frame(
        empty,
        request,
        symbol_columns=("code",),
        date_columns=("kline_time",),
        allow_empty=True,
    )


def test_a_zero_row_frame_still_needs_its_required_columns():
    request = DataRequest("daily", ("000001.SZ",), date(2024, 1, 2), date(2024, 1, 5))
    with pytest.raises(ContractError, match="date column"):
        validate_supplier_frame(
            pd.DataFrame(columns=["code", "close"]),
            request,
            symbol_columns=("code",),
            date_columns=("kline_time",),
            allow_empty=True,
        )


def test_an_empty_frame_is_still_refused_without_allow_empty():
    request = DataRequest("daily", ("000001.SZ",), date(2024, 1, 2), date(2024, 1, 5))
    with pytest.raises(ContractError, match="empty response"):
        validate_supplier_frame(
            pd.DataFrame(columns=["code", "kline_time"]),
            request,
            symbol_columns=("code",),
            date_columns=("kline_time",),
        )


def test_a_non_empty_frame_keeps_the_symbol_set_check():
    """The relaxed rule must not leak into the ordinary path."""
    request = DataRequest("daily", ("000001.SZ",), date(2024, 1, 2), date(2024, 1, 5))
    wrong = pd.DataFrame(
        [{"code": "600000.SH", "kline_time": "2024-01-02", "close": 1.0}]
    )
    with pytest.raises(ContractError, match="each requested symbol"):
        validate_supplier_frame(
            wrong,
            request,
            symbol_columns=("code",),
            date_columns=("kline_time",),
            allow_empty=True,
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_source_contracts.py -q -k "zero_row or empty_frame or symbol_set"`
Expected: `test_a_zero_row_frame_passes_when_empty_is_allowed` FAIL — `ContractError: supplier response does not contain each requested symbol`；其余三条 PASS。

- [ ] **Step 3: 实现**

`src/stock_quant/data_sources/base.py`，把 `validate_supplier_frame` 的 225 行以下函数体改为（上方 isinstance/empty/truncated 三段保持原样）：

```python
    symbol_column = _first_present(frame, symbol_columns)
    if require_symbol and symbol_column is None:
        raise ContractError("supplier response has no symbol column")

    date_column = _first_present(frame, date_columns)
    if require_date and date_column is None:
        raise ContractError("supplier response has no date column")

    if frame.empty:
        # A zero-row frame the caller allowed is an answer with no values in
        # it (ADR-020 D7): the column *presence* checks above are all that can
        # be checked.  Comparing the requested symbol set against a frame with
        # no rows would compare it against the empty set, and there are no
        # dates to range-check -- ``allow_empty`` alone never reached this
        # point, because the set comparison below raised first.
        return

    if require_symbol:
        returned_symbols = {
            _comparison_symbol(value) for value in frame[symbol_column].dropna()
        }
        requested_symbols = {_comparison_symbol(symbol) for symbol in request.symbols}
        if requested_symbols != returned_symbols:
            raise ContractError(
                "supplier response does not contain each requested symbol"
            )

    if date_column is not None:
        raw_dates = frame[date_column].astype(str)
        if raw_dates.str.fullmatch(r"\d{8}").all():
            dates = pd.to_datetime(raw_dates, format="%Y%m%d", errors="coerce")
        else:
            dates = pd.to_datetime(raw_dates, errors="coerce")
        if dates.isna().any():
            raise ContractError("supplier response has an invalid date")
        start = pd.Timestamp(request.start_date)
        end = pd.Timestamp(request.end_date)
        if ((dates < start) | (dates > end)).any():
            raise ContractError(
                "supplier response falls outside the requested date range"
            )
```

- [ ] **Step 4: 跑测试确认通过，并跑受影响的邻居**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_source_contracts.py -q`
再跑 `grep -rl "validate_supplier_frame" tests/ | head` 找到直接使用方，逐个点名跑一遍。
Expected: PASS（本任务新增 4 项 + 该文件既有用例全绿）。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/data_sources/base.py tests/integration/test_source_contracts.py
git commit -m "feat(sources): give allow_empty a defined meaning for zero-row frames

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 2: 批次请求证据模型与内容寻址 registry

**Files:**
- Create: `src/stock_quant/data_model/batch_evidence.py`
- Create: `src/stock_quant/data_sources/batch_evidence_store.py`
- Test: `tests/unit/test_batch_request_evidence.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `BatchOutcomeRecord(symbol, request_key, outcome, snapshot_file_sha256=None, message="")`，`outcome ∈ {"ok","empty","refused"}`
  - `BatchRequestEvidence(source, endpoint, transport_id, batch_id, batch_request_parameters, request_timestamp, response_timestamp, outcomes)`，`frozen=True`；`.payload() -> dict`；`.sha256 -> str`；`.from_payload(dict)`
  - `batch_request_parameters(endpoint, symbols, start_date, end_date, params) -> str`；`batch_id_for(parameters) -> str`；`canonical_json(payload) -> str`
  - `BatchEvidenceStore(project_root)`：`save(evidence) -> Path`、`load(source, endpoint, transport_id, batch_id, sha256) -> BatchRequestEvidence | None`、`load_by_sha(sha256) -> BatchRequestEvidence | None`、`lookup_by_snapshot(source, endpoint, request_key, sha256) -> BatchRequestEvidence | None`、`path_for(...) -> Path`

- [ ] **Step 1: 写失败的测试**

`tests/unit/test_batch_request_evidence.py`：

```python
"""Batch transmission provenance (spec §5, ADR-020 D8)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from stock_quant.data_model.batch_evidence import (
    OUTCOME_EMPTY,
    OUTCOME_OK,
    OUTCOME_REFUSED,
    BatchOutcomeRecord,
    BatchRequestEvidence,
    batch_id_for,
    batch_request_parameters,
)
from stock_quant.data_sources.batch_evidence_store import BatchEvidenceStore

_START = date(2024, 1, 2)
_END = date(2024, 1, 5)
_PARAMS = batch_request_parameters(
    "daily", ("000001.SZ", "600000.SH"), _START, _END, {"adjustment": "unadjusted"}
)


def _evidence(**overrides) -> BatchRequestEvidence:
    fields = {
        "source": "xingyao",
        "endpoint": "daily",
        "transport_id": "xingyao-broker-tcp",
        "batch_id": batch_id_for(_PARAMS),
        "batch_request_parameters": _PARAMS,
        "request_timestamp": "2024-01-05T09:00:00+00:00",
        "response_timestamp": "2024-01-05T09:00:07+00:00",
        "outcomes": (
            BatchOutcomeRecord("000001.SZ", "a" * 64, OUTCOME_OK, "b" * 64),
            BatchOutcomeRecord("600000.SH", "c" * 64, OUTCOME_REFUSED, None, "no key"),
        ),
    }
    fields.update(overrides)
    return BatchRequestEvidence(**fields)


def test_the_recorded_request_is_the_whole_ordered_chunk():
    """The batch shape, not just the per-symbol shapes, must be rebuildable."""
    assert json.loads(_evidence().batch_request_parameters) == {
        "endpoint": "daily",
        "symbols": ["000001.SZ", "600000.SH"],
        "start_date": "2024-01-02",
        "end_date": "2024-01-05",
        "params": {"adjustment": "unadjusted"},
    }


def test_the_batch_id_is_the_canonical_hash_of_its_parameters():
    evidence = _evidence()
    assert evidence.batch_id == batch_id_for(evidence.batch_request_parameters)


def test_the_evidence_hash_covers_the_outcomes():
    """A different outcome vector is different evidence, not the same record."""
    first = _evidence()
    second = _evidence(
        outcomes=(
            BatchOutcomeRecord("000001.SZ", "a" * 64, OUTCOME_OK, "b" * 64),
            BatchOutcomeRecord("600000.SH", "c" * 64, OUTCOME_EMPTY, None),
        )
    )
    assert first.sha256 != second.sha256


def test_a_refused_outcome_carries_no_snapshot_identity():
    refused = [o for o in _evidence().outcomes if o.outcome == OUTCOME_REFUSED][0]
    assert refused.snapshot_file_sha256 is None
    assert refused.message


def test_an_ok_outcome_must_carry_a_snapshot_identity():
    with pytest.raises(ValueError, match="snapshot identity"):
        BatchOutcomeRecord("000001.SZ", "a" * 64, OUTCOME_OK, None)


def test_saving_twice_writes_one_content_addressed_record(tmp_path):
    store = BatchEvidenceStore(tmp_path)
    first = store.save(_evidence())
    second = store.save(_evidence())
    assert first == second
    assert first.name == f"{_evidence().sha256}.json"
    assert json.loads(first.read_text())["batch_id"] == _evidence().batch_id


def test_the_registry_is_not_the_raw_snapshot_tree(tmp_path):
    """Batch evidence must not live under data/raw: RawStore owns that tree."""
    path = BatchEvidenceStore(tmp_path).save(_evidence())
    relative = path.relative_to(tmp_path)
    assert relative.parts[0] == "data"
    assert relative.parts[1] == "raw_batch_requests"


def test_loading_a_missing_record_is_none_not_an_error(tmp_path):
    store = BatchEvidenceStore(tmp_path)
    assert (
        store.load(
            "xingyao", "daily", "xingyao-broker-tcp", _evidence().batch_id, "f" * 64
        )
        is None
    )


def test_a_loaded_record_round_trips_its_outcomes(tmp_path):
    store = BatchEvidenceStore(tmp_path)
    store.save(_evidence())
    loaded = store.load(
        "xingyao", "daily", "xingyao-broker-tcp", _evidence().batch_id, _evidence().sha256
    )
    assert loaded is not None
    assert loaded.outcomes == _evidence().outcomes


def test_a_tampered_record_is_refused(tmp_path):
    store = BatchEvidenceStore(tmp_path)
    path = store.save(_evidence())
    payload = json.loads(path.read_text())
    payload["outcomes"][0]["symbol"] = "999999.SZ"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="hash mismatch"):
        store.load(
            "xingyao",
            "daily",
            "xingyao-broker-tcp",
            _evidence().batch_id,
            _evidence().sha256,
        )


def test_a_record_can_be_loaded_by_its_hash_alone(tmp_path):
    """The drift audit binds hashes, not paths: it must resolve them."""
    store = BatchEvidenceStore(tmp_path)
    store.save(_evidence())
    loaded = store.load_by_sha(_evidence().sha256)
    assert loaded is not None
    assert loaded.batch_id == _evidence().batch_id
    assert store.load_by_sha("f" * 64) is None


def test_a_snapshot_can_be_traced_back_to_the_batch_that_produced_it(tmp_path):
    """A reused snapshot must carry its batch provenance back (spec §5)."""
    store = BatchEvidenceStore(tmp_path)
    store.save(_evidence())
    found = store.lookup_by_snapshot("xingyao", "daily", "a" * 64, "b" * 64)
    assert found is not None
    assert found.batch_id == _evidence().batch_id


def test_a_snapshot_from_no_batch_has_no_record(tmp_path):
    store = BatchEvidenceStore(tmp_path)
    store.save(_evidence())
    assert store.lookup_by_snapshot("xingyao", "daily", "0" * 64, "0" * 64) is None


def test_a_snapshot_from_another_endpoint_has_no_record(tmp_path):
    """The trace-back is scoped: a same-key snapshot elsewhere is a different read."""
    store = BatchEvidenceStore(tmp_path)
    store.save(_evidence())
    assert (
        store.lookup_by_snapshot("xingyao", "backward_factor", "a" * 64, "b" * 64)
        is None
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_batch_request_evidence.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stock_quant.data_model.batch_evidence'`

- [ ] **Step 3: 实现模型**

`src/stock_quant/data_model/batch_evidence.py`：

```python
"""The real transport request behind a batch of per-symbol evidence.

A logical request is one symbol; a batch call answers many at once.  Reuse,
the raw snapshot path and ``request_key`` stay keyed by the logical request
(ADR-015, unchanged), so the batch itself has to be recorded somewhere else:
this module is that record.  Its ``batch_id`` is the canonical hash of the
parameters the call actually carried, which is what makes a later replay
comparable -- a per-symbol digest cannot express "these codes travelled
together", and an absent key or a truncated answer may depend on that
grouping.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date

#: Outcome vocabulary, shared with the lane's per-symbol state.
OUTCOME_OK = "ok"
OUTCOME_EMPTY = "empty"
OUTCOME_REFUSED = "refused"

OUTCOMES = (OUTCOME_OK, OUTCOME_EMPTY, OUTCOME_REFUSED)


def canonical_json(payload: object) -> str:
    """One serialization for every hash and every stored record."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def batch_request_parameters(
    endpoint: str,
    symbols: tuple[str, ...],
    start_date: date,
    end_date: date,
    params: dict[str, str],
) -> str:
    """The chunk as it was asked, in a rebuildable canonical form."""
    return canonical_json(
        {
            "endpoint": endpoint,
            "symbols": list(symbols),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "params": dict(params),
        }
    )


def batch_id_for(parameters: str) -> str:
    """The batch identity: the hash of the parameters string as given."""
    return hashlib.sha256(parameters.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BatchOutcomeRecord:
    """One requested symbol's outcome inside a batch call.

    ``snapshot_file_sha256`` is the content address of the per-symbol raw
    snapshot this outcome produced, or ``None`` for ``refused`` -- where no
    supplier object exists, so no snapshot may be fabricated for it.
    """

    symbol: str
    request_key: str
    outcome: str
    snapshot_file_sha256: str | None = None
    message: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown batch outcome {self.outcome!r}")
        if (self.outcome == OUTCOME_REFUSED) != (self.snapshot_file_sha256 is None):
            raise ValueError("only a refused outcome may lack a snapshot identity")


@dataclass(frozen=True)
class BatchRequestEvidence:
    """One actual supplier transmission and what it produced, per symbol."""

    source: str
    endpoint: str
    transport_id: str
    batch_id: str
    batch_request_parameters: str
    request_timestamp: str
    response_timestamp: str
    outcomes: tuple[BatchOutcomeRecord, ...]

    def payload(self) -> dict[str, object]:
        return {
            "source": self.source,
            "endpoint": self.endpoint,
            "transport_id": self.transport_id,
            "batch_id": self.batch_id,
            "batch_request_parameters": self.batch_request_parameters,
            "request_timestamp": self.request_timestamp,
            "response_timestamp": self.response_timestamp,
            "outcomes": [asdict(outcome) for outcome in self.outcomes],
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(
            canonical_json(self.payload()).encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "BatchRequestEvidence":
        rows = payload["outcomes"]
        assert isinstance(rows, list)
        return cls(
            source=str(payload["source"]),
            endpoint=str(payload["endpoint"]),
            transport_id=str(payload["transport_id"]),
            batch_id=str(payload["batch_id"]),
            batch_request_parameters=str(payload["batch_request_parameters"]),
            request_timestamp=str(payload["request_timestamp"]),
            response_timestamp=str(payload["response_timestamp"]),
            outcomes=tuple(
                BatchOutcomeRecord(
                    symbol=str(row["symbol"]),
                    request_key=str(row["request_key"]),
                    outcome=str(row["outcome"]),
                    snapshot_file_sha256=(
                        None
                        if row.get("snapshot_file_sha256") is None
                        else str(row["snapshot_file_sha256"])
                    ),
                    message=str(row.get("message", "")),
                )
                for row in rows
            ),
        )
```

- [ ] **Step 4: 实现 registry**

`src/stock_quant/data_sources/batch_evidence_store.py`：

```python
"""Content-addressed store for batch transmission evidence.

Deliberately *not* the raw snapshot tree: ``RawStore.save`` reuses an existing
``<request_key>/<file_sha256>`` directory and reads the stored manifest back
instead of writing a new one, so batch metadata welded onto a per-symbol
manifest would be silently dropped whenever those exact bytes had already been
stored by a single-symbol request.  Batch provenance gets its own immutable
tree instead.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from stock_quant.data_model.batch_evidence import (
    BatchRequestEvidence,
    canonical_json,
)

_ROOT = ("data", "raw_batch_requests")


class BatchEvidenceStore:
    """``data/raw_batch_requests/<source>/<endpoint>/<transport>/<batch_id>/<sha>.json``."""

    def __init__(self, project_root: Path) -> None:
        self._root = Path(project_root).joinpath(*_ROOT)

    def path_for(
        self, source: str, endpoint: str, transport_id: str, batch_id: str, sha256: str
    ) -> Path:
        return (
            self._root / source / endpoint / transport_id / batch_id / f"{sha256}.json"
        )

    def save(self, evidence: BatchRequestEvidence) -> Path:
        path = self.path_for(
            evidence.source,
            evidence.endpoint,
            evidence.transport_id,
            evidence.batch_id,
            evidence.sha256,
        )
        if path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json(evidence.payload()) + "\n", encoding="utf-8")
        return path

    def load(
        self, source: str, endpoint: str, transport_id: str, batch_id: str, sha256: str
    ) -> BatchRequestEvidence | None:
        path = self.path_for(source, endpoint, transport_id, batch_id, sha256)
        if not path.is_file():
            return None
        return self._read(path, sha256)

    def load_by_sha(self, sha256: str) -> BatchRequestEvidence | None:
        """Resolve a recorded evidence hash without knowing its batch path.

        The published ``build_config.batch_request_evidence`` stores hashes,
        so a reader (the drift audit) has nothing but the hash to start from.
        """
        if not self._root.is_dir():
            return None
        for path in sorted(self._root.rglob(f"{sha256}.json")):
            return self._read(path, sha256)
        return None

    def lookup_by_snapshot(
        self,
        source: str,
        endpoint: str,
        request_key: str,
        sha256: str,
    ) -> BatchRequestEvidence | None:
        """The batch record that produced this snapshot, or ``None``.

        A snapshot read back from the store must carry the batch it came from
        (spec §5); its per-symbol manifest cannot hold that (``RawStore``
        dedupe), so it is resolved from the batch registry instead.  The scan
        is bounded by one source x endpoint subtree, and deliberately does not
        name a transport: the caller is the pipeline, which must not have to
        know which adapter answered.
        """
        base = self._root / source / endpoint
        if not base.is_dir():
            return None
        for path in sorted(base.glob("*/*/*.json")):
            evidence = self._read(path, path.stem)
            if any(
                outcome.request_key == request_key
                and outcome.snapshot_file_sha256 == sha256
                for outcome in evidence.outcomes
            ):
                return evidence
        return None

    def _read(self, path: Path, sha256: str) -> BatchRequestEvidence:
        raw = path.read_text(encoding="utf-8")
        if hashlib.sha256(raw.strip().encode("utf-8")).hexdigest() != sha256:
            raise ValueError("batch evidence hash mismatch")
        payload = json.loads(raw)
        return BatchRequestEvidence.from_payload(payload)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_batch_request_evidence.py -q`
Expected: PASS（14 项）。

- [ ] **Step 6: 提交**

```bash
git add src/stock_quant/data_model/batch_evidence.py src/stock_quant/data_sources/batch_evidence_store.py tests/unit/test_batch_request_evidence.py
git commit -m "feat(evidence): record the batch transmission behind per-symbol evidence

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 3: tdx 仲裁器候选池预取

**Files:**
- Modify: `src/stock_quant/data_pipeline.py`（`_LazyActionArbiter` 新增 `prefetch`/`_store_frame`）
- Modify: `src/stock_quant/data_pipeline.py`（per-symbol CA 循环结束处与 `_build_action_arbiter` 之间接线）
- Test: `tests/unit/test_tdx_arbiter.py`

**Interfaces:**
- Consumes: `fetch_xdxr_frames(symbols, *, timeout, servers=None) -> dict[str, pd.DataFrame]`（既有）、`_warn_arbiter_failure(issues, symbol, error)`（既有）
- Produces: `_LazyActionArbiter.prefetch(symbols) -> None`；模块级 `_candidate_symbols(frames_by_symbol) -> list[str]`；`DataPipeline._build_action_arbiter(..., candidates: Sequence[str] = ())`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/unit/test_tdx_arbiter.py` 末尾（沿用该文件既有的 `_StubConfig`/`_xdxr`；补齐 `from stock_quant.data_sources.tdx import TdxUnavailableError`）：

```python
def test_a_prefetch_asks_for_the_whole_candidate_pool_in_one_channel_call(monkeypatch):
    """One session for the round, not one per symbol (ADR-020 D5).

    The pool is a superset of the symbols that end up arbitrated, so this
    genuinely asks the channel about more codes than the lazy path would --
    the win is session count, and it is the only win.
    """
    calls: list[list[str]] = []

    def fake_fetch(symbols, *, timeout=30.0, servers=None):
        calls.append(list(symbols))
        return {symbol: _xdxr() for symbol in symbols}

    from stock_quant import data_pipeline

    monkeypatch.setattr(data_pipeline, "fetch_xdxr_frames", fake_fetch)
    lazy = data_pipeline._LazyActionArbiter(
        _StubConfig(),
        date(2020, 1, 1),
        date(2020, 12, 31),
        issues=[],
        raw_snapshots=[],
        record_raw=lambda result: result,
    )

    lazy.prefetch(["600519.SH", "000001.SZ"])

    assert calls == [["600519.SH", "000001.SZ"]]
    # A prefetched symbol is answered from cache: no second channel call.
    assert lazy.frame_for("600519.SH") is not None
    assert calls == [["600519.SH", "000001.SZ"]]


def test_a_prefetch_failure_degrades_to_the_lazy_path(monkeypatch):
    """An unreachable channel must not fail the run; it must stay fail-closed."""
    calls: list[list[str]] = []

    def fake_fetch(symbols, *, timeout=30.0, servers=None):
        calls.append(list(symbols))
        raise TdxUnavailableError("channel down")

    from stock_quant import data_pipeline

    monkeypatch.setattr(data_pipeline, "fetch_xdxr_frames", fake_fetch)
    issues: list = []
    lazy = data_pipeline._LazyActionArbiter(
        _StubConfig(),
        date(2020, 1, 1),
        date(2020, 12, 31),
        issues=issues,
        raw_snapshots=[],
        record_raw=lambda result: result,
    )

    lazy.prefetch(["600519.SH"])

    # The failure is remembered, exactly as ``frame_for``'s own failure is: the
    # symbol is not asked again within the run, and it still counts as an
    # absent channel (which asserts nothing, ADR-009).
    assert lazy.frame_for("600519.SH") is None
    assert calls == [["600519.SH"]]
    assert [i for i in issues if i.details.get("source") == data_pipeline.ARBITER_NAME]


def test_a_prefetched_symbol_is_never_re_asked(monkeypatch):
    """The cache must answer, not fall through to a second channel call."""
    calls: list[list[str]] = []

    def fake_fetch(symbols, *, timeout=30.0, servers=None):  # pragma: no cover
        calls.append(list(symbols))
        return {symbol: _xdxr() for symbol in symbols}

    from stock_quant import data_pipeline

    monkeypatch.setattr(data_pipeline, "fetch_xdxr_frames", fake_fetch)
    lazy = data_pipeline._LazyActionArbiter(
        _StubConfig(),
        date(2020, 1, 1),
        date(2020, 12, 31),
        issues=[],
        raw_snapshots=[],
        record_raw=lambda result: result,
    )

    lazy.prefetch(["600519.SH"])
    calls.clear()
    lazy.prefetch(["600519.SH"])

    assert calls == []


def test_an_empty_prefetch_builds_no_session(monkeypatch):
    """A round with no disputed symbol makes none -- the sources.yml comment."""
    calls: list[list[str]] = []

    def fake_fetch(symbols, *, timeout=30.0, servers=None):  # pragma: no cover
        calls.append(list(symbols))
        return {}

    from stock_quant import data_pipeline

    monkeypatch.setattr(data_pipeline, "fetch_xdxr_frames", fake_fetch)
    lazy = data_pipeline._LazyActionArbiter(
        _StubConfig(),
        date(2020, 1, 1),
        date(2020, 12, 31),
        issues=[],
        raw_snapshots=[],
        record_raw=lambda result: result,
    )

    lazy.prefetch([])

    assert calls == []


def test_the_candidate_pool_is_the_symbols_with_action_rows():
    from stock_quant import data_pipeline

    frames = {
        "600519.SH": {"cninfo": [_xdxr()], "eastmoney": [], ARBITER_NAME: []},
        "000001.SZ": {"cninfo": [], "eastmoney": [], ARBITER_NAME: []},
    }
    assert data_pipeline._candidate_symbols(frames) == ["600519.SH"]
    assert data_pipeline._candidate_symbols({}) == []


def test_the_candidate_pool_reaches_the_lazy_arbiter_through_its_wrappers(
    tmp_path, monkeypatch
):
    """``_build_action_arbiter`` returns ``_GuardedArbiter(FirstAnsweringArbiter(...))``.

    The lazy arbiter is two layers inside that, and the wrapper exposes
    ``frame_for`` but not ``prefetch`` -- so a prefetch wired at the call site
    would silently do nothing at all.  It has to happen where the concrete
    object is built, and this test is what says so.
    """
    import shutil

    from stock_quant.bootstrap import bootstrap_dataset
    from stock_quant.config import SourceConfig
    from stock_quant.data_pipeline import DataPipeline
    from stock_quant import data_pipeline

    repo_root = Path(__file__).resolve().parents[2]
    root = tmp_path / "project"
    configs = root / "configs"
    configs.mkdir(parents=True)
    for name in (
        "project.yml",
        "sources.yml",
        "costs.yml",
        "trading_rules.yml",
        "universe.yml",
    ):
        shutil.copy(repo_root / "templates" / "project-config" / name, configs / name)
    bootstrap_dataset(root)

    calls: list[list[str]] = []

    def fake_fetch(symbols, *, timeout=30.0, servers=None):
        calls.append(list(symbols))
        return {symbol: _xdxr() for symbol in symbols}

    monkeypatch.setattr(data_pipeline, "fetch_xdxr_frames", fake_fetch)
    pipeline = DataPipeline(root, sources={})
    pipeline._project_config = pipeline._project_config.model_copy(
        update={
            "sources": {
                **pipeline._project_config.sources,
                data_pipeline.ARBITER_NAME: SourceConfig(enabled=True),
            }
        }
    )
    # Isolate the tdx lane: the price lane's open_days needs are not this test's.
    monkeypatch.setattr(pipeline, "_build_price_channel", lambda *a, **k: None)

    pipeline._build_action_arbiter(
        ["600519.SH"], date(2020, 1, 1), date(2020, 12, 31), [], [], []
    )

    assert calls == [["600519.SH"]]
```

补齐 import：`from pathlib import Path`、`SourceConfig`（若缺）。

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_tdx_arbiter.py -q -k "prefetch or candidate_pool"`
Expected: FAIL — `AttributeError: '_LazyActionArbiter' object has no attribute 'prefetch'`

- [ ] **Step 3: 实现 `prefetch`**

在 `src/stock_quant/data_pipeline.py` 的 `_LazyActionArbiter.frame_for` 之前插入：

```python
    def prefetch(self, symbols: Iterable[str]) -> None:
        """Ask the channel about a whole candidate pool in one session.

        The pool is a conservative superset of the symbols that will actually
        be arbitrated (it comes from the reconcile input frames), so this can
        only ever warm the cache -- it decides nothing.  A failed read is
        remembered exactly as ``frame_for``'s own failure is: the symbol is
        not asked again within the run, and an absent channel asserts nothing
        (ADR-009).
        """
        pending = [
            symbol
            for symbol in dict.fromkeys(symbols)
            if symbol not in self._frames and symbol not in self._failed
        ]
        if not pending:
            return
        try:
            frames = fetch_xdxr_frames(
                pending, timeout=float(self._config.timeout_seconds)
            )
        except Exception as error:  # noqa: BLE001 - best-effort third opinion
            for symbol in pending:
                _warn_arbiter_failure(self._issues, symbol, error)
                self._failed.add(symbol)
            return
        for symbol in pending:
            frame = frames.get(symbol)
            if frame is not None and not frame.empty:
                self._frames[symbol] = frame
                self._store_frame(symbol, frame)
            else:
                # Same cache shape the lazy path stores for a missing frame.
                self._frames[symbol] = pd.DataFrame()
```

新增私有方法（把 `frame_for` 里记录快照的那段原样搬进来）：

```python
    def _store_frame(self, symbol: str, frame: pd.DataFrame) -> None:
        """Record the channel's answer as the same raw evidence the lazy path records."""
        self._raw_snapshots.append(
            self._record_raw(
                FetchResult(
                    source=ARBITER_NAME,
                    endpoint=XDXR_ENDPOINT,
                    request_key=request_key(
                        DataRequest(XDXR_ENDPOINT, (symbol,), self._start, self._end)
                    ),
                    frame=frame,
                    metadata={"transport_id": ARBITER_NAME},
                )
            )
        )
```

并把 `frame_for` 里那段替换为 `self._store_frame(symbol, frame)`。

- [ ] **Step 4: 接线候选池**

`src/stock_quant/data_pipeline.py`，在 per-symbol CA 循环结束之后、`arbiter = self._build_action_arbiter(` 调用**之前**插入：

```python
        # Candidate pool for the two lazily-consulted channels (ADR-020 D5):
        # derived from the reconcile *input* frames, so it exists before the
        # arbiter that consumes it is built -- deriving it from reconcile
        # output would be circular.  It is a conservative superset: which
        # symbols are actually disputed is only known after reconciling.
        candidates = _candidate_symbols(frames_by_symbol)
```

构建段改为（**预取必须在具体懒对象被构造的地方发生**，见下）：`update()` 里的三行现在是

```python
        arbiter = self._build_action_arbiter(
            symbols, start, end, issues, raw_snapshots, open_days
        )
        factor_channel = self._build_factor_channel(end, issues, raw_snapshots)
        price_basis = self._build_price_basis_settler(
            arbiter, factor_channel, issues, raw_snapshots
        )
```

只把第一行改为传 `candidates`（因子通道的实参由 Task 8 加，那一步同时改这一行）：

```python
        arbiter = self._build_action_arbiter(
            symbols, start, end, issues, raw_snapshots, open_days, candidates
        )
        factor_channel = self._build_factor_channel(end, issues, raw_snapshots)
        price_basis = self._build_price_basis_settler(
            arbiter, factor_channel, issues, raw_snapshots
        )
```

`_build_action_arbiter` 的签名末尾加 `candidates: Sequence[str] = ()`，并在它构造 `_LazyActionArbiter` 的两行之间插入预取：

```python
        config = self._project_config.sources.get(ARBITER_NAME)
        if config is not None and config.enabled:
            lazy = _LazyActionArbiter(
                config,
                start,
                end,
                issues=issues,
                raw_snapshots=raw_snapshots,
                record_raw=self._record_raw,
            )
            # Here, not at the call site: this method returns a
            # ``_GuardedArbiter(FirstAnsweringArbiter(...))``, which delegates
            # ``frame_for`` but has no ``prefetch`` -- delivering the pool
            # through the returned object would silently prefetch nothing.
            lazy.prefetch(candidates)
            arbiters.append(lazy)
```

`candidates` 默认为空是给测试与其它调用点的安全网（`prefetch` 对空池自我短路）；`update()` 那一个生产调用点**必须**传它。

模块级 helper 放在 `_stack_action_frames` 之前：

```python
def _candidate_symbols(
    frames_by_symbol: Mapping[str, Mapping[str, list[pd.DataFrame]]],
) -> list[str]:
    """Symbols with at least one corporate-action row in the window.

    Ordered by the input mapping (which follows the requested symbol order),
    so the batch request is reproducible run over run.
    """
    return [
        symbol
        for symbol, by_endpoint in frames_by_symbol.items()
        if any(frames for frames in by_endpoint.values())
    ]
```

- [ ] **Step 5: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_tdx_arbiter.py -q`
Expected: PASS（含既有的"干净轮不建会话"与"冲突轮只问一次"断言）。

- [ ] **Step 6: 提交**

```bash
git add src/stock_quant/data_pipeline.py tests/unit/test_tdx_arbiter.py
git commit -m "feat(arbiter): warm the tdx channel from the reconcile input pool

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 4: 两对批量配置字段

**Files:**
- Modify: `src/stock_quant/config.py`（`SourceConfig`）
- Modify: `project/configs/sources.yml`、`templates/project-config/sources.yml`（xingyao 段注释）
- Test: `tests/unit/test_source_config_batch.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces: `SourceConfig.batch_size`、`batch_timeout_seconds`、`factor_batch_size`、`factor_batch_timeout_seconds`（均可为 `None`）；成对校验——只给一半即 `ValidationError`。

- [ ] **Step 1: 写失败的测试**

`tests/unit/test_source_config_batch.py`：

```python
"""The two batch field pairs (spec §5)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stock_quant.config import SourceConfig


def test_both_pairs_default_off():
    config = SourceConfig()
    assert config.batch_size is None
    assert config.batch_timeout_seconds is None
    assert config.factor_batch_size is None
    assert config.factor_batch_timeout_seconds is None


def test_a_pair_may_be_configured_together():
    config = SourceConfig(batch_size=1000, batch_timeout_seconds=180)
    assert config.batch_size == 1000
    assert config.batch_timeout_seconds == 180


def test_a_size_without_its_timeout_is_a_configuration_error():
    with pytest.raises(ValidationError, match="batch_timeout_seconds"):
        SourceConfig(batch_size=1000)


def test_a_timeout_without_its_size_is_a_configuration_error():
    with pytest.raises(ValidationError, match="batch_size"):
        SourceConfig(batch_timeout_seconds=180)


def test_the_factor_pair_is_validated_independently():
    """Endpoints are probed separately; one pair never configures the other."""
    with pytest.raises(ValidationError, match="factor_batch_timeout_seconds"):
        SourceConfig(factor_batch_size=200)


def test_batch_sizes_must_be_positive():
    with pytest.raises(ValidationError):
        SourceConfig(batch_size=0, batch_timeout_seconds=180)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_source_config_batch.py -q`
Expected: FAIL — pydantic 报未知字段（`extra="forbid"`）。

- [ ] **Step 3: 实现**

`src/stock_quant/config.py` 的 `SourceConfig` 增加字段与校验（现有字段与 `extra="forbid"` 不动）：

```python
    #: Batch fields come in pairs and are off unless both are set: a size with
    #: no process bound would let one call hold the run, and a bound with no
    #: size cannot be applied.  Unset means the batch channel is not used at
    #: all (the lane falls back to per-symbol requests); the defaults are
    #: frozen from the batch-channel probes (docs/operations/), never guessed
    #: here.
    batch_size: int | None = Field(default=None, ge=1, le=1000)
    batch_timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    factor_batch_size: int | None = Field(default=None, ge=1, le=1000)
    factor_batch_timeout_seconds: int | None = Field(default=None, ge=1, le=3600)

    @model_validator(mode="after")
    def _batch_fields_are_paired(self) -> "SourceConfig":
        for size_field, timeout_field in (
            ("batch_size", "batch_timeout_seconds"),
            ("factor_batch_size", "factor_batch_timeout_seconds"),
        ):
            size = getattr(self, size_field)
            timeout = getattr(self, timeout_field)
            if (size is None) != (timeout is None):
                raise ValueError(
                    f"{size_field} and {timeout_field} must be configured together"
                )
        return self
```

补齐 `model_validator` 的 import（与文件既有的 `field_validator` import 风格一致）。

- [ ] **Step 4: 两份 yml 补注释（值不动）**

`project/configs/sources.yml` 与 `templates/project-config/sources.yml` 的 `xingyao:` 段补：

```yaml
  xingyao:
    # 批量通道（ADR-020）：日线校验车道与因子通道各有一对字段，取值由
    # project/probe_batch_channel.py 的实测冻结（见 docs/operations/）。
    # 成对配置：只给 size 或只给 timeout 会被 SourceConfig 拒绝；
    # 两者都不给 = 该端点仍走逐标的请求，不猜默认值。
    # batch_size: 1000
    # batch_timeout_seconds: 180
    # factor_batch_size: 200
    # factor_batch_timeout_seconds: 300
    enabled: false
```

- [ ] **Step 5: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_source_config_batch.py -q`
再 `ls tests/unit | grep -i config` 找到既有配置回归测试并一起点名跑。
Expected: PASS（新增 6 项 + 既有配置回归全绿）。

- [ ] **Step 6: 提交**

```bash
git add src/stock_quant/config.py project/configs/sources.yml templates/project-config/sources.yml tests/unit/test_source_config_batch.py
git commit -m "feat(config): add paired per-endpoint batch size and timeout fields

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 5: xingyao 日线批量能力（`fetch_batch`）

**Files:**
- Modify: `src/stock_quant/data_sources/base.py`（新增 `fetch_batch_with_retry`）
- Modify: `src/stock_quant/data_sources/xingyao.py`（`fetch_batch` + 结果类型 + 批量 worker）
- Test: `tests/unit/test_xingyao_source.py`

**Interfaces:**
- Consumes: `run_isolated`、`validate_supplier_frame(allow_empty=True)`（Task 1）、`SourceConfig.batch_*`（Task 4）、`batch_id_for`/`batch_request_parameters`（Task 2）
- Produces:
  - `BatchOutcome(symbol, status, result=None, message="")`，`status ∈ {"ok","empty","refused"}`，`result: FetchResult | None`
  - `BatchTransmission(symbols, request_parameters, batch_id, transport_id, request_timestamp, response_timestamp)` —— `transport_id` 由适配器给出，车道不反查该模块的常量
  - `BatchResult(outcomes, transmissions)`
  - `UnreadableFrame(message)`
  - `XingyaoSource.fetch_batch(requests, *, on_attempt=None) -> BatchResult`；outcomes 与入参 requests **逐位对齐**
  - `base.fetch_batch_with_retry(source, requests, policy, *, sleeper=time.sleep, on_attempt=None, max_bisect_levels=2) -> BatchResult`（`source` 注解为 `Any`：批量能力是可选鸭子类型，spec §5 明确不扩 `DataSource` Protocol）

> **与 spec §5 的对应**：spec 写的是 `fetch_batch(...) -> list[_BatchOutcome]`。这里扩成 `BatchResult`，因为同一段还要求"逐标的响应时间戳来自同一次调用"，而时间戳只有适配器知道；`outcomes` 仍是逐位对齐的列表，`transmissions` 只多带批次事实，二分后的子片各带各的。ADR-020 里注明这条实现形态。

- [ ] **Step 1: 把既有假 SDK 改成多 code 形状**

`tests/unit/test_xingyao_source.py`：批量调用下 worker 只回批次原始 dict，所以假 SDK 的回答形状从"单帧"变成"以 code 为键的 dict"。**既有的断言一条都不改**（它们只走 `fetch`，严格语义不变）。替换 `FakeSdk` 与其三个子类：

```python
class FakeSdk:
    """Stand-in for the SDK: one multi-code answer, keyed by code.

    The answer travels back from a forked child, so a fake that merely
    *records* what it was asked cannot be read back by these tests -- the
    append happens in the child.  What the tests assert on is the answer's
    content, which the worker does return.
    """

    def __init__(
        self,
        *,
        login_error: Exception | None = None,
        kline_error: Exception | None = None,
    ) -> None:
        self.login_error = login_error
        self.kline_error = kline_error
        self.logins = 0

    def login(self, **_):
        self.logins += 1
        if self.login_error is not None:
            raise self.login_error
        return object()

    def query_kline(self, *, symbols=None, **_):
        if self.kline_error is not None:
            raise self.kline_error
        return {
            code: FakeKline(
                rows=[{**row, "code": code} for row in FakeKline().rows]
            )
            for code in list(symbols or [])
        }


class _EmptySdk(FakeSdk):
    def query_kline(self, *, symbols=None, **_):
        return {code: FakeKline(rows=[]) for code in symbols}


class _NoDate(FakeSdk):
    def query_kline(self, *, symbols=None, **_):
        return {code: FakeKline(rows=[{"code": code, "close": 10.5}]) for code in symbols}


class _OutOfWindow(FakeSdk):
    def query_kline(self, *, symbols=None, **_):
        return {
            code: FakeKline(
                rows=[
                    {
                        "kline_time": "2023-12-01",
                        "code": code,
                        "open": 1.0,
                        "high": 1.0,
                        "low": 1.0,
                        "close": 1.0,
                        "volume": 1.0,
                        "amount": 1.0,
                    }
                ]
            )
            for code in symbols
        }
```

`fetch` 仍以 `symbol=` 调用单 code 路径的 worker，所以假 SDK 的 `query_kline` 只接受 `symbols=`：把既有单标的 worker 调用改为同样传列表（Task 5 Step 5 的 `fetch` 实现已经这么做），若既有假 SDK 里还有 `symbol=` 分支就一并删掉。

- [ ] **Step 2: 写批量路径的失败测试**

追加到同一文件（补齐 `json` 的 import）：

```python
def _batch_request(symbols):
    return [
        DataRequest("daily", (symbol,), _START, _END, {"adjustment": "unadjusted"})
        for symbol in symbols
    ]


def test_one_batch_call_answers_every_requested_symbol():
    """One login, one calendar, one query -- however many codes (ADR-020 D1)."""
    result = _source(FakeSdk()).fetch_batch(_batch_request(["000001.SZ", "600000.SH"]))

    assert [outcome.symbol for outcome in result.outcomes] == [
        "000001.SZ",
        "600000.SH",
    ]
    assert all(outcome.status == "ok" for outcome in result.outcomes)
    assert len(result.transmissions) == 1
    assert json.loads(result.transmissions[0].request_parameters)["symbols"] == [
        "000001.SZ",
        "600000.SH",
    ]


def test_each_ok_outcome_carries_its_own_fetch_result():
    """request_key and metadata stay per symbol even when one call served both."""
    result = _source(FakeSdk()).fetch_batch(_batch_request(["000001.SZ", "600000.SH"]))
    first, second = (outcome.result for outcome in result.outcomes)

    assert first.request_key != second.request_key
    assert first.metadata["transport_id"] == "xingyao-broker-tcp"
    assert first.metadata["supplier_endpoint"] == "xingyao.query_kline"
    assert first.metadata["request_timestamp"] == second.metadata["request_timestamp"]
    assert first.frame["code"].tolist() == ["000001.SZ", "000001.SZ"]
    assert second.frame["code"].tolist() == ["600000.SH", "600000.SH"]


def test_a_supplier_object_with_no_rows_is_an_empty_answer():
    """The zero-row object exists, so it is an answer -- and is snapshotted."""
    result = _source(_EmptySdk()).fetch_batch(_batch_request(["000001.SZ"]))

    assert result.outcomes[0].status == "empty"
    assert result.outcomes[0].result is not None
    assert result.outcomes[0].result.frame.empty


def test_a_code_absent_from_the_answer_is_refused_not_empty():
    """Fail-closed: an absent key has an unknowable cause (ADR-020 D2)."""

    class _Partial(FakeSdk):
        def query_kline(self, *, symbols=None, **_):
            return {"000001.SZ": FakeKline()}

    result = _source(_Partial()).fetch_batch(
        _batch_request(["000001.SZ", "600000.SH"])
    )

    assert [outcome.status for outcome in result.outcomes] == ["ok", "refused"]
    assert result.outcomes[1].result is None
    assert result.outcomes[1].message


def test_a_contract_break_in_one_code_refuses_only_that_code():
    """Layer 2: one bad frame must not take the chunk down with it."""

    class _OneBad(FakeSdk):
        def query_kline(self, *, symbols=None, **_):
            return {
                "000001.SZ": FakeKline(),
                "600000.SH": FakeKline(rows=[{"code": "600000.SH", "close": 1.0}]),
            }

    result = _source(_OneBad()).fetch_batch(
        _batch_request(["000001.SZ", "600000.SH"])
    )

    assert [outcome.status for outcome in result.outcomes] == ["ok", "refused"]


def test_an_answer_that_is_not_a_mapping_terminates_without_retry():
    """A whole-call contract break is permanent, not transient (spec §4 layer 1)."""

    class _NotAMapping(FakeSdk):
        def query_kline(self, *, symbols=None, **_):
            return object()

    with pytest.raises(ContractError):
        _source(_NotAMapping()).fetch_batch(_batch_request(["000001.SZ"]))


def test_a_value_that_is_not_a_frame_refuses_that_code_in_the_parent():
    """The worker never judges a code; it only makes the value transportable."""

    class _Unreadable(FakeSdk):
        def query_kline(self, *, symbols=None, **_):
            return {"000001.SZ": object()}

    result = _source(_Unreadable()).fetch_batch(_batch_request(["000001.SZ"]))

    assert result.outcomes[0].status == "refused"
    assert result.outcomes[0].result is None


def test_batch_and_single_request_judge_an_empty_answer_differently():
    """ADR-020 D2's boundary: the lane may call a zero-row object an answer,
    the single-request path may not."""
    with pytest.raises(ContractError):
        _source(_EmptySdk()).fetch(_request())
    result = _source(_EmptySdk()).fetch_batch(_batch_request(["000001.SZ"]))
    assert result.outcomes[0].status == "empty"


def test_each_batch_call_reports_one_attempt_with_its_code_count():
    """The parent counts attempts: a child-side counter never comes back."""
    attempts: list[int] = []
    _source(FakeSdk()).fetch_batch(
        _batch_request(["000001.SZ", "600000.SH"]), on_attempt=attempts.append
    )
    assert attempts == [2]


def test_a_batch_request_that_is_not_one_window_is_refused():
    with pytest.raises(ValueError, match="exactly one window"):
        _source(FakeSdk()).fetch_batch(
            [
                DataRequest("daily", ("000001.SZ",), _START, _END, {}),
                DataRequest("daily", ("600000.SH",), _START, date(2024, 1, 6), {}),
            ]
        )
```

- [ ] **Step 3: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_xingyao_source.py -q`
Expected: 既有用例 PASS（假 SDK 已改形状，`fetch` 语义未变）；新增用例 FAIL — `AttributeError: 'XingyaoSource' object has no attribute 'fetch_batch'`

- [ ] **Step 4: 实现 `fetch_batch_with_retry`**

`src/stock_quant/data_sources/base.py`，紧跟 `fetch_with_retry` 之后：

```python
def fetch_batch_with_retry(
    source: Any,
    requests: Sequence[DataRequest],
    policy: RetryPolicy,
    *,
    sleeper: Callable[[float], None] = time.sleep,
    on_attempt: Callable[[int], None] | None = None,
    max_bisect_levels: int = 2,
) -> Any:
    """One batch chunk: retry transient faults, then bisect -- and nothing else.

    ``source`` is duck-typed (``Any``, not ``DataSource``): the batch
    capability is optional and the caller checks for it before getting here,
    and ``DataSource`` stays the two-method Protocol it is (spec §5).

    Bisection is a *retry* policy, never an attribution policy.  Only a
    timeout or a server-side fault that survived its retries splits the chunk;
    bad credentials, a configuration error, a contract-breaking batch answer
    and a rate limit do not (splitting a rate-limited chunk would work around
    the supplier's own control rather than respect it).  Whatever still fails
    after bisection is a *chunk-level* failure: the caller records the chunk
    and never invents a per-symbol verdict from it.
    """
    try:
        return _fetch_batch_attempts(
            source, requests, policy, sleeper=sleeper, on_attempt=on_attempt
        )
    except (TimeoutError, ServerError):
        if max_bisect_levels <= 0 or len(requests) <= 1:
            raise
        middle = len(requests) // 2
        left = fetch_batch_with_retry(
            source,
            requests[:middle],
            policy,
            sleeper=sleeper,
            on_attempt=on_attempt,
            max_bisect_levels=max_bisect_levels - 1,
        )
        right = fetch_batch_with_retry(
            source,
            requests[middle:],
            policy,
            sleeper=sleeper,
            on_attempt=on_attempt,
            max_bisect_levels=max_bisect_levels - 1,
        )
        return type(left)(
            outcomes=left.outcomes + right.outcomes,
            transmissions=left.transmissions + right.transmissions,
        )


def _fetch_batch_attempts(
    source: Any,
    requests: Sequence[DataRequest],
    policy: RetryPolicy,
    *,
    sleeper: Callable[[float], None],
    on_attempt: Callable[[int], None] | None,
) -> Any:
    """Attempts under the adapter's own process bound, then the policy's wait.

    Deliberately *no* ``default_request_timeout`` wrapper here (unlike
    ``fetch_with_retry``): that bounds an HTTP session, and a batch call's
    bound is the adapter's own ``run_isolated`` timeout.  Only the wait
    between attempts comes from the policy.
    """
    for attempt in range(1, policy.max_attempts + 1):
        failure: Exception
        try:
            return source.fetch_batch(requests, on_attempt=on_attempt)
        except TransientSourceError as error:
            failure = error
        if attempt == policy.max_attempts:
            raise failure
        wait_seconds = min(attempt, policy.maximum_wait_seconds)
        if wait_seconds:
            sleeper(wait_seconds)
    raise AssertionError("retry loop must return or raise")
```

imports 追加 `Sequence`（若文件尚未导入）。

- [ ] **Step 5: 实现适配器的批量路径**

`src/stock_quant/data_sources/xingyao.py`：

在 `XingyaoSource` 之前新增结果类型：

```python
@dataclass(frozen=True)
class UnreadableFrame:
    """A per-code answer the worker could not turn into a frame.

    Conversion, not judgement: the child must not decide that a code is
    refused (spec §4 layer 2 gives that verdict to the parent), but a value
    that is not a frame and has no ``to_frame`` cannot cross the process
    boundary either.  It travels as this marker and is mapped to ``refused``
    in the parent.
    """

    message: str


@dataclass(frozen=True)
class BatchOutcome:
    """One requested symbol's outcome from a multi-code call.

    ``result`` is the adapter-assembled ``FetchResult`` for ``ok``/``empty``
    (its ``request_key`` and metadata are the symbol's own, even though one
    call served the whole chunk), and ``None`` for ``refused`` -- a refusal
    has no supplier object, so no snapshot may stand in for one.
    """

    symbol: str
    status: str
    result: FetchResult | None = None
    message: str = ""


@dataclass(frozen=True)
class BatchTransmission:
    """One actual multi-code call: what it asked, and when it answered."""

    symbols: tuple[str, ...]
    request_parameters: str
    batch_id: str
    transport_id: str
    request_timestamp: str
    response_timestamp: str


@dataclass(frozen=True)
class BatchResult:
    """Outcomes aligned with the requests, plus the transmissions behind them."""

    outcomes: tuple[BatchOutcome, ...]
    transmissions: tuple[BatchTransmission, ...]
```

`fetch` 与 `fetch_batch` 共用同一段 `FetchResult` 组装：

```python
    def fetch(self, request: DataRequest) -> FetchResult:
        if request.endpoint != "daily":
            raise ValueError("XingyaoSource serves only the daily endpoint")
        if len(request.symbols) != 1:
            raise ValueError("xingyao daily requests take exactly one symbol")
        _require_credentials()
        request_timestamp = _utc_timestamp()
        frames = run_isolated(
            _fetch_kline_batch,
            timeout_seconds=float(self.config.timeout_seconds),
            client=self._client,
            symbols=[request.symbols[0]],
            start=request.start_date,
            end=request.end_date,
        )
        response_timestamp = _utc_timestamp()
        frame = _frame_for(frames, request.symbols[0])
        # The single-request path keeps its strictness: silence is not an
        # answer here (ADR-009).  The relaxed zero-row rule belongs to the
        # lane's batch outcomes, not to this path (ADR-020 D2).
        validate_supplier_frame(
            frame,
            request,
            symbol_columns=("code",),
            date_columns=(DATE_COLUMN,),
        )
        return self._fetch_result(request, frame, request_timestamp, response_timestamp)

    def fetch_batch(
        self,
        requests: Sequence[DataRequest],
        *,
        on_attempt: Callable[[int], None] | None = None,
    ) -> BatchResult:
        """One multi-code call for the whole chunk; three states per symbol.

        Preconditions (the lane guarantees them): one endpoint, one window,
        exactly one symbol per request.  The returned outcomes are aligned
        with ``requests`` positionally, which is what lets the reuse partition
        hand back only the misses.
        """
        if not requests:
            return BatchResult(outcomes=(), transmissions=())
        if {request.endpoint for request in requests} != {"daily"}:
            raise ValueError("XingyaoSource serves only the daily endpoint")
        if any(len(request.symbols) != 1 for request in requests):
            raise ValueError("xingyao daily requests take exactly one symbol")
        symbols = tuple(request.symbols[0] for request in requests)
        if len({(request.start_date, request.end_date) for request in requests}) != 1:
            raise ValueError("xingyao batch requests take exactly one window")
        _require_credentials()
        if on_attempt is not None:
            on_attempt(len(symbols))
        request_timestamp = _utc_timestamp()
        frames = run_isolated(
            _fetch_kline_batch,
            timeout_seconds=float(self.config.batch_timeout_seconds),
            client=self._client,
            symbols=list(symbols),
            start=requests[0].start_date,
            end=requests[0].end_date,
        )
        response_timestamp = _utc_timestamp()
        outcomes = tuple(
            self._batch_outcome(
                request, frames, request_timestamp, response_timestamp
            )
            for request in requests
        )
        parameters = batch_request_parameters(
            "daily",
            symbols,
            requests[0].start_date,
            requests[0].end_date,
            dict(requests[0].params),
        )
        return BatchResult(
            outcomes=outcomes,
            transmissions=(
                BatchTransmission(
                    symbols=symbols,
                    request_parameters=parameters,
                    batch_id=batch_id_for(parameters),
                    transport_id=TRANSPORT_ID,
                    request_timestamp=request_timestamp,
                    response_timestamp=response_timestamp,
                ),
            ),
        )

    def _fetch_result(
        self,
        request: DataRequest,
        frame: pd.DataFrame,
        requested_at: str,
        answered_at: str,
    ) -> FetchResult:
        """The per-symbol evidence record, identical in both paths."""
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=frame,
            metadata=request_metadata(
                request,
                "xingyao.query_kline",
                self._sdk_version,
                transport_id=TRANSPORT_ID,
                request_timestamp=requested_at,
                response_timestamp=answered_at,
            ),
        )

    def _batch_outcome(
        self,
        request: DataRequest,
        frames: Mapping[str, Any],
        requested_at: str,
        answered_at: str,
    ) -> BatchOutcome:
        """The parent's per-symbol verdict: ok, empty, or refused."""
        symbol = request.symbols[0]
        if symbol not in frames:
            # Absent key: truncation, a silently dropped code and a supplier
            # omission are indistinguishable here, so this is fail-closed.
            return BatchOutcome(
                symbol=symbol,
                status="refused",
                message="supplier answer carried no frame for this code",
            )
        value = frames[symbol]
        if isinstance(value, UnreadableFrame):
            return BatchOutcome(symbol=symbol, status="refused", message=value.message)
        try:
            frame = _to_frame(value)
            validate_supplier_frame(
                frame,
                request,
                symbol_columns=("code",),
                date_columns=(DATE_COLUMN,),
                allow_empty=True,
            )
        except ContractError as error:
            return BatchOutcome(symbol=symbol, status="refused", message=str(error))
        result = self._fetch_result(request, frame, requested_at, answered_at)
        return BatchOutcome(
            symbol=symbol,
            status="empty" if frame.empty else "ok",
            result=result,
        )
```

worker 与 helper：

```python
def _fetch_kline_batch(
    *, client: Any, symbols: list[str], start, end
) -> dict[str, Any]:
    """The worker body: one login, one multi-code query, one logout.

    Returns the supplier's answer in a transportable shape, keyed by code.
    Nothing per-symbol is judged here: a multi-code call has no per-code
    failure to report, and a shape that is not a mapping at all is a
    *permanent* fault of the whole call (spec §4 layer 1: no retry, no
    bisection), not a verdict about any code.
    """
    logged_in = False
    try:
        if not client.login(**_credentials()):
            raise AuthenticationError("xingyao rejected the credentials")
        logged_in = True
        response = client.query_kline(
            symbols=list(symbols), begin_date=start, end_date=end
        )
        if not isinstance(response, Mapping):
            raise ContractError("xingyao returned an unreadable kline response")
        return {code: _transportable_frame(value) for code, value in response.items()}
    except (AuthenticationError, ContractError):
        raise
    except Exception as error:  # noqa: BLE001 - map, never leak SDK text upward
        if _looks_like_authentication(error):
            raise AuthenticationError("xingyao rejected the credentials") from None
        raise ServerError(
            f"xingyao kline request failed ({type(error).__name__})"
        ) from None
    finally:
        if logged_in:
            try:
                client.logout()
            except Exception:  # noqa: BLE001 - a logout failure costs nothing
                pass


def _transportable_frame(value: Any) -> Any:
    """Turn one code's answer into something that can cross the process boundary.

    A DataFrame travels as-is; anything with ``to_frame`` is converted and
    otherwise the value is marked unreadable.  The columns are never touched:
    what the parent validates must be what the supplier answered.
    """
    if isinstance(value, pd.DataFrame):
        return value
    to_frame = getattr(value, "to_frame", None)
    if to_frame is None:
        return UnreadableFrame("the supplier's answer for this code is not a frame")
    try:
        return to_frame()
    except Exception as error:  # noqa: BLE001 - the parent decides what this means
        return UnreadableFrame(
            f"the supplier's answer could not be read as a frame "
            f"({type(error).__name__})"
        )


def _frame_for(frames: Mapping[str, Any], symbol: str) -> pd.DataFrame:
    """The one symbol's frame on the strict (single-request) path."""
    if symbol not in frames:
        raise ContractError(f"xingyao returned no kline frame for {symbol!r}")
    value = frames[symbol]
    if isinstance(value, UnreadableFrame):
        raise ContractError(value.message)
    return _to_frame(value)
```

`_RealClient.query_kline` 改签名为 code 列表并只做形状检查（逐 code 判定归父进程）：

```python
    def query_kline(self, *, symbols, begin_date, end_date) -> dict[str, Any]:
        ad = self._ad
        if self._market is None:
            base = ad.BaseData()
            self._market = ad.MarketData(base.get_calendar())
        result = self._market.query_kline(
            list(symbols),
            begin_date=int(begin_date.strftime("%Y%m%d")),
            end_date=int(end_date.strftime("%Y%m%d")),
            period=ad.constant.Period.day.value,
            is_local=False,
        )
        if not isinstance(result, dict):
            raise ContractError("xingyao returned an unreadable kline response")
        return result
```

删除旧的 `_fetch_kline`（被 `_fetch_kline_batch` 取代）—— 它是本任务改动的直接对象。imports 追加：`from dataclasses import dataclass`、`from collections.abc import Callable, Mapping, Sequence`，以及 `from stock_quant.data_model.batch_evidence import batch_id_for, batch_request_parameters`。

- [ ] **Step 6: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_xingyao_source.py tests/unit/test_isolated_call.py -q`
Expected: PASS（既有 12 项 + 新增 10 项）。

- [ ] **Step 7: 提交**

```bash
git add src/stock_quant/data_sources/base.py src/stock_quant/data_sources/xingyao.py tests/unit/test_xingyao_source.py
git commit -m "feat(sources): serve a chunk of symbols from one xingyao session

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 6: 校验车道走批量通道

**Files:**
- Modify: `src/stock_quant/data_pipeline.py`（`_fetch_validation_daily`、`__init__`、`update` 的重置处、`dataset_build_config`、publish 调用点）
- Test: `tests/unit/test_xingyao_batch_lane.py`（新建）

**Interfaces:**
- Consumes: `XingyaoSource.fetch_batch`/`BatchResult`/`BatchOutcome`/`BatchTransmission`（Task 5）、`BatchEvidenceStore`/`BatchRequestEvidence`/`BatchOutcomeRecord`（Task 2）、`fetch_batch_with_retry`（Task 5）、`SourceConfig.batch_*`（Task 4）
- Produces: `SourceStatus` 新稳定码 `batch_fetch_failure`；`_transport_counts`；`_batch_evidence_shas`；`data/raw_batch_requests/**` 落盘；`build_config.batch_request_evidence`（证据哈希的排序列表）

- [ ] **Step 1: 写失败的车道测试**

`tests/unit/test_xingyao_batch_lane.py`：

```python
"""The batched validation lane: one session per chunk, three states per symbol."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from stock_quant.bootstrap import bootstrap_dataset
from stock_quant.config import SourceConfig
from stock_quant.data_model.batch_evidence import (
    batch_id_for,
    batch_request_parameters,
)
from stock_quant.data_pipeline import (
    DataPipeline,
    DataUpdateRequest,
    SourceStatus,
    dataset_build_config,
)
from stock_quant.data_sources.base import (
    DataRequest,
    FetchResult,
    ServerError,
    request_key,
    request_metadata,
)
from stock_quant.data_sources.batch_evidence_store import BatchEvidenceStore
from stock_quant.data_sources.xingyao import (
    TRANSPORT_ID,
    BatchOutcome,
    BatchResult,
    BatchTransmission,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_START = date(2024, 1, 2)
_END = date(2024, 1, 5)
_SYMBOLS = ["000001.SZ", "600000.SH"]


def _project(tmp_path) -> Path:
    """A minimal valid project root (the recipe tests/unit/test_suspensions.py uses)."""
    root = tmp_path / "project"
    configs = root / "configs"
    configs.mkdir(parents=True)
    for name in (
        "project.yml",
        "sources.yml",
        "costs.yml",
        "trading_rules.yml",
        "universe.yml",
    ):
        shutil.copy(_REPO_ROOT / "templates" / "project-config" / name, configs / name)
    bootstrap_dataset(root)
    return root


def _frame(symbol: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "kline_time": "2024-01-02",
                "code": symbol,
                "open": 10.0,
                "high": 11.0,
                "low": 9.5,
                "close": 10.5,
                "volume": 1000.0,
                "amount": 10500.0,
            }
        ]
    )


def _result_for(request: DataRequest, frame: pd.DataFrame) -> FetchResult:
    return FetchResult(
        source="xingyao",
        endpoint="daily",
        request_key=request_key(request),
        frame=frame,
        metadata=request_metadata(
            request,
            "xingyao.query_kline",
            "fake",
            transport_id=TRANSPORT_ID,
            request_timestamp="2024-01-05T09:00:00+00:00",
            response_timestamp="2024-01-05T09:00:07+00:00",
        ),
    )


class _BatchSource:
    """A lane-level fake: scripted outcomes, answered in-process (no fork)."""

    name = "xingyao"

    def __init__(self, outcomes, *, error=None):
        self.outcomes = outcomes
        self.error = error
        self.chunks: list[list[str]] = []

    def _answer(self, request: DataRequest) -> BatchOutcome:
        symbol = request.symbols[0]
        if symbol not in self.outcomes:
            return BatchOutcome(symbol, "refused", None, "no frame for this code")
        result = _result_for(request, _frame(symbol))
        return BatchOutcome(symbol, "ok", result)

    def _empty(self, request: DataRequest) -> BatchOutcome:
        symbol = request.symbols[0]
        result = _result_for(request, _frame(symbol).iloc[:0])
        return BatchOutcome(symbol, "empty", result)

    def _refused(self, request: DataRequest) -> BatchOutcome:
        return BatchOutcome(
            request.symbols[0], "refused", None, "no frame for this code"
        )

    def fetch_batch(self, requests, *, on_attempt=None):
        symbols = tuple(request.symbols[0] for request in requests)
        self.chunks.append(list(symbols))
        if on_attempt is not None:
            on_attempt(len(symbols))
        if self.error is not None:
            raise self.error
        parameters = batch_request_parameters(
            "daily",
            symbols,
            requests[0].start_date,
            requests[0].end_date,
            dict(requests[0].params),
        )
        return BatchResult(
            outcomes=tuple(
                self.outcomes.get(symbol, self._answer)(request)
                for symbol, request in zip(symbols, requests, strict=True)
            ),
            transmissions=(
                BatchTransmission(
                    symbols=symbols,
                    request_parameters=parameters,
                    batch_id=batch_id_for(parameters),
                    transport_id=TRANSPORT_ID,
                    request_timestamp="2024-01-05T09:00:00+00:00",
                    response_timestamp="2024-01-05T09:00:07+00:00",
                ),
            ),
        )


class _PerSymbolSource:
    """An adapter with no batch capability: tushare/akshare/baostock's shape."""

    name = "xingyao"

    def __init__(self):
        self.requests: list[str] = []

    def fetch(self, request):
        symbol = request.symbols[0]
        self.requests.append(symbol)
        return _result_for(request, _frame(symbol))


def _lane(tmp_path, source, *, batch=True):
    """Drive ``_fetch_validation_daily`` with (or without) the batch pair."""
    root = _project(tmp_path)
    pipeline = DataPipeline(root, sources={"xingyao": source})
    if batch:
        pipeline._project_config = pipeline._project_config.model_copy(
            update={
                "sources": {
                    **pipeline._project_config.sources,
                    "xingyao": SourceConfig(
                        enabled=True, batch_size=1000, batch_timeout_seconds=180
                    ),
                }
            }
        )
    issues: list = []
    statuses: dict[str, SourceStatus] = {}
    snapshots: list = []
    rows: list = []
    pipeline._fetch_validation_daily(
        "xingyao",
        {"xingyao"},
        _SYMBOLS,
        _START,
        _END,
        issues,
        statuses,
        snapshots,
        rows,
        reuse=False,
    )
    return pipeline, root, issues, statuses, snapshots, rows


def test_one_chunk_is_one_call_for_the_whole_universe(tmp_path):
    source = _BatchSource({})
    pipeline, root, issues, statuses, snapshots, rows = _lane(tmp_path, source)

    assert source.chunks == [_SYMBOLS]
    assert statuses["xingyao"].ok is True
    assert statuses["xingyao"].reason_code == "ok"
    assert len(rows) == 2
    assert len(snapshots) == 2


def test_an_empty_answer_is_evidence_and_does_not_touch_the_status(tmp_path):
    source = _BatchSource({"600000.SH": _BatchSource._empty})
    _, root, issues, statuses, snapshots, rows = _lane(tmp_path, source)

    assert statuses["xingyao"].ok is True
    assert statuses["xingyao"].reason_code == "ok"
    assert len(snapshots) == 2, "the empty answer must be snapshotted"
    assert len(rows) == 1, "an empty answer contributes no validation row"


def test_a_refused_symbol_warns_and_marks_the_source_partial(tmp_path):
    source = _BatchSource({"600000.SH": _BatchSource._refused})
    _, root, issues, statuses, snapshots, rows = _lane(tmp_path, source)

    assert statuses["xingyao"].ok is False
    assert statuses["xingyao"].reason_code == "partial_fetch_failure"
    assert len(snapshots) == 1, "a refusal has no supplier object to snapshot"
    assert [i for i in issues if i.details.get("symbol") == "600000.SH"]


def test_a_terminal_chunk_failure_is_one_chunk_record_with_a_stable_code(tmp_path):
    source = _BatchSource({}, error=ServerError("chunk timed out"))
    _, root, issues, statuses, snapshots, rows = _lane(tmp_path, source)

    assert statuses["xingyao"].ok is False
    assert statuses["xingyao"].reason_code == "batch_fetch_failure"
    assert snapshots == [] and rows == []
    assert not [
        issue for issue in issues if issue.details.get("symbol") is not None
    ], "a chunk-level failure must never be attributed to individual symbols"


def test_a_batch_lane_counts_attempts_not_per_symbol_calls(tmp_path):
    source = _BatchSource({})
    pipeline, *_ = _lane(tmp_path, source)

    assert pipeline._transport_counts["xingyao"]["daily"] == {
        "sessions": 1,
        "code_queries": 1,
    }


def test_the_chunk_is_recorded_as_batch_evidence(tmp_path):
    source = _BatchSource({})
    pipeline, root, *_ = _lane(tmp_path, source)

    records = list(
        (root / "data" / "raw_batch_requests" / "xingyao" / "daily").rglob("*.json")
    )
    assert len(records) == 1
    evidence = BatchEvidenceStore(root).load_by_sha(records[0].stem)
    assert evidence is not None
    assert {outcome.outcome for outcome in evidence.outcomes} == {"ok"}
    assert all(outcome.snapshot_file_sha256 for outcome in evidence.outcomes)
    assert pipeline._batch_evidence_shas == frozenset({evidence.sha256})


def test_a_refusal_is_recorded_too_but_without_a_snapshot_identity(tmp_path):
    """Losing a refusal would leave the record claiming it was never asked."""
    source = _BatchSource({"600000.SH": _BatchSource._refused})
    _, root, *_ = _lane(tmp_path, source)

    records = list(
        (root / "data" / "raw_batch_requests" / "xingyao" / "daily").rglob("*.json")
    )
    evidence = BatchEvidenceStore(root).load_by_sha(records[0].stem)
    refused = [o for o in evidence.outcomes if o.outcome == "refused"]
    assert len(refused) == 1
    assert refused[0].snapshot_file_sha256 is None


class _StoredSnapshot:
    """The two attributes the reuse path reads off a snapshot."""

    def __init__(self, sha256, manifest):
        self.sha256 = sha256
        self.manifest = manifest


def test_a_reused_snapshot_brings_its_batch_evidence_back(tmp_path):
    """Reuse must not drop the batch fact behind the bytes (spec §5)."""
    source = _BatchSource({})
    pipeline, root, *_ = _lane(tmp_path, source)
    (sha,) = pipeline._batch_evidence_shas
    witness = BatchEvidenceStore(root).load_by_sha(sha).outcomes[0]

    pipeline._batch_evidence_shas.clear()
    pipeline._raw_store.resolve_reusable = lambda name, endpoint, request: (
        _StoredSnapshot(witness.snapshot_file_sha256, {"metadata": {}}),
        _frame(_SYMBOLS[0]),
    )

    pending, hits = pipeline._partition_reusable(
        "xingyao", "daily", _SYMBOLS[:1], _START, _END, None, reuse=True
    )

    assert pending == []
    assert len(hits) == 1
    assert pipeline._batch_evidence_shas == frozenset({sha})


def test_the_published_build_config_binds_the_evidence_hash(tmp_path):
    """The dataset version must point at the batch records it was built from."""
    source = _BatchSource({})
    pipeline, *_ = _lane(tmp_path, source)
    (sha,) = pipeline._batch_evidence_shas

    payload = dataset_build_config(
        run_id="data_update_test",
        request=DataUpdateRequest(start_date=_START, end_date=_END),
        effective_start_date=_START,
        resolved_end_date=_END,
        statuses={},
        raw_snapshots=[],
        calendar_spans=[],
        acceptance_start=None,
        definition_hashes={},
        skipped_definitions=[],
        batch_request_evidence=sorted(pipeline._batch_evidence_shas),
    )
    assert payload["batch_request_evidence"] == [sha]


def test_a_lane_without_the_batch_pair_stays_per_symbol(tmp_path):
    """Unset means unused: no probe value, no batch channel (ADR-020 D6)."""
    source = _PerSymbolSource()
    _, root, issues, statuses, snapshots, rows = _lane(tmp_path, source, batch=False)

    assert source.requests == _SYMBOLS
    assert statuses["xingyao"].ok is True
    assert len(rows) == 2


def _own_request(symbol: str) -> DataRequest:
    """The request one symbol's evidence must be filed under, batch or not."""
    return DataRequest("daily", (symbol,), _START, _END, {"adjustment": "unadjusted"})


def test_a_batched_symbol_lands_on_its_own_request_path(tmp_path):
    """Batching changes who answers, never what one symbol's evidence *is*.

    ADR-020 D1's whole safety argument is that one symbol stays one logical
    request with one piece of evidence: a batch-written snapshot must be filed
    under that symbol's own request -- not a batch-level one -- and must land
    on exactly the path the per-symbol lane would have produced.  If the lane
    ever filed a chunk-wide request key, every later single-request lookup
    would miss while every test above still passed, so this invariant is
    asserted against the raw tree itself (spec §6).
    """
    symbol = _SYMBOLS[0]
    pipeline, root, _, _, batch_snapshots, _ = _lane(tmp_path, _BatchSource({}))
    _, _, _, _, solo_snapshots, _ = _lane(
        tmp_path / "solo", _PerSymbolSource(), batch=False
    )

    key = request_key(_own_request(symbol))
    batched = next(s for s in batch_snapshots if s.manifest["request_key"] == key)
    solo = next(s for s in solo_snapshots if s.manifest["request_key"] == key)
    # Same request key, same transport, same frame bytes => same content
    # address.  Timestamps differ between the two rounds on purpose: the path
    # must not depend on them.
    assert batched.path == solo.path
    assert batched.sha256 == solo.sha256

    # And it is therefore usable by that symbol's own request next round: the
    # batch lane bought throughput without buying a new evidence identity.
    found = pipeline._raw_store.resolve_reusable("xingyao", "daily", _own_request(symbol))
    assert found is not None
    assert found[0].sha256 == batched.sha256

    # The chunk as a whole is *not* a request anyone may look up: the batch
    # request lives in BatchRequestEvidence, never in the per-symbol tree.
    assert not (
        root
        / "data"
        / "raw"
        / "xingyao"
        / "daily"
        / TRANSPORT_ID
        / request_key(
            DataRequest("daily", tuple(_SYMBOLS), _START, _END)
        )
    ).exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_xingyao_batch_lane.py -q`
Expected: FAIL — 车道仍逐标的 `_dispatch`，`source.chunks` 为空、`_transport_counts` 不存在。

- [ ] **Step 3: 加计数器与证据收集槽**

`src/stock_quant/data_pipeline.py`：

- `__init__`：在 `self._reuse_counts = {}` 之后加

```python
        # Attempted transport operations per source x endpoint: sessions
        # established and code-bearing queries issued (ADR-020 D6).  Counted
        # by the parent before each worker start, so retries and bisections
        # are visible; they feed the ledger's ``transport`` section.
        self._transport_counts: dict[str, dict[str, dict[str, int]]] = {}
        # Content addresses of the batch evidence records this run wrote,
        # bound into build_config on publish and on every failure path.
        self._batch_evidence_shas: set[str] = set()
```

- `update()` 里重置 `self._reuse_counts` 的那一行下方一并重置：

```python
        self._reuse_counts = {}
        self._transport_counts = {}
        self._batch_evidence_shas = set()
```

- `dataset_build_config` 签名末尾加 `batch_request_evidence: Sequence[str] | None = None`，并在 `if raw_snapshot_reuse:` 那句旁加：

```python
    if batch_request_evidence:
        config["batch_request_evidence"] = list(batch_request_evidence)
```

docstring 补一句：记录一轮实际用到的批次传输证据哈希（ADR-020 D8），使数据集版本能指回它据以构建的批次记录。

- publish 处的调用点加：

```python
                    raw_snapshot_reuse=_reuse_evidence(self._reuse_counts),
                    batch_request_evidence=sorted(self._batch_evidence_shas),
```

- [ ] **Step 4: 实现车道**

`_fetch_validation_daily` 的签名与 docstring 不动，函数体改为：

```python
        source = self._adapter_or_warn(name, issues)
        if source is None:
            statuses[name] = SourceStatus(
                name,
                False,
                False,
                reason="optional source unavailable",
                reason_code="optional_source_unavailable",
            )
            return
        config = self._project_config.sources.get(name, SourceConfig())
        if (
            getattr(source, "fetch_batch", None) is None
            or config.batch_size is None
            or config.batch_timeout_seconds is None
        ):
            # No batch capability, or no frozen batch pair: the lane stays
            # per-symbol.  An unfrozen pair is not guessed at (ADR-020 D6).
            self._fetch_validation_per_symbol(
                name,
                source,
                symbols,
                start,
                end,
                issues,
                statuses,
                raw_snapshots,
                validation_rows,
                reuse=reuse,
            )
            return
        policy = RetryPolicy(
            max_attempts=min(config.max_retries + 1, 3),
            maximum_wait_seconds=min(config.timeout_seconds, 30),
            call_timeout_seconds=config.timeout_seconds,
        )
        failures = 0
        chunk_failed = False
        for chunk in _chunks(symbols, int(config.batch_size)):
            pending, hits = self._partition_reusable(
                name, "daily", chunk, start, end, issues, reuse=reuse
            )
            for result, snapshot in hits:
                raw_snapshots.append(snapshot)
                validation_rows.append(
                    normalize_daily(
                        result.frame, name, _ingest_time(result.metadata)
                    ).valid
                )
            if not pending:
                continue
            requests = [
                DataRequest(
                    "daily", (symbol,), start, end, {"adjustment": "unadjusted"}
                )
                for symbol in pending
            ]
            try:
                batch = fetch_batch_with_retry(
                    source,
                    requests,
                    policy,
                    sleeper=self._sleeper,
                    on_attempt=self._transport_attempt(name, "daily"),
                )
            except Exception as error:  # noqa: BLE001 - optional lane
                chunk_failed = True
                failures += len(pending)
                issues.append(
                    _issue(
                        Severity.WARNING,
                        CODE_OPTIONAL_SOURCE_FAILURE,
                        details={
                            "source": name,
                            "endpoint": "daily",
                            "batch_size": len(pending),
                            "symbols": ",".join(pending),
                            "message": str(translate_supplier_error(error)),
                        },
                    )
                )
                continue
            failures += self._record_batch_outcomes(
                name, batch, requests, issues, raw_snapshots, validation_rows
            )
        if chunk_failed:
            statuses[name] = SourceStatus(
                name,
                False,
                False,
                reason=(
                    f"a batch chunk failed after retry and bisection "
                    f"({failures} of {len(symbols)} validation requests unanswered)"
                ),
                reason_code="batch_fetch_failure",
            )
        elif failures:
            statuses[name] = SourceStatus(
                name,
                False,
                False,
                reason=f"{failures} of {len(symbols)} validation requests failed",
                reason_code="partial_fetch_failure",
            )
        else:
            statuses[name] = SourceStatus(name, False, True, reason_code="ok")
```

新增 helper（放在 `_fetch_validation_daily` 之后）：

```python
    def _fetch_validation_per_symbol(
        self,
        name,
        source,
        symbols,
        start,
        end,
        issues,
        statuses,
        raw_snapshots,
        validation_rows,
        *,
        reuse,
    ) -> None:
        """The pre-batch lane, kept for adapters without ``fetch_batch``.

        Behaviour is unchanged from the per-symbol era, so a lane that is not
        batched keeps producing exactly the evidence it produced before.
        """
        failures = 0
        for symbol in symbols:
            dispatched = self._dispatch(
                name,
                source,
                "daily",
                symbol,
                start,
                end,
                {"adjustment": "unadjusted"},
                required=False,
                issues=issues,
                reuse=reuse,
            )
            if dispatched is None:
                failures += 1
                continue
            result, snapshot = dispatched
            raw_snapshots.append(snapshot)
            validation_rows.append(
                normalize_daily(result.frame, name, _ingest_time(result.metadata)).valid
            )
        if failures:
            statuses[name] = SourceStatus(
                name,
                False,
                False,
                reason=f"{failures} of {len(symbols)} validation requests failed",
                reason_code="partial_fetch_failure",
            )
        else:
            statuses[name] = SourceStatus(name, False, True, reason_code="ok")

    def _partition_reusable(
        self, name, endpoint, symbols, start, end, issues, *, reuse
    ):
        """Reuse first, network second: hits served, misses returned.

        Mirrors ``_dispatch``'s reuse step exactly -- same lookup, same
        ``reuse_candidate_rejected`` warning for a candidate that exists but
        is refused, same ``FetchResult`` rebuilt from the stored manifest --
        so a reused answer and a fetched answer reach the same downstream.
        """
        store = BatchEvidenceStore(self._project_root)
        pending: list[str] = []
        hits: list[tuple[FetchResult, RawSnapshot]] = []
        for symbol in symbols:
            request = DataRequest(
                endpoint, (symbol,), start, end, {"adjustment": "unadjusted"}
            )
            if reuse:
                resolved = self._raw_store.resolve_reusable(name, endpoint, request)
                if resolved is not None:
                    snapshot, frame = resolved
                    self._count_fetch(name, endpoint, "reused")
                    # The bytes came back from the store, but the batch that
                    # produced them is still the transport fact behind this
                    # run's evidence: without this the version's
                    # ``batch_request_evidence`` would silently lose every
                    # chunk that reuse answered (spec §5).
                    origin = store.lookup_by_snapshot(
                        name, endpoint, request_key(request), snapshot.sha256
                    )
                    if origin is not None:
                        self._batch_evidence_shas.add(origin.sha256)
                    hits.append(
                        (
                            FetchResult(
                                source=name,
                                endpoint=endpoint,
                                request_key=request_key(request),
                                frame=frame,
                                metadata=dict(snapshot.manifest.get("metadata") or {}),
                            ),
                            snapshot,
                        )
                    )
                    continue
                if issues is not None and self._raw_store.has_candidate(
                    name, endpoint, request
                ):
                    issues.append(
                        _issue(
                            Severity.WARNING,
                            CODE_REUSE_CANDIDATE_REJECTED,
                            symbol=symbol,
                            details={"source": name, "endpoint": endpoint},
                        )
                    )
            pending.append(symbol)
        return pending, hits

    def _record_batch_outcomes(
        self, name, batch, requests, issues, raw_snapshots, validation_rows
    ) -> int:
        """Book one chunk's outcomes and its batch provenance; return refusals."""
        failures = 0
        recorded: dict[str, BatchOutcomeRecord] = {}
        for request, outcome in zip(requests, batch.outcomes, strict=True):
            symbol = request.symbols[0]
            if outcome.status == "refused":
                failures += 1
                issues.append(
                    _issue(
                        Severity.WARNING,
                        CODE_OPTIONAL_SOURCE_FAILURE,
                        symbol=symbol,
                        details={
                            "source": name,
                            "endpoint": "daily",
                            "symbol": symbol,
                            "message": outcome.message,
                        },
                    )
                )
                recorded[symbol] = BatchOutcomeRecord(
                    symbol, request_key(request), "refused", None, outcome.message
                )
                continue
            # ``ok`` and ``empty`` take the same path: both are answers the
            # supplier gave an object for, so both are snapshotted as-is.
            snapshot = self._record_raw(outcome.result)
            raw_snapshots.append(snapshot)
            self._count_fetch(name, "daily", "fetched")
            if outcome.status == "ok":
                validation_rows.append(
                    normalize_daily(
                        outcome.result.frame,
                        name,
                        _ingest_time(outcome.result.metadata),
                    ).valid
                )
            recorded[symbol] = BatchOutcomeRecord(
                symbol, outcome.result.request_key, outcome.status, snapshot.sha256
            )
        self._save_batch_evidence(name, batch, recorded)
        return failures

    def _save_batch_evidence(self, name, batch, recorded) -> None:
        """Bind each transmission to what it produced, per symbol.

        Written even when a symbol was refused: a refusal has no snapshot but
        is still a fact about the batch, and losing it would leave the record
        claiming the symbol was never asked for.
        """
        store = BatchEvidenceStore(self._project_root)
        for transmission in batch.transmissions:
            outcomes = tuple(
                recorded[symbol]
                for symbol in transmission.symbols
                if symbol in recorded
            )
            if not outcomes:
                continue
            evidence = BatchRequestEvidence(
                source=name,
                endpoint="daily",
                transport_id=transmission.transport_id,
                batch_id=transmission.batch_id,
                batch_request_parameters=transmission.request_parameters,
                request_timestamp=transmission.request_timestamp,
                response_timestamp=transmission.response_timestamp,
                outcomes=outcomes,
            )
            store.save(evidence)
            self._batch_evidence_shas.add(evidence.sha256)

    def _transport_attempt(self, name: str, endpoint: str):
        """A hook the batch call fires once per attempted worker start."""

        def attempt(code_count: int) -> None:
            row = self._transport_counts.setdefault(name, {}).setdefault(
                endpoint, {"sessions": 0, "code_queries": 0}
            )
            row["sessions"] += 1
            row["code_queries"] += 1

        return attempt
```

> `code_count` 在本任务与 Task 8 都不参与计数：一次批量调用无论带几个 code 都是 1 次 code 查询。保留形参是为了让钩子签名与"片大小"这一事实绑定，Task 10 的探针据此打印实测片大小。

模块级 helper：

```python
def _chunks(symbols: Sequence[str], size: int) -> Iterator[list[str]]:
    """Contiguous chunks: the batch order must follow the requested order."""
    for start in range(0, len(symbols), size):
        yield list(symbols[start : start + size])
```

imports 追加：`Iterator`（`_chunks` 用），并把 `from stock_quant.data_sources.base import (...)` 那组补上 `fetch_batch_with_retry`，再补 `from stock_quant.data_model.batch_evidence import BatchOutcomeRecord, BatchRequestEvidence` 与 `from stock_quant.data_sources.batch_evidence_store import BatchEvidenceStore`。

**不要**为了 `transport_id` 从 `stock_quant.data_sources.xingyao` 导入常量：车道用 `transmission.transport_id`（Task 5 让适配器自己报），`data_pipeline` 因此不必在模块层依赖某个具体适配器的模块。

- [ ] **Step 5: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_xingyao_batch_lane.py -q`
Expected: PASS（11 项）。

- [ ] **Step 6: 跑邻居回归**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_pipeline_fetch_coverage.py tests/unit/test_suspensions.py -q`
Expected: PASS。若因新分支出现新语义需要新用例，按新语义补，而不是放宽旧断言。

- [ ] **Step 7: 提交**

```bash
git add src/stock_quant/data_pipeline.py tests/unit/test_xingyao_batch_lane.py
git commit -m "feat(pipeline): drive the validation lane from one batched session

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 7: 成本计数器与账本统一终态持久化

**Files:**
- Modify: `src/stock_quant/data_model/call_ledger.py`（`render_call_ledger`）
- Modify: `src/stock_quant/data_pipeline.py`（`_result`；删除 publish 成功路径上的独立写点）
- Test: `tests/unit/test_call_ledger.py`

**Interfaces:**
- Consumes: `_transport_counts`（Task 6）
- Produces: `render_call_ledger(sources, reused=None, transport=None)`，每源行新增 `transport` 段；`_transport_ledger_payload(counts)`；账本由 `_result` 在**每条终止路径**写出

- [ ] **Step 1: 更新既有精确断言并加新用例**

`tests/unit/test_call_ledger.py`：把三处精确字典断言更新为含 `transport` 的新形状（**不得**改成子集断言），并追加：

```python
def test_transport_renders_attempted_sessions_and_code_queries():
    """Attempted operations, not successes: retries and bisection count too."""
    rows = render_call_ledger(
        {"xingyao": _ScalarCalls()},
        transport={"xingyao": {"daily": {"sessions": 3, "code_queries": 4}}},
    )
    assert rows["xingyao"]["transport"] == {
        "daily": {"sessions": 3, "code_queries": 4}
    }


def test_transport_defaults_to_empty_and_sorts_endpoints():
    rows = render_call_ledger(
        {"xingyao": _ScalarCalls()},
        transport={
            "xingyao": {
                "backward_factor": {"sessions": 1, "code_queries": 1},
                "daily": {"sessions": 1, "code_queries": 1},
            }
        },
    )
    assert list(rows["xingyao"]["transport"]) == ["backward_factor", "daily"]
    assert render_call_ledger({"xingyao": _ScalarCalls()})["xingyao"]["transport"] == {}
```

追加"账本在终止漏斗写出"的两条（用 `tests/unit/test_suspensions.py` 的项目根配方）：

```python
import json
import shutil
from pathlib import Path

from stock_quant.bootstrap import bootstrap_dataset
from stock_quant.data_pipeline import (
    DataPipeline,
    SourceStatus,
    _CONFIGURED_SOURCES,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _project(tmp_path) -> Path:
    root = tmp_path / "project"
    configs = root / "configs"
    configs.mkdir(parents=True)
    for name in (
        "project.yml",
        "sources.yml",
        "costs.yml",
        "trading_rules.yml",
        "universe.yml",
    ):
        shutil.copy(_REPO_ROOT / "templates" / "project-config" / name, configs / name)
    bootstrap_dataset(root)
    return root


def _ok_statuses() -> dict:
    return {
        name: SourceStatus(name, False, True, reason_code="ok")
        for name in _CONFIGURED_SOURCES
    }


def test_the_result_funnel_persists_the_ledger_on_every_termination(tmp_path):
    """Success, quality block and source failure all leave a ledger (spec §4)."""
    root = _project(tmp_path)
    pipeline = DataPipeline(root, sources={})
    pipeline._transport_counts = {
        "xingyao": {"daily": {"sessions": 1, "code_queries": 1}}
    }

    pipeline._result([], None, "data_update_abc123", None, _ok_statuses(), [])

    path = root / "data" / "runs" / "data_update_abc123" / "call_ledger.json"
    payload = json.loads(path.read_text())
    assert payload["xingyao"]["transport"] == {
        "daily": {"sessions": 1, "code_queries": 1}
    }


def test_a_result_without_a_run_id_writes_nothing(tmp_path):
    root = _project(tmp_path)
    DataPipeline(root, sources={})._result([], None, None, None, _ok_statuses(), [])
    assert not (root / "data" / "runs").exists()
```

`SourceStatus` 的真实签名（`data_pipeline.py`，已核对）：`SourceStatus(source: str, required: bool, ok: bool, reason: str | None = None, reason_code: str | None = None)`——前三个位置传参，`reason_code` 关键字传参；本计划各处均按此写，不要再加 `name=`/`required_ok=` 这类关键字。

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_call_ledger.py -q`
Expected: FAIL — 缺 `transport` 键 / `render_call_ledger` 不接受 `transport` / `_result` 未写账本。

- [ ] **Step 3: 实现渲染**

```python
def render_call_ledger(
    sources: Mapping[str, object],
    reused: Mapping[str, Mapping[str, int]] | None = None,
    transport: Mapping[str, Mapping[str, Mapping[str, int]]] | None = None,
) -> dict[str, object]:
    """Normalized ledger rows: one entry per source.

    ``reused`` carries, per source x endpoint, how many requests were served
    from stored raw snapshots instead of the supplier (ADR-015).  ``transport``
    carries the *attempted* transport operations per source x endpoint --
    sessions established and code-bearing queries issued -- so retry and
    bisection attempts stay visible instead of hiding behind a success count;
    a batch call is one code-bearing query however many codes it carried.
    """
    rows: dict[str, object] = {}
    for name, source in sorted(sources.items()):
        total, endpoints = _summarize_calls(getattr(source, "calls", 0))
        reused_endpoints: dict[str, int] = {}
        reused_for_source = (reused or {}).get(name)
        if isinstance(reused_for_source, Mapping):
            reused_endpoints = {
                str(endpoint): int(count)
                for endpoint, count in sorted(reused_for_source.items())
            }
        transport_endpoints: dict[str, dict[str, int]] = {}
        transport_for_source = (transport or {}).get(name)
        if isinstance(transport_for_source, Mapping):
            transport_endpoints = {
                str(endpoint): {
                    "sessions": int(counts.get("sessions", 0)),
                    "code_queries": int(counts.get("code_queries", 0)),
                }
                for endpoint, counts in sorted(transport_for_source.items())
            }
        rows[name] = {
            "calls": total,
            "endpoints": endpoints,
            "reused": reused_endpoints,
            "transport": transport_endpoints,
        }
    return rows
```

（`_summarize_calls(calls) -> (total, by_endpoint)` 是 `call_ledger.py` 既有 helper，已核对：签名与行为照用，不改。`write_call_ledger(project_root, run_id, payload)` 同样既有、照用。）

- [ ] **Step 4: 把写点收进终止漏斗**

`src/stock_quant/data_pipeline.py`：

1. 删除 publish 成功路径上的独立 `write_call_ledger(...)` 调用及其上方注释。
2. `_result` 改为：

```python
    def _result(
        self,
        issues,
        dataset_ref,
        run_id,
        resolved_end,
        statuses,
        raw_snapshots,
    ) -> DataUpdateResult:
        """The single exit of ``update``: every terminal path returns through here.

        The call ledger is written here rather than only after a successful
        publish (spec §4): a blocked publication, a failed required source and
        an authentication refusal all consumed supplier quota, and an
        accounting that only survives success cannot answer "what did this
        failed attempt cost?".  ``run_id`` is absent only for paths that never
        started a run, which have nothing to persist.
        """
        if run_id:
            write_call_ledger(
                self._project_root,
                run_id,
                render_call_ledger(
                    self._active_sources(),
                    reused=_reused_ledger_payload(self._reuse_counts),
                    transport=_transport_ledger_payload(self._transport_counts),
                ),
            )
        return DataUpdateResult(
            quality_report=QualityReport(issues=tuple(issues)),
            dataset_ref=dataset_ref,
            run_id=run_id,
            resolved_end_date=resolved_end,
            source_status=tuple(statuses[name] for name in _CONFIGURED_SOURCES),
            raw_snapshots=tuple(snapshot.sha256 for snapshot in raw_snapshots),
        )
```

3. 在 `_reused_ledger_payload` 旁加：

```python
def _transport_ledger_payload(counts) -> dict[str, dict[str, dict[str, int]]]:
    """Copy the attempted-operation counters into a plain, sortable payload."""
    return {
        name: {
            endpoint: {
                "sessions": int(row["sessions"]),
                "code_queries": int(row["code_queries"]),
            }
            for endpoint, row in sorted(by_endpoint.items())
        }
        for name, by_endpoint in sorted(counts.items())
    }
```

- [ ] **Step 5: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_call_ledger.py tests/unit/test_xingyao_batch_lane.py -q`
Expected: PASS（`test_call_ledger` 新增 4 项 + `test_xingyao_batch_lane` 11 项）。

- [ ] **Step 6: 提交**

```bash
git add src/stock_quant/data_model/call_ledger.py src/stock_quant/data_pipeline.py tests/unit/test_call_ledger.py
git commit -m "feat(ledger): count attempted sessions and queries, on every terminal path

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 8: 因子通道批量预取

**Files:**
- Modify: `src/stock_quant/data_sources/xingyao_factor.py`（`fetch_factor_frames`）
- Modify: `src/stock_quant/data_pipeline.py`（`_LazyFactorChannel`、`_build_factor_channel`、`_fetch_factor_chunks`）
- Test: `tests/unit/test_xingyao_factor.py`

**Interfaces:**
- Consumes: `SourceConfig.factor_batch_*`（Task 4）、候选池接线（Task 3）、`_transport_attempt`（Task 6）、`ContractError`（既有，需补进 `data_pipeline` 的 base 导入）
- Produces: `fetch_factor_frames(symbols, *, timeout_seconds, end=None) -> dict[str, pd.DataFrame]`；`_LazyFactorChannel.prefetch(symbols) -> None`；`_LazyFactorChannel(..., fetch_frames=None)`（**可选**形参，既有构造点不受影响）；`DataPipeline._build_factor_channel(end, issues, raw_snapshots, candidates=())`

> **前置（探针 1 的因子分支）**：本任务假设多 code 的 `get_backward_factor(codes, is_local=False)` 返回**以 code 为键的 mapping**（与 `query_kline` 同形）。若 Task 10 的探针显示因子端点返回**单张宽表**（列就是 code，没有 dict 层），批量分片不成立——停下把发现回报 owner，而不是把宽表按行拆成"逐标的帧"（那会伪造供应商没给出的形状）。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/unit/test_xingyao_factor.py`（`_wide_frame()` 用文件既有用例构造宽表的方式取同形对象；若已有 helper 就复用）：

```python
def test_a_factor_chunk_is_one_call_for_several_codes(monkeypatch):
    """The factor endpoint is batched on its own terms (ADR-020 D5/D6)."""
    from stock_quant.data_sources import xingyao_factor

    calls: list[list[str]] = []
    wide = _wide_frame()

    def fake_isolated(target, *, timeout_seconds, **kwargs):
        calls.append(list(kwargs["symbols"]))
        return {code: wide for code in kwargs["symbols"]}

    monkeypatch.setattr(xingyao_factor, "run_isolated", fake_isolated)
    frames = xingyao_factor.fetch_factor_frames(
        ["000001.SZ", "600000.SH"], timeout_seconds=120.0
    )

    assert calls == [["000001.SZ", "600000.SH"]]
    assert set(frames) == {"000001.SZ", "600000.SH"}


def test_a_single_symbol_factor_fetch_still_uses_one_worker(monkeypatch):
    from stock_quant.data_sources import xingyao_factor

    calls: list[list[str]] = []
    wide = _wide_frame()

    def fake_isolated(target, *, timeout_seconds, **kwargs):
        calls.append(list(kwargs["symbols"]))
        return {code: wide for code in kwargs["symbols"]}

    monkeypatch.setattr(xingyao_factor, "run_isolated", fake_isolated)
    frame = xingyao_factor.fetch_factor_frame("000001.SZ", timeout_seconds=30.0)

    assert calls == [["000001.SZ"]]
    assert not frame.empty


def test_a_missing_code_in_a_factor_answer_is_dropped_not_invented(monkeypatch):
    """No supplier object means no frame -- never a synthesised one."""
    from stock_quant.data_sources import xingyao_factor

    wide = _wide_frame()

    def fake_isolated(target, *, timeout_seconds, **kwargs):
        return {"000001.SZ": wide}

    monkeypatch.setattr(xingyao_factor, "run_isolated", fake_isolated)
    frames = xingyao_factor.fetch_factor_frames(
        ["000001.SZ", "600000.SH"], timeout_seconds=120.0
    )

    assert set(frames) == {"000001.SZ"}


def test_a_single_symbol_factor_fetch_still_refuses_an_absent_code(monkeypatch):
    """The single-symbol path keeps ADR-009's strictness."""
    from stock_quant.data_sources import xingyao_factor

    monkeypatch.setattr(
        xingyao_factor, "run_isolated", lambda target, *, timeout_seconds, **kwargs: {}
    )
    with pytest.raises(ContractError):
        xingyao_factor.fetch_factor_frame("000001.SZ", timeout_seconds=30.0)
```

通道级的预取用例加到 `tests/unit/test_tdx_arbiter.py`（该文件直接构造 `_LazyFactorChannel`）：

```python
def _prefetchable_channel(fetch_frames, issues, raw_snapshots):
    """A factor channel whose lazy single-symbol path must never be reached."""
    from stock_quant import data_pipeline

    return data_pipeline._LazyFactorChannel(
        "xingyao",
        lambda symbol: pytest.fail("the lazy single-symbol path must not run"),
        lambda frame, symbol: [],
        lambda symbol, frame: FetchResult(
            source="xingyao",
            endpoint="backward_factor",
            request_key=request_key(
                DataRequest("backward_factor", (symbol,), date(1990, 12, 19), date(2020, 12, 31))
            ),
            frame=frame,
            metadata={"transport_id": "xingyao-broker-tcp"},
        ),
        date(2020, 12, 31),
        issues=issues,
        raw_snapshots=raw_snapshots,
        record_raw=lambda result: result,
        fetch_frames=fetch_frames,
    )


def test_a_factor_prefetch_answers_a_whole_chunk_without_the_lazy_path():
    asked: list[list[str]] = []
    issues: list = []
    snapshots: list = []

    def fetch_frames(symbols):
        asked.append(list(symbols))
        return {symbol: _factor_frame() for symbol in symbols}

    channel = _prefetchable_channel(fetch_frames, issues, snapshots)
    channel.prefetch(["000001.SZ", "600000.SH"])

    assert asked == [["000001.SZ", "600000.SH"]]
    assert channel.frame_for("000001.SZ") is not None
    assert len(snapshots) == 2, "each code's own frame is still its own evidence"
    assert issues == []


def test_a_code_the_factor_answer_did_not_carry_is_reported_not_cached():
    asked: list[list[str]] = []
    issues: list = []

    def fetch_frames(symbols):
        asked.append(list(symbols))
        return {"000001.SZ": _factor_frame()}

    channel = _prefetchable_channel(fetch_frames, issues, [])
    channel.prefetch(["000001.SZ", "600000.SH"])

    assert asked == [["000001.SZ", "600000.SH"]], "one chunk, not a session per code"
    assert [i for i in issues if i.details.get("symbol") == "600000.SH"]
    assert channel.frame_for("600000.SH") is None
    assert asked == [["000001.SZ", "600000.SH"]], "and it is not asked again"


def test_a_factor_channel_without_a_batch_fetcher_prefetches_nothing():
    channel = _prefetchable_channel(None, [], [])
    channel.prefetch(["000001.SZ"])
```

`_factor_frame()` 用该文件 779 行附近既有的因子宽帧构造方式（若无独立 helper，就地构造一张以日期为索引、列为 code 的宽表）。补齐 `FetchResult`/`request_key`/`DataRequest` 的 import。

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_xingyao_factor.py -q -k "chunk or missing_code or absent_code"`
Expected: FAIL — `AttributeError: module 'stock_quant.data_sources.xingyao_factor' has no attribute 'fetch_factor_frames'`

- [ ] **Step 3: 实现因子批量取帧**

`src/stock_quant/data_sources/xingyao_factor.py`：

```python
def fetch_factor_frames(
    symbols: Sequence[str], *, timeout_seconds: float, end: date | None = None
) -> dict[str, pd.DataFrame]:
    """One worker for a whole chunk of factor series.

    The stored bytes stay per symbol (the supplier's own wide frame for that
    code); this only changes how many sessions it takes to obtain them.  A
    code the answer did not carry is simply absent from the mapping -- the
    caller must not invent a frame for it.
    """
    return run_isolated(
        _fetch_backward_factor_batch,
        timeout_seconds=timeout_seconds,
        symbols=list(symbols),
        end=(end or date.today()).isoformat(),
    )


def fetch_factor_frame(
    symbol: str, *, timeout_seconds: float, end: date | None = None
) -> pd.DataFrame:
    """One worker, one symbol: the strict path keeps ADR-009's stance."""
    frames = fetch_factor_frames([symbol], timeout_seconds=timeout_seconds, end=end)
    frame = frames.get(symbol)
    if frame is None:
        raise ContractError(f"xingyao returned no factor frame for {symbol!r}")
    return frame
```

`_login_and_import()` 把现有 `_fetch_backward_factor` 的凭据检查与 SDK 导入原样提取（报错文本一字不改），返回已登录的 `ad` 模块；批量 worker 为：

```python
def _fetch_backward_factor_batch(*, symbols: list[str], end: str) -> dict[str, pd.DataFrame]:
    """The batch worker body: one login, one multi-code query, one logout.

    Same credential and error discipline as the single-symbol worker.  Every
    frame is clipped to the recorded ``end`` so its bytes remain a function of
    the request (owner ruling 2026-09-26).
    """
    ad = _login_and_import()
    try:
        response = ad.BaseData().get_backward_factor(list(symbols), is_local=False)
        if not isinstance(response, Mapping):
            raise ContractError("xingyao returned an unreadable factor response")
        return {
            code: _clip_to_end(_to_frame(frame), end)
            for code, frame in response.items()
        }
    except (AuthenticationError, ContractError):
        raise
    except Exception as error:  # noqa: BLE001 - map, never leak SDK text upward
        if _looks_like_authentication(error):
            raise AuthenticationError("xingyao rejected the credentials") from None
        raise ServerError(
            f"xingyao factor request failed ({type(error).__name__})"
        ) from None
    finally:
        try:
            ad.logout()
        except Exception:  # noqa: BLE001 - a logout failure costs nothing
            pass
```

`_fetch_backward_factor` 改为：`ad = _login_and_import()`，然后 `get_backward_factor([symbol], is_local=False)`、`_clip_to_end(_to_frame(response), end)`，用同一套 except/finally。imports 追加 `Mapping`、`Sequence`。

- [ ] **Step 4: 让因子通道支持预取**

`_LazyFactorChannel.__init__` 增加**可选**形参 `fetch_frames: Any | None = None`（存 `self._fetch_frames`）—— 可选是关键：`tests/unit/test_tdx_arbiter.py` 直接构造该通道，必须继续可构造。新增：

```python
    def prefetch(self, symbols: Iterable[str]) -> None:
        """Warm the per-symbol cache from chunked multi-code reads.

        Best effort in the same direction as the lazy path, and recorded the
        same way: a chunk that fails warns once per symbol, and a code the
        answer did not carry is an absent answer, not a cache miss -- it is
        reported through the same ``_report`` the lazy path uses rather than
        stored as a silent ``None``, which would turn a fail-closed case into
        an unexplained absence.
        """
        if self._fetch_frames is None:
            return
        pending = [
            symbol
            for symbol in dict.fromkeys(symbols)
            if symbol not in self._frames and symbol not in self._failed
        ]
        if not pending:
            return
        try:
            frames = self._fetch_frames(pending)
        except Exception as error:  # noqa: BLE001 - best-effort evidence channel
            for symbol in pending:
                self._report(symbol, error)
            return
        for symbol in pending:
            frame = frames.get(symbol)
            if frame is None:
                self._report(
                    symbol,
                    ContractError(f"xingyao returned no factor frame for {symbol!r}"),
                )
                continue
            if not frame.empty:
                try:
                    self._raw_snapshots.append(
                        self._record_raw(self._make_snapshot(symbol, frame))
                    )
                except Exception:  # noqa: BLE001 - evidence capture is best effort
                    pass
            self._frames[symbol] = frame
```

`_build_factor_channel` 的签名改为 `def _build_factor_channel(self, end: date, issues, raw_snapshots, candidates: Sequence[str] = ()):`，`update()` 里的那一行改为

```python
        factor_channel = self._build_factor_channel(
            end, issues, raw_snapshots, candidates
        )
```

并把 `return _LazyFactorChannel(...)` 改成先绑定局部变量、就地预取再返回：预取必须发生在**装了 `fetch_frames` 的那个实例**上（理由同 Task 3），先绑定也让这一步在后续改动里不易丢。`_LazyFactorChannel` 增加 `fetch_frames` 实参，其余实参一字不动：

```python
        channel = _LazyFactorChannel(
            "xingyao",
            lambda symbol: fetch_factor_frame(
                symbol,
                timeout_seconds=float(config.timeout_seconds),
                end=end,
            ),
            factor_event_dates,
            # ``snapshot_result`` demands ``end`` ... （既有注释与实参保持不动）
            lambda symbol, frame: snapshot_result(symbol, frame, end=end),
            end,
            issues=issues,
            raw_snapshots=raw_snapshots,
            record_raw=self._record_raw,
            fetch_frames=(
                None
                if config.factor_batch_size is None
                or config.factor_batch_timeout_seconds is None
                else lambda symbols: self._fetch_factor_chunks(
                    symbols,
                    chunk_size=int(config.factor_batch_size),
                    timeout_seconds=float(config.factor_batch_timeout_seconds),
                    end=end,
                )
            ),
        )
        channel.prefetch(candidates)
        return channel
```

`DataPipeline` 新增方法：

```python
    def _fetch_factor_chunks(
        self, symbols, *, chunk_size: int, timeout_seconds: float, end
    ):
        """Chunked multi-code factor reads, each chunk one session (ADR-020 D6)."""
        from stock_quant.data_sources.xingyao_factor import fetch_factor_frames

        frames: dict[str, pd.DataFrame] = {}
        for start in range(0, len(symbols), chunk_size):
            chunk = list(symbols[start : start + chunk_size])
            self._transport_attempt("xingyao", "backward_factor")(len(chunk))
            frames.update(
                fetch_factor_frames(chunk, timeout_seconds=timeout_seconds, end=end)
            )
        return frames
```

（函数内导入与 `_build_factor_channel` 既有写法一致。）

imports 追加：把 `ContractError` 补进 `from stock_quant.data_sources.base import (...)` 那一组（`prefetch` 对"答案没带这个 code"报的就是它）。

- [ ] **Step 5: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_xingyao_factor.py tests/unit/test_tdx_arbiter.py -q`
Expected: PASS（本任务新增/更新 7 项，含 `test_tdx_arbiter` 的 3 项因子预取用例）。

- [ ] **Step 6: 提交**

```bash
git add src/stock_quant/data_sources/xingyao_factor.py src/stock_quant/data_pipeline.py tests/unit/test_xingyao_factor.py
git commit -m "feat(factor): read a whole candidate chunk from one factor session

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 9: drift audit 按批次原形重放

**Files:**
- Modify: `project/drift_audit.py`
- Test: `tests/unit/test_drift_audit.py`

**Interfaces:**
- Consumes: `BatchEvidenceStore.load_by_sha`（Task 2）、`fetch_batch`（Task 5）、`build_config["batch_request_evidence"]`（Task 6）
- Produces: `replay_batch(source, evidence) -> dict[str, pd.DataFrame]`；`replay_recorded_batches(source, store, shas) -> dict[str, pd.DataFrame]`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/unit/test_drift_audit.py`：

```python
def _batch_evidence(symbols=("000001.SZ", "600000.SH")):
    from stock_quant.data_model.batch_evidence import (
        BatchOutcomeRecord,
        BatchRequestEvidence,
        batch_id_for,
        batch_request_parameters,
    )

    parameters = batch_request_parameters(
        "daily",
        symbols,
        date(2024, 1, 2),
        date(2024, 1, 5),
        {"adjustment": "unadjusted"},
    )
    return BatchRequestEvidence(
        source="xingyao",
        endpoint="daily",
        transport_id="xingyao-broker-tcp",
        batch_id=batch_id_for(parameters),
        batch_request_parameters=parameters,
        request_timestamp="2024-01-05T09:00:00+00:00",
        response_timestamp="2024-01-05T09:00:07+00:00",
        outcomes=tuple(
            BatchOutcomeRecord(symbol, symbol, "ok", "a" * 64) for symbol in symbols
        ),
    )


def _batch_result(symbols):
    from stock_quant.data_sources.xingyao import BatchOutcome, BatchResult

    return BatchResult(
        outcomes=tuple(BatchOutcome(symbol, "ok", _frame(symbol)) for symbol in symbols),
        transmissions=(),
    )


def test_a_batch_record_replays_the_whole_chunk_once():
    """A per-symbol re-ask cannot reproduce a batch answer (ADR-020 D8)."""
    from project.drift_audit import replay_batch

    asked: list[list[str]] = []

    class _Source:
        def fetch_batch(self, requests, **_):
            asked.append([r.symbols[0] for r in requests])
            return _batch_result([r.symbols[0] for r in requests])

    frames = replay_batch(_Source(), _batch_evidence())

    assert asked == [["000001.SZ", "600000.SH"]]
    assert set(frames) == {"000001.SZ", "600000.SH"}


def test_a_code_the_replay_did_not_answer_is_absent_not_invented():
    from project.drift_audit import replay_batch

    class _Source:
        def fetch_batch(self, requests, **_):
            return _batch_result([requests[0].symbols[0]])

    frames = replay_batch(_Source(), _batch_evidence())

    assert set(frames) == {"000001.SZ"}


def test_a_refused_code_contributes_no_frame():
    """An outcome with no result must not become a frame-shaped hole."""
    from project.drift_audit import replay_batch
    from stock_quant.data_sources.xingyao import BatchOutcome, BatchResult

    class _Source:
        def fetch_batch(self, requests, **_):
            return BatchResult(
                outcomes=(
                    BatchOutcome("000001.SZ", "ok", _frame("000001.SZ")),
                    BatchOutcome("600000.SH", "refused", None, "no key"),
                ),
                transmissions=(),
            )

    assert set(replay_batch(_Source(), _batch_evidence())) == {"000001.SZ"}


def test_every_recorded_batch_is_replayed_once(tmp_path):
    from project.drift_audit import replay_recorded_batches
    from stock_quant.data_sources.batch_evidence_store import BatchEvidenceStore

    store = BatchEvidenceStore(tmp_path)
    evidence = _batch_evidence()
    store.save(evidence)
    asked: list[list[str]] = []

    class _Source:
        def fetch_batch(self, requests, **_):
            asked.append([r.symbols[0] for r in requests])
            return _batch_result([r.symbols[0] for r in requests])

    frames = replay_recorded_batches(_Source(), store, [evidence.sha256])

    assert asked == [["000001.SZ", "600000.SH"]]
    assert set(frames) == {"000001.SZ", "600000.SH"}


def test_an_unresolvable_evidence_hash_is_skipped_not_guessed(tmp_path):
    from project.drift_audit import replay_recorded_batches
    from stock_quant.data_sources.batch_evidence_store import BatchEvidenceStore

    class _Source:
        def fetch_batch(self, requests, **_):  # pragma: no cover
            raise AssertionError("nothing should be replayed")

    frames = replay_recorded_batches(_Source(), BatchEvidenceStore(tmp_path), ["f" * 64])

    assert frames == {}
```

`_frame(symbol)` 用文件既有的 daily 帧构造方式（若没有，加一个返回 `code`/`kline_time` 单行的 DataFrame helper）。

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_drift_audit.py -q -k batch`
Expected: FAIL — `ImportError: cannot import name 'replay_batch'`

- [ ] **Step 3: 实现**

`project/drift_audit.py`：

```python
def replay_batch(source: object, evidence: BatchRequestEvidence) -> dict[str, pd.DataFrame]:
    """Re-ask exactly the recorded batch, once, and split it back per code.

    Degrading a batch record into per-symbol re-asks would change the
    question: an absent key, a truncation or a different answer shape can
    depend on which codes travelled together, so the comparison would no
    longer be like-for-like.  A code the replay did not answer is simply
    absent -- never synthesised.
    """
    parameters = json.loads(evidence.batch_request_parameters)
    requests = [
        DataRequest(
            str(parameters["endpoint"]),
            (str(symbol),),
            date.fromisoformat(str(parameters["start_date"])),
            date.fromisoformat(str(parameters["end_date"])),
            dict(parameters.get("params") or {}),
        )
        for symbol in parameters["symbols"]
    ]
    result = source.fetch_batch(requests)
    return {
        outcome.symbol: outcome.result.frame
        for outcome in result.outcomes
        if outcome.result is not None
    }


def replay_recorded_batches(
    source: object, store: BatchEvidenceStore, shas: Sequence[str]
) -> dict[str, pd.DataFrame]:
    """Replay every batch the version bound to, and return frames by symbol.

    ``build_config.batch_request_evidence`` records hashes, so the resolution
    starts from the hash: an unresolvable one is skipped rather than turned
    into a guessed single-symbol re-ask.
    """
    frames: dict[str, pd.DataFrame] = {}
    for sha in shas:
        evidence = store.load_by_sha(sha)
        if evidence is None:
            continue
        frames.update(replay_batch(source, evidence))
    return frames
```

在 `run(...)` 的目标循环里接线：从版本 manifest 的 `build_config["batch_request_evidence"]` 取出哈希列表，对落在批次记录里的标的用 `replay_recorded_batches` 的结果做比较，其余标的保持既有的单请求重放路径。

- [ ] **Step 4: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_drift_audit.py -q`
Expected: PASS（新增 5 项 + 既有单请求路径用例全绿）。

- [ ] **Step 5: 提交**

```bash
git add project/drift_audit.py tests/unit/test_drift_audit.py
git commit -m "feat(drift-audit): replay a recorded batch as the batch it was

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 10: 真实探针（联网，需 owner 授权）

**Files:**
- Create: `project/probe_batch_channel.py`
- Create: `docs/operations/2026-09-27-batched-channel-probes.md` + `.evidence.json`

**Interfaces:**
- Consumes: `XingyaoSource.fetch_batch`（Task 5）、`fetch_factor_frames`（Task 8）
- Produces: 冻结 spec §8 的五项常量，并落进两份 `sources.yml`

> **STOP — 本任务要联网、要用凭据，必须先拿到 owner 的明确授权再运行。** 未获授权前只写脚本与记录骨架，不执行、不冻结任何值。

- [ ] **Step 1: 写探针脚本（不运行）**

`project/probe_batch_channel.py`，四个子命令，各自打印结构化读数并 `--evidence` 写 JSON：

```python
"""Freeze the batch-channel constants from measurement (spec §6/§8).

Never run this without the owner's go-ahead: it logs in to the broker and
consumes quota.  Every reading is printed and written to an evidence JSON so
the frozen default can be traced to a measurement rather than to a guess.
"""
```

1. `probe-limit --endpoint daily|backward_factor`：日线从 1000 起、因子由小到大试探单次 code 上限，遇到缺键、报错或截断即停，输出最大可接受 code 数。
2. `probe-absence`：对**窗口内一个交易日都没有**的标的（默认 601238.SH 的 2026-09-14..2026-09-24 纯停牌窗口）做多 code 调用，打印返回 mapping 的键集合与每键行数；同时打印一个"窗口内既有交易日又有停牌日"的标的作对照。
3. `probe-latency --endpoint daily|backward_factor`：全片一次调用的墙钟耗时（多次取最大），输出建议 `batch_timeout_seconds = 耗时 × 3`。
4. `probe-counters`：一轮真实会话与计数器读数（日线 `sessions`/`code_queries` 应为 1；因子为 `ceil(candidate/factor_batch_size)`）。

- [ ] **Step 2: 请求授权并运行**

向 owner 报告四条命令、预计配额与时间，拿到明确许可后逐条运行，把输出（含 `.evidence.json`）落到 `docs/operations/2026-09-27-batched-channel-probes.md`。

- [ ] **Step 3: 按探针 2 的结果分支**

- **"有 key、帧空"** → 保留 `empty` 分支与 `test_an_empty_answer_is_evidence_and_does_not_touch_the_status`。
- **"无 key"** → **停下回报 owner**：fail-closed 默认会让每个长期停牌标的每轮记一次逐标的 `refused`。要么接受该噪音，要么由 owner 裁定引入独立状态；不得自行改判为 `empty`，也不得删掉 `refused` 用例。
- **符号端点从不返回零行对象** → 按 spec §4 收尾段落**删掉 `empty` 分支**及其用例（不保留实测不可达的状态），并同步 ADR-020。

- [ ] **Step 4: 冻结默认值**

按实测把两份 `sources.yml` 的注释值放开为实测值，并把读数写进 ADR-020 的 Evidence 一节（Task 11）。

- [ ] **Step 5: 提交**

```bash
git add project/probe_batch_channel.py docs/operations/2026-09-27-batched-channel-probes.md docs/operations/2026-09-27-batched-channel-probes.evidence.json project/configs/sources.yml templates/project-config/sources.yml
git commit -m "docs(operations): freeze the batch-channel constants from measurement

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 11: ADR-020 与文档同步

**Files:**
- Create: `docs/adr/020-batched-validation-channel.md`
- Modify: `docs/adr/DECISIONS_INDEX.md`、`docs/adr/016-xingyao-baostock-succession.md`、`RUNBOOK.md`、`docs/architecture/data-flow.md`

**Interfaces:**
- Consumes: 全部前序任务
- Produces: `docs/adr/020-batched-validation-channel.md`（`status: accepted`）与索引项

- [ ] **Step 1: 写 ADR-020**

按 `docs/adr/` 既有文件的 frontmatter 形状写 D1–D8（条文见 spec §7）：

```yaml
---
status: accepted
date: 2026-09-27
decision: "<一句话决策；值里出现冒号必须整段加引号——019 因此坏了>"
affects:
  - src/stock_quant/data_sources/xingyao.py
  - src/stock_quant/data_sources/xingyao_factor.py
  - src/stock_quant/data_sources/base.py
  - src/stock_quant/data_sources/batch_evidence_store.py
  - src/stock_quant/data_model/batch_evidence.py
  - src/stock_quant/data_model/call_ledger.py
  - src/stock_quant/data_pipeline.py
  - src/stock_quant/config.py
  - project/drift_audit.py
  - project/configs/sources.yml
---
```

正文含：

- **Context**：三条每请求一次会话的通路（xingyao 日线校验车道、因子通道、tdx 仲裁器）；ADR-016 decision 11 的启用前置；探针 5 显示 F < 0.0025 单位、不可分辨。
- **Decision**：D1–D8 逐条。
- **What this does not change**：`xingyao.enabled` 仍为 `false`；单标的 `fetch` 的严格语义（空帧仍 `ContractError`）；`RawSnapshot` 路径布局与 `request_key`；`REUSABLE_CHANNELS`；ADR-013 链；ADR-009 的复用资格判据。
- **Implementation notes**：`fetch_batch` 返回 `BatchResult`（而非 spec §5 的裸列表）及其理由；子进程只回可运输数据、逐标的判定归父进程；批次证据独立于 raw 树（`RawStore.save` 去重会吞掉后写的 manifest）。
- **Consequences**：tdx 的 `code_queries` 真实增加（`N → N`，多 code 能力不存在）；批次证据不进数据集身份，只以哈希绑定；`empty` 分支的存废由探针 2 定；`SourceStatus` 词汇扩充 `batch_fetch_failure`。
- **Rejected alternatives**：把批次 metadata 塞进逐标的 manifest；把"缺 key"映射为 `empty`；对限流做二分；用 `fetch_batch` 替换 `fetch` 并放宽其严格性。
- **Evidence**：`docs/operations/2026-09-27-batched-channel-probes.md` 与 `.evidence.json`。

- [ ] **Step 2: ADR-016 加注记**

在 decision 11 段落后加（**不改写 decision 11 本身**）：

```markdown
**注记（2026-09-27）**：批量通道前置已落地（ADR-020）。启用仍是单独动作，
decision 11 的门禁在启用动作上继续有效。
```

- [ ] **Step 3: 索引与既有文档**

- `docs/adr/DECISIONS_INDEX.md` 加 020 行（`tools/check_context_governance.py` 校验链接可达）。
- `RUNBOOK.md`：xingyao 启用程序一节的"前置：批量通道"标为已落地；把"四个逐符号车道"改为准确现状（xingyao 校验车道与因子通道为分片批量、缺批量配置时回退逐标的，tdx 为一次会话预取 + 懒取兜底）；**不动** `enabled` 的值。
- `docs/architecture/data-flow.md`：原文 "each per-symbol request on the four `_dispatch` lanes" 补一句批量形状（哪条车道批量、哪条仍逐请求、证据为何仍逐标的一路径、批次证据在哪个 registry）。
- `docs/superpowers/specs/2026-09-26-xingyao-baostock-succession-design.md`：§3.1（"批次 1：校验车道接替"）结尾加一行回写——「批量通道已落地，见 ADR-020」；§3.3 若同样写明逐标的形状，加同一行。**不改写该 spec 的描述**，只加指向。

- [ ] **Step 4: 跑治理与文档测试**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_context_governance_docs.py -q`
Expected: 020 相关项 PASS。若仍红，逐条确认失败文件——**若是 `docs/adr/019-price-basis-representation.md` 的 frontmatter**（既有 WIP，本节开工前就红），报告 owner。

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_source_config_batch.py tests/unit/test_batch_request_evidence.py tests/unit/test_xingyao_source.py tests/unit/test_xingyao_batch_lane.py tests/unit/test_xingyao_factor.py tests/unit/test_tdx_arbiter.py tests/unit/test_call_ledger.py -q`
Expected: PASS（spec §6 验收清单的核心部分）。

- [ ] **Step 5: 提交**

```bash
git add docs/adr/020-batched-validation-channel.md docs/adr/DECISIONS_INDEX.md docs/adr/016-xingyao-baostock-succession.md RUNBOOK.md docs/architecture/data-flow.md docs/superpowers/specs/2026-09-26-xingyao-baostock-succession-design.md
git commit -m "docs(adr): record the batched validation channel as ADR-020

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## 完成前的验收

按 spec §6 的验收清单逐条跑（全部点名文件，不用裸 `pytest`）：

```bash
/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_xingyao_source.py tests/unit/test_xingyao_factor.py -q
/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_tdx_arbiter.py tests/unit/test_batch_request_evidence.py tests/unit/test_source_config_batch.py -q
/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_isolated_call.py tests/unit/test_xingyao_batch_lane.py tests/unit/test_call_ledger.py -q
/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_drift_audit.py tests/unit/test_raw_reuse.py tests/unit/test_raw_store.py -q
/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_pipeline_fetch_coverage.py tests/integration/test_source_contracts.py -q
```

**不在本次验收范围内**（按 spec 决策 4）：一次真实更新。`xingyao.enabled` 仍为 `false`，通道落地即止。
