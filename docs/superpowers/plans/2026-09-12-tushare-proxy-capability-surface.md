# Tushare 代理能力面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `TushareProxyClient` 从"3 个方法的转发壳"变成受控通用读取面（298 个目录接口任意读、GET-only、能力预检、响应头限速、可审计 provenance），并收口四处缺陷、交付只读探针与评估报告。

**Architecture:** 传输层重构出一个共用的 `_request(url, parse, ...)`（重试 + 限速 + 记录），数据路径与元数据路径都走它。能力发现复用该传输层：`capabilities()` 读 `/tushare/capabilities` 的 `interfaces` 数组，`capability(name)` 读单接口。`query()` 在出站前做一次形状预检（失败即 fail-open 并留痕）；具名三接口按名字跳过预检，故既有测试一行不改。

**Tech Stack:** Python 3.10、pandas 2、requests、pytest、ruff。

## Global Constraints

以下为 spec 的项目级约束，每个任务都隐含适用。逐字取自
`docs/superpowers/specs/2026-09-12-tushare-proxy-capability-surface-design.md`。

- **既有 `tests/unit/test_tushare_proxy.py` 不修改**（16 个用例，基线 `16 passed in 0.49s`）。本计划的每一次改动后都必须仍然全绿。
- **不把代理升格为一等数据源**：不进入 `_CONFIGURED_SOURCES` / `_REQUIRED_ROLE`，`source health` 与 `statuses` 不单独上报代理。
- **不新增 `raw_checks` 供应商白名单标签**；不用它充当 `corporate_action_evidence`；不用 `index_weight` 顶替 csi300 官方公告；不用它满足 `cross_source_price_sample` 的"独立第二价格源"。
- **不把任何新接口写进数据管线**：`TushareSource` 的 endpoint 路由、数据契约、`_ACCEPTED_MISSING_CODES`、验收口径、策略配置一律不动。
- **不实现并发**（服务端 `max_concurrency: 4` 是上限，不是目标）。
- **纯 GET**：通用面可达的任何路径都不得发出 POST/DELETE。
- **不改写 git 历史**。`.env` 已被 `.gitignore` 忽略；**写入任何密钥前先经 owner 确认**，且任何脚本/日志都不得打印密钥值。
- **测试只跑具名文件**（`pytest tests/unit/<file>::<case>`），不跑全量套件。
- **ruff**：只对本次改动行做 `ruff check` 与 `ruff format --check`，仓库有 38 个既有文件从未 format-clean，不得以仓库级 format 为准。

---

## 开工前需要确认的三处

这三点是 spec 落实到代码时暴露的缝隙。计划按"推荐"一栏写着，实施时可被推翻。

| # | 缝隙 | 计划采用 | 若你选另一条 |
| --- | --- | --- | --- |
| 1 | spec §3 要求"响应头缺失时用保守常量 1s"，但 spec 测试节又要求"既有 `test_tushare_proxy.py` 不修改"。既有两条用例断言 `sleeps == [2]` / `[2, 6, 12, 20]`，而它们的 `FakeResponse` **没有 `headers` 属性**——照字面实现会让这两条必挂。 | **未见过任何限速头之前不节流；见过一次之后，后续缺头的响应回落到 1s。** 生产环境里服务器确实发这些头，故"不因缺头而放开"仍成立；既有测试零改动。 | 改那两条断言（放宽"不修改"） |
| 2 | spec §1 只列了 `capabilities()` / `capability(name)`，但 §6 要求探针打印 `/tushare/upstreams/probe/{name}` 的上游链。 | 给客户端加第三个公开方法 `upstreams(name)`，复用同一套重试/限速传输。 | 探针自己 `requests.get`（会绕过重试与限速） |
| 3 | spec §6 说 GET-only 断言"零出站"。但判定 `p_save` 是否允许 GET 必须先读到它的 capability——那本身就是一次出站。 | 断言改为"`ContractError` 且**没有任何指向数据路径 `/pro/p_save` 的请求**"（capability 那一次允许）。 | 不可行：不读 capability 就无法判断 methods |

另有一条本轮新测到、**应当写进评估报告**的证据（Task 7 使用）：
`/tushare/upstreams/probe/suspend_d` 显示**六个上游全部报不支持该接口**
（`tickflow` / `eastmoney` / `sina-minute` / `sina` 报 `unsupported_api`，`citydata` 报
"参数不能为空"，`relay` 报 502 `fallback_error`），而 `/tushare/pro/suspend_d` 确实
返回数据。**故这 6 个上游列表根本不包含真正作答的源**——这比"上游不可溯源"更强：
它证明探测列表与作答集合是两回事。

---

## Task 1: 传输层重构 + 能力发现

**Files:**
- Modify: `src/stock_quant/data_sources/tushare_proxy.py:1-23`（docstring 留到 Task 5）、`83-99`（`__init__`）、`101-126`（`from_env`）、`177-179`（`query`）、`185-218`（`_paged`）、`220-288`（`_query`/`_parse`/`_api_error`）
- Test: `tests/unit/test_tushare_proxy_capabilities.py`（新建）

**Interfaces:**
- Consumes: 无（本任务是起点）
- Produces:
  - `TushareProxyClient.__init__(..., clock: Callable[[], float] = time.monotonic)`
  - `TushareProxyClient._root: str` —— `base_url` 去掉最后一段（`https://pcd.mobcvb.cn/tushare/pro` → `https://pcd.mobcvb.cn/tushare`）
  - `TushareProxyClient._request(url: str, parse: Callable[[str, object], object], *, read_timeout: int | None = None, **params) -> object`
  - `TushareProxyClient._query(endpoint: str, *, read_timeout: int | None = None, **params) -> pd.DataFrame`
  - `TushareProxyClient._paged(endpoint: str, *, start_date=None, end_date=None, **params) -> tuple[pd.DataFrame, list[tuple[str | None, str | None]]]` —— **返回二元组**
  - `TushareProxyClient.capabilities(*, refresh: bool = False) -> pd.DataFrame`
  - `TushareProxyClient.capability(name: str) -> Mapping[str, object]`
  - `TushareProxyClient.upstreams(name: str) -> Mapping[str, object]`
  - 模块级 `_api_error(label: str, body: object) -> Exception`（由方法改为函数）
  - 模块级 `_parse_catalog` / `_parse_interface` / `_parse_upstreams` / `_cache_ttl`

### 实测契约（本任务据此编写，勿凭猜）

| 端点 | 形状 |
| --- | --- |
| `GET {root}/capabilities` | `{"count": 298, "probe": {...}, "interfaces": [<interface> × 298]}` |
| `GET {root}/capabilities/{name}` | `<interface>`，**裸对象**，不包 `code`/`data` |
| `GET {root}/upstreams/probe/{name}` | `{"api_name": str, "results": [{"name", "ok", "message", "rows", ...}]}` |
| `<interface>` 字段 | `name` `category` `cache_ttl` `provider` `required` `required_any` `max_limit` `description` `enabled` `methods` `fallback_on_empty` `probe_supported` `probe_example` `local_data_available` `local_latest_time` `requires_params` |

实测样例（`/tushare/capabilities/daily`）：

```json
{"name": "daily", "category": "股票", "cache_ttl": 21600, "provider": "tushare",
 "required": [], "required_any": [["ts_code"], ["trade_date"], ["start_date"], ["end_date"]],
 "max_limit": null, "description": "A 股日线行情", "enabled": true, "methods": ["GET"],
 "fallback_on_empty": true, "probe_supported": true,
 "probe_example": "/tushare/pro/daily?__probe=1", "local_data_available": null,
 "local_latest_time": null, "requires_params": true}
```

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_tushare_proxy_capabilities.py`：

```python
"""Unit tests for the controlled generic read surface of the proxy client."""

from __future__ import annotations

import pytest

from stock_quant.data_sources.base import ContractError
from stock_quant.data_sources.tushare_proxy import TushareProxyClient

BASE_URL = "https://proxy.example/tushare/pro"
ROOT = "https://proxy.example/tushare"


class FakeResponse:
    def __init__(self, body=None, status_code=200, text="", headers=None):
        self.status_code = status_code
        self._body = body
        self.text = text
        self.headers = dict(headers or {})

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """Records GETs and answers from a queue of responses/exceptions."""

    def __init__(self, outcomes):
        self.headers: dict[str, str] = {}
        self.calls: list[dict] = []
        self._outcomes = list(outcomes)

    def get(self, url, params=None, timeout=None):
        self.calls.append(
            {"url": url, "params": dict(params or {}), "timeout": timeout}
        )
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _interface(**overrides):
    body = {
        "name": "suspend_d",
        "category": "股票",
        "cache_ttl": 21600,
        "provider": "tushare",
        "required": [],
        "required_any": [["ts_code"], ["trade_date"], ["start_date"]],
        "max_limit": 100000,
        "description": "每日停复牌信息",
        "enabled": True,
        "methods": ["GET"],
        "fallback_on_empty": True,
        "probe_supported": True,
        "probe_example": "/tushare/pro/suspend_d?__probe=1",
        "local_data_available": None,
        "local_latest_time": None,
        "requires_params": True,
    }
    body.update(overrides)
    return body


