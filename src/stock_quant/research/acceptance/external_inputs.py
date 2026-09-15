"""External inputs: the operator-supplied official evidence an operator attests.

Three of the nine manual checks cannot be evidenced by this project alone (an
official exchange calendar, a second price source, official trading-rule
effective dates).  Whatever the operator supplies is copied into a
content-addressed, append-only store under the project root rather than
merely pointed at: a published record only pins the worksheet's hash, so an
excerpt deleted or swapped afterwards would leave an
``EXTERNAL_CORROBORATED`` claim nobody could re-check.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
import yaml

from stock_quant.data_quality.compare import (
    DEFAULT_THRESHOLDS,
    ComparisonThresholds,
    compare_daily_sources,
)
from stock_quant.data_quality.models import Severity
from stock_quant.research.acceptance.worksheet import (
    WorksheetError,
    external_inputs_root,
)
from stock_quant.safe_yaml import read_yaml

__all__ = [
    "EXCERPT_CODES",
    "CalendarComparison",
    "CalendarExcerpt",
    "PriceComparison",
    "RuleComparison",
    "RuleRow",
    "StoredBlob",
    "VersionFacts",
    "compare_calendar",
    "compare_for_code",
    "compare_price_sources",
    "compare_trading_rules",
    "load_blob",
    "parse_calendar_excerpt",
    "parse_rule_excerpt",
    "price_sample_frame",
    "rule_rows",
    "store_blob",
    "version_facts",
]


@dataclass(frozen=True)
class StoredBlob:
    """One stored external input: its project-relative path and its hash."""

    reference: str
    sha256: str
    name: str


def _safe_name(name: str) -> str:
    """A file name fit for the store, or a stable fallback."""
    candidate = Path(name).name
    if not candidate or candidate.startswith(".") or "\x00" in candidate:
        return "external-input"
    return candidate


def store_blob(project_root: Path, data: bytes, name: str) -> StoredBlob:
    """Copy bytes into the store under their SHA-256 and return the pointer.

    Identical content is stored exactly once, whatever name it arrives under;
    different content lands under a different digest and never overwrites.
    """
    root = Path(project_root).resolve()
    digest = hashlib.sha256(data).hexdigest()
    directory = external_inputs_root(root) / digest
    existing = (
        sorted(entry for entry in directory.iterdir()) if directory.is_dir() else []
    )
    if existing:
        chosen = existing[0]
    else:
        directory.parent.mkdir(parents=True, exist_ok=True)
        staging = directory.parent / f".{digest}.{uuid4().hex}.tmp"
        try:
            staging.mkdir()
            (staging / _safe_name(name)).write_bytes(data)
            if directory.exists():
                # A concurrent writer won the rename: reuse its copy.
                shutil.rmtree(staging, ignore_errors=True)
                chosen = sorted(directory.iterdir())[0]
            else:
                os.replace(staging, directory)
                chosen = directory / _safe_name(name)
        except OSError as error:
            shutil.rmtree(staging, ignore_errors=True)
            raise WorksheetError("external_input_invalid") from error
    return StoredBlob(
        reference=chosen.relative_to(root).as_posix(),
        sha256=digest,
        name=chosen.name,
    )


def load_blob(project_root: Path, reference: str) -> bytes:
    """Read one stored blob back, refusing any path outside the project root."""
    root = Path(project_root).resolve()
    try:
        candidate = (root / reference).resolve()
    except ValueError as error:
        raise WorksheetError("external_input_invalid") from error
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise WorksheetError("external_input_invalid")
    return candidate.read_bytes()


#: The two operator-only codes that consume an operator-supplied excerpt.
EXCERPT_CODES = ("exchange_calendar_sample", "trading_rule_effective_dates")


@dataclass(frozen=True)
class CalendarExcerpt:
    """One parsed official calendar excerpt."""

    open_days: frozenset[date]
    closed_days: frozenset[date]
    has_close_column: bool


def parse_calendar_excerpt(data: bytes) -> CalendarExcerpt:
    """Parse the official calendar format ``bootstrap_seed --calendar-csv`` uses.

    One ISO date per line, ``#`` comments, the first token being the date.  A
    second token ``1|0`` marks open/closed; without it the excerpt cannot tell
    "officially closed" from "the operator forgot to list the day", which is
    exactly why the strength then stays ``OPERATOR_ATTESTED``.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorksheetError("external_input_invalid") from error
    open_days: set[date] = set()
    closed_days: set[date] = set()
    has_close_column = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens = stripped.split()
        try:
            day = date.fromisoformat(tokens[0])
        except ValueError as error:
            raise WorksheetError("external_input_invalid") from error
        if len(tokens) >= 2:
            if tokens[1] not in ("0", "1"):
                raise WorksheetError("external_input_invalid")
            has_close_column = True
            (open_days if tokens[1] == "1" else closed_days).add(day)
        else:
            open_days.add(day)
    return CalendarExcerpt(
        open_days=frozenset(open_days),
        closed_days=frozenset(closed_days),
        has_close_column=has_close_column,
    )


