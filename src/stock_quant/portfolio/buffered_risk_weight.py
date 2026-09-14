"""Two-stage buffered ranking and capped inverse-volatility weights.

``build_buffered_target`` turns one signal date's ``FactorResult`` plus the
trusted risk frame into the common, scenario-free construction target:

1. ``raw_momentum_rank`` orders factor-valid candidates by descending
   ``processed_value`` then ascending full symbol string -- never supplier
   row order;
2. risk-invalid rows are removed and the remaining order renumbered
   continuously into ``risk_eligible_rank``, so a risk-invalid top name never
   consumes an entry slot and the survivors shift forward;
3. eligible previous targets keep their seats through ``hold_rank``; the
   remaining seats fill only from ``risk_eligible_rank <= entry_rank``;
   candidates are never pulled from beyond the entry rank -- unfillable seats
   stay as cash;
4. every factor-valid candidate plus every previous target gets one audit
   row with a stable ``member_status`` (``retained|entered|exited|
   not_selected|risk_invalid``) and an explicit reason.

The membership state is fold-local by contract: the caller passes an empty
``previous_target_members`` on each fold's first signal and only the prior
*frozen common target members* afterwards -- never quantities, fills or any
scenario account state.

``allocate_capped_inverse_volatility`` distributes
``min(gross_exposure, count * max_single_weight)`` over the members by
inverse applied volatility, capping overweight names deterministically, then
quantizes every weight down to ``weight_quantum`` and redistributes the
residual quanta in ascending symbol order (never past the cap).  The
unallocatable residue returns as cash, so ``sum(weights) + cash`` equals the
gross exposure exactly in ``Decimal`` arithmetic.
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Mapping, Sequence

import pandas as pd

from stock_quant.factors.models import FactorResult
from stock_quant.portfolio.buffered_models import (
    MEMBER_STATUS_ENTERED,
    MEMBER_STATUS_EXITED,
    MEMBER_STATUS_NOT_SELECTED,
    MEMBER_STATUS_RETAINED,
    MEMBER_STATUS_RISK_INVALID,
    PORTFOLIO_CONSTRUCTION_COLUMNS,
    BufferedRiskWeightedPolicy,
    PortfolioConstructionResult,
)
from stock_quant.research.walk_forward.policy import canonical_sha256

#: The canonical rule name this module builds targets for.
BUFFERED_RULE_NAME = "buffered_risk_weighted"

#: Stable audit reasons beside the ``member_status`` vocabulary.
REASON_RETAINED = "retained_through_hold_rank"
REASON_ENTERED = "entered_from_entry_ranks"
REASON_LEFT_POOL = "left_point_in_time_pool"
REASON_FACTOR_INVALID = "factor_invalid"
REASON_RANK_BELOW_HOLD = "rank_below_hold_rank"
REASON_BEYOND_TARGET_COUNT = "beyond_target_count"
REASON_BEYOND_ENTRY_RANK = "beyond_entry_rank"

_DEFAULT_FALLBACK_REASON = "untrusted_missing_observation"

_PRECISION = 50


def buffered_portfolio_rule_version(policy: BufferedRiskWeightedPolicy) -> str:
    """The canonical policy JSON hash -- the rule's ``portfolio_rule_version``.

    ``{"name": ..., **policy}`` is exactly the canonical content of
    ``BufferedRiskWeightedPortfolioRule.model_dump(mode="json")``, so this
    matches the strategy snapshot's ``portfolio_rule_version`` for the same
    parameters; it is never a handwritten label.
    """
    payload = {"name": BUFFERED_RULE_NAME, **policy.model_dump(mode="json")}
    return canonical_sha256(payload)


def build_buffered_target(
    *,
    factors: FactorResult,
    risks: pd.DataFrame,
    previous_target_members: Sequence[str],
    policy: BufferedRiskWeightedPolicy,
) -> PortfolioConstructionResult:
    """One signal date's buffered, risk-weighted construction target."""
    _assert_single_signal_date(factors)
    frame = factors.frame
    signal_date = sorted({day for day in frame["trade_date"]})[0]
    valid = frame[frame["is_valid"].fillna(False).astype(bool)]
    ranked = _ranked_valid_symbols(valid)
    raw_rank = {
        symbol: position
        for position, (symbol, _) in enumerate(ranked, start=1)
    }
    risk_by_symbol = _risk_rows_by_symbol(risks, list(raw_rank))
    previous = [str(symbol) for symbol in previous_target_members]
    if len(set(previous)) != len(previous):
        raise ValueError(
            f"previous_target_members contains duplicates: {previous}"
        )

    # Two-stage ranks: drop risk-invalid rows, renumber continuously.
    eligible: list[str] = []
    risk_invalid: set[str] = set()
    for symbol, _ in ranked:
        risk_row = risk_by_symbol.get(symbol)
        if risk_row is None or not bool(risk_row["risk_is_valid"]):
            risk_invalid.add(symbol)
            continue
        eligible.append(symbol)
    eligible_rank = {
        symbol: position for position, symbol in enumerate(eligible, start=1)
    }

    # Stage one: retain eligible previous targets through the hold rank.
    retained = [
        symbol
        for symbol in sorted(previous,
                             key=lambda item: eligible_rank.get(item, 0))
        if symbol in eligible_rank and eligible_rank[symbol] <= policy.hold_rank
    ][: policy.target_count]
    # Stage two: fill the remaining seats only from the entry ranks.
    members: list[str] = list(retained)
    for symbol in eligible:
        if len(members) >= policy.target_count:
            break
        if symbol in members or eligible_rank[symbol] > policy.entry_rank:
            continue
        members.append(symbol)
    members = sorted(members)

    member_risks = {
        symbol: Decimal(str(risk_by_symbol[symbol]
                            ["applied_annualized_volatility"]))
        for symbol in members
    }
    raw_weights, capped_weights, weights, cash = _solve_weights(
        member_risks, policy
    )
    audit_frame = _audit_frame(
        signal_date=signal_date,
        factor_frame=frame,
        raw_rank=raw_rank,
        eligible_rank=eligible_rank,
        risk_by_symbol=risk_by_symbol,
        risk_invalid=risk_invalid,
        previous=set(previous),
        members=frozenset(members),
        raw_weights=raw_weights,
        capped_weights=capped_weights,
        weights=weights,
        cash=cash,
        policy=policy,
    )
    return PortfolioConstructionResult(
        signal_date=signal_date,
        target_members=tuple(members),
        target_weights=weights,
        cash_weight=cash,
        audit_frame=audit_frame,
    )


