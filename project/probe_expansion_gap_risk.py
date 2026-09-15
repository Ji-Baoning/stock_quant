"""Measure how many blocking gaps the 659-symbol expansion would produce.

Status: diagnostic.

Stage B widens the tracked universe from 30 symbols to every listed CSI300
member.  One quality code decides whether that can publish at all:
``unexplained_primary_gap`` (``data_model/suspensions.py:148``) is an
``ERROR`` inside ``PUBLICATION_BLOCKING_CODES``, and ``DataPipeline.update``
publishes nothing -- not a partial dataset -- when any one of them appears.
With 629 new symbols over 2015-2026 there will be thousands of suspension
runs; a single chain break that no accepted corporate action explains blocks
the whole run after roughly an hour of fetching.

This probe answers that question *before* the budget is spent, by running the
production proof on a deterministic sample:

1. fetch ``daily`` for each sampled symbol through the same relay transport
   ``data update`` uses, plus the two akshare corporate-action endpoints;
2. prepare and reconcile them exactly as ``_refresh_corporate_actions`` does
   (``prepare_*_dividend_frame`` -> ``filter_corporate_actions_to_window`` ->
   ``normalize_corporate_actions`` **per symbol** -> reviews once over the
   union), because a symbol whose frames cannot be reconciled loses only its
   own actions, not the sample's;
3. feed each symbol's chain and the reconciled actions to ``suspension_rows``
   and count the issues it emits, by code.

The estimate is a **lower bound** on blocking issues: reconciliation only ever
drops actions (cross-source conflicts are quarantined, never accepted), and
dropping an action turns a proven run into an unexplained one.  It is not an
upper bound on anything -- un-sampled symbols may carry blocking issues too.

Read-only: no dataset is published, no raw snapshot is written, ``CURRENT``
is untouched, nothing enters ``data/standardized/``.
"""

from __future__ import annotations

import argparse
import os
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stock_quant.config import ProjectConfig, load_project_config
from stock_quant.data_model.corporate_actions import (
    RECONCILED_COLUMNS,
    CorporateActionResult,
    apply_corporate_action_reviews,
    filter_corporate_actions_to_window,
    normalize_corporate_actions,
    prepare_cninfo_dividend_frame,
    prepare_eastmoney_dividend_frame,
)
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.suspensions import suspension_rows
from stock_quant.data_quality.models import (
    CODE_SUSPENSION_ROW,
    CODE_SUSPENSION_RUN_UNVERIFIED,
    CODE_UNEXPLAINED_PRIMARY_GAP,
)
from stock_quant.data_sources.akshare import AkShareSource
from stock_quant.data_sources.base import DataRequest
from stock_quant.data_sources.tushare import TushareSource
from stock_quant.data_sources.tushare_transport import RELAY, TRANSPORT_ENV
from stock_quant.project_root import resolve_project_root
from stock_quant.safe_yaml import read_yaml

WINDOW_START = date(2015, 1, 5)
WINDOW_END = date(2026, 8, 28)
BLOCKING_CODES = frozenset({CODE_UNEXPLAINED_PRIMARY_GAP})
PROVEN_CODE = CODE_SUSPENSION_ROW
UNVERIFIED_CODE = CODE_SUSPENSION_RUN_UNVERIFIED
_ENDPOINTS = (
    ("cninfo_corporate_actions", "cninfo", prepare_cninfo_dividend_frame),
    ("eastmoney_corporate_actions", "eastmoney", prepare_eastmoney_dividend_frame),
)


@dataclass
class SampleOutcome:
    """One sampled symbol: its fetched frames and its proof result."""

    symbol: str
    board: str
    daily_rows: int = 0
    action_rows: int = 0
    codes: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    runs: list[str] = field(default_factory=list)
    chain: pd.DataFrame | None = None
    #: Set once the proof has run.  ``chain`` is released afterwards, so it
    #: cannot double as the success flag.
    proved: bool = False
    #: True when this symbol is still listed, i.e. it belongs to stage B's
    #: universe as well as to the wider master.
    listed: bool = True
    #: True when the symbol's own corporate-action frames could not be
    #: reconciled -- a WARNING in the pipeline, and here the reason its
    #: chain breaks have no actions to explain them.
    reconcile_failed: bool = False

    @property
    def blocking(self) -> int:
        return sum(self.codes[code] for code in BLOCKING_CODES)

    @property
    def ok(self) -> bool:
        return self.proved