@dataclass(frozen=True)
class CalendarComparison:
    """The two-way difference between the dataset calendar and the excerpt."""

    status: str
    has_close_column: bool
    dataset_open_official_absent: tuple[str, ...]
    official_open_dataset_absent: tuple[str, ...]
    official_closed_dataset_open: tuple[str, ...]

    @property
    def differences(self) -> tuple[str, ...]:
        """Every difference, in fixed column order."""
        return (
            *self.dataset_open_official_absent,
            *self.official_open_dataset_absent,
            *self.official_closed_dataset_open,
        )

    @property
    def corroborated(self) -> bool:
        """True only for a two-column excerpt with no difference at all."""
        return (
            self.status == "compared" and self.has_close_column and not self.differences
        )

    @property
    def queue_rows(self) -> tuple[str, ...]:
        """The differences an operator must look at before signing over them.

        An excerpt may legitimately cover only part of the window, so a
        difference is not an error by itself -- but it is exactly the thing the
        operator is signing about, so it enters the queue and has to be
        acknowledged one by one.
        """
        return self.differences


def compare_calendar(
    dataset_open_days: Sequence[date], excerpt: CalendarExcerpt | None
) -> CalendarComparison:
    """Compare the version's open days with the official excerpt, both ways."""
    if excerpt is None:
        return CalendarComparison("no_external_input", False, (), (), ())
    window = frozenset(dataset_open_days)
    listed = excerpt.open_days | excerpt.closed_days
    return CalendarComparison(
        status="compared",
        has_close_column=excerpt.has_close_column,
        dataset_open_official_absent=tuple(
            sorted(day.isoformat() for day in window if day not in listed)
        ),
        official_open_dataset_absent=tuple(
            sorted(day.isoformat() for day in excerpt.open_days if day not in window)
        ),
        official_closed_dataset_open=tuple(
            sorted(day.isoformat() for day in excerpt.closed_days if day in window)
        ),
    )


@dataclass(frozen=True)
class RuleRow:
    """One declared or official price-limit row."""

    board: str
    status: str
    effective_from: str
    rate: str


def _rule_key(row: RuleRow) -> str:
    return f"{row.board}|{row.status}|{row.effective_from}"


