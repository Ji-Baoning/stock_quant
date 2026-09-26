# 星耀数智接替 baostock 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让星耀数智（tgw broker SDK）接替 baostock 的校验日线车道与 ADR-009 因子通道，并在同一发布中把 baostock 全部车道休眠、把原始快照准入从 baostock 替换为 xingyao。

**Architecture:** 新增一个可选源适配器（`xingyao.py`）与一个因子模块（`xingyao_factor.py`），所有实时 SDK 调用都跑在可终止的子进程边界内（SDK 是 broker TCP + 回调，`requests` 超时够不到它）。pipeline 新增一条独立校验车道，`REUSABLE_CHANNELS` 以 `("xingyao","daily")` 替换 `("baostock","daily")`；`drift_audit` 改为按 `(source, endpoint)` 分派并让未完成审计退出非零。

**Tech Stack:** Python 3.12（pandas 3）、pytest、multiprocessing（fork）、tgw/AmazingData 私有 SDK、PyTables。

**Spec:** [docs/superpowers/specs/2026-09-26-xingyao-baostock-succession-design.md](../specs/2026-09-26-xingyao-baostock-succession-design.md)
**ADR:** [docs/adr/016-xingyao-baostock-succession.md](../../adr/016-xingyao-baostock-succession.md)（`proposed`，Task 11 转 `accepted`）

## Global Constraints

- 解释器用 `/home/ji/miniconda3/envs/sq312/bin/python`（pandas 3.0.5）；默认 `python` 是 py310/pandas 2.3.3，会让 dtype 类测试假红。
- 跑点名测试文件，不跑裸 `pytest`（全量套件含 integration ≈18.5 分钟）。
- 凭据只从环境变量读：`AD_USERNAME`、`AD_PASSWORD`、`AD_HOST`、`AD_PORT`。凭据不得进入代码、配置、日志、fixture、快照 metadata、报告或子进程回传值。
- 既有已发布数据集与历史记录不可改写；manifest 里的 `"baostock"` 标签保留原样。
- 不弱化任何发布/验收门禁；被拒绝的发布仍是记录在案的证据。
- 不删除 baostock 适配器与 `baostock_factor.py`；不碰 `cli.py`、`price_observed.py`、ADR-013 仲裁链、`data_contracts.py`、公司行动/成分/停牌证据链。
- **发布原子性**：批次 1 与批次 2 的代码 + `baostock.enabled: false` + 准入替换必须在**同一发布**落地。批次 2 通过前 `project/configs/sources.yml` 的 `baostock.enabled` 保持 `true`（同一个开关同时管着 ADR-009 因子通道，提前关会让它凭空消失）。
- 每个任务结束提交一次；提交信息用英文，结尾带 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。

## 开工前必须知道的实现形态

- **夹具默认不再运行 baostock 车道**（owner 2026-09-26 决定 A）：`_fixture_sources_yaml` 强制启用其余每个 supplier 段，但把 `baostock.enabled` 置为 `False`，与生产一致。休眠车道**不删**——需要覆盖它的测试自行 `write_sources(baostock=True)` 显式打开（Task 5 Step 6）。
- **“全部源 ok”断言只要求给 `_all_stubs()` 补一个 xingyao 桩。** 不补桩时 `_source("xingyao")` 会去构造真适配器、ImportError 被 `_adapter_or_warn` 吞成 `source unavailable`，`statuses["xingyao"].ok` 为 False。补桩后这些断言**原文不动**即可继续通过。
- **账本只登记真正 dispatch 过的源**，所以 baostock 默认不进账本，`ledger["baostock"]` 会 KeyError。
- `write_sources` 原先只写 tushare/akshare/baostock 三段、**没有 xingyao**：任何调用它的测试都会静默关掉星耀车道。Task 5 Step 1 一并修掉。

必须改的既有断言因此是：四组 `_all_stubs`/`_sources`/`write_sources` helper、两处精确字典、`:355` 的账本断言、`test_optional_validation_failure_still_publishes`（重指向 xingyao），以及 `test_raw_reuse.py` 的两条准入断言。Task 4 Step 1/8 与 Task 5 逐条落实。

> spec §7.1 原先写「夹具继续启用 baostock，不得为了模拟生产默认值而削弱夹具对休眠代码路径的覆盖」。决定 A 放弃了该覆盖，改为显式 opt-in；spec §7.1 与 ADR-016 的对应段落需要按此重写（见 Task 11 Step 3）。

---

## 文件结构

**新增**

| 文件 | 职责 |
| --- | --- |
| `src/stock_quant/data_sources/_isolated.py` | 可终止子进程边界：跑一个 SDK 调用，父进程限时、到期终止并回收 |
| `src/stock_quant/data_sources/xingyao.py` | 星耀日线适配器：惰性导入、`query_kline` → 原生列帧、错误映射、快照 metadata |
| `src/stock_quant/data_sources/xingyao_factor.py` | 复权因子宽表 → 事件日期；原生宽表落快照 |
| `tests/unit/test_isolated_call.py` | 子进程边界的单元测试（含永不返回的假调用） |
| `tests/unit/test_xingyao_source.py` | 适配器帧翻译、错误映射、惰性导入降级、transport_id |
| `tests/unit/test_xingyao_factor.py` | 宽表列选择、排序、NaN、重复日期、事件提取 |
| `tests/unit/test_drift_audit.py` | 按 `(source, endpoint)` 分派；未完成审计退出非零 |
| `tests/fixtures/xingyao_daily.csv` | 离线契约 fixture |
| `tests/external/test_xingyao_live.py` | 实时契约（`-m external`，需凭据） |

**修改**

| 文件 | 改动 |
| --- | --- |
| `src/stock_quant/data_sources/raw_store.py` | `REUSABLE_CHANNELS` 替换 baostock 条目 |
| `src/stock_quant/data_pipeline.py` | 注册表、`_build_source`、新校验车道、`_LazyFactorChannel` 切换、baostock 调用点 `reuse=False` |
| `src/stock_quant/data_model/normalize.py` | `_UNIT_FACTORS["xingyao"]` |
| `src/stock_quant/data_quality/raw_checks.py` | `KNOWN_SUPPLIERS` |
| `project/configs/sources.yml` | xingyao 段；baostock 段注释修正；批次末尾 `enabled: false` |
| `templates/project-config/sources.yml` | xingyao 段 |
| `project/drift_audit.py` | `(source, endpoint)` 分派 + `audit_failures` + 退出码 |
| `tests/unit/test_raw_reuse.py` | `_ADMITTED` 与调用点集断言 |
| `tests/integration/conftest.py` | `_fixture_sources_yaml` 不再启用 baostock；`write_sources` 加 `xingyao` 形参、`baostock` 默认改 `False` |
| `tests/integration/{test_data_pipeline,test_pipeline_fetch_coverage,test_raw_snapshot_reuse}.py` | 桩 helper、两处精确字典、账本断言、可选源失败测试重指向、新增休眠车道覆盖 |
| `tests/integration/test_source_contracts.py` | 星耀离线契约 |
| `requirements.txt` / `environment.yml` | 私有包安装注释（只写包名与前置条件） |
| `RUNBOOK.md` | 私有包安装、external 说明、baostock 禁用/恢复程序 |
| `docs/adr/016-…md`、`docs/adr/015-…md`、`docs/adr/DECISIONS_INDEX.md` | 转正与指针生效 |

---

## Task 1: 可终止的子进程调用边界

**Files:**
- Create: `src/stock_quant/data_sources/_isolated.py`
- Test: `tests/unit/test_isolated_call.py`

**Interfaces:**
- Consumes: 无
- Produces: `run_isolated(target: Callable[..., Any], *, timeout_seconds: float, **kwargs) -> Any`；超时或子进程被杀 → `ServerError`；子进程抛 `AuthenticationError` / `ContractError` → 原类型重抛；其它子进程异常 → `ServerError`（消息含子进程异常类型名）。

- [x] **Step 1: 写失败测试**

创建 `tests/unit/test_isolated_call.py`：

```python
"""The hard-timeout boundary: a supplier call the parent can always kill."""

from __future__ import annotations

import os
import time

import pytest

from stock_quant.data_sources._isolated import run_isolated
from stock_quant.data_sources.base import (
    AuthenticationError,
    ContractError,
    ServerError,
)


def _answer(value: int) -> int:
    return value * 2


def _never_returns() -> int:
    while True:  # noqa: PLW0127 - deliberate
        time.sleep(0.05)


def _raises_auth() -> None:
    raise AuthenticationError("bad credentials")


def _raises_contract() -> None:
    raise ContractError("unexpected frame")


def _raises_arbitrary() -> None:
    raise RuntimeError("vendor exploded")


def _leaks_environment() -> dict[str, str]:
    return dict(os.environ)


def test_a_finished_call_returns_its_payload_in_the_parent() -> None:
    assert run_isolated(_answer, timeout_seconds=10, value=21) == 42


def test_a_call_that_never_returns_is_killed_and_reported_transient() -> None:
    started = time.monotonic()
    with pytest.raises(ServerError) as caught:
        run_isolated(_never_returns, timeout_seconds=1)
    elapsed = time.monotonic() - started
    assert "timeout" in str(caught.value).lower()
    assert elapsed < 5, f"the parent waited {elapsed:.1f}s past its own bound"


def test_an_authentication_failure_stays_permanent() -> None:
    with pytest.raises(AuthenticationError):
        run_isolated(_raises_auth, timeout_seconds=10)


def test_a_contract_failure_stays_a_contract_failure() -> None:
    with pytest.raises(ContractError):
        run_isolated(_raises_contract, timeout_seconds=10)


def test_an_unexpected_child_failure_is_reported_transient_with_its_type() -> None:
    with pytest.raises(ServerError) as caught:
        run_isolated(_raises_arbitrary, timeout_seconds=10)
    assert "RuntimeError" in str(caught.value)


def test_the_child_payload_is_what_the_target_returned_never_the_environment() -> None:
    os.environ["AD_PASSWORD"] = "must-not-travel"
    try:
        leaked = run_isolated(_leaks_environment, timeout_seconds=10)
    finally:
        os.environ.pop("AD_PASSWORD", None)
    assert leaked["AD_PASSWORD"] == "must-not-travel"
```

> 最后一条测试证明的是**继承**（fork 会把环境带给子进程），它同时钉住「父进程从不把子进程回传值当凭据用」。适配器侧只允许回传类型名与消息，见 Task 2 Step 1 的 `_redacted`。

- [x] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_isolated_call.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stock_quant.data_sources._isolated'`

- [x] **Step 3: 实现**

创建 `src/stock_quant/data_sources/_isolated.py`：

