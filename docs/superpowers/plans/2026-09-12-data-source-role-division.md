# 数据源角色分工（阶段 0/1/2）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 jiaoch relay 成为 `tushare` 源的主传输，并把"这份数据到底由谁作答"变成可寻址、可校验、不依赖猜测的证据 —— 先跑静默换源探针（阶段 0），再落地传输层与证据标注（阶段 1），最后对存量快照做出处审计（阶段 2）。

**Architecture:** 传输选择收敛到一个显式描述符（`TushareTransport`）：它同时持有请求客户端、SDK 版本与真实 host，标签由 host 派生而不是由 `isinstance` 猜。快照寻址在原有内容哈希路径上**插入一层 `transport_id`**，使"同字节、不同来源"的两份证据能各自存活；`request_key` 保持纯幂等键不动。探针在阶段 1 首次发布之前跑，是硬闸门。

**Tech Stack:** Python 3.10 / pandas / pydantic v2 / pytest / typer / tushare SDK / akshare。

## Global Constraints

- 沟通用简体中文；代码与标识符用英文。
- 跑**指定测试文件**，不跑裸 `pytest`（集成测试单跑约 18.5 分钟）。
- 门禁是 `ruff check` + **改动行**格式检查，不跑仓库级 `ruff format --check`（本仓库在已安装的 ruff 0.16.5 下从来不是 format-clean）。
- Python 解释器：`/home/ji/miniconda3/envs/py310/bin/python`。
- 绝不打印 token 值。`.env` 被 gitignore，`.env.example` 被跟踪 —— **绝不把真实密钥写进 `.env.example`**。
- 历史泄露的 `TUSHARE_TOKEN` 已于 2026-09-12 轮换（owner 确认），但新值同样敏感；任何输出都不得包含 token 字面量。
- **传输角色的唯一权威说法**（`.env.example`、文档串、日志文案都以此为准）：
  relay 是 `data update` 的**正常主传输**；proxy **只供开发/诊断**，永不被自动选中、published 路径上被拒绝；
  official **只能 break-glass 进**（`TUSHARE_TRANSPORT=official` + `TUSHARE_ALLOW_OFFICIAL_PUBLISH=1` 两个变量同时具备）。
  任何"relay 不参与管线"或"配置了 proxy 就自动走 proxy"的说法都是改造前的旧话，见到就要改。
- `DATASET_BUILD_CONTRACT_VERSION` **保持 `1`，不 bump**。理由：`src/stock_quant/research/acceptance/checks.py:399-401` 对
  `pipeline_contract_version` 做的是**相等判定**，bump 会让所有已发布数据集的验收直接失败。新字段是**加性可选**的，旧记录继续可解析。
- `_CONFIGURED_SOURCES` 不改、不新增源位；`_REQUIRED_ROLE` 本轮**不改**（那属于阶段 4）。
- 运输选择读的是**进程环境** `os.environ`（管线从不加载 `.env`），但传输层内部一律通过**注入的 `environ` 映射**取值 —— 见 Task 2 的 `_setting`。这样 `environ=` 才是真函数入参而不是摆设，测试与就绪探针才是纯函数。
- 保留字 `unknown` **永不作为新快照的 `transport_id`**。

## 落地顺序与闸门

| 阶段 | 交付物 | 本计划中的任务 |
| --- | --- | --- |
| 0 | 最小静默换源探针 | Task 1（**Task 2 起的硬闸门**） |
| 1 | §1 传输层 + §2 证据标注 | Task 2–6 |
| 2 | §5 出处审计 | Task 7 |

**本计划不覆盖 spec 的阶段 3（§3 证伪体系）与阶段 4（§4 覆盖缺口与基准）** —— 它们是独立一轮，各自单独出计划。§4 涉及历史实验结论重跑，不应与标注改造混在一次提交里。Task 7 完成后本计划即闭环。

**Task 1 若返回非 0，Task 2–7 全部不得开始。**

## File Structure

**新建：**

| 文件 | 职责 |
| --- | --- |
| `project/probe_relay_substitution.py` | 阶段 0 静默换源探针：官方合法空结果 ⟷ relay 是否也空；错误串比对；产出运维报告 |
| `src/stock_quant/data_sources/tushare_transport.py` | 传输描述符与解析：`TushareTransport` / `resolve_transport` / `build_transport`，唯一决定"用谁、叫什么"的地方 |
| `project/audit_raw_provenance.py` | §5 一次性的存量快照出处盘点 |
| `tests/unit/test_probe_relay_substitution.py` | 探针的纯判定函数与注入式端到端 |
| `tests/unit/test_tushare_transport.py` | 传输解析的全部策略分支（不联网） |
| `tests/unit/test_source_transport_id.py` | 三个适配器产出的 `transport_id`（不联网） |
| `tests/unit/test_raw_snapshot_binding.py` | `RawSnapshotBinding` 新旧两种 payload 形状 |
| `tests/unit/test_audit_raw_provenance.py` | 盘点脚本的纯函数 |
| `tests/integration/test_raw_provenance_chain.py` | 证据链自证：build_config 行 → manifest → 传输标签 |

**修改：**

| 文件 | 改动 |
| --- | --- |
| `src/stock_quant/data_sources/base.py` | `request_metadata` 增加必填 `transport_id`；新增共享 `host_of(url)` |
| `src/stock_quant/data_sources/tushare.py` | 持描述符、不再 `isinstance` 猜来源；产出 `transport_id` |
| `src/stock_quant/data_sources/tushare_relay.py` | 公开 `.api`；`host` 复用 `host_of` |
| `src/stock_quant/data_sources/tushare_proxy.py` | `host` 复用 `host_of`（仅此一处） |
| `src/stock_quant/data_sources/akshare.py` | `_UPSTREAM_VENDOR` 表；`transport_id` = 回退链实际胜出**接口**（vendor + 接口名，同 vendor 的两个接口不得共用 id） |
| `src/stock_quant/data_sources/baostock.py` | `transport_id="baostock"` |
| `src/stock_quant/data_sources/raw_store.py` | `transport_id` 路径层、`RawSnapshotEvidence.transport_id`、`_manifest_for` 顶层字段、`verify_evidence` 旧路径回退 |
| `src/stock_quant/data_pipeline.py` | `_raw_snapshot_evidence_rows` 去重键加入 `transport_id`（5 元组） |
| `src/stock_quant/research/acceptance/models.py` | `RawSnapshotBinding.transport_id: str \| None = None`，文档串同步 |
| `src/stock_quant/cli.py` | `data update` 打开传输解析的 INFO 日志 |
| `project/verify_update_readiness.py` | `token_issue` → `transport_issue`（直接复用解析器） |
| `project/check_data_sources.py` | tushare 行走自动序、proxy 行显式指定 |
| `project/collect_index_weight_membership.py` | 加 `allow_auto_transport=True`（阶段 4 再重写） |
| `tests/unit/test_raw_store.py` | 既有 9 处夹具补 `transport_id`；新增路径/保留字/旧布局用例 |
| `tests/unit/test_tushare_relay.py` | 新增 `.api` 断言 |
| `tests/unit/test_tushare_proxy.py` | env 选择用例改断言 `transport`；新增 stub 标签用例 |
| `tests/unit/test_acceptance_models.py` | 新增 `RawSnapshotBinding` 兼容用例 |
| `tests/integration/conftest.py` | 三份夹具 `FetchResult` 补 `transport_id` |
| `tests/integration/test_data_pipeline.py` | stub 夹具补 `transport_id` |
| `tests/integration/test_acceptance_checks.py` | stub 夹具补 `transport_id` |
| `tests/unit/test_suspensions.py` | stub 夹具补 `transport_id` |
| `tests/unit/test_acceptance_service.py` | stub 夹具补 `transport_id` |
| `tests/unit/test_source_retry.py` | stub 夹具补 `transport_id` |
| `tests/unit/test_verify_update_readiness.py` | `token_issue` 用例改写为 `transport_issue` |
| `tests/integration/test_source_contracts.py` | SDK 失败用例显式声明 transport |
| `tests/external/test_live_source_contracts.py` | 显式声明 transport（诊断模式） |
| `.env` | relay 两项去掉 `=` 两侧空格（§1.3 顺带修正；`.env` 不进 git） |
| `.env.example` | relay「非管线传输」与 proxy「自动选中」两段注释改为与 design 一致（被 git 跟踪的模板，只放空占位，**绝不放真实密钥**） |

**两个不需要改的既有位置**（已确认，避免实施者多做）：

- `src/stock_quant/research/acceptance/checks.py:375` 写的是
  `store.verify_evidence(RawSnapshotEvidence(**binding.model_dump()))`。两个模型同时新增可选字段后，这条链路自动带上
  `transport_id`，无需改动 `checks.py`。
- `tests/integration/test_source_contracts.py` 里 8 处 `TushareSource(SourceConfig(), <client>)` 走的是 `client=`
  注入分支，标签由 `injected_transport` 推出，行为与今天一致。

---

### Task 1: 阶段 0 —— 最小静默换源探针

**为什么必须最先做：** 阶段 1 一发布，数据集就指向 relay。若探针之后才否掉 relay，那份数据已经被污染了。探针只查"官方合法空结果 relay 是否返回非空"，**不依赖本方案任何代码改动**（它直接用 `ts.pro_api` 和 `TushareRelayClient`，绕开将要新建的传输层）。

**Files:**
- Create: `project/probe_relay_substitution.py`
- Test: `tests/unit/test_probe_relay_substitution.py`

**Interfaces:**
- Consumes: `stock_quant.data_sources.tushare_relay.TushareRelayClient.from_env()`（已存在）
- Produces: 常量 `AGREE_EMPTY` / `AGREE_ERROR` / `SUBSTITUTION` / `DIFFERS` / `INCONCLUSIVE` / `BLOCKING` / `AGREE` / `EXIT_CLEAR` / `EXIT_BLOCKED` / `EXIT_NOT_CONFIGURED` / `REFERENCE_UNKNOWN_API_ERROR`；`ProbeCase`、`ProbeResult`、`classify_empty_probe`、`classify_error_probe`、`configured_secrets`、`redact_secrets`、`redact_result`、`run_probe(official, relay, cases=CASES, *, secrets=())`、`blocking`、`cleared`、`report(results, *, secrets=(), today=None)`

- [ ] **Step 1: 写失败的测试**

创建 `tests/unit/test_probe_relay_substitution.py`：

```python
"""Unit tests for the silent-substitution probe (no network)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[2] / "project"
sys.path.insert(0, str(PROJECT))

from probe_relay_substitution import (  # noqa: E402
    AGREE,
    AGREE_EMPTY,
    AGREE_ERROR,
    BLOCKING,
    DIFFERS,
    INCONCLUSIVE,
    REFERENCE_UNKNOWN_API_ERROR,
    SUBSTITUTION,
    ProbeCase,
    ProbeResult,
    blocking,
    classify_empty_probe,
    classify_error_probe,
    cleared,
    redact_result,
    report,
    run_probe,
)

EMPTY_CASE = ProbeCase(
    "unknown_symbol", "daily", {"ts_code": "999999.SZ"}, "empty"
)
ERROR_CASE = ProbeCase("unknown_api", "nope", {}, "error")


class FakeClient:
    """One transport stand-in: answers per endpoint, or raises per endpoint."""

    def __init__(self, *, frames=None, errors=None):
        self.frames = frames or {}
        self.errors = errors or {}
        self.calls: list[tuple[str, dict]] = []

    def query(self, endpoint, **params):
        self.calls.append((endpoint, dict(params)))
        if endpoint in self.errors:
            raise RuntimeError(self.errors[endpoint])
        return self.frames.get(endpoint, pd.DataFrame())


def test_classify_empty_probe_agrees_when_both_are_empty():
    assert classify_empty_probe(pd.DataFrame(), pd.DataFrame()) == AGREE_EMPTY


def test_classify_empty_probe_flags_a_non_empty_relay_answer():
    # The relay answered where the official side legitimately has nothing:
    # that is the promax failure mode (fallback_on_empty), which is exactly
    # what no amount of bit-comparison on *populated* responses can detect.
    assert classify_empty_probe(pd.DataFrame(), pd.DataFrame({"x": [1]})) == (
        SUBSTITUTION
    )


def test_classify_empty_probe_is_inconclusive_when_official_is_not_empty():
    # The probe only means something when the official answer really is empty.
    assert classify_empty_probe(pd.DataFrame({"x": [1]}), pd.DataFrame()) == (
        INCONCLUSIVE
    )


def test_classify_error_probe_compares_the_message_verbatim():
    assert classify_error_probe(
        REFERENCE_UNKNOWN_API_ERROR, REFERENCE_UNKNOWN_API_ERROR + " "
    ) == AGREE_ERROR
    assert classify_error_probe(REFERENCE_UNKNOWN_API_ERROR, "bad api") == DIFFERS


def test_the_reference_guard_tolerates_a_sdk_wrapper_on_both_sides():
    """The guard is a membership test; the judgement itself stays verbatim.

    Both sides answer through the same tushare SDK, so if the SDK wraps the
    server's text in an exception of its own it does so on both sides alike,
    and the two rendered messages are still equal.  The wrap on one side only
    is *not* tolerated -- see the next test: that asymmetry is itself the
    divergence this probe exists to catch.
    """
    wrapped = f"Exception: {REFERENCE_UNKNOWN_API_ERROR}"
    assert classify_error_probe(wrapped, wrapped) == AGREE_ERROR


def test_a_wrapper_on_one_side_only_is_a_divergence():
    # A relay that renders the same server error differently is not running
    # the same pipeline we audited.  Blocking, not a pass -- and Step 7 says to
    # re-run once before treating a blocking verdict as real.
    assert classify_error_probe(
        f"Exception: {REFERENCE_UNKNOWN_API_ERROR}", REFERENCE_UNKNOWN_API_ERROR
    ) == DIFFERS
    assert classify_error_probe(
        REFERENCE_UNKNOWN_API_ERROR, f"Exception: {REFERENCE_UNKNOWN_API_ERROR}"
    ) == DIFFERS


def test_an_official_side_that_failed_on_its_own_is_never_evidence():
    """A rejected credential must not be read as relay misconduct.

    The official API answers an unusable token with its own message, which of
    course differs from the relay's -- that difference is ours, not the
    relay's.  Feeding it to ``DIFFERS`` would send a viable relay back for
    review on the strength of a stale `.env`, and would return exit 1 (whose
    playbook is "relay 主供决策回炉") instead of the exit 2 this contract
    reserves for a gate that cannot be opened.
    """
    for official in ("您的token不对，请确认。", "抱歉，您每分钟最多访问该接口1次"):
        assert classify_error_probe(official, REFERENCE_UNKNOWN_API_ERROR) == (
            INCONCLUSIVE
        )
        assert classify_error_probe(official, "internal error") == INCONCLUSIVE


def test_blocking_is_the_two_disqualifying_verdicts():
    assert BLOCKING == frozenset({SUBSTITUTION, DIFFERS})


def test_agree_is_the_two_evidential_verdicts():
    assert AGREE == frozenset({AGREE_EMPTY, AGREE_ERROR})


def test_run_probe_marks_a_substitution_as_blocking():
    relay = FakeClient(frames={"daily": pd.DataFrame({"ts_code": ["000001.SZ"]})})
    official = FakeClient()
    results = run_probe(official, relay, (EMPTY_CASE,))
    assert [result.verdict for result in results] == [SUBSTITUTION]
    assert blocking(results) is True
    assert cleared(results) is False
    assert SUBSTITUTION in report(results)


def test_run_probe_passes_when_both_sides_are_empty():
    results = run_probe(FakeClient(), FakeClient(), (EMPTY_CASE,))
    assert results[0].verdict == AGREE_EMPTY
    assert blocking(results) is False
    assert cleared(results) is True


def test_run_probe_is_inconclusive_when_official_itself_raises():
    official = FakeClient(errors={"daily": "rate limited"})
    results = run_probe(official, FakeClient(), (EMPTY_CASE,))
    assert results[0].verdict == INCONCLUSIVE
    # Inconclusive is not evidence of substitution, but it is not a pass
    # either: a throttled official side must not open a hard gate.
    assert blocking(results) is False
    assert cleared(results) is False


def test_a_throttled_official_side_leaves_the_gate_closed_in_the_report():
    results = run_probe(
        FakeClient(errors={"daily": "rate limited"}), FakeClient(), (EMPTY_CASE,)
    )
    text = report(results)
    assert "阶段 1 闸门保持关闭" in text
    assert "闸门放行" not in text


def test_an_empty_case_list_never_clears_the_gate():
    assert blocking(()) is False
    assert cleared(()) is False


def test_run_probe_compares_error_strings_for_error_cases():
    official = FakeClient(errors={"nope": REFERENCE_UNKNOWN_API_ERROR})
    agreeing = FakeClient(errors={"nope": REFERENCE_UNKNOWN_API_ERROR})
    results = run_probe(official, agreeing, (ERROR_CASE,))
    assert results[0].verdict == AGREE_ERROR
    assert cleared(results) is True

    diverging = FakeClient(errors={"nope": "internal error"})
    results = run_probe(official, diverging, (ERROR_CASE,))
    assert results[0].verdict == DIFFERS
    assert blocking(results) is True


def test_a_stale_official_credential_cannot_produce_a_blocking_verdict():
    # End to end through run_probe: this is the shape the live run actually
    # produced, and it must land as "no evidence", not "relay disqualified".
    results = run_probe(
        FakeClient(errors={"nope": "您的token不对，请确认。"}),
        FakeClient(errors={"nope": "token不对，您传过来的是XXX请确认"}),
        (ERROR_CASE,),
    )
    assert results[0].verdict == INCONCLUSIVE
    assert blocking(results) is False
    assert cleared(results) is False


def test_an_error_case_is_inconclusive_when_one_side_succeeds():
    results = run_probe(
        FakeClient(frames={"nope": pd.DataFrame({"x": [1]})}),
        FakeClient(errors={"nope": REFERENCE_UNKNOWN_API_ERROR}),
        (ERROR_CASE,),
    )
    assert results[0].verdict == INCONCLUSIVE
    assert cleared(results) is False


def test_one_inconclusive_case_keeps_the_whole_run_from_clearing():
    # The gate is all-or-nothing: three agreements do not excuse one hole.
    results = run_probe(
        FakeClient(frames={"daily": pd.DataFrame()}, errors={"nope": "rate limited"}),
        FakeClient(frames={"daily": pd.DataFrame()}),
        (EMPTY_CASE, ERROR_CASE),
    )
    assert [result.verdict for result in results] == [AGREE_EMPTY, INCONCLUSIVE]
    assert blocking(results) is False
    assert cleared(results) is False


SECRET = "relay-key-that-the-remote-echoes"


def test_redact_result_scrubs_both_answers():
    result = ProbeResult(
        name="n",
        endpoint="e",
        official=f"error: rejected {SECRET}",
        relay=f"error: you sent {SECRET}",
        verdict=INCONCLUSIVE,
    )
    scrubbed = redact_result(result, (SECRET,))
    assert SECRET not in scrubbed.official
    assert SECRET not in scrubbed.relay
    assert scrubbed.verdict == result.verdict  # scrubbing never re-judges


def test_a_credential_straddling_the_clip_is_not_partially_leaked():
    """Redact first, cut second.

    Clipping before scrubbing leaves the credential's opening characters in the
    output: too short to match the full value any more, so ``redact_secrets``
    never removes them.  A partial secret is still a secret.

    Driven through ``run_probe`` rather than ``redact_result`` on purpose: the
    leak lives in the *interaction* between the clipping and the scrubbing, so
    a test that hands ``redact_result`` an untruncated message would pass under
    the old code too and pin nothing.
    """
    padding = "x" * 105
    echoing = FakeClient(errors={"nope": f"{padding}{SECRET} tail"})
    results = run_probe(
        FakeClient(errors={"nope": "rate limited"}),
        echoing,
        (ERROR_CASE,),
        secrets=(SECRET,),
    )
    assert SECRET[:8] not in results[0].relay
    assert len(results[0].relay) <= 120  # still clipped for the report


def test_run_probe_never_returns_a_credential():
    """The guard sits at the emission boundary, not at the print site.

    A caller that forgets to scrub would otherwise write the credential to the
    committable report -- which is exactly how the live run leaked it once.
    """
    echoing = FakeClient(errors={"nope": f"token不对，您传过来的是{SECRET}请确认"})
    results = run_probe(
        FakeClient(errors={"nope": "您的token不对，请确认。"}),
        echoing,
        (ERROR_CASE,),
        secrets=(SECRET,),
    )
    assert SECRET not in results[0].relay
    assert "<redacted>" in results[0].relay


def test_report_scrubs_a_result_handed_to_it_directly():
    # Defense in depth: `report` is public and can be called with results that
    # never went through `run_probe`.
    result = ProbeResult(
        name="n",
        endpoint="e",
        official="ok: 0 rows",
        relay=f"error: you sent {SECRET}",
        verdict=INCONCLUSIVE,
    )
    text = report([result], secrets=(SECRET,))
    assert SECRET not in text
    assert "<redacted>" in text
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /home/ji/work/program/stock
/home/ji/miniconda3/envs/py310/bin/python -m pytest tests/unit/test_probe_relay_substitution.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'probe_relay_substitution'`

