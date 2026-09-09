"""Isolated walk-forward fold execution and the resumable artifact contract.

``WalkForwardRunner`` executes one immutable :class:`~stock_quant.research.
walk_forward.schedule.FoldSchedule` fold by fold.  Before any account exists
it persists ``fold_schedule.json`` and its canonical SHA-256 (rewriting the
identical bytes is allowed; divergent bytes are refused).  Every fold then
runs its preflights -- pinned dataset acceptance, point-in-time universe
coverage, warmup/session counts, benchmark coverage and, for folds whose
calendar year has no open session, versioned market-wide closure evidence
(absence of that evidence is a FAILED, never a legal skip) -- and only a
fully-preflighted fold executes.

Execution is isolated per fold and per declared cost scenario: a fresh
:class:`~stock_quant.backtest.engine.BacktestRequest` and a fresh account with
the identical fixed initial cash for every fold, factor inputs read over the
warmup + OOS window but signals/targets/orders restricted to the fold's OOS
sessions, engine valuation restricted to the expected OOS open days, and the
engine's current ``total_equity`` mapped to the canonical fold artifact column
``net_equity_after_cost``.  Any fold/system integrity failure leaves the
schedule untouched, writes one outcome for every planned fold plus a FAILED
manifest whose ``stability_conclusion`` is null, and raises
:class:`WalkForwardRunFailed` -- it can never become INCONCLUSIVE.

Resume: every fold-scenario stage records a completion hash over the fold id,
scenario, schedule hash and snapshot hashes beside its artifact hashes, so a
re-run can never reuse another fold's artifacts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Mapping, Sequence

import pandas as pd

from stock_quant.backtest.costs import CostModel
from stock_quant.backtest.engine import BacktestEngine, BacktestRequest
from stock_quant.backtest.rebalancer import AccountAwareWeeklyRebalancer
from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.data_model.trading_rules import TradingRuleBook
from stock_quant.factors.base import Factor, FactorContext
from stock_quant.factors.models import FactorResult
from stock_quant.portfolio.equal_weight import TopNEqualWeight
from stock_quant.research.spec import ExperimentSpec
from stock_quant.research.universe import UniverseResolver
from stock_quant.research.walk_forward.evaluation import (
    StabilityEvaluation,
    evaluate_stability,
)
from stock_quant.research.walk_forward.metrics import (
    FoldMetrics,
    OOSIntegrityError,
    build_daily_returns,
    compute_fold_metrics,
)
from stock_quant.research.walk_forward.policy import (
    StabilityPolicy,
    WalkForwardPolicy,
    canonical_sha256,
)
from stock_quant.research.walk_forward.schedule import (
    FoldOutcome,
    FoldOutcomeLedger,
    FoldOutcomeStatus,
    FoldSchedule,
    FoldWindow,
    sha256_file,
    write_canonical_json,
)
from stock_quant.research.walk_forward.snapshots import SnapshotBundle

#: Fraction of the fixed initial cash the portfolio sizes its target notional
#: from (mirrors the engineering path's standing cash buffer).
SIZING_FRACTION = 0.85

_STAGES_FILE = "walk_forward_stages.json"

#: Quality annotation for engine bars (phase-one authoritative path).
_QUALITY_SEVERITY_AUTHORITATIVE = "INFO"


class WalkForwardRunFailed(RuntimeError):
    """A fold/system integrity failure: the run is FAILED, conclusion null.

    ``schedule_path`` locates the untouched immutable schedule, ``outcomes``
    is the full per-planned-fold outcome tuple, and
    ``stability_conclusion`` is always ``None`` -- a FAILED run can never
    carry a stability verdict.
    """

    def __init__(
        self,
        message: str,
        *,
        schedule_path: Path,
        outcomes_path: Path,
        outcomes: Sequence[FoldOutcome],
        stability_conclusion: str | None,
        failed_reason_codes: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.schedule_path = Path(schedule_path)
        self.outcomes_path = Path(outcomes_path)
        self.outcomes = tuple(outcomes)
        self.stability_conclusion = stability_conclusion
        self.failed_reason_codes = tuple(failed_reason_codes)


@dataclass(frozen=True)
class WalkForwardRequest:
    """Everything one isolated walk-forward run consumes (all inputs frozen).

    The runner never resolves ``CURRENT``, never rewrites the schedule and
    never bypasses the acceptance/universe gates owned by the caller (the
    research runner pins them before the identity is computed).
    """

    run_dir: Path
    spec: ExperimentSpec
    schedule: FoldSchedule
    snapshot_bundle: SnapshotBundle
    walk_forward_policy: WalkForwardPolicy
    stability_policy: StabilityPolicy
    calendar: TradingCalendar
    rule_book: TradingRuleBook
    initial_cash: float
    dataset_version: str
    universe_symbols: Sequence[str]
    bars: pd.DataFrame
    benchmarks: pd.DataFrame
    benchmark_symbols: Sequence[str]
    corporate_actions: pd.DataFrame
    factors: Mapping[str, Factor]
    factor_input_provider: Callable[[], pd.DataFrame]
    portfolio_builder: TopNEqualWeight
    cost_models: Mapping[str, CostModel]
    universe_resolver: UniverseResolver
    #: The sanitized audit of the pinned real-data acceptance record; a run
    #: without an ACCEPTED audit bound to ``dataset_version`` fails preflight.
    acceptance_audit: Mapping | None
    #: Fold id -> market-wide closure evidence map, the only legal basis for a
    #: ``skipped_not_tradeable`` outcome (folds with no open session at all).
    market_closure_evidence: Mapping[str, Mapping[str, str]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class FoldRunResult:
    """One executed fold's in-memory artifacts and identity facts."""

    fold_id: str
    initial_cash: float
    opening_positions: dict[str, int]
    first_trading_day: date | None
    last_trading_day: date | None
    orders: pd.DataFrame
    fills: pd.DataFrame
    equity: pd.DataFrame
    daily_returns: pd.DataFrame
    metrics: tuple[FoldMetrics, ...]
    artifact_hashes: dict[str, str]