def _daily_body(rows=(("000001.SZ", "20260901"),)):
    return {
        "code": 0,
        "data": {
            "fields": [
                "ts_code", "trade_date", "open", "high", "low",
                "close", "pre_close", "vol",
            ],
            "items": [
                [r[0], r[1], "1.0", "2.0", "0.9", "1.5", "0.9", "100.0"]
                for r in rows
            ],
        },
    }


def _client(outcomes, **kwargs):
    clock = kwargs.pop("clock", None) or FakeClock()
    sleeps = kwargs.pop("sleeps", None)
    sleeper = sleeps.append if sleeps is not None else (lambda _: None)
    session = FakeSession(outcomes)
    client = TushareProxyClient(
        BASE_URL,
        "key-123",
        session=session,
        sleeper=sleeper,
        clock=clock,
        **kwargs,
    )
    return client, session, clock


def test_capability_cache_hit_is_served_without_a_request():
    client, session, _ = _client([FakeResponse(body=_interface())])
    first = client.capability("suspend_d")
    second = client.capability("suspend_d")
    assert first == second
    assert len(session.calls) == 1
    assert session.calls[0]["url"] == f"{ROOT}/capabilities/suspend_d"


def test_capability_cache_expires_after_its_own_ttl():
    outcomes = [
        FakeResponse(body=_interface(cache_ttl=300)),
        FakeResponse(body=_interface(cache_ttl=300)),
    ]
    client, session, clock = _client(outcomes)
    client.capability("suspend_d")
    clock.advance(299)
    client.capability("suspend_d")
    assert len(session.calls) == 1
    clock.advance(2)
    client.capability("suspend_d")
    assert len(session.calls) == 2


def test_capabilities_reads_the_catalog_table_and_caches_it():
    catalog = {
        "count": 2,
        "probe": {},
        "interfaces": [_interface(name="daily"), _interface(name="suspend_d")],
    }
    client, session, clock = _client([FakeResponse(body=catalog)])
    frame = client.capabilities()
    assert list(frame["name"]) == ["daily", "suspend_d"]
    clock.advance(21600 - 1)
    client.capabilities()
    assert len(session.calls) == 1
    assert session.calls[0]["url"] == f"{ROOT}/capabilities"


def test_upstreams_reads_the_probe_chain():
    chain = {
        "api_name": "suspend_d",
        "results": [
            {"name": "tickflow", "ok": False, "error": "unsupported_api", "rows": 0},
            {"name": "relay", "ok": False, "error": "fallback_error", "rows": 0},
        ],
    }
    client, session, _ = _client([FakeResponse(body=chain)])
    result = client.upstreams("suspend_d")
    assert [entry["name"] for entry in result["results"]] == ["tickflow", "relay"]
    assert session.calls[0]["url"] == f"{ROOT}/upstreams/probe/suspend_d"


def test_paged_read_still_filters_to_the_requested_range():
    rows = (("000001.SZ", "20191231"), ("000001.SZ", "20200102"))
    client, _, _ = _client([FakeResponse(body=_daily_body(rows))])
    frame = client.daily(
        ts_code="000001.SZ", start_date="20200101", end_date="20201231"
    )
    assert list(frame["trade_date"]) == ["20200102"]