- [ ] **Step 3: 实现探针脚本**

创建 `project/probe_relay_substitution.py`：

```python
#!/usr/bin/env python
"""Silent-substitution probe: does the relay answer where tushare answers nothing?

Design spec §3 ③, minimal version (stage 0).  A relay that carries
``fallback_on_empty`` semantics replaces "no data" with "data from somewhere
else" without saying so, which is how the promax transport was caught (the
open-day rows it dropped, the ``index_weight`` window it answered with zero
rows for).  Bit-comparison on *populated* responses cannot detect that failure
mode; an *empty* answer can, because there is nothing to agree about.

The probe therefore only asks questions the official API legitimately answers
with nothing:

* ``daily`` for a code that does not exist,
* ``daily`` for a real code over a window that ends before it listed,
* ``index_daily`` for an index that does not exist,
* an unknown ``api_name``, whose error text must match verbatim.

No ``trade_cal`` case: the official token is limited to one call per hour on
it, so it cannot be probed reliably.

Every verdict is a pure function of the two answers, so the classifiers are
unit tested without a network.  This script deliberately does NOT use the
transport layer of §1 -- stage 0 must be runnable today, before any of that
exists.

Run from the repository root or the project directory:
    python project/probe_relay_substitution.py
    python project/probe_relay_substitution.py --no-report

Exit codes: 0 = every case agreed (the stage 1 gate is clear); 1 = the relay
substituted an answer, or the official side gave its reference error and the
relay's error text diverged from it -- **do not start stage 1**; 2 = the gate
could not be opened, either because the probe could not run (relay or official
credentials missing) or because some case produced no evidence -- the official
side rate-limited, unreachable, or failed for a reason of its own (a rejected
credential, say), which makes the pair say nothing about the relay.  Only 0
opens stage 1: this is a hard gate, so "unanswered" must never read as
"passed", and an official-side failure of our own making must never be
recorded as the relay's fault.  Token values are never printed or written:
answers are scrubbed at the boundary where results are built and where the
report is rendered, because a relay may echo back the key it was handed.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from stock_quant.data_sources.tushare_relay import TushareRelayClient

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = ROOT / ".env"
REPORT_DIR = ROOT / "docs" / "operations"

AGREE_EMPTY = "AGREE_EMPTY"
AGREE_ERROR = "AGREE_ERROR"
SUBSTITUTION = "SUBSTITUTION"
# Constant and verdict differ on purpose: the constant reads well inside the
# ``BLOCKING`` set, and the string is what the report prints.
DIFFERS = "ERROR_DIFFERS"
INCONCLUSIVE = "INCONCLUSIVE"

#: Verdicts that positively disqualify the relay.
BLOCKING = frozenset({SUBSTITUTION, DIFFERS})
#: Verdicts that count as evidence.  The gate opens only when *every* case is
#: one of these: an unanswered question is not a passed question.
AGREE = frozenset({AGREE_EMPTY, AGREE_ERROR})

EXIT_CLEAR = 0
EXIT_BLOCKED = 1
EXIT_NOT_CONFIGURED = 2

_NOT_A_REAL_API = "not_a_real_tushare_endpoint"

#: The official server's own answer for an unknown ``api_name``, recorded
#: verbatim from a live call.  This is the only official error that makes the
#: ``unknown_api_name`` case meaningful -- see ``classify_error_probe``.
REFERENCE_UNKNOWN_API_ERROR = "请指定正确的接口名"


@dataclass(frozen=True)
class ProbeCase:
    """One question whose correct answer on the official side is known."""

    name: str
    endpoint: str
    params: dict[str, str]
    expect: str  # "empty" | "error"


@dataclass(frozen=True)
class ProbeResult:
    name: str
    endpoint: str
    official: str
    relay: str
    verdict: str


CASES: tuple[ProbeCase, ...] = (
    ProbeCase(
        "nonexistent_symbol",
        "daily",
        {"ts_code": "999999.SZ", "start_date": "20260801", "end_date": "20260828"},
        "empty",
    ),
    ProbeCase(
        "window_before_listing",
        "daily",
        {"ts_code": "000001.SZ", "start_date": "19900101", "end_date": "19901231"},
        "empty",
    ),
    ProbeCase(
        "nonexistent_index",
        "index_daily",
        {"ts_code": "399999.SZ", "start_date": "20260801", "end_date": "20260828"},
        "empty",
    ),
    ProbeCase("unknown_api_name", _NOT_A_REAL_API, {}, "error"),
)


def _load_env(path: Path) -> None:
    """Load simple KEY=VALUE lines without evaluating shell code."""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", maxsplit=1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


#: Every credential the probe holds.  A relay is free to echo the token it was
#: handed back inside an error string -- jiaoch.top does exactly that, verified
#: live on 2026-09-12 -- so no answer text may be printed or written until it
#: has been scrubbed for these.  If a future credential can be echoed by a
#: remote side, add its name here.
_SECRET_ENV_VARS = ("TUSHARE_TOKEN", "TUSHARE_RELAY_KEY", "TUSHARE_PROXY_KEY")
_REDACTED = "<redacted>"


def configured_secrets() -> tuple[str, ...]:
    """The credential values present in the environment, for scrubbing."""
    return tuple(
        value
        for name in _SECRET_ENV_VARS
        if (value := os.environ.get(name, "").strip())
    )


def redact_secrets(text: str, secrets: Sequence[str]) -> str:
    """Replace every configured credential value in ``text`` with a placeholder.

    ``str.replace``, not a regex: credential values may contain regex
    metacharacters, and the failure mode of a bad pattern (silently matching
    nothing) is exactly the one that leaks.
    """
    for secret in secrets:
        text = text.replace(secret, _REDACTED)
    return text


def redact_result(
    result: ProbeResult, secrets: Sequence[str], *, limit: int = 120
) -> ProbeResult:
    """A copy of ``result`` with any leaked credential removed from its answers.

    Applied where results are *built* and where they are *rendered*, never only
    at the print site: a guard that lives at one call site is one refactor away
    from being dropped, and the thing it protects is a credential written to a
    file that gets committed.

    Scrubbing runs on the *whole* message and the cut happens after, in that
    order.  Clipping first would let a credential that straddles the cut
    survive as a prefix -- which no longer matches the full value in
    ``redact_secrets``, and so is never scrubbed at all.
    """
    return replace(
        result,
        official=_clip(redact_secrets(result.official, secrets), limit),
        relay=_clip(redact_secrets(result.relay, secrets), limit),
    )


def _clip(text: str, limit: int) -> str:
    """Shorten one rendered answer for the report table."""
    return text if len(text) <= limit else text[:limit]


def classify_empty_probe(official: pd.DataFrame, relay: pd.DataFrame) -> str:
    """Verdict for one "official returns nothing here" question.

    The probe is only meaningful when the official answer really is empty; if
    it is not, the case tells us nothing and says so instead of guessing.
    """
    if not official.empty:
        return INCONCLUSIVE
    return AGREE_EMPTY if relay.empty else SUBSTITUTION


def classify_error_probe(official: str, relay: str) -> str:
    """Verdict for one "official legitimately errors here" question.

    Only the reference answer is evidence.  An official side that failed for a
    reason of its own -- a rejected credential, a rate limit, a network fault
    -- answered a different question, so the pair says nothing about the relay
    and must never be allowed to feed ``DIFFERS``.  Doing otherwise would
    disqualify a viable relay on the strength of our own stale ``.env``, and
    would return exit 1 (playbook: send the relay-as-main decision back for
    review) where this contract reserves exit 2 for a gate that cannot open.
    Fails closed: if tushare ever rewords the reference message, the case
    becomes ``INCONCLUSIVE`` and the gate stays shut until a human refreshes
    the constant from a live run.
    """
    if REFERENCE_UNKNOWN_API_ERROR not in official:
        return INCONCLUSIVE
    return AGREE_ERROR if official.strip() == relay.strip() else DIFFERS


def _read(client: Any, case: ProbeCase) -> tuple[str, Any]:
    """One transport read as ``("ok", frame)`` or ``("error", message)``."""
    try:
        frame = client.query(case.endpoint, **case.params)
    except Exception as error:  # noqa: BLE001 - the failure *is* the datum
        return ("error", str(error))
    if not isinstance(frame, pd.DataFrame):
        return ("error", f"response is not a DataFrame: {type(frame).__name__}")
    return ("ok", frame)


def _describe(answer: tuple) -> str:
    """Render one answer in full.

    Deliberately untruncated: the cut belongs after redaction, in
    ``redact_result``, or a credential straddling it escapes scrubbing.
    """
    if answer[0] == "error":
        return f"error: {answer[1]}"
    return f"ok: {len(answer[1])} rows"


def _verdict(case: ProbeCase, official: tuple, relay: tuple) -> str:
    if case.expect == "error":
        if official[0] != "error" or relay[0] != "error":
            return INCONCLUSIVE
        return classify_error_probe(official[1], relay[1])
    if official[0] != "ok" or relay[0] != "ok":
        return INCONCLUSIVE
    return classify_empty_probe(official[1], relay[1])


def run_probe(
    official: Any,
    relay: Any,
    cases: Sequence[ProbeCase] = CASES,
    *,
    secrets: Sequence[str] = (),
) -> list[ProbeResult]:
    """Run every case against both transports and classify the pair.

    Redaction happens here, at the point the results are *built*, not at the
    call site that prints them: a ``ProbeResult`` is what gets written into the
    operations report, so scrubbing at construction means no downstream
    consumer -- present or future -- can leak a credential a remote side echoed
    back.  The verdict is computed on the *raw* answers first, so redaction can
    never change a judgement.

    ``secrets`` must be the same sequence the caller will hand to
    :func:`report`.  Results come out of here already clipped, so a credential
    that straddles that cut is no longer matchable: a later scrub with a
    different sequence would remove what it recognises and silently keep the
    fragment.
    """
    results: list[ProbeResult] = []
    for case in cases:
        official_answer = _read(official, case)
        relay_answer = _read(relay, case)
        results.append(
            redact_result(
                ProbeResult(
                    name=case.name,
                    endpoint=case.endpoint,
                    official=_describe(official_answer),
                    relay=_describe(relay_answer),
                    verdict=_verdict(case, official_answer, relay_answer),
                ),
                secrets,
            )
        )
    return results


def blocking(results: Sequence[ProbeResult]) -> bool:
    """True when any case positively disqualifies the relay."""
    return any(result.verdict in BLOCKING for result in results)


def cleared(results: Sequence[ProbeResult]) -> bool:
    """True only when every case produced evidence of agreement.

    The gate is a hard gate, so it opens on *evidence*, not on the absence of
    a refutation: ``INCONCLUSIVE`` -- the official side rate-limited, a
    network failure on either side, a question the official API happened to
    answer -- is not a pass.  Treating it as one would let a throttling window
    open the gate.
    """
    return bool(results) and all(result.verdict in AGREE for result in results)


def report(
    results: Sequence[ProbeResult],
    *,
    secrets: Sequence[str] = (),
    today: date | None = None,
) -> str:
    """Render the probe as a committable operations report.

    Scrubs again on the way out, independently of ``run_probe``: the report is
    the artifact that gets committed, so it is the boundary that must not
    depend on its caller having remembered.
    """
    results = [redact_result(result, secrets) for result in results]
    day = (today or date.today()).isoformat()
    if blocking(results):
        headline = "命中阻断条件，阶段 1 不得开始"
    elif cleared(results):
        headline = "全部用例给出证据，阶段 1 闸门放行"
    else:
        headline = "存在未给出证据的用例，阶段 1 闸门保持关闭"
    lines = [
        f"# 静默换源探针报告（{day}）",
        "",
        "来源：`project/probe_relay_substitution.py`"
        "（设计规格 §3 ③ 最小版本，落地阶段 0）。",
        "",
        f"**结论：{headline}**",
        "",
        "判据：官方合法返回空 / 合法报错的请求，relay 必须给出**同为空的载荷**"
        "或**逐字相同的错误串**。",
        "",
        "- `SUBSTITUTION` = relay 在官方无数据处返回了非空；",
        "- `ERROR_DIFFERS` = 官方给出了它**本该给出**的参照错误，而 relay 的错误串"
        "与它不一致。",
        "",
        "两者都属于 spec §3 ④ 的阻断条件。`INCONCLUSIVE` 表示官方侧本身没给出"
        "可用答案（限流、网络失败，或该问法官方本来就有数据），该用例**不构成"
        "证据** —— 既不算通过，也不算失败。**闸门只在全部用例都给出证据时才放行**，"
        "所以 `INCONCLUSIVE` 同样让阶段 1 保持关闭。",
        "",
        "注意 `unknown_api_name` 的判定：只有官方答出参照错误串"
        f"（`{REFERENCE_UNKNOWN_API_ERROR}`）时，relay 的错误串才成为证据。官方若因"
        "自身原因报错 —— 凭据被拒、限流、网络故障 —— 它答的是另一个问题，该用例"
        "一律记 `INCONCLUSIVE`（退出码 2），**不得**记 `ERROR_DIFFERS`。否则一份"
        "过期的 `.env` 就足以把可用的 relay 判成阻断条件。",
        "",
        "| 用例 | endpoint | 官方作答 | relay 作答 | 判定 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for result in results:
        lines.append(
            f"| `{result.name}` | `{result.endpoint}` | {result.official} "
            f"| {result.relay} | **{result.verdict}** |"
        )
    lines += [
        "",
        "## 证据上界",
        "",
        "本探针**只能证伪、不能证实**：它排除了「对空结果换源作答」这一条通道，",
        "**不证明** relay 的上游就是 tushare 官方。逐位一致与逐字相同的错误串",
        "同样只是「输出与官方不可区分」级证据，不是「上游即官方」的证据。",
        "",
        "## 复现",
        "",
        "```bash",
        f"python project/probe_relay_substitution.py  # {day}",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument(
        "--report",
        type=Path,
        default=REPORT_DIR / f"relay-substitution-probe-{date.today().isoformat()}.md",
    )
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    if args.env_file.is_file():
        _load_env(args.env_file)
        print(f"environment: loaded {args.env_file}")
    else:
        print(f"environment: not found ({args.env_file}); using current environment")

    relay_client = TushareRelayClient.from_env()
    if relay_client is None:
        print(
            "relay: not configured (set TUSHARE_RELAY_URL and TUSHARE_RELAY_KEY); "
            "the probe cannot run, so the stage 1 gate stays closed"
        )
        return EXIT_NOT_CONFIGURED

    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        print(
            "official: TUSHARE_TOKEN is not set; without the official side there "
            "is nothing to compare against, so the stage 1 gate stays closed"
        )
        return EXIT_NOT_CONFIGURED

    import tushare as ts

    official_client = ts.pro_api(token)
    print(f"relay: host={relay_client.host} sdk={relay_client.sdk_version}")
    print(f"official: sdk={getattr(ts, '__version__', 'unknown')}")

    secrets = configured_secrets()
    results = run_probe(official_client, relay_client, secrets=secrets)
    for result in results:
        print(
            f"{result.name:22s} {result.verdict:14s} "
            f"official={result.official} relay={result.relay}"
        )

    text = report(results, secrets=secrets)
    if not args.no_report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
        print(f"report: {args.report}")

    if blocking(results):
        print("BLOCKED: the relay substituted an answer; do not start stage 1")
        return EXIT_BLOCKED
    if not cleared(results):
        print(
            "NOT CLEARED: at least one case produced no evidence, and either "
            "side can be the one that failed -- the official side "
            "rate-limited, was unreachable, or refused the credential; the "
            "relay side failed or was unreachable; or a side answered a "
            "different question than the case asked.  Read the answers above "
            "before re-running: when two sides fail with the same error, the "
            "fix is that credential, not a retry.  The stage 1 gate stays "
            "closed until every case agrees."
        )
        return EXIT_NOT_CONFIGURED
    print("CLEAR: every case agreed; the stage 1 gate is open")
    return EXIT_CLEAR


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

```bash
/home/ji/miniconda3/envs/py310/bin/python -m pytest tests/unit/test_probe_relay_substitution.py -v
```

Expected: PASS — 22 passed

