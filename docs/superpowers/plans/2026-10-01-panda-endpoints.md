# Panda 嫁接 P1 · index_weight / daily_basic 端点扩展实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有 `TushareSource` 内落地 `index_weight`（按月分片快照，逐份 sha256）与 `daily_basic`（按日全市场）两个端点及探针证据链，使 CSI500/1000 成分采集与 `basic_factor` 的市值/换手率取数不再依赖离线脚本的私有通道（spec §6）。

**Architecture:** 端点全部实现在 `TushareSource`（auto transport：relay 优先、官方退路），请求词汇固定为窗口/日期，不逐 symbol 打散；proxy 一侧默认走 `query()` 的 capability 预检，具名集合默认不动。单位换算与指数代码常量只从 dated 探针 evidence 冻结。`collect_index_weight_membership.py` 的按月分片逻辑提升进端点，脚本降级为薄封装。

**Tech Stack:** Python 3.12（`/home/ji/miniconda3/envs/sq312/bin/python`）、pandas、pytest。无新依赖、无新供应商、无新凭据。

**Spec:** [2026-09-29-panda-data-loop-grafting-design.md](../specs/2026-09-29-panda-data-loop-grafting-design.md) §6 全部、§1.3、§11；路线图 [2026-10-01-panda-data-loop-grafting-implementation.md](2026-10-01-panda-data-loop-grafting-implementation.md) P1 批次（Task 6–9）。

## Global Constraints

- 解释器 `/home/ji/miniconda3/envs/sq312/bin/python`；跑**点名测试文件**，不跑裸 `pytest`（integration 全量 ≈18.5 分钟）。
- **联网一律需 owner 明确授权**（探针联网子命令与任何真实调用都不例外）；离线步骤用 stub/fixture。
- **本期零新供应商、零新凭据**；凭据零容忍：只由 `build_transport` 从环境读，不进代码、配置、日志、fixture、报告、evidence。
- 已发布数据集不可变；**门禁不得弱化**；`pipeline_contract_version` 保持 `1`。
- **探针前不得写死单位换算**（×10000/÷100 只是 §7.2 靶子假设）**与 CSI500/1000 指数代码**（panda 的 `000905.SH`/`000852.SH` 只是探针起点，非依据）；常量从 evidence 逐字回填并注明来源，禁止数字前缀猜测。
- **禁止手改 `_NAMED_ENDPOINTS` 或 capability 校验默认值绕过校验**（§6.2）；两端点默认走 proxy `query()` 预检；是否纳入具名集合由探针结论 + capability 校验决定，需 owner 裁定并记 evidence。
- 空响应绝不解释为"该日无成分/无因子"；缺失保持 null，禁止填 0；normalize 是纯函数、不做 I/O。
- 保护在途 WIP：`src/stock_quant/cli.py`、`src/stock_quant/reporting/html.py`、`templates/experiment.html.j2`、`RUNBOOK.md`、两份 spec 与四个测试文件——不覆盖、不回退、不暂存；只 `git add` 本任务文件。
- 每任务独立提交，提交信息英文，结尾 `Co-Authored-By: Claude Code <noreply@anthropic.com>`；新增 `project/*.py` 必须同步 `project/SCRIPTS.md`。
- evidence 记录日期本计划钉 **2026-10-02**；实际执行日不同时，同一次提交内同步改脚本 `RECORD_BASENAME` 与 `basic_factor_normalize` 的两个证据路径常量。

## 开工前必须知道的实现形态（已对源码核实）

1. `TushareSource.fetch`（`tushare.py:89-99`）按端点名分派，现只支持 `daily`/`index_daily`/`stock_basic`/`trade_cal`；`_fetch_trade_cal` 是"无 symbol、params 携带维度"的既有先例。
2. proxy 具名集合 `_NAMED_ENDPOINTS = ("daily", "index_daily", "stock_basic")`（`tushare_proxy.py:74`），具名读跳过 capability 预检；官方/relay 会话是 SDK `DataApi`，靠 `__getattr__` 应答任意端点名（SDK 侧 `DataApi.__getattr__ = partial(self.query, name)`；**本仓 `tushare_relay.py` 里没有 `__getattr__`**，它只重写 base URL，"先具名方法、缺失回落 `query()`" 这条链的可靠性属于探针实测项，见留白 (4)）——"先 `getattr` 具名方法、缺失回落 `query()`"一条链覆盖三种 transport。
3. `validate_supplier_frame`（`base.py:290`）分类：非 DataFrame/空响应/`frame.attrs["truncated"]`/缺 symbol 列/缺 date 列/symbol 集合不等/日期越窗；**重复主键与缺业务列它不查**，端点自检。`index_weight` 传 `symbol_columns=("index_code",)` 可让"请求的指数码 == 每行指数码"免费受检。
4. `collect_index_weight_membership.py` 按月 `client.index_weight(index_code=…, start_date=f"{ym}01", end_date=f"{ym}31")`（31 写法是生产验证过的形态，照抄）；空月写仅表头 CSV（"EMPTY (kept)"）；快照逐文件 sha256 进 `manifest.json`；脚本走 `source._client` 私有口 + `allow_auto_transport=True`。
5. proxy 单响应 **~6000 行静默截断**（`tushare_proxy.py` 模块注）；`daily_basic` 按日全市场约 5000+ 行，贴近阈值——探针必测单日行数。
6. `probe_batch_channel.py` 先例：evidence JSON 骨架 `_status: "awaiting_authorization"`，首个读数翻 `"measured"`；`RECORD_PATH` 钉日期（先例常量名是 `RECORD_PATH`，**不是** `RECORD_BASENAME`）；脚本从不读/印凭据。
7. 仓库 `tests/` **没有**引用 `collect_index_weight_membership.py` 的用例（已核实）。§6.4 末条"lineage 重建回归保持通过"由三件事兑现：（a）脚本步骤 2–6（离线转换/发布）一行不动；（b）端点 digest 与脚本逐月 CSV 字节等价的新回归；（c）点名邻居套件全绿。
8. `tests/unit/test_tushare_endpoints.py` 不存在（Task 1 新建）；`tests/integration/test_source_contracts.py` 存在但本计划未读其内容，追加以"先读再接"步骤处理（Task 2 Step 5）。

## 文件结构

| 文件 | 新/改 | 职责 |
| --- | --- | --- |
| `project/probe_index_weight_daily_basic.py` | 新 | §6.4 探针：`render` 离线渲染请求形状；`index-weight`/`daily-basic` 联网子命令（需授权） |
| `docs/operations/2026-10-02-endpoint-probe-evidence.md`（+ `.evidence.json`） | 新 | dated 探针结论；单位/指数代码/节奏冻结的唯一效力来源 |
| `src/stock_quant/data_model/basic_factor_normalize.py` | 新 | `daily_basic` → `basic_factor` 行的纯函数换算；冻结常量与证据读数 |
| `tests/unit/test_tushare_endpoints.py` | 新 | 探针形状、两端点契约、digest 字节等价、量级断言 |
| `src/stock_quant/data_sources/tushare.py` | 改 | `fetch` 分派 + `_client_read` + 两个 fetch 方法与校验器 + `INDEX_WEIGHT_INDEX_CODES` 冻结表 |
| `src/stock_quant/data_sources/tushare_proxy.py` | 改（默认零改动） | 仅当 owner 依 evidence 裁定后才把端点加入 `_NAMED_ENDPOINTS`（独立提交） |
| `project/collect_index_weight_membership.py` | 改 | 步骤 1 拉取循环替换为一次端点调用 + 逐月落盘（digest 断言等价）；删除 `--pause-seconds` |
| `project/SCRIPTS.md` | 改 | 登记 `probe_index_weight_daily_basic.py` 一行 |
| `tests/integration/test_source_contracts.py` | 改 | 先读再接：按既有 stub 形态追加两端点契约行（Task 2 Step 5） |