@dataclass(frozen=True)
class WalkForwardRunResult:
    """One completed (status-wise) walk-forward run's auditable outputs."""

    schedule_path: Path
    outcomes_path: Path
    manifest_path: Path
    schedule_sha256: str
    outcomes_sha256: str
    outcomes: tuple[FoldOutcome, ...]
    executed_folds: tuple[FoldRunResult, ...]
    scenario_metrics: tuple[FoldMetrics, ...]
    evaluation: StabilityEvaluation
    manifest: dict


class WalkForwardRunner:
    """Executes one frozen schedule with isolated, resumable folds."""

    def run(self, request: WalkForwardRequest) -> WalkForwardRunResult:
        if not isinstance(request, WalkForwardRequest):
            raise TypeError(
                "WalkForwardRunner.run expects a WalkForwardRequest, got "
                f"{type(request).__name__}"
            )
        run_dir = Path(request.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        # The schedule is written and hashed before any fold executes and is
        # never modified afterwards (write_canonical_json refuses divergent
        # overwrite; identical bytes are a legal resume).
        schedule_hash = write_canonical_json(
            run_dir / "fold_schedule.json", request.schedule
        )
        scenario_names = list(request.spec.cost_scenarios)
        missing_models = [
            scenario for scenario in scenario_names
            if scenario not in request.cost_models
        ]
        if missing_models:
            raise ValueError(
                "the request has no cost model for declared scenario(s): "
                + ", ".join(missing_models)
            )
        outcomes: list[FoldOutcome] = []
        executed: list[FoldRunResult] = []
        scenario_metrics: list[FoldMetrics] = []
        integrity_failures: list[str] = []
        for fold in request.schedule.folds:
            reason = self._preflight_fold(fold, request)
            if reason is not None:
                reason_code, detail = reason
                outcomes.append(
                    FoldOutcome(
                        fold_id=fold.fold_id,
                        status=FoldOutcomeStatus.FAILED_PREFLIGHT,
                        reason_code=reason_code,
                        detail=detail,
                    )
                )
                integrity_failures.append(reason_code)
                continue
            if fold.first_trading_day is None:
                evidence = request.market_closure_evidence.get(fold.fold_id)
                if evidence:
                    outcomes.append(
                        FoldOutcome(
                            fold_id=fold.fold_id,
                            status=FoldOutcomeStatus.SKIPPED_NOT_TRADEABLE,
                            reason_code="MARKET_WIDE_CLOSURE",
                            detail="versioned market-wide closure evidence supplied",
                            evidence={key: str(value)
                                      for key, value in sorted(evidence.items())},
                        )
                    )
                    continue
                outcomes.append(
                    FoldOutcome(
                        fold_id=fold.fold_id,
                        status=FoldOutcomeStatus.FAILED_PREFLIGHT,
                        reason_code="CLOSURE_EVIDENCE_MISSING",
                        detail=(
                            "the fold has no confirmed open session and no "
                            "market-wide closure evidence was supplied"
                        ),
                    )
                )
                integrity_failures.append("CLOSURE_EVIDENCE_MISSING")
                continue
            try:
                fold_result = self._execute_fold(fold, request, schedule_hash)
            except (OOSIntegrityError, ValueError) as error:
                reason_code = (
                    "OOS_INTEGRITY"
                    if isinstance(error, OOSIntegrityError)
                    else "EXECUTION_INTEGRITY"
                )
                outcomes.append(
                    FoldOutcome(
                        fold_id=fold.fold_id,
                        status=FoldOutcomeStatus.FAILED_PREFLIGHT,
                        reason_code=reason_code,
                        detail=str(error)[:500],
                    )
                )
                integrity_failures.append(reason_code)
                continue
            executed.append(fold_result)
            scenario_metrics.extend(fold_result.metrics)
            outcomes.append(
                FoldOutcome(fold_id=fold.fold_id, status=FoldOutcomeStatus.EXECUTED)
            )
        ledger = FoldOutcomeLedger.for_schedule(request.schedule, outcomes=outcomes)
        outcomes_path = run_dir / "fold_outcomes.json"
        outcomes_hash = write_canonical_json(outcomes_path, ledger)
        evaluation = evaluate_stability(
            policy=request.stability_policy,
            declared_scenarios=tuple(scenario_names),
            scenario_metrics=scenario_metrics,
            outcomes=tuple(outcomes),
            integrity_failures=tuple(integrity_failures),
        )
        manifest = {
            "status": evaluation.research_status.value,
            "stability_conclusion": evaluation.stability_conclusion,
            "fold_schedule_sha256": schedule_hash,
            "fold_outcomes_sha256": outcomes_hash,
            "snapshot_hashes": dict(request.snapshot_bundle.hashes),
            "dataset_version": request.dataset_version,
            "initial_cash": request.initial_cash,
            "declared_scenarios": list(scenario_names),
            "evaluation": evaluation.model_dump(mode="json"),
        }
        manifest_path = run_dir / "walk_forward_manifest.json"
        write_canonical_json(manifest_path, _JsonPayload(manifest))
        failed_codes = sorted(
            {
                outcome.reason_code
                for outcome in outcomes
                if outcome.status is FoldOutcomeStatus.FAILED_PREFLIGHT
            }
        )
        if failed_codes or evaluation.research_status.value == "FAILED":
            raise WalkForwardRunFailed(
                "walk-forward run failed fold/system preflight: "
                + ", ".join(failed_codes or ["evaluation FAILED"]),
                schedule_path=run_dir / "fold_schedule.json",
                outcomes_path=outcomes_path,
                outcomes=tuple(outcomes),
                stability_conclusion=None,
                failed_reason_codes=failed_codes,
            )
        return WalkForwardRunResult(
            schedule_path=run_dir / "fold_schedule.json",
            outcomes_path=outcomes_path,
            manifest_path=manifest_path,
            schedule_sha256=schedule_hash,
            outcomes_sha256=outcomes_hash,
            outcomes=tuple(outcomes),
            executed_folds=tuple(executed),
            scenario_metrics=tuple(scenario_metrics),
            evaluation=evaluation,
            manifest=manifest,
        )

    # ------------------------------------------------------------------ #
    # Preflights (per fold, before any account exists)
    # ------------------------------------------------------------------ #

    def _preflight_fold(
        self, fold: FoldWindow, request: WalkForwardRequest
    ) -> tuple[str, str] | None:
        """The first failing preflight as ``(reason_code, detail)``, else None."""
        audit = request.acceptance_audit
        if not isinstance(audit, Mapping) or not audit.get("acceptance_id"):
            return ("ACCEPTANCE_MISSING", "no pinned real-data acceptance audit")
        if str(audit.get("decision")) != "ACCEPTED":
            return (
                "ACCEPTANCE_MISSING",
                f"acceptance decision is {audit.get('decision')!r}, not ACCEPTED",
            )
        if str(audit.get("dataset_version")) != request.dataset_version:
            return (
                "ACCEPTANCE_MISSING",
                "acceptance audit is not bound to the pinned dataset version",
            )
        if fold.warmup_session_count < (
            request.walk_forward_policy.min_warmup_trading_days
        ):
            return (
                "WARMUP_SESSIONS_SHORT",
                f"fold {fold.fold_id} has {fold.warmup_session_count} confirmed "
                f"warmup sessions < "
                f"{request.walk_forward_policy.min_warmup_trading_days}",
            )
        policy = request.walk_forward_policy
        open_days = self._fold_open_days(fold, request.calendar)
        try:
            for day in open_days:
                request.universe_resolver.members_on(day)
        except Exception as error:  # noqa: BLE001 - coverage errors are preflight
            return ("UNIVERSE_COVERAGE", str(error)[:300])
        factor_dates = self._factor_input_dates(request)
        stable_history = sum(1 for day in factor_dates if day < fold.first_trading_day)
        if stable_history < policy.required_stable_history_days_before_s:
            return (
                "FACTOR_HISTORY_SHORT",
                f"only {stable_history} factor sessions exist before "
                f"{fold.first_trading_day.isoformat()} < "
                f"{policy.required_stable_history_days_before_s}",
            )
        benchmark = _prepare_benchmarks(request.benchmarks)
        for symbol in request.benchmark_symbols:
            series = benchmark.get(str(symbol), {})
            missing = [day for day in open_days if not series.get(day)]
            if missing:
                return (
                    "BENCHMARK_GAP",
                    f"benchmark {symbol} lacks closes on "
                    f"{len(missing)} fold open day(s) from "
                    f"{missing[0].isoformat()}",
                )
        return None

    def _factor_input_dates(self, request: WalkForwardRequest) -> list[date]:
        if getattr(self, "_factor_dates", None) is None:
            frame = request.factor_input_provider()
            self._factor_dates = sorted(
                {day for day in (_as_date(v) for v in frame["trade_date"])}
            )
        return self._factor_dates

    @staticmethod
    def _fold_open_days(
        fold: FoldWindow, calendar: TradingCalendar
    ) -> tuple[date, ...]:
        if fold.first_trading_day is None or fold.last_trading_day is None:
            return ()
        return tuple(
            day for day in calendar.open_days
            if fold.first_trading_day <= day <= fold.last_trading_day
        )

    # ------------------------------------------------------------------ #
    # One-fold execution
    # ------------------------------------------------------------------ #

    def _execute_fold(
        self,
        fold: FoldWindow,
        request: WalkForwardRequest,
        schedule_hash: str,
    ) -> FoldRunResult:
        signals = request.calendar.last_trading_day_each_week(
            fold.first_trading_day, fold.last_trading_day
        )
        open_days = self._fold_open_days(fold, request.calendar)
        factor_frame = self._compute_factors(fold, request, signals)
        targets_by_day, signal_rows = self._build_targets(
            fold, request, factor_frame, signals
        )
        possible_held = frozenset(
            {symbol for _, targets in targets_by_day.values() for symbol in targets}
        )
        scenario_names = list(request.spec.cost_scenarios)
        canonical = "full_cost" if "full_cost" in scenario_names else scenario_names[-1]
        per_scenario: dict[str, dict] = {}
        for scenario in scenario_names:
            per_scenario[scenario] = self._execute_fold_scenario(
                fold, request, schedule_hash, scenario, targets_by_day,
                possible_held, open_days,
            )
        canonical_row = per_scenario[canonical]
        fold_dir = Path(request.run_dir) / "folds" / fold.fold_id
        fold_dir.mkdir(parents=True, exist_ok=True)
        hashes = self._write_fold_artifacts(
            fold, request, fold_dir, canonical_row, signal_rows, per_scenario
        )
        metrics = tuple(
            per_scenario[scenario]["metrics"] for scenario in scenario_names
        )
        return FoldRunResult(
            fold_id=fold.fold_id,
            initial_cash=request.initial_cash,
            # The account is constructed fresh per fold/scenario: no position
            # can exist before the fold's first OOS session.
            opening_positions={},
            first_trading_day=fold.first_trading_day,
            last_trading_day=fold.last_trading_day,
            orders=canonical_row["submitted"],
            fills=canonical_row["fills"],
            equity=canonical_row["equity"],
            daily_returns=canonical_row["daily_returns"],
            metrics=metrics,
            artifact_hashes=hashes,
        )

    def _compute_factors(
        self,
        fold: FoldWindow,
        request: WalkForwardRequest,
        signals: tuple[date, ...],
    ) -> pd.DataFrame:
        """Warmup+OOS factor reads; only the fold's OOS signal days produce rows."""
        resolver = request.universe_resolver
        frames: list[pd.DataFrame] = []
        versions = dict(request.spec.factor_versions)
        for name in sorted(versions):
            factor = request.factors.get(name)
            if factor is None:
                raise ValueError(f"no registered factor {name!r} for the fold run")
            if factor.name != name or factor.version != versions[name]:
                raise ValueError(
                    f"factor {factor.name!r}/{factor.version!r} does not match the "
                    f"spec request {name!r}/{versions[name]!r}"
                )
            context = FactorContext(
                dataset=_FactorDatasetAdapter(request.factor_input_provider),
                universe_version=request.spec.universe_version,
                start_date=fold.warmup_calendar_start,
                end_date=fold.last_trading_day,
                signal_dates=signals,
                members_on=resolver.members_on,
                membership_snapshot_for=resolver.snapshot_for,
            )
            result = factor.compute(context)
            if not isinstance(result, FactorResult):
                raise TypeError(
                    f"factor {name} returned {type(result).__name__}, expected "
                    "a FactorResult"
                )
            frames.append(result.frame)
        combined = (
            frames[0]
            if len(frames) == 1
            else pd.concat(frames, ignore_index=True)
        )
        combined["trade_date"] = [_as_date(day) for day in combined["trade_date"]]
        return combined.sort_values(
            ["trade_date", "symbol"], kind="stable"
        ).reset_index(drop=True)

    def _build_targets(
        self,
        fold: FoldWindow,
        request: WalkForwardRequest,
        factor_frame: pd.DataFrame,
        signals: tuple[date, ...],
    ) -> tuple[dict[date, tuple[date, dict[str, int]]], pd.DataFrame]:
        """The frozen, scenario-independent per-signal-day target book."""
        prices = _prepare_bars(request.bars)
        price_map: dict[date, pd.DataFrame] = {
            day: frame for day, frame in prices.groupby("trade_date")
        }
        sizing_capital = request.initial_cash * SIZING_FRACTION
        targets_by_day: dict[date, tuple[date, dict[str, int]]] = {}
        signal_rows: list[dict] = []
        for signal in signals:
            one_signal = factor_frame[factor_frame["trade_date"] == signal]
            if one_signal.empty:
                continue
            result = FactorResult(
                factor_name=str(one_signal["factor_name"].iloc[0]),
                factor_version=str(one_signal["factor_version"].iloc[0]),
                frame=one_signal.copy(),
            )
            execution_day = request.calendar.next_trading_day(signal)
            target = request.portfolio_builder.build(
                result,
                price_map.get(signal, pd.DataFrame(columns=["symbol", "close"])),
                sizing_capital,
            )
            members = set(request.universe_resolver.members_on(signal))
            outsiders = sorted(
                {str(row["symbol"]) for row in target.frame.to_dict("records")}
                - members
            )
            if outsiders:
                raise ValueError(
                    f"fold {fold.fold_id}: portfolio targets {outsiders} on "
                    f"{signal.isoformat()} are not point-in-time members; "
                    "membership-first factor filtering was violated"
                )
            targets: dict[str, int] = {
                str(row["symbol"]): int(row["target_quantity"])
                for row in target.frame.to_dict("records")
            }
            targets_by_day[execution_day] = (signal, targets)
            signal_rows.append(
                {
                    "signal_date": signal,
                    "execution_date": execution_day,
                    "n_targets": len(targets),
                    "unallocated_weight": round(target.unallocated_weight, 6),
                }
            )
        return targets_by_day, pd.DataFrame(signal_rows)

    def _execute_fold_scenario(
        self,
        fold: FoldWindow,
        request: WalkForwardRequest,
        schedule_hash: str,
        scenario: str,
        targets_by_day: dict[date, tuple[date, dict[str, int]]],
        possible_held: frozenset[str],
        open_days: tuple[date, ...],
    ) -> dict:
        """One fresh account replayed over the fold's OOS sessions."""
        stage_key = f"{fold.fold_id}|{scenario}"
        stages = _load_stages(Path(request.run_dir))
        stage = stages.get(stage_key)
        input_hash = canonical_sha256(
            {
                "fold_id": fold.fold_id,
                "scenario": scenario,
                "schedule_sha256": schedule_hash,
                "snapshot_hashes": request.snapshot_bundle.hashes,
                "initial_cash": request.initial_cash,
                "first_trading_day": fold.first_trading_day.isoformat()
                if fold.first_trading_day
                else None,
                "last_trading_day": fold.last_trading_day.isoformat()
                if fold.last_trading_day
                else None,
            }
        )
        if stage and stage.get("input_hash") == input_hash and _outputs_intact(
            Path(request.run_dir), stage.get("outputs", {})
        ):
            return _load_fold_scenario(
                Path(request.run_dir), fold, request, scenario, open_days, stage
            )
        bars = _prepare_bars(request.bars)
        fold_bars = bars[
            (bars["trade_date"] >= fold.first_trading_day)
            & (bars["trade_date"] <= fold.last_trading_day)
        ].copy()
        if fold_bars.empty:
            raise OOSIntegrityError(
                f"fold {fold.fold_id}: no bars cover the OOS window"
            )
        fold_bars["quality_severity"] = _QUALITY_SEVERITY_AUTHORITATIVE
        benchmark = _prepare_benchmarks(request.benchmarks)
        benchmark_rows = [
            {"symbol": symbol, "trade_date": day, "close": close}
            for symbol, series in benchmark.items()
            for day, close in sorted(series.items())
            if fold.first_trading_day <= day <= fold.last_trading_day
        ]
        rebalancer = AccountAwareWeeklyRebalancer(targets_by_day)
        # A fresh BacktestRequest (and therefore a fresh account with the
        # identical fixed initial cash) for every fold and every scenario.
        engine_request = BacktestRequest(
            dataset_version=request.dataset_version,
            initial_cash=request.initial_cash,
            calendar=request.calendar,
            rule_book=request.rule_book,
            cost_model=request.cost_models[scenario],
            bars=fold_bars,
            corporate_actions=request.corporate_actions,
            schedule=(),
            benchmark_symbols=tuple(str(s) for s in request.benchmark_symbols),
            benchmarks=pd.DataFrame(benchmark_rows),
            order_provider=rebalancer.orders_for,
            possible_held_symbols=possible_held,
        )
        result = BacktestEngine().run(engine_request)
        equity = result.daily_equity.copy()
        equity["trade_date"] = [_as_date(day) for day in equity["trade_date"]]
        equity = equity.sort_values("trade_date").reset_index(drop=True)
        equity.insert(1, "initial_equity", float(request.initial_cash))
        equity = equity.rename(columns={"total_equity": "net_equity_after_cost"})
        daily_returns = build_daily_returns(
            equity,
            initial_equity=float(request.initial_cash),
            expected_open_days=open_days,
        )
        _ledger_preflight(result.submitted_orders, result.fills, result.rejections)
        diffs = _order_diffs(result.submitted_orders, result.fills, result.rejections)
        metrics = compute_fold_metrics(
            equity,
            result.fills,
            submitted_orders=result.submitted_orders,
            order_diffs=diffs,
            initial_equity=float(request.initial_cash),
            expected_open_days=open_days,
            fold_id=fold.fold_id,
            scenario=scenario,
        )
        scenario_dir = Path(request.run_dir) / "backtest" / fold.fold_id / scenario
        scenario_dir.mkdir(parents=True, exist_ok=True)
        outputs: dict[str, str] = {}
        for name, frame in (
            ("submitted_orders.parquet", result.submitted_orders),
            ("fills.parquet", result.fills),
            ("rejections.parquet", result.rejections),
            ("equity.parquet", equity),
            ("daily_returns.parquet", daily_returns),
            ("order_diffs.parquet", diffs),
        ):
            frame.to_parquet(scenario_dir / name, index=False)
            outputs[name] = sha256_file(scenario_dir / name)
        stages = _load_stages(Path(request.run_dir))
        stages[stage_key] = {"input_hash": input_hash, "outputs": outputs}
        (Path(request.run_dir) / _STAGES_FILE).write_text(
            json.dumps(stages, indent=2, sort_keys=True), encoding="utf-8"
        )
        return {
            "submitted": result.submitted_orders,
            "fills": result.fills,
            "rejections": result.rejections,
            "equity": equity,
            "daily_returns": daily_returns,
            "order_diffs": diffs,
            "metrics": metrics,
        }

    def _write_fold_artifacts(
        self,
        fold: FoldWindow,
        request: WalkForwardRequest,
        fold_dir: Path,
        canonical: dict,
        signal_rows: pd.DataFrame,
        per_scenario: Mapping[str, dict],
    ) -> dict[str, str]:
        """Publish the fold's declared artifact set and its manifest."""
        signals_path = fold_dir / "signals.parquet"
        signal_rows.to_parquet(signals_path, index=False)
        canonical["submitted"].to_parquet(fold_dir / "orders.parquet", index=False)
        canonical["fills"].to_parquet(fold_dir / "fills.parquet", index=False)
        canonical["equity"].to_parquet(fold_dir / "equity.parquet", index=False)
        canonical["daily_returns"].to_parquet(
            fold_dir / "daily_returns.parquet", index=False
        )
        metrics_payload = {
            "fold_id": fold.fold_id,
            "initial_equity": request.initial_cash,
            "scenarios": {
                scenario: per_scenario[scenario]["metrics"].model_dump(mode="json")
                for scenario in sorted(per_scenario)
            },
        }
        metrics_path = fold_dir / "metrics.json"
        metrics_path.write_text(
            json.dumps(metrics_payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        membership_snapshots = {
            day: request.universe_resolver.snapshot_for(_as_date(day))
            for day in sorted(fold.membership_snapshot_sha256s)
        } if fold.membership_snapshot_sha256s else {}
        fold_manifest = {
            "fold_id": fold.fold_id,
            "calendar_start": fold.calendar_start.isoformat(),
            "calendar_end": fold.calendar_end.isoformat(),
            "first_trading_day": fold.first_trading_day.isoformat()
            if fold.first_trading_day
            else None,
            "last_trading_day": fold.last_trading_day.isoformat()
            if fold.last_trading_day
            else None,
            "warmup_calendar_start": fold.warmup_calendar_start.isoformat(),
            "warmup_calendar_end": fold.warmup_calendar_end.isoformat(),
            "warmup_session_count": fold.warmup_session_count,
            "oos_session_count": fold.oos_session_count,
            "daily_membership_snapshots": membership_snapshots,
            "initial_cash": request.initial_cash,
            "dataset_version": request.dataset_version,
            "declared_scenarios": list(request.spec.cost_scenarios),
            "status": "executed",
        }
        manifest_path = fold_dir / "fold_manifest.json"
        manifest_path.write_text(
            json.dumps(
                fold_manifest, ensure_ascii=False, indent=2, sort_keys=True
            ),
            encoding="utf-8",
        )
        return {
            name: sha256_file(fold_dir / name)
            for name in (
                "signals.parquet",
                "orders.parquet",
                "fills.parquet",
                "equity.parquet",
                "daily_returns.parquet",
                "metrics.json",
                "fold_manifest.json",
            )
        }


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


class _JsonPayload:
    """A write_canonical_json adapter for an already-JSON mapping."""

    def __init__(self, payload: Mapping) -> None:
        self._payload = dict(payload)

    def model_dump(self, mode: str = "json") -> dict:
        return self._payload


class _FactorDatasetAdapter:
    """A minimal ``FactorDataset`` over a precomputed observation frame."""

    def __init__(self, provider: Callable[[], pd.DataFrame]) -> None:
        self._provider = provider

    def factor_input(self) -> pd.DataFrame:
        return self._provider()


def _prepare_bars(bars: pd.DataFrame) -> pd.DataFrame:
    frame = bars.copy()
    frame["trade_date"] = [_as_date(day) for day in frame["trade_date"]]
    return frame.sort_values(["symbol", "trade_date"], kind="stable").reset_index(
        drop=True
    )


def _prepare_benchmarks(benchmarks: pd.DataFrame) -> dict[str, dict[date, float]]:
    index: dict[str, dict[date, float]] = {}
    if benchmarks is None or benchmarks.empty:
        return index
    frame = benchmarks.copy()
    frame["trade_date"] = [_as_date(day) for day in frame["trade_date"]]
    for row in frame.to_dict("records"):
        close = row.get("close")
        if close is None or pd.isna(close) or float(close) <= 0:
            continue
        index.setdefault(str(row["symbol"]), {})[row["trade_date"]] = float(close)
    return index


def _ledger_preflight(
    submitted: pd.DataFrame, fills: pd.DataFrame, rejections: pd.DataFrame
) -> None:
    """Every submitted order is accounted exactly once by fills/rejections."""
    submitted_ids = [str(value) for value in submitted["order_id"]]
    if len(set(submitted_ids)) != len(submitted_ids):
        raise OOSIntegrityError("submitted orders contain duplicate order ids")
    filled_by_id: dict[str, int] = {}
    for row in fills.to_dict("records"):
        filled_by_id[str(row["order_id"])] = (
            filled_by_id.get(str(row["order_id"]), 0) + int(row["quantity"])
        )
    rejected_by_id: dict[str, int] = {}
    for row in rejections.to_dict("records"):
        order_id = str(row["order_id"])
        if order_id in rejected_by_id:
            raise OOSIntegrityError(
                f"order {order_id} has multiple rejection records"
            )
        rejected_by_id[order_id] = int(row["rejected_quantity"])
    overlap = sorted(set(filled_by_id) & set(rejected_by_id))
    if overlap:
        raise OOSIntegrityError(
            f"orders {overlap} carry both fills and rejections"
        )
    for order_id, quantity in (
        (row["order_id"], int(row["quantity"]))
        for row in submitted.to_dict("records")
    ):
        filled = filled_by_id.get(str(order_id), 0)
        rejected = rejected_by_id.get(str(order_id), 0)
        if filled == 0 and rejected == 0:
            raise OOSIntegrityError(
                f"order {order_id} produced neither a fill nor a rejection"
            )
        if filled + rejected != quantity:
            raise OOSIntegrityError(
                f"order {order_id}: filled {filled} + rejected {rejected} != "
                f"submitted {quantity}"
            )


def _order_diffs(
    submitted: pd.DataFrame, fills: pd.DataFrame, rejections: pd.DataFrame
) -> pd.DataFrame:
    """One per-order submitted/filled/rejected row (ledger already checked)."""
    filled_by_id: dict[str, int] = {}
    for row in fills.to_dict("records"):
        filled_by_id[str(row["order_id"])] = (
            filled_by_id.get(str(row["order_id"]), 0) + int(row["quantity"])
        )
    rejected_by_id = {
        str(row["order_id"]): int(row["rejected_quantity"])
        for row in rejections.to_dict("records")
    }
    rows: list[dict] = []
    for row in submitted.to_dict("records"):
        order_id = str(row["order_id"])
        planned = int(row["quantity"])
        filled = filled_by_id.get(order_id, 0)
        rejected = rejected_by_id.get(order_id, 0)
        status = "FILLED" if filled == planned else (
            "REJECTED" if filled == 0 else "PARTIAL"
        )
        rows.append(
            {
                "order_id": order_id,
                "planned_quantity": planned,
                "filled_quantity": filled,
                "rejected_quantity": rejected,
                "status": status,
            }
        )
    frame = pd.DataFrame(
        rows,
        columns=["order_id", "planned_quantity", "filled_quantity",
                 "rejected_quantity", "status"],
    )
    for column in ("planned_quantity", "filled_quantity", "rejected_quantity"):
        frame[column] = frame[column].astype("int64")
    return frame


def _load_stages(run_dir: Path) -> dict:
    path = run_dir / _STAGES_FILE
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _outputs_intact(run_dir: Path, outputs: Mapping[str, str]) -> bool:
    for relative, expected in outputs.items():
        path = run_dir / relative
        if not path.is_file() or sha256_file(path) != expected:
            return False
    return True


def _load_fold_scenario(
    run_dir: Path,
    fold: FoldWindow,
    request: WalkForwardRequest,
    scenario: str,
    open_days: tuple[date, ...],
    stage: Mapping,
) -> dict:
    """Rebuild one fold-scenario's in-memory rows from its intact artifacts."""
    scenario_dir = run_dir / "backtest" / fold.fold_id / scenario
    submitted = pd.read_parquet(scenario_dir / "submitted_orders.parquet")
    fills = pd.read_parquet(scenario_dir / "fills.parquet")
    rejections = pd.read_parquet(scenario_dir / "rejections.parquet")
    equity = pd.read_parquet(scenario_dir / "equity.parquet")
    daily_returns = pd.read_parquet(scenario_dir / "daily_returns.parquet")
    diffs = pd.read_parquet(scenario_dir / "order_diffs.parquet")
    metrics = compute_fold_metrics(
        equity,
        fills,
        submitted_orders=submitted,
        order_diffs=diffs,
        initial_equity=float(request.initial_cash),
        expected_open_days=open_days,
        fold_id=fold.fold_id,
        scenario=scenario,
    )
    return {
        "submitted": submitted,
        "fills": fills,
        "rejections": rejections,
        "equity": equity,
        "daily_returns": daily_returns,
        "order_diffs": diffs,
        "metrics": metrics,
    }


def _as_date(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, pd.Timestamp):
        return value
    stamp = pd.Timestamp(value)
    if stamp is pd.NaT:
        raise ValueError(f"cannot interpret {value!r} as a date")
    return stamp.date()