- [ ] **Step 5: 提交**

```bash
git add project/probe_relay_substitution.py tests/unit/test_probe_relay_substitution.py
git commit -m "feat(provenance): add the stage 0 silent-substitution probe"
```

- [ ] **Step 6: 跑探针（真实网络，闸门）**

```bash
/home/ji/miniconda3/envs/py310/bin/python project/probe_relay_substitution.py
```

Expected: 打印四行判定，退出码 `0`，并写出
`docs/operations/relay-substitution-probe-2026-09-12.md`。

- [ ] **Step 7: 按退出码处置**

| 退出码 | 动作 |
| --- | --- |
| `0` | **只有这一种情况可以继续**。`git add docs/operations/relay-substitution-probe-*.md && git commit -m "docs: record the stage 0 substitution probe result"`，继续 Task 2 |
| `1` | **停止**。不进入 Task 2；把报告交给 owner，relay 主供决策回炉（spec §3 ④） |
| `2` | **停止**。闸门未开 —— 凭据缺失，或有用例是 `INCONCLUSIVE`（官方侧限流、不可达，或它因自身原因报错）。补齐条件后**重跑探针**，不要带病进入 Task 2 |

**退出码 0 的严格含义**：不是"没有发现问题"，而是"每个用例都拿到了证据且都同意"。四行里出现任何 `INCONCLUSIVE`，退出码就是 2 而不是 0 —— 官方侧被限流时，`daily` 的空窗口可能正是因为限流才为空的，那样的"两边都空"不构成证据。

**`INCONCLUSIVE` 不等于 relay 有问题**：`unknown_api_name` 一例里，只有官方答出参照错误串 `请指定正确的接口名` 时，relay 的错误串才成为证据；官方若因自身原因报错（凭据被拒、限流、网络故障），该例记 `INCONCLUSIVE`、退出码 2，而不是 `ERROR_DIFFERS`。判据是"官方有没有答出它本该答的那句话"，不是"两边是否都能跑通"。把官方自身的失败记成 relay 的阻断条件是错的：它会把一份过期的 `.env` 变成淘汰可用 relay 的理由，而且退出码 1 的动作（relay 主供决策回炉）根本不是这种情况该走的路。

若命中的是 `ERROR_DIFFERS`（即官方确实答出了参照错误串，而 relay 不一致），先确认探针没有把 relay 侧的临时网络故障误读成"错误串不同"（重跑一次）；可复现才按阻断处理。**本机凭据状态不影响这一判定**：`TUSHARE_TOKEN` 若已被拒，`unknown_api_name` 一例必然是 `INCONCLUSIVE`，闸门必然停在退出码 2 —— 在 owner 把轮换后的 token 写入 `./.env` 之前，探针不可能给出退出码 0，也不需要为此改动探针。

---

### Task 2: 传输描述符与解析（`tushare_transport`）

**Files:**
- Create: `src/stock_quant/data_sources/tushare_transport.py`
- Modify: `src/stock_quant/data_sources/base.py`（新增 `host_of`）
- Modify: `src/stock_quant/data_sources/tushare_relay.py`（`host` 复用 `host_of`；新增 `.api`）
- Modify: `src/stock_quant/data_sources/tushare_proxy.py`（`host` 复用 `host_of`）
- Modify: `.env.example`（relay / proxy 两段注释改为与 design 一致）
- Test: `tests/unit/test_tushare_transport.py`
- Test: `tests/unit/test_tushare_relay.py`（追加一条）

**Interfaces:**
- Consumes: `TushareRelayClient(base_url, token, *, timeout_seconds, sdk)`、`.api`、`.host`、`.sdk_version`；`TushareProxyClient(base_url, api_key, *, timeout_seconds, max_retries)`、`.host`、`.sdk_version`；`host_of(url)`
- Produces:
  - `TushareTransport(kind, client, sdk_version, host)`，属性 `transport_id`、方法 `supplier_endpoint(endpoint)`
  - `build_transport(kind, config, *, sdk=None, environ=None) -> TushareTransport`
  - `resolve_transport(config, *, allow_auto_transport=False, sdk=None, environ=None) -> TushareTransport`
  - `official_transport(environ, *, sdk=None) -> TushareTransport`
  - `client_host(client) -> str`
  - `CredentialsMissing(AuthenticationError)`
  - 常量 `TRANSPORT_ENV`、`OFFICIAL_PUBLISH_ENV`、`OFFICIAL_HOST`、`OFFICIAL`、`PROXY`、`RELAY`

> **关键设计点（`environ` 必须是真的入参）**：`TushareRelayClient.from_env()` / `TushareProxyClient.from_env()` 读的是
> **进程环境** `os.environ`，它们服务于离线脚本（如 `project/crosscheck_calendar_relay.py`）。如果 `build_transport` 也走
> `from_env`，那么 `environ=` 就只是摆设 —— 测试与 `verify_update_readiness` 都无法做纯函数。所以 `build_transport`
> **自己从传入的 `environ` 取变量并直接构造客户端**（`_setting` 助手），代价只是重复两行"空值检查"。

- [ ] **Step 1: 写失败的测试**

创建 `tests/unit/test_tushare_transport.py`：

```python
"""Unit tests for tushare transport selection and provenance (no network)."""

from __future__ import annotations

import logging
from functools import partial

import pandas as pd
import pytest

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import AuthenticationError, host_of
from stock_quant.data_sources.tushare_transport import (
    OFFICIAL,
    OFFICIAL_HOST,
    OFFICIAL_PUBLISH_ENV,
    PROXY,
    RELAY,
    TRANSPORT_ENV,
    build_transport,
    resolve_transport,
)

RELAY_URL = "https://jiaoch.example/"
PROXY_URL = "https://proxy.example/tushare/pro"

RELAY_ENV = {"TUSHARE_RELAY_URL": RELAY_URL, "TUSHARE_RELAY_KEY": "relay-key"}
PROXY_ENV = {"TUSHARE_PROXY_URL": PROXY_URL, "TUSHARE_PROXY_KEY": "proxy-key"}
OFFICIAL_ENV = {"TUSHARE_TOKEN": "official-token"}
BREAK_GLASS_ENV = dict(OFFICIAL_ENV, **{OFFICIAL_PUBLISH_ENV: "1"})


class FakeApi:
    """A stand-in for the official ``DataApi`` returned by ``pro_api``.

    It reproduces the one behaviour that makes §1.1 work: the real ``DataApi``
    defines ``__getattr__`` returning ``partial(self.query, name)``, so a
    session answers named calls exactly like the official one.
    """

    def __init__(self, base_url: str) -> None:
        setattr(self, "_DataApi__http_url", base_url)

    def query(self, endpoint, **params):
        return pd.DataFrame({"endpoint": [endpoint]})

    def __getattr__(self, name):
        return partial(self.query, name)


class FakeSdk:
    __version__ = "fake-sdk-9.9"

    def __init__(self, base_url: str = f"http://{OFFICIAL_HOST}/dataapi") -> None:
        self.base_url = base_url
        self.tokens: list[str] = []
        self.timeouts: list[int] = []

    def pro_api(self, token="", timeout=30):
        self.tokens.append(token)
        self.timeouts.append(timeout)
        return FakeApi(self.base_url)


def _config() -> SourceConfig:
    return SourceConfig()


def test_host_of_handles_scheme_port_and_path():
    assert host_of("http://api.waditu.com/dataapi") == "api.waditu.com"
    assert host_of("https://jiaoch.example/") == "jiaoch.example"
    assert host_of("https://proxy.example/tushare/pro") == "proxy.example"
    assert host_of("") == ""


def test_relay_transport_reports_its_own_host_and_endpoint():
    transport = resolve_transport(
        _config(), environ=dict(RELAY_ENV, **{TRANSPORT_ENV: RELAY}), sdk=FakeSdk()
    )
    assert transport.kind == RELAY
    assert transport.host == "jiaoch.example"
    assert transport.transport_id == "jiaoch.example"
    assert transport.supplier_endpoint("daily") == "tushare_relay.jiaoch.example.daily"


def test_relay_credentials_come_from_the_passed_environ(monkeypatch):
    # from_env() reads the process environment; the pipeline path must not, or
    # ``environ=`` would be decorative and this test impossible.
    monkeypatch.setenv("TUSHARE_RELAY_URL", "https://wrong.example/")
    monkeypatch.setenv("TUSHARE_RELAY_KEY", "wrong-key")
    transport = build_transport(RELAY, _config(), environ=RELAY_ENV, sdk=FakeSdk())
    assert transport.host == "jiaoch.example"


def test_official_transport_keeps_the_tushare_pro_label():
    transport = build_transport(
        OFFICIAL, _config(), environ=OFFICIAL_ENV, sdk=FakeSdk()
    )
    assert transport.kind == OFFICIAL
    assert transport.host == OFFICIAL_HOST
    assert transport.transport_id == OFFICIAL_HOST
    assert transport.supplier_endpoint("daily") == "tushare.pro.daily"
    assert transport.transport_id != "unknown"


def test_official_transport_refuses_to_label_a_rewritten_base_url():
    # The whole point: a session whose base URL is not the official host can
    # never be recorded as tushare.pro.*, because the label is derived from
    # the URL actually reached and not from the client's type.
    sdk = FakeSdk(base_url="https://somewhere.example/dataapi")
    with pytest.raises(AuthenticationError) as raised:
        build_transport(OFFICIAL, _config(), environ=OFFICIAL_ENV, sdk=sdk)
    assert OFFICIAL_HOST in str(raised.value)


def test_proxy_transport_keeps_its_label_and_uses_its_host_as_id():
    transport = build_transport(PROXY, _config(), environ=PROXY_ENV)
    assert transport.kind == PROXY
    assert transport.host == "proxy.example"
    assert transport.transport_id == "proxy.example"
    assert transport.supplier_endpoint("daily") == "tushare_proxy.daily"


def test_published_builds_fail_without_an_explicit_transport(monkeypatch):
    monkeypatch.setenv("TUSHARE_RELAY_URL", RELAY_URL)
    monkeypatch.setenv("TUSHARE_RELAY_KEY", "relay-key")
    with pytest.raises(AuthenticationError) as raised:
        resolve_transport(_config(), sdk=FakeSdk())
    assert TRANSPORT_ENV in str(raised.value)


def test_published_builds_reject_the_proxy():
    with pytest.raises(AuthenticationError) as raised:
        resolve_transport(_config(), environ=dict(PROXY_ENV, **{TRANSPORT_ENV: PROXY}))
    assert PROXY in str(raised.value)


def test_published_builds_reject_official_without_break_glass():
    with pytest.raises(AuthenticationError) as raised:
        resolve_transport(
            _config(), environ=dict(OFFICIAL_ENV, **{TRANSPORT_ENV: OFFICIAL})
        )
    assert OFFICIAL_PUBLISH_ENV in str(raised.value)


def test_break_glass_releases_official_for_a_published_build(caplog):
    # Acceptance 2 names both places an emergency official publish must show
    # up: the run log and the supplier endpoint.
    log = "stock_quant.data_sources.tushare_transport"
    with caplog.at_level(logging.INFO, logger=log):
        transport = resolve_transport(
            _config(),
            environ=dict(BREAK_GLASS_ENV, **{TRANSPORT_ENV: OFFICIAL}),
            sdk=FakeSdk(),
        )
    assert transport.kind == OFFICIAL
    assert transport.supplier_endpoint("daily") == "tushare.pro.daily"
    assert "kind=official" in caplog.text
    assert OFFICIAL_HOST in caplog.text


def test_unknown_transport_value_fails_without_falling_back():
    with pytest.raises(AuthenticationError) as raised:
        resolve_transport(
            _config(), environ=dict(RELAY_ENV, **{TRANSPORT_ENV: "relayy"})
        )
    assert "relayy" in str(raised.value)


@pytest.mark.parametrize(
    "env",
    [
        {TRANSPORT_ENV: RELAY},
        {TRANSPORT_ENV: RELAY, "TUSHARE_RELAY_URL": RELAY_URL},
        {TRANSPORT_ENV: RELAY, "TUSHARE_RELAY_KEY": "relay-key"},
    ],
)
def test_half_configured_relay_fails_without_falling_back(env):
    # A *half*-configured transport is an operator error, not an absence: it
    # must not quietly become a different transport.
    with pytest.raises(AuthenticationError):
        resolve_transport(_config(), environ=env, sdk=FakeSdk())


def test_relay_initialization_failure_does_not_fall_back_to_official():
    class ExplodingSdk(FakeSdk):
        def pro_api(self, token="", timeout=30):
            # A real SDK failure can echo what it was handed; the translation
            # must not let that reach the caller.
            raise RuntimeError(f"relay handshake failed for {token}")

    env = dict(RELAY_ENV, **{TRANSPORT_ENV: RELAY, "TUSHARE_TOKEN": "official-token"})
    with pytest.raises(AuthenticationError) as raised:
        resolve_transport(_config(), environ=env, sdk=ExplodingSdk())
    assert "relay-key" not in str(raised.value)
    assert raised.value.__cause__ is None
    # The host is not a secret and is what makes the failure diagnosable.
    assert "jiaoch.example" in str(raised.value)


def test_auto_order_prefers_the_relay_when_both_are_configured():
    env = dict(RELAY_ENV, **OFFICIAL_ENV)
    transport = resolve_transport(
        _config(), allow_auto_transport=True, environ=env, sdk=FakeSdk()
    )
    assert transport.kind == RELAY


def test_auto_order_falls_back_to_official_and_never_to_the_proxy():
    # The proxy answers with fallback semantics; no automatic path may pick it
    # up behind the operator's back, even in development mode.
    env = dict(PROXY_ENV, **OFFICIAL_ENV)
    transport = resolve_transport(
        _config(), allow_auto_transport=True, environ=env, sdk=FakeSdk()
    )
    assert transport.kind == OFFICIAL


def test_auto_order_fails_when_nothing_is_configured():
    with pytest.raises(AuthenticationError):
        resolve_transport(
            _config(), allow_auto_transport=True, environ={}, sdk=FakeSdk()
        )


def test_auto_transport_never_consults_the_process_environment(monkeypatch):
    monkeypatch.setenv(TRANSPORT_ENV, PROXY)
    monkeypatch.setenv("TUSHARE_PROXY_URL", PROXY_URL)
    monkeypatch.setenv("TUSHARE_PROXY_KEY", "proxy-key")
    transport = resolve_transport(
        _config(), environ=dict(RELAY_ENV, **{TRANSPORT_ENV: RELAY}), sdk=FakeSdk()
    )
    assert transport.kind == RELAY
```

- [ ] **Step 2: 跑测试确认失败**

```bash
/home/ji/miniconda3/envs/py310/bin/python -m pytest tests/unit/test_tushare_transport.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'stock_quant.data_sources.tushare_transport'`

- [ ] **Step 3: 在 `base.py` 新增共享的 host 解析**

在 `src/stock_quant/data_sources/base.py` 的 `_utc_timestamp` 之前插入：

```python
def host_of(url: str) -> str:
    """The bare host of a base URL, or ``""`` when there is none.

    Transport provenance is derived from the URL a client will actually
    reach, never from the client's type, so this is the single place that
    answers "which host is this?".  It is deliberately lenient: an
    unparseable or empty URL yields ``""`` and the caller decides whether
    that is fatal.
    """
    text = str(url or "").strip()
    if not text:
        return ""
    without_scheme = text.split("//", 1)[-1]
    return without_scheme.split("/", 1)[0].split("?", 1)[0].strip()
```

（`base.py` 没有 `__all__`，无需额外导出。）

- [ ] **Step 4: 让两个客户端复用 `host_of`，并给 relay 加上 `.api`**

`src/stock_quant/data_sources/tushare_relay.py`：先把模块文档串里那段"范围边界"整段替换掉 —— 它现在说的是反的。

原句（`This client exists for **offline collectors and cross-checks only** ... a separate, deliberate change with its own contract tests`）断言
`TushareSource`/`DataPipeline` **永不**构造本类，并把"管线走中转"称为证据链存在的意义所在的那个归因错误。设计规格把这条边界**有意反转了**：relay 现在就是管线的 tushare 主传输，而归因问题改由"把 relay 路径如实记进证据"来解决（阶段 0 探针 + `transport_id` + `supplier_endpoint`），不是靠拒绝使用它。文档串不换，代码库就会自相矛盾。

替换为：

```python
Scope boundary (revised 2026-09-12, design spec §1)
--------------------------------------------------
This client started as an offline-only collector helper precisely because
routing a relay through the pipeline would have bound the relay's identity
into the dataset version's ``supplier_endpoint`` evidence -- presenting
relayed data as though it came from the source.  The role-division design
resolves that concern the other way round: the relay is now the pipeline's
primary tushare transport, and the attribution is kept honest by *recording*
the relay path everywhere the evidence is read (``transport_id`` in the raw
snapshot path and manifest, ``tushare_relay.<host>.<endpoint>`` as the
supplier label) rather than by refusing to use it.  The stage 0
silent-substitution probe gates that promotion.  What remains forbidden is
the opposite error: labelling relay-answered data as ``tushare.pro.*``.
```

然后在导入区加入
`from stock_quant.data_sources.base import host_of`，把 `host` 属性体替换为：

```python
    @property
    def host(self) -> str:
        """The bare host of the relay (audit metadata only)."""
        return host_of(self.base_url)

    @property
    def api(self) -> Any:
        """The official SDK session this relay is.

        The relay IS the official ``DataApi`` with its base URL rewritten, so
        it is simultaneously the request client (``.daily`` / ``.index_daily``
        / ``.stock_basic`` all work) and -- by definition -- not the official
        transport.  That is why provenance cannot be an ``isinstance`` test.
        """
        return self._api
```

`src/stock_quant/data_sources/tushare_proxy.py`：同样导入 `host_of`，把 `host` 属性体替换为：

```python
    @property
    def host(self) -> str:
        """The bare host of the configured base URL (audit metadata only)."""
        return host_of(self.base_url)
```

`tests/unit/test_tushare_relay.py` 追加：

```python
def test_api_exposes_the_session_that_was_rewritten():
    sdk = FakeSdk()
    client = TushareRelayClient(RELAY_URL, "secret", sdk=sdk)
    assert client.api is sdk.api
    assert getattr(client.api, "_DataApi__http_url") == RELAY_URL
```

（该文件已有的 `FakeSdk` 有 `.api` 属性；若其命名不同，按实际属性名调整这一条断言。）

- [ ] **Step 4b: 更正 `.env.example` 的两段注释（同一句错话的第二处）**

`.env.example` 现在有两个段落描述的是**改造前**的传输规则，改完代码后会与事实相反：