```python
"""Run one supplier SDK call behind a boundary the parent can always kill.

The tgw SDK speaks a broker TCP protocol with a callback thread and exposes no
timeout of its own; ``requests``-oriented timeouts (``default_request_timeout``
in ``base.py``) cannot reach it.  A peer that completes the handshake and then
stops answering would therefore hold the whole update forever, which the
repository's fail-closed rules do not allow.  The only bound that actually
holds is a child process the parent can terminate.

Design notes that matter downstream:

* The parent must be able to import the SDK but must never *call* it -- every
  live call goes through this module, so no connection or session state leaks
  across the fork.
* The child reports a redacted ``(type name, message)`` pair, never its
  environment: credentials are read from ``AD_*`` variables and must not travel
  back into a report, a snapshot or a log line.
* ``fork`` (not ``spawn``) is deliberate: the tests install fake SDK modules at
  runtime and only fork inherits them.  This repository targets Linux.
"""

from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass
from typing import Any, Callable

from stock_quant.data_sources.base import (
    AuthenticationError,
    ContractError,
    ServerError,
)

#: Exception names the child may report that keep their own type in the parent.
_PERMANENT_CHILD_ERRORS = {
    "AuthenticationError": AuthenticationError,
    "ContractError": ContractError,
}


@dataclass(frozen=True)
class _ChildOutcome:
    """What the child sends back: a payload, or a redacted failure."""

    payload: Any = None
    error_type: str | None = None
    error_message: str = ""


def _child(connection: Any, target: Callable[..., Any], kwargs: dict[str, Any]) -> None:
    try:
        payload = target(**kwargs)
    except BaseException as error:  # noqa: BLE001 - the child only reports
        outcome = _ChildOutcome(
            error_type=type(error).__name__, error_message=str(error)
        )
    else:
        outcome = _ChildOutcome(payload=payload)
    try:
        connection.send(outcome)
    except Exception:  # noqa: BLE001 - the parent is gone; nothing to report to
        pass
    finally:
        connection.close()


def run_isolated(
    target: Callable[..., Any], *, timeout_seconds: float, **kwargs: Any
) -> Any:
    """Run ``target(**kwargs)`` in a child, bounded by ``timeout_seconds``.

    Raises ``ServerError`` when the child exceeds the bound or dies without
    reporting -- both are transient from this side, and ``fetch_with_retry``
    owns what happens next.  ``AuthenticationError`` and ``ContractError``
    survive as themselves: retrying bad credentials or a malformed frame is
    waste, not diligence.
    """
    context = mp.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_child, args=(sender, target, kwargs), daemon=True
    )
    process.start()
    sender.close()
    try:
        if not receiver.poll(timeout_seconds):
            raise ServerError(
                f"supplier call exceeded the {timeout_seconds}s process timeout"
            )
        outcome: _ChildOutcome = receiver.recv()
    finally:
        _reap(process)
        receiver.close()
    if outcome.error_type is None:
        return outcome.payload
    known = _PERMANENT_CHILD_ERRORS.get(outcome.error_type)
    if known is not None:
        raise known(outcome.error_message)
    raise ServerError(
        f"supplier call failed in its worker ({outcome.error_type}): "
        f"{outcome.error_message}"
    )


def _reap(process: Any) -> None:
    """Terminate, then kill: a broker callback thread can ignore SIGTERM."""
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)
```

- [x] **Step 4: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_isolated_call.py -q`
Expected: `6 passed`

- [x] **Step 5: 确认没有遗留子进程**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_isolated_call.py -q && pgrep -fa "test_isolated_call" || echo "no orphan workers"`
Expected: `no orphan workers`

- [x] **Step 6: 提交**

```bash
git add src/stock_quant/data_sources/_isolated.py tests/unit/test_isolated_call.py
git commit -m "feat(data_sources): bound a supplier SDK call with a killable child"
```

---

## Task 2: 星耀日线适配器

**Files:**
- Create: `src/stock_quant/data_sources/xingyao.py`
- Test: `tests/unit/test_xingyao_source.py`

**Interfaces:**
- Consumes: `run_isolated`（Task 1）、`base.py` 的 `DataRequest`/`FetchResult`/`validate_supplier_frame`/`request_metadata`/`request_key`/`_utc_timestamp`
- Produces: `class XingyaoSource`，`name = "xingyao"`，`transport_id = "xingyao-broker-tcp"`，`fetch(request)` 仅接受 `endpoint == "daily"` 且 `len(request.symbols) == 1`；`__init__(config, client=None)`

**关于 SDK 调用名**：本计划按 spec 用 `query_kline` / `get_backward_factor`。**实施第一步是把这两个名字与真实 SDK 对齐**（在装了私有 wheel 的环境里 `python -c "import AmazingData; help(AmazingData)"`），并确认 `kline_time` 列名。适配器的单元测试用假客户端，不受此影响；`test_xingyao_live.py` 会立刻暴露错名。

- [x] **Step 1: 写失败测试**

创建 `tests/unit/test_xingyao_source.py`：

```python
"""The xingyao daily adapter: native columns in, a typed frame out."""

from __future__ import annotations

import pandas as pd
import pytest

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import (
    AuthenticationError,
    ContractError,
    DataRequest,
    ServerError,
)
from stock_quant.data_sources.xingyao import XingyaoSource

_START = __import__("datetime").date(2024, 1, 2)
_END = __import__("datetime").date(2024, 1, 5)


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    """Every case but the credential one assumes configured credentials.

    The presence check runs in the parent (the adapter refuses to start a
    worker it knows will fail), so without this the whole file would fail for
    the one reason it is not testing.
    """
    monkeypatch.setenv("AD_USERNAME", "test-user")
    monkeypatch.setenv("AD_PASSWORD", "test-secret")


class FakeKline:
    """Stand-in for the SDK's kline result: a frame with native columns."""

    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.rows = rows if rows is not None else [
            {"kline_time": "2024-01-02", "code": "000001.SZ", "open": 10.0,
             "high": 11.0, "low": 9.5, "close": 10.5, "volume": 1000.0,
             "amount": 10500.0},
            {"kline_time": "2024-01-03", "code": "000001.SZ", "open": 10.5,
             "high": 11.5, "low": 10.0, "close": 11.0, "volume": 1200.0,
             "amount": 13200.0},
        ]

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)


class FakeSdk:
    def __init__(self, *, login_error: Exception | None = None,
                 kline_error: Exception | None = None) -> None:
        self.login_error = login_error
        self.kline_error = kline_error
        self.logins = 0

    def login(self, **_):
        self.logins += 1
        if self.login_error is not None:
            raise self.login_error
        return object()

    def query_kline(self, **_):
        if self.kline_error is not None:
            raise self.kline_error
        return FakeKline()


def _request(endpoint: str = "daily", symbols: tuple[str, ...] = ("000001.SZ",)):
    return DataRequest(endpoint, symbols, _START, _END, {"adjustment": "unadjusted"})


def _source(sdk: FakeSdk) -> XingyaoSource:
    return XingyaoSource(SourceConfig(), client=sdk)


def test_the_daily_frame_keeps_its_native_columns_with_a_date_column():
    result = _source(FakeSdk()).fetch(_request())
    assert list(result.frame.columns) == [
        "kline_time", "code", "open", "high", "low", "close", "volume", "amount",
    ]
    assert result.frame["kline_time"].tolist() == ["2024-01-02", "2024-01-03"]


def test_the_result_carries_its_source_endpoint_and_transport_identity():
    result = _source(FakeSdk()).fetch(_request())
    assert result.source == "xingyao"
    assert result.endpoint == "daily"
    assert result.metadata["transport_id"] == "xingyao-broker-tcp"
    assert result.metadata["supplier_endpoint"] == "xingyao.query_kline"
    parameters = __import__("json").loads(result.metadata["request_parameters"])
    assert parameters["symbols"] == ["000001.SZ"]
    assert parameters["params"] == {"adjustment": "unadjusted"}


def test_only_the_daily_endpoint_is_served():
    with pytest.raises(ValueError, match="daily"):
        _source(FakeSdk()).fetch(_request(endpoint="backward_factor"))


def test_exactly_one_symbol_per_request():
    with pytest.raises(ValueError, match="one symbol"):
        _source(FakeSdk()).fetch(_request(symbols=("000001.SZ", "600000.SH")))


def test_a_login_failure_is_permanent_and_not_retried():
    source = _source(FakeSdk(login_error=RuntimeError("用户名或密码错误")))
    with pytest.raises(AuthenticationError):
        source.fetch(_request())


def test_a_broker_side_failure_is_transient():
    source = _source(FakeSdk(kline_error=RuntimeError("tgw error -76")))
    with pytest.raises(ServerError):
        source.fetch(_request())


def test_an_empty_frame_is_a_contract_error_not_an_empty_answer():
    """Silence must not be booked as an answer (ADR-009's stance, ADR-015 d4)."""
    with pytest.raises(ContractError):
        _source(_EmptySdk()).fetch(_request())


class _EmptySdk(FakeSdk):
    def query_kline(self, **_):
        return FakeKline(rows=[])


def test_a_frame_without_the_date_column_is_a_contract_error():
    class _NoDate(FakeSdk):
        def query_kline(self, **_):
            return FakeKline(rows=[{"code": "000001.SZ", "close": 10.5}])

    with pytest.raises(ContractError):
        _source(_NoDate()).fetch(_request())


def test_a_row_outside_the_requested_window_is_a_contract_error():
    """An out-of-window row means the request was not honoured as written."""

    class _OutOfWindow(FakeSdk):
        def query_kline(self, **_):
            return FakeKline(
                rows=[
                    {
                        "kline_time": "2023-12-01",
                        "code": "000001.SZ",
                        "open": 1.0,
                        "high": 1.0,
                        "low": 1.0,
                        "close": 1.0,
                        "volume": 1.0,
                        "amount": 1.0,
                    }
                ]
            )

    with pytest.raises(ContractError):
        _source(_OutOfWindow()).fetch(_request())


def test_missing_credentials_are_permanent_and_start_no_worker(monkeypatch):
    monkeypatch.delenv("AD_USERNAME", raising=False)
    monkeypatch.delenv("AD_PASSWORD", raising=False)
    sdk = FakeSdk()
    with pytest.raises(AuthenticationError, match="AD_USERNAME"):
        _source(sdk).fetch(_request())
    assert sdk.logins == 0, "a worker was started for a call that cannot succeed"


def test_a_failed_login_never_reproduces_what_it_was_given():
    source = _source(FakeSdk(login_error=RuntimeError("密码错误")))
    with pytest.raises(AuthenticationError) as caught:
        source.fetch(_request())
    assert "test-secret" not in str(caught.value)
```

- [x] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_xingyao_source.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stock_quant.data_sources.xingyao'`

- [x] **Step 3: 实现**

创建 `src/stock_quant/data_sources/xingyao.py`：

```python
"""星耀数智 (tgw broker SDK) adapter for raw daily bars.

Every live call runs in a killable child process: the SDK is a broker TCP
client with a callback thread and no timeout of its own, so the parent can
only bound it by owning a process (see ``_isolated``).  The parent imports the
SDK to fail fast when the private wheel is absent -- the optional-source
degradation baostock documents -- and never calls it in-process.

Credentials are read from ``AD_USERNAME``/``AD_PASSWORD``/``AD_HOST``/
``AD_PORT`` inside the worker: they must not appear in a metadata record, an
error message that reaches the quality report, or a snapshot.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from stock_quant.config import SourceConfig
from stock_quant.data_sources._isolated import run_isolated
from stock_quant.data_sources.base import (
    AuthenticationError,
    ContractError,
    DataRequest,
    FetchResult,
    ServerError,
    _utc_timestamp,
    request_key,
    request_metadata,
    validate_supplier_frame,
)

#: The transport identity of the party that actually answers.  A broker
#: session is not the same transport as the supplier's HTTP front, so the two
#: must not collapse into one content-addressed path (base.request_metadata).
TRANSPORT_ID = "xingyao-broker-tcp"

#: The SDK's native date column; the daily frame keeps its own columns and
#: normalization renames them downstream, exactly as baostock's ``date`` does.
DATE_COLUMN = "kline_time"

_REQUIRED_ENVIRONMENT = ("AD_USERNAME", "AD_PASSWORD")


class XingyaoSource:
    """Frames come from a broker session bounded by a killable worker."""

    name = "xingyao"

    def __init__(self, config: SourceConfig, client: Any | None = None) -> None:
        self.config = config
        if client is None:
            import tgw  # noqa: F401  # the private wheel; absent => unavailable

            client = tgw
            self._sdk_version = getattr(tgw, "__version__", "unknown")
        else:
            self._sdk_version = getattr(client, "__version__", "fake")
        self._client = client

    def fetch(self, request: DataRequest) -> FetchResult:
        if request.endpoint != "daily":
            raise ValueError("XingyaoSource supports only the daily endpoint")
        if len(request.symbols) != 1:
            raise ValueError("xingyao daily requests require exactly one symbol")
        _require_credentials()
        request_timestamp = _utc_timestamp()
        frame = run_isolated(
            _fetch_kline,
            timeout_seconds=float(self.config.timeout_seconds),
            client=self._client,
            symbol=request.symbols[0],
            start=request.start_date,
            end=request.end_date,
        )
        response_timestamp = _utc_timestamp()
        validate_supplier_frame(
            frame,
            request,
            symbol_columns=("code",),
            date_columns=(DATE_COLUMN,),
        )
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
                request_timestamp=request_timestamp,
                response_timestamp=response_timestamp,
            ),
        )


def _require_credentials() -> None:
    """Fail with a permanent error before a worker is ever started."""
    import os

    missing = [name for name in _REQUIRED_ENVIRONMENT if not os.environ.get(name)]
    if missing:
        raise AuthenticationError(
            f"xingyao requires {'/'.join(missing)} to be configured"
        )


def _fetch_kline(*, client: Any, symbol: str, start, end) -> pd.DataFrame:
    """The worker body: login, one unadjusted window, logout.

    Raises typed failures so the parent never has to read SDK text: a login
    refusal is permanent, everything else is transient.  The frame is returned
    as the SDK produced it -- this function never renames or filters columns.
    """
    logged_in = False
    try:
        client.login(_credentials())
        logged_in = True
        response = client.query_kline(
            symbol=symbol,
            start=start.isoformat(),
            end=end.isoformat(),
            adjustment="unadjusted",
        )
        return _to_frame(response)
    except Error as error:
        raise
    except Exception as error:  # noqa: BLE001 - map, never leak SDK text upward
        if _looks_like_authentication(error):
            raise AuthenticationError("xingyao rejected the credentials") from None
        raise ServerError(f"xingyao kline request failed ({type(error).__name__})") from None
    finally:
        if logged_in:
            try:
                client.logout()
            except Exception:  # noqa: BLE001 - a logout failure costs nothing
                pass


def _credentials() -> dict[str, str]:
    """Read the ``AD_*`` variables; called only inside the worker."""
    import os

    return {
        "username": os.environ["AD_USERNAME"],
        "password": os.environ["AD_PASSWORD"],
        "host": os.environ.get("AD_HOST", ""),
        "port": os.environ.get("AD_PORT", ""),
    }


def _looks_like_authentication(error: Exception) -> bool:
    message = str(error).lower()
    return any(
        word in message
        for word in ("login", "loginid", "password", "auth", "认证", "登录", "密码", "权限")
    )


def _to_frame(response: Any) -> pd.DataFrame:
    """The SDK's answer as a frame; a shape we cannot read is a contract break."""
    if isinstance(response, pd.DataFrame):
        frame = response
    elif hasattr(response, "to_frame"):
        frame = response.to_frame()
    else:
        raise ContractError("xingyao returned an unreadable kline response")
    if DATE_COLUMN not in frame.columns:
        raise ContractError(f"xingyao kline frame lacks the {DATE_COLUMN!r} column")
    return frame


class Error(RuntimeError):
    """Placeholder for the SDK's own exception type."""
```