def allocate_capped_inverse_volatility(
    risks: Mapping[str, Decimal], policy: BufferedRiskWeightedPolicy
) -> tuple[dict[str, Decimal], Decimal]:
    """Deterministic capped-simplex weights plus the unallocatable cash."""
    _, _, weights, cash = _solve_weights(dict(risks), policy)
    return weights, cash


# --------------------------------------------------------------------------- #
# The capped decimal solver
# --------------------------------------------------------------------------- #


def _solve_weights(
    risks: Mapping[str, Decimal], policy: BufferedRiskWeightedPolicy
) -> tuple[dict[str, Decimal], dict[str, Decimal], dict[str, Decimal], Decimal]:
    """Raw, capped (pre-quantization) and final weights plus the cash.

    The raw weights are the pure inverse-volatility shares of the realizable
    exposure before any cap; the capped weights are the water-fill result
    before quantization; the final weights are quantized down to
    ``weight_quantum`` with the residual quanta redistributed in ascending
    symbol order.  All arithmetic runs in one high-precision decimal context,
    so identical inputs always yield identical weights.
    """
    members = sorted(risks)
    if not members:
        raise ValueError("allocation needs at least one member")
    quantum = policy.weight_quantum
    cap = policy.max_single_weight
    scores: dict[str, Decimal] = {}
    with localcontext() as context:
        context.prec = _PRECISION
        for symbol in members:
            volatility = risks[symbol]
            if not volatility.is_finite() or volatility <= 0:
                raise ValueError(
                    f"applied volatility for {symbol} must be a positive "
                    f"finite Decimal, got {volatility}"
                )
            scores[symbol] = Decimal(1) / max(
                volatility, policy.volatility_floor_annualized
            )
        target_exposure = min(
            policy.gross_exposure, Decimal(len(members)) * cap
        )
        score_total = sum(scores.values())
        raw = {
            symbol: target_exposure * scores[symbol] / score_total
            for symbol in members
        }
        capped = _cap_overweight_names(scores, target_exposure, cap)
        final = {
            symbol: (capped[symbol] / quantum).to_integral_value(
                rounding="ROUND_FLOOR"
            )
            * quantum
            for symbol in members
        }
        # Redistribute the residual quanta: ascending symbol order, one
        # quantum at a time, never past the cap.
        residual = (
            (target_exposure - sum(final.values())) / quantum
        ).to_integral_value(rounding="ROUND_FLOOR")
        while residual > 0:
            progressed = False
            for symbol in members:
                if residual <= 0:
                    break
                if final[symbol] + quantum > cap:
                    continue
                final[symbol] += quantum
                residual -= 1
                progressed = True
            if not progressed:  # every name sits at its cap
                break
        cash = policy.gross_exposure - sum(final.values())
    if sum(final.values()) + cash != policy.gross_exposure:
        raise ValueError(
            "allocation residue must leave sum(weights) + cash equal to the "
            f"gross exposure {policy.gross_exposure}"
        )
    return raw, capped, final, cash