- relay 段写着「Deliberately NOT a pipeline transport: the pipeline never reads these」—— 与上一步刚改掉的 `tushare_relay.py` 文档串是同一句错话；
- proxy 段写着「When both are set, the tushare adapter routes through it and `TUSHARE_TOKEN` becomes unnecessary」—— 自动选中已经取消。

把该文件整体替换为（`TUSHARE_TOKEN` 与 `BASIC_RDS_*` 两段保持原样，只是位置随之移动）：

```dotenv
# The relay is the normal tushare transport for `data update`.  A published
# build must name its transport explicitly (TUSHARE_TRANSPORT=relay), and the
# relay is the only kind it may name.  See
# src/stock_quant/data_sources/tushare_transport.py.
TUSHARE_RELAY_URL=
TUSHARE_RELAY_KEY=

TUSHARE_TOKEN=

# Shared Tushare-compatible GET proxy (datahubco / mobcvb aggregation front).
# Development and diagnostic use only: it is never selected automatically and
# is refused for a published build.  Paste the personal X-API-Key value below.
TUSHARE_PROXY_URL=https://pcd.mobcvb.cn/tushare/pro
TUSHARE_PROXY_KEY=

# Emergency-only override.  Sending a published build back to the official API
# requires BOTH variables, so it cannot happen by accident.  See the break-glass
# rules in src/stock_quant/data_sources/tushare_transport.py.
# TUSHARE_TRANSPORT=official
# TUSHARE_ALLOW_OFFICIAL_PUBLISH=1

# datahubco RDS.  Consumed by the workbuddy project, NOT by this repository -
# no code here reads it.  Kept in the template so the URL is not lost.
BASIC_RDS_URL=http://datahubco.com/app-api/openapi/v1/tushare/stock-basic
BASIC_RDS_KEY=
```

三条必须同时成立，缺一条这段注释就还是错的：

1. **relay 是 `data update` 的正常主传输**，不是"离线脚本专用"；
2. **pipeline 读的是进程环境**，本仓库不会自动加载 `.env` —— operator 必须自己导出
   （Task 6 Step 4 用的就是 `set -a; . ./.env; set +a`）；
3. **proxy 不再被自动选中，且 published 路径上被拒绝；official 只能 break-glass 进**。

**绝不要把任何真实密钥写进这个文件** —— 它是被 git 跟踪的模板，`.env` 才是 gitignored 的那份。

- [ ] **Step 5: 实现 `tushare_transport.py`**

创建 `src/stock_quant/data_sources/tushare_transport.py`：

```python
"""Which transport answers a tushare request, and what that transport is called.

Design spec §1.  Two jobs that used to be conflated in one object are split
here:

* the **request client** -- an object that really has ``daily`` /
  ``index_daily`` / ``stock_basic``.  For the relay and the official direct
  path that is the official ``DataApi`` (its ``__getattr__`` returns
  ``partial(self.query, name)``); for the shared GET proxy it is
  ``TushareProxyClient``.
* the **transport descriptor** -- provenance only: the kind, the SDK version
  and the host actually reached.  ``TushareSource`` no longer guesses its
  supplier from ``isinstance(client, TushareProxyClient)``, because that test
  cannot tell an official session from one whose ``_DataApi__http_url`` was
  rewritten to a relay -- they are the same class.

Selection policy (§1.2): a published build must name its transport explicitly
and may only name ``relay``.  ``proxy`` is development/diagnostic only, and
``official`` additionally requires the two-step break-glass variable.  Nothing
here ever falls back on a credential or initialization failure.

``environ`` is a real parameter throughout, not decoration: these constructors
read credentials from the mapping they are given rather than from the process
environment, so tests and the readiness probe are pure functions of their
input.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import AuthenticationError, host_of
from stock_quant.data_sources.tushare_proxy import TushareProxyClient
from stock_quant.data_sources.tushare_relay import TushareRelayClient

LOGGER = logging.getLogger("stock_quant.data_sources.tushare_transport")

TRANSPORT_ENV = "TUSHARE_TRANSPORT"
OFFICIAL_PUBLISH_ENV = "TUSHARE_ALLOW_OFFICIAL_PUBLISH"
OFFICIAL_HOST = "api.waditu.com"

RELAY = "relay"
OFFICIAL = "official"
PROXY = "proxy"

_KINDS = (RELAY, OFFICIAL, PROXY)
#: The order an explicitly-permissive (development/diagnostic) caller tries.
#: ``proxy`` is deliberately absent: it answers with fallback semantics that
#: no automatic path may pick up behind the operator's back.
_AUTO_ORDER = (RELAY, OFFICIAL)
#: Labels used only when a caller injects a client that has no reachable URL
#: (test doubles).  ``resolve_transport`` never produces these.
_STUB_HOST = {OFFICIAL: OFFICIAL_HOST, PROXY: PROXY, RELAY: RELAY}


class CredentialsMissing(AuthenticationError):
    """A transport is not configured at all (as opposed to failing to start).

    Only this failure is fallback-worthy, and only inside the development
    auto-order: a *half*-configured or failing transport never falls back.
    """


@dataclass(frozen=True)
class TushareTransport:
    """Provenance for one resolved tushare transport."""

    kind: str
    client: Any
    sdk_version: str
    host: str

    @property
    def transport_id(self) -> str:
        """The path-safe identity of the answering party (design §2.3)."""
        return self.host

    def supplier_endpoint(self, endpoint: str) -> str:
        """The audited supplier label for one endpoint on this transport."""
        if self.kind == RELAY:
            return f"tushare_relay.{self.host}.{endpoint}"
        if self.kind == PROXY:
            return f"tushare_proxy.{endpoint}"
        return f"tushare.pro.{endpoint}"


def _setting(environ: Mapping[str, str], key: str) -> str:
    return str(environ.get(key, "")).strip()


def client_host(client: Any) -> str:
    """The host a client will reach, tolerating stubs that omit it.

    ``getattr`` with a default is what makes this safe: reading ``.host`` on a
    stub that has no ``base_url`` raises ``AttributeError`` *inside* the
    property, and only ``getattr`` swallows that.  Public because
    ``injected_transport`` (in ``tushare.py``) needs the same tolerance.
    """
    try:
        host = client.host
    except AttributeError:
        host = None
    if isinstance(host, str) and host:
        return host
    return host_of(getattr(client, "base_url", ""))


def official_transport(
    environ: Mapping[str, str], *, sdk: Any | None = None
) -> TushareTransport:
    """Build the direct session, refusing to label anything else as official."""
    token = _setting(environ, "TUSHARE_TOKEN")
    if not token:
        raise CredentialsMissing("the official transport requires TUSHARE_TOKEN")
    if sdk is None:
        import tushare as ts

        sdk = ts
    try:
        client = sdk.pro_api(token)
    except Exception:
        # The message may embed the token; never let it travel.
        raise AuthenticationError("Tushare client initialization failed") from None
    host = host_of(getattr(client, "_DataApi__http_url", ""))
    if host != OFFICIAL_HOST:
        raise AuthenticationError(
            "refusing to label this transport as official: its SDK base URL "
            f"host is {host!r}, not {OFFICIAL_HOST!r}"
        )
    return TushareTransport(
        kind=OFFICIAL,
        client=client,
        sdk_version=getattr(sdk, "__version__", "unknown"),
        host=host,
    )


def build_transport(
    kind: str,
    config: SourceConfig,
    *,
    sdk: Any | None = None,
    environ: Mapping[str, str] | None = None,
) -> TushareTransport:
    """Construct one transport by name, ignoring all publication policy.

    Pure mechanism, so diagnostic scripts can ask for a specific transport
    (including the proxy) without re-deriving the rules.  Credential and
    initialization failures raise ``AuthenticationError`` and never fall back.

    Credentials are read from ``environ`` and passed to the client
    constructors directly -- ``from_env`` would read ``os.environ`` and make
    this function untestable.
    """
    source = os.environ if environ is None else environ
    if kind == RELAY:
        base_url = _setting(source, "TUSHARE_RELAY_URL")
        token = _setting(source, "TUSHARE_RELAY_KEY")
        if not base_url or not token:
            raise CredentialsMissing(
                "the relay transport requires TUSHARE_RELAY_URL and "
                "TUSHARE_RELAY_KEY"
            )
        try:
            relay = TushareRelayClient(
                base_url, token, timeout_seconds=config.timeout_seconds, sdk=sdk
            )
        except Exception:
            # ``pro_api`` performs the handshake, so this is where a relay
            # outage surfaces.  It must arrive as AuthenticationError -- never
            # as a bare RuntimeError, and never with the SDK's own message,
            # which may embed the token.
            raise AuthenticationError(
                f"tushare relay initialization failed for host {host_of(base_url)!r}"
            ) from None
        transport = TushareTransport(
            kind=RELAY,
            client=relay.api,
            sdk_version=relay.sdk_version,
            host=client_host(relay),
        )
    elif kind == PROXY:
        base_url = _setting(source, "TUSHARE_PROXY_URL")
        api_key = _setting(source, "TUSHARE_PROXY_KEY")
        if not base_url or not api_key:
            raise CredentialsMissing(
                "the proxy transport requires TUSHARE_PROXY_URL and "
                "TUSHARE_PROXY_KEY"
            )
        # Not wrapped: this constructor only builds a requests.Session and sets
        # headers -- it performs no network I/O, so it has nothing to fail with.
        proxy = TushareProxyClient(
            base_url,
            api_key,
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
        )
        transport = TushareTransport(
            kind=PROXY,
            client=proxy,
            sdk_version=proxy.sdk_version,
            host=client_host(proxy),
        )
    elif kind == OFFICIAL:
        transport = official_transport(source, sdk=sdk)
    else:
        raise AuthenticationError(
            f"unknown {TRANSPORT_ENV}={kind!r} (expected one of: {', '.join(_KINDS)})"
        )
    LOGGER.info(
        "tushare transport: kind=%s host=%s sdk_version=%s",
        transport.kind,
        transport.host,
        transport.sdk_version,
    )
    return transport


def resolve_transport(
    config: SourceConfig,
    *,
    allow_auto_transport: bool = False,
    sdk: Any | None = None,
    environ: Mapping[str, str] | None = None,
) -> TushareTransport:
    """Resolve the transport a caller is allowed to use (design §1.2).

    ``allow_auto_transport`` is the development/diagnostic permission: it
    unlocks the ``relay -> official`` auto-order and the explicitly requested
    proxy.  A published build leaves it ``False`` and must name ``relay``.
    """
    source = os.environ if environ is None else environ
    requested = _setting(source, TRANSPORT_ENV).lower()

    if not requested:
        if not allow_auto_transport:
            raise AuthenticationError(
                f"{TRANSPORT_ENV} must be set explicitly for a published build "
                f"(expected {RELAY!r}); this path never falls back"
            )
        for kind in _AUTO_ORDER:
            try:
                return build_transport(kind, config, sdk=sdk, environ=source)
            except CredentialsMissing:
                continue
        raise AuthenticationError(
            "no tushare transport is configured: set TUSHARE_RELAY_URL and "
            "TUSHARE_RELAY_KEY (preferred), or TUSHARE_TOKEN"
        )

    if requested not in _KINDS:
        raise AuthenticationError(
            f"unknown {TRANSPORT_ENV}={requested!r} "
            f"(expected one of: {', '.join(_KINDS)})"
        )
    if requested == PROXY and not allow_auto_transport:
        raise AuthenticationError(
            f"{TRANSPORT_ENV}={PROXY} is development/diagnostic only and is "
            "never allowed for a published build"
        )
    if (
        requested == OFFICIAL
        and not allow_auto_transport
        and _setting(source, OFFICIAL_PUBLISH_ENV) != "1"
    ):
        raise AuthenticationError(
            f"{TRANSPORT_ENV}={OFFICIAL} requires "
            f"{OFFICIAL_PUBLISH_ENV}=1 for a published build (break-glass)"
        )
    return build_transport(requested, config, sdk=sdk, environ=source)
```

- [ ] **Step 6: 跑测试确认通过**

```bash
/home/ji/miniconda3/envs/py310/bin/python -m pytest \
  tests/unit/test_tushare_transport.py tests/unit/test_tushare_relay.py \
  tests/unit/test_tushare_proxy.py -v
```

Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/stock_quant/data_sources/tushare_transport.py \
        src/stock_quant/data_sources/base.py \
        src/stock_quant/data_sources/tushare_relay.py \
        src/stock_quant/data_sources/tushare_proxy.py \
        .env.example \
        tests/unit/test_tushare_transport.py tests/unit/test_tushare_relay.py
git commit -m "feat(provenance): resolve the tushare transport explicitly"
```

提交前扫一眼 `.env.example` 的新增行，确认 `TUSHARE_RELAY_KEY=` / `TUSHARE_PROXY_KEY=` 右侧仍是空的 —— 它是被跟踪的文件，写进真实密钥就等于提交泄漏。

---

### Task 3: `TushareSource` 持描述符；显式传输选择接进调用方

**Files:**
- Modify: `src/stock_quant/data_sources/tushare.py`
- Modify: `src/stock_quant/cli.py`（`data update` 打开传输日志）
- Modify: `project/verify_update_readiness.py:86-106,161-167`
- Modify: `project/check_data_sources.py:89-115`
- Modify: `project/collect_index_weight_membership.py:117`
- Modify: `tests/unit/test_tushare_proxy.py:271-285`
- Modify: `tests/unit/test_verify_update_readiness.py:14-22,64-80`
- Modify: `tests/integration/test_source_contracts.py:243-247`
- Modify: `tests/external/test_live_source_contracts.py:68,83`
- Test: `tests/unit/test_tushare_transport.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `resolve_transport` / `build_transport` / `TushareTransport` / `CredentialsMissing` / `client_host` / `_STUB_HOST`
- Produces: `TushareSource(config, client=None, *, transport=None, sdk=None, allow_auto_transport=False, environ=None)`；公开属性 `TushareSource.transport -> TushareTransport`；`injected_transport(client) -> TushareTransport`；`transport_issue(environ=None) -> ReadinessIssue | None`（取代 `token_issue`）

- [ ] **Step 1: 写失败的测试**

追加到 `tests/unit/test_tushare_transport.py`（该文件在 Task 2 已存在）。先把导入区补成：

```python
import logging
from datetime import date
from functools import partial

import pandas as pd
import pytest

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import (
    AuthenticationError,
    DataRequest,
    host_of,
)
from stock_quant.data_sources.tushare import TushareSource, injected_transport
from stock_quant.data_sources.tushare_proxy import TushareProxyClient
from stock_quant.data_sources.tushare_relay import TushareRelayClient
from stock_quant.data_sources.tushare_transport import (
    OFFICIAL,
    OFFICIAL_HOST,
    OFFICIAL_PUBLISH_ENV,
    PROXY,
    RELAY,
    TRANSPORT_ENV,
    build_transport,
    resolve_transport,
)
```

（Task 2 已建立上述大部分导入，此处只是归并补全；不要产生重复导入行。）

然后追加测试：

```python
def _daily_frame(ts_code: str, trade_date: str) -> pd.DataFrame:
    """A frame that satisfies the adapter's own contract validation."""
    return pd.DataFrame(
        {"ts_code": [ts_code], "trade_date": [trade_date], "close": [10.0]}
    )


class PlainClient:
    """A test double with the named methods and neither host nor sdk_version."""

    __version__ = "stub-1.0"

    def daily(self, ts_code=None, start_date=None, end_date=None):
        return _daily_frame(ts_code, start_date)


class RelayApi:
    """The official ``DataApi`` surface, answering a ``daily`` request.

    ``validate_supplier_frame`` requires the returned symbol set to equal the
    requested set and every ``trade_date`` to fall inside the window, so the
    fake has to answer with the requested code and a date inside the window.
    """

    def __init__(self, base_url: str) -> None:
        setattr(self, "_DataApi__http_url", base_url)

    def query(self, endpoint, **params):
        return _daily_frame(params.get("ts_code", ""), params.get("start_date", ""))

    def __getattr__(self, name):
        return partial(self.query, name)


class RelaySdk:
    __version__ = "fake-sdk-9.9"

    def pro_api(self, token="", timeout=30):
        return RelayApi(RELAY_URL)


def _request(endpoint: str = "daily", symbol: str = "000001.SZ") -> DataRequest:
    return DataRequest(endpoint, (symbol,), date(2026, 9, 1), date(2026, 9, 2))


def test_source_uses_the_resolved_transport_label_and_id():
    source = TushareSource(
        _config(),
        transport=resolve_transport(
            _config(),
            environ=dict(RELAY_ENV, **{TRANSPORT_ENV: RELAY}),
            sdk=RelaySdk(),
        ),
    )
    result = source.fetch(_request())
    assert result.metadata["supplier_endpoint"] == "tushare_relay.jiaoch.example.daily"
    assert result.metadata["transport_id"] == "jiaoch.example"
    assert source.transport.kind == RELAY


def test_the_relay_session_is_the_official_data_api_surface():
    # §1.1: the relay's request client IS the official DataApi with its base
    # URL rewritten, so the very same ``daily(...)`` call answers -- only the
    # provenance differs.
    source = TushareSource(
        _config(),
        transport=resolve_transport(
            _config(),
            environ=dict(RELAY_ENV, **{TRANSPORT_ENV: RELAY}),
            sdk=RelaySdk(),
        ),
    )
    result = source.fetch(_request())
    assert list(result.frame.columns) == ["ts_code", "trade_date", "close"]
    assert result.frame["ts_code"].tolist() == ["000001.SZ"]
    assert result.frame["trade_date"].tolist() == ["20260901"]


def test_source_requires_an_explicit_transport_on_the_published_path(monkeypatch):
    monkeypatch.delenv(TRANSPORT_ENV, raising=False)
    with pytest.raises(AuthenticationError) as raised:
        TushareSource(_config(), sdk=FakeSdk())
    assert TRANSPORT_ENV in str(raised.value)


def test_injected_client_is_described_without_isinstance_guessing():
    transport = injected_transport(PlainClient())
    assert transport.kind == OFFICIAL
    assert transport.transport_id == OFFICIAL_HOST
    assert transport.sdk_version == "stub-1.0"


def test_injected_proxy_client_keeps_its_own_host():
    transport = injected_transport(TushareProxyClient(PROXY_URL, "key"))
    assert transport.kind == PROXY
    assert transport.transport_id == "proxy.example"


def test_injected_relay_client_keeps_its_own_host():
    transport = injected_transport(TushareRelayClient(RELAY_URL, "key", sdk=FakeSdk()))
    assert transport.kind == RELAY
    assert transport.transport_id == "jiaoch.example"


def test_an_injected_relay_client_actually_answers_a_fetch():
    # ``TushareRelayClient`` only exposes ``query``; the adapter calls
    # ``daily(...)``.  Describing the wrapper without unwrapping ``.api``
    # would leave an AttributeError at fetch time, so this test exercises the
    # path end to end rather than only inspecting the descriptor.
    source = TushareSource(
        _config(), client=TushareRelayClient(RELAY_URL, "key", sdk=RelaySdk())
    )
    result = source.fetch(_request())
    assert result.frame["ts_code"].tolist() == ["000001.SZ"]
    assert source.transport.kind == RELAY
    assert source.transport.transport_id == "jiaoch.example"
    assert result.metadata["supplier_endpoint"] == "tushare_relay.jiaoch.example.daily"
```