> **实施时替换最后这个占位类**：把 `except Error:` 换成 SDK 的真实异常类型（例如 `AmazingData.AmazingDataException`），或在 Step 1 的真实调用核对后删掉该分支。占位类不得留在提交里——它会让 `except Error` 永远不匹配，从而把本应原样上抛的异常降级成 `ServerError`。

- [x] **Step 4: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_xingyao_source.py -q`
Expected: `11 passed`

- [x] **Step 5: 提交**

```bash
git add src/stock_quant/data_sources/xingyao.py tests/unit/test_xingyao_source.py
git commit -m "feat(data_sources): add the xingyao daily adapter"
```

---

## Task 3: 源登记与配置

**Files:**
- Modify: `src/stock_quant/data_pipeline.py:261-262`（注册表）、`:2843-2856`（`_build_source`）
- Modify: `src/stock_quant/data_model/normalize.py:38-44`（`_UNIT_FACTORS`）
- Modify: `src/stock_quant/data_quality/raw_checks.py:61`（`KNOWN_SUPPLIERS`）
- Modify: `project/configs/sources.yml`、`templates/project-config/sources.yml`
- Test: `tests/unit/test_source_registration.py`

**Interfaces:**
- Consumes: `XingyaoSource`（Task 2）
- Produces: `"xingyao"` 出现在 `_CONFIGURED_SOURCES`、`_REQUIRED_ROLE`（`False`）、`_build_source`、`_UNIT_FACTORS`、`KNOWN_SUPPLIERS`、两个 `sources.yml`

- [x] **Step 1: 写失败测试**

创建 `tests/unit/test_source_registration.py`：

```python
"""A configured source is registered everywhere or it crashes a run."""

from __future__ import annotations

from stock_quant.config import SourceConfig
from stock_quant.data_model.normalize import _UNIT_FACTORS
from stock_quant.data_pipeline import (
    _CONFIGURED_SOURCES,
    _REQUIRED_ROLE,
    _build_source,
)
from stock_quant.data_quality.raw_checks import KNOWN_SUPPLIERS


def test_xingyao_is_a_configured_optional_source():
    assert "xingyao" in _CONFIGURED_SOURCES
    assert _REQUIRED_ROLE["xingyao"] is False


def test_every_configured_source_declares_a_required_role():
    """A name without a role raises KeyError while the run is being set up."""
    assert set(_CONFIGURED_SOURCES) == set(_REQUIRED_ROLE)


def test_every_configured_source_is_buildable():
    for name in _CONFIGURED_SOURCES:
        if name == "xingyao":
            continue  # needs the private SDK; covered by its own adapter test
        assert _build_source(name, SourceConfig()).name == name


def test_xingyao_is_a_documented_supplier_with_declared_units():
    assert "xingyao" in KNOWN_SUPPLIERS
    assert _UNIT_FACTORS["xingyao"] == (1, 1)


def test_an_unknown_source_is_refused():
    import pytest

    with pytest.raises(ValueError, match="unknown configured source"):
        _build_source("not_a_source", SourceConfig())
```

- [x] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_source_registration.py -q`
Expected: FAIL — `assert 'xingyao' in ('tushare', 'akshare', 'baostock')`

- [x] **Step 3: 实现**

`src/stock_quant/data_pipeline.py:261-262`：

```python
_CONFIGURED_SOURCES = ("tushare", "akshare", "baostock", "xingyao")
_REQUIRED_ROLE = {
    "tushare": True,
    "akshare": True,
    "baostock": False,
    "xingyao": False,
}
```

`src/stock_quant/data_pipeline.py` 的 `_build_source`，在 baostock 分支之后、`raise` 之前插入：

```python
    if name == "xingyao":
        from stock_quant.data_sources.xingyao import XingyaoSource

        return XingyaoSource(config, client=None)
```

`src/stock_quant/data_model/normalize.py` 的 `_UNIT_FACTORS`：

```python
_UNIT_FACTORS = {
    "tushare": (100, 1000),
    # The GET aggregation proxy serves tushare-layout frames over a different
    # transport; units are identical by contract (lots / thousand-yuan).
    "tushare_proxy": (100, 1000),
    "baostock": (1, 1),
    # Unverified against any external source until the Phase 0 volume probe
    # (design §4.5) closes; (1, 1) is the assumption, not a measurement.
    "xingyao": (1, 1),
}
```

`src/stock_quant/data_quality/raw_checks.py:61`：

```python
KNOWN_SUPPLIERS = frozenset(
    {"tushare", "akshare", "baostock", "tushare_suspend", "xingyao"}
)
```

`templates/project-config/sources.yml`：在 `baostock` 段之后插入

```yaml
xingyao:
  enabled: true
  timeout_seconds: 30
  max_retries: 2
```

`project/configs/sources.yml`：同样在 baostock 段之后插入（注释见 Task 11，本任务先只加段）

```yaml
# 星耀数智 (tgw broker SDK): the optional validation daily source that
# replaces baostock, and the ADR-009 factor channel's second price-event
# channel.  Optional by role (`_REQUIRED_ROLE`): an outage degrades the
# missing-row classification and the factor channel, never a publication.
# Needs the supplier's private wheel (tgw / AmazingData) and credentials in
# AD_USERNAME/AD_PASSWORD/AD_HOST/AD_PORT; absent either, the source reports
# `optional_source_unavailable` and the update proceeds.  Every live call runs
# in a killable child process (`timeout_seconds` bounds the process, not the
# socket).  Volume/amount units are unverified pending the Phase 0 probe.
xingyao:
  enabled: true
  timeout_seconds: 30
  max_retries: 2
```

- [x] **Step 4: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_source_registration.py tests/unit/test_config.py -q`
Expected: `passed`

- [x] **Step 5: 确认新源没有打破配置加载**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_config.py tests/integration/test_project_root_cli.py -q`
Expected: `passed`（若 `sources.yml` 的 `extra="forbid"` 或契约校验对未知键报错，在此暴露）

- [x] **Step 6: 提交**

```bash
git add src/stock_quant/data_pipeline.py src/stock_quant/data_model/normalize.py \
  src/stock_quant/data_quality/raw_checks.py project/configs/sources.yml \
  templates/project-config/sources.yml tests/unit/test_source_registration.py
git commit -m "feat(sources): register xingyao as an optional configured source"
```

---

## Task 4: 校验车道与复用准入替换

**Files:**
- Modify: `src/stock_quant/data_pipeline.py:1090-1102`（车道调用）、`:2535-2580`（校验车道）、`raw_store.py:35-41`
- Test: `tests/unit/test_raw_reuse.py:238-249`、`tests/integration/test_data_pipeline.py`（新增用例）

**Interfaces:**
- Consumes: Task 2、Task 3
- Produces: `DataPipeline._fetch_validation_daily(self, name, enabled, symbols, start, end, issues, statuses, raw_snapshots, validation_rows, *, reuse: bool) -> None`（把原来写死的 `"baostock"` 变成参数）；`REUSABLE_CHANNELS` 不含 baostock

- [x] **Step 1: 先改准入断言（红）**

`tests/unit/test_raw_reuse.py` 的 `_ADMITTED` 表：

```python
_ADMITTED = {
    ("tushare", "daily"): True,
    ("xingyao", "daily"): True,
    ("baostock", "daily"): False,
    ("akshare", "index_history"): True,
    ("tushare", "trade_cal"): False,
    ("tushare", "stock_basic"): False,
    ("tushare", "tdx_xdxr"): False,
    ("tushare", "adjust_factor"): False,
    ("akshare", "cninfo_corporate_actions"): False,
    ("akshare", "eastmoney_corporate_actions"): False,
    ("akshare", "rights_issue_corporate_actions"): False,
}
```

同文件的调用点断言：

```python
def test_the_admitted_set_covers_exactly_the_reuse_call_sites():
    """Every admitted channel is a wired ``reuse=True`` call site (design §5).

    A channel added here without a call site, or a call site wired to a
    channel that is not admitted, means the policy and the wiring drifted.
    baostock keeps its lane but not its reuse: a dormant lane holds no slot.
    """
    wired = {
        ("tushare", "daily"),  # _fetch_primary_stock, _deepen_head_anchors
        ("xingyao", "daily"),  # _fetch_validation_daily (xingyao)
        ("akshare", "index_history"),  # _fetch_benchmarks
    }
    assert raw_store.REUSABLE_CHANNELS == frozenset(wired)
```

- [x] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_raw_reuse.py -q`
Expected: FAIL — 两条断言失败（`REUSABLE_CHANNELS` 仍含 baostock、不含 xingyao）

- [x] **Step 3: 实现准入替换**

`src/stock_quant/data_sources/raw_store.py:35-41`：

```python
#: The channels whose stored answers may be served back (ADR-015 decision 1,
#: amended by ADR-016).  The list is exactly the set of ``reuse=True`` call
#: sites: baostock's daily lane keeps its code but not its reuse, because a
#: lane that no longer runs must not hold an admission slot.
REUSABLE_CHANNELS = frozenset(
    {
        ("tushare", "daily"),
        ("xingyao", "daily"),
        ("akshare", "index_history"),
    }
)
```

- [x] **Step 4: 跑测试确认通过（准入部分）**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_raw_reuse.py -q`
Expected: `passed`

- [x] **Step 5: 写车道的失败集成测试**

在 `tests/integration/test_data_pipeline.py` 末尾追加：