def _rate(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise WorksheetError("external_input_invalid") from error


def rule_rows(config_path: Path) -> tuple[RuleRow, ...]:
    """Every declared ``price_limits`` row, one per (board, status) pair."""
    try:
        payload = read_yaml(config_path)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise WorksheetError("external_input_invalid") from error
    limits = payload.get("price_limits") if isinstance(payload, dict) else None
    if not isinstance(limits, list):
        raise WorksheetError("external_input_invalid")
    rows: list[RuleRow] = []
    for entry in limits:
        if not isinstance(entry, dict):
            raise WorksheetError("external_input_invalid")
        boards = entry.get("boards")
        statuses = entry.get("status")
        boards = boards if isinstance(boards, list) else [boards]
        statuses = statuses if isinstance(statuses, list) else [statuses]
        for board in boards:
            for status in statuses:
                rows.append(
                    RuleRow(
                        board=str(board),
                        status=str(status),
                        effective_from=str(entry.get("effective_from")),
                        rate=str(entry.get("rate")),
                    )
                )
    return tuple(sorted(rows, key=_rule_key))


_RULE_EXCERPT_COLUMNS = (
    "board",
    "status",
    "effective_from",
    "rate",
    "source_url",
)


def parse_rule_excerpt(data: bytes) -> tuple[RuleRow, ...]:
    """Parse the official rule excerpt CSV the operator supplies."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorksheetError("external_input_invalid") from error
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or tuple(reader.fieldnames) != (_RULE_EXCERPT_COLUMNS):
        raise WorksheetError("external_input_invalid")
    rows: list[RuleRow] = []
    for record in reader:
        try:
            rows.append(
                RuleRow(
                    board=record["board"].strip(),
                    status=record["status"].strip(),
                    effective_from=record["effective_from"].strip(),
                    rate=record["rate"].strip(),
                )
            )
        except (AttributeError, KeyError) as error:
            raise WorksheetError("external_input_invalid") from error
    if not rows:
        raise WorksheetError("external_input_invalid")
    return tuple(rows)


@dataclass(frozen=True)
class RuleComparison:
    """How far an official rule excerpt covers the declared configuration."""

    status: str
    uncovered: tuple[str, ...]
    conflicting: tuple[str, ...]
    unclaimed: tuple[str, ...]

    @property
    def corroborated(self) -> bool:
        """True only when every declared row is covered and none disagrees."""
        return self.status == "compared" and not self.uncovered and not self.conflicting

    @property
    def queue_rows(self) -> tuple[str, ...]:
        """The declared rows an operator must look at before signing."""
        return tuple(sorted({*self.uncovered, *self.conflicting}))


def compare_trading_rules(
    declared: Sequence[RuleRow], excerpt: tuple[RuleRow, ...] | None
) -> RuleComparison:
    """Compare declared price limits against the official excerpt row by row."""
    if excerpt is None:
        return RuleComparison("no_external_input", (), (), ())
    official = {_rule_key(row): row for row in excerpt}
    uncovered: list[str] = []
    conflicting: list[str] = []
    for row in declared:
        key = _rule_key(row)
        found = official.get(key)
        if found is None:
            uncovered.append(key)
        elif _rate(found.rate) != _rate(row.rate):
            conflicting.append(key)
    declared_keys = {_rule_key(row) for row in declared}
    unclaimed = tuple(sorted(key for key in official if key not in declared_keys))
    return RuleComparison(
        status="compared",
        uncovered=tuple(sorted(uncovered)),
        conflicting=tuple(sorted(conflicting)),
        unclaimed=unclaimed,
    )


@dataclass(frozen=True)
class PriceComparison:
    """The cross-source verdict over the version's price sample."""

    status: str
    reason: str
    sources: tuple[str, ...]
    rows_compared: int
    exceeding: tuple[str, ...]


def price_sample_frame(daily: pd.DataFrame, open_days: Sequence[date]) -> pd.DataFrame:
    """The deterministic price sample: every bar on the window's edge days.

    Bounded by ``2 x symbols`` regardless of window length, and stable for a
    given version, so the same sample is reviewed every time.
    """
    days = sorted(set(open_days))
    if not days:
        return daily.iloc[0:0]
    edges = {days[0], days[-1]}
    # ``open_days`` are plain ``date`` objects while the stored column may be
    # ``datetime64[us]`` (DatasetReader) or object ``date`` (a direct Parquet
    # read): compare on the date part so neither shape depends on the implicit
    # cast ``isin`` is deprecating away.  A silently emptied sample would be
    # reported as ``single_price_source`` -- a false reason about the version
    # -- so this comparison must never be the thing that fails quietly.
    wanted = daily[pd.to_datetime(daily["trade_date"]).dt.date.isin(edges)]
    keys = [key for key in ("trade_date", "symbol", "source") if key in wanted.columns]
    return wanted.sort_values(keys, kind="stable")


def compare_price_sources(
    sample: pd.DataFrame,
    thresholds: ComparisonThresholds = DEFAULT_THRESHOLDS,
) -> PriceComparison:
    """Compare every sampled security-date across independent daily sources.

    Reuses the design-spec §13.4 thresholds.  Two ways to be incomparable, and
    both say so with a stable reason instead of reporting a vacuous pass: fewer
    than two independent daily sources in the version, or two sources that
    never cover the same ``(symbol, trade_date)`` -- the normal shape of a
    fixture where equities come from one source and benchmarks from another.  A
    ``compared`` verdict with ``rows_compared=0`` would read as "checked and
    clean" while nothing was checked at all.
    """
    sources = tuple(sorted({str(value) for value in sample["source"]}))
    if len(sources) < 2:
        return PriceComparison(
            status="not_comparable",
            reason="single_price_source",
            sources=sources,
            rows_compared=0,
            exceeding=(),
        )
    by_key: dict[tuple[str, date], dict[str, Any]] = {}
    for row in sample.to_dict("records"):
        by_key.setdefault((row["symbol"], row["trade_date"]), {})[
            str(row["source"])
        ] = row
    paired = {key: pair for key, pair in by_key.items() if len(pair) >= 2}
    if not paired:
        return PriceComparison(
            status="not_comparable",
            reason="no_paired_bars",
            sources=sources,
            rows_compared=0,
            exceeding=(),
        )
    compared = 0
    exceeding: list[str] = []
    for (symbol, day), pair in sorted(paired.items()):
        # The pair's own two sources, not a fixed global pair: with sources
        # that divide the symbol space there is no global pair to name.
        first, second = sorted(pair)[:2]
        issues = compare_daily_sources(pair[first], pair[second], thresholds)
        compared += 1
        if any(issue.severity is Severity.ERROR for issue in issues):
            exceeding.append(f"{symbol}@{day.isoformat()}")
    return PriceComparison(
        status="compared",
        reason="",
        sources=sources,
        rows_compared=compared,
        exceeding=tuple(sorted(exceeding)),
    )


@dataclass(frozen=True)
class VersionFacts:
    """The version-side facts each worksheet program area is built from.

    Read once per operation and shared by the candidate rows and the three
    comparators, so a worksheet can never be rendered from one read of the
    version while its comparison comes from another.
    """

    open_days: tuple[date, ...]
    rules: tuple[RuleRow, ...]
    price_sample: pd.DataFrame


def version_facts(
    project_root: Path, dataset_version: str, start: date, end: date
) -> VersionFacts:
    """Read one version's calendar, trading rules and price sample.

    The window is the same requested-start/resolved-end window the
    mechanisable evidence pack uses (``checks._window``), so a worksheet's
    program area and the evidence pack describe one window, never two.
    """
    root = Path(project_root).resolve()
    from stock_quant.data_model.dataset import DatasetReader
    from stock_quant.research.acceptance.checks import _open_days

    try:
        with DatasetReader(root).open(dataset_version) as dataset:
            daily = dataset.read("daily_bar")
            calendar = dataset.read("trading_calendar")
    except (OSError, KeyError, ValueError, TypeError) as error:
        raise WorksheetError("external_input_invalid") from error
    open_days = tuple(day for day in _open_days(calendar) if start <= day <= end)
    return VersionFacts(
        open_days=open_days,
        rules=rule_rows(root / "configs" / "trading_rules.yml"),
        price_sample=price_sample_frame(daily, open_days),
    )


def compare_for_code(
    code: str, facts: VersionFacts, data: bytes | None
) -> CalendarComparison | RuleComparison | PriceComparison:
    """The one comparison one operator-only code is judged by.

    ``prepare`` and ``confirm`` both call this, from the same facts and the
    same stored bytes: two call sites building their own comparison would
    eventually disagree, and the whole point of the queue is that the operator
    sees at signing time what they were shown at preparation time.  ``data`` is
    ``None`` whenever no excerpt was supplied -- including every call for
    ``cross_source_price_sample``, which never takes one.
    """
    if code == "exchange_calendar_sample":
        return compare_calendar(
            facts.open_days,
            parse_calendar_excerpt(data) if data is not None else None,
        )
    if code == "trading_rule_effective_dates":
        return compare_trading_rules(
            facts.rules,
            parse_rule_excerpt(data) if data is not None else None,
        )
    if code == "cross_source_price_sample":
        return compare_price_sources(facts.price_sample)
    raise WorksheetError("unknown_check_code")
