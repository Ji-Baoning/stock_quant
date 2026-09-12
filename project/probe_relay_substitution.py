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
substituted an answer, or an error string diverged -- **do not start stage 1**;
2 = the gate could not be opened, either because the probe could not run (relay
or official credentials missing) or because some case produced no evidence
(the official side rate-limited or unreachable).  Only 0 opens stage 1: this is
a hard gate, so "unanswered" must never read as "passed".  Token values are
never printed.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
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


def classify_empty_probe(official: pd.DataFrame, relay: pd.DataFrame) -> str:
    """Verdict for one "official returns nothing here" question.

    The probe is only meaningful when the official answer really is empty; if
    it is not, the case tells us nothing and says so instead of guessing.
    """
    if not official.empty:
        return INCONCLUSIVE
    return AGREE_EMPTY if relay.empty else SUBSTITUTION


def classify_error_probe(official: str, relay: str) -> str:
    """Compare two failure messages; the relay must not invent its own."""
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
    if answer[0] == "error":
        return f"error: {answer[1][:120]}"
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
    official: Any, relay: Any, cases: Sequence[ProbeCase] = CASES
) -> list[ProbeResult]:
    """Run every case against both transports and classify the pair."""
    results: list[ProbeResult] = []
    for case in cases:
        official_answer = _read(official, case)
        relay_answer = _read(relay, case)
        results.append(
            ProbeResult(
                name=case.name,
                endpoint=case.endpoint,
                official=_describe(official_answer),
                relay=_describe(relay_answer),
                verdict=_verdict(case, official_answer, relay_answer),
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


def report(results: Sequence[ProbeResult], *, today: date | None = None) -> str:
    """Render the probe as a committable operations report."""
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
        "- `ERROR_DIFFERS` = 错误串与官方不一致。",
        "",
        "两者都属于 spec §3 ④ 的阻断条件。`INCONCLUSIVE` 表示官方侧本身没给出"
        "可用答案（限流、网络失败，或该问法官方本来就有数据），该用例**不构成"
        "证据** —— 既不算通过，也不算失败。**闸门只在全部用例都给出证据时才放行**，"
        "所以 `INCONCLUSIVE` 同样让阶段 1 保持关闭。",
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

    results = run_probe(official_client, relay_client)
    for result in results:
        print(
            f"{result.name:22s} {result.verdict:14s} "
            f"official={result.official} relay={result.relay}"
        )

    text = report(results)
    if not args.no_report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
        print(f"report: {args.report}")

    if blocking(results):
        print("BLOCKED: the relay substituted an answer; do not start stage 1")
        return EXIT_BLOCKED
    if not cleared(results):
        print(
            "NOT CLEARED: at least one case produced no evidence (official "
            "side rate-limited or unreachable); the stage 1 gate stays closed "
            "-- re-run the probe when the official API answers again"
        )
        return EXIT_NOT_CONFIGURED
    print("CLEAR: every case agreed; the stage 1 gate is open")
    return EXIT_CLEAR


if __name__ == "__main__":
    raise SystemExit(main())