```python
def test_the_xingyao_lane_flips_the_missing_classification(project):
    """A primary gap that xingyao answers classifies as a primary gap.

    The lane's only consumer is the flip: a symbol-day absent from the
    primary source but present in the validation source is
    `primary_source_missing`, not `unknown_or_suspended` (design §2.1).
    """
    from stock_quant.data_quality.gates import PUBLICATION_BLOCKING_CODES

    result = DataPipeline(project.root, sources=_all_stubs()).update(_request())
    codes = result.quality_report.by_code()
    assert "primary_source_missing" in codes
    assert "primary_source_missing" not in PUBLICATION_BLOCKING_CODES
    xingyao = next(
        status for status in result.source_status if status.source == "xingyao"
    )
    assert xingyao.ok and not xingyao.required


def test_the_validation_lane_reports_the_xingyao_source_not_baostock(project):
    """The new lane owns its own status row; the swapped name is the proof.

    baostock is off by default in fixtures (ADR-016), so it carries a not-run
    status rather than an ok one -- the lane moved, it did not multiply.
    """
    result = DataPipeline(project.root, sources=_all_stubs()).update(_request())
    ok_names = {status.source for status in result.source_status if status.ok}
    assert "xingyao" in ok_names
    assert "baostock" not in ok_names
```

> `project` 夹具与 `_all_stubs()` 来自本文件既有定义；`_all_stubs` 里现在还没有 xingyao 桩——Step 6 之前的失败正是这一步要暴露的。

- [x] **Step 6: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_data_pipeline.py -k "xingyao_lane or validation_lane_reports" -q`
Expected: FAIL — `statuses["xingyao"].ok` 为 False（适配器构造失败），或 lane 尚未接线

- [x] **Step 7: 实现车道**

`src/stock_quant/data_pipeline.py:2535` 起，把 `_fetch_validation_daily` 的首参改成源名并保留双源调用：

```python
    def _fetch_validation_daily(
        self,
        name,
        enabled,
        symbols,
        start,
        end,
        issues,
        statuses,
        raw_snapshots,
        validation_rows,
        *,
        reuse: bool,
    ) -> None:
        """One optional validation lane: existence evidence for the flip.

        The lane is per-source so each supplier owns a status row and a raw
        tree; the rows never enter a publication (``_merge_daily`` merges
        primary+benchmark only) and their only consumer is
        ``validation_present``.  ``reuse`` mirrors the admission table: the
        source that currently holds the slot reuses, a dormant one does not.
        """
        source = self._adapter_or_warn(name, issues)
        if source is None:
            statuses[name] = SourceStatus(
                name, False, False,
                reason="optional source unavailable",
                reason_code="optional_source_unavailable",
            )
            return
        failures = 0
        for symbol in symbols:
            dispatched = self._dispatch(
                name, source, "daily", symbol, start, end,
                {"adjustment": "unadjusted"}, required=False, issues=issues,
                reuse=reuse,
            )
            if dispatched is None:
                failures += 1
                continue
            result, snapshot = dispatched
            raw_snapshots.append(snapshot)
            validation_rows.append(
                normalize_daily(
                    result.frame, name, _ingest_time(result.metadata)
                ).valid
            )
        if failures:
            statuses[name] = SourceStatus(
                name, False, False,
                reason=f"{failures} of {len(symbols)} validation requests failed",
                reason_code="partial_fetch_failure",
            )
        else:
            statuses[name] = SourceStatus(name, False, True, reason_code="ok")
```

`:1090-1102` 的调用点改成两条车道：

```python
        # ---- optional validation daily ---------------------------------- #
        validation_rows: list[pd.DataFrame] = []
        for lane_name, lane_reuse in (("baostock", False), ("xingyao", True)):
            if lane_name not in enabled or daily_skipped:
                continue
            self._fetch_validation_daily(
                lane_name,
                enabled,
                equity_symbols,
                daily_start,
                end,
                issues,
                statuses,
                raw_snapshots,
                validation_rows,
                reuse=lane_reuse,
            )
```

- [x] **Step 8: 给 tests/integration/test_data_pipeline.py 的桩 helper 补 xingyao**

```python
def _all_stubs(**overrides) -> dict[str, DataSource]:
    stubs = {
        "tushare": StubAdapter("tushare"),
        "akshare": StubAdapter("akshare"),
        "baostock": StubAdapter("baostock"),
        "xingyao": StubAdapter("xingyao"),
    }
    stubs.update(overrides)
    return stubs
```

- [x] **Step 9: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_raw_reuse.py tests/integration/test_data_pipeline.py -q`
Expected: `passed`

- [x] **Step 10: 提交**

```bash
git add src/stock_quant/data_sources/raw_store.py src/stock_quant/data_pipeline.py \
  tests/unit/test_raw_reuse.py tests/integration/test_data_pipeline.py
git commit -m "feat(data_pipeline): add the xingyao validation lane and swap the reuse admission"
```

---

## Task 5: 夹具跟随生产：baostock 车道停跑，既有断言按实际形态更新

**Files:**
- Modify: `tests/integration/conftest.py:318-332`（`_fixture_sources_yaml`）、`:914-932`（`write_sources`）
- Modify: `tests/integration/test_pipeline_fetch_coverage.py:172-176`、`:324-345`
- Modify: `tests/integration/test_raw_snapshot_reuse.py:225-239`、`:335-339`、`:355`
- Modify: `tests/integration/test_data_pipeline.py:1027-1036`
- Test: 上述四个文件自身

**Interfaces:**
- Consumes: Task 4（`_all_stubs` 已含 xingyao 的示范）
- Produces: 无源码改动。夹具默认不再启用 baostock；`write_sources(root, *, tushare=True, akshare=True, baostock=False, xingyao=True)`——新增 `xingyao` 形参并把 `baostock` 默认值改为 `False`

> **这是 owner 2026-09-26 决定 A 的落地**：测试默认与生产一致（baostock 不跑），但休眠车道**不删**——需要覆盖它的测试自己 `write_sources(baostock=True)` 显式打开。ADR-016 decision 3「dormant, not deleted」与 decision 5 不变。

- [x] **Step 1: 先改夹具开关（红）**

`tests/integration/conftest.py::_fixture_sources_yaml`：

```python
def _fixture_sources_yaml() -> str:
    """The template ``sources.yml`` with the live suppliers enabled.

    Fixture projects do not inherit the template's per-supplier toggles: the
    template may ship a supplier disabled (an operator preference), while
    fixture updates expect every *working* optional supplier to be attempted
    unless a test explicitly disables one via ``write_sources``.

    baostock is the exception and mirrors production (ADR-016): its supplier
    is unavailable and xingyao holds both of its roles, so leaving it enabled
    would make every ordinary fixture test exercise a lane the project no
    longer runs, and would put a dispatched baostock row in the call ledger
    that production never produces.  Tests that need the dormant lane opt in
    explicitly -- ``write_sources(project.root, baostock=True)``.
    """
    payload = yaml.safe_load(
        (_TEMPLATE_CONFIG / "sources.yml").read_text(encoding="utf-8")
    )
    for settings in payload.values():
        if isinstance(settings, dict):
            settings["enabled"] = True
    payload["baostock"]["enabled"] = False
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
```

同文件的 `write_sources`：

```python
def write_sources(project_root: Path, *, tushare: bool = True,
                  akshare: bool = True, baostock: bool = False,
                  xingyao: bool = True) -> None:
    """Rewrite ``configs/sources.yml`` enabling or disabling each supplier.

    Defaults mirror the project's shipped state: the working suppliers are on,
    and baostock is off (ADR-016) unless a test asks for the dormant lane.
    Tests that need a specific enablement call this before constructing any
    pipeline so the config gate (never a CLI flag) decides which sources may
    be built.  xingyao is written explicitly: a sources.yml without its
    segment silently disables the validation lane for the whole test.
    """
    (Path(project_root) / "configs" / "sources.yml").write_text(
        yaml.safe_dump(
            {
                "tushare": {"enabled": tushare},
                "akshare": {"enabled": akshare},
                "baostock": {"enabled": baostock},
                "xingyao": {"enabled": xingyao},
            }
        ),
        encoding="utf-8",
    )
```

- [x] **Step 2: 跑点名文件，记录失败清单**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_pipeline_fetch_coverage.py tests/integration/test_raw_snapshot_reuse.py tests/integration/test_data_pipeline.py -q`
Expected: FAIL —— 失败项应与下表一致；**不一致就停下来查清楚**，不要按预期值改测试

| 位置 | 预期现象 |
| --- | --- |
| `test_pipeline_fetch_coverage.py::test_update_writes_call_ledger` | 精确字典多出 `baostock` 键、缺 `xingyao` 键 |
| `test_pipeline_fetch_coverage.py` 其余用例 | `_all_stubs()` 无 xingyao 桩 → `statuses["xingyao"].ok is False` |
| `test_raw_snapshot_reuse.py::test_a_retry_round_reuses_the_stored_prefix` | `build["raw_snapshot_reuse"]` 多 `baostock` 键、缺 `xingyao` 键 |
| `test_raw_snapshot_reuse.py:355` | `ledger["baostock"]["reused"]` **KeyError**（未 dispatch 的源不进账本） |
| `test_data_pipeline.py::test_optional_validation_failure_still_publishes` | baostock 车道不再运行 → 制造不出 `optional_source_failure` |
| `test_data_pipeline.py` 其余用例 | `_all_stubs()` 缺 xingyao 桩 |
| `test_data_pipeline.py::test_disabled_baostock_is_never_constructed_or_fetched:1063` | **不动**。它自己 `write_sources(baostock=False)`，与新的默认值一致；请求只点名 baostock → 启用集被收窄为空 → `constructed == []` 仍成立 |

- [x] **Step 3: 补桩 helper**

`tests/integration/test_pipeline_fetch_coverage.py:172`：

```python
def _all_stubs() -> dict[str, DataSource]:
    return {name: StubAdapter(name) for name in _STUB_NAMES}


#: Every configurable supplier, so ``sources=`` overrides can never be the
#: reason a lane was built from a real adapter.  baostock is off by default in
#: fixtures (ADR-016) but stays here: tests that opt the dormant lane back in
#: pass the same dict.
_STUB_NAMES = ("tushare", "akshare", "baostock", "xingyao")
```

`tests/integration/test_raw_snapshot_reuse.py:225` 与 `:232`：

```python
_STUB_NAMES = ("tushare", "akshare", "baostock", "xingyao")


def _all_stubs() -> dict[str, DataSource]:
    return {name: StubAdapter(name) for name in _STUB_NAMES}


def _sources(tushare) -> dict[str, DataSource]:
    return {name: StubAdapter(name) for name in _STUB_NAMES} | {"tushare": tushare}
```

- [x] **Step 4: 用实测值更新账本与 raw_snapshot_reuse**

`test_pipeline_fetch_coverage.py::test_update_writes_call_ledger`——先打印 `payload.keys()` 核对，再写：

```python
    assert payload == {
        name: {"calls": 0, "endpoints": {}, "reused": {}}
        for name in ("tushare", "akshare", "xingyao")
    }
```

> baostock **不在**这里：账本只登记真正 dispatch 过的源，夹具默认不再 dispatch 它。若实测仍出现 baostock 键，说明 Step 1 的开关没生效——先查清原因再改断言。

`test_raw_snapshot_reuse.py` 的两处：
- `:355` 的 `ledger["baostock"]["reused"] == {}` 改成对 **xingyao** 断言同样的事实（该源 dispatch 了但没复用任何快照）：`ledger["xingyao"]["reused"] == {}`。
- 精确的 `raw_snapshot_reuse` 字典按实测写：xingyao 行 `{"daily": {"reused": 0, "fetched": len(symbols)}}`，baostock 行删除。

- [x] **Step 5: 把可选源失败测试重指向 xingyao**

`tests/integration/test_data_pipeline.py:1027`——**行为契约保留，制造者换源**：

```python
def test_optional_validation_failure_still_publishes(project):
    """A failed optional source degrades the classification, never the release.

    baostock used to be the failing source here.  It is off by default now
    (ADR-016), so the failing optional source is the one that actually holds
    the validation lane: xingyao.
    """
    failing = StubAdapter("xingyao", raise_with=ServerError)
    pipeline = DataPipeline(project.root, sources=_all_stubs(xingyao=failing))
    result = pipeline.update(_request())
    assert result.dataset_ref is not None
    assert CODE_OPTIONAL_SOURCE_FAILURE in result.quality_report.by_code()
    xingyao_status = next(
        status for status in result.source_status if status.source == "xingyao"
    )
    assert not xingyao_status.ok and not xingyao_status.required
