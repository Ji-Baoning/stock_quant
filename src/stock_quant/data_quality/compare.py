"""Cross-source daily-bar comparison under design-spec §13.4 thresholds.

Rows may only be compared when they share an adjustment basis and units. Given
equal basis, an absolute difference of at most ¥0.01 is ``INFO``; a difference
above ¥0.01 whose relative difference exceeds 0.05% is ``WARNING``; a close
difference above 0.20% is ``ERROR`` for that security-date.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from stock_quant.data_quality.models import (
    CODE_ADJUSTMENT_BASIS_MISMATCH,
    CODE_CLOSE_DIFFERENCE,
    CODE_PRICE_DIFFERENCE,
    CODE_UNIT_MISMATCH,
    CODE_WITHIN_TOLERANCE,
    TABLE_DAILY_BAR,
    QualityIssue,
    Severity,
)

_COMPARE_FIELDS = ("open", "high", "low", "close")
_DEFAULT_ADJUSTMENT = "unadjusted"
_DEFAULT_UNITS = ("share", "yuan")


@dataclass(frozen=True)
class ComparisonThresholds:
    """The cross-source comparison thresholds, overridable per build."""

    absolute_price_tolerance: float = 0.01
    relative_warning: float = 0.0005  # 0.05%
    close_error_relative: float = 0.002  # 0.20%


DEFAULT_THRESHOLDS = ComparisonThresholds()


def compare_daily_sources(
    left: Mapping[str, object] | object,
    right: Mapping[str, object] | object,
    thresholds: ComparisonThresholds = DEFAULT_THRESHOLDS,
) -> list[QualityIssue]:
    """Compare two canonical daily bars and return the difference issues.

    ``left`` is the primary source and ``right`` the validation source; a row
    may be any record exposing fields by attribute or mapping key. Issues are
    returned in fixed field order (open, high, low, close). When the two bars
    cannot be compared (adjustment basis or declared units differ) a single
    explanatory ``WARNING`` is returned instead of any price comparison.
    """
    if _value(left, "adjustment", _DEFAULT_ADJUSTMENT) != _value(
        right, "adjustment", _DEFAULT_ADJUSTMENT
    ):
        return [
            _basis_issue(
                CODE_ADJUSTMENT_BASIS_MISMATCH,
                left,
                right,
                "adjustment",
            )
        ]
    left_units = (
        _value(left, "volume_unit", "share"),
        _value(left, "amount_unit", "yuan"),
    )
    right_units = (
        _value(right, "volume_unit", "share"),
        _value(right, "amount_unit", "yuan"),
    )
    if left_units != right_units:
        return [_basis_issue(CODE_UNIT_MISMATCH, left, right, "units")]

    symbol = _value(left, "symbol")
    trade_date = _value(left, "trade_date")
    issues: list[QualityIssue] = []
    for field in _COMPARE_FIELDS:
        left_value = _value(left, field)
        right_value = _value(right, field)
        if left_value is None or right_value is None:
            continue
        if left_value == right_value:
            continue
        absolute = abs(left_value - right_value)
        relative = absolute / abs(left_value) if left_value else float("inf")
        severity, code = _classify(field, absolute, relative, thresholds)
        issues.append(
            QualityIssue(
                severity=severity,
                code=code,
                table=TABLE_DAILY_BAR,
                symbol=symbol,
                trade_date=_as_date(trade_date),
                details={
                    "field": field,
                    "left": left_value,
                    "right": right_value,
                    "absolute_difference": absolute,
                    "relative_difference": relative,
                },
            )
        )
    return issues


def _classify(
    field: str,
    absolute: float,
    relative: float,
    thresholds: ComparisonThresholds,
) -> tuple[Severity, str]:
    if field == "close" and relative > thresholds.close_error_relative:
        return Severity.ERROR, CODE_CLOSE_DIFFERENCE
    if (
        absolute > thresholds.absolute_price_tolerance
        and relative > thresholds.relative_warning
    ):
        return Severity.WARNING, CODE_PRICE_DIFFERENCE
    return Severity.INFO, CODE_WITHIN_TOLERANCE


def _basis_issue(
    code: str, left: object, right: object, subject: str
) -> QualityIssue:
    return QualityIssue(
        severity=Severity.WARNING,
        code=code,
        table=TABLE_DAILY_BAR,
        symbol=_value(left, "symbol"),
        trade_date=_as_date(_value(left, "trade_date")),
        details={
            "subject": subject,
            "left": _value(left, "adjustment", _DEFAULT_ADJUSTMENT),
            "right": _value(right, "adjustment", _DEFAULT_ADJUSTMENT),
        },
    )


def _value(record: object, name: str, default: object = None) -> object:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None