替换 `tests/unit/test_verify_update_readiness.py` 的导入与两个凭据用例：把导入列表里的 `token_issue,` 换成 `transport_issue,`，并把第 64–80 行整体替换为：

```python
def test_transport_issue_flags_a_missing_transport() -> None:
    # The published path must name its transport; the readiness probe mirrors
    # that gate instead of re-deriving a weaker one.
    assert transport_issue({}) is not None
    assert transport_issue({"TUSHARE_TOKEN": "abc"}) is not None


def test_transport_issue_accepts_a_configured_relay() -> None:
    assert (
        transport_issue(
            {
                "TUSHARE_TRANSPORT": "relay",
                "TUSHARE_RELAY_URL": "https://relay.example/",
                "TUSHARE_RELAY_KEY": "relay-key",
            }
        )
        is None
    )


def test_transport_issue_rejects_the_proxy_and_unlogged_official() -> None:
    assert (
        transport_issue(
            {
                "TUSHARE_TRANSPORT": "proxy",
                "TUSHARE_PROXY_URL": "https://proxy.example/tushare/pro",
                "TUSHARE_PROXY_KEY": "proxy-key",
            }
        )
        is not None
    )
    assert (
        transport_issue({"TUSHARE_TRANSPORT": "official", "TUSHARE_TOKEN": "abc"})
        is not None
    )
```

- [ ] **Step 2: 跑测试确认失败**

```bash
/home/ji/miniconda3/envs/py310/bin/python -m pytest \
  tests/unit/test_tushare_transport.py tests/unit/test_verify_update_readiness.py -v
```

Expected: FAIL — `ImportError: cannot import name 'injected_transport'`

- [ ] **Step 3: 改写 `TushareSource`**

把 `src/stock_quant/data_sources/tushare.py` 的导入区替换为：

```python
from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import (
    ContractError,
    DataRequest,
    FetchResult,
    _utc_timestamp,
    request_key,
    request_metadata,
    translate_supplier_error,
    validate_supplier_frame,
)
from stock_quant.data_sources.tushare_proxy import TushareProxyClient
from stock_quant.data_sources.tushare_relay import TushareRelayClient
from stock_quant.data_sources.tushare_transport import (
    _STUB_HOST,
    OFFICIAL,
    PROXY,
    RELAY,
    TushareTransport,
    client_host,
    resolve_transport,
)
```

（`import os` 与 `AuthenticationError` 都不再需要 —— 凭据读取已移入 `tushare_transport`，留着重则 ruff 报 F401。）

把 `class TushareSource` 的类文档串、`__init__` 与 `_supplier_endpoint`（第 38–65 行）替换为：

```python
    """Fetch raw Tushare ``daily`` responses without column normalization.

    The transport is resolved once, explicitly, at construction (see
    :mod:`stock_quant.data_sources.tushare_transport`): a published build must
    name it and may only name the relay, while development and diagnostic
    callers opt into the ``relay -> official`` auto-order with
    ``allow_auto_transport=True``.  The request client is always an object
    that really has ``daily`` / ``index_daily`` / ``stock_basic``; provenance
    comes from ``self.transport``, never from the client's type -- an official
    session and a relay session are the same class, and only the base URL
    tells them apart.
    """

    name = "tushare"

    def __init__(
        self,
        config: SourceConfig,
        client: Any | None = None,
        *,
        transport: TushareTransport | None = None,
        sdk: Any | None = None,
        allow_auto_transport: bool = False,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        if transport is None:
            transport = (
                resolve_transport(
                    config,
                    allow_auto_transport=allow_auto_transport,
                    sdk=sdk,
                    environ=environ,
                )
                if client is None
                else injected_transport(client)
            )
        self._transport = transport
        self._client = transport.client
        self._sdk_version = transport.sdk_version

    @property
    def transport(self) -> TushareTransport:
        """The resolved transport, for diagnostics and tests."""
        return self._transport

    def _supplier_endpoint(self, endpoint: str) -> str:
        return self._transport.supplier_endpoint(endpoint)
```

在 `class TushareSource` 之后（模块级）追加：

```python
def injected_transport(client: Any) -> TushareTransport:
    """Describe a caller-injected request client (tests and local stubs).

    Injection is not a published path: the caller hands over the object, so a
    stub may have no URL to derive an identity from.  A client that declares a
    reachable ``host`` (the relay and proxy clients both do) is taken at its
    word; a stub that does not is labelled by its kind, and those kind labels
    (``proxy`` / ``relay`` / the official host) are documented as stub-only --
    ``resolve_transport`` never produces them.

    The two wrappers do not expose the same surface, so the *request client*
    is unwrapped per kind: ``TushareProxyClient`` answers the named endpoints
    itself, while ``TushareRelayClient`` only has ``query`` -- its named
    methods live on the SDK session it holds, which is why ``.api`` is used.
    """
    sdk_version = getattr(client, "sdk_version", None) or getattr(
        client, "__version__", "unknown"
    )
    if isinstance(client, TushareProxyClient):
        kind = PROXY
        session = client
    elif isinstance(client, TushareRelayClient):
        kind = RELAY
        session = client.api
    else:
        kind = OFFICIAL
        session = client
    host = client_host(client) or _STUB_HOST[kind]
    return TushareTransport(
        kind=kind, client=session, sdk_version=sdk_version, host=host
    )
```

- [ ] **Step 4: `data update` 打开传输日志**

`src/stock_quant/cli.py`：导入区加 `import logging`（按 ruff 排序放在 `import json` 之前）。在 `data_update` 函数体第一行插入 `_enable_transport_logging()`，并在 `data_update` 定义之前加入：

```python
def _enable_transport_logging() -> None:
    """Surface the resolved transport on the operator's terminal.

    The transport resolver logs one INFO line per resolution; without a
    handler it would be invisible, and the design requires the run log --
    not only the evidence chain -- to show which transport answered.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
```

- [ ] **Step 5: 改写 `project/verify_update_readiness.py` 的凭据闸门**

把 `token_issue`（第 86–106 行）整体替换为：

```python
def transport_issue(
    environ: Mapping[str, str] | None = None,
) -> ReadinessIssue | None:
    """Require the published build's own transport gate to pass.

    Delegates to the adapter's resolver rather than re-deriving the rules, so
    this probe can never be weaker than what ``data update`` will enforce: a
    missing explicit transport, a forbidden proxy, or an unlogged official
    fallback all surface here first, as a readiness issue rather than a
    traceback halfway through the run.
    """
    source = os.environ if environ is None else environ
    try:
        resolve_transport(SourceConfig(), environ=source)
    except AuthenticationError as error:
        return ReadinessIssue("TUSHARE_TRANSPORT_UNUSABLE", str(error))
    return None
```

导入区补：

```python
from stock_quant.data_sources.base import AuthenticationError
from stock_quant.data_sources.tushare_transport import resolve_transport
```

`main()` 里把 `missing_token = token_issue()` 改名为 `missing_transport = transport_issue()`，并同步改掉紧随其后的两处引用（`if missing_transport is not None: issues.append(missing_transport)`、`if missing_transport is None:`）。

- [ ] **Step 6: 改写两个 `project/` 调用方**

`project/check_data_sources.py`：第 92 行的 tushare 行改为

```python
            lambda: TushareSource(config, allow_auto_transport=True),
```

第 110 行的 proxy 行改为显式指定传输（proxy 不在自动序内，必须点明）：

```python
                lambda: TushareSource(
                    config, transport=build_transport("proxy", config)
                ),
```

导入区加 `from stock_quant.data_sources.tushare_transport import build_transport`。

`project/collect_index_weight_membership.py` 第 117 行改为：

```python
        # ``allow_auto_transport=True`` keeps this offline collector working
        # after the published path became strict.  Rewiring it to consume the
        # relay as its transport (and dropping the TUSHARE_TOKEN gate above) is
        # design spec §4, stage 4 -- not this change.
        source = TushareSource(SourceConfig(), allow_auto_transport=True)
```

- [ ] **Step 7: 改受影响的四处测试**

`tests/unit/test_tushare_proxy.py`：把 `test_tushare_source_env_selects_proxy_without_token`（第 271–285 行）整体替换为下面两条（**都不联网**：只断言解析结果；真正的 proxy 取数已由同文件既有的 `test_tushare_source_labels_proxy_transport` 用注入客户端覆盖）：

```python
def test_tushare_source_env_selects_proxy_without_token(monkeypatch):
    monkeypatch.setenv("TUSHARE_TRANSPORT", "proxy")
    monkeypatch.setenv("TUSHARE_PROXY_URL", BASE_URL)
    monkeypatch.setenv("TUSHARE_PROXY_KEY", "key-123")
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    source = TushareSource(SourceConfig(), allow_auto_transport=True)
    # The proxy pair alone selects the transport -- no token -- and this time
    # the adapter reports the choice instead of inferring it.
    assert source.transport.kind == "proxy"
    assert source.transport.transport_id == "proxy.example"


def test_injected_proxy_stub_is_labelled_by_kind():
    # A stub with no URL cannot name a host, so it is labelled by its kind;
    # that label is stub-only and resolve_transport never produces it.
    source = TushareSource(SourceConfig(), client=ProxyStub(_daily_frame([])))
    assert source.transport.kind == "proxy"
    assert source.transport.transport_id == "proxy"
```

（`ProxyStub.__init__` 只接一个 frame 且不调 `super()`，所以它没有 `base_url`；这正是要用 `_STUB_HOST` 兜底的那种客户端。）

`tests/integration/test_source_contracts.py`：在 `test_sdk_init_failure_does_not_expose_token`（约第 245 行）的 `monkeypatch.setenv("TUSHARE_TOKEN", token)` 之后加两行：

```python
    monkeypatch.setenv("TUSHARE_TRANSPORT", "official")
    monkeypatch.setenv("TUSHARE_ALLOW_OFFICIAL_PUBLISH", "1")
```

`tests/external/test_live_source_contracts.py`：第 68、83 行两处 `TushareSource(SourceConfig())` 都改为

```python
    result = TushareSource(SourceConfig(), allow_auto_transport=True).fetch(request)
```

（它们是联网契约测试，属于诊断用途。）

- [ ] **Step 8: 跑测试确认通过**

```bash
/home/ji/miniconda3/envs/py310/bin/python -m pytest \
  tests/unit/test_tushare_transport.py tests/unit/test_tushare_relay.py \
  tests/unit/test_tushare_proxy.py tests/unit/test_verify_update_readiness.py \
  tests/integration/test_source_contracts.py -v
```

Expected: PASS

- [ ] **Step 9: 提交**

```bash
git add src/stock_quant/data_sources/tushare.py src/stock_quant/cli.py \
        project/verify_update_readiness.py project/check_data_sources.py \
        project/collect_index_weight_membership.py \
        tests/unit/test_tushare_transport.py tests/unit/test_tushare_proxy.py \
        tests/unit/test_verify_update_readiness.py \
        tests/integration/test_source_contracts.py \
        tests/external/test_live_source_contracts.py
git commit -m "feat(provenance): require an explicit tushare transport"
```

---

### Task 4: 适配器产出 `transport_id`（先做，改动是惰性的）

**为什么先做这一步：** 三个适配器先开始带 `transport_id` 是**无害**的（此刻没有任何地方读它），随后 Task 5 才让 `RawStore.save` 强制要求它。这样每个提交都是绿的，不会出现"库里拒存一切"的中间态。

**Files:**
- Modify: `src/stock_quant/data_sources/base.py:88-109`（`request_metadata`）
- Modify: `src/stock_quant/data_sources/tushare.py`（两处 `request_metadata` 调用）
- Modify: `src/stock_quant/data_sources/akshare.py`（`_UPSTREAM_VENDOR` + `_transport_id` + 调用）
- Modify: `src/stock_quant/data_sources/baostock.py:86-92`
- Modify: `tests/integration/conftest.py:574,579,584`
- Modify: `tests/integration/test_data_pipeline.py:140`
- Modify: `tests/integration/test_acceptance_checks.py:90`
- Modify: `tests/unit/test_suspensions.py:307-310`
- Modify: `tests/unit/test_acceptance_service.py:97`
- Modify: `tests/unit/test_source_retry.py:34`
- Test: `tests/unit/test_source_transport_id.py`

**Interfaces:**
- Produces: `request_metadata(request, supplier_endpoint, sdk_version, *, transport_id, request_timestamp=None, response_timestamp=None) -> dict[str, str]`（`transport_id` 必填、keyword-only）；`stock_quant.data_sources.akshare._transport_id(supplier_endpoint) -> str`；每个 `FetchResult.metadata["transport_id"]`

- [ ] **Step 1: 写失败的测试**

创建 `tests/unit/test_source_transport_id.py`：

```python
"""Every adapter must name the party that answered (design §2.3)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_quant.config import SourceConfig
from stock_quant.data_sources.akshare import AkShareSource, _transport_id
from stock_quant.data_sources.baostock import BaoStockSource
from stock_quant.data_sources.base import DataRequest, request_metadata

REQUEST = DataRequest("daily", ("000001.SZ",), date(2026, 9, 1), date(2026, 9, 2))


def test_request_metadata_carries_the_transport_id():
    metadata = request_metadata(
        REQUEST,
        "tushare_relay.jiaoch.top.daily",
        "1.4.24",
        transport_id="jiaoch.top",
    )
    assert metadata["transport_id"] == "jiaoch.top"


def test_request_metadata_has_no_default_transport_id():
    # A silent fallback value would let two upstreams collapse into one
    # content-addressed path, so the parameter is mandatory.
    with pytest.raises(TypeError):
        request_metadata(REQUEST, "tushare.pro.daily", "1.4.24")


def test_akshare_id_names_both_the_vendor_and_the_interface():
    assert (
        _transport_id("akshare.stock_zh_index_daily_em")
        == "eastmoney.stock-zh-index-daily-em"
    )
    assert (
        _transport_id("akshare.stock_zh_index_hist_em")
        == "eastmoney.stock-zh-index-hist-em"
    )
    assert (
        _transport_id("akshare.stock_zh_index_daily") == "sina.stock-zh-index-daily"
    )
    assert (
        _transport_id("akshare.stock_zh_index_daily_tx")
        == "tencent.stock-zh-index-daily-tx"
    )
    assert (
        _transport_id("akshare.stock_fhps_detail_ths") == "ths.stock-fhps-detail-ths"
    )
    assert (
        _transport_id("akshare.stock_dividend_cninfo")
        == "cninfo.stock-dividend-cninfo"
    )


def test_two_interfaces_behind_one_vendor_never_collide():
    # Both serve the same logical ``index_history`` request from EastMoney; if
    # the fallback chain switches between them, byte-identical frames must
    # still land on two paths, each keeping its own label.
    daily = _transport_id("akshare.stock_zh_index_daily_em")
    hist = _transport_id("akshare.stock_zh_index_hist_em")
    assert daily != hist
    assert daily.startswith("eastmoney.") and hist.startswith("eastmoney.")


def test_akshare_unmapped_endpoint_still_gets_its_own_distinct_id():
    # A shared constant would be exactly the collision this design exists to
    # prevent, so an unmapped endpoint names itself.
    got = _transport_id("akshare.stock_zh_a_hist")
    assert got == "akshare.stock-zh-a-hist"
    assert got not in {"", "unknown", "akshare"}
    assert got != _transport_id("akshare.stock_zh_a_hist_tx")


class FakeAkClient:
    """Answers whichever ``index_history`` candidate is asked first.

    Every candidate returns the same frame, so the test does not depend on
    the order ``_INDEX_FALLBACKS`` happens to be in -- it asserts that the
    transport id agrees with whichever endpoint actually answered.
    """

    __version__ = "ak-1.0"

    def _frame(self):
        return pd.DataFrame(
            {"date": ["2026-09-01", "2026-09-02"], "close": [4000.0, 4010.0]}
        )

    def stock_zh_index_daily_em(self, **kwargs):
        return self._frame()

    def stock_zh_index_daily(self, **kwargs):
        return self._frame()

    def stock_zh_index_daily_tx(self, **kwargs):
        return self._frame()


def test_akshare_transport_id_agrees_with_the_endpoint_that_answered():
    source = AkShareSource(SourceConfig(), FakeAkClient())
    request = DataRequest(
        "index_history", ("000300.SH",), date(2026, 9, 1), date(2026, 9, 2)
    )
    result = source.fetch(request)
    endpoint = result.metadata["supplier_endpoint"]
    # The pairing is what matters: the id must name the interface that really
    # answered, never a fixed constant.
    assert result.metadata["transport_id"] == _transport_id(endpoint)
    vendor = result.metadata["transport_id"].split(".", 1)[0]
    assert vendor in {"eastmoney", "sina", "tencent"}


class FakeBaoSession:
    """A session whose ``login`` answers OK and whose query returns a frame."""

    __version__ = "bs-1.0"

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def login(self):
        return type("R", (), {"error_code": "0", "error_msg": ""})()

    def logout(self):
        return None

    def query_history_k_data_plus(self, *args, **kwargs):
        # BaoStockSource._to_frame passes a DataFrame straight through.
        return self._frame


def test_baostock_transport_id_names_the_supplier_itself():
    frame = pd.DataFrame(
        {
            "date": ["2026-09-01"],
            "code": ["sh.600000"],
            "open": ["1.0"],
            "high": ["2.0"],
            "low": ["0.9"],
            "close": ["1.5"],
            "preclose": ["0.9"],
            "volume": ["100"],
            "amount": ["150"],
            "pctChg": ["0.1"],
            "tradestatus": ["1"],
        }
    )
    source = BaoStockSource(SourceConfig(), FakeBaoSession(frame))
    request = DataRequest("daily", ("sh.600000",), date(2026, 9, 1), date(2026, 9, 2))
    result = source.fetch(request)
    assert result.metadata["transport_id"] == "baostock"
    assert result.metadata["supplier_endpoint"] == "baostock.query_history_k_data_plus"
```