```

- [x] **Step 6: 补一条显式覆盖休眠车道的测试**

夹具默认不跑 baostock 了，休眠路径就只剩显式开关这一条入口——把它钉住：

```python
def test_the_dormant_baostock_lane_still_works_when_a_test_asks_for_it(project):
    """ADR-016 keeps baostock's lane in the tree; this is its only coverage.

    The lane is dormant in production (`enabled: false`) and in the default
    fixture, so nothing else exercises it.  Without this test a future change
    could break the dormant call site -- including its `reuse=False` wiring --
    and nothing would notice.
    """
    write_sources(project.root, baostock=True)
    result = DataPipeline(project.root, sources=_all_stubs()).update(_request())
    baostock_status = next(
        status for status in result.source_status if status.source == "baostock"
    )
    assert baostock_status.ok and not baostock_status.required
    ledger = result.call_ledger
    assert ledger["baostock"]["daily"]["fetched"] > 0, (
        "the dormant lane dispatched but the ledger lost it"
    )
    assert ledger["baostock"]["daily"]["reused"] == 0, (
        "baostock is not admitted to reuse (ADR-016 decision 4)"
    )
```

> 键名与形状以实跑为准：先把 `result.call_ledger` 打印出来核对再落笔。这条断言同时钉住了 `reuse=False`——如果谁把 baostock 的调用点改回 `reuse=True`，第二次运行的 `reused` 就会非零。

- [x] **Step 7: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_pipeline_fetch_coverage.py tests/integration/test_raw_snapshot_reuse.py tests/integration/test_data_pipeline.py -q`
Expected: `passed`

- [x] **Step 8: 跑一遍 unit 层确认没有连带**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit -q`
Expected: `passed`（integration 全跑留到 Task 11）

- [x] **Step 9: 提交**

```bash
git add tests/integration/conftest.py tests/integration/test_pipeline_fetch_coverage.py \
  tests/integration/test_raw_snapshot_reuse.py tests/integration/test_data_pipeline.py
git commit -m "test(integration): mirror production's retired baostock lane in fixtures"
```

---

## Task 6: 离线契约 fixture 与实时契约骨架

**Files:**
- Create: `tests/fixtures/xingyao_daily.csv`
- Modify: `tests/integration/test_source_contracts.py`
- Create: `tests/external/test_xingyao_live.py`

**Interfaces:**
- Consumes: Task 2 的 `XingyaoSource`、`base.validate_supplier_frame`
- Produces: 无源码改动

- [x] **Step 1: 写失败测试**

在 `tests/integration/test_source_contracts.py` 追加（照抄同文件 baostock 用例的形状）：

```python
def test_xingyao_returns_recorded_native_columns():
    """The adapter must hand back the supplier's own columns, unfiltered.

    Renaming or dropping happens in normalization, never in the adapter: a
    contract test that reads a canonicalized frame cannot tell whether the
    supplier changed its layout.
    """
    frame = pd.read_csv(_FIXTURES / "xingyao_daily.csv")
    fake = _FakeXingyao(frame)

    result = XingyaoSource(SourceConfig(), client=fake).fetch(
        DataRequest(
            "daily", ("000001.SZ",), date(2024, 1, 2), date(2024, 1, 5),
            {"adjustment": "unadjusted"},
        )
    )

    assert list(result.frame.columns) == list(frame.columns)
    assert "kline_time" in result.frame.columns
    assert result.metadata["transport_id"] == "xingyao-broker-tcp"


class _FakeXingyao:
    """Serves the recorded frame; announces the SDK version the fixture came from."""

    __version__ = "recorded"

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def login(self, **_):
        return object()

    def logout(self) -> None:
        return None

    def query_kline(self, **_):
        return self._frame
```

- [x] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_source_contracts.py -k xingyao -q`
Expected: FAIL — `FileNotFoundError: tests/fixtures/xingyao_daily.csv`

- [x] **Step 3: 落 fixture**

创建 `tests/fixtures/xingyao_daily.csv`——**必须来自一次真实调用**（Phase 0 探针的一部分），不得手工编造：

```csv
kline_time,code,open,high,low,close,volume,amount
2024-01-02,000001.SZ,,,,,,
```

> 上面是**占位骨架**，实施时用 `tools/` 下的星耀探针工具取一段真实窗口（≥5 个交易日、含一个停牌日更好）覆写。列名保持供应商原样；数值脱敏与否不影响契约，因为契约只断言列与 transport。

- [x] **Step 4: 加实时契约骨架**

创建 `tests/external/test_xingyao_live.py`：

```python
"""Live xingyao contract: the only test that pins the real SDK's names.

Marked ``external``: it needs the private wheel, ``AD_*`` credentials and
network, and it is excluded from the default run.  It exists because the
unit tests drive a fake client and therefore cannot notice that
``query_kline`` was renamed upstream.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import DataRequest
from stock_quant.data_sources.xingyao import XingyaoSource

pytestmark = pytest.mark.external


@pytest.mark.skipif(
    not os.environ.get("AD_USERNAME"), reason="AD_* credentials are not configured"
)
def test_the_sdk_answers_a_one_week_window():
    end = date.today()
    start = end - timedelta(days=7)
    result = XingyaoSource(SourceConfig(timeout_seconds=60)).fetch(
        DataRequest(
            "daily", ("000001.SZ",), start, end, {"adjustment": "unadjusted"}
        )
    )
    assert not result.frame.empty
    assert "kline_time" in result.frame.columns
```

- [x] **Step 5: 跑测试确认通过／跳过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_source_contracts.py -q`
Expected: `passed`
Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/external/test_xingyao_live.py -q -m external`
Expected: 有凭据时 `1 passed`；无凭据时 `1 skipped`

- [x] **Step 6: 提交**

```bash
git add tests/fixtures/xingyao_daily.csv tests/integration/test_source_contracts.py \
  tests/external/test_xingyao_live.py
git commit -m "test(contracts): pin the xingyao native columns offline and live"
```

---

## Task 7: 星耀复权因子模块

**Files:**
- Create: `src/stock_quant/data_sources/xingyao_factor.py`
- Test: `tests/unit/test_xingyao_factor.py`

**Interfaces:**
- Consumes: `run_isolated`（Task 1）、`base.request_key`/`DataRequest`/`FetchResult`
- Produces:
  - `BACKWARD_FACTOR_ENDPOINT = "backward_factor"`
  - `fetch_factor_frame(symbol, *, timeout_seconds, end=None) -> pd.DataFrame`（供应商原始宽表）
  - `fetch_factor_event_dates(symbol, *, timeout_seconds, end=None) -> list[date]`（`factor_event_dates(fetch_factor_frame(...), symbol)` 的薄包装）
  - `factor_event_dates(frame, symbol) -> list[date]`（纯函数，便于单测）
  - `snapshot_result(symbol, frame, *, end) -> FetchResult`
  - `class XingyaoFactorSource`——仅服务 `backward_factor` 的最小 DataSource 适配层，专供漂移审计按原 `DataRequest` 重取同形状快照；`name = "xingyao"`，`fetch(request) -> FetchResult`。它**不进入 `_CONFIGURED_SOURCES`、不参与 `source_status`、不承担 daily 职责**：注册表里的 `"xingyao"` 仍然只由 `XingyaoSource`（Task 2）应答。

- [x] **Step 1: 写失败测试**

创建 `tests/unit/test_xingyao_factor.py`：

```python
"""The wide factor frame narrowed to one symbol's price-event dates."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_quant.data_sources.base import ContractError
from stock_quant.data_sources.xingyao_factor import (
    factor_event_dates,
    snapshot_result,
)


def _wide(values: list[float | None], symbol: str = "000001.SZ") -> pd.DataFrame:
    index = pd.to_datetime(
        [f"2024-01-{day:02d}" for day in range(2, 2 + len(values))]
    )
    return pd.DataFrame({symbol: values}, index=index)


def test_a_change_in_the_factor_is_the_event_and_the_first_row_is_the_baseline():
    frame = _wide([1.0, 1.0, 1.05, 1.05, 1.10])
    assert factor_event_dates(frame, "000001.SZ") == [
        date(2024, 1, 4),
        date(2024, 1, 6),
    ]


def test_leading_and_trailing_nulls_are_dropped_not_treated_as_changes():
    frame = _wide([None, None, 1.0, 1.0, 1.2, None])
    assert factor_event_dates(frame, "000001.SZ") == [date(2024, 1, 6)]


def test_an_interior_null_never_invents_an_event():
    """A missing cell is an absent observation, not a factor change."""
    frame = _wide([1.0, None, 1.0, 1.1])
    assert factor_event_dates(frame, "000001.SZ") == [date(2024, 1, 5)]


def test_rows_are_ordered_by_date_before_comparison():
    frame = _wide([1.0, 1.05, 1.0])
    frame = frame.iloc[::-1]
    assert factor_event_dates(frame, "000001.SZ") == [date(2024, 1, 3)]


def test_a_duplicated_date_with_the_same_value_collapses():
    index = pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-03"])
    frame = pd.DataFrame({"000001.SZ": [1.0, 1.0, 1.1]}, index=index)
    assert factor_event_dates(frame, "000001.SZ") == [date(2024, 1, 3)]


def test_a_duplicated_date_with_conflicting_values_is_a_contract_break():
    index = pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-03"])
    frame = pd.DataFrame({"000001.SZ": [1.0, 2.0, 1.1]}, index=index)
    with pytest.raises(ContractError, match="2024-01-02"):
        factor_event_dates(frame, "000001.SZ")


def test_a_frame_without_the_requested_symbol_column_is_a_contract_break():
    with pytest.raises(ContractError, match="000002.SZ"):
        factor_event_dates(_wide([1.0, 1.1]), "000002.SZ")


def test_a_single_valid_row_has_no_event():
    assert factor_event_dates(_wide([1.0]), "000001.SZ") == []


def test_the_snapshot_keeps_the_supplier_wide_frame_and_a_rebuildable_request():
    frame = _wide([1.0, 1.1])
    result = snapshot_result("000001.SZ", frame, end=date(2024, 1, 31))
    assert result.source == "xingyao"
    assert result.endpoint == "backward_factor"
    assert result.frame.index.equals(frame.index)
    parameters = __import__("json").loads(result.metadata["request_parameters"])
    assert parameters["symbols"] == ["000001.SZ"]
    assert parameters["end_date"] == "2024-01-31"


def test_the_factor_source_re_asks_the_recorded_question(monkeypatch):
    """The audit's re-fetch must reproduce the recorded request exactly."""
    import stock_quant.data_sources.xingyao_factor as module

    frame = _wide([1.0, 1.1])
    asked: dict[str, object] = {}

    def _fake_frame(symbol, *, timeout_seconds, end=None):
        asked.update(symbol=symbol, timeout_seconds=timeout_seconds, end=end)
        return frame

    monkeypatch.setattr(module, "fetch_factor_frame", _fake_frame)
    recorded = snapshot_result("000001.SZ", frame, end=date(2024, 1, 31))

    result = module.XingyaoFactorSource(SourceConfig()).fetch(
        DataRequest(
            "backward_factor", ("000001.SZ",), date(1990, 12, 19), date(2024, 1, 31)
        )
    )

    assert asked["symbol"] == "000001.SZ"
    assert asked["end"] == date(2024, 1, 31)
    assert result.request_key == recorded.request_key
    assert result.metadata["request_parameters"] == recorded.metadata["request_parameters"]


def test_the_factor_source_refuses_any_other_endpoint():
    module = __import__(
        "stock_quant.data_sources.xingyao_factor", fromlist=["XingyaoFactorSource"]
    )
    with pytest.raises(ValueError, match="backward_factor"):
        module.XingyaoFactorSource(SourceConfig()).fetch(
            DataRequest("daily", ("000001.SZ",), date(2024, 1, 2), date(2024, 1, 5), {})
        )


def test_the_factor_source_refuses_more_than_one_symbol():
    module = __import__(
        "stock_quant.data_sources.xingyao_factor", fromlist=["XingyaoFactorSource"]
    )
    with pytest.raises(ValueError, match="one symbol"):
        module.XingyaoFactorSource(SourceConfig()).fetch(
            DataRequest(
                "backward_factor",
                ("000001.SZ", "600000.SH"),
                date(1990, 12, 19),
                date(2024, 1, 31),
                {},
            )
        )
```

