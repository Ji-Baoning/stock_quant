"""Strict walk-forward policies, canonicalization and hashing.

The first-phase discipline is one approved contract, not a configuration
surface: :class:`WalkForwardPolicy` fixes the ``fixed_calendar_oos_v1`` mode
(three calendar years of warmup, at least 756 confirmed warmup sessions, 60
stable-history sessions before the first OOS day, 12-month non-overlapping
OOS folds on a January 1 anchor, a per-fold account reset, OOS-only
aggregation and per-fold drawdown with cross-fold drawdown/Calmar
*forbidden*).  :class:`StabilityPolicy` is the versioned, hashable decision
rule (``stability-v1``): at least five executed folds, a positive-fold ratio
of at least 60% and a worst fold calendar return strictly above -10% per
declared cost scenario.

Every field is a ``Literal`` and the models are frozen with
``extra="forbid"``, so any attempt to retune the contract is a validation
error rather than a silent research-parameter change.  Both policies render
to deterministic canonical JSON whose SHA-256 (:func:`canonical_sha256`)
enters snapshots, the experiment identity and the stability report.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


def canonical_json_text(value: Any) -> str:
    """Canonical JSON text: sorted keys, compact UTF-8 separators, no NaN.

    Dates and enums must already be JSON-ready (use ``model_dump(mode="json")``
    for Pydantic models); ``allow_nan=False`` refuses any non-finite float so a
    hash can never silently depend on a NaN payload.
    """
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_sha256(value: Any) -> str:
    """SHA-256 of :func:`canonical_json_text` of ``value``."""
    return hashlib.sha256(canonical_json_text(value).encode("utf-8")).hexdigest()


class WalkForwardPolicy(BaseModel):
    """The frozen ``fixed_calendar_oos_v1`` calendar and aggregation contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["fixed_calendar_oos_v1"] = "fixed_calendar_oos_v1"
    calendar_anchor: Literal["jan_1"] = "jan_1"
    warmup_years: Literal[3] = 3
    warmup_unit: Literal["calendar_years"] = "calendar_years"
    min_warmup_trading_days: Literal[756] = 756
    required_stable_history_days_before_s: Literal[60] = 60
    oos_months: Literal[12] = 12
    step_months: Literal[12] = 12
    account_reset: Literal[True] = True
    aggregate: Literal["oos_only"] = "oos_only"
    return_aggregation: Literal["concatenate_oos_daily_returns"] = (
        "concatenate_oos_daily_returns"
    )
    global_drawdown_aggregation: Literal["forbidden"] = "forbidden"
    per_fold_drawdown: Literal["required"] = "required"
    partial_boundary_policy: Literal["record_not_evaluated"] = (
        "record_not_evaluated"
    )
    fold_status_policy: Literal["strict_market_calendar_v1"] = (
        "strict_market_calendar_v1"
    )


class StabilityPolicy(BaseModel):
    """The frozen, versioned stability decision rule (``stability-v1``).

    The rule is applied independently to every predeclared cost scenario and
    the final STABLE conclusion is their conjunction; thresholds are policy,
    hashable, and never a hidden code constant.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["stability-v1"] = "stability-v1"
    minimum_executed_folds: Literal[5] = 5
    minimum_positive_fold_ratio: Literal[0.60] = 0.60
    worst_fold_calendar_return_floor: Literal[-0.10] = -0.10
    annualization_sessions: Literal[252] = 252
    risk_free_rate: Literal[0.0] = 0.0
