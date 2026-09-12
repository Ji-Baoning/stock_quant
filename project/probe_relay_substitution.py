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
* an unknown ``api_name``, whose error text must match verbatim.

Two cases are deliberately absent.  ``trade_cal`` is limited to one official
call per hour, and ``index_daily`` carries the same limit on a free official
token; in the live run of 2026-09-12 that limit -- not the relay -- was what
kept ``index_daily`` from producing evidence, on a gate that has to be
repeatable before it is believable.  A hard gate that a quota turns into a
random blocker is worse than no case at all.  ``index_daily``'s
empty-result and substitution behaviour moves to the stage 3 transport
fidelity script, where the official quota can be spent deliberately; it must
not block stage 0.  Add no case whose official answer depends on a quota this
token cannot sustain.

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
    ProbeCase("unknown_api_name", _NOT_A_REAL_API, {}, "error"),
)


def _load_env(path: Path) -> None:
    """Load simple KEY=VALUE lines without evaluating shell code.

    The file wins over an ambient value, and a disagreement is announced by
    variable *name* only.  ``os.environ.setdefault`` was wrong here: the probe
    is pointed at this file explicitly, so a stale exported ``TUSHARE_TOKEN``
    silently outvoted it.  The run then reported the official server refusing
    a credential, and the operator -- who had already replaced it in this very
    file -- went looking in the wrong place.
    """
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", maxsplit=1)
        key, value = key.strip(), value.strip().strip("\"'")
        previous = os.environ.get(key)
        if previous is not None and previous != value:
            print(f"environment: {key} is set and differs from {path}; the file wins")
        os.environ[key] = value


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