def load_env(path: Path) -> dict[str, str]:
    """Parse simple KEY=VALUE lines; no shell evaluation."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        env[key.strip()] = value.strip().strip("\"'")
    return env


def credential_environ(root: Path) -> dict[str, str]:
    """``os.environ`` overlaid by the project's ``.env``, pinned to the relay.

    The transport is named explicitly rather than left to auto-selection: the
    probe's entire verdict is about the relay, and ``resolve_transport`` would
    otherwise refuse (or, with auto-selection, quietly answer from the
    official endpoint).  ``run`` still asserts the resolved kind afterwards,
    so this cannot mask a misconfiguration -- it only makes the intent
    explicit and keeps a re-run reproducible.
    """
    env = dict(os.environ)
    for candidate in (root / ".env", root.parent / ".env"):
        env.update(load_env(candidate))
    env[TRANSPORT_ENV] = RELAY
    return env


def new_symbols(master: pd.DataFrame, old_universe: Path | None) -> list[str]:
    """Master symbols the tracked universe does not already name."""
    known: set[str] = set()
    if old_universe is not None and old_universe.exists():
        document = read_yaml(old_universe)
        if isinstance(document, dict):
            for entry in document.get("entries") or ():
                if isinstance(entry, dict) and entry.get("symbol"):
                    known.add(str(entry["symbol"]))
    return sorted(
        str(symbol) for symbol in master["symbol"] if str(symbol) not in known
    )


def board_quotas(boards: Mapping[str, Sequence[Any]], size: int) -> dict[str, int]:
    """Seats per board, summing to ``size`` exactly (largest remainder).

    Every board keeps at least one seat, and the sum is forced back to
    ``size`` so no caller has to truncate the result -- truncating a
    symbol-sorted sample silently drops the highest-sorting board.
    """
    if size < len(boards):
        raise ValueError(f"sample size {size} cannot cover {len(boards)} boards")
    total = sum(len(group) for group in boards.values())
    share = {board: size * len(group) / total for board, group in boards.items()}
    quota = {board: max(1, int(share[board])) for board in boards}
    remainder_order = sorted(
        boards, key=lambda board: (-(share[board] - int(share[board])), board)
    )
    for board in remainder_order[: max(0, size - sum(quota.values()))]:
        quota[board] += 1
    while sum(quota.values()) > size:
        largest = max(quota, key=lambda board: (quota[board], board))
        quota[largest] -= 1
    return quota


def stratified_sample(
    master: pd.DataFrame, symbols: Sequence[str], size: int
) -> list[dict[str, Any]]:
    """A deterministic spread over board and listing history.

    Within each board the rows are sorted by ``list_date`` and then walked
    with a fixed stride, so the sample spreads over both board and history
    length rather than clustering at either end.  No RNG: the same inputs
    always select the same symbols.
    """
    wanted = set(symbols)
    rows = [row for row in master.to_dict("records") if str(row["symbol"]) in wanted]
    boards: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        boards.setdefault(str(row["board"]), []).append(row)
    quota = board_quotas(boards, size)
    selected: list[dict[str, Any]] = []
    for board in sorted(boards):
        group = sorted(
            boards[board],
            key=lambda row: (str(row.get("list_date") or ""), str(row["symbol"])),
        )
        seats = quota[board]
        if seats >= len(group):
            selected.extend(group)
            continue
        stride = len(group) / seats
        selected.extend(group[int(index * stride)] for index in range(seats))
    return sorted(selected, key=lambda row: str(row["symbol"]))


def fetch_actions(
    akshare: AkShareSource, symbol: str
) -> tuple[dict[str, list[pd.DataFrame]], list[str]]:
    """Fetch and prepare both corporate-action endpoints for one symbol.

    Mirrors ``_refresh_corporate_actions``' inner loop: a raw frame is
    prepared, clipped to the window and filed by source.  A failure is
    recorded and treated as an empty frame, exactly as the pipeline's
    best-effort role does.
    """
    frames: dict[str, list[pd.DataFrame]] = {"cninfo": [], "eastmoney": []}
    errors: list[str] = []
    for endpoint, source_name, prepare in _ENDPOINTS:
        request = DataRequest(endpoint, (symbol,), WINDOW_START, WINDOW_END, {})
        try:
            frame = akshare.fetch(request).frame
        except Exception as error:  # noqa: BLE001 - best-effort role
            errors.append(f"{endpoint}: {type(error).__name__}: {error}")
            continue
        if frame is None or frame.empty:
            continue
        frames[source_name].append(
            filter_corporate_actions_to_window(
                prepare(frame, symbol), WINDOW_START, WINDOW_END
            )
        )
    return frames, errors


def reconcile_by_symbol(
    frames_by_symbol: dict[str, dict[str, list[pd.DataFrame]]],
    config: ProjectConfig,
) -> tuple[pd.DataFrame, Counter, dict[str, str], str | None]:
    """Reconcile each symbol in isolation, then review the union.

    The per-symbol try/except is the pipeline's own shape: each symbol goes
    through ``_reconcile_action_frames`` separately, so a symbol whose frames
    cannot be reconciled loses *its own* actions (a WARNING) and no one
    else's.  Reconciling the whole sample in one call would let one such
    symbol erase every action and would overstate the blocking count.

    Returns the accepted actions, per-symbol accepted counts, the symbols
    whose reconciliation failed, and a note about the review pass.
    """
    accepted_frames: list[pd.DataFrame] = []
    failures: dict[str, str] = {}
    for symbol, frames in frames_by_symbol.items():
        try:
            result = normalize_corporate_actions(
                _concat(frames["cninfo"]), _concat(frames["eastmoney"])
            )
        except Exception as error:  # noqa: BLE001 - pipeline degrades to a warning
            failures[symbol] = f"{type(error).__name__}: {error}"
            continue
        if result.accepted is not None and not result.accepted.empty:
            accepted_frames.append(result.accepted)
    accepted = _concat(accepted_frames)
    if accepted is None:
        accepted = pd.DataFrame(columns=RECONCILED_COLUMNS)

    # One review pass over the union, as the pipeline does.  A review pins a
    # specific cross-source conflict, so it only applies when that symbol is
    # in the sample at all; the sampled reviews are named in the note rather
    # than silently skipped.
    in_window = [
        review
        for review in config.corporate_action_reviews
        if WINDOW_START <= review.ex_date <= WINDOW_END
    ]
    note: str | None = None
    if in_window:
        try:
            accepted = apply_corporate_action_reviews(
                CorporateActionResult(accepted=accepted, quarantined=pd.DataFrame()),
                [review.model_dump() for review in in_window],
            ).accepted
        except ValueError as error:
            note = (
                "项目 reviews "
                f"({', '.join(f'{r.symbol}@{r.ex_date}' for r in in_window)}) "
                f"未在样本内命中隔离行，本轮按未复核结果计：{error}"
            )
    counts: Counter = Counter()
    if accepted is not None and not accepted.empty:
        counts.update(str(symbol) for symbol in accepted["symbol"])
    return accepted, counts, failures, note


def _concat(frames: Any) -> pd.DataFrame | None:
    collected = [frame for frame in frames if frame is not None and not frame.empty]
    if not collected:
        return None
    return pd.concat(collected, ignore_index=True)


def upper_bound(observed: int, total: int, population: int) -> float:
    """95% upper bound on the population's blocking-issue count.

    With none seen, the rule of three gives ``3/n``; with some seen, a normal
    approximation.  Either way this is the number that matters: how many
    blocking issues the un-sampled majority might still hide.
    """
    if total == 0:
        return float("nan")
    if observed == 0:
        rate = 3.0 / total
    else:
        proportion = observed / total
        standard = (proportion * (1 - proportion) / total) ** 0.5
        rate = proportion + 1.96 * standard
    return min(rate, 1.0) * population


def _subset_line(label: str, subset: Sequence[SampleOutcome], population: int) -> str:
    """One line estimating a subset's blocking count from its sample."""
    proved = [item for item in subset if item.ok]
    blocking = sum(item.blocking for item in subset)
    if not proved:
        return f"- {label}：样本为空，无法估计"
    estimate = blocking / len(proved) * population
    bound = upper_bound(blocking, len(proved), population)
    return (
        f"- {label}：样本 {len(proved)} 只中 {blocking} 个阻断项 → "
        f"总体 {population} 只预计 **{estimate:.1f}** 个，95% 上界 **{bound:.1f}**"
    )