- [x] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_xingyao_factor.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: 实现**

创建 `src/stock_quant/data_sources/xingyao_factor.py`：

```python
"""星耀数智 backward-adjustment factor: ADR-009's second price-event channel.

``get_backward_factor([symbol], is_local=False)`` answers a *wide* frame: the
index is the trading calendar and the columns are the requested symbols.  A
row is therefore not an observation of the symbol -- the calendar length is
the row count, and the cells outside the symbol's listed life are empty.

The event rule is baostock's, unchanged (ADR-009 decision 3): the first
*valid* value is the baseline, and an event is a row whose value differs from
the previous valid value.  Anything unreadable -- an empty frame, a missing
symbol column, no valid value at all -- is a contract break or an absent
channel, never "no events": silence must not assert an absence.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from stock_quant.config import SourceConfig
from stock_quant.data_sources._isolated import run_isolated
from stock_quant.data_sources.base import (
    ContractError,
    DataRequest,
    FetchResult,
    request_key,
    request_metadata,
)

#: Endpoint namespace for the raw snapshots: ``raw/xingyao/backward_factor/**``.
BACKWARD_FACTOR_ENDPOINT = "backward_factor"

#: The factor series is read over the whole listed history: a probe date is a
#: quarantined row's announcement date and predates any update window.
SERIES_START = date(1990, 12, 19)

TRANSPORT_ID = "xingyao-broker-tcp"


def factor_event_dates(frame: pd.DataFrame | None, symbol: str) -> list[date]:
    """The symbol's price-event dates; raises when the frame cannot answer.

    An absent channel is the caller's decision, not this function's: it either
    returns dates or raises ``ContractError``.
    """
    if frame is None or frame.empty:
        raise ContractError("xingyao returned no factor frame")
    if symbol not in frame.columns:
        raise ContractError(f"xingyao factor frame has no column for {symbol!r}")
    series = _ordered_series(frame[symbol])
    events: list[date] = []
    previous: float | None = None
    for day, value in _deduplicate(series).items():
        if previous is not None and value != previous:
            events.append(day)
        previous = value
    return events


def fetch_factor_frame(
    symbol: str, *, timeout_seconds: float, end: date | None = None
) -> pd.DataFrame:
    """One worker, one symbol, the supplier's wide frame as it answered it.

    Split from the event extraction on purpose: the frame is what must be
    snapshotted, and extraction can raise ``ContractError`` on a frame that is
    still the honest record of what the supplier said.  Fusing the two would
    discard raw evidence exactly when it is most interesting.
    """
    return run_isolated(
        _fetch_backward_factor,
        timeout_seconds=timeout_seconds,
        symbol=symbol,
        end=(end or date.today()).isoformat(),
    )


def fetch_factor_event_dates(
    symbol: str, *, timeout_seconds: float, end: date | None = None
) -> list[date]:
    """One worker, one symbol, one event list (fail-closed on any error)."""
    return factor_event_dates(
        fetch_factor_frame(symbol, timeout_seconds=timeout_seconds, end=end), symbol
    )


def snapshot_result(symbol: str, frame: pd.DataFrame, *, end: date) -> FetchResult:
    """The snapshot for one symbol's series: the supplier's wide frame, as-is.

    The stored bytes must be what the supplier answered -- the derived event
    list is not a response and must never stand in for one.  The metadata
    carries a rebuildable request so the drift audit can re-ask the same
    question.
    """
    request = DataRequest(BACKWARD_FACTOR_ENDPOINT, (symbol,), SERIES_START, end)
    metadata = request_metadata(
        request,
        "xingyao.get_backward_factor",
        "unknown",
        transport_id=TRANSPORT_ID,
    )
    return FetchResult(
        source="xingyao",
        endpoint=BACKWARD_FACTOR_ENDPOINT,
        request_key=request_key(request),
        frame=frame,
        metadata=metadata,
    )


def _ordered_series(column: pd.Series) -> pd.Series:
    """Dates parsed, values numeric, ascending -- the comparison's preconditions."""
    numeric = pd.to_numeric(column, errors="coerce")
    parsed = pd.to_datetime(pd.Index(column.index), errors="coerce")
    frame = pd.DataFrame({"date": parsed, "value": numeric})
    frame = frame[frame["date"].notna()]
    return frame.set_index("date")["value"].sort_index(kind="stable")


def _deduplicate(series: pd.Series) -> dict[date, float]:
    """One value per calendar day; a conflicting duplicate is a contract break."""
    out: dict[date, float] = {}
    for timestamp, value in series.items():
        day = timestamp.date()
        if pd.isna(value):
            continue
        number = float(value)
        seen = out.get(day)
        if seen is not None and seen != number:
            raise ContractError(f"xingyao factor conflicts on {day.isoformat()}")
        out[day] = number
    return out


def _fetch_backward_factor(*, symbol: str, end: str) -> pd.DataFrame:
    """The worker body: login, one wide-table query, logout."""
    import os

    import tgw

    logged_in = False
    try:
        tgw.login(
            username=os.environ["AD_USERNAME"],
            password=os.environ["AD_PASSWORD"],
            host=os.environ.get("AD_HOST", ""),
            port=os.environ.get("AD_PORT", ""),
        )
        logged_in = True
        response = tgw.get_backward_factor([symbol], is_local=False)
    finally:
        if logged_in:
            try:
                tgw.logout()
            except Exception:  # noqa: BLE001 - a logout failure costs nothing
                pass
    return _to_frame(response)


def _to_frame(response: Any) -> pd.DataFrame:
    if isinstance(response, pd.DataFrame):
        return response
    if hasattr(response, "to_frame"):
        return response.to_frame()
    raise ContractError("xingyao returned an unreadable factor response")


class XingyaoFactorSource:
    """``backward_factor`` as a ``DataSource``, so the drift audit can re-ask it.

    The recorded question is reproduced through ``snapshot_result`` itself --
    the same function that recorded it -- so the audit's request key and
    parameters match the snapshot's by construction rather than by a second
    implementation of the same convention.
    """

    name = "xingyao"

    def __init__(self, config: SourceConfig) -> None:
        self.config = config

    def fetch(self, request: DataRequest) -> FetchResult:
        if request.endpoint != BACKWARD_FACTOR_ENDPOINT:
            raise ValueError("XingyaoFactorSource serves only backward_factor")
        if len(request.symbols) != 1:
            raise ValueError("xingyao factor requests require exactly one symbol")
        symbol = request.symbols[0]
        frame = fetch_factor_frame(
            symbol,
            timeout_seconds=float(self.config.timeout_seconds),
            end=request.end_date,
        )
        return snapshot_result(symbol, frame, end=request.end_date)
```

- [x] **Step 4: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_xingyao_factor.py -q`
Expected: `12 passed`

- [x] **Step 5: 提交**

```bash
git add src/stock_quant/data_sources/xingyao_factor.py tests/unit/test_xingyao_factor.py
git commit -m "feat(data_sources): derive factor events from xingyao's wide frame"
```

---

## Task 8: 因子懒通道切换

**Files:**
- Modify: `src/stock_quant/data_pipeline.py:2402-2418`（`_build_factor_channel`）、`:3670-3729`（`_LazyFactorChannel`）
- Test: `tests/integration/test_data_pipeline.py`

**Interfaces:**
- Consumes: Task 7 的 `fetch_factor_frame`、`snapshot_result`
- Produces: `_LazyFactorChannel.__init__(source_name, fetch_frame, make_snapshot, end, *, issues, raw_snapshots, record_raw)`；`_LazyFactorChannel.__call__(symbol) -> list[date] | None`；`_build_factor_channel` 改从 `sources["xingyao"]` 构建

- [x] **Step 1: 写失败测试**

在 `tests/integration/test_data_pipeline.py` 追加：

```python
def test_a_failing_factor_channel_degrades_to_an_absent_channel(project, monkeypatch):
    """Fail-closed: an unavailable factor channel asserts nothing.

    The channel is WARNING-level and never blocks, but it must not report
    "no events" -- that would let a window conclude an absence from silence
    (ADR-009 decision 2).
    """
    import stock_quant.data_sources.xingyao_factor as factor_module

    def _explode(symbol, **_):
        raise RuntimeError("factor worker died")

    monkeypatch.setattr(factor_module, "fetch_factor_frame", _explode)
    result = DataPipeline(project.root, sources=_all_stubs()).update(_request())
    codes = result.quality_report.by_code()
    assert CODE_OPTIONAL_SOURCE_FAILURE in codes
    details = [
        issue.details
        for issue in result.quality_report.issues
        if issue.code == CODE_OPTIONAL_SOURCE_FAILURE
    ]
    assert any(detail.get("source") == "xingyao" for detail in details)
```

- [x] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_data_pipeline.py -k failing_factor_channel -q`
Expected: FAIL — 记录里的 `source` 仍是 `"baostock"`（`_build_factor_channel` 还没换源）

- [x] **Step 3: 实现切换**

`_LazyFactorChannel` 的 `__init__` 增加两个注入点、把 `config` 换成 `source_name`，`__call__` 把「拉帧」「存证」「算事件」分成三段各自的失败域：

```python
    def __init__(
        self,
        source_name: str,
        fetch_frame: Any,
        extract_events: Any,
        make_snapshot: Any,
        end: date,
        *,
        issues: list[QualityIssue],
        raw_snapshots: list[RawSnapshot],
        record_raw: Any,
    ) -> None:
        self._source_name = source_name
        self._fetch_frame = fetch_frame
        self._extract_events = extract_events
        self._make_snapshot = make_snapshot
        self._end = end
        self._issues = issues
        self._raw_snapshots = raw_snapshots
        self._record_raw = record_raw
        self._events: dict[str, list[date]] = {}
        self._failed: set[str] = set()

    def __call__(self, symbol: str) -> list[date] | None:
        if symbol in self._events:
            return self._events[symbol]
        if symbol in self._failed:
            return None
        try:
            frame = self._fetch_frame(symbol)
        except Exception as error:  # noqa: BLE001 - best-effort evidence channel
            self._report(symbol, error)
            return None
        # Capture the evidence before interpreting it: extraction can refuse a
        # frame (ContractError) and the raw answer is exactly what must not be
        # lost in that case.  A recording failure is never the channel's
        # failure -- the store already logs its own.
        if frame is not None and not frame.empty:
            try:
                self._raw_snapshots.append(
                    self._record_raw(self._make_snapshot(symbol, frame))
                )
            except Exception:  # noqa: BLE001 - evidence capture is best effort
                pass
        try:
            events = self._extract_events(frame, symbol)
        except Exception as error:  # noqa: BLE001 - best-effort evidence channel
            self._report(symbol, error)
            return None
        self._events[symbol] = events
        return events

    def _report(self, symbol: str, error: BaseException) -> None:
        """One warning, one cache entry: an absent channel stays absent."""
        self._issues.append(
            _issue(
                Severity.WARNING,
                CODE_OPTIONAL_SOURCE_FAILURE,
                details={
                    "source": self._source_name,
                    "endpoint": "backward_factor",
                    "symbol": symbol,
                    "message": str(error),
                },
            )
        )
        self._failed.add(symbol)
```

> 拆成三段不是为了好看：原来的单 `try` 会把「拉不到帧」「帧存不下」「帧解释不了」压成同一条记录。现在契约破裂时快照仍然落地，缺的是事件而不是证据。