---

### Task 1: 探针脚本、SCRIPTS.md 登记与 evidence 骨架（联网门单列）

**Files:** Create `project/probe_index_weight_daily_basic.py`、`docs/operations/2026-10-02-endpoint-probe-evidence.md`、`...evidence.json`；Modify `project/SCRIPTS.md`；Test `tests/unit/test_tushare_endpoints.py`（新建）

**Interfaces:** Produces `DEFAULT_INDEX_CODES`、`DEFAULT_REFERENCE_SYMBOL`、`RECORD_BASENAME`、`render(args)`；evidence JSON 键 `probes.index_weight` / `probes.daily_basic`（每 transport 一节；`daily_basic` 节内 `by-day` 读数携带 `magnitude_reference`），`_status ∈ {awaiting_authorization, measured}`。Task 2/3/4 消费这些常量与节名。

- [ ] **Step 1: 写失败测试**（新建 `tests/unit/test_tushare_endpoints.py`）

```python
"""Endpoint and probe-shape tests for the P1 tushare endpoint extension."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import types
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import ContractError, DataRequest
from stock_quant.data_sources.tushare import TushareSource

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = REPO_ROOT / "project" / "probe_index_weight_daily_basic.py"


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "probe_index_weight_daily_basic", PROBE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_probe_render_is_offline_and_lists_every_request_shape(capsys):
    module = _load_probe()
    module.render(types.SimpleNamespace(
        index_codes=module.DEFAULT_INDEX_CODES,
        reference_symbol=module.DEFAULT_REFERENCE_SYMBOL))
    shapes = json.loads(capsys.readouterr().out)
    endpoints = {e["endpoint"] for group in shapes.values() for e in group}
    assert endpoints == {"index_weight", "daily_basic"}
    assert all(e["transport"] in ("relay", "proxy")
               for group in shapes.values() for e in group)
    assert "000905.SH" in json.dumps(shapes)  # probe starting point, not evidence
```