def render(
    outcomes: Sequence[SampleOutcome],
    *,
    population: int,
    listed_population: int,
    relay_host: str,
    reconcile_failures: Mapping[str, str],
    review_note: str | None,
) -> str:
    """The operator-facing report body."""
    ok = [item for item in outcomes if item.ok]
    failed = [item for item in outcomes if not item.ok]
    blocking = sum(item.blocking for item in outcomes)
    blocking_symbols = [item.symbol for item in outcomes if item.blocking]
    listed = [item for item in outcomes if item.listed]
    delisted = [item for item in outcomes if not item.listed]
    totals: Counter = Counter()
    for item in outcomes:
        totals.update(item.codes)
    lines = [
        "# 扩池停牌缺口风险实测（Stage B 前置）",
        "",
        f"- 运行时间：{datetime.now(timezone.utc).isoformat()}",
        f"- 窗口：{WINDOW_START.isoformat()}..{WINDOW_END.isoformat()}",
        f"- relay host：`{relay_host}`",
        f"- 抽样：{len(outcomes)} 只（日线成功 {len(ok)}，失败 {len(failed)}）",
        f"- 目标总体：master 相对现行 universe.yml 的 {population} 只新增标的"
        f"（其中在市 {listed_population} 只 = Stage B 的扩池对象，"
        f"退市 {population - listed_population} 只 = Stage A 的对象）",
        "",
        "## 结论",
        "",
    ]
    if failed:
        lines.append(
            f"- **INCONCLUSIVE**：{len(failed)} 只抽样日线取数失败，"
            f"其缺口风险未被测量（{', '.join(item.symbol for item in failed)}）。"
        )
    if review_note:
        lines.append(f"- reviews：{review_note}")
    lines.append(_subset_line("Stage B（在市新增）", listed, listed_population))
    lines.append(
        _subset_line("Stage A（退市新增）", delisted, population - listed_population)
    )
    if blocking:
        lines.append(
            f"- 抽样中共 **{blocking} 个阻断项**（`{CODE_UNEXPLAINED_PRIMARY_GAP}`），"
            f"分布在 {len(blocking_symbols)} 只：{', '.join(blocking_symbols)}。"
            " `data update` 遇到任意一个即整轮不发布——"
            "按上界，Stage B 单独跑通的前提并不牢靠。"
        )
    else:
        lines.append("- 抽样中 **0 个阻断项**。")
    lines += [
        "",
        "## 逐号结果",
        "",
        "| symbol | board | 状态 | 日线行 | 已接受动作 | 已证明停牌 "
        "| 未证实 | 阻断 | 错误 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for item in outcomes:
        lines.append(
            f"| {item.symbol} | {item.board} | "
            f"{'在市' if item.listed else '退市'} | {item.daily_rows} "
            f"| {item.action_rows} "
            f"| {item.codes[PROVEN_CODE]} | {item.codes[UNVERIFIED_CODE]} "
            f"| {item.blocking} | {'; '.join(item.errors)[:140] or ''} |"
        )
    lines += [
        "",
        "## 汇总",
        "",
        f"- `{PROVEN_CODE}`（已证明停牌，INFO）：{totals[PROVEN_CODE]}",
        f"- `{UNVERIFIED_CODE}`（窗口首尾无锚，WARNING）：{totals[UNVERIFIED_CODE]}",
        f"- `{CODE_UNEXPLAINED_PRIMARY_GAP}`（阻断，ERROR）："
        f"{totals[CODE_UNEXPLAINED_PRIMARY_GAP]}",
        f"- 单只公司行为对账失败（生产记为 WARNING，等于该只失去全部动作）："
        f"{len(reconcile_failures)}",
    ]
    if reconcile_failures:
        lines += ["", "### 对账失败明细", ""]
        for symbol in sorted(reconcile_failures):
            lines.append(f"- {symbol}: {reconcile_failures[symbol]}")
    if blocking_symbols:
        lines += ["", "### 阻断明细", ""]
        for item in outcomes:
            for run in item.runs:
                lines.append(f"- {item.symbol}: {run}")
    lines += [
        "",
        "## 口径",
        "",
        "本探针只读：不发布数据集、不写 raw 快照、不动 `CURRENT`。",
        "公司行为走生产同款 prepare → clip → **逐只** reconcile → reviews 链路；",
        "逐只隔离是刻意的——生产在 `_refresh_corporate_actions` 里逐只对账并吞掉异常，",
        "所以一只对账失败只会让它自己失去动作，不会波及其他标的。",
        "即便如此，估算值仍是阻断项的**下界**：对账只会丢弃动作（跨源冲突进隔离），",
        "而丢弃动作会把已证明的停牌变成未解释缺口；且未抽中的标的仍可能带阻断项。",
        "样本外的结论是估计，不是保证。",
    ]
    return "\n".join(lines) + "\n"


def run(
    root: Path,
    *,
    version: str | None,
    sample_size: int,
    pause_seconds: float,
    report_path: Path | None,
) -> int:
    config = load_project_config(root)
    tushare = TushareSource(config.sources["tushare"], environ=credential_environ(root))
    if tushare.transport.kind != "relay":
        print(
            f"refusing: transport is {tushare.transport.kind!r}, not 'relay'; "
            "this probe's verdict is about the relay only"
        )
        return 2
    relay_host = tushare.transport.host
    akshare = AkShareSource(config.sources["akshare"])

    reader = DatasetReader(root)
    resolved = version or DatasetPublisher(root).current().version
    with reader.open(resolved) as dataset:
        master = dataset.read("security_master")
        calendar = dataset.read("trading_calendar")
    open_days = sorted(
        pd.Timestamp(value).date() for value in calendar["calendar_date"]
    )

    targets = new_symbols(master, root / "configs" / "universe.yml")
    # Stage B can only carry symbols that are still listed; the delisted
    # remainder is stage A's business.  The two populations are estimated
    # separately because their samples differ in size and in history length.
    delisted_targets = {
        symbol
        for symbol in targets
        if _as_date(master.loc[master["symbol"] == symbol, "delist_date"].iloc[0])
        is not None
    }
    listed_population = len(targets) - len(delisted_targets)
    sample = stratified_sample(master, targets, sample_size)
    print(
        f"dataset={resolved} master={len(master)} new_symbols={len(targets)} "
        f"sampled={len(sample)} relay={relay_host}"
    )
    print(
        "sample boards="
        f"{dict(sorted(Counter(str(row['board']) for row in sample).items()))}"
    )

    outcomes: list[SampleOutcome] = []
    frames_by_symbol: dict[str, dict[str, list[pd.DataFrame]]] = {}
    for index, row in enumerate(sample, start=1):
        symbol = str(row["symbol"])
        outcome = SampleOutcome(
            symbol=symbol,
            board=str(row["board"]),
            listed=_as_date(row.get("delist_date")) is None,
        )
        outcomes.append(outcome)
        body = DataRequest(
            "daily", (symbol,), WINDOW_START, WINDOW_END, {"adjustment": "unadjusted"}
        )
        try:
            raw = tushare.fetch(body).frame
        except Exception as error:  # noqa: BLE001 - a probe reports, never raises
            outcome.errors.append(f"daily: {type(error).__name__}: {error}")
            raw = None
        if raw is not None and not raw.empty and "pre_close" in raw.columns:
            outcome.daily_rows = len(raw)
            outcome.chain = pd.DataFrame(
                {
                    "trade_date": pd.to_datetime(raw["trade_date"].astype(str)),
                    "close": pd.to_numeric(raw["close"]),
                    "pre_close": pd.to_numeric(raw["pre_close"]),
                }
            )
        elif raw is not None:
            outcome.errors.append("daily: empty response or no pre_close column")
        frames, action_errors = fetch_actions(akshare, symbol)
        frames_by_symbol[symbol] = frames
        outcome.errors.extend(action_errors)
        print(
            f"[{index}/{len(sample)}] {symbol} daily={outcome.daily_rows} "
            f"cn={sum(len(f) for f in frames['cninfo'])} "
            f"em={sum(len(f) for f in frames['eastmoney'])} "
            f"{'; '.join(outcome.errors)[:70]}"
        )
        if index < len(sample) and pause_seconds > 0:
            time.sleep(pause_seconds)

    accepted, accepted_counts, reconcile_failures, review_note = reconcile_by_symbol(
        frames_by_symbol, config
    )
    for outcome in outcomes:
        if outcome.symbol in reconcile_failures:
            outcome.reconcile_failed = True
            outcome.errors.append(f"reconcile: {reconcile_failures[outcome.symbol]}")
    if reconcile_failures:
        print(
            f"per-symbol reconciliation failures={len(reconcile_failures)}: "
            f"{', '.join(sorted(reconcile_failures))}"
        )
    if review_note:
        print(f"reviews: {review_note}")
    print(
        f"reconciled accepted actions={len(accepted)} "
        f"covering {len(accepted_counts)} symbols"
    )

    ingested = datetime.now(timezone.utc)
    window = [day for day in open_days if WINDOW_START <= day <= WINDOW_END]
    listing = {
        str(row["symbol"]): (
            _as_date(row.get("list_date")),
            _as_date(row.get("delist_date")),
        )
        for row in master.to_dict("records")
    }
    for outcome in outcomes:
        if outcome.chain is None:
            continue
        outcome.action_rows = accepted_counts.get(outcome.symbol, 0)
        list_date, delist_date = listing.get(outcome.symbol, (None, None))
        _, issues = suspension_rows(
            outcome.symbol,
            outcome.chain,
            window,
            list_date=list_date,
            delist_date=delist_date,
            actions=accepted,
            ingested_at=ingested,
        )
        for issue in issues:
            outcome.codes[issue.code] += 1
            if issue.code in BLOCKING_CODES:
                outcome.runs.append(str(issue.details.get("run")))
        outcome.proved = True
        outcome.chain = None  # release the frame; only the counts are needed

    body = render(
        outcomes,
        population=len(targets),
        listed_population=listed_population,
        relay_host=relay_host,
        reconcile_failures=reconcile_failures,
        review_note=review_note,
    )
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(body, encoding="utf-8")
        print(f"wrote {report_path}")
    blocking = sum(item.blocking for item in outcomes)
    failed = [item for item in outcomes if not item.ok]
    print(
        f"\nsampled={len(outcomes)} daily_failed={len(failed)} blocking={blocking} "
        f"proven={sum(item.codes[PROVEN_CODE] for item in outcomes)} "
        f"reconcile_failed={len(reconcile_failures)}"
    )
    if failed:
        return 2
    return 1 if blocking else 0


def _as_date(value: object) -> date | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("project"))
    parser.add_argument(
        "--version", default=None, help="Dataset version (default: CURRENT)"
    )
    parser.add_argument("--sample-size", type=int, default=60)
    parser.add_argument("--pause-seconds", type=float, default=0.3)
    parser.add_argument(
        "--report-path",
        type=Path,
        default=Path("docs/operations/2026-09-14-expansion-gap-risk-probe.md"),
    )
    args = parser.parse_args(argv)
    return run(
        resolve_project_root(args.root),
        version=args.version,
        sample_size=args.sample_size,
        pause_seconds=args.pause_seconds,
        report_path=args.report_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