def _cap_overweight_names(
    scores: Mapping[str, Decimal],
    target_exposure: Decimal,
    cap: Decimal,
) -> dict[str, Decimal]:
    """Water-fill the exposure by score share, capping overweight names."""
    symbols = sorted(scores)
    weights = {symbol: Decimal(0) for symbol in symbols}
    fixed: set[str] = set()
    with localcontext() as context:
        context.prec = _PRECISION
        while True:
            uncapped = [symbol for symbol in symbols if symbol not in fixed]
            if not uncapped:
                break
            total_score = sum(scores[symbol] for symbol in uncapped)
            remaining = target_exposure - sum(
                weights[symbol] for symbol in symbols if symbol in fixed
            )
            overweight: list[str] = []
            for symbol in uncapped:
                share = remaining * scores[symbol] / total_score
                if share > cap:
                    overweight.append(symbol)
                    weights[symbol] = cap
                else:
                    weights[symbol] = share
            if not overweight:
                break
            fixed.update(overweight)
    return weights


# --------------------------------------------------------------------------- #
# Ranking and audit helpers
# --------------------------------------------------------------------------- #


def _assert_single_signal_date(factors: FactorResult) -> None:
    frame = factors.frame
    if frame.empty:
        raise ValueError(
            "factor result must contain rows for exactly one signal "
            "trade_date, got none"
        )
    signal_dates = frame["trade_date"].nunique()
    if signal_dates != 1:
        raise ValueError(
            "buffered target needs exactly one signal trade_date, "
            f"got {signal_dates}"
        )


def _ranked_valid_symbols(valid: pd.DataFrame) -> list[tuple[str, Decimal]]:
    """Factor-valid ``(symbol, processed_value)`` pairs, best rank first."""
    ordered = valid.sort_values(
        ["processed_value", "symbol"], ascending=[False, True], kind="stable"
    )
    return [
        (str(row["symbol"]), Decimal(str(row["processed_value"])))
        for row in ordered.to_dict("records")
    ]


def _risk_rows_by_symbol(risks: pd.DataFrame, symbols: list[str]) -> dict:
    if not isinstance(risks, pd.DataFrame):
        raise TypeError(
            f"risks must be a pandas DataFrame, got {type(risks).__name__}"
        )
    by_symbol: dict[str, dict] = {}
    for row in risks.to_dict("records"):
        symbol = str(row["symbol"])
        if symbol in by_symbol:
            raise ValueError(f"risk frame carries duplicate rows for {symbol}")
        by_symbol[symbol] = row
    missing = sorted(set(symbols) - set(by_symbol))
    if missing:
        raise ValueError(
            "risk frame carries no row for factor candidate(s): "
            + ", ".join(missing)
        )
    return by_symbol