- [ ] **Step 2: 确认失败** — Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_tushare_endpoints.py -q` → Expected: FAIL（`FileNotFoundError`，探针脚本尚未创建）。

- [ ] **Step 3: 实现探针脚本 + evidence 骨架 + SCRIPTS.md**

`project/probe_index_weight_daily_basic.py`：

```python
#!/usr/bin/env python
"""Probe the tushare index_weight / daily_basic endpoints (spec §6.4).

``render`` is offline and prints every request shape the network subcommands
would issue.  ``index-weight``/``daily-basic`` hit real transports and consume
quota: NEVER run them without the owner's explicit authorization.  Conclusions
are dated evidence, not architecture facts.  Credentials are read only by
``build_transport`` from the environment; never read, printed or written here.
Exit codes: 0 completed reading, 1 call failure, 2 no transport, 3 unexpected.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import (
    AuthenticationError, ContractError, ServerError)
from stock_quant.data_sources.tushare_transport import PROXY, RELAY, build_transport

RECORD_BASENAME = "2026-10-02-endpoint-probe-evidence"
DEFAULT_EVIDENCE = Path("docs/operations") / f"{RECORD_BASENAME}.evidence.json"
#: panda's codes are probe STARTING POINTS only, never evidence (spec §6.2).
DEFAULT_INDEX_CODES = ("399300.SZ", "000905.SH", "000852.SH")
DEFAULT_REFERENCE_SYMBOL = "000001.SZ"
FIELDS = "ts_code,trade_date,total_mv,turnover_rate"
#: Bounded plan: 3 codes x (4 cadence months + 4 history probes) + 1 empty
#: shape for index_weight; by-day + by-range + 4 history for daily_basic; x2.
MAX_REQUESTS = 60


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _years(spec: str) -> list[str]:
    return [part.strip() for part in spec.split(",")]


def _transports():
    for kind in (RELAY, PROXY):
        try:
            yield kind, build_transport(kind, SourceConfig()).client
        except AuthenticationError as error:
            print(f"transport {kind}: unavailable ({error})")


def _read(client, budget: list, endpoint: str, label: str, **params):
    budget[0] -= 1
    if budget[0] < 0:
        raise SystemExit(f"request budget exhausted before {label}")
    method = getattr(client, endpoint, None)
    return method(**params) if method else client.query(endpoint, **params)


def _shape(label: str, frame: pd.DataFrame, reference: str) -> dict[str, object]:
    """One reading: rows, columns, nulls, cadence days, weight/value shape."""
    entry: dict[str, object] = {
        "label": label, "rows": int(len(frame)),
        "columns": [str(c) for c in frame.columns],
        "null_counts": {str(c): int(frame[c].isna().sum())
                        for c in frame.columns}}
    if len(frame) and "trade_date" in frame.columns:
        entry["snapshot_days"] = len({str(d) for d in frame["trade_date"]})
    if len(frame) and {"con_code", "weight"} <= set(frame.columns):
        weights = pd.to_numeric(frame["weight"], errors="coerce")
        entry["shape"] = {"con_codes": int(frame["con_code"].nunique()),
                          "weight_dtype": str(frame["weight"].dtype),
                          "weight_min": float(weights.min()),
                          "weight_max": float(weights.max())}
    if len(frame) and {"ts_code", "total_mv", "turnover_rate"} <= set(
            frame.columns):
        hit = frame[frame["ts_code"] == reference]
        if len(hit):
            entry["magnitude_reference"] = {
                "symbol": reference,
                "trade_date": str(hit.iloc[0]["trade_date"]),
                "raw_total_mv": str(hit.iloc[0]["total_mv"]),
                "raw_turnover_rate": str(hit.iloc[0]["turnover_rate"])}
    return entry


def _index_weight_calls(codes, cadence_start, cadence_months, years):
    months = [str(p).replace("-", "") for p in pd.period_range(
        cadence_start, periods=cadence_months, freq="M")]
    calls = []
    for code in codes:  # per code, not just the first
        calls += [(f"{code} cadence {m}", "index_weight",
                   {"index_code": code, "start_date": f"{m}01",
                    "end_date": f"{m}31"}) for m in months]
        calls += [(f"{code} history {y}01", "index_weight",
                   {"index_code": code, "start_date": f"{y}0101",
                    "end_date": f"{y}0131"}) for y in years]
    calls.append(("empty-shape", "index_weight",  # pre-coverage window
                  {"index_code": codes[0], "start_date": "19900101",
                   "end_date": "19901231"}))
    return calls


def _daily_basic_calls(trade_date: str, reference: str, years):
    start = (datetime.strptime(trade_date, "%Y%m%d")
             - timedelta(days=9)).strftime("%Y%m%d")
    return ([("by-day", "daily_basic",
              {"trade_date": trade_date, "fields": FIELDS}),
             ("by-range", "daily_basic",
              {"ts_code": reference, "start_date": start,
               "end_date": trade_date})]
            + [(f"history {y}01", "daily_basic",
                {"ts_code": reference, "start_date": f"{y}0101",
                 "end_date": f"{y}0131"}) for y in years])


def render(args: argparse.Namespace) -> None:
    shapes = {"index_weight": [
        {"transport": kind, "endpoint": "index_weight",
         "params": {"index_code": code, "start_date": "YYYYMM01",
                    "end_date": "YYYYMM31"}}
        for kind in (RELAY, PROXY) for code in args.index_codes]}
    shapes["daily_basic"] = [
        {"transport": kind, "endpoint": "daily_basic", "params": params}
        for kind in (RELAY, PROXY)
        for params in ({"trade_date": "YYYYMMDD", "fields": FIELDS},
                       {"ts_code": args.reference_symbol,
                        "start_date": "YYYYMMDD", "end_date": "YYYYMMDD"})]
    print(json.dumps(shapes, indent=1, sort_keys=True))


def _first_client():
    for _kind, client in _transports():
        return client
    raise SystemExit("no transport configured; cannot probe")


def _run_probe(section, calls, evidence: Path | None, reference: str) -> None:
    budget = [MAX_REQUESTS]
    findings: dict[str, object] = {}
    for kind, client in _transports():
        readings = []
        for label, endpoint, params in calls:
            try:
                frame = _read(client, budget, endpoint,
                              f"{kind} {label}", **params)
            except (ServerError, ContractError) as error:
                readings.append({"label": label,
                                 "error": type(error).__name__})
                continue
            readings.append(_shape(label, frame, reference))
        findings[kind] = {"observed_at_utc": _utc_now(), "readings": readings}
    print(json.dumps(findings, ensure_ascii=False, indent=1, sort_keys=True))
    if not any(entry["readings"] for entry in findings.values()):
        raise SystemExit("no transport produced a reading; refusing to write "
                         "evidence or flip _status to 'measured'")
    if evidence is not None:
        _write_evidence(evidence, section, findings)


def _daily_bar_diff(root: Path, trade_date: str, ts_codes: set[str]) -> object:
    from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader

    version = DatasetPublisher(root).current().version
    with DatasetReader(root).open(version) as dataset:
        if "daily_bar" not in dataset.tables:
            return {"error": "no daily_bar table"}
        bar = dataset.read("daily_bar")
    day = bar[pd.to_datetime(bar["trade_date"]).dt.strftime("%Y%m%d") == trade_date]
    symbols = {str(s) for s in day["symbol"]}
    return {"daily_bar_symbols": len(symbols),
            "daily_basic_symbols": len(ts_codes),
            "only_in_daily_bar": sorted(symbols - ts_codes)[:20],
            "only_in_daily_basic": sorted(ts_codes - symbols)[:20]}


def _write_evidence(path: Path, section: str, payload: dict[str, object]) -> None:
    if path.exists():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SystemExit(f"evidence file {path} is not valid JSON ({error})")
        if not isinstance(document.get("probes"), dict):
            raise SystemExit(f"evidence file {path} is not probe-evidence shaped")
    else:
        document = {"record": f"docs/operations/{RECORD_BASENAME}.md",
                    "probes": {}}
    document["probes"][section] = payload
    if any(entry.get("readings")
           for entry in document["probes"].values()
           if isinstance(entry, dict)):
        document["_status"] = "measured"
    document["last_updated_utc"] = _utc_now()
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(f"evidence: section {section!r} written to {path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="probe_index_weight_daily_basic.py",
        description=("Probe the tushare index_weight / daily_basic endpoints "
                     "(spec §6.4). The index-weight and daily-basic subcommands "
                     "hit real transports and consume quota -- never run them "
                     "without the owner's explicit authorization."))
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    sub = parser.add_subparsers(dest="probe", required=True)
    for name in ("render", "index-weight", "daily-basic"):
        probe = sub.add_parser(name)
        probe.add_argument("--index-codes", default=",".join(DEFAULT_INDEX_CODES))
        probe.add_argument("--reference-symbol", default=DEFAULT_REFERENCE_SYMBOL)
        probe.add_argument("--cadence-start", default="202604")
        probe.add_argument("--cadence-months", type=int, default=4)
        probe.add_argument("--history-years", default="2005,2010,2015,2020")
    sub.choices["daily-basic"].add_argument(
        "--trade-date", type=date.fromisoformat, required=True)
    sub.choices["daily-basic"].add_argument(
        "--root", type=Path, default=None, help="project root, for the daily_bar diff")
    args = parser.parse_args(argv)
    args.index_codes = tuple(c.strip() for c in args.index_codes.split(","))
    years = _years(args.history_years)
    try:
        if args.probe == "render":
            render(args)
        elif args.probe == "index-weight":
            _run_probe("index_weight", _index_weight_calls(
                args.index_codes, args.cadence_start,
                args.cadence_months, years), args.evidence,
                args.reference_symbol)
        else:
            trade_date = args.trade_date.strftime("%Y%m%d")
            _run_probe("daily_basic", _daily_basic_calls(
                trade_date, args.reference_symbol, years), args.evidence,
                args.reference_symbol)
            if args.root is not None:  # symbol-set diff vs the published bar
                by_code = _read(_first_client(), [MAX_REQUESTS],
                                "daily_basic", "diff by-day",
                                trade_date=trade_date, fields="ts_code")
                _write_evidence(args.evidence, "daily_basic_bar_diff", {
                    "trade_date": trade_date,
                    "diff": _daily_bar_diff(
                        args.root, trade_date,
                        {str(c) for c in by_code["ts_code"]})})
    except AuthenticationError as error:
        print(f"no usable transport: {error}")
        return 2
    except (ServerError, ContractError) as error:
        print(f"supplier call failed: {type(error).__name__}: {error}")
        return 1
    except Exception as error:  # noqa: BLE001 - report, never traceback-leak
        print(f"unexpected failure ({type(error).__name__}): {error}")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`docs/operations/2026-10-02-endpoint-probe-evidence.evidence.json` 骨架：

```json
{
  "record": "docs/operations/2026-10-02-endpoint-probe-evidence.md",
  "_status": "awaiting_authorization",
  "probes": {}
}
```

evidence md 骨架（`2026-10-02-endpoint-probe-evidence.md`）：日期、状态 `awaiting_authorization`、脚本路径、结论载体说明（`.evidence.json`，`_status` 翻 `measured` 后方可在实现中回填），外加待回填小节清单——index_weight：可用性（relay/proxy × 三指数码）、节奏判定（不稳定记 `unknown`，§6.4）、历史窗口起点、单快照行数与权重列形态、空响应形态、两 transport 差异与配额；daily_basic：历史起点、按日/按区间两种取法、`total_mv`/`turnover_rate` 原生单位与空值形态、同日 symbol 集合与 `daily_bar` 差异、单日行数 vs ~6000 截断阈值、配额与失败形态、可得性时点观测（T 日数据当日何时可查，按 `observed_at_utc` 记录）；末节：单位冻结值与量级参考读数（relay 优先）及具名端点集合裁定。

`project/SCRIPTS.md` 在 `probe_expansion_gap_risk` 行后插入：

```markdown
| `project/probe_index_weight_daily_basic.py` | diagnostic | 探测 index_weight/daily_basic 端点契约（联网子命令需 owner 授权） | 无替代项 |
```

- [ ] **Step 4: 确认通过 + 离线 dry-run + 治理校验** — Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_tushare_endpoints.py tests/unit/test_context_governance_docs.py -q && /home/ji/miniconda3/envs/sq312/bin/python project/probe_index_weight_daily_basic.py render | head -30` → Expected: PASS；render 打印 relay/proxy × 两端点的请求形状（无网络）。

- [ ] **Step 5: 提交**

```bash
git add project/probe_index_weight_daily_basic.py docs/operations/2026-10-02-endpoint-probe-evidence.md docs/operations/2026-10-02-endpoint-probe-evidence.evidence.json project/SCRIPTS.md tests/unit/test_tushare_endpoints.py
git commit -m "feat(probe): add the index_weight/daily_basic endpoint probe with offline render" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

**联网门（阻塞 Task 2/3 的冻结提交与 Task 4 全部；照路线图惯例单列）**：取得 owner 书面授权（日期窗口、指数代码、请求数上限成文）后运行 `/home/ji/miniconda3/envs/sq312/bin/python project/probe_index_weight_daily_basic.py index-weight` 与 `... daily-basic --trade-date <最近交易日> --root project`；把结论（节奏可判定性——不稳定记 `unknown`、可得性时点观测、单位与空值形态、两 transport 差异与配额、单日行数 vs 截断阈值）补写进 evidence md 并提交（`docs(probe): record endpoint probe readings under owner authorization`）。

---

### Task 2: `daily_basic` 端点（契约测试先行；单位常量从 evidence 回填）

**Files:** Modify `src/stock_quant/data_sources/tushare.py`；Create `src/stock_quant/data_model/basic_factor_normalize.py`；Test `tests/unit/test_tushare_endpoints.py`（追加）

**Interfaces:** Consumes Task 1 evidence 的 `probes.daily_basic` 节。Produces：`DataRequest(endpoint="daily_basic", symbols=(), start_date=d, end_date=d, params={})` → 原样供应商帧（`ts_code/trade_date/total_mv/turnover_rate`，null 保持 null）；`TushareSource._client_read(endpoint, **params)`（Task 3 复用）；`daily_basic_to_basic_factor_rows(frame)` 与 `TOTAL_MV_TO_YUAN_MULTIPLIER`/`TURNOVER_RATE_TO_RATIO_DIVISOR`/`PROBE_EVIDENCE_RECORD`/`PROBE_EVIDENCE_JSON`（Task 4 依赖）。

- [ ] **Step 1: 写失败测试**（追加）

```python
class StubClient:
    sdk_version = "stub-1"

    def __init__(self, frame):
        self.frame, self.calls = frame, []

    def daily_basic(self, **params):
        self.calls.append(params)
        return self.frame


class ProxyShapeStub:
    sdk_version = "stub-proxy-1"

    def __init__(self, frame):
        self.frame, self.calls = frame, []

    def query(self, endpoint, **params):
        self.calls.append((endpoint, params))
        return self.frame


def _daily_basic_frame(rows):
    return pd.DataFrame(rows, columns=["ts_code", "trade_date",
                                       "total_mv", "turnover_rate"])


def _truncated(frame):
    frame.attrs["truncated"] = True
    return frame


def _daily_basic_request(day=date(2026, 9, 1)):
    return DataRequest("daily_basic", (), day, day, {})


def test_daily_basic_passes_the_raw_frame_through_without_zero_fill():
    frame = _daily_basic_frame([["000001.SZ", "20260901", "372000.0", "0.53"],
                                ["600000.SH", "20260901", None, None]])
    stub = StubClient(frame)
    result = TushareSource(SourceConfig(), client=stub).fetch(_daily_basic_request())
    assert stub.calls == [{"trade_date": "20260901",
                           "fields": "ts_code,trade_date,total_mv,turnover_rate"}]
    assert result.frame is frame  # raw evidence; no normalization, no zero fill
    assert result.frame["total_mv"].isna().tolist() == [False, True]
    assert result.metadata["supplier_endpoint"] == "tushare.pro.daily_basic"


def test_daily_basic_falls_back_to_the_proxys_verified_query():
    stub = ProxyShapeStub(_daily_basic_frame([["000001.SZ", "20260901", "1", "0.5"]]))
    TushareSource(SourceConfig(), client=stub).fetch(_daily_basic_request())
    assert stub.calls == [("daily_basic", {"trade_date": "20260901",
                           "fields": "ts_code,trade_date,total_mv,turnover_rate"})]


@pytest.mark.parametrize("frame,match", [
    (_daily_basic_frame([]), "empty response"),
    (pd.DataFrame([["000001.SZ", "1.0", "0.5"]],
                  columns=["ts_code", "total_mv", "turnover_rate"]),
     "missing columns: trade_date"),  # 缺日期键
    (pd.DataFrame([["000001.SZ", "20260901", "0.5"]],
                  columns=["ts_code", "trade_date", "turnover_rate"]),
     "missing columns: total_mv"),  # schema drift: value column renamed away
    (_daily_basic_frame([["000001.SZ", "20260901", "1", "0.5"],
                         ["000001.SZ", "20260901", "1", "0.5"]]),
     "duplicate primary-key"),
    (_truncated(_daily_basic_frame([["000001.SZ", "20260901", "1", "0.5"]])),
     "truncated"),
    (_daily_basic_frame([["000001.SZ", "20260910", "1", "0.5"]]),
     "outside the requested date range"),
])
def test_daily_basic_classifies_contract_failures(frame, match):
    with pytest.raises(ContractError, match=match):
        TushareSource(SourceConfig(), client=StubClient(frame)).fetch(
            _daily_basic_request())


def test_daily_basic_rejects_symbol_scoped_and_multi_day_windows():
    source = TushareSource(
        SourceConfig(), client=StubClient(
            _daily_basic_frame([["000001.SZ", "20260901", "1", "0.5"]])))
    with pytest.raises(ValueError, match="whole-market per-day"):
        source.fetch(DataRequest("daily_basic", ("000001.SZ",),
                                 date(2026, 9, 1), date(2026, 9, 1), {}))
    with pytest.raises(ValueError, match="exactly one trade_date"):
        source.fetch(DataRequest("daily_basic", (), date(2026, 9, 1),
                                 date(2026, 9, 2), {}))


def test_daily_basic_conversion_uses_the_frozen_constants_and_keeps_nulls():
    from stock_quant.data_model.basic_factor_normalize import (
        TOTAL_MV_TO_YUAN_MULTIPLIER, TURNOVER_RATE_TO_RATIO_DIVISOR,
        daily_basic_to_basic_factor_rows)
    rows = daily_basic_to_basic_factor_rows(_daily_basic_frame(
        [["000001.SZ", "20260901", "372000.0", "0.53"],
         ["600000.SH", "20260901", None, None]]))
    assert rows["market_cap"].tolist()[0] == pytest.approx(
        372000.0 * TOTAL_MV_TO_YUAN_MULTIPLIER)
    assert rows["turnover_rate"].tolist()[0] == pytest.approx(
        0.53 / TURNOVER_RATE_TO_RATIO_DIVISOR)
    assert rows["market_cap"].isna().tolist() == [False, True]  # never 0
```

- [ ] **Step 2: 确认失败** — Run: `... -m pytest tests/unit/test_tushare_endpoints.py -q -k daily_basic` → Expected: FAIL——端点用例 `ValueError: TushareSource supports only ...`；换算用例 `ImportError`（`basic_factor_normalize` 不存在）。

- [ ] **Step 3: 实现**

`tushare.py`：import 区加 `import hashlib`、`import json`；`fetch` 在 `daily/index_daily` 分支后加分派行（`index_weight` 分派行属 Task 3），兜底 `ValueError` 文案本任务只列到 `trade_cal and daily_basic`，**到 Task 3 落地 `index_weight` 分派行时再把 `index_weight` 加进这段文案**——Task 2 提交后、Task 3 提交前，该文案不能宣称一个尚未实现、调用即抛兜底异常的名字：

```python
        if request.endpoint == "daily_basic":
            return self._fetch_daily_basic(request)
```

模块级常量（官方/relay 的 SDK `__getattr__` 应答任意名；proxy 无具名方法时回落 `query()`，capability 预检就在那里跑——具名集合与校验默认值从不手改，§6.2）：

```python
_DAILY_BASIC_FIELDS = "ts_code,trade_date,total_mv,turnover_rate"
_DAILY_BASIC_COLUMNS = ("ts_code", "trade_date", "total_mv", "turnover_rate")
```

类内追加：

```python
    def _client_read(self, endpoint: str, **params: object) -> pd.DataFrame:
        """Named method when the transport has one, else the proxy's query()."""
        method = getattr(self._client, endpoint, None)
        if method is not None:
            return method(**params)
        query = getattr(self._client, "query", None)
        if query is None:
            raise ValueError(
                f"tushare transport {self._transport.transport_id} has no "
                f"{endpoint} endpoint")
        return query(endpoint, **params)

    def _fetch_daily_basic(self, request: DataRequest) -> FetchResult:
        """One whole-market daily_basic snapshot for a single trade date.

        Per-day vocabulary (spec §6.2): ``symbols`` empty, window exactly one
        day, callers paginate by day.  Only identity + total_mv/turnover_rate
        are requested -- amount/OHLCV are ``daily_bar`` facts (spec §6.1).
        """
        if request.symbols:
            raise ValueError("Tushare daily_basic is a whole-market per-day "
                             "request, not a symbol-scoped query")
        if request.start_date != request.end_date:
            raise ValueError("Tushare daily_basic requests exactly one "
                             "trade_date (start_date must equal end_date)")
        request_timestamp = _utc_timestamp()
        try:
            frame = self._client_read(
                "daily_basic",
                trade_date=request.start_date.strftime("%Y%m%d"),
                fields=_DAILY_BASIC_FIELDS)
        except Exception as error:
            translated = translate_supplier_error(error)
            if translated is error:
                raise
            raise translated from None
        self._validate_daily_basic(frame, request)
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=frame,
            metadata=request_metadata(
                request, self._supplier_endpoint(request.endpoint),
                self._sdk_version,
                transport_id=self._transport.transport_id,
                request_timestamp=request_timestamp,
                response_timestamp=_utc_timestamp()))

    @staticmethod
    def _validate_daily_basic(frame: pd.DataFrame, request: DataRequest) -> None:
        """Classify the daily_basic contract; an empty day is never success."""
        if not isinstance(frame, pd.DataFrame):
            raise ContractError("supplier response is not a pandas DataFrame")
        missing = [c for c in _DAILY_BASIC_COLUMNS if c not in frame.columns]
        if missing:
            raise ContractError("supplier daily_basic response is missing "
                                "columns: " + ", ".join(missing))
        duplicated = frame.duplicated(subset=["ts_code", "trade_date"]).sum()
        if duplicated:
            raise ContractError(
                f"supplier daily_basic response has {int(duplicated)} "
                "duplicate primary-key rows")
        validate_supplier_frame(frame, request, symbol_columns=("ts_code",),
                                date_columns=("trade_date",),
                                require_symbol=False)
