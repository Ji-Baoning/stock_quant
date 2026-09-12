"""Evaluate the two hard gates a real update must clear before acceptance.

Gate one: no unexplained missing bars over the window -- the check the
current dataset fails by 5,327 rows.  Gate two: trusted corporate-action
coverage tiling the window.  The script also previews the eight automated
acceptance checks, so a checklist is never prepared against a dataset that
cannot pass.  Read-only.

Both gates read the window from the same place the acceptance checker does
(``build_config.requested_start_date``/``resolved_end_date``), so a probe can
never silently disagree with the gate it previews; the spec window below is an
equality assertion, not the source of truth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.universe import Universe
from stock_quant.data_quality.raw_checks import classify_missing_row
from stock_quant.research.acceptance.checks import (
    _ACCEPTED_MISSING_CODES,
    AcceptanceCheckInput,
    _missing_row_failures,
    _open_days,
    _window,
    run_automated_checks,
)
from stock_quant.research.trust import evaluate_corporate_action_trust

ROOT = Path(__file__).resolve().parent
#: The window the update must be requested with; asserted, never assumed.
# The acceptance checker requires the requested window to start on or
# after the calendar's first open day, so the 2015 window is requested
# from 2015-01-05, the first open day (data coverage is identical:
# 2015-01-01..04 were non-trading days).
WINDOW_START = date(2015, 1, 5)
WINDOW_END = date(2026, 8, 28)


@dataclass(frozen=True)
class Gap:
    """One missing bar the acceptance gate would reject, with its class."""

    symbol: str
    trade_date: date
    code: str


@dataclass(frozen=True)
class GateVerdict:
    """Gate one's verdict: whether any gap remains, and a bounded sample."""

    passed: bool
    total: int
    sample: tuple[Gap, ...]


def _as_date(value: object) -> date | None:
    """Coerce one frame cell to a ``date``; missing/NaT reads as ``None``."""
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def window_open_days(
    calendar: pd.DataFrame, start: date, end: date
) -> list[date]:
    """The open days inside ``[start, end]``, ascending."""
    return [day for day in _open_days(calendar) if start <= day <= end]


def gap_details(
    daily: pd.DataFrame,
    master: pd.DataFrame,
    calendar: pd.DataFrame,
    start: date,
    end: date,
) -> list[Gap]:
    """Every missing bar the acceptance gate would reject, with its class.

    Reuses the acceptance checker's own classification vocabulary so this
    tool can never accept a bar the gate would reject.
    """
    facts = {
        str(row.get("symbol")): (
            _as_date(row.get("list_date")),
            _as_date(row.get("delist_date")),
        )
        for row in master.to_dict("records")
    }
    present = {
        (str(row.get("symbol")), _as_date(row.get("trade_date")))
        for row in daily.to_dict("records")
    }
    gaps: list[Gap] = []
    for symbol in sorted(facts):
        list_date, delist_date = facts[symbol]
        for day in window_open_days(calendar, start, end):
            if (symbol, day) in present:
                continue
            code = classify_missing_row(
                trade_date=day,
                list_date=list_date,
                delist_date=delist_date,
                is_trading_day=True,
                primary_present=False,
                validation_present=False,
            )
            if code not in _ACCEPTED_MISSING_CODES:
                gaps.append(Gap(symbol, day, code))
    return gaps


def bar_gate(
    daily: pd.DataFrame,
    master: pd.DataFrame,
    calendar: pd.DataFrame,
    start: date,
    end: date,
) -> GateVerdict:
    """Gate one, cross-checked against the acceptance checker's own count.

    The detail list is recomputed here so a failing dataset can be reported
    per symbol and per year; the total is then asserted equal to what
    ``_missing_row_failures`` counts, so this preview can never drift from
    the gate it previews.
    """
    details = gap_details(daily, master, calendar, start, end)
    reported = _missing_row_failures(
        daily, master, window_open_days(calendar, start, end)
    )
    total = next(
        (
            int(row[1])
            for row in reported
            if row[0] == "unexplained_missing_rows"
        ),
        0,
    )
    if total != len(details):
        raise AssertionError(
            f"gap recount {len(details)} disagrees with the acceptance "
            f"checker's {total}"
        )
    return GateVerdict(
        passed=not details, total=len(details), sample=tuple(details[:20])
    )


def corporate_action_gate(
    coverage: pd.DataFrame | None,
    symbols: tuple[str, ...],
    start: date,
    end: date,
) -> tuple[bool, tuple[tuple[str, str], ...]]:
    """Gate two: every universe symbol has trusted coverage tiling the window."""
    decision = evaluate_corporate_action_trust(coverage, symbols, start, end)
    return decision.trusted, tuple(
        (reason.symbol, reason.code) for reason in decision.reasons
    )


def main() -> int:
    symbols = Universe.from_yaml(ROOT / "configs" / "universe.yml").symbols
    version = DatasetPublisher(ROOT).current().version
    manifest = json.loads(
        (
            ROOT / "data" / "standardized" / version / "dataset_manifest.json"
        ).read_text(encoding="utf-8")
    )
    build = manifest.get("build_config")
    if not isinstance(build, dict) or "requested_start_date" not in build:
        print(
            f"dataset={version} carries no data-update build window "
            "(offline rebuild); nothing to gate"
        )
        return 1
    start, end = _window(build)
    if (start, end) != (WINDOW_START, WINDOW_END):
        print(
            f"build window {start}..{end} != spec window "
            f"{WINDOW_START}..{WINDOW_END}"
        )
        return 1
    print(
        f"dataset={version} window={start}..{end} universe_symbols={len(symbols)}"
    )
    with DatasetReader(ROOT).open(version) as dataset:
        daily = dataset.read("daily_bar")
        master = dataset.read("security_master")
        calendar = dataset.read("trading_calendar")
        coverage = (
            dataset.read("corporate_action_coverage")
            if "corporate_action_coverage" in dataset.tables
            else None
        )
    bars = bar_gate(daily, master, calendar, start, end)
    print(f"bar_gate passed={str(bars.passed).lower()} unexplained={bars.total}")
    for gap in bars.sample:
        print(f"  gap {gap.symbol} {gap.trade_date.isoformat()} {gap.code}")
    trusted, reasons = corporate_action_gate(coverage, symbols, start, end)
    print(
        f"corporate_action_gate trusted={str(trusted).lower()} "
        f"reasons={len(reasons)}"
    )
    for symbol, code in reasons[:20]:
        print(f"  coverage {symbol} {code}")
    checks = run_automated_checks(AcceptanceCheckInput(ROOT, version))
    for check in checks:
        print(f"check {check.code} {check.status.value} {check.summary}")
    failed = [
        check.code for check in checks if check.status.value == "FAIL"
    ]
    passed = bars.passed and trusted and not failed
    print(f"verdict passed={str(passed).lower()}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