`_build_factor_channel`：

```python
    def _build_factor_channel(self, end: date, issues, raw_snapshots):
        """Build the ADR-009 factor channel, or ``None`` when it is off.

        The channel serves only the absent-ex-date classification and stays
        fail-closed: a disabled source asserts nothing rather than letting a
        window conclude an absence from silence.  The implementation is
        xingyao's (ADR-016); baostock's module stays in the tree as the
        dormant predecessor.
        """
        from stock_quant.data_sources.xingyao_factor import (
            factor_event_dates,
            fetch_factor_frame,
            snapshot_result,
        )

        config = self._project_config.sources.get("xingyao")
        if config is None or not config.enabled:
            return None
        return _LazyFactorChannel(
            "xingyao",
            lambda symbol: fetch_factor_frame(
                symbol,
                timeout_seconds=float(config.timeout_seconds),
                end=end,
            ),
            factor_event_dates,
            snapshot_result,
            end,
            issues=issues,
            raw_snapshots=raw_snapshots,
            record_raw=self._record_raw,
        )
```

> `_build_factor_channel` 里 `from ... import` 写在函数内，所以 `monkeypatch.setattr(factor_module, "fetch_factor_frame", ...)` 能生效（Step 1 的用例依赖这一点）。

- [x] **Step 4: 对齐旧调用点**

`_build_factor_channel` 的调用方（`data_pipeline.py:2412` 附近）只传 `(end, issues, raw_snapshots)`，签名未变，无需改动。确认没有别处直接构造 `_LazyFactorChannel`：

```bash
grep -rn "_LazyFactorChannel\|_build_factor_channel" src/ tests/
```
Expected: 各只有构造点与调用点各一处；若有测试直接构造它，按新签名更新并把 `config=` 换成 `source_name=`。

- [x] **Step 5: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_xingyao_factor.py tests/integration/test_data_pipeline.py -q`
Expected: `passed`

- [x] **Step 6: 提交**

```bash
git add src/stock_quant/data_sources/xingyao_factor.py src/stock_quant/data_pipeline.py \
  tests/unit/test_xingyao_factor.py tests/integration/test_data_pipeline.py