```

新建 `src/stock_quant/data_model/basic_factor_normalize.py`（**回填门**：常量在 evidence `_status == "measured"` 后才可提交；下列数值是 §7.2 靶子假设，与 evidence 实测不符时以实测为准并同步 evidence md；探针未完成则停在本步，不得提交）：

```python
"""daily_basic -> basic_factor rows (P1: units frozen from probe evidence)."""

from __future__ import annotations

import pandas as pd

#: Probe evidence (docs/operations/2026-10-02-endpoint-probe-evidence.md,
#: §daily_basic): total_mv arrives in 万元 and turnover_rate in percent --
#: values below are the measured confirmation, cited verbatim from the
#: record; never guessed from column names (spec §6.2/§7.2).
TOTAL_MV_TO_YUAN_MULTIPLIER = 10_000.0
TURNOVER_RATE_TO_RATIO_DIVISOR = 100.0
PROBE_EVIDENCE_RECORD = "docs/operations/2026-10-02-endpoint-probe-evidence.md"
PROBE_EVIDENCE_JSON = (
    "docs/operations/2026-10-02-endpoint-probe-evidence.evidence.json")


def daily_basic_to_basic_factor_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert raw daily_basic rows to basic_factor business columns.

    Nulls stay null (never 0); no bar columns are copied (spec §6.1/§7.2).
    """
    return pd.DataFrame({
        "trade_date": pd.to_datetime(
            frame["trade_date"], format="%Y%m%d", errors="raise").dt.date,
        "symbol": frame["ts_code"].astype(str),
        "market_cap": pd.to_numeric(frame["total_mv"], errors="raise")
        * TOTAL_MV_TO_YUAN_MULTIPLIER,
        "turnover_rate": pd.to_numeric(frame["turnover_rate"], errors="raise")
        / TURNOVER_RATE_TO_RATIO_DIVISOR})
```

**具名端点集合裁定（门）**：默认不动 `_NAMED_ENDPOINTS`（proxy 走 `query()` 预检）。仅当 evidence 显示 capability 元数据与实测矛盾、且 owner 明确裁定时，才独立提交把 `"daily_basic"`（及 Task 3 的 `"index_weight"`）加入 `_NAMED_ENDPOINTS` 并在 evidence 记录依据；禁止改 `_verify_capability` 或其默认值绕过。

- [ ] **Step 4: 确认通过 + 点名邻居** — Run: `... -m pytest tests/unit/test_tushare_endpoints.py tests/unit/test_tushare_proxy.py tests/unit/test_tushare_proxy_capabilities.py tests/unit/test_tushare_relay.py -q` → Expected: PASS（`_NAMED_ENDPOINTS` 默认不动，proxy 邻居不受影响）。

- [ ] **Step 5: 先读再接 `test_source_contracts.py`，然后提交** — 先读 `tests/integration/test_source_contracts.py` 的既有 stub/fixture 形态：若容纳"stub client + DataRequest → FetchResult 断言"，照其手法追加 `daily_basic` 契约行（断言与单测一致，不新增断言类型）；若不容纳，向 owner 报告并保持单测覆盖，不强改。

```bash
git add src/stock_quant/data_sources/tushare.py src/stock_quant/data_model/basic_factor_normalize.py tests/unit/test_tushare_endpoints.py tests/integration/test_source_contracts.py
git commit -m "feat(endpoints): add the daily_basic endpoint with fail-closed contract checks" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: `index_weight` 端点（提升既有脚本；脚本降级薄封装）

**Files:** Modify `src/stock_quant/data_sources/tushare.py`、`project/collect_index_weight_membership.py`；Test `tests/unit/test_tushare_endpoints.py`（追加）

**Interfaces:** Consumes `_client_read`（Task 2）与 Task 1 evidence 的指数代码结论。Produces：`DataRequest(endpoint="index_weight", symbols=(index_code,), start_date, end_date)` → 全窗拼接帧 + `metadata["index_weight_snapshots"] = json.dumps([{"month","rows","sha256"}, ...])`；`INDEX_WEIGHT_INDEX_CODES` 模块常量（薄封装与 P2b/P2c 依赖）。sha256 = 每月帧 `to_csv(index=False)` 的 UTF-8 字节摘要，与脚本逐月 CSV 字节等价。

- [ ] **Step 1: 写失败测试**（追加）

```python
class IndexWeightStub:
    sdk_version = "stub-1"

    def __init__(self, frames):
        self._frames, self.calls = list(frames), []

    def index_weight(self, **params):
        self.calls.append(params)
        return self._frames.pop(0)


def _weight_rows(rows):
    return pd.DataFrame(rows, columns=["index_code", "con_code",
                                       "trade_date", "weight"])


def _weight_request(end=date(2026, 2, 28)):
    return DataRequest("index_weight", ("399300.SZ",), date(2026, 1, 1), end, {})


def test_index_weight_shards_by_calendar_month_and_digests_each_snapshot():
    january = _weight_rows([["399300.SZ", "000001.SZ", "20260130", "0.32"]])
    stub = IndexWeightStub([
        january,
        _weight_rows([["399300.SZ", "000001.SZ", "20260227", "0.33"]])])
    result = TushareSource(SourceConfig(), client=stub).fetch(_weight_request())
    assert stub.calls == [
        {"index_code": "399300.SZ", "start_date": "20260101",
         "end_date": "20260131"},
        {"index_code": "399300.SZ", "start_date": "20260201",
         "end_date": "20260231"}]
    assert len(result.frame) == 2
    digests = json.loads(result.metadata["index_weight_snapshots"])
    assert [d["month"] for d in digests] == ["202601", "202602"]
    assert digests[0]["sha256"] == hashlib.sha256(
        january.to_csv(index=False).encode("utf-8")).hexdigest()


def test_an_empty_month_is_recorded_not_interpreted():
    january = _weight_rows([])  # empty month WITH columns: kept, rows=0
    stub = IndexWeightStub([
        january, _weight_rows([["399300.SZ", "000001.SZ", "20260227", "0.33"]])])
    result = TushareSource(SourceConfig(), client=stub).fetch(_weight_request())
    digests = json.loads(result.metadata["index_weight_snapshots"])
    assert digests[0]["rows"] == 0
    assert digests[0]["sha256"] == hashlib.sha256(
        january.to_csv(index=False).encode("utf-8")).hexdigest()
    assert len(result.frame) == 1  # no membership invented for the empty month


def test_an_all_empty_window_is_a_contract_failure():
    stub = IndexWeightStub([_weight_rows([]), _weight_rows([])])
    with pytest.raises(ContractError, match="empty response"):
        TushareSource(SourceConfig(), client=stub).fetch(_weight_request())


def test_index_weight_rejects_codes_outside_the_probe_frozen_table():
    request = DataRequest("index_weight", ("999999.SZ",),
                          date(2026, 1, 1), date(2026, 1, 31), {})
    with pytest.raises(ValueError, match="probe-frozen index table"):
        TushareSource(SourceConfig(), client=IndexWeightStub([])).fetch(request)


@pytest.mark.parametrize("january,match", [
    (_weight_rows([["399300.SZ", "000001.SZ", "20260130", "0.3"],
                   ["399300.SZ", "000001.SZ", "20260130", "0.3"]]),
     "duplicate primary-key"),
    (_weight_rows([["399300.SZ", "000001.SZ", "20260302", "0.3"]]),
     "outside the requested date range"),
    (pd.DataFrame([["000001.SZ", "20260130", "0.3"]],
                  columns=["con_code", "trade_date", "weight"]),
     "missing columns: index_code"),  # 缺指数
])
def test_index_weight_classifies_contract_failures(january, match):
    request = DataRequest("index_weight", ("399300.SZ",),
                          date(2026, 1, 1), date(2026, 1, 31), {})
    with pytest.raises(ContractError, match=match):
        TushareSource(SourceConfig(),
                      client=IndexWeightStub([january])).fetch(request)
```

- [ ] **Step 2: 确认失败** — Run: `... -m pytest tests/unit/test_tushare_endpoints.py -q -k "index_weight or empty_month or empty_window or frozen_table"` → Expected: FAIL——`ValueError: TushareSource supports only ...`（分派未接通）。

- [ ] **Step 3: 实现端点 + 薄封装**

`tushare.py` 模块级（**冻结门**：`000905.SH`/`000852.SH` 两项由 evidence 确认后才可提交；`399300.SZ` 有 766 行 lineage 直接佐证；evidence 未 measured 时冻结表只含 csi300 一项，CSI500/1000 待探针后追加）：

```python
#: Index codes the index_weight endpoint may be asked for, frozen from the
#: §6.4 probe evidence (docs/operations/2026-10-02-endpoint-probe-evidence.md):
#: 399300.SZ is lineage-proven (766-row custom_csi300_tw_tradable); the
#: CSI500/1000 entries are the probe-confirmed codes -- never guessed from
#: digit prefixes (spec §6.2).
INDEX_WEIGHT_INDEX_CODES = ("399300.SZ", "000905.SH", "000852.SH")
_INDEX_WEIGHT_COLUMNS = ("index_code", "con_code", "trade_date", "weight")


def _month_shards(start: date, end: date) -> list[tuple[str, str, str]]:
    """(YYYYMM, YYYYMM01, YYYYMM31) month shards, lineage-shaped."""
    shards = []
    cursor = pd.Period(start.strftime("%Y%m"), freq="M")
    last = pd.Period(end.strftime("%Y%m"), freq="M")
    while cursor <= last:
        yearmonth = str(cursor).replace("-", "")
        shards.append((yearmonth, f"{yearmonth}01", f"{yearmonth}31"))
        cursor += 1
    return shards
```

（模块已有 `from __future__ import annotations`，`date` 注解不求值，无需新增 import。）`fetch` 加 `index_weight` 分派行；类内追加：

```python
    def _fetch_index_weight(self, request: DataRequest) -> FetchResult:
        """Month-sharded index_weight snapshots, one sha256 per month.

        Sharding mirrors the lineage script's proven shape (spec §6.2).  An
        empty month is recorded as a rows=0 shard, never interpreted as "no
        constituents that day"; an all-empty window fails the contract.
        """
        if len(request.symbols) != 1:
            raise ValueError("Tushare index_weight requests require exactly "
                             "one index code")
        index_code = request.symbols[0]
        if index_code not in INDEX_WEIGHT_INDEX_CODES:
            raise ValueError(
                f"index code {index_code!r} is not in the probe-frozen index "
                f"table ({', '.join(INDEX_WEIGHT_INDEX_CODES)}); extend the "
                "table from probe evidence, never by guessing")
        request_timestamp = _utc_timestamp()
        frames: list[pd.DataFrame] = []
        digests: list[dict[str, object]] = []
        for yearmonth, month_start, month_end in _month_shards(
                request.start_date, request.end_date):
            try:
                frame = self._client_read(
                    "index_weight", index_code=index_code,
                    start_date=month_start, end_date=month_end)
            except Exception as error:
                translated = translate_supplier_error(error)
                if translated is error:
                    raise
                raise translated from None
            period = pd.Period(yearmonth, freq="M")
            frame = self._empty_month_as_shard(frame)
            self._validate_index_weight(frame, DataRequest(
                request.endpoint, request.symbols,
                max(period.start_time.date(), request.start_date),
                min(period.end_time.date(), request.end_date),
                dict(request.params)))
            frames.append(frame)
            digests.append({
                "month": yearmonth, "rows": int(len(frame)),
                "sha256": hashlib.sha256(
                    frame.to_csv(index=False).encode("utf-8")).hexdigest()})
        combined = pd.concat(frames, ignore_index=True)
        validate_supplier_frame(  # all-empty windows fail here
            combined, request, symbol_columns=("index_code",),
            date_columns=("trade_date",))
        metadata = request_metadata(
            request, self._supplier_endpoint("index_weight"),
            self._sdk_version, transport_id=self._transport.transport_id,
            request_timestamp=request_timestamp,
            response_timestamp=_utc_timestamp())
        metadata["index_weight_snapshots"] = json.dumps(digests, sort_keys=True)
        return FetchResult(source=self.name, endpoint=request.endpoint,
                           request_key=request_key(request), frame=combined,
                           metadata=metadata)

    @staticmethod
    def _empty_month_as_shard(frame: pd.DataFrame | None) -> pd.DataFrame:
        """Absorb the supplier's empty-month shapes exactly as the lineage script did.

        ``collect_index_weight_membership.py:133-137`` substituted an empty
        4-column frame whenever a month's response was ``None`` or a
        column-less empty frame -- the shape a month predating the index's
        coverage comes back as.  Keeping that rule here makes such a month a
        recorded rows=0 shard instead of failing the whole window; a
        columns-bearing empty frame already passes ``_validate_index_weight``.
        """
        if frame is None or (frame.empty and not len(frame.columns)):
            return pd.DataFrame(columns=list(_INDEX_WEIGHT_COLUMNS))
        return frame

    @staticmethod
    def _validate_index_weight(frame: pd.DataFrame, request: DataRequest) -> None:
        """Per-month shape; empty months pass through as recorded evidence."""
        if not isinstance(frame, pd.DataFrame):
            raise ContractError("supplier response is not a pandas DataFrame")
        missing = [c for c in _INDEX_WEIGHT_COLUMNS if c not in frame.columns]
        if missing:
            raise ContractError("supplier index_weight response is missing "
                                "columns: " + ", ".join(missing))
        if frame.empty:
            return
        duplicated = frame.duplicated(subset=["con_code", "trade_date"]).sum()
        if duplicated:
            raise ContractError(
                f"supplier index_weight response has {int(duplicated)} "
                "duplicate primary-key rows")
        validate_supplier_frame(frame, request, symbol_columns=("index_code",),
                                date_columns=("trade_date",))
```

`project/collect_index_weight_membership.py`：删 `import time` 与 `--pause-seconds`（argparse、`run` 形参、调用点），import 区加 `from stock_quant.data_sources.base import DataRequest`。

**替换范围是 `token = os.environ.get("TUSHARE_TOKEN")`（:106）一直到 `time.sleep(pause_seconds)`（:142），整段替换为下面这段。** 只从 `source = TushareSource(...)`（:120）开始替换会留下三处不配套的东西：（a）:106-108 的 token 门——纯 relay 环境在这里就 `SystemExit`，下面新写的 relay-pair 检查永远执行不到；（b）:116-119 那段"dropping the `TUSHARE_TOKEN` gate … is design spec §4, stage 4 -- not this change"的旧注释，与新代码直接矛盾；（c）:112 的 `months = _month_ends(start, end)`——替换后无人使用，成 F841。替换片段自带 `if not skip_pull:`，正好替掉 :115 那一行，不要与它形成嵌套重复。


```python
    if not skip_pull:
        if not (os.environ.get("TUSHARE_TOKEN")
                or (os.environ.get("TUSHARE_RELAY_URL")
                    and os.environ.get("TUSHARE_RELAY_KEY"))):
            raise SystemExit("a tushare transport is required for the pull "
                             "(relay pair or TUSHARE_TOKEN)")
        # Thin wrapper: monthly sharding, per-month validation and per-month
        # sha256 digests now live in the TushareSource endpoint (spec §6.2).
        source = TushareSource(config.sources["tushare"],
                               allow_auto_transport=True)
        request = DataRequest(
            endpoint="index_weight", symbols=(index_code,),
            start_date=_month_end_date(start).replace(day=1),
            end_date=_month_end_date(end))
        result = source.fetch(request)
        for shard in json.loads(result.metadata["index_weight_snapshots"]):
            month = str(shard["month"])
            target = snapshot_dir / f"{index_code}_{month}.csv"
            if target.exists():
                continue
            month_frame = result.frame[
                result.frame["trade_date"].astype(str).str.startswith(month)]
            month_frame.to_csv(target, index=False)
            assert _sha256_file(target) == shard["sha256"], (
                f"endpoint digest mismatch for {target.name}")
            print(f"[{month}] {shard['rows']} rows "
                  f"sha256={shard['sha256'][:12]}…")
```

（digest 断言即 lineage 重建的复现性回归：端点摘要与脚本逐月 CSV 字节等价。全窗皆空时旧路径"逐月写空文件、步骤 3 才失败"，现在在端点处以明确 `ContractError` 提前失败——收紧而非放宽。）

- [ ] **Step 4: 确认通过 + 邻居回归** — Run: `... -m pytest tests/unit/test_tushare_endpoints.py tests/unit/test_tushare_proxy.py tests/unit/test_tushare_proxy_capabilities.py tests/unit/test_tushare_relay.py -q` → Expected: PASS。lineage 重建回归按开工前 #7 三件事兑现（脚本步骤 2–6 未动、digest 字节等价用例、邻居全绿）。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/data_sources/tushare.py project/collect_index_weight_membership.py tests/unit/test_tushare_endpoints.py
git commit -m "feat(endpoints): promote index_weight month sharding into TushareSource with snapshot digests" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: 量级断言测试（素材取自探针 evidence）

**Files:** Modify `src/stock_quant/data_model/basic_factor_normalize.py`；Test `tests/unit/test_tushare_endpoints.py`（追加）

**Interfaces:** Consumes Task 2 的换算函数与 `PROBE_EVIDENCE_JSON`、Task 1 联网门的 `probes.daily_basic.relay` 读数中 label 为 `by-day` 的 `magnitude_reference`。Produces `EVIDENCE_REFERENCE_READINGS`（P2c 的 `basic_factor` 契约测试复用的冻结读数）。

- [ ] **Step 1: 写失败测试**（追加）

```python
def _relay_by_day_reference() -> dict:
    from stock_quant.data_model.basic_factor_normalize import PROBE_EVIDENCE_JSON
    document = json.loads(
        (REPO_ROOT / PROBE_EVIDENCE_JSON).read_text(encoding="utf-8"))
    assert document["_status"] == "measured", "probe evidence not measured yet"
    readings = [reading for reading in
                document["probes"]["daily_basic"]["relay"]["readings"]
                if "magnitude_reference" in reading]
    assert readings, "relay by-day reading carries no magnitude_reference"
    return readings[0]["magnitude_reference"]


def test_frozen_readings_match_the_measured_evidence():
    from stock_quant.data_model.basic_factor_normalize import (
        EVIDENCE_REFERENCE_READINGS)
    reference = _relay_by_day_reference()
    assert [dict(r) for r in EVIDENCE_REFERENCE_READINGS] == [{
        "symbol": reference["symbol"], "trade_date": reference["trade_date"],
        "raw_total_mv": reference["raw_total_mv"],
        "raw_turnover_rate": reference["raw_turnover_rate"]}]


def test_magnitude_reference_converts_into_plausible_bands():
    from stock_quant.data_model.basic_factor_normalize import (
        EVIDENCE_REFERENCE_READINGS, daily_basic_to_basic_factor_rows)
    assert EVIDENCE_REFERENCE_READINGS
    rows = daily_basic_to_basic_factor_rows(pd.DataFrame(
        [[r["symbol"], r["trade_date"],
          r["raw_total_mv"], r["raw_turnover_rate"]]
         for r in EVIDENCE_REFERENCE_READINGS],
        columns=["ts_code", "trade_date", "total_mv", "turnover_rate"]))
    for value in rows["market_cap"]:  # yuan, large-cap order of magnitude
        assert 1e10 <= value <= 1e12
    for value in rows["turnover_rate"]:  # dimensionless ratio, not percent
        assert 1e-5 <= value <= 0.2
```

- [ ] **Step 2: 确认失败** — Run: `... -m pytest tests/unit/test_tushare_endpoints.py -q -k "frozen_readings or magnitude_reference"` → Expected: FAIL——`ImportError: cannot import name 'EVIDENCE_REFERENCE_READINGS'`。

- [ ] **Step 3: 实现冻结读数（回填门）** — 读 `docs/operations/2026-10-02-endpoint-probe-evidence.evidence.json` 的 `probes.daily_basic.relay.readings` 中 `by-day` 读数的 `magnitude_reference`，四个字段值逐字抄录（保持字符串形态；relay 不可用时停在门上报告 owner，不得改用 proxy 读数冒充）；在 `basic_factor_normalize.py` 追加：

```python
#: Known-security readings copied VERBATIM from the relay section of the
#: dated probe evidence (docs/operations/2026-10-02-endpoint-probe-evidence.md,
#: §daily_basic by-day magnitude_reference).  String values stay strings;
#: these feed the magnitude assertions in tests/unit/test_tushare_endpoints.py.
EVIDENCE_REFERENCE_READINGS: tuple[dict[str, str], ...] = (
    {"symbol": "000001.SZ", "trade_date": "20260930",
     "raw_total_mv": "<measured>", "raw_turnover_rate": "<measured>"},
)
```

（两个 `<measured>` 与 `symbol`/`trade_date` 均以 evidence 逐字替换后才算完成本步。）

- [ ] **Step 4: 确认通过 + 点名套件** — Run: `... -m pytest tests/unit/test_tushare_endpoints.py tests/unit/test_tushare_proxy.py tests/unit/test_tushare_proxy_capabilities.py -q` → Expected: PASS（evidence 已 measured、读数已逐字冻结时转绿；量级带越界时先核 evidence 单位结论与常量一致性，不得放宽带宽凑绿）。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/data_model/basic_factor_normalize.py tests/unit/test_tushare_endpoints.py
git commit -m "test(endpoints): pin market-cap and turnover magnitude bands to probe evidence" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Self-Review 记录

- **规格覆盖（§6 逐条勾稽）**：§6.1（两端口都在 `TushareSource` 内、不取 amount/OHLCV、不触星耀 lane/tdx——Task 2/3 不涉其他源）；§6.2（窗口/日期词汇=Task 2/3 请求构造；按月分片提升+薄封装=Task 3；单位探针前不写死=Task 2 回填门；`validate_supplier_frame` 全覆盖+空响应绝不解释=Task 2/3 契约测试；`fetch_batch` 不实现——本计划无任何 fetch_batch 代码；具名集合/校验默认不手改=Task 2 裁定门）；§6.3（ts_code→canonical 沿用既有归一，落在 P2c normalize，本计划不新写映射；每份快照 sha256=Task 3；成分不写行情行——端点产物只有快照帧）；§6.4（探针条目全部在 Task 1 脚本+evidence 骨架清单：可用性/节奏含 unknown/窗口起点/行形态/空响应/两 transport 差异与配额；daily_basic 历史起点/两取法/单位与空值/同日 symbol 差异/配额失败形态/可得性时点；量级断言=Task 4；契约注入五形态=Task 2/3 参数化用例（含缺指数）；lineage 重建回归=Task 3 Step 4+开工前 #7）。§1.3（"适配器当前没有两端点"由本计划补齐；panda 代码仅作行为输入，零复制）。§11 相关行：端点超时/限额走既有错误分类（`translate_supplier_error` 照用）；"空响应被误读"对应 Task 2/3 的空响应契约测试。
- **跨任务类型/名字一致性**：`_client_read`（Task 2 定义、Task 3 用）；`INDEX_WEIGHT_INDEX_CODES`、`_month_shards`、`_DAILY_BASIC_FIELDS`/`_DAILY_BASIC_COLUMNS`、`index_weight_snapshots`（Task 3 写、薄封装读）；`daily_basic_to_basic_factor_rows`、`TOTAL_MV_TO_YUAN_MULTIPLIER`、`TURNOVER_RATE_TO_RATIO_DIVISOR`、`EVIDENCE_REFERENCE_READINGS`、`PROBE_EVIDENCE_JSON`、`RECORD_BASENAME`、`magnitude_reference`、`readings`、`_status` 各任务引用一致；Task 1 建的测试文件头只 `import` Task 1 实际用到的名字（`importlib`/`json`/`types`/`Path`），`hashlib`、`pandas as pd`、`pytest`、`SourceConfig`、`ContractError`、`DataRequest`、`TushareSource`、`date` 由 Task 2/3/4 各自在用到它的那一步追加——一次性写全会让 Task 1 提交带着 F401（本仓 `ruff check` 是有效门禁，见 §开工前），每个任务的"确认通过"只跑 pytest 不会暴露它。
- **留白与先读再接（成文，非占位）**：（1）Task 2 Step 5 的 `test_source_contracts.py` 追加——本计划成文时未读该文件，先读再接，不容纳则报告 owner；（2）evidence 日期钉 2026-10-02，执行日不同需三处同步（脚本 `RECORD_BASENAME`、normalize 两常量）；（3）Task 4 的 `<measured>` 读数与冻结表 CSI500/1000 两项——探针前不可知，均带回填门与停障语义，沿 coverage-evidence-model 计划对 dated evidence 的 `<date>` 惯例；（4）proxy ~6000 行截断 vs daily_basic 单日行数、relay/proxy 对两端点的 capability 实测声明、SDK `__getattr__` 实测行为——源码只有注释级证据，由探针实测；（5）`data/raw/csi/index_weight` 既有快照目录不入库，薄封装 `target.exists()` 续采路径以磁盘现状为准。
- **执行顺序**：Task 1 离线步骤 → 提交 → 联网门（owner 授权）→ evidence 补写 → Task 2 → Task 3 → Task 4。Task 2/3 的离线契约测试可在授权前先行，但其冻结门（常量提交、冻结表 CSI500/1000 两项、Task 4 全部）以 evidence measured 为前提。