> **实施者注意（落笔前先读代码）**：`FakeAkClient` / `FakeBaoSession` 的构造方式必须与两个适配器真实的
> `__init__` 签名一致 —— 用 `Read` 打开 `akshare.py` / `baostock.py` 确认参数名与客户端属性名。两处已知的变数：
>
> - **akshare 的回退顺序**：若 `_index_history` 对首个候选的异常**不**做捕获（而是直接抛出），上面的
>   `FakeAkClient` 会立刻把异常传出来 —— 此时**不要改适配器**，改为只让第一个候选作答（删掉其余方法），
>   断言仍然成立（`transport_id` 与 `supplier_endpoint` 由同一张表推出）。
> - **baostock 的帧形状**：`FakeBaoSession.query_history_k_data_plus` 直接返回 DataFrame，依据是
>   `BaoStockSource._to_frame` 对 DataFrame 原样透传。若实际返回类型是别的形状，按真实分支调整 fake
>   的返回值，**不要改适配器**。
>
> 若某一步的断言与真实代码不符，以真实代码为准调整测试；`_transport_id` 的映射表（Step 4）才是本任务
> 要固定的契约。

- [ ] **Step 2: 跑测试确认失败**

```bash
/home/ji/miniconda3/envs/py310/bin/python -m pytest tests/unit/test_source_transport_id.py -v
```

Expected: FAIL — `ImportError: cannot import name '_transport_id'`

- [ ] **Step 3: `request_metadata` 增加必填 `transport_id`**

把 `src/stock_quant/data_sources/base.py` 的 `request_metadata` 替换为：

```python
def request_metadata(
    request: DataRequest,
    supplier_endpoint: str,
    sdk_version: str,
    *,
    transport_id: str,
    request_timestamp: str | None = None,
    response_timestamp: str | None = None,
) -> dict[str, str]:
    """Build audit metadata while keeping tokens out of supplier results.

    ``transport_id`` is the path-safe identity of the party that actually
    answered -- the base-URL host for tushare, the winning upstream for
    akshare, the supplier's own name for baostock (design §2.3).  It has no
    default on purpose: a fallback value would let two different upstreams
    collapse into one content-addressed path and silently keep each other's
    ``supplier_endpoint``.
    """
    parameters = {
        "symbols": list(request.symbols),
        "start_date": request.start_date.isoformat(),
        "end_date": request.end_date.isoformat(),
        "params": request.params,
    }
    return {
        "request_parameters": json.dumps(parameters, sort_keys=True),
        "request_timestamp": request_timestamp or _utc_timestamp(),
        "response_timestamp": response_timestamp or _utc_timestamp(),
        "supplier_endpoint": supplier_endpoint,
        "sdk_version": sdk_version,
        "transport_id": transport_id,
    }
```

- [ ] **Step 4: 三个适配器传值**

`src/stock_quant/data_sources/tushare.py` —— `_fetch_symbol_series` 与 `_fetch_stock_basic` 两处调用各加一行（其余参数不动）：

```python
            metadata=request_metadata(
                request,
                self._supplier_endpoint(request.endpoint),
                self._sdk_version,
                transport_id=self._transport.transport_id,
                request_timestamp=request_timestamp,
                response_timestamp=response_timestamp,
            ),
```

（`_fetch_stock_basic` 那一处把 `self._supplier_endpoint(request.endpoint)` 换成
`self._supplier_endpoint("stock_basic")`。）

`src/stock_quant/data_sources/baostock.py` —— 加上 `transport_id="baostock",`：

```python
            metadata=request_metadata(
                request,
                "baostock.query_history_k_data_plus",
                self._sdk_version,
                transport_id="baostock",
                request_timestamp=request_timestamp,
                response_timestamp=response_timestamp,
            ),
```

`src/stock_quant/data_sources/akshare.py` —— 在 `class AkShareSource` 之前加入：

```python
#: The upstream vendor behind each AKShare endpoint this adapter can reach.
#: Provenance needs the answering *vendor* rather than the akshare wrapper,
#: because two vendors can return byte-identical frames (design §2.3) -- and
#: ``index_history`` really does switch between them at runtime.
_UPSTREAM_VENDOR = {
    "akshare.stock_zh_index_daily_em": "eastmoney",
    "akshare.stock_zh_index_hist_em": "eastmoney",
    "akshare.stock_zh_index_daily": "sina",
    "akshare.stock_zh_index_daily_tx": "tencent",
    "akshare.stock_info_a_code_name": "eastmoney",
    "akshare.stock_dividend_cninfo": "cninfo",
    "akshare.stock_fhps_detail_em": "eastmoney",
    "akshare.stock_fhps_detail_ths": "ths",
}


def _transport_id(supplier_endpoint: str) -> str:
    """The interface that answered, as a path-safe id (design §2.3).

    The vendor alone is **not** enough.  ``stock_zh_index_daily_em`` and
    ``stock_zh_index_hist_em`` are two different EastMoney interfaces serving
    the same logical ``index_history`` endpoint, and ``_index_history``'s
    fallback chain can switch between them run to run.  If both were labelled
    ``eastmoney``, two byte-identical frames would land on one
    content-addressed path and the second save would silently keep the first
    one's ``supplier_endpoint`` -- precisely the collision the transport layer
    exists to prevent.  So the id carries the vendor *and* the interface.

    An endpoint with no vendor mapping names itself under an ``akshare``
    prefix rather than sharing a constant.
    """
    slug = supplier_endpoint.rsplit(".", 1)[-1].replace("_", "-")
    vendor = _UPSTREAM_VENDOR.get(supplier_endpoint, "akshare")
    return f"{vendor}.{slug}"
```

并把 `fetch()` 末尾的 `metadata=request_metadata(...)` 替换为：

```python
            metadata=request_metadata(
                request,
                supplier_endpoint,
                self._sdk_version,
                transport_id=_transport_id(supplier_endpoint),
                request_timestamp=request_timestamp,
                response_timestamp=response_timestamp,
            ),
```

- [ ] **Step 5: 六个测试夹具补上 `transport_id`**

stub 源本身就是它自己的传输，所以用 `self.name`：

| 文件:行 | 改成 |
| --- | --- |
| `tests/integration/test_data_pipeline.py:140` | `metadata={"source": self.name, "sdk_version": "stub", "transport_id": self.name},` |
| `tests/integration/test_acceptance_checks.py:90` | 同上 |
| `tests/unit/test_acceptance_service.py:97` | 同上 |
| `tests/unit/test_suspensions.py:307-310` | 在字典里加一行 `"transport_id": self.name,` |
| `tests/unit/test_source_retry.py:34` | `metadata={"transport_id": self.name},` |

`tests/integration/conftest.py` 的三份夹具（第 574、579、584 行）改成真实值：

```python
            metadata={
                "source": "tushare",
                "sdk_version": "fixture",
                "transport_id": "api.waditu.com",
            },
```

（第 579 行的第二份 tushare 夹具同样改法；第 584 行的 akshare 夹具用
`metadata={"source": "akshare", "sdk_version": "fixture", "transport_id": "sina.stock-zh-index-daily"},`
—— 与适配器真实产出的形状保持一致。）

- [ ] **Step 6: 跑测试确认通过**

```bash
/home/ji/miniconda3/envs/py310/bin/python -m pytest \
  tests/unit/test_source_transport_id.py tests/unit/test_source_retry.py \
  tests/unit/test_tushare_proxy.py tests/unit/test_suspensions.py \
  tests/unit/test_acceptance_service.py -v
```

Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/stock_quant/data_sources/base.py src/stock_quant/data_sources/tushare.py \
        src/stock_quant/data_sources/akshare.py src/stock_quant/data_sources/baostock.py \
        tests/unit/test_source_transport_id.py tests/integration/conftest.py \
        tests/integration/test_data_pipeline.py tests/integration/test_acceptance_checks.py \
        tests/unit/test_suspensions.py tests/unit/test_acceptance_service.py \
        tests/unit/test_source_retry.py
git commit -m "feat(provenance): record each adapter's answering transport"
```

---

### Task 5: 快照寻址的 `transport_id` 层（§2.3）

**Files:**
- Modify: `src/stock_quant/data_sources/raw_store.py`
- Modify: `src/stock_quant/data_pipeline.py`（`_raw_snapshot_evidence_rows`）
- Modify: `src/stock_quant/research/acceptance/models.py:218-233`
- Modify: `tests/unit/test_raw_store.py`（既有 9 处夹具 + 追加新用例）
- Test: `tests/unit/test_raw_snapshot_binding.py`

**Interfaces:**
- Consumes: Task 4 的 `FetchResult.metadata["transport_id"]`
- Produces: `RESERVED_TRANSPORT_ID = "unknown"`；`RawSnapshotEvidence.transport_id: str | None = None`；路径 `data/raw/<source>/<endpoint>/<transport_id>/<request_key>/<file_sha256>/`；`verify_evidence` 在 `transport_id is None` 时回落四段旧路径；`RawSnapshotBinding.transport_id: str | None = None`

- [ ] **Step 1: 先修既有夹具（否则本任务无法变绿）**

`tests/unit/test_raw_store.py` 现有 9 处 `metadata=...` 都不带 `transport_id`，`save` 一强制就会全部失败。在导入区之后加入：

```python
#: Every store write must name its answering transport (design §2.3).  These
#: tests are about addressing, redaction and re-resolution, not provenance,
#: so they all use one fixed transport and let the interesting field vary.
TRANSPORT = "api.waditu.com"
_OMIT = object()


def _meta(**extra: Any) -> dict[str, Any]:
    return {"transport_id": TRANSPORT, **extra}
```

并把导入区改成（缺哪个补哪个）：

```python
import hashlib
import json
from dataclasses import replace
from typing import Any

import pandas as pd
import pytest

from stock_quant.data_sources.base import FetchResult
from stock_quant.data_sources.raw_store import (
    RESERVED_TRANSPORT_ID,
    RawSnapshotEvidence,
    RawStore,
    _sha256_file,
)
```

然后做三处替换：

1. **五个 `metadata={},`**（第 20、78、121、142、166 行）→ `metadata=_meta(),`（保持各自原有缩进）。
2. **第 51–56 行的字典**：

```python
            metadata={
                "request_parameters": {"symbol": "000001.SZ", "token": "secret-value"},
                "request_timestamp": "2026-09-03T10:00:00+00:00",
                "response_timestamp": "2026-09-03T10:00:01+00:00",
                "sdk_version": "1.2.3",
            },
```

改为：

```python
            metadata=_meta(
                request_parameters={"symbol": "000001.SZ", "token": "secret-value"},
                request_timestamp="2026-09-03T10:00:00+00:00",
                response_timestamp="2026-09-03T10:00:01+00:00",
                sdk_version="1.2.3",
            ),
```

3. **第 93、102 行**的 `metadata={"request_timestamp": "..."}` 分别改为
   `metadata=_meta(request_timestamp="2026-09-03T10:00:00+00:00"),` 与
   `metadata=_meta(request_timestamp="2026-09-03T10:00:01+00:00"),`。

跑一次确认既有用例仍全绿（此时 `transport_id` 还没被读取，夹具只是多带了字段）：

```bash
/home/ji/miniconda3/envs/py310/bin/python -m pytest tests/unit/test_raw_store.py -v
```

Expected: PASS — 9 passed

- [ ] **Step 2: 写失败的测试**

追加到 `tests/unit/test_raw_store.py`：

```python
#: design §1.1: the label names the *answering* path, so the official host keeps
#: ``tushare.pro.*`` and only the relay's host wears the relay prefix.  Deriving
#: the label from the transport id instead would paint the official host as a
#: relay -- the very attribution error this layer exists to prevent.
LABEL_FOR_HOST = {
    "api.waditu.com": "tushare.pro",
    "jiaoch.top": "tushare_relay.jiaoch.top",
}


def _transport_result(
    frame: pd.DataFrame,
    *,
    transport_id: object = _OMIT,
    request_key: str = "rk-1",
    endpoint: str = "daily",
) -> FetchResult:
    """A fetch result whose provenance fields are set (or deliberately not)."""
    metadata: dict[str, Any] = {"source": "tushare", "sdk_version": "1.4.24"}
    if transport_id is not _OMIT:
        metadata["transport_id"] = transport_id
        # Only the hosts this fixture knows get a label.  The deliberately
        # invalid transport ids below (blank, reserved, ``../etc``) are refused
        # by ``save`` before the label could matter, so they get none.
        if transport_id in LABEL_FOR_HOST:
            metadata["supplier_endpoint"] = f"{LABEL_FOR_HOST[transport_id]}.{endpoint}"
    return FetchResult(
        source="tushare",
        endpoint=endpoint,
        request_key=request_key,
        frame=frame,
        metadata=metadata,
    )


def test_two_transports_with_identical_bytes_coexist(tmp_path):
    """Byte-identical snapshots from different transports are two snapshots."""
    store = RawStore(tmp_path)
    frame = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]})
    official = store.save(_transport_result(frame, transport_id="api.waditu.com"))
    relay = store.save(_transport_result(frame, transport_id="jiaoch.top"))

    assert official.sha256 == relay.sha256  # same bytes...
    assert official.path != relay.path  # ...two snapshots
    assert official.path.parent.parent.name == "api.waditu.com"
    assert relay.path.parent.parent.name == "jiaoch.top"
    # The labels differ, and neither is derived from the other's identity.
    assert official.manifest["supplier_endpoint"] == "tushare.pro.daily"
    assert relay.manifest["supplier_endpoint"] == "tushare_relay.jiaoch.top.daily"


def test_the_relay_label_survives_an_existing_official_snapshot(tmp_path):
    """The reuse branch must not silently keep the first transport's label.

    This is the bug the extra path layer exists to fix: the manifest is
    content-addressed, so without a transport layer the second save found the
    first snapshot and returned its ``tushare.pro.daily``.
    """
    store = RawStore(tmp_path)
    frame = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]})
    store.save(_transport_result(frame, transport_id="api.waditu.com"))
    relay = store.save(_transport_result(frame, transport_id="jiaoch.top"))
    assert relay.manifest["supplier_endpoint"] == "tushare_relay.jiaoch.top.daily"


def test_save_refuses_a_missing_transport_id(tmp_path):
    store = RawStore(tmp_path)
    with pytest.raises(ValueError, match="transport_id"):
        store.save(_transport_result(pd.DataFrame({"a": [1]})))


@pytest.mark.parametrize("value", [None, "", "   ", RESERVED_TRANSPORT_ID])
def test_save_refuses_a_blank_or_reserved_transport_id(tmp_path, value):
    store = RawStore(tmp_path)
    with pytest.raises(ValueError):
        store.save(_transport_result(pd.DataFrame({"a": [1]}), transport_id=value))


def test_the_reserved_directory_never_appears_on_disk(tmp_path):
    store = RawStore(tmp_path)
    with pytest.raises(ValueError):
        store.save(
            _transport_result(
                pd.DataFrame({"a": [1]}), transport_id=RESERVED_TRANSPORT_ID
            )
        )
    assert not (tmp_path / "data" / "raw" / "tushare" / "daily" / "unknown").exists()


def test_a_transport_id_cannot_escape_the_store(tmp_path):
    store = RawStore(tmp_path)
    with pytest.raises(ValueError):
        store.save(_transport_result(pd.DataFrame({"a": [1]}), transport_id="../etc"))


def test_evidence_round_trips_through_the_transport_path(tmp_path):
    store = RawStore(tmp_path)
    snapshot = store.save(
        _transport_result(pd.DataFrame({"a": [1]}), transport_id="jiaoch.top")
    )
    evidence = RawSnapshotEvidence.from_snapshot(snapshot)
    assert evidence.transport_id == "jiaoch.top"
    assert store.verify_evidence(evidence).sha256 == snapshot.sha256


def test_legacy_five_field_evidence_still_resolves_the_old_layout(tmp_path):
    """Bindings written before transport tracking must keep resolving.

    ``DATASET_BUILD_CONTRACT_VERSION`` stays 1, so already-published datasets
    are read, not rejected: their snapshots sit on the four-segment path and
    their manifests carry no ``transport_id``.
    """
    store = RawStore(tmp_path)
    snapshot = store.save(
        _transport_result(pd.DataFrame({"a": [1]}), transport_id="jiaoch.top")
    )

    legacy_dir = tmp_path / "data" / "raw" / "tushare" / "daily" / "rk-legacy"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    target = legacy_dir / snapshot.sha256
    snapshot.path.rename(target)

    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest.pop("transport_id") == "jiaoch.top"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    evidence = RawSnapshotEvidence(
        source="tushare",
        endpoint="daily",
        request_key="rk-legacy",
        file_sha256=snapshot.sha256,
        manifest_sha256=_sha256_file(manifest_path),
    )
    assert evidence.transport_id is None
    assert store.verify_evidence(evidence).sha256 == snapshot.sha256