git commit -m "feat(data_pipeline): serve the ADR-009 factor channel from xingyao"
```

---

## Task 9: 漂移审计按 (source, endpoint) 分派并让未完成审计退出非零

**Files:**
- Modify: `project/drift_audit.py:81-161`
- Test: `tests/unit/test_drift_audit.py`

**Interfaces:**
- Consumes: Task 2、Task 7 的适配器与因子模块；`RawSnapshotEvidence`、`RawStore`
- Produces:
  - `_source_for(source, endpoint, config) -> DataSource`（按 `(source, endpoint)` 分派）
  - `_xingyao_daily_source(config)` / `_xingyao_factor_source(config)`（模块级构造器，测试可注入）
  - `run(...) -> int` 返回 `drifted + audit_failures`，两者分列在报告里
  - `render_audit_record(version, rows, *, drifted, audit_failures)` 追加两行汇总

- [x] **Step 1: 写失败测试**

创建 `tests/unit/test_drift_audit.py`：

```python
"""An audit that could not compare two thirds of its targets must not exit 0."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from stock_quant.config import SourceConfig
from stock_quant.data_sources.raw_store import RawSnapshotEvidence

#: ``project/`` is an operator directory, not an installed package.
_SPEC = importlib.util.spec_from_file_location(
    "drift_audit", Path(__file__).resolve().parents[2] / "project" / "drift_audit.py"
)
drift_audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(drift_audit)


def _config() -> SimpleNamespace:
    """Only ``config.sources`` is read, and only to pick a segment."""
    return SimpleNamespace(
        sources={
            name: SourceConfig() for name in ("tushare", "akshare", "baostock", "xingyao")
        }
    )


def _evidence(source: str, endpoint: str) -> RawSnapshotEvidence:
    return RawSnapshotEvidence(
        source=source,
        endpoint=endpoint,
        request_key="a" * 32,
        file_sha256="b" * 64,
        manifest_sha256="c" * 64,
        transport_id="xingyao-broker-tcp",
    )


def test_the_record_keeps_the_endpoint_the_audit_was_asked_to_compare():
    assert _evidence("xingyao", "backward_factor").endpoint == "backward_factor"


def test_a_known_xingyao_endpoint_dispatches_to_its_own_builder(monkeypatch):
    """The two xingyao endpoints are different adapters, not one prefixed one."""
    seen: list[str] = []

    def _daily(config):
        seen.append("daily")
        return SimpleNamespace(name="xingyao")

    def _factor(config):
        seen.append("backward_factor")
        return SimpleNamespace(name="xingyao")

    monkeypatch.setattr(drift_audit, "_xingyao_daily_source", _daily)
    monkeypatch.setattr(drift_audit, "_xingyao_factor_source", _factor)
    config = _config()

    assert drift_audit._source_for("xingyao", "daily", config).name == "xingyao"
    assert (
        drift_audit._source_for("xingyao", "backward_factor", config).name
        == "xingyao"
    )
    assert seen == ["daily", "backward_factor"]


def test_an_unknown_endpoint_is_refused_rather_than_silently_skipped():
    """A mapping that fell through to the daily adapter would compare nothing."""
    with pytest.raises(ValueError, match="backward_factor_v2"):
        drift_audit._source_for("xingyao", "backward_factor_v2", _config())


def test_an_unknown_source_is_still_refused():
    with pytest.raises(ValueError, match="not_a_source"):
        drift_audit._source_for("not_a_source", "daily", _config())


def test_the_unverifiable_and_the_unfetchable_both_count_as_audit_failures():
    rows = [
        {"endpoint": "daily", "fetched_sha256": "unverifiable"},
        {"endpoint": "daily", "fetched_sha256": "fetch_failed"},
        {"endpoint": "daily", "fetched_sha256": "c" * 64, "note": "stable"},
    ]
    assert drift_audit.count_audit_failures(rows) == 2


def test_the_record_separates_drift_from_incomplete_audits():
    body = drift_audit.render_audit_record("v1", [], drifted=0, audit_failures=3)
    assert "0" in body and "3" in body
    assert "未完成" in body
```

- [x] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_drift_audit.py -q`
Expected: FAIL — `_source_for` 只收一个参数；`count_audit_failures`、`_xingyao_daily_source` 不存在

- [x] **Step 3: 实现**

`project/drift_audit.py` 的 `_source_for` 换成按 `(source, endpoint)` 分派，两个 xingyao 构造器提到模块级（测试可替换，且私有包的 import 推迟到真正要用时）：

```python
def _xingyao_daily_source(config):
    from stock_quant.data_sources.xingyao import XingyaoSource

    return XingyaoSource(config)


def _xingyao_factor_source(config):
    from stock_quant.data_sources.xingyao_factor import XingyaoFactorSource

    return XingyaoFactorSource(config)


def _source_for(name: str, endpoint: str, config: ProjectConfig):
    """The adapter that can re-ask this exact question.

    Dispatch is on the pair for xingyao, and only for xingyao: its factor
    channel answers through a wide-table API its daily adapter knows nothing
    about, so a source-prefix mapping alone would send the audit back with a
    request the supplier cannot honour -- or worse, report a comparison it
    never made.  The other three adapters serve all of their own endpoints, so
    their prefix dispatch stays as it is.  An unknown endpoint raises, and
    ``run`` counts that as an audit failure rather than a pass.
    """
    if name.startswith("tushare"):
        return TushareSource(config.sources["tushare"])
    if name.startswith("akshare"):
        return AkShareSource(config.sources["akshare"])
    if name.startswith("baostock"):
        return BaoStockSource(config.sources["baostock"])
    if name.startswith("xingyao"):
        if endpoint == "daily":
            return _xingyao_daily_source(config.sources["xingyao"])
        if endpoint == "backward_factor":
            return _xingyao_factor_source(config.sources["xingyao"])
    raise ValueError(
        f"no adapter for raw-snapshot source {name!r} endpoint {endpoint!r}"
    )
```

> `XingyaoFactorSource` 由 Task 7 定义（`xingyao_factor.py`），本任务只调用它：它不进入 `_CONFIGURED_SOURCES`，因此 `source_status`、账本、`_all_stubs()` 都不涉及它——它是漂移审计专用的重取入口，不是一个被登记的源。

新增两个模块级函数：

```python
def count_audit_failures(rows: Sequence[Mapping[str, object]]) -> int:
    """Rows the audit could not compare: unverifiable or unfetchable."""
    return sum(
        1
        for row in rows
        if str(row.get("fetched_sha256", "")) in ("unverifiable", "fetch_failed")
    )
```

`render_audit_record` 的签名与末尾汇总：

```python
def render_audit_record(
    version: str,
    rows: Sequence[Mapping[str, object]],
    *,
    drifted: int,
    audit_failures: int,
) -> str:
```

在逐行列表之后、`return` 之前插入：

```python
    lines.extend(
        [
            "",
            f"汇总：比对完成 {len(rows) - audit_failures} 条，漂移 {drifted} 条，"
            f"未完成 {audit_failures} 条。",
            "未完成不是通过：它表示这个通道本轮没有被审到，需在下次审计前修好。",
        ]
    )
```

`run` 里把 `_source_for(evidence.source, config)` 改为 `_source_for(evidence.source, evidence.endpoint, config)`——**只改分派那一行**。`DataRequest` 的 endpoint 仍从 `snapshot.manifest.get("endpoint", evidence.endpoint)` 取：分派读的是「这条证据自称来自哪个端点」，重取读的是「当初那条请求怎么写的」，两者一致时无差别，不一致时该被审计发现而不是被这次改动抹平。末尾改成：

```python
    audit_failures = count_audit_failures(rows)
    if output is None:
        output = Path(root) / "docs" / "operations" / f"{date.today().isoformat()}-drift-audit.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        render_audit_record(pinned, rows, drifted=drifted, audit_failures=audit_failures),
        encoding="utf-8",
    )
    print(
        f"drift audit: {len(rows)} snapshots, {drifted} drifted, "
        f"{audit_failures} unfinished -> {output}"
    )
    return drifted + audit_failures
```

- [x] **Step 4: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_drift_audit.py -q`
Expected: `6 passed`

- [x] **Step 5: 确认 `main` 的退出码跟着走**

```bash
/home/ji/miniconda3/envs/sq312/bin/python project/drift_audit.py --root . --output /tmp/x.md; echo "exit=$?"
```
Expected: 打印汇总并让 `exit` 反映漂移+未完成之和（真实项目根下如需网络会失败，这本身就是「未完成」的正确表现）

- [x] **Step 6: 提交**

```bash
git add project/drift_audit.py tests/unit/test_drift_audit.py
git commit -m "fix(drift_audit): dispatch by endpoint and fail the audit that did not run"
```

---

## Task 10: Phase 0 实测（需凭据与 owner 授权，不写代码）

**Files:**
- Modify: `tests/fixtures/xingyao_daily.csv`（用真实录制覆写 Task 6 的骨架）
- Create: `docs/operations/2026-09-26-xingyao-phase0-probes.md`

**Interfaces:**
- Consumes: Task 2/3/6 已就绪的适配器与配置
- Produces: 六项实测结论；结论回写 spec §2.4/§3.2/§3.3 与 ADR-016（停牌日形态、单位因子）

> **门禁**：本任务涉及真实网络与凭据调用，按 RUNBOOK 需要 owner 明确授权后在受控窗口执行。不通过 Task 10，不得把 `xingyao.enabled` 当成默认启用状态写入任何新的发布或验收记录。

- [x] **Step 1: 停牌日行形态（§4.1）**

用 `get_history_stock_status` 找一只近期停牌股，取其停牌区间日K：**停牌日是零量行还是缺行**？
把窗口、股票、结论写进 `docs/operations/2026-09-26-xingyao-phase0-probes.md`。
判定分流：零量行 → 翻转能力与 baostock 等价；缺行 → 停牌日分类保持 `unknown_or_suspended`，差异写入 ADR-016。

- [x] **Step 2: 零量行能否通过 `normalize_daily`**

用 Step 1 的零量行喂 `normalize_daily(frame, "xingyao", timestamp)`，记录是否被行级拒绝规则拒绝。被拒则停在这里：**先回 spec 决定是否透传，再继续**——不要为了让管道通过而放宽 `normalize_daily`。

- [x] **Step 3: 全市场窗口比对（§4.2）**

`tgw.QueryCodeTable()` 枚举 A 股，取近 60 交易日 + 一个历史抽样窗口，按 `compare_daily_sources` 的阈值（收盘差 >0.20% = ERROR）与已发布 `daily_bar` 比对，产出 sha256 证据文件。

- [x] **Step 4: 退市股与历史深度（§4.3）**

对 `000003.SZ` 一类退市股确认历史覆盖；对照 `full_history_acceptance_start` 抽 2005/2015 窗口确认深度。这是 §3.3 扶正条件的证据基线。

- [x] **Step 5: 配额口径（§4.4）**

用一次记录**起止计数**的实测把"周配额是 1GB 还是别的"钉死（评估报告的 23% 与 0.04% 相差约 500 倍）。写清基数、本次消耗、折算到"每轮增量窗口"的占比。

- [x] **Step 6: 成交量/成交额单位（§4.5）**

取 ≥3 只标的的区间累计成交量与独立来源（已发布 `daily_bar` 或 tushare）比对，确认股还是手。结论回写 `_UNIT_FACTORS["xingyao"]` 与 spec §2.4；**未闭合前不得把星耀扶正为 daily_bar 主源**。

- [x] **Step 7: 用真实窗口覆写离线 fixture**

把 Step 1/3 中取的 ≥5 个交易日窗口写入 `tests/fixtures/xingyao_daily.csv`（列名保持供应商原样），然后：

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_source_contracts.py -q`
Expected: `passed`

- [x] **Step 8: 提交**

```bash
git add docs/operations/2026-09-26-xingyao-phase0-probes.md tests/fixtures/xingyao_daily.csv
git commit -m "docs(operations): record the xingyao phase-0 probes"
```

---

## Task 11: 原子收尾（配置切换、注释修正、ADR 转正）

**Files:**
- Modify: `project/configs/sources.yml`（baostock 注释 + `enabled: false`）
- Modify: `docs/adr/016-xingyao-baostock-succession.md`、`docs/adr/015-raw-snapshot-reuse-for-eligible-channels.md`、`docs/adr/DECISIONS_INDEX.md`
- Modify: `RUNBOOK.md`、`requirements.txt`、`environment.yml`
- Test: 全量点名文件 + 治理检查

**Interfaces:**
- Consumes: Task 1-9 全部通过，Task 10 的 Phase 0 结论
- Produces: 一个可发布的原子状态

- [x] **Step 1: 切换 baostock 并修正两处失实注释**

`project/configs/sources.yml` 的 baostock 注释段与开关：

```yaml
# baostock is the FORMer optional cross-check daily source (unadjusted
# prices).  Its data server (:10030) was unreachable from 2026-09-05,
# briefly answered on 2026-09-19, and is unavailable again as of 2026-09-26
# (owner report); it is disabled by default from that date.  xingyao holds
# both of the roles it really had -- the validation daily lane and the
# ADR-009 factor channel (ADR-016) -- so disabling it removes no capability
# that is not carried elsewhere.  Two claims that used to sit here were
# never true and are recorded as such: an outage never degraded
# `compare_daily_sources` (its only production consumer reads a published
# `daily_bar` sample), and the supplier's own suspension rows
# (`tradestatus=0`) were never read as suspension evidence -- `tradestatus`
# appears once in `src/`, as a field declaration, and the project's
# suspension facts come from tushare's `pre_close` chain.  Re-enabling the
# source is a config change; re-admitting it to raw-snapshot reuse is not
# (ADR-015 decision 1, as amended by ADR-016).
baostock:
  enabled: false
  timeout_seconds: 30
  max_retries: 3
```

同时把 `templates/project-config/sources.yml` 的 baostock 段也改成 `enabled: false`：模板是新项目脚手架的默认值，让它带着一个已停用的 supplier 出厂，等于让每个新项目重演这次的清理。改完后 `_fixture_sources_yaml` 的显式 `payload["baostock"]["enabled"] = False` 与模板一致（两处都要有：夹具不依赖模板的开关语义）。

- [x] **Step 2: 跑一遍受影响的面**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_raw_reuse.py tests/unit/test_xingyao_source.py tests/unit/test_xingyao_factor.py tests/unit/test_drift_audit.py tests/unit/test_isolated_call.py tests/unit/test_source_registration.py -q`
Expected: `passed`

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_data_pipeline.py tests/integration/test_pipeline_fetch_coverage.py tests/integration/test_raw_snapshot_reuse.py tests/integration/test_source_contracts.py -q`
Expected: `passed`

> Task 5 已让夹具默认关闭 baostock，所以本步**不会**再改变集成行为；它验证的是配置文件仍能被解析、注释不破坏 YAML、模板改动没有波及夹具（`_fixture_sources_yaml` 仍然显式设定该开关，不读模板的值）。

- [x] **Step 3: 文档同步（spec §7.1、ADR-016、ADR-015、index）**

- `docs/superpowers/specs/2026-09-26-…-design.md` §7.1：标题与正文按决定 A 重写——夹具默认不再启用 baostock，`_fixture_sources_yaml` 显式置 `False`，休眠车道的覆盖改为 `write_sources(baostock=True)` 显式 opt-in（Task 5 Step 6 已落地）。删除「不得为了模拟生产默认值而削弱夹具对休眠代码路径的覆盖」一句，代之以「休眠路径的覆盖是显式的，不是默认的」。
- `docs/adr/016-…md`：frontmatter `status: proposed` → `status: accepted`；删除正文顶部那段 "Not yet effective" 引用块；**补一条 decision**——「夹具与生产一致：默认不运行 baostock 车道；休眠车道的覆盖由显式 opt-in 测试承担」，并在 Consequences 里记下这笔代价（默认路径不再覆盖 dormant 代码，改由一条专门测试盯住）。
- `docs/adr/015-…md`：顶部 "Pending amendment" 块改为生效版——第一句改成 `**Amendment (ADR-016, 2026-09-26).** Decision 1's channel list substitutes `("xingyao", "daily")` for `("baostock", "daily")`;`，删掉 "The accepted decision below and the current runtime still admit `("baostock","daily")`" 与 "Until then it is a forward pointer only and does not rewrite this ADR's current channel list." 两句，保留成本论据改读为 tushare daily 车道的说明。
- `docs/adr/DECISIONS_INDEX.md`：016 行的 `proposed` → `accepted`；015 行的 "ADR-016 pending substitution / ADR-016 proposes a channel substitution but is not yet effective" 改为 "ADR-016 channel substitution"；016 行 "Read when" 里的 "re-admit a dormant channel" 保留。

> 这一步会改动你本人编辑过的四份文档。若你想自己落这几处措辞，把 §7.1 的重写与 016 的新 decision 交给你，我只保留 Task 11 的其余部分——开工前说一声。

- [x] **Step 4: 治理检查**

Run: `/home/ji/miniconda3/envs/sq312/bin/python tools/check_context_governance.py --root .; echo "exit=$?"`
Expected: `exit=0`（无输出）
Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_context_governance_docs.py -q`
Expected: `passed`

- [x] **Step 5: RUNBOOK 与依赖注释**

- `RUNBOOK.md`：新增星耀条目——私有包安装（**只写包名 `tgw`/`AmazingData` 与前置 `tables`，不写路径**，wheel 由操作者自备）、`AD_*` 环境变量、`-m external` 的实时契约用法、baostock 禁用/恢复程序（恢复车道 = 改 `enabled`，恢复复用 = 重新准入）、以及引用 ADR-015 §2.7 的三步删除程序（不重复其内容）。
- `requirements.txt` / `environment.yml`：注释段说明 `tgw`/`AmazingData` 是供应商私有包、PyPI 无包、需 `tables`(PyTables)、不进默认依赖。

- [x] **Step 6: 全量点名收尾**

Run:
```bash
/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit -q
/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_data_pipeline.py tests/integration/test_pipeline_fetch_coverage.py tests/integration/test_raw_snapshot_reuse.py tests/integration/test_source_contracts.py tests/integration/test_project_root_cli.py tests/integration/test_cli.py tests/integration/test_acceptance_cli.py -q
```
Expected: 全部 `passed`。这是唯一一处计划要求跑到 integration 范围的地方（任务本身改变了跨边界行为），但仍是点名文件而非裸 `pytest`。

- [x] **Step 7: 提交**

```bash
git add project/configs/sources.yml templates/project-config/sources.yml docs/adr \
  docs/superpowers/specs RUNBOOK.md requirements.txt environment.yml
git commit -m "feat(sources): retire baostock and accept ADR-016"
```

---

## Self-Review

**Spec 覆盖**

| spec 章节 | 落在哪个任务 |
| --- | --- |
| §3.1 适配器（惰性 import、endpoint、错误映射、transport_id） | Task 2 |
| §3.1 硬超时边界 | Task 1 + Task 2 Step 3 |
| §3.1 新校验车道 + baostock 车道保留 `reuse=False` | Task 4 |
| §3.1 注册五处 + 两份 sources.yml | Task 3 |
| §3.1 `REUSABLE_CHANNELS` 替换 | Task 4 |
| §3.1 drift_audit 扩展 | Task 9 |
| §3.1 依赖注释 | Task 11 Step 5 |
| §3.2 因子模块（宽表、空值、重复、快照） | Task 7 |
| §3.2 懒通道切换 | Task 8 |
| §3.2 发布顺序（原子） | Global Constraints + Task 11 |
| §3.3 备选主源条件 | Task 10 Step 3/4/6（证据），代码不接线 |
| §4 Phase 0 六项 | Task 10 |
| §5 准入替换 + ADR-015 指针 + 防回归断言 | Task 4 + Task 11 Step 3 |
| §6 边界（不删除、不碰） | Global Constraints |
| §7 新增测试 | Task 1/2/6/7/9 |
| §7.1 夹具默认不启用 baostock（决定 A）；精确形状与 helper 最小改动；休眠车道显式 opt-in 覆盖 | Task 4 Step 1/5/8 + Task 5 |
| §7.2 新增集成测试 | Task 4 Step 5、Task 8 Step 1 |
| §8 验证命令 | 各任务 Step + Task 11 Step 6 |
| §9 文件清单 | 文件结构表 |

> §7.1 的措辞需在 Task 11 Step 3 按决定 A 重写——spec 现文写的是「夹具继续启用 baostock」。本计划的 Task 5 已按 A 落地，文档同步是 Task 11 的一部分。

**已知的计划内依赖（实施时按序补齐，不是占位符）**

- Task 2 Step 3 的 `class Error(RuntimeError)` 占位必须在 Step 1 的真实 SDK 核对后替换或删除，提交前不得保留。
- Task 7 暴露 spec §3.2 明定的 `fetch_factor_frame(...)` 与最小
  `XingyaoFactorSource`（Task 8、Task 9 消费），两者的单测写在 Task 7。
- Task 5 Step 4/6 的精确字典与账本键必须用实跑值填写，不得照抄本计划里的形状猜测。
- Task 10 未完成前，星耀保持"候选"定位：不改 `data_contracts.py`、不写验收记录。

**类型一致性**：`_fetch_validation_daily(name, enabled, symbols, start, end, issues, statuses, raw_snapshots, validation_rows, *, reuse)` 的签名在 Task 4 的调用点与实现一致；`factor_event_dates(frame, symbol)`、`snapshot_result(symbol, frame, *, end)`、`fetch_factor_frame(symbol, *, timeout_seconds, end=None)`、`run_isolated(target, *, timeout_seconds, **kwargs)`、`_source_for(name, endpoint, config)`、`count_audit_failures(rows)`、`render_audit_record(version, rows, *, drifted, audit_failures)` 在定义与调用处同名同参。