def _audit_frame(
    *,
    signal_date,
    factor_frame: pd.DataFrame,
    raw_rank: Mapping[str, int],
    eligible_rank: Mapping[str, int],
    risk_by_symbol: Mapping[str, dict],
    risk_invalid: set[str],
    previous: set[str],
    members: frozenset[str],
    raw_weights: Mapping[str, Decimal],
    capped_weights: Mapping[str, Decimal],
    weights: Mapping[str, Decimal],
    cash: Decimal,
    policy: BufferedRiskWeightedPolicy,
) -> pd.DataFrame:
    """One ordered audit row per factor-valid candidate plus previous target."""
    rule_version = buffered_portfolio_rule_version(policy)
    factor_valid: set[str] = set()
    factor_invalid_reason: dict[str, str] = {}
    for row in factor_frame.to_dict("records"):
        symbol = str(row["symbol"])
        if bool(row["is_valid"]):
            factor_valid.add(symbol)
        else:
            factor_invalid_reason[symbol] = str(
                row.get("invalid_reason") or REASON_FACTOR_INVALID
            )
    audit_symbols = sorted(factor_valid | previous)

    records: list[dict] = []
    for symbol in audit_symbols:
        risk_row = risk_by_symbol.get(symbol)
        rank = raw_rank.get(symbol)
        erank = eligible_rank.get(symbol)
        member = symbol in members
        was_previous = symbol in previous
        if symbol not in factor_valid:
            status, reason = (
                MEMBER_STATUS_EXITED,
                factor_invalid_reason.get(symbol, REASON_LEFT_POOL),
            )
        elif symbol in risk_invalid:
            status = MEMBER_STATUS_RISK_INVALID
            reason = (
                str(risk_row["risk_invalid_reason"])
                if risk_row is not None
                else _DEFAULT_FALLBACK_REASON
            )
        elif member and was_previous:
            status, reason = MEMBER_STATUS_RETAINED, REASON_RETAINED
        elif member:
            status, reason = MEMBER_STATUS_ENTERED, REASON_ENTERED
        elif was_previous:
            if erank is not None and erank > policy.hold_rank:
                status, reason = MEMBER_STATUS_EXITED, REASON_RANK_BELOW_HOLD
            else:
                status, reason = MEMBER_STATUS_EXITED, REASON_LEFT_POOL
        else:
            status = MEMBER_STATUS_NOT_SELECTED
            reason = (
                REASON_BEYOND_TARGET_COUNT
                if erank is not None and erank <= policy.entry_rank
                else REASON_BEYOND_ENTRY_RANK
            )
        records.append(
            {
                "signal_date": signal_date,
                "symbol": symbol,
                "previous_target_member": was_previous,
                "raw_momentum_rank": _float_or_nan(rank),
                "risk_eligible_rank": _float_or_nan(erank),
                "member_status": status,
                "member_reason": reason,
                "window_start": risk_row["window_start"]
                if risk_row is not None
                else None,
                "window_end": risk_row["window_end"]
                if risk_row is not None
                else None,
                "real_close_observations": (
                    int(risk_row["real_close_observations"])
                    if risk_row is not None
                    else 0
                ),
                "suspension_carry_days": (
                    int(risk_row["suspension_carry_days"])
                    if risk_row is not None
                    else 0
                ),
                "risk_is_valid": (
                    bool(risk_row["risk_is_valid"])
                    if risk_row is not None
                    else False
                ),
                "risk_invalid_reason": (
                    str(risk_row["risk_invalid_reason"])
                    if risk_row is not None
                    else _DEFAULT_FALLBACK_REASON
                ),
                "raw_annualized_volatility": (
                    float(risk_row["raw_annualized_volatility"])
                    if risk_row is not None
                    else float("nan")
                ),
                "applied_annualized_volatility": (
                    float(risk_row["applied_annualized_volatility"])
                    if risk_row is not None
                    else float("nan")
                ),
                "risk_score": _risk_score(risk_row, policy),
                "raw_weight": raw_weights.get(symbol, Decimal(0)),
                "capped_weight": capped_weights.get(symbol, Decimal(0)),
                "target_weight": weights.get(symbol, Decimal(0)),
                "cash_weight": cash,
                "portfolio_rule_version": rule_version,
            }
        )
    frame = pd.DataFrame(records, columns=list(PORTFOLIO_CONSTRUCTION_COLUMNS))
    # Deterministic row order: raw momentum rank ascending, rankless
    # (left-the-pool) rows last, symbol ascending inside both groups.
    frame["_missing_rank"] = frame["raw_momentum_rank"].isna()
    frame = frame.sort_values(
        ["_missing_rank", "raw_momentum_rank", "symbol"], kind="stable"
    ).drop(columns=["_missing_rank"])
    return frame.reset_index(drop=True)


def _risk_score(
    risk_row: Mapping[str, object] | None,
    policy: BufferedRiskWeightedPolicy,
) -> Decimal:
    """``1 / applied volatility`` for a risk-valid row, else zero."""
    if risk_row is None or not bool(risk_row["risk_is_valid"]):
        return Decimal(0)
    with localcontext() as context:
        context.prec = _PRECISION
        volatility = Decimal(str(risk_row["applied_annualized_volatility"]))
        return Decimal(1) / max(
            volatility, policy.volatility_floor_annualized
        )


def _float_or_nan(value: int | None) -> float:
    return float("nan") if value is None else float(value)