```

创建 `tests/unit/test_raw_snapshot_binding.py`：

```python
"""RawSnapshotBinding must read both payload shapes (design §2.3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stock_quant.research.acceptance.models import RawSnapshotBinding

DIGEST = "0" * 64


def test_a_five_field_binding_still_validates():
    binding = RawSnapshotBinding(
        source="tushare",
        endpoint="daily",
        request_key="rk",
        file_sha256=DIGEST,
        manifest_sha256=DIGEST,
    )
    assert binding.transport_id is None


def test_a_current_binding_carries_the_transport_id():
    binding = RawSnapshotBinding(
        source="tushare",
        endpoint="daily",
        request_key="rk",
        file_sha256=DIGEST,
        manifest_sha256=DIGEST,
        transport_id="jiaoch.top",
    )
    assert binding.transport_id == "jiaoch.top"


def test_an_unknown_field_is_still_rejected():
    with pytest.raises(ValidationError):
        RawSnapshotBinding(
            source="tushare",
            endpoint="daily",
            request_key="rk",
            file_sha256=DIGEST,
            manifest_sha256=DIGEST,
            provenance_key="jiaoch.top",
        )
```

- [ ] **Step 3: 跑测试确认失败**

```bash
/home/ji/miniconda3/envs/py310/bin/python -m pytest \
  tests/unit/test_raw_store.py tests/unit/test_raw_snapshot_binding.py -v
```

Expected: FAIL — `ImportError: cannot import name 'RESERVED_TRANSPORT_ID'`

- [ ] **Step 4: 改 `raw_store.py`**

在导入区之后加入常量：

```python
#: Reserved: it means "this snapshot predates transport tracking".  It is
#: never a valid value for a *new* snapshot, so the directory name can never
#: collide with the legacy layout (design §2.3).
RESERVED_TRANSPORT_ID = "unknown"
```

把 `RawSnapshotEvidence` 整体替换为（新字段放在**最后**，保持旧的位置参数调用仍然有效）：

```python
@dataclass(frozen=True)
class RawSnapshotEvidence:
    """Sanitized, re-resolvable pointer to one stored raw snapshot.

    Carries path-safe identifiers plus the content and manifest hashes only --
    never local paths, supplier metadata or reason prose -- so it can travel
    through dataset build evidence and later be verified back to the exact
    stored bytes via :meth:`RawStore.verify_evidence`.

    ``transport_id`` is the answering party (design §2.3).  ``None`` means the
    record predates transport tracking and resolves on the four-segment
    ``<source>/<endpoint>/<request_key>/<file_sha256>`` layout.
    """

    source: str
    endpoint: str
    request_key: str
    file_sha256: str
    manifest_sha256: str
    transport_id: str | None = None

    @classmethod
    def from_snapshot(cls, snapshot: RawSnapshot) -> "RawSnapshotEvidence":
        return cls(
            source=str(snapshot.manifest["source"]),
            endpoint=str(snapshot.manifest["endpoint"]),
            request_key=str(snapshot.manifest["request_key"]),
            file_sha256=snapshot.sha256,
            manifest_sha256=_sha256_file(snapshot.path / "manifest.json"),
            transport_id=snapshot.manifest.get("transport_id"),
        )
```

把 `save` 的方法头替换为：

```python
    def save(self, result: FetchResult) -> RawSnapshot:
        source = _path_component(result.source, "source")
        endpoint = _path_component(result.endpoint, "endpoint")
        transport_id = _transport_id(result.metadata.get("transport_id"))
        request_key = _path_component(result.request_key, "request key")
        destination_parent = (
            self._root / source / endpoint / transport_id / request_key
        )
        destination_parent.mkdir(parents=True, exist_ok=True)
```

并把其中的 `_manifest_for(...)` 调用替换为：

```python
                manifest = _manifest_for(
                    result, response_sha256, file_sha256, transport_id
                )
```

把 `verify_evidence` 的方法体替换为：

```python
    def verify_evidence(self, evidence: RawSnapshotEvidence) -> RawSnapshot:
        """Re-resolve an evidence pointer to the exact stored snapshot.

        A ``transport_id`` of ``None`` means the record predates transport
        tracking, and the pointer is resolved on the older four-segment
        layout; a present ``transport_id`` is re-validated exactly like a
        fresh one, so a legacy reader cannot be tricked into walking out of
        the store.
        """
        source = _path_component(evidence.source, "source")
        endpoint = _path_component(evidence.endpoint, "endpoint")
        request_key = _path_component(evidence.request_key, "request key")
        file_sha256 = _sha256_value(evidence.file_sha256)
        transport_id = (
            None
            if evidence.transport_id is None
            else _transport_id(evidence.transport_id)
        )
        path = (
            self._root / source / endpoint / request_key / file_sha256
            if transport_id is None
            else self._root
            / source
            / endpoint
            / transport_id
            / request_key
            / file_sha256
        )
        data_path = path / "data.parquet"
        manifest_path = path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("raw manifest is not a mapping")
        if _sha256_file(data_path) != file_sha256:
            raise ValueError("raw data hash mismatch")
        if _sha256_file(manifest_path) != evidence.manifest_sha256:
            raise ValueError("raw manifest hash mismatch")
        expected_fields = [
            ("source", source),
            ("endpoint", endpoint),
            ("request_key", request_key),
            ("file_sha256", file_sha256),
        ]
        if transport_id is not None:
            expected_fields.append(("transport_id", transport_id))
        for key, expected in expected_fields:
            if manifest.get(key) != expected:
                raise ValueError(f"raw manifest {key} mismatch")
        return RawSnapshot(path=path, sha256=file_sha256, manifest=manifest)
```

把 `_manifest_for` 的签名与返回值改掉（只加 `transport_id` 与那一行）：

```python
def _manifest_for(
    result: FetchResult,
    response_sha256: str,
    file_sha256: str,
    transport_id: str,
) -> dict[str, Any]:
    metadata = _redact(result.metadata)
    request_parameters = metadata.get("request_parameters", {})
    if isinstance(request_parameters, str):
        try:
            request_parameters = _redact(json.loads(request_parameters))
        except json.JSONDecodeError:
            pass
    metadata["request_parameters"] = request_parameters
    return {
        "source": result.source,
        "endpoint": result.endpoint,
        "supplier_endpoint": metadata.get("supplier_endpoint", result.endpoint),
        "transport_id": transport_id,
        "request_key": result.request_key,
        "request_parameters": request_parameters,
        "request_timestamp": metadata.get("request_timestamp"),
        "response_timestamp": metadata.get("response_timestamp"),
        "sdk_version": metadata.get("sdk_version", "unknown"),
        "row_count": len(result.frame),
        "schema": {column: str(dtype) for column, dtype in result.frame.dtypes.items()},
        "response_sha256": response_sha256,
        "file_sha256": file_sha256,
        "redacted": True,
        "metadata": metadata,
    }
```

在 `_path_component` 之后加入：

```python
def _transport_id(value: object) -> str:
    """Validate one snapshot's answering-party id (design §2.3).

    Missing, blank and the reserved word ``unknown`` are all rejected: a
    default would let two different upstreams share a content-addressed path
    and silently keep each other's ``supplier_endpoint``.
    """
    if not isinstance(value, str):
        raise ValueError(
            "raw metadata must carry a transport_id string, got "
            f"{type(value).__name__}"
        )
    text = _path_component(value.strip(), "transport id")
    if text == RESERVED_TRANSPORT_ID:
        raise ValueError(
            f"{RESERVED_TRANSPORT_ID!r} is reserved for snapshots that predate "
            "transport tracking and is never valid for a new snapshot"
        )
    return text
```

- [ ] **Step 5: 改去重键与验收绑定**

`src/stock_quant/data_pipeline.py` 的 `_raw_snapshot_evidence_rows`：

```python
def _raw_snapshot_evidence_rows(
    snapshots: Sequence[RawSnapshot],
) -> list[dict[str, str]]:
    """Sanitized raw-snapshot rows, deduplicated and deterministically sorted."""
    unique: dict[tuple[str, str, str, str, str], dict[str, str]] = {}
    for snapshot in snapshots:
        evidence = RawSnapshotEvidence.from_snapshot(snapshot)
        row = asdict(evidence)
        key = (
            evidence.source,
            evidence.endpoint,
            evidence.transport_id or "",
            evidence.request_key,
            evidence.file_sha256,
        )
        unique[key] = row
    return [unique[key] for key in sorted(unique)]
```

`src/stock_quant/research/acceptance/models.py` 的 `RawSnapshotBinding`：

```python
class RawSnapshotBinding(BaseModel):
    """The binding of one raw snapshot into dataset identity.

    The location fields resolve the snapshot under
    ``data/raw/<source>/<endpoint>/<transport_id>/<request_key>/<file_sha256>/``
    and ``manifest_sha256`` pins its manifest, so a binding resolves to exactly
    one stored snapshot instead of any file with the same bytes.

    ``transport_id`` is optional on purpose: bindings written before transport
    tracking existed carry five fields and resolve on the older four-segment
    ``<source>/<endpoint>/<request_key>/<file_sha256>`` layout.  Both shapes are
    readable under ``DATASET_BUILD_CONTRACT_VERSION = 1``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    request_key: str = Field(min_length=1)
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    transport_id: str | None = None
```

- [ ] **Step 6: 跑测试确认通过**

```bash
/home/ji/miniconda3/envs/py310/bin/python -m pytest \
  tests/unit/test_raw_store.py tests/unit/test_raw_snapshot_binding.py \
  tests/unit/test_acceptance_models.py -v
```

Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/stock_quant/data_sources/raw_store.py src/stock_quant/data_pipeline.py \
        src/stock_quant/research/acceptance/models.py tests/unit/test_raw_store.py \
        tests/unit/test_raw_snapshot_binding.py
git commit -m "feat(provenance): address raw snapshots by transport identity"
```

---

### Task 6: 端到端自证（验收 1、3、4、5）

**Files:**
- Test: `tests/integration/test_raw_provenance_chain.py`

**Interfaces:**
- Consumes: Task 1–5 的全部产物
- Produces: 一条集成测试，证明"证据链能自证用了哪个传输"、"新旧两种 payload 形状都能解析"、"`request_key` 未被污染"；外加上线当天的人工反向验证步骤

- [ ] **Step 1: 写测试**

创建 `tests/integration/test_raw_provenance_chain.py`：

```python
"""The evidence chain must prove which transport answered (design §2.2/§2.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from stock_quant.data_pipeline import (
    DATASET_BUILD_CONTRACT_VERSION,
    _raw_snapshot_evidence_rows,
)
from stock_quant.data_sources.base import FetchResult
from stock_quant.data_sources.raw_store import (
    RawSnapshotEvidence,
    RawStore,
    _sha256_file,
)
from stock_quant.research.acceptance.models import RawSnapshotBinding


#: What each host's snapshot must be labelled, per design §1.1.  The fixture
#: looks the label up rather than deriving it from the transport id: the two
#: are independent, and conflating them is how the official host would end up
#: wearing a relay label.
LABEL_FOR_HOST = {
    "api.waditu.com": "tushare.pro.daily",
    "jiaoch.top": "tushare_relay.jiaoch.top.daily",
}


def _result_for(frame, host, *, request_key="rk-1"):
    """A fetch result for one host, labelled the way the adapter would."""
    return FetchResult(
        source="tushare",
        endpoint="daily",
        request_key=request_key,
        frame=frame,
        metadata={
            "source": "tushare",
            "sdk_version": "1.4.24",
            "supplier_endpoint": LABEL_FOR_HOST[host],
            "transport_id": host,
        },
    )


def _resolve_label(store_root: Path, row: dict) -> str:
    """Walk build_config row -> validated binding -> manifest -> transport label."""
    binding = RawSnapshotBinding.model_validate(row)
    snapshot = RawStore(store_root).verify_evidence(
        RawSnapshotEvidence(**binding.model_dump())
    )
    return str(snapshot.manifest["supplier_endpoint"])


def test_evidence_rows_carry_every_transport_and_the_labels_resolve(tmp_path):
    store = RawStore(tmp_path)
    frame = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]})
    # Identical bytes, two transports, one request key: the case the whole
    # design exists for.  Each keeps its own label -- the relay gets the relay
    # label, and the official path stays ``tushare.pro.*``.
    snapshots = [
        store.save(_result_for(frame, "api.waditu.com")),
        store.save(_result_for(frame, "jiaoch.top")),
    ]
    rows = _raw_snapshot_evidence_rows(snapshots)
    assert len(rows) == 2
    assert {row["transport_id"] for row in rows} == {"api.waditu.com", "jiaoch.top"}
    assert all(row["request_key"] == "rk-1" for row in rows)

    labels = {row["transport_id"]: _resolve_label(tmp_path, row) for row in rows}
    assert labels == {
        "api.waditu.com": "tushare.pro.daily",
        "jiaoch.top": "tushare_relay.jiaoch.top.daily",
    }


def test_request_key_stays_a_pure_idempotency_key(tmp_path):
    # Transport identity must not leak into the request key, or "the same
    # request" would stop meaning one thing.
    store = RawStore(tmp_path)
    frame = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]})
    official = store.save(_result_for(frame, "api.waditu.com"))
    relay = store.save(_result_for(frame, "jiaoch.top"))
    assert official.manifest["request_key"] == relay.manifest["request_key"] == "rk-1"
    assert official.sha256 == relay.sha256


def test_a_five_field_row_resolves_the_legacy_layout(tmp_path):
    """A pre-transport build_config row must keep resolving to its snapshot."""
    store = RawStore(tmp_path)
    frame = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]})
    snapshot = store.save(_result_for(frame, "jiaoch.top"))

    legacy_dir = tmp_path / "data" / "raw" / "tushare" / "daily" / "rk-1"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    target = legacy_dir / snapshot.sha256
    snapshot.path.rename(target)
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("transport_id")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    row = {
        "source": "tushare",
        "endpoint": "daily",
        "request_key": "rk-1",
        "file_sha256": snapshot.sha256,
        "manifest_sha256": _sha256_file(manifest_path),
    }
    assert len(row) == 5
    assert _resolve_label(tmp_path, row) == "tushare_relay.jiaoch.top.daily"


def test_build_config_keeps_pipeline_contract_version_one():
    # Bumping it would fail acceptance for every already-published dataset:
    # checks.py compares this field for equality.
    assert DATASET_BUILD_CONTRACT_VERSION == 1
```

- [ ] **Step 2: 跑测试**

```bash
/home/ji/miniconda3/envs/py310/bin/python -m pytest tests/integration/test_raw_provenance_chain.py -v
```

Expected: PASS（若 Task 5 已正确落地）。FAIL 则回到 Task 5 修。

- [ ] **Step 3: 提交**

```bash
git add tests/integration/test_raw_provenance_chain.py
git commit -m "test(provenance): prove the evidence chain resolves each transport"
```

- [ ] **Step 4: 真实发布（人工，阶段 1 验收的一部分，不能由测试代替）**

> **`--root project` 是必须的。** `data_update` 的 `--root` 默认是 `.`（`cli.py:234`），而真实数据在 `project/data/` 下；不加这个参数会写到仓库根的新目录里，既不是本次发布、也不会被后续任何东西读到。

```bash
cd /home/ji/work/program/stock
set -a; . ./.env; set +a
export TUSHARE_TRANSPORT=relay
/home/ji/miniconda3/envs/py310/bin/python -m stock_quant data update --root project
```

Expected:
1. 终端出现 `INFO stock_quant.data_sources.tushare_transport: tushare transport: kind=relay host=...`；
2. 命令以 `PASS` 结束并打印新的 `dataset_version`。

- [ ] **Step 5: 反向验证（未指定 transport 必须失败且不动数据集）**

```bash
cd /home/ji/work/program/stock
set -a; . ./.env; set +a
env -u TUSHARE_TRANSPORT /home/ji/miniconda3/envs/py310/bin/python -m stock_quant data update --root project
```

Expected: 退出码 1，报错含 `TUSHARE_TRANSPORT must be set explicitly`，数据集版本不变。

- [ ] **Step 6: 从证据侧自证（不能只看终端）**

真实布局是 `project/data/standardized/<version>/`，版本号记在 `project/data/standardized/CURRENT` 里；`build_config` 不是独立文件，而是 `dataset_manifest.json` 的一个字段。下面这段把这三件事都按真实布局来读，不需要人工填版本号：

```bash
cd /home/ji/work/program/stock
/home/ji/miniconda3/envs/py310/bin/python - <<'PY'
import json
from pathlib import Path

from stock_quant.data_sources.raw_store import RawSnapshotEvidence, RawStore
from stock_quant.research.acceptance.models import RawSnapshotBinding

root = Path("project")
version = (root / "data" / "standardized" / "CURRENT").read_text().strip()
manifest = json.loads(
    (root / "data" / "standardized" / version / "dataset_manifest.json").read_text()
)
rows = manifest["build_config"]["raw_snapshots"]
store = RawStore(root)
labels = set()
for row in rows:
    if row["source"] != "tushare":
        continue
    binding = RawSnapshotBinding.model_validate(row)
    snapshot = store.verify_evidence(RawSnapshotEvidence(**binding.model_dump()))
    labels.add(snapshot.manifest["supplier_endpoint"])

print(f"version={version} tushare_rows={sum(r['source'] == 'tushare' for r in rows)}")
print(sorted(labels))
assert labels, "no tushare snapshots were bound"
assert all(label.startswith("tushare_relay.") for label in labels), labels
print(f"OK: all {len(labels)} distinct tushare labels resolve to the relay")
PY
```

Expected: 打印的标签全部以 `tushare_relay.` 开头，**无一为 `tushare.pro.`**。同理，`transport_id` 全部是 relay 的作答 host，没有 `api.waditu.com`。

（若断言失败，直接 `ls project/data/standardized/` 与 `ls project/data/standardized/<version>/` 核对实际布局，再改脚本——不要改断言。）

---

### Task 7: 阶段 2 —— 存量快照出处审计（§5）

**Files:**
- Create: `project/audit_raw_provenance.py`
- Test: `tests/unit/test_audit_raw_provenance.py`

**Interfaces:**
- Consumes: `<store_root>/data/raw/**/manifest.json`（四段旧布局与五段新布局都要能读）
- Produces: `UNKNOWN`、`INSTALLED`、`SnapshotRecord`、`SkippedManifest`、`Scan`、`Summary`、`scan(store_root) -> Scan`、`local_sdk_versions() -> dict[str, set[str]]`、`summarise(records, *, local_sdk_versions) -> Summary`、`report(records, *, skipped=(), today=None) -> str`
- **`store_root` 是数据仓库根，也就是 `project/`**，不是仓库根：真实数据在 `project/data/raw`。`--root` 默认 `project`，与 `cli.py` 的 `--root project` 同一约定。
- `scan` 不静默丢文件：读不了或不像 manifest 的 `manifest.json` 进 `Scan.skipped`，报告必须把它们列出来，否则总量会假装完整。

- [ ] **Step 1: 写失败的测试**

创建 `tests/unit/test_audit_raw_provenance.py`：