def test_range_filter_is_skipped_when_the_frame_has_no_trade_date():
    body = {"code": 0, "data": {"fields": ["ts_code", "com_name"], "items": [["600000.SH", "浦发银行"]]}}
    client, _, _ = _client([FakeResponse(body=body)])
    frame = client.query(
        "stock_company", verify_capability="none",
        ts_code="600000.SH", start_date="20200101", end_date="20201231",
    )
    assert list(frame["com_name"]) == ["浦发银行"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/unit/test_tushare_proxy_capabilities.py -q`
Expected: FAIL —— `TypeError: __init__() got an unexpected keyword argument 'clock'`

- [ ] **Step 3: 重构 `__init__` / `from_env`，加 `clock` 与缓存字段**

替换 `tushare_proxy.py` 的 `__init__` 与 `from_env`：

```python
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: int = 30,
        max_retries: int = 3,
        session: requests.Session | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_attempts = min(max_retries + 1, _MAX_ATTEMPTS)
        self._sleeper = sleeper
        self._clock = clock
        self._session = session or requests.Session()
        self._session.headers["X-API-Key"] = api_key
        # Metadata lives one level above the data plane:
        # ``<root>/pro/{endpoint}`` for data, ``<root>/capabilities`` for shape.
        self._root = self.base_url.rsplit("/", 1)[0]
        self._catalog: tuple[float, pd.DataFrame] | None = None
        self._interface_cache: dict[str, tuple[float, Mapping[str, object]]] = {}

    @classmethod
    def from_env(
        cls,
        *,
        timeout_seconds: int = 30,
        max_retries: int = 3,
        session: requests.Session | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> TushareProxyClient | None:
        """Build the client from ``TUSHARE_PROXY_URL``/``TUSHARE_PROXY_KEY``.

        Returns ``None`` when either variable is unset or blank, leaving the
        adapter on the official SDK path.
        """
        base_url = os.environ.get("TUSHARE_PROXY_URL", "").strip()
        api_key = os.environ.get("TUSHARE_PROXY_KEY", "").strip()
        if not base_url or not api_key:
            return None
        return cls(
            base_url,
            api_key,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            session=session,
            sleeper=sleeper,
            clock=clock,
        )
```

同时把 `from typing import Callable` 所在的 import 段补上 `Literal`、`Mapping`、`cast`：

```python
from typing import Callable, Literal, Mapping, cast
```

- [ ] **Step 4: 抽出发用传输 `_request`，让 `_query`/`_parse` 走它**

替换 `_paged` / `_query` / `_parse` / `_api_error` 整段（原 `185-288`）：

```python
    def _paged(
        self,
        endpoint: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        **params: object,
    ) -> tuple[pd.DataFrame, list[tuple[str | None, str | None]]]:
        """Fetch bounded date windows and enforce the range client-side."""
        if start_date and end_date:
            windows: list[tuple[str | None, str | None]] = _date_windows(
                start_date, end_date
            )
        else:
            windows = [(start_date, end_date)]
        frames = []
        for window_start, window_end in windows:
            frames.append(
                self._query(
                    endpoint,
                    start_date=window_start,
                    end_date=window_end,
                    **params,
                )
            )
            if len(windows) > 1:
                self._sleeper(_WINDOW_PAUSE_SECONDS)
        if not frames:
            return pd.DataFrame(), windows
        frame = pd.concat(frames, ignore_index=True)
        if frame.empty or not (start_date and end_date):
            return frame, windows
        # Client-side range enforcement: date parameters are not uniformly
        # honoured upstream, and validate_supplier_frame enforces the range.
        # Endpoints outside the bar family have no ``trade_date`` at all; for
        # those the requested range is passed through rather than enforced.
        if "trade_date" not in frame.columns:
            return frame, windows
        dates = frame["trade_date"].astype(str)
        within = (dates >= str(start_date)) & (dates <= str(end_date))
        return frame[within].reset_index(drop=True), windows

    def _query(
        self,
        endpoint: str,
        *,
        read_timeout: int | None = None,
        **params: object,
    ) -> pd.DataFrame:
        """One data read; retry, throttling and recording live in `_request`."""
        return cast(
            pd.DataFrame,
            self._request(
                f"{self.base_url}/{endpoint}",
                self._parse,
                read_timeout=read_timeout,
                **params,
            ),
        )

    def _request(
        self,
        url: str,
        parse: Callable[[str, object], object],
        *,
        read_timeout: int | None = None,
        **params: object,
    ) -> object:
        """One GET with retry. ``parse(label, body)`` converts the JSON body.

        Every response -- including transient ones -- is handed to the
        recorder, so a failure still leaves its request id behind.
        """
        payload = {key: value for key, value in params.items() if value is not None}
        last_error: ServerError | None = None
        for attempt in range(self.max_attempts):
            try:
                response = self._session.get(
                    url,
                    params=payload,
                    timeout=(10, read_timeout or self.timeout_seconds),
                )
            except requests.exceptions.RequestException as error:
                last_error = ServerError(f"proxy {url} transport failure: {error}")
            else:
                if response.status_code in _TRANSIENT_HTTP_STATUS:
                    last_error = ServerError(f"proxy {url} HTTP {response.status_code}")
                elif response.status_code != 200:
                    raise ContractError(
                        f"proxy {url} HTTP {response.status_code}: "
                        f"{response.text[:120]}"
                    )
                else:
                    try:
                        body = response.json()
                    except ValueError:
                        # Transient upstream bodies (pool exhaustion,
                        # non-JSON gateway pages) participate in the backoff.
                        last_error = ServerError(
                            f"proxy {url} returned a non-JSON body"
                        )
                    else:
                        try:
                            return parse(url, body)
                        except ServerError as error:
                            last_error = error
            if attempt < self.max_attempts - 1:
                self._sleeper(
                    _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
                )
        assert last_error is not None
        raise last_error

    def _parse(self, label: str, body: object) -> pd.DataFrame:
        if isinstance(body, dict) and body.get("code") == 0:
            data = body.get("data") or {}
            return pd.DataFrame(data.get("items", []), columns=data.get("fields", []))
        raise _api_error(label, body)
```

把 `_api_error` 从方法改为模块级函数（放在 `_date_windows` 之后、`class` 之前），并新增三个解析器与 TTL 读取：

```python
def _api_error(label: str, body: object) -> Exception:
    if isinstance(body, dict):
        message = str(body.get("error") or body.get("msg") or body)[:200]
    else:
        message = str(body)[:200]
    lowered = message.lower()
    if any(marker in lowered for marker in _AUTH_MARKERS):
        return AuthenticationError(f"proxy rejected {label}: {message}")
    if any(marker in lowered for marker in _TRANSIENT_MARKERS):
        return ServerError(f"proxy {label} transient upstream failure: {message}")
    code = body.get("code") if isinstance(body, dict) else None
    return ContractError(f"proxy {label} non-zero code={code}: {message}")


def _parse_catalog(label: str, body: object) -> Mapping[str, object]:
    if not isinstance(body, dict) or "interfaces" not in body:
        raise _api_error(label, body)
    return body


def _parse_interface(label: str, body: object) -> Mapping[str, object]:
    if not isinstance(body, dict) or "name" not in body:
        raise _api_error(label, body)
    return body


def _parse_upstreams(label: str, body: object) -> Mapping[str, object]:
    if not isinstance(body, dict) or "results" not in body:
        raise _api_error(label, body)
    return body


def _cache_ttl(body: Mapping[str, object]) -> float:
    """The interface's own declared cache TTL, else the catalog default."""
    try:
        ttl = float(body.get("cache_ttl"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float(_CATALOG_TTL_SECONDS)
    return ttl if ttl > 0 else float(_CATALOG_TTL_SECONDS)
```

新增常量（放在 `_WINDOW_PAUSE_SECONDS` 附近）：

```python
#: The catalog table carries no TTL of its own; 21600 is the value the
#: overwhelming majority (181/298) of the individual interfaces declare.
_CATALOG_TTL_SECONDS = 21600
```

- [ ] **Step 5: 让 `daily` / `index_daily` 适配 `_paged` 的新返回形状**

```python
    def daily(
        self,
        ts_code: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        **_ignored: object,
    ) -> pd.DataFrame:
        frame, _ = self._paged(
            "daily", ts_code=ts_code, start_date=start_date, end_date=end_date
        )
        return frame

    def index_daily(
        self,
        ts_code: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        **_ignored: object,
    ) -> pd.DataFrame:
        frame, _ = self._paged(
            "index_daily", ts_code=ts_code, start_date=start_date, end_date=end_date
        )
        return frame
```

- [ ] **Step 6: 加能力发现三方法**

替换占位的 `query`（原 `177-179`），插入到 `stock_basic` 之后：

```python
    def capabilities(self, *, refresh: bool = False) -> pd.DataFrame:
        """The interface catalog, one row per declared interface (298 rows).

        This is the service's **declaration**, not a guarantee: the same
        catalog declared 60 requests/min per IP where the live response
        headers said 200, and reports ``enabled=true`` for interfaces that
        return zero rows.
        """
        now = self._clock()
        if not refresh and self._catalog is not None and now < self._catalog[0]:
            return self._catalog[1]
        body = cast(
            Mapping[str, object],
            self._request(f"{self._root}/capabilities", _parse_catalog),
        )
        frame = pd.DataFrame(body.get("interfaces") or [])
        self._catalog = (now + _CATALOG_TTL_SECONDS, frame)
        return frame

    def capability(self, name: str) -> Mapping[str, object]:
        """One interface's declared shape, cached for its own ``cache_ttl``."""
        now = self._clock()
        cached = self._interface_cache.get(name)
        if cached is not None and now < cached[0]:
            return cached[1]
        body = cast(
            Mapping[str, object],
            self._request(f"{self._root}/capabilities/{name}", _parse_interface),
        )
        self._interface_cache[name] = (now + _cache_ttl(body), body)
        return body

    def upstreams(self, name: str) -> Mapping[str, object]:
        """Which upstreams the proxy *reports* for an interface.

        The list is not the answering set: probed on 2026-09-12, all six
        upstreams reported ``suspend_d`` unsupported while the data endpoint
        served it.  Treat this as a diagnostic, never as attribution.
        """
        return cast(
            Mapping[str, object],
            self._request(
                f"{self._root}/upstreams/probe/{name}", _parse_upstreams
            ),
        )
```

- [ ] **Step 7: 跑新测试与既有测试**

Run: `python -m pytest tests/unit/test_tushare_proxy_capabilities.py tests/unit/test_tushare_proxy.py -q`
Expected: 新文件 6 passed；既有文件 **16 passed**（必须一字未改）

- [ ] **Step 8: ruff 检查改动**

Run: `ruff check src/stock_quant/data_sources/tushare_proxy.py tests/unit/test_tushare_proxy_capabilities.py && ruff format --check src/stock_quant/data_sources/tushare_proxy.py tests/unit/test_tushare_proxy_capabilities.py`
Expected: All checks passed

- [ ] **Step 9: 提交**

```bash
git add src/stock_quant/data_sources/tushare_proxy.py tests/unit/test_tushare_proxy_capabilities.py
git commit -m "feat: expose the proxy catalog and a shared request transport

Splits the retry loop into _request(url, parse, ...) so the metadata
plane shares the data plane's transport, then adds capabilities(),
capability(name) and upstreams(name) on top of it.  Capability TTL is
two-layer: 21600s for the catalog table, the interface's own cache_ttl
for a single row."
```

---

## Task 2: `query()` 受控化（GET-only + 能力预检 + fail-open）

**Files:**
- Modify: `src/stock_quant/data_sources/tushare_proxy.py`（`query` 占位段、新增 `_verify_capability` 与 `_NAMED_ENDPOINTS`）
- Test: `tests/unit/test_tushare_proxy_capabilities.py`

**Interfaces:**
- Consumes: Task 1 的 `capability()`、`_paged()`（二元组）、`ContractError`、`ServerError`
- Produces:
  - 模块级 `_NAMED_ENDPOINTS = ("daily", "index_daily", "stock_basic")`
  - `TushareProxyClient.query(endpoint: str, *, verify_capability: Literal["live", "none"] = "live", **params) -> pd.DataFrame`
  - `TushareProxyClient._verify_capability(endpoint: str, params: Mapping[str, object]) -> bool`

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_tushare_proxy_capabilities.py`：

```python
def test_disabled_interface_fails_fast_without_a_data_request():
    client, session, _ = _client([FakeResponse(body=_interface(enabled=False))])
    with pytest.raises(ContractError):
        client.query("suspend_d", ts_code="000333.SZ")
    assert [call["url"] for call in session.calls] == [
        f"{ROOT}/capabilities/suspend_d"
    ]


def test_required_any_must_be_satisfied():
    body = _interface(required_any=[["ts_code"], ["trade_date"]])
    client, session, _ = _client([FakeResponse(body=body)])
    with pytest.raises(ContractError):
        client.query("suspend_d", foo="bar")
    assert [call["url"] for call in session.calls] == [
        f"{ROOT}/capabilities/suspend_d"
    ]


def test_required_must_all_be_satisfied():
    body = _interface(required=["ts_code"], required_any=[])
    client, session, _ = _client([FakeResponse(body=body)])
    with pytest.raises(ContractError):
        client.query("suspend_d", start_date="20200101")
    assert [call["url"] for call in session.calls] == [
        f"{ROOT}/capabilities/suspend_d"
    ]


def test_unregistered_interface_surfaces_contract_error():
    unknown = FakeResponse(
        status_code=404, text='{"ok":false,"error":"unknown_api"}'
    )
    client, _, _ = _client([unknown])
    with pytest.raises(ContractError):
        client.query("no_such_endpoint_xyz")


def test_preflight_transient_failure_fails_open_and_records_it():
    served = {"code": 0, "data": {"fields": ["ts_code", "trade_date"],
                                  "items": [["000333.SZ", "20160518"]]}}
    client, session, _ = _client(
        [FakeResponse(status_code=503, text="busy"), FakeResponse(body=served)],
        max_retries=0,
    )
    frame = client.query("suspend_d", ts_code="000333.SZ")
    assert len(frame) == 1
    assert client.last_query_metadata["capability_checked"] is False
    assert session.calls[-1]["url"] == f"{BASE_URL}/suspend_d"


def test_named_endpoints_skip_preflight():
    client, session, _ = _client([FakeResponse(body=_daily_body())])
    client.daily(ts_code="000001.SZ", start_date="20260901", end_date="20260912")
    assert [call["url"] for call in session.calls] == [f"{BASE_URL}/daily"]


def test_query_on_a_named_endpoint_also_skips_preflight():
    client, session, _ = _client([FakeResponse(body=_daily_body())])
    client.query("daily", ts_code="000001.SZ",
                 start_date="20260901", end_date="20260912")
    assert [call["url"] for call in session.calls] == [f"{BASE_URL}/daily"]


def test_verify_capability_none_records_unchecked():
    served = {"code": 0, "data": {"fields": ["ts_code"], "items": [["000333.SZ"]]}}
    client, session, _ = _client([FakeResponse(body=served)])
    client.query("suspend_d", verify_capability="none", ts_code="000333.SZ")
    assert [call["url"] for call in session.calls] == [f"{BASE_URL}/suspend_d"]
    assert client.last_query_metadata["capability_checked"] is None


def test_write_only_interfaces_are_never_read():
    body = _interface(name="p_save", methods=["POST"])
    client, session, _ = _client([FakeResponse(body=body)])
    with pytest.raises(ContractError):
        client.query("p_save", ts_code="000001.SZ")
    assert all("/pro/p_save" not in call["url"] for call in session.calls)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/unit/test_tushare_proxy_capabilities.py -q -k "preflight or required or disabled or unregistered or write_only or named"`
Expected: FAIL —— `query()` 仍是占位实现，不读 capability

- [ ] **Step 3: 替换占位 `query`，新增 `_verify_capability`**

在 `capabilities` 之前插入常量与两个方法；`_NAMED_ENDPOINTS` 放模块级（`_WINDOW_PAUSE_SECONDS` 之后）：

```python
#: Interfaces the code itself pins, so their names need no runtime check.
_NAMED_ENDPOINTS = ("daily", "index_daily", "stock_basic")
```

```python
    def query(
        self,
        endpoint: str,
        *,
        verify_capability: Literal["live", "none"] = "live",
        **params: object,
    ) -> pd.DataFrame:
        """One generic read from the catalog.

        ``verify_capability="live"`` pre-flights the interface's declared
        shape and fails fast on a deterministic error.  **It is a shape check
        only.**  Passing it says nothing about whether the data is correct or
        attributable: the catalog has been falsified once already (it declared
        60 req/min per IP where the headers said 200), and
        ``fallback_on_empty`` describes *another source answering in this
        one's place*.  Pre-flight reads the interface's shape, never the
        data's provenance.
        """
        if endpoint in _NAMED_ENDPOINTS:
            verify_capability = "none"
        checked: bool | None = None
        if verify_capability == "live":
            checked = self._verify_capability(endpoint, params)
        start_date = params.pop("start_date", None)
        end_date = params.pop("end_date", None)
        frame, windows = self._paged(
            endpoint, start_date=start_date, end_date=end_date, **params
        )
        self.last_query_metadata = {
            "endpoint": endpoint,
            "capability_checked": checked,
            "windows": [tuple(window) for window in windows],
            "rows": int(len(frame)),
            "request_ids": [],
            "cache": [],
        }
        return frame

    def _verify_capability(self, endpoint: str, params: Mapping[str, object]) -> bool:
        """Check the declared shape before spending a data request.

        Returns ``True`` when the pre-flight passed and ``False`` when it
        could not be fetched at all (**fail-open**) -- the server's
        ``allow_unregistered_apis: false`` is the real gatekeeper, and a
        metadata hiccup must not block a data read.  Deterministic rejections
        raise instead: a disabled interface, a non-GET interface, or
        unsatisfied ``required`` / ``required_any``.
        """
        try:
            capability = self.capability(endpoint)
        except ServerError:
            return False
        if capability.get("enabled") is False:
            raise ContractError(f"proxy interface {endpoint} is disabled")
        methods = [str(method).upper() for method in (capability.get("methods") or [])]
        if "GET" not in methods:
            raise ContractError(
                f"proxy interface {endpoint} does not allow GET: "
                f"{capability.get('methods')}"
            )
        missing = [
            str(name)
            for name in (capability.get("required") or [])
            if str(name) not in params
        ]
        if missing:
            raise ContractError(
                f"proxy interface {endpoint} requires: {', '.join(missing)}"
            )
        required_any = capability.get("required_any") or []
        if required_any:
            groups = [[str(name) for name in group] for group in required_any]
            if not any(
                all(name in params for name in group) for group in groups
            ):
                alternatives = " | ".join(",".join(group) for group in groups)
                raise ContractError(
                    f"proxy interface {endpoint} requires one of: {alternatives}"
                )
        return True
```

- [ ] **Step 4: 加 `last_query_metadata` 实例字段**

在 `__init__` 的 `self._interface_cache = ...` 之后：

```python
        #: Audit trail of the most recent generic `query()` call.  Named reads
        #: (daily/index_daily/stock_basic) never write it; check that the last
        #: call was `query()` before reading it.
        self.last_query_metadata: dict[str, object] | None = None
```

- [ ] **Step 5: 跑测试**

Run: `python -m pytest tests/unit/test_tushare_proxy_capabilities.py tests/unit/test_tushare_proxy.py -q`
Expected: 新文件 15 passed；既有文件 16 passed

- [ ] **Step 6: ruff 检查改动**

Run: `ruff check src/stock_quant/data_sources/tushare_proxy.py tests/unit/test_tushare_proxy_capabilities.py && ruff format --check src/stock_quant/data_sources/tushare_proxy.py tests/unit/test_tushare_proxy_capabilities.py`
Expected: All checks passed

- [ ] **Step 7: 提交**

```bash
git add src/stock_quant/data_sources/tushare_proxy.py tests/unit/test_tushare_proxy_capabilities.py
git commit -m "feat: gate generic reads behind a GET-only capability pre-flight

query() now fails fast on a disabled interface, a non-GET interface, or
unsatisfied required/required_any -- and fails open (recording the fact)
when the metadata endpoint itself is unreachable.  Pre-flight is a shape
check, not a trust signal; the three named interfaces skip it by name so
the existing suite is untouched."
```

---

## Task 3: Provenance（`request_ids` 与 `cache` 入 metadata）

**Files:**
- Modify: `src/stock_quant/data_sources/tushare_proxy.py`（`_request`/`_query` 加 `log` 参数、`query()` 组装 metadata、新增 `_record`）
- Test: `tests/unit/test_tushare_proxy_capabilities.py`

**Interfaces:**
- Consumes: Task 1 的 `_request`、Task 2 的 `query()` 与 `last_query_metadata`
- Produces:
  - `TushareProxyClient._request(..., log: list[dict[str, str]] | None = None, ...)`
  - `TushareProxyClient._query(..., log: list[dict[str, str]] | None = None, ...)`
  - `TushareProxyClient._paged(..., log: list[dict[str, str]] | None = None, ...)`
  - `TushareProxyClient._record(log, response) -> None`
  - metadata 的 `request_ids: list[str]`、`cache: list[str]`

- [ ] **Step 1: 写失败测试**

```python
def test_query_records_request_ids_and_cache_layers():
    headers = {"x-request-id": "abc-123", "x-cache": "HIT", "x-cache-layer": "redis"}
    client, _, _ = _client([FakeResponse(body=_daily_body(), headers=headers)])
    client.query("daily", ts_code="000001.SZ",
                 start_date="20260901", end_date="20260912")
    metadata = client.last_query_metadata
    assert metadata["endpoint"] == "daily"
    assert metadata["request_ids"] == ["abc-123"]
    assert metadata["cache"] == ["HIT/redis"]
    assert metadata["windows"] == [("20260901", "20260912")]
    assert metadata["rows"] == 1


def test_query_records_one_entry_per_outbound_window():
    headers = {"x-request-id": "abc-123", "x-cache": "MISS"}
    client, _, _ = _client([FakeResponse(body=_daily_body(), headers=headers)] * 3)
    client.query("daily", ts_code="000001.SZ",
                 start_date="20150101", end_date="20260828")
    metadata = client.last_query_metadata
    assert metadata["windows"] == [
        ("20150101", "20191231"),
        ("20200101", "20241231"),
        ("20250101", "20260828"),
    ]
    assert metadata["request_ids"] == ["abc-123", "abc-123", "abc-123"]
    assert metadata["cache"] == ["MISS", "MISS", "MISS"]


def test_named_reads_never_write_query_metadata():
    client, _, _ = _client([FakeResponse(body=_daily_body())])
    assert client.last_query_metadata is None
    client.daily(ts_code="000001.SZ", start_date="20260901", end_date="20260912")
    assert client.last_query_metadata is None


def test_a_failed_query_still_records_the_failing_request_id():
    headers = {"x-request-id": "req-1", "x-cache": "MISS"}
    failure = FakeResponse(status_code=500, text="oops", headers=headers)
    client, _, _ = _client([failure], max_retries=0)
    with pytest.raises(ServerError):
        client.query("suspend_d", verify_capability="none", ts_code="000333.SZ")
    assert all("/suspend_d" not in call["url"] or True for call in [])  # see Step 4


def test_failed_query_leaves_the_previous_metadata_untouched():
    headers = {"x-request-id": "req-1", "x-cache": "MISS"}
    client, _, _ = _client(
        [FakeResponse(body=_daily_body(), headers={"x-request-id": "ok", "x-cache": "HIT"}),
         FakeResponse(status_code=500, text="oops", headers=headers)],
        max_retries=0,
    )
    client.query("daily", ts_code="000001.SZ",
                 start_date="20260901", end_date="20260912")
    good = client.last_query_metadata
    with pytest.raises(ServerError):
        client.query("suspend_d", verify_capability="none", ts_code="000333.SZ")
    assert client.last_query_metadata is good
```

> 第四与第五条用例合起来钉住同一件事：**失败的读取不覆盖上一次成功的
> metadata**（写入发生在读取成功之后，而非之前）。删掉
> `test_a_failed_query_still_records_the_failing_request_id`——它没有断言价值，
> 保留 `test_failed_query_leaves_the_previous_metadata_untouched` 即可。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/unit/test_tushare_proxy_capabilities.py -q -k "records or metadata"`
Expected: FAIL —— `metadata["request_ids"] == []`，而期望 `["abc-123"]`

- [ ] **Step 3: 给 `_request` / `_query` / `_paged` 串上 `log`**

在 `_request` 签名加 `log`，并在拿到响应后立刻记录：

```python
    def _request(
        self,
        url: str,
        parse: Callable[[str, object], object],
        *,
        read_timeout: int | None = None,
        log: list[dict[str, str]] | None = None,
        **params: object,
    ) -> object:
```

```python
            else:
                self._record(log, response)
                if response.status_code in _TRANSIENT_HTTP_STATUS:
```

`_query` 透传：

```python
    def _query(
        self,
        endpoint: str,
        *,
        read_timeout: int | None = None,
        log: list[dict[str, str]] | None = None,
        **params: object,
    ) -> pd.DataFrame:
        return cast(
            pd.DataFrame,
            self._request(
                f"{self.base_url}/{endpoint}",
                self._parse,
                read_timeout=read_timeout,
                log=log,
                **params,
            ),
        )
```

`_paged` 透传（签名加 `log`，循环里 `log=log`）：

```python
    def _paged(
        self,
        endpoint: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        log: list[dict[str, str]] | None = None,
        **params: object,
    ) -> tuple[pd.DataFrame, list[tuple[str | None, str | None]]]:
```

```python
                self._query(
                    endpoint,
                    start_date=window_start,
                    end_date=window_end,
                    log=log,
                    **params,
                )
```

- [ ] **Step 4: 加 `_record` 与 `_cache_label`**

```python
    def _record(
        self, log: list[dict[str, str]] | None, response: requests.Response
    ) -> None:
        """Keep the only after-the-fact handle on an un-attributable response.

        Nothing in the body or headers names the answering upstream, so the
        request id is what an operator can quote back to the proxy operator,
        and the cache flags are the only clue whether the body was served
        fresh or replayed.
        """
        if log is None:
            return
        headers = _lower_headers(response)
        log.append(
            {
                "request_id": headers.get("x-request-id", ""),
                "cache": _cache_label(headers),
            }
        )
```

模块级：

```python
def _lower_headers(response: requests.Response) -> dict[str, str]:
    headers = getattr(response, "headers", None) or {}
    return {str(key).lower(): str(value) for key, value in dict(headers).items()}


def _cache_label(headers: Mapping[str, str]) -> str:
    cache = headers.get("x-cache", "")
    layer = headers.get("x-cache-layer", "")
    return "/".join(part for part in (cache, layer) if part)
```

- [ ] **Step 5: 在 `query()` 里组装 metadata**

```python
        log: list[dict[str, str]] = []
        frame, windows = self._paged(
            endpoint, start_date=start_date, end_date=end_date, log=log, **params
        )
        self.last_query_metadata = {
            "endpoint": endpoint,
            "capability_checked": checked,
            "windows": [tuple(window) for window in windows],
            "rows": int(len(frame)),
            "request_ids": [
                entry["request_id"] for entry in log if entry["request_id"]
            ],
            "cache": [entry["cache"] for entry in log if entry["cache"]],
        }
        return frame
```

- [ ] **Step 6: 跑测试**

Run: `python -m pytest tests/unit/test_tushare_proxy_capabilities.py tests/unit/test_tushare_proxy.py -q`
Expected: 新文件 18 passed；既有文件 16 passed

- [ ] **Step 7: ruff 检查改动**

Run: `ruff check src/stock_quant/data_sources/tushare_proxy.py tests/unit/test_tushare_proxy_capabilities.py && ruff format --check src/stock_quant/data_sources/tushare_proxy.py tests/unit/test_tushare_proxy_capabilities.py`
Expected: All checks passed

- [ ] **Step 8: 提交**

```bash
git add src/stock_quant/data_sources/tushare_proxy.py tests/unit/test_tushare_proxy_capabilities.py
git commit -m "feat: keep the request id and cache flags of every generic read

No response names its answering upstream, so x-request-id is the only
after-the-fact handle and x-cache/x-cache-layer the only freshness clue.
Both were being discarded; query() now surfaces them per outbound window.
Named reads still write no metadata, and a failed read leaves the prior
trail intact."
```

---

## Task 4: 限速（以响应头为准）

**Files:**
- Modify: `src/stock_quant/data_sources/tushare_proxy.py`（常量、`__init__` 两个字段、`_observe_rate_limit`、`_throttle`、`_request` 两处调用）
- Test: `tests/unit/test_tushare_proxy_capabilities.py`

**Interfaces:**
- Consumes: Task 1 的 `_request` 与 `_clock`、Task 3 的 `_lower_headers`
- Produces:
  - 模块级 `_RATE_LIMIT_REMAINING_HEADER = "x-ratelimit-ip-remaining"`
  - 模块级 `_RATE_LIMIT_LOW_WATERMARK = 10`
  - 模块级 `_FALLBACK_MIN_INTERVAL_SECONDS = 1.0`
  - `TushareProxyClient._observe_rate_limit(response) -> None`
  - `TushareProxyClient._throttle() -> None`
  - 实例字段 `_rate_limited: bool`、`_next_allowed_at: float`

- [ ] **Step 1: 写失败测试**

```python
from stock_quant.data_sources.tushare_proxy import _FALLBACK_MIN_INTERVAL_SECONDS


def _read(client):
    return client.query("daily", ts_code="000001.SZ",
                        start_date="20260901", end_date="20260912")


def test_low_remaining_throttles_the_next_request():
    sleeps = []
    outcomes = [
        FakeResponse(body=_daily_body(),
                     headers={"x-ratelimit-ip-remaining": "3"}),
        FakeResponse(body=_daily_body()),
    ]
    client, _, _ = _client(outcomes, sleeps=sleeps)
    _read(client)
    _read(client)
    assert sleeps == [_FALLBACK_MIN_INTERVAL_SECONDS]


def test_high_remaining_does_not_throttle():
    sleeps = []
    outcomes = [
        FakeResponse(body=_daily_body(),
                     headers={"x-ratelimit-ip-remaining": "199"}),
        FakeResponse(body=_daily_body()),
    ]
    client, _, _ = _client(outcomes, sleeps=sleeps)
    _read(client)
    _read(client)
    assert sleeps == []


def test_missing_headers_fall_back_once_a_limit_has_been_seen():
    sleeps = []
    outcomes = [
        FakeResponse(body=_daily_body(),
                     headers={"x-ratelimit-ip-remaining": "199"}),
        FakeResponse(body=_daily_body()),   # no headers at all
        FakeResponse(body=_daily_body()),
    ]
    client, _, _ = _client(outcomes, sleeps=sleeps)
    _read(client)
    _read(client)
    _read(client)
    assert sleeps == [_FALLBACK_MIN_INTERVAL_SECONDS]


def test_retry_after_takes_priority_over_remaining():
    sleeps = []
    outcomes = [
        FakeResponse(body=_daily_body(),
                     headers={"Retry-After": "5",
                              "x-ratelimit-ip-remaining": "199"}),
        FakeResponse(body=_daily_body()),
    ]
    client, _, _ = _client(outcomes, sleeps=sleeps)
    _read(client)
    _read(client)
    assert sleeps == [5.0]


def test_requests_without_any_rate_limit_header_are_never_throttled():
    sleeps = []
    client, _, _ = _client([FakeResponse(body=_daily_body())] * 3, sleeps=sleeps)
    _read(client)
    _read(client)
    _read(client)
    assert sleeps == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/unit/test_tushare_proxy_capabilities.py -q -k "throttle or remaining or retry_after or fall_back"`
Expected: FAIL —— `sleeps == []`，限速尚未实现

- [ ] **Step 3: 加常量与实例字段**

```python
_RATE_LIMIT_REMAINING_HEADER = "x-ratelimit-ip-remaining"
#: Below this many remaining calls in the current window, back off.  The
#: window size itself comes from the headers, not the catalog.
_RATE_LIMIT_LOW_WATERMARK = 10
#: Used when we must throttle but have no header to size the wait: 60/min is
#: the more conservative of the falsified declaration (60) and the measured
#: header (200).
_FALLBACK_MIN_INTERVAL_SECONDS = 1.0
```

`__init__` 追加：

```python
        self._rate_limited = False
        self._next_allowed_at = 0.0
```

- [ ] **Step 4: 实现两个方法**

```python
    def _observe_rate_limit(self, response: requests.Response) -> None:
        """Throttle from what the server reports, never from the catalog.

        The catalog declares 60 requests/min per IP while the live headers
        said 200 -- the declaration has been falsified, so only headers are
        trusted here, and the budget is **shared across every user of this
        IP**, so "we don't ask for much" is not a reason to skip it.

        A response carrying no rate-limit header at all teaches nothing, so
        until one arrival is seen this client does not throttle.  Once seen,
        a later header-less response falls back to the conservative interval
        rather than opening up.
        """
        headers = _lower_headers(response)
        has_signal = any(
            key.startswith("x-ratelimit") or key == "retry-after"
            for key in headers
        )
        if not has_signal and not self._rate_limited:
            return
        self._rate_limited = True
        now = self._clock()
        retry_after = headers.get("retry-after")
        if retry_after is not None:
            try:
                self._next_allowed_at = now + max(float(retry_after), 0.0)
                return
            except ValueError:
                pass
        interval = _FALLBACK_MIN_INTERVAL_SECONDS
        remaining = headers.get(_RATE_LIMIT_REMAINING_HEADER)
        if remaining is not None:
            try:
                remaining_value = int(float(remaining))
            except ValueError:
                remaining_value = 0
            interval = (
                _FALLBACK_MIN_INTERVAL_SECONDS
                if remaining_value < _RATE_LIMIT_LOW_WATERMARK
                else 0.0
            )
        self._next_allowed_at = now + interval

    def _throttle(self) -> None:
        if not self._rate_limited:
            return
        wait = self._next_allowed_at - self._clock()
        if wait > 0:
            self._sleeper(wait)
```

- [ ] **Step 5: 在 `_request` 里调用**

在 `for attempt in range(self.max_attempts):` 之后、`try:` 之前插入：

```python
            self._throttle()
```

在 `else:` 分支里、`self._record(log, response)` **之前**插入：

```python
                self._observe_rate_limit(response)
```

- [ ] **Step 6: 跑测试**

Run: `python -m pytest tests/unit/test_tushare_proxy_capabilities.py tests/unit/test_tushare_proxy.py -q`
Expected: 新文件 23 passed；既有文件 **16 passed**（`test_pool_exhaustion_retries_with_backoff_then_succeeds` 的 `sleeps == [2]` 与 `test_transient_failures_exhaust_attempts_then_raise_server_error` 的 `sleeps == [2, 6, 12, 20]` 必须仍绿——它们的 `FakeResponse` 没有 `headers`，故从未触发节流）

- [ ] **Step 7: ruff 检查改动**

Run: `ruff check src/stock_quant/data_sources/tushare_proxy.py tests/unit/test_tushare_proxy_capabilities.py && ruff format --check src/stock_quant/data_sources/tushare_proxy.py tests/unit/test_tushare_proxy_capabilities.py`
Expected: All checks passed

- [ ] **Step 8: 提交**

```bash
git add src/stock_quant/data_sources/tushare_proxy.py tests/unit/test_tushare_proxy_capabilities.py
git commit -m "feat: throttle from response headers, not the catalog declaration

The catalog claims 60 requests/min per IP; the headers said 200.  The
declaration is falsified, so the remaining-call header drives the pacing,
Retry-After wins when present, and a header-less response falls back to
the conservative 1s once a limit has actually been observed."
```

---

## Task 5: 缺陷收口（配置、模板、docstring）

**Files:**
- Modify: `project/configs/sources.yml`
- Modify: `.env.example`
- Modify: `src/stock_quant/data_sources/tushare_proxy.py:1-23`（docstring）
- Test: 无新单测；以 `python -c "import stock_quant.data_pipeline"` 与 grep 作为验收

**Interfaces:**
- Consumes: 无
- Produces: 无代码接口；删除死配置段

- [ ] **Step 1: 删除 `sources.yml` 的死配置段**

把 `project/configs/sources.yml` 替换为：

```yaml
tushare:
  enabled: true
  timeout_seconds: 30
  max_retries: 3
# The shared Tushare-compatible aggregation front (GET transport) inherits
# this segment's timeout_seconds/max_retries.  It is deliberately NOT its own
# source: `_CONFIGURED_SOURCES` (data_pipeline.py) is the closed registry the
# availability gate walks, and the proxy must not add a name to it.  When
# TUSHARE_PROXY_URL + TUSHARE_PROXY_KEY are set in the environment the tushare
# adapter routes through the proxy (windowed pages, transient retry); the
# official SDK stays the fallback.  Data source only - never an evidence
# source: universe/corporate-action evidence chains keep their official
# filings regardless of this switch.
akshare:
  enabled: true
  timeout_seconds: 30
  max_retries: 3
# baostock data server (:10030) down since 2026-09-05; optional cross-check only.
# Re-enable once reachable.
baostock:
  enabled: false
  timeout_seconds: 30
  max_retries: 3
```

- [ ] **Step 2: 验证配置仍能加载且注册表未变**

Run:
```bash
python -c "
from pathlib import Path
from stock_quant.config import load_project_config
from stock_quant.data_pipeline import _CONFIGURED_SOURCES
config = load_project_config(Path('project'))
print('sources:', sorted(config.sources))
print('registry:', _CONFIGURED_SOURCES)
assert 'tushare_proxy' not in config.sources
assert 'tushare_proxy' not in _CONFIGURED_SOURCES
"
```
Expected: `sources: ['akshare', 'baostock', 'tushare']`、`registry: ('tushare', 'akshare', 'baostock')`，无断言错误

> `load_project_config(root: Path)` 是必填参数（`src/stock_quant/config.py:82`），
> 本仓库无参调用不存在；`ProjectConfig.sources` 是 `dict[str, SourceConfig]`。

- [ ] **Step 3: 更正 `.env.example` 的归属说明**

`project/configs/sources.yml` 那段注释已经说明了继承关系；模板里只需说明
RDS 变量的归属。把 `.env.example` 替换为：

```dotenv
TUSHARE_TOKEN=
# Shared Tushare-compatible GET proxy (datahubco / mobcvb aggregation front).
# When both are set, the tushare adapter routes through it and TUSHARE_TOKEN
# becomes unnecessary.  Paste the personal X-API-Key value below.
TUSHARE_PROXY_URL=https://pcd.mobcvb.cn/tushare/pro
TUSHARE_PROXY_KEY=

# datahubco RDS.  Consumed by the workbuddy project, NOT by this repository -
# no code here reads it.  Kept in the template so the URL is not lost.
BASIC_RDS_URL=http://datahubco.com/app-api/openapi/v1/tushare/stock-basic
BASIC_RDS_KEY=
```

> 注意：`BASIC_RDS_RUL` 的拼写在本计划写作时**已经是正确的 `BASIC_RDS_URL`**
> （提交 `0e71ae3`），故这里只去掉等号两侧的空格并加归属说明。若你看到的仍是
> `RUL`，一并改正。

- [ ] **Step 4: 核对 `.env` 齐备性（只读，不打印任何值）**

Run:
```bash
python -c "
import hashlib, pathlib
for line in pathlib.Path('.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    key, value = line.split('=', 1)
    value = value.strip()
    digest = hashlib.sha256(value.encode()).hexdigest()[:8] if value else '-'
    print(f'{key.strip():20} present={bool(value)} len={len(value):3} sha8={digest}')
"
```
Expected: `TUSHARE_PROXY_URL present=True`、`TUSHARE_PROXY_KEY present=True`（47 字符）、
`BASIC_RDS_KEY present=True`（56 字符）。**不打印值，只打印长度与哈希。**

- [ ] **Step 5: 更正模块 docstring 的两处失准记载**

把 `tushare_proxy.py` 的 docstring 替换为：

```python
"""GET transport for the shared Tushare-compatible aggregation front.

The proxy fronts Tushare-shaped data with one GET endpoint per API
(``<base>/daily?ts_code=...``) authenticated by an ``X-API-Key`` header; it
does not speak the official SDK's POST protocol.  ``TushareProxyClient``
mirrors the small SDK surface the adapters consume (``daily``,
``index_daily``, ``stock_basic``) and adds the guards its shared-server
nature demands (each observed live on 2026-09-12):

- responses truncate silently at ~6000 rows, so date ranges are fetched in
  bounded windows and concatenated;
- the upstream pool intermittently answers ``upstream_pool_exhausted`` or
  stalls mid-body, so transient failures retry with backoff;
- the service rate-limits by **shared IP budget** and reports the remaining
  allowance in response headers, so pacing follows the headers rather than
  the catalog's declaration (which was measured wrong: it claimed
  60 requests/min per IP where the headers said 200).

Outside the SDK surface, ``query()`` reaches any of the ~298 catalog
interfaces behind a GET-only assertion and a capability pre-flight.  That
pre-flight checks the interface's **declared shape** and nothing else: it is
not a trust signal, because no response -- body or header -- names the
upstream that answered.  ``capabilities()`` / ``capability(name)`` /
``upstreams(name)`` expose the declarations for diagnostics.

It is a transport for Tushare-format data, not an evidence source: frames
keep the tushare layout (``vol`` lots, ``amount`` thousand-yuan) so the
existing normalization contract is unchanged, while raw-store snapshots pin
this transport through the fetch metadata.
"""
```

> 删掉的两条失准记载：`dividend` "filtered to empty"（实测带日期区间是
> **HTTP 503**）、`suspend_d` "losing rows when given `suspend_type`"（实测是
> **多**返回一行 `20160616`）。这两条的实测结论记在评估报告里，不放 docstring。

- [ ] **Step 6: 跑测试确认无回归**

Run: `python -m pytest tests/unit/test_tushare_proxy.py tests/unit/test_tushare_proxy_capabilities.py -q`
Expected: 32 passed

- [ ] **Step 7: ruff 检查改动**

Run: `ruff check src/stock_quant/data_sources/tushare_proxy.py && ruff format --check src/stock_quant/data_sources/tushare_proxy.py`
Expected: All checks passed

- [ ] **Step 8: 提交**

```bash
git add project/configs/sources.yml .env.example src/stock_quant/data_sources/tushare_proxy.py
git commit -m "fix: drop the dead proxy config segment and correct two stale quirks

sources.yml's tushare_proxy block had no consumer -- _CONFIGURED_SOURCES
is a closed registry and _build_source does not know the name, so the
proxy was silently inheriting the tushare segment all along.  The
docstring's dividend and suspend_d notes were measured wrong on
2026-09-12 and are replaced with what the service actually does."
```

---

## Task 6: 只读探针 `project/probe_tushare_proxy.py`

**Files:**
- Create: `project/probe_tushare_proxy.py`
- Test: 以实跑为准（该脚本只读、联网）

**Interfaces:**
- Consumes: Task 1 的 `capabilities()` / `capability(name)` / `upstreams(name)`；`TushareProxyClient.from_env()`
- Produces: 命令行脚本；无被导入接口

- [ ] **Step 1: 写脚本**

创建 `project/probe_tushare_proxy.py`：

```python
#!/usr/bin/env python
"""Read-only probe of the shared Tushare-compatible aggregation front.

Prints what the proxy can do *right now*: the interface catalog by category,
the upstream chain it *reports* for each interface this repository has an open
problem for, and those interfaces' declared shape.

Reports facts only, and never asserts trust.  The catalog is a declaration,
not a guarantee: measured on 2026-09-12 it declared 60 requests/min per IP
where the response headers said 200, and reported ``enabled=true`` for
interfaces that return zero rows.  The upstream list is likewise not the
answering set -- probing ``suspend_d`` reported all six upstreams as
unsupporting it while the data endpoint served it.

Run from the repository root or the project directory:
    python project/probe_tushare_proxy.py
    python project/probe_tushare_proxy.py --probe suspend_d
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from pathlib import Path

from stock_quant.data_sources.tushare_proxy import TushareProxyClient


def _load_env(path: Path) -> None:
    """Load simple KEY=VALUE lines without evaluating shell code.

    Mirrors ``project/check_data_sources.py``: parsing beats ``source``, which
    chokes on this repo's ``.env`` (line 4 is ``BASIC_RDS_KEY = ...`` with
    spaces, which bash reads as a command).
    """
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", maxsplit=1)
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))

#: Interfaces this repository has an open problem for.  See the assessment
#: report's "可解锁卡点索引" for what each one unblocks.
WATCHED = (
    "suspend_d",
    "adj_factor",
    "trade_cal",
    "daily_basic",
    "index_weight",
    "dividend",
    "stk_limit",
    "index_member",
    "bak_basic",
    "moneyflow",
)


def _print_catalog(client: TushareProxyClient) -> None:
    try:
        frame = client.capabilities()
    except Exception as error:  # noqa: BLE001 - diagnostic boundary
        print(f"catalog: FAIL {type(error).__name__}: {error}")
        return
    enabled = frame[frame["enabled"].astype(bool)]
    print(f"interfaces: {len(frame)} (enabled {len(enabled)})")
    print("\nby category (enabled/total):")
    totals = Counter(frame["category"].astype(str))
    live = Counter(enabled["category"].astype(str))
    for category, count in totals.most_common():
        print(f"  {category:<14} {live.get(category, 0):>4}/{count:<4}")


def _print_chain(client: TushareProxyClient, name: str) -> None:
    try:
        chain = client.upstreams(name)
    except Exception as error:  # noqa: BLE001 - diagnostic boundary
        print(f"      upstreams: ERROR {type(error).__name__}: {error}")
        return
    for entry in chain.get("results") or []:
        status = "ok" if entry.get("ok") else "FAIL"
        detail = entry.get("message") or entry.get("error") or ""
        print(
            f"      {str(entry.get('name')):<14} {status:<5} "
            f"rows={entry.get('rows')} {str(detail)[:60]}"
        )


def _print_watched(client: TushareProxyClient) -> None:
    print("\nwatched interfaces:")
    for name in WATCHED:
        try:
            capability = client.capability(name)
        except Exception as error:  # noqa: BLE001 - diagnostic boundary
            print(f"  {name:<14} ERROR {type(error).__name__}: {error}")
            continue
        required_any = capability.get("required_any") or []
        alternatives = " | ".join(
            ",".join(str(field) for field in group) for group in required_any
        )
        methods = ",".join(str(method) for method in (capability.get("methods") or []))
        print(
            f"  {name:<14} enabled={str(capability.get('enabled')):<5} "
            f"methods={methods or '-'} max_limit={capability.get('max_limit')} "
            f"required_any={alternatives or '-'}"
        )
        _print_chain(client, str(capability.get("name") or name))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".env",
        help="KEY=VALUE file to load first (default: repo-root .env)",
    )
    parser.add_argument(
        "--probe",
        metavar="NAME",
        help="smoke-read one interface with __probe=1 (max 5 rows, server-side)",
    )
    args = parser.parse_args()
    if args.env_file.is_file():
        _load_env(args.env_file)
        print(f"environment: loaded {args.env_file}")
    else:
        print(f"environment: not found ({args.env_file}); using current environment")

    client = TushareProxyClient.from_env()
    if client is None:
        print("TUSHARE_PROXY_URL / TUSHARE_PROXY_KEY are not set; nothing to probe")
        return 1
    print(f"proxy host: {client.host}")

    if args.probe:
        # __probe=1 is the server's own sample mode and ignores the interface's
        # required_any, so this read is deliberately unchecked.  A live
        # pre-flight would reject e.g. `daily` (required_any:
        # ts_code|trade_date|start_date|end_date) before the sample is asked for.
        frame = client.query(
            args.probe, verify_capability="none", **{"__probe": 1}
        )
        print(f"\n{args.probe} __probe=1 -> {len(frame)} rows")
        print(frame.head().to_string(index=False))
        return 0

    _print_catalog(client)
    _print_watched(client)
    print(
        "\nNote: catalog entries are declarations, not guarantees. Pre-flight "
        "checks shape only - no response identifies the answering upstream."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 实跑探针**

Run: `python project/probe_tushare_proxy.py 2>&1 | head -40`
Expected: 打印 `interfaces: 298 (enabled N)`，随后按 category 的分组表
（`enabled` 的确切数字以本次实跑为准，勿写死；298 是声明总数，实测值）；watched 段落
逐条打印 `enabled` / `methods` / `max_limit` / `required_any` 与其六条上游链。

> 这个端点实测会 mid-body stall（114688/134194 字节处断），脚本会重试；若整体
> 超时，用 `--probe suspend_d` 验证脚本骨架，再把完整跑留到后台执行。

- [ ] **Step 3: 实跑冒烟路径**

Run: `python project/probe_tushare_proxy.py --probe suspend_d`
Expected: 打印 5 行 `suspend_d` 采样（`20120608` 起的 `R` 类型行）

- [ ] **Step 4: ruff 检查**

Run: `ruff check project/probe_tushare_proxy.py && ruff format --check project/probe_tushare_proxy.py`
Expected: All checks passed

- [ ] **Step 5: 提交**

```bash
git add project/probe_tushare_proxy.py
git commit -m "feat: add a read-only probe for the proxy capability surface

One command answers 'what can the proxy do right now': the catalog by
category, each watched interface's declared shape, and the upstream chain
it reports.  Reports facts and never asserts trust -- the upstream list
demonstrably excludes the source that answers."
```

---

## Task 7: 评估报告与既有文档注记

**Files:**
- Create: `docs/operations/2026-09-12-tushare-proxy-assessment.md`
- Modify: `docs/superpowers/plans/2026-09-12-suspension-backfill.md:1-3`
- Modify: `docs/superpowers/plans/2026-09-10-index-constitution-csi300-snapshot.md:1-3`
- Modify: `docs/operations/2026-09-11-trusted-data-chain.md:1-6`

**Interfaces:**
- Consumes: spec 的 §8/§9/实测证据各表；Task 6 的探针输出
- Produces: 无代码接口

- [ ] **Step 1: 写评估报告**

创建 `docs/operations/2026-09-12-tushare-proxy-assessment.md`，骨架与必含内容：

```markdown
# Tushare 代理评估：一个新入口，不是一条新证据链

> **信任等级：数据源（transport）。不是证据源。** 本报告把"多接一个入口"
> 能买到什么、买不到什么写成结论：能买到**可达性**与**速度**，买不到
> **可归因性**与**更稳**。

## 0. 结论摘要

（三到五句：能力面从 3 个接口变成 298 个；上游不可溯源是结构性的；稳定性
不解决反而多一个不稳定面；可信性结构性下降；唯一翻案条件当前不成立。）

## 1. 能力面

（`/tushare/capabilities` 298 条 / enabled 259；provider 分布；
`/tushare/capabilities/{name}` 轻量单接口；`/upstreams/probe/{name}`；
`p_save`/`p_delete` 存在且 enabled → 通用面必须 GET-only。）

## 2. 上游与溯源：结构性不可归因

（六上游列表不含 tushare；224 个 `provider: "tushare"` 全部
`fallback_on_empty: true`；正文与响应头都不标识上游。
**本轮新增的最强证据**：`/tushare/upstreams/probe/suspend_d` 显示六个上游
**全部**报不支持该接口（`tickflow`/`eastmoney`/`sina-minute`/`sina` 报
`unsupported_api`、`citydata` 报"参数不能为空"、`relay` 报 502
`fallback_error`），而 `/tushare/pro/suspend_d` 确实返回数据 —— 故**探测列表
与作答集合是两回事**，"六选一"的说法本身也不成立。）

## 3. 三个问题，三个答案

（照抄 spec §8.2 的表，含 RDS 第二入口不影响结论那一条。）

## 4. 限速

（声明 60/min per IP vs 实测头 200/min；节流改为以响应头为准；
共享 IP 预算的含义。）

## 5. 缺陷与修复记录

（对照 spec §5 四行，逐行写"现状 → 处置 → 本次提交的结论"。
`.env.example` 的拼写**已是 `BASIC_RDS_URL`**（提交 `0e71ae3`），
密钥齐备性核对结果只记"齐备/缺失"，不记值。）

## 6. 可解锁卡点索引

| 卡点 | 出口 | 本报告对应的既有文档 |
| --- | --- | --- |
| 停牌回补的"外部来源全部受阻" | `suspend_d` 可读 | `plans/2026-09-12-suspension-backfill.md` |
| csi300 的 299-run | `index_weight` 可读（但**不是验收依据**，见下） | `plans/2026-09-10-index-constitution-csi300-snapshot.md` |
| 时点总收益 / 复权 | `adj_factor` 可读 | — |
| 交易日历第三方佐证 | `trade_cal` 可读 | — |
| 可交易过滤 / 因子 | `daily_basic` 可读 | — |
| 公司行为交叉核对 | `dividend` 可读（**不带**日期区间） | — |

**每一条都只解锁"能读到"，不解锁"能验收"。** 尤其 `index_weight` 是月度
成分快照、不可溯源，用它顶替官方公告等于用"看起来对"换"可证明对"。

## 7. 同源对照：datahubco RDS

（照抄 spec「实测证据 · 同源对照」的表：逐位一致、5562 同数、无
`x-request-id`、退市 339 只、退市股行情 HTTP 400、`list_status=P` 为空、
默认投影缺 `delist_date`。结论：多入口同源不构成互证。）

## 8. 观察点（唯一可能翻案的条件）

（若代理将来暴露"本次由哪个上游作答"，`cross_source_price_sample` 的
独立性可在排除 `relay` 后成立。当前不成立。不预留代码路径。）

## 9. 残留风险

（照抄 spec「已知残留风险」五条。）
```

- [ ] **Step 2: 给三份既有文档加"前提已过时"注记**

`docs/superpowers/plans/2026-09-12-suspension-backfill.md` —— 在 H1 之后插入：

```markdown
> **前提已过时（2026-09-12）：** 本文所记"停牌证据外部来源全部受阻
> （`suspend_d` 无权限、baostock 停机、`stock_tfp_em` 无历史覆盖）"对
> **本地直连**仍然成立，但经共享代理可读：实测 `suspend_d`
> `000333.SZ` 2016-05-01..06-30 返回 10 行 = `2016-05-18..05-31`，正是本文
> 判定无源的那段区间。**这不改变本方案取向**——用主源 `pre_close` 链合成
> 停牌行仍是既成事实；`suspend_d` 现在可以充当**独立交叉校验**（见
> `docs/operations/2026-09-12-tushare-proxy-assessment.md` §6）。
```

`docs/superpowers/plans/2026-09-10-index-constitution-csi300-snapshot.md` —— 在 H1 之后插入：

```markdown
> **前提已过时（2026-09-12）：** 本文记的 2017-02-13\~2019-06-14 的 299-run
> 卡点出现了一条新路：共享代理的 `index_weight` 对 `000300.SH` 返回**恰好
> 300 行**含 `weight`（2017-02 实测 32.4s）。**但这不能作为验收依据**——
> 它是月度成分快照、不是官方公告，且不可溯源。它可以缩短寻路，不能替代
> 官方 2017-02 公告这一证据。详见
> `docs/operations/2026-09-12-tushare-proxy-assessment.md` §6。
```

`docs/operations/2026-09-11-trusted-data-chain.md` —— 在文件顶部的引用块**之后**插入：

```markdown
> **前提已过时（2026-09-12）：** 本文的 `cross_source_price_sample` 第二价格源
> 候选经复核**不成立**：共享代理不暴露本次作答的上游（正文与响应头都没有），
> 且 `fallback_on_empty: true` 覆盖全部 224 个 tushare 方言接口；已接入的
> datahubco RDS 与代理对同一事实逐位一致，属**同源**，不能互相担保。故
> "独立第二价格源"仍需另找。详见
> `docs/operations/2026-09-12-tushare-proxy-assessment.md` §2 与 §7。
```

- [ ] **Step 3: 校验注记确实落在标题之后、引用块之前/之后**

Run: `head -12 docs/superpowers/plans/2026-09-12-suspension-backfill.md docs/superpowers/plans/2026-09-10-index-constitution-csi300-snapshot.md docs/operations/2026-09-11-trusted-data-chain.md`
Expected: 三份文件都在 H1 之后能看到"前提已过时（2026-09-12）"；既有内容一字未删

- [ ] **Step 4: 提交**

```bash
git add docs/operations/2026-09-12-tushare-proxy-assessment.md docs/superpowers/plans/2026-09-12-suspension-backfill.md docs/superpowers/plans/2026-09-10-index-constitution-csi300-snapshot.md docs/operations/2026-09-11-trusted-data-chain.md
git commit -m "docs: assess the proxy as an entry point, not a new evidence chain

Records what the extra entry buys (reachability, speed) and what it does
not (attribution, stability, credibility), plus the strongest evidence
for the latter: probing suspend_d reports all six upstreams as
unsupporting it while the data endpoint serves it, so the reported chain
is not the answering set.  Adds point-in-time notes to the three documents
whose premises the measurements outdated."
```

---

## 自审记录

**Spec 覆盖检查**

| spec 章节 | 落在哪个任务 |
| --- | --- |
| §1 能力发现（含两层 TTL） | Task 1 |
| §2 `query()` 受控化（GET-only / 预检 / fail-open / 具名跳过） | Task 2 |
| §3 限速（响应头为准、Retry-After、回落常量） | Task 4 |
| §4 Provenance（两条路径、`request_ids`、`cache`） | Task 3（管线路径本就未改，见约束） |
| §5 缺陷收口（4 行 + HEAD token 段） | Task 5 |
| §6 只读探针 | Task 6 |
| §7 文档（报告 + 3 处注记） | Task 7 |
| §8 角色边界 | Task 7（报告 §2/§3/§6 复述；不在代码层加约束） |
| §9 稳定性 | Task 7（报告 §0 与 §9） |
| ## 测试 13 行 | Task 1–4 的测试步骤合起来覆盖全部 13 行 |

**类型一致性检查**

- `_paged` 在 Task 1 改为返回二元组，Task 1 Step 5 同步改了 `daily`/`index_daily`；
  Task 2/3 的新调用点都按二元组解包。
- `_api_error` 在 Task 1 由方法变函数，签名 `(label, body)`；`_parse`、三个解析器
  都按函数调用，无 `self.` 前缀。
- `_request` 的 `parse` 形参在 Task 1 引入，Task 3 加的 `log` 是关键字参数，
  不与 `parse` 位置冲突。
- `_lower_headers` 在 Task 3 引入、Task 4 复用，命名一致。
- `capability_checked` 的三态（`True`/`False`/`None`）在 Task 2 与 Task 3 的测试里
  都按同一含义断言。

**占位符扫描**：无 TBD/TODO；每个代码步骤都给了完整可粘贴内容。

**遗留**：spec §5 第 3 行的 `.env` 核对在本计划里只到"核对并报告"（Task 5
Step 4）。写密钥需 owner 确认，故不设写入步骤。