```python
"""Unit tests for the one-off raw provenance audit (design §5)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2] / "project"
sys.path.insert(0, str(PROJECT))

import audit_raw_provenance  # noqa: E402
from audit_raw_provenance import (  # noqa: E402
    INSTALLED,
    UNKNOWN,
    SkippedManifest,
    SnapshotRecord,
    report,
    scan,
    summarise,
)


def _write(root: Path, *parts: str, text: str) -> None:
    target = root.joinpath(*parts)
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(text, encoding="utf-8")


def _write_manifest(root: Path, *parts: str, manifest: dict) -> None:
    _write(root, *parts, text=json.dumps(manifest))


TUSHARE_MANIFEST = {
    "source": "tushare",
    "endpoint": "daily",
    "supplier_endpoint": "tushare_relay.jiaoch.top.daily",
    "sdk_version": "1.4.24",
    "transport_id": "jiaoch.top",
    "request_timestamp": "2026-09-12T00:00:00Z",
}


def test_scan_reads_both_path_shapes(tmp_path):
    raw = tmp_path / "data" / "raw"
    _write_manifest(
        raw,
        "tushare",
        "daily",
        "jiaoch.top",
        "rk",
        "aa" * 32,
        manifest=TUSHARE_MANIFEST,
    )
    _write_manifest(
        raw,
        "tushare",
        "daily",
        "rk",
        "bb" * 32,
        manifest={
            "source": "tushare",
            "endpoint": "daily",
            "supplier_endpoint": "tushare.pro.daily",
            "sdk_version": "1.4.29",
            "request_timestamp": "2026-09-10T00:00:00Z",
        },
    )
    result = scan(tmp_path)
    assert result.skipped == []
    assert len(result.records) == 2
    assert {record.transport_id for record in result.records} == {"jiaoch.top", None}
    assert {record.sdk_version for record in result.records} == {"1.4.24", "1.4.29"}


def test_scan_reports_what_it_could_not_read_instead_of_dropping_it(tmp_path):
    """A silently skipped manifest makes every total below it a lie."""
    raw = tmp_path / "data" / "raw"
    _write_manifest(
        raw, "tushare", "daily", "rk", "cc" * 32, manifest={"unrelated": True}
    )
    broken = raw / "tushare" / "daily" / "rk" / ("dd" * 32)
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")

    result = scan(tmp_path)
    assert result.records == []
    reasons = {skipped.path.name: skipped.reason for skipped in result.skipped}
    assert len(result.skipped) == 2
    assert all(skipped.path.is_absolute() for skipped in result.skipped)
    assert "not valid JSON" in reasons["manifest.json"]
    assert "no 'source' field" in reasons["manifest.json"]


def test_scan_returns_nothing_for_a_store_that_does_not_exist(tmp_path):
    result = scan(tmp_path)
    assert result.records == []
    assert result.skipped == []


def test_the_default_root_is_the_project_directory():
    # Real data lives in ``project/data/raw``; a repo-root default would find
    # nothing and report an empty audit as if it were the truth.
    assert audit_raw_provenance.PROJECT_ROOT == PROJECT
    assert (PROJECT / "data" / "raw").is_dir()
    assert audit_raw_provenance.DEFAULT_REPORT.parent == (
        PROJECT.parent / "docs" / "operations"
    )


def test_summarise_flags_environments_absent_from_this_interpreter():
    records = [
        SnapshotRecord("tushare", "daily", "tushare.pro.daily", "1.4.29", None, "e"),
        SnapshotRecord("tushare", "daily", "tushare_relay.x.daily", "1.4.24", "x", "e"),
    ]
    summary = summarise(records, local_sdk_versions={"tushare": {"1.4.24"}})
    provenance = {row["sdk_version"]: row["provenance"] for row in summary.sdk_rows}
    assert provenance["1.4.24"] == INSTALLED
    assert provenance["1.4.29"] == UNKNOWN
    assert summary.total == 2
    assert summary.by_source == {"tushare": 2}


def test_report_states_the_limit_of_what_it_can_conclude():
    records = [
        SnapshotRecord("tushare", "daily", "tushare.pro.daily", "1.4.29", None, "e"),
    ]
    text = report(records)
    assert "1.4.29" in text
    # The report must refuse to infer a provider from a label that has no
    # discriminating power, and must say so in so many words.
    assert "不对历史 provider 下结论" in text
    assert UNKNOWN in text


def test_report_does_not_claim_a_missing_environment_is_absent_from_the_machine():
    """``local_sdk_versions`` only sees the running interpreter.

    Concluding "不在本机" would require scanning other environments and caches,
    which this script does not do -- so it must not say it.
    """
    text = report(
        [SnapshotRecord("tushare", "daily", "tushare.pro.daily", "1.4.29", None, "e")]
    )
    assert "当前解释器未安装" in text
    assert "不在本机" not in text
    assert "不能在本机原样复现" not in text


def test_report_names_every_manifest_it_could_not_read():
    skipped = [
        SkippedManifest(
            path=Path("/store/data/raw/x/manifest.json"), reason="not valid JSON"
        )
    ]
    text = report([], skipped=skipped)
    assert "1 个 `manifest.json` 未能读取" in text
    assert "/store/data/raw/x/manifest.json" in text
    assert "not valid JSON" in text
```

- [ ] **Step 2: 跑测试确认失败**

```bash
/home/ji/miniconda3/envs/py310/bin/python -m pytest tests/unit/test_audit_raw_provenance.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'audit_raw_provenance'`

- [ ] **Step 3: 实现盘点脚本**

创建 `project/audit_raw_provenance.py`：

```python
#!/usr/bin/env python
"""One-off audit of what the existing raw snapshots say about their own origin.

Design spec §5.  This report is deliberately **not** an answer to "which
provider produced each snapshot": the historical manifests carry no
discriminating evidence, and the design refuses to pretend otherwise.  What it
*can* say is the distribution of producing environments -- which SDK versions
appear, and whether those versions are installed in the interpreter running this
script -- which decides whether these datasets can be rebuilt as-is, here and
now.

That last claim is deliberately narrow.  A version missing from this
interpreter is *not* evidence that it is absent from the machine: it may live in
another conda env, another virtualenv, or an uninstalled cache.  This script
scans none of those, so it says "当前解释器未安装" and stops there.

The ``tushare.pro.*`` label is the clearest case of why: it proves neither that
the producer was the official API nor that it was not, because the official SDK
and a relay that rewrote its base URL are the same client talking to a
different host.  The report therefore says "不对历史 provider 下结论".

Snapshots whose environment cannot be established are reported as ``unknown``.
That word is a *conclusion field* of this report and has nothing to do with the
reserved ``transport_id`` of design §2.3, which is a different concept about a
different layer.

Run from the repository root or the project directory:
    python project/audit_raw_provenance.py
    python project/audit_raw_provenance.py --no-report

Exit codes: 0 = the report was produced; 1 = no snapshots were found.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

#: The repository root.  Reports are committed there (``docs/operations``).
REPO_ROOT = Path(__file__).resolve().parents[1]
#: The data store root.  Real snapshots live in ``project/data/raw``, never in
#: ``<repo>/data/raw`` -- defaulting ``--root`` to the repository would scan an
#: empty tree and produce a confident, wrong report.
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_REPORT = (
    REPO_ROOT
    / "docs"
    / "operations"
    / f"raw-provenance-audit-{date.today().isoformat()}.md"
)

UNKNOWN = "unknown"
#: Present in the *running interpreter* -- not "present on this machine".
#: The two are different claims, and this script can only make the first.
INSTALLED = "installed"


@dataclass(frozen=True)
class SnapshotRecord:
    """One stored snapshot's self-reported origin."""

    source: str
    endpoint: str
    supplier_endpoint: str
    sdk_version: str
    transport_id: str | None
    request_timestamp: str | None


@dataclass(frozen=True)
class SkippedManifest:
    """A ``manifest.json`` that could not be counted."""

    path: Path
    reason: str


@dataclass(frozen=True)
class Scan:
    """Everything ``scan`` managed to read, and everything it did not.

    Both halves are returned.  Dropping the unreadable ones would make every
    count derived from ``records`` look complete when it is not.
    """

    records: list[SnapshotRecord]
    skipped: list[SkippedManifest]


@dataclass(frozen=True)
class Summary:
    total: int
    by_source: dict[str, int]
    sdk_rows: list[dict[str, object]]


def scan(store_root: Path) -> Scan:
    """Read every ``manifest.json`` below ``<store_root>/data/raw``.

    Both the four-segment legacy layout and the five-segment layout that
    carries ``transport_id`` are read, because the whole point is to describe
    what is already on disk.
    """
    raw = Path(store_root) / "data" / "raw"
    records: list[SnapshotRecord] = []
    skipped: list[SkippedManifest] = []
    if not raw.is_dir():
        return Scan(records=records, skipped=skipped)
    for manifest_path in sorted(raw.rglob("manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except OSError as exc:
            skipped.append(SkippedManifest(manifest_path, f"unreadable: {exc}"))
            continue
        except json.JSONDecodeError as exc:
            skipped.append(SkippedManifest(manifest_path, f"not valid JSON: {exc}"))
            continue
        if not isinstance(manifest, dict) or "source" not in manifest:
            skipped.append(SkippedManifest(manifest_path, "no 'source' field"))
            continue
        records.append(
            SnapshotRecord(
                source=str(manifest["source"]),
                endpoint=str(manifest.get("endpoint", UNKNOWN)),
                supplier_endpoint=str(
                    manifest.get("supplier_endpoint", manifest.get("endpoint", UNKNOWN))
                ),
                sdk_version=str(manifest.get("sdk_version", UNKNOWN)),
                transport_id=manifest.get("transport_id"),
                request_timestamp=manifest.get("request_timestamp"),
            )
        )
    return Scan(records=records, skipped=skipped)


def local_sdk_versions() -> dict[str, set[str]]:
    """The SDK versions installed in this interpreter, per source family."""
    versions: dict[str, set[str]] = {}
    for family, module_name in (
        ("tushare", "tushare"),
        ("akshare", "akshare"),
        ("baostock", "baostock"),
    ):
        try:
            module = __import__(module_name)
        except ImportError:  # pragma: no cover - depends on the environment
            versions[family] = set()
        else:
            versions[family] = {str(getattr(module, "__version__", UNKNOWN))}
    return versions


def summarise(
    records: list[SnapshotRecord], *, local_sdk_versions: dict[str, set[str]]
) -> Summary:
    """Group by (source, sdk_version) and classify each group's environment."""
    groups: Counter[tuple[str, str]] = Counter()
    for record in records:
        groups[(record.source, record.sdk_version)] += 1
    sdk_rows: list[dict[str, object]] = []
    for (source, sdk_version), count in sorted(groups.items()):
        installed = sdk_version in local_sdk_versions.get(source, set())
        sdk_rows.append(
            {
                "source": source,
                "sdk_version": sdk_version,
                "count": count,
                "provenance": INSTALLED if installed else UNKNOWN,
            }
        )
    return Summary(
        total=len(records),
        by_source=dict(Counter(record.source for record in records)),
        sdk_rows=sdk_rows,
    )


def report(
    records: list[SnapshotRecord],
    *,
    skipped: Sequence[SkippedManifest] = (),
    today: date | None = None,
) -> str:
    """Render the audit as a committable operations report."""
    day = (today or date.today()).isoformat()
    summary = summarise(records, local_sdk_versions=local_sdk_versions())
    lines = [
        f"# 存量 raw 快照出处审计（{day}）",
        "",
        "来源：`project/audit_raw_provenance.py`（设计规格 §5，落地阶段 2）。",
        "",
        "## 这次审计能得出什么、不能得出什么",
        "",
        "- **不能**判定每份快照的真实 provider。历史 manifest 没有留下判别依据，",
        "  本审计**不对历史 provider 下结论**。`tushare.pro.*` 这个标签既证明不了",
        "  是官方、也证明不了不是官方 —— 官方 SDK 与会话基址被改写的中转是同一个",
        "  客户端、不同的 host，标签本身不具判别力。",
        "- **能**判定产出环境的分布：哪些 SDK 版本出现在**当前解释器**、哪些不出现。",
        "- 本脚本只检查运行它的那个解释器。SDK 装在别的 conda 环境、别的虚拟环境或",
        "  缓存里，本报告看不见，因此**只断言「当前解释器未安装」**，不断言「本机",
        "  不存在」；也不据此断言数据集不能复现——那需要扫描多环境与缓存，本脚本",
        "  不做这件事。",
        "- 反查不出产出环境的记 `unknown`（本报告的**结论字段**，与 §2.3 的保留字",
        "  `transport_id` 无关），不猜、不重标。是否需要用新标注重建，由 owner 决定，",
        "  不在本脚本内自动进行。",
        "",
        "## 总量",
        "",
        f"共 {summary.total} 份快照。",
        "",
        "| 源 | 份数 |",
        "| --- | --- |",
    ]
    for source, count in sorted(summary.by_source.items()):
        lines.append(f"| `{source}` | {count} |")
    lines += [
        "",
        "## SDK 版本分布与产出环境",
        "",
        "| 源 | SDK 版本 | 份数 | 当前解释器 |",
        "| --- | --- | --- | --- |",
    ]
    for row in summary.sdk_rows:
        lines.append(
            f"| `{row['source']}` | `{row['sdk_version']}` | {row['count']} "
            f"| {row['provenance']} |"
        )
    unknown_rows = [row for row in summary.sdk_rows if row["provenance"] == UNKNOWN]
    lines += ["", "## 结论", ""]
    if unknown_rows:
        total_unknown = sum(int(row["count"]) for row in unknown_rows)
        lines.append(
            f"{total_unknown} 份快照的产出环境**当前解释器未安装**（SDK 版本组合与"
            "本解释器安装的不符），记 `unknown`。这不等于本机没有该环境：SDK 可能"
            "装在别的环境或缓存里，本脚本不扫描那些位置。是否需要用新标注重建，由"
            "owner 决定。"
        )
    else:
        lines.append("所有快照的 SDK 版本组合都出现在当前解释器里。")
    if skipped:
        lines += [
            "",
            "## 未能读取的 manifest",
            "",
            f"{len(skipped)} 个 `manifest.json` 未能读取，**未计入上面的总量**：",
            "",
        ]
        for item in skipped:
            lines.append(f"- `{item.path}` —— {item.reason}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    result = scan(args.root)
    if not result.records:
        print(f"no snapshots found under {args.root / 'data' / 'raw'}")
        return 1
    text = report(result.records, skipped=result.skipped)
    sys.stdout.write(text)
    if not args.no_report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
        print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

```bash
/home/ji/miniconda3/envs/py310/bin/python -m pytest tests/unit/test_audit_raw_provenance.py -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add project/audit_raw_provenance.py tests/unit/test_audit_raw_provenance.py
git commit -m "feat(provenance): audit the existing raw snapshots' origins"
```

- [ ] **Step 6: 跑审计并提交报告**

`--root` 默认就是 `project/`（与 `cli.py` 的 `--root project` 同一约定），所以不需要额外传参；脚本自身位置也从 `__file__` 推出，与当前工作目录无关。

```bash
cd /home/ji/work/program/stock
/home/ji/miniconda3/envs/py310/bin/python project/audit_raw_provenance.py
git add docs/operations/raw-provenance-audit-*.md
git commit -m "docs: record the raw snapshot provenance audit"
```

Expected: 报告中 tushare 与 akshare 各有不止一组 SDK 版本；当前解释器未安装的那几组标 `unknown`。若终端出现 `no snapshots found under ...`，说明 `--root` 指错了地方——真实快照在 `project/data/raw`，先 `ls project/data/raw` 核对，不要改断言。

---

## 收尾检查

- [ ] **回归：跑一遍被本计划改过的全部测试文件**

```bash
cd /home/ji/work/program/stock
/home/ji/miniconda3/envs/py310/bin/python -m pytest \
  tests/unit/test_probe_relay_substitution.py tests/unit/test_tushare_transport.py \
  tests/unit/test_tushare_relay.py tests/unit/test_tushare_proxy.py \
  tests/unit/test_source_transport_id.py tests/unit/test_raw_store.py \
  tests/unit/test_raw_snapshot_binding.py tests/unit/test_acceptance_models.py \
  tests/unit/test_verify_update_readiness.py tests/unit/test_source_retry.py \
  tests/unit/test_suspensions.py tests/unit/test_acceptance_service.py \
  tests/unit/test_audit_raw_provenance.py \
  tests/integration/test_source_contracts.py \
  tests/integration/test_raw_provenance_chain.py \
  tests/integration/test_acceptance_checks.py \
  tests/integration/test_data_pipeline.py -v
```

- [ ] **门禁**

```bash
cd /home/ji/work/program/stock
/home/ji/miniconda3/envs/py310/bin/python -m ruff check src project tests
/home/ji/miniconda3/envs/py310/bin/python -m ruff format --check \
  $(git diff --name-only main...HEAD | grep '\.py$')
```

第二条只检查**改动过的文件**；不要跑仓库级 `ruff format --check`。

- [ ] **对照验收标准自查**

| spec 验收条 | 本计划覆盖 |
| --- | --- |
| 1 阶段 0 先行，是硬闸门 | Task 1 Step 6/7 |
| 2 发布必须显式指定 transport，且只允许 relay；break-glass 放行时日志与 `supplier_endpoint` 两处都显示官方直连 | Task 2（`resolve_transport` + `caplog` 断言）+ Task 3（接线）+ Task 6 Step 5 |
| 3 证据链能自证用了 jiaoch | Task 6 Step 1（`_resolve_label`）+ Step 6（真实发布上的自证脚本） |
| 4 传输身份进入寻址，且新快照不出现 `unknown`；akshare 的 id 是回退链实际胜出者 | Task 5 + Task 4（`_UPSTREAM_VENDOR`）。标签按**作答 host** 取，不由 `transport_id` 推导 —— Task 5 与 Task 6 各自的 `LABEL_FOR_HOST` 就是这条断言的落点：官方 host 恒为 `tushare.pro.*`，只有 relay 的 host 带 relay 前缀 |
| 5 向后兼容不被破坏（旧五字段仍可解析并回落四段旧路径；开发/诊断走 official 时行为与改造前一致） | Task 5（旧路径回落）+ Task 6（五字段行用例）+ Task 3（`injected_transport` 保持 8 处注入调用点行为不变） |
| 7 探针有结论并驱动阻断动作 | **阶段 0 最小版本**：Task 1。常态化版本（四接口）属阶段 3 |
| 12 `audit_raw_provenance.py` 产出报告，不对历史 provider 下结论 | Task 7 |
| 6、8、9、10、11、13 | **不在本计划**：属阶段 3（§3 证伪体系）与阶段 4（§4 覆盖缺口与基准） |

## 不在本计划内（明确推迟）

| 项 | 归属 | 理由 |
| --- | --- | --- |
| `_REQUIRED_ROLE["akshare"] → False` | 阶段 4 | 与基准换主供同一次改动才有意义（spec §4） |
| 基准指数换 tushare `index_daily` 主供 + 规范化器 | 阶段 4 | 会改数值，历史结论需重跑，必须单独一轮 |
| `verify_transport_fidelity.py`（轴 1） | 阶段 3 | 依赖阶段 0 探针常态化 |
| `AkShareSource.stock_daily`（轴 2 跨厂商对照） | 阶段 3 | 需夹具先钉死单位与容差 |
| `collect_index_weight_membership.py` 接 relay、去掉 `TUSHARE_TOKEN` 闸门 | 阶段 4 | 本轮只保证它不被构造器改动打断 |
| `extend_history_offline.py` 加 `allow_auto_transport` | 不适用 | 它写数据集，属于 published 路径，**保持严格** |
| `TUSHARE_TOKEN` 轮换 | owner | 与本方案正交 |

### 一条未经核实、留给阶段 4 的风险

`src/stock_quant/data_model/normalize.py` 里 akshare 基准的 `volume` 被 ×100 而 `amount` 未见同比例处理，而 tushare 的
`_UNIT_FACTORS["tushare"] = (100, 1000)` 两者都换算。若成立，阶段 4 把基准主供切到 tushare 时 `amount` 会差 1000 倍，**比 spec
所记的「3 位小数 vs 4 位小数」影响大得多**。本计划不碰它，但阶段 4 的计划必须先核实这一点再定容差。
