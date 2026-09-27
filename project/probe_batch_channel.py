#!/usr/bin/env python
"""Freeze the batch-channel constants from measurement (spec §6/§8).

Never run this without the owner's go-ahead: it logs in to the broker and
consumes quota.  Every reading is printed and written to an evidence JSON so
the frozen default can be traced to a measurement rather than to a guess.

Status: written, never executed (batched-validation-channel plan Task 10
Step 1).  Running any subcommand below is Step 2 and waits for the owner's
explicit authorization; until then nothing here may be executed, and no
measured value may be frozen into a sources.yml.

Every subcommand requires ``--symbols-file`` (one code per line, ``#``
comments allowed); there is no built-in code list.  Daily probes go through
``XingyaoSource(SourceConfig(...)).fetch_batch`` and factor probes through
``fetch_factor_frames`` -- the adapters own the private SDK and the
credential environment (``AD_USERNAME``/``AD_PASSWORD``/``AD_HOST``/
``AD_PORT``); this script never imports the SDK and never reads, prints,
logs or writes a credential.

Subcommands (plan Task 10 Step 1):
    probe-limit      --endpoint daily|backward_factor --symbols-file PATH
                     [--evidence PATH]  ... single-call code-count ceiling
    probe-absence    --symbols-file PATH [--evidence PATH]
                     ... is a window-without-trading-days code a key with an
                     empty frame, or an omitted key?
    probe-latency    --endpoint daily|backward_factor --symbols-file PATH
                     [--repeats N] [--evidence PATH]
                     ... full-chunk wall clock; suggests timeout = max x 3
    probe-counters   --symbols-file PATH [--evidence PATH]
                     ... one real round; sessions/code_queries readout

Exit codes: 0 a completed reading (including "every size was rejected");
1 the reading could not be completed (call failure); 2 authentication
rejected (credentials come from AD_* environment variables alone -- values
are never read or printed here); 3 unexpected failure.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shlex
import sys
import time
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import (
    AuthenticationError,
    ContractError,
    DataRequest,
    ServerError,
)
from stock_quant.data_sources.xingyao import XingyaoSource
from stock_quant.data_sources.xingyao_factor import fetch_factor_frames

RECORD_PATH = "docs/operations/2026-09-27-batched-channel-probes.md"

#: The plan's default probe window: 601238.SH's pure-suspension window
#: (phase-0 probe 1 measured nine suspended trading days, 2026-09-14..24).
DEFAULT_WINDOW_START = date(2026, 9, 14)
DEFAULT_WINDOW_END = date(2026, 9, 24)
DEFAULT_ABSENCE_SYMBOL = "601238.SH"
#: Phase-0 evidence: 601059.SH traded on 09-14/22/23/24 and was suspended
#: 09-15..09-21 inside the same window -- both kinds of day for the control.
DEFAULT_CONTROL_SYMBOL = "601059.SH"

#: probe-limit stepping plans.  Daily descends from the 1000-code ceiling
#: (SourceConfig's own batch-size bound); backward_factor climbs from a
#: small chunk.  A candidate that errors, drops a code, answers an
#: unrequested key, or truncates a control frame ends its direction.
DAILY_LIMIT_START = 1000
FACTOR_LIMIT_START = 50
#: SourceConfig caps both batch-size fields at 1000; nothing larger is
#: freezable, so nothing larger is probed.
BATCH_SIZE_CEILING = 1000

#: The probe's own worker bound -- deliberately generous against the
#: expected few-second calls and bounded against an unbounded hang.  This
#: is a probe-safety knob, never a frozen value.
DEFAULT_PROBE_TIMEOUT_SECONDS = 600
DEFAULT_REPEATS = 3
#: The commented candidate in both sources.yml files (not yet frozen).
DEFAULT_FACTOR_BATCH_SIZE = 200

#: Control codes re-fetched inside every candidate call: a drop in a
#: control's row count against its own small-call baseline is the
#: truncation signal (the supplier's observed failure shape is absent rows,
#: so a silent row cut is otherwise indistinguishable from suspensions).
CONTROL_CODE_COUNT = 3

_SYMBOL_PATTERN = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


# ----------------------------------------------------------------------- #
# Shared helpers                                                           #
# ----------------------------------------------------------------------- #


def load_symbols(path: Path) -> tuple[str, ...]:
    """One code per line; ``#`` starts a comment (whole line or trailing)."""
    symbols: list[str] = []
    seen: set[str] = set()
    duplicates = 0
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        content = raw.split("#", 1)[0].strip()
        if not content:
            continue
        if len(content.split()) != 1:
            raise ValueError(f"{path}:{lineno}: one code per line, got {content!r}")
        if not _SYMBOL_PATTERN.fullmatch(content):
            raise ValueError(
                f"{path}:{lineno}: {content!r} is not a canonical code "
                "(six digits + .SH/.SZ/.BJ)"
            )
        if content in seen:
            duplicates += 1
            continue
        seen.add(content)
        symbols.append(content)
    if not symbols:
        raise ValueError(f"{path}: no codes found")
    if duplicates:
        print(f"symbols: {len(symbols)} unique codes ({duplicates} duplicates dropped)")
    return tuple(symbols)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _command_line() -> str:
    """The exact argv; credentials arrive via the environment, never argv."""
    return shlex.join(sys.argv)


def write_evidence(path: Path, section: str, payload: dict[str, object]) -> None:
    """Merge one completed reading into the record's evidence JSON.

    An existing file is updated section-wise, never clobbered: a file that
    is not the expected shape aborts instead of being overwritten.  The
    skeleton ships with ``_status: "awaiting_authorization"``; the first
    merged reading flips it to ``"measured"``.
    """
    if path.exists():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SystemExit(
                f"evidence file {path} is not valid JSON; refusing to "
                f"overwrite it ({error})"
            )
        probes = document.get("probes") if isinstance(document, dict) else None
        if not isinstance(probes, dict):
            raise SystemExit(
                f"evidence file {path} is not probe-evidence shaped "
                "(no 'probes' object); refusing to overwrite it"
            )
    else:
        probes = {}
        document = {"record": RECORD_PATH, "probes": probes}
    probes[section] = payload
    document["_status"] = "measured"
    document["last_updated_utc"] = payload["measured_at_utc"]
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"evidence: section {section!r} written to {path}")


def _daily_config(code_count: int, timeout_seconds: int) -> SourceConfig:
    """The batch pair under measurement, kept together as SourceConfig demands."""
    return SourceConfig(
        batch_size=min(code_count, BATCH_SIZE_CEILING),
        batch_timeout_seconds=timeout_seconds,
    )


def _factor_config(code_count: int, timeout_seconds: int) -> SourceConfig:
    return SourceConfig(
        factor_batch_size=min(code_count, BATCH_SIZE_CEILING),
        factor_batch_timeout_seconds=timeout_seconds,
    )


def _daily_requests(
    symbols: Sequence[str], start: date, end: date
) -> list[DataRequest]:
    return [
        DataRequest(endpoint="daily", symbols=(symbol,), start_date=start, end_date=end)
        for symbol in symbols
    ]


class _Counters:
    """The lane's per-attempt counter row, kept by the probe itself.

    Mirrors ``data_pipeline._transport_attempt``: one worker start is one
    session and one code query, counted before the call it belongs to, so
    failed starts count too.
    """

    def __init__(self) -> None:
        self.sessions = 0
        self.code_queries = 0
        self.attempt_code_counts: list[int] = []

    def hook(self, code_count: int) -> None:
        self.sessions += 1
        self.code_queries += 1
        self.attempt_code_counts.append(code_count)

    def as_dict(self) -> dict[str, object]:
        return {
            "sessions": self.sessions,
            "code_queries": self.code_queries,
            "attempt_code_counts": list(self.attempt_code_counts),
        }


def _report(title: str, rows: Sequence[tuple[str, object]]) -> None:
    print(f"== {title}")
    for key, value in rows:
        if isinstance(value, (dict, list)):
            print(f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
        else:
            print(f"{key}: {value}")


def _window_field(window_start: date, window_end: date) -> dict[str, str]:
    return {"start": window_start.isoformat(), "end": window_end.isoformat()}


# ----------------------------------------------------------------------- #
# probe-limit                                                              #
# ----------------------------------------------------------------------- #


def _daily_candidates(code_count: int) -> list[int]:
    """Descend from the ceiling by halving down to one code."""
    candidates: list[int] = []
    size = min(DAILY_LIMIT_START, code_count)
    while size >= 1:
        candidates.append(size)
        size //= 2
    return candidates


def _factor_candidates() -> list[int]:
    """Climb from a small chunk by doubling up to the freezable ceiling."""
    candidates: list[int] = []
    size = FACTOR_LIMIT_START
    while size < BATCH_SIZE_CEILING:
        candidates.append(size)
        size *= 2
    candidates.append(BATCH_SIZE_CEILING)
    return candidates


def _daily_baseline(
    controls: Sequence[str], start: date, end: date, timeout_seconds: int
) -> dict[str, int]:
    """Row counts of a few codes from their own small call."""
    source = XingyaoSource(_daily_config(len(controls), timeout_seconds))
    batch = source.fetch_batch(_daily_requests(controls, start, end))
    baseline: dict[str, int] = {}
    for outcome in batch.outcomes:
        if outcome.status not in ("ok", "empty") or outcome.result is None:
            raise SystemExit(
                f"baseline call failed for {outcome.symbol} ({outcome.message}); "
                "the endpoint is not measurable right now"
            )
        baseline[outcome.symbol] = len(outcome.result.frame)
    return baseline


def _daily_trial(
    codes: Sequence[str],
    start: date,
    end: date,
    timeout_seconds: int,
    baseline: dict[str, int],
) -> tuple[bool, str | None, float]:
    """One candidate call; success means no error, no dropped code and no
    control-frame truncation."""
    source = XingyaoSource(_daily_config(len(codes), timeout_seconds))
    started = time.perf_counter()
    try:
        batch = source.fetch_batch(_daily_requests(codes, start, end))
    except AuthenticationError:
        raise
    except (ServerError, ContractError) as error:
        reason = f"call failed: {type(error).__name__}: {error}"
        return False, reason, time.perf_counter() - started
    elapsed = time.perf_counter() - started
    refused = [outcome.symbol for outcome in batch.outcomes if outcome.status == "refused"]
    if refused:
        reason = f"supplier answer missing/unusable for: {', '.join(refused)}"
        return False, reason, elapsed
    shrunk = sorted(
        outcome.symbol
        for outcome in batch.outcomes
        if outcome.status in ("ok", "empty")
        and outcome.symbol in baseline
        and outcome.result is not None
        and len(outcome.result.frame) != baseline[outcome.symbol]
    )
    if shrunk:
        reason = (
            "control frame row count changed against its own small-call "
            f"baseline (truncation suspected): {', '.join(shrunk)}"
        )
        return False, reason, elapsed
    return True, None, elapsed


def _factor_trial(
    codes: Sequence[str], timeout_seconds: int, end: date | None
) -> tuple[bool, str | None, float, dict[str, int]]:
    config = _factor_config(len(codes), timeout_seconds)
    started = time.perf_counter()
    try:
        frames = fetch_factor_frames(
            codes,
            timeout_seconds=float(
                config.factor_batch_timeout_seconds or timeout_seconds
            ),
            end=end,
        )
    except AuthenticationError:
        raise
    except (ServerError, ContractError) as error:
        reason = f"call failed: {type(error).__name__}: {error}"
        return False, reason, time.perf_counter() - started, {}
    elapsed = time.perf_counter() - started
    requested = set(codes)
    missing = [code for code in codes if code not in frames]
    if missing:
        reason = f"supplier answer omitted: {', '.join(missing)}"
        return False, reason, elapsed, {}
    extra = sorted(set(frames) - requested)
    if extra:
        reason = f"supplier answer carried unrequested keys: {', '.join(extra)}"
        return False, reason, elapsed, {}
    return True, None, elapsed, {code: len(frames[code]) for code in codes}


def run_probe_limit(
    endpoint: str,
    symbols: tuple[str, ...],
    symbols_file: Path,
    evidence_path: Path | None,
    window_start: date,
    window_end: date,
    factor_end: date | None,
    timeout_seconds: int,
) -> None:
    trials: list[dict[str, object]] = []
    controls = symbols[:CONTROL_CODE_COUNT]
    if endpoint == "daily":
        baseline = _daily_baseline(controls, window_start, window_end, timeout_seconds)
        max_acceptable: int | None = None
        last_rejected: int | None = None
        for size in _daily_candidates(len(symbols)):
            accepted, reason, elapsed = _daily_trial(
                symbols[:size], window_start, window_end, timeout_seconds, baseline
            )
            trials.append(
                {
                    "codes": size,
                    "accepted": accepted,
                    "reason": reason,
                    "elapsed_seconds": round(elapsed, 3),
                }
            )
            print(f"trial codes={size} accepted={accepted} reason={reason} elapsed_s={elapsed:.3f}")
            if accepted:
                max_acceptable = size
                break
            last_rejected = size
        if max_acceptable is not None:
            verdict = f"descending step plan: {max_acceptable} codes accepted in one call"
            if last_rejected is not None:
                verdict += (
                    f" (a {last_rejected}-code call was rejected; the exact boundary "
                    "lies in between and was not refined to spare quota)"
                )
        else:
            verdict = "no size accepted, even a single code failed"
    else:
        baseline_ok, baseline_reason, _elapsed, baseline = _factor_trial(
            controls, timeout_seconds, factor_end
        )
        if not baseline_ok:
            raise SystemExit(
                f"baseline call failed ({baseline_reason}); the endpoint is "
                "not measurable right now"
            )
        max_acceptable = None
        for size in _factor_candidates():
            if size > len(symbols):
                trials.append(
                    {
                        "codes": size,
                        "accepted": None,
                        "reason": "skipped: the symbols file holds fewer codes",
                        "elapsed_seconds": None,
                    }
                )
                continue
            accepted, reason, elapsed, _rows = _factor_trial(
                symbols[:size], timeout_seconds, factor_end
            )
            trials.append(
                {
                    "codes": size,
                    "accepted": accepted,
                    "reason": reason,
                    "elapsed_seconds": round(elapsed, 3),
                }
            )
            print(f"trial codes={size} accepted={accepted} reason={reason} elapsed_s={elapsed:.3f}")
            if not accepted:
                break
            max_acceptable = size
        verdict = (
            f"ascending step plan: {max_acceptable} codes accepted in one call"
            if max_acceptable is not None
            else "no size accepted, even the smallest chunk failed"
        )
    feeds = "batch_size" if endpoint == "daily" else "factor_batch_size"
    _report(
        f"probe-limit ({endpoint})",
        [
            ("max_acceptable_codes", max_acceptable),
            ("feeds", feeds),
            ("verdict", verdict),
        ],
    )
    if evidence_path is not None:
        window = (
            _window_field(window_start, window_end)
            if endpoint == "daily"
            else {"end": factor_end.isoformat() if factor_end else "supplier default (today)"}
        )
        write_evidence(
            evidence_path,
            f"probe_limit_{endpoint}",
            {
                "measured_at_utc": _utc_now(),
                "command": _command_line(),
                "endpoint": endpoint,
                "symbols_file": str(symbols_file),
                "code_count": len(symbols),
                "window": window,
                "trials": trials,
                "max_acceptable_codes": max_acceptable,
                "verdict": verdict,
            },
        )


# ----------------------------------------------------------------------- #
# probe-absence                                                            #
# ----------------------------------------------------------------------- #


def run_probe_absence(
    symbols: tuple[str, ...],
    symbols_file: Path,
    evidence_path: Path | None,
    window_start: date,
    window_end: date,
    absence_symbol: str,
    control_symbol: str,
    timeout_seconds: int,
) -> None:
    if absence_symbol == control_symbol:
        raise SystemExit("absence and control symbols must differ")
    #: The two probe codes lead the call so a tail-truncating supplier could
    #: never silently drop exactly the codes the reading is about.
    codes = list(dict.fromkeys((absence_symbol, control_symbol, *symbols)))
    source = XingyaoSource(_daily_config(len(codes), timeout_seconds))
    batch = source.fetch_batch(_daily_requests(codes, window_start, window_end))
    outcomes = {outcome.symbol: outcome for outcome in batch.outcomes}
    answered = [code for code in codes if outcomes[code].status in ("ok", "empty")]
    row_counts = {
        code: len(outcomes[code].result.frame)
        for code in answered
        if outcomes[code].result is not None
    }
    refused = [
        {"symbol": outcome.symbol, "message": outcome.message}
        for outcome in batch.outcomes
        if outcome.status == "refused"
    ]
    absence_status = outcomes[absence_symbol].status
    control_status = outcomes[control_symbol].status
    if absence_status == "empty":
        verdict = (
            "branch 1: the no-trading-days code came back as a key with an "
            "empty frame; keep the empty branch and its test"
        )
    elif absence_status == "refused":
        verdict = (
            "branch 2: the no-trading-days code came back as no key at all; "
            "STOP and report to the owner -- the fail-closed default would "
            "record a per-symbol refused for every long-suspended code each "
            "round; accept that noise or let the owner rule on a separate "
            "state; never reclassify it as empty"
        )
    else:
        verdict = (
            "unexpected: the no-trading-days code came back with rows; "
            "report to the owner"
        )
    if control_status == "refused":
        verdict += " (control refused -- the reading is unreliable)"
    _report(
        "probe-absence",
        [
            ("window", f"{window_start.isoformat()}..{window_end.isoformat()}"),
            ("codes_in_call", len(codes)),
            ("answered_mapping_keys", len(answered)),
            ("mapping_key_row_counts", row_counts),
            ("refused", refused),
            (f"absence_status[{absence_symbol}]", absence_status),
            (f"control_status[{control_symbol}]", control_status),
            ("verdict", verdict),
        ],
    )
    if evidence_path is not None:
        write_evidence(
            evidence_path,
            "probe_absence",
            {
                "measured_at_utc": _utc_now(),
                "command": _command_line(),
                "symbols_file": str(symbols_file),
                "code_count": len(codes),
                "absence_symbol": absence_symbol,
                "control_symbol": control_symbol,
                "window": _window_field(window_start, window_end),
                "answered_keys": answered,
                "row_counts": row_counts,
                "refused": refused,
                "absence_status": absence_status,
                "control_status": control_status,
                "verdict": verdict,
            },
        )


# ----------------------------------------------------------------------- #
# probe-latency                                                            #
# ----------------------------------------------------------------------- #


def run_probe_latency(
    endpoint: str,
    symbols: tuple[str, ...],
    symbols_file: Path,
    evidence_path: Path | None,
    repeats: int,
    window_start: date,
    window_end: date,
    factor_end: date | None,
    timeout_seconds: int,
) -> None:
    if len(symbols) > BATCH_SIZE_CEILING:
        raise SystemExit(
            f"the symbols file holds {len(symbols)} codes; one full chunk may "
            f"not exceed the freezable batch-size bound ({BATCH_SIZE_CEILING}); "
            "trim the file"
        )
    attempts: list[dict[str, object]] = []
    for index in range(1, repeats + 1):
        anomalies: list[str] = []
        started = time.perf_counter()
        try:
            if endpoint == "daily":
                batch = XingyaoSource(
                    _daily_config(len(symbols), timeout_seconds)
                ).fetch_batch(_daily_requests(symbols, window_start, window_end))
                anomalies = [
                    outcome.symbol
                    for outcome in batch.outcomes
                    if outcome.status == "refused"
                ]
            else:
                frames = fetch_factor_frames(
                    symbols,
                    timeout_seconds=float(
                        _factor_config(len(symbols), timeout_seconds)
                        .factor_batch_timeout_seconds
                        or timeout_seconds
                    ),
                    end=factor_end,
                )
                anomalies = [code for code in symbols if code not in frames]
        except AuthenticationError:
            raise
        except (ServerError, ContractError) as error:
            attempts.append(
                {
                    "repeat": index,
                    "elapsed_seconds": None,
                    "reason": f"{type(error).__name__}: {error}",
                }
            )
            print(f"repeat {index} FAILED ({type(error).__name__})")
            continue
        elapsed = time.perf_counter() - started
        attempts.append(
            {
                "repeat": index,
                "elapsed_seconds": round(elapsed, 3),
                "codes_without_answer": anomalies,
            }
        )
        print(f"repeat {index} elapsed_s={elapsed:.3f} codes_without_answer={len(anomalies)}")
    completed: list[float] = []
    clean: list[float] = []
    for attempt in attempts:
        elapsed = attempt.get("elapsed_seconds")
        if not isinstance(elapsed, (int, float)):
            continue
        completed.append(float(elapsed))
        if not attempt.get("codes_without_answer"):
            clean.append(float(elapsed))
    timed = clean or completed
    max_elapsed = max(timed) if timed else None
    suggested: int | None = None
    timeout_field = (
        "batch_timeout_seconds" if endpoint == "daily" else "factor_batch_timeout_seconds"
    )
    if max_elapsed is None:
        verdict = "no repeat completed; no timeout can be suggested"
    else:
        suggested = math.ceil(max_elapsed * 3)
        verdict = f"max full-chunk wall clock {max_elapsed:.3f}s over the repeats"
        if not clean:
            verdict += " (no repeat answered every code; the max includes incomplete answers)"
        elif len(clean) < len(completed):
            verdict += " (max over the repeats that answered every code)"
        verdict += f"; suggested {timeout_field} = max x 3 = {suggested}"
        if suggested > 3600:
            verdict += " -- exceeds SourceConfig's 3600s bound; the chunk is too heavy"
    _report(
        f"probe-latency ({endpoint})",
        [
            ("repeats", repeats),
            ("max_elapsed_seconds", max_elapsed),
            ("suggested_timeout_seconds", suggested),
            ("feeds", timeout_field),
            ("verdict", verdict),
        ],
    )
    if evidence_path is not None:
        window = (
            _window_field(window_start, window_end)
            if endpoint == "daily"
            else {"end": factor_end.isoformat() if factor_end else "supplier default (today)"}
        )
        write_evidence(
            evidence_path,
            f"probe_latency_{endpoint}",
            {
                "measured_at_utc": _utc_now(),
                "command": _command_line(),
                "endpoint": endpoint,
                "symbols_file": str(symbols_file),
                "code_count": len(symbols),
                "window": window,
                "repeats": repeats,
                "attempts": attempts,
                "max_elapsed_seconds": max_elapsed,
                "suggested_timeout_seconds": suggested,
                "timeout_field": timeout_field,
                "verdict": verdict,
            },
        )


# ----------------------------------------------------------------------- #
# probe-counters                                                           #
# ----------------------------------------------------------------------- #


def run_probe_counters(
    symbols: tuple[str, ...],
    symbols_file: Path,
    evidence_path: Path | None,
    window_start: date,
    window_end: date,
    factor_end: date | None,
    timeout_seconds: int,
    factor_batch_size: int,
) -> None:
    daily = _Counters()
    batch = XingyaoSource(_daily_config(len(symbols), timeout_seconds)).fetch_batch(
        _daily_requests(symbols, window_start, window_end),
        on_attempt=daily.hook,
    )
    daily_status_counts: dict[str, int] = {}
    for outcome in batch.outcomes:
        daily_status_counts[outcome.status] = daily_status_counts.get(outcome.status, 0) + 1

    factor = _Counters()
    chunks: list[dict[str, object]] = []
    missing_total = 0
    for start in range(0, len(symbols), factor_batch_size):
        chunk = list(symbols[start : start + factor_batch_size])
        factor.hook(len(chunk))  # counted before the call, as the lane counts
        frames = fetch_factor_frames(
            chunk,
            timeout_seconds=float(
                _factor_config(factor_batch_size, timeout_seconds)
                .factor_batch_timeout_seconds
                or timeout_seconds
            ),
            end=factor_end,
        )
        missing = [code for code in chunk if code not in frames]
        missing_total += len(missing)
        chunks.append({"codes": len(chunk), "returned": len(frames), "missing": missing})

    expected_factor_sessions = math.ceil(len(symbols) / factor_batch_size)
    daily_expected = 1
    daily_ok = (
        daily.sessions == daily_expected
        and daily.code_queries == daily_expected
        and daily.attempt_code_counts == [len(symbols)]
    )
    factor_ok = (
        factor.sessions == expected_factor_sessions
        and factor.code_queries == expected_factor_sessions
    )
    verdict = (
        f"daily counters {'match' if daily_ok else 'MISMATCH against'} the lane "
        "model (one multi-code call = 1 session / 1 code query); factor "
        f"counters {'match' if factor_ok else 'MISMATCH against'} ceil("
        f"{len(symbols)}/{factor_batch_size}) = {expected_factor_sessions}"
    )
    if missing_total:
        verdict += f"; {missing_total} code(s) the factor answer omitted"
    _report(
        "probe-counters",
        [
            (
                "daily",
                {**daily.as_dict(), "expected": daily_expected, "status_counts": daily_status_counts},
            ),
            (
                "factor",
                {
                    **factor.as_dict(),
                    "factor_batch_size": factor_batch_size,
                    "expected": expected_factor_sessions,
                    "chunks": chunks,
                },
            ),
            ("verdict", verdict),
        ],
    )
    if evidence_path is not None:
        write_evidence(
            evidence_path,
            "probe_counters",
            {
                "measured_at_utc": _utc_now(),
                "command": _command_line(),
                "symbols_file": str(symbols_file),
                "code_count": len(symbols),
                "window": _window_field(window_start, window_end),
                "factor_end": (
                    factor_end.isoformat() if factor_end else "supplier default (today)"
                ),
                "daily": {
                    **daily.as_dict(),
                    "expected": daily_expected,
                    "status_counts": daily_status_counts,
                },
                "factor": {
                    **factor.as_dict(),
                    "factor_batch_size": factor_batch_size,
                    "expected": expected_factor_sessions,
                    "chunks": chunks,
                },
                "verdict": verdict,
            },
        )


# ----------------------------------------------------------------------- #
# CLI                                                                      #
# ----------------------------------------------------------------------- #


def _positive_timeout(value: str) -> int:
    seconds = int(value)
    if not 1 <= seconds <= 3600:
        raise argparse.ArgumentTypeError(
            "timeout must be within 1..3600 (SourceConfig bound)"
        )
    return seconds


def _window_pair(args: argparse.Namespace) -> None:
    if args.window_start > args.window_end:
        raise SystemExit("--window-start must not be after --window-end")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="probe_batch_channel.py",
        description=(
            "Batch-channel probes for freezing the spec §6/§8 constants. "
            "NOT RUN YET: every subcommand logs in to the broker and consumes "
            "quota -- never execute one without the owner's explicit "
            "authorization (plan Task 10 Step 2)."
        ),
        epilog=(
            "Symbols come from a required --symbols-file (one code per line, "
            "# comments allowed); there is no built-in code list. Credentials "
            "are read by the adapters alone, from the AD_USERNAME/AD_PASSWORD/"
            "AD_HOST/AD_PORT environment variables; this script never reads, "
            "prints, logs or writes them and never imports the private SDK. "
            "Exit codes: 0 completed reading, 1 reading not completed, "
            "2 authentication rejected, 3 unexpected failure."
        ),
    )
    sub = parser.add_subparsers(dest="probe", required=True)

    def common_arguments(sub_parser: argparse.ArgumentParser) -> None:
        sub_parser.add_argument(
            "--symbols-file",
            required=True,
            type=Path,
            help="one canonical code per line (NNNNNN.SH/.SZ/.BJ), # comments allowed",
        )
        sub_parser.add_argument(
            "--evidence",
            type=Path,
            help="merge this reading's JSON into the given evidence file",
        )
        sub_parser.add_argument(
            "--window-start",
            type=date.fromisoformat,
            default=DEFAULT_WINDOW_START,
            help="daily window start (default: 2026-09-14, the plan's default window)",
        )
        sub_parser.add_argument(
            "--window-end",
            type=date.fromisoformat,
            default=DEFAULT_WINDOW_END,
            help="daily window end (default: 2026-09-24)",
        )
        sub_parser.add_argument(
            "--factor-end",
            type=date.fromisoformat,
            default=None,
            help="backward_factor clip end (default: the adapter's own today)",
        )
        sub_parser.add_argument(
            "--timeout-seconds",
            type=_positive_timeout,
            default=DEFAULT_PROBE_TIMEOUT_SECONDS,
            help=(
                "the probe's own killable-worker bound, not a frozen value "
                f"(default: {DEFAULT_PROBE_TIMEOUT_SECONDS})"
            ),
        )

    limit = sub.add_parser(
        "probe-limit",
        help="find the largest code count one call accepts",
        description=(
            "Daily descends from 1000 codes by halving; backward_factor climbs "
            "from a small chunk by doubling up to the freezable ceiling of "
            "1000. A candidate stops its direction on an error, a missing key, "
            "an unrequested key, or a control frame truncated against its own "
            "small-call baseline; the largest accepted count is printed."
        ),
    )
    limit.add_argument(
        "--endpoint",
        required=True,
        choices=("daily", "backward_factor"),
    )
    common_arguments(limit)
    limit.set_defaults(handler=run_probe_limit)

    absence = sub.add_parser(
        "probe-absence",
        help="does a no-trading-days code come back as an empty frame or as no key",
        description=(
            "One multi-code daily call over the default pure-suspension window "
            "(601238.SH, 2026-09-14..2026-09-24) with a mixed-window control "
            "(601059.SH, trading and suspended days both); prints the returned "
            "mapping's answered key set with per-key row counts and the "
            "branch verdict."
        ),
    )
    common_arguments(absence)
    absence.add_argument(
        "--absence-symbol",
        default=DEFAULT_ABSENCE_SYMBOL,
        help=f"code with no trading day in the window (default: {DEFAULT_ABSENCE_SYMBOL})",
    )
    absence.add_argument(
        "--control-symbol",
        default=DEFAULT_CONTROL_SYMBOL,
        help=(
            "code with trading and suspended days in the window "
            f"(default: {DEFAULT_CONTROL_SYMBOL})"
        ),
    )
    absence.set_defaults(handler=run_probe_absence)

    latency = sub.add_parser(
        "probe-latency",
        help="wall clock of one full-chunk call, repeated",
        description=(
            "Times a whole-chunk call (every code in the symbols file in one "
            "call) repeats times and reports the MAX, suggesting "
            "batch_timeout_seconds (daily) or factor_batch_timeout_seconds "
            "(backward_factor) = max x 3."
        ),
    )
    latency.add_argument(
        "--endpoint",
        required=True,
        choices=("daily", "backward_factor"),
    )
    common_arguments(latency)
    latency.add_argument(
        "--repeats",
        type=int,
        default=DEFAULT_REPEATS,
        help=f"how many timed calls (default: {DEFAULT_REPEATS})",
    )
    latency.set_defaults(handler=run_probe_latency)

    counters = sub.add_parser(
        "probe-counters",
        help="one real round with the lane's session/code-query counters read out",
        description=(
            "One real daily multi-code call (sessions/code_queries expected 1) "
            "and one chunked factor round (expected ceil(candidates / "
            "factor_batch_size) sessions), counted exactly as the lane's "
            "on_attempt hook counts them."
        ),
    )
    common_arguments(counters)
    counters.add_argument(
        "--factor-batch-size",
        type=int,
        default=DEFAULT_FACTOR_BATCH_SIZE,
        help=(
            "chunk size for the factor round, mirroring the lane "
            f"(default: {DEFAULT_FACTOR_BATCH_SIZE}, the commented candidate "
            "in both sources.yml)"
        ),
    )
    counters.set_defaults(handler=run_probe_counters)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _window_pair(args)
    try:
        symbols = load_symbols(args.symbols_file)
    except (OSError, ValueError) as error:
        print(f"symbols file error: {error}")
        return 1
    if args.probe == "probe-limit":
        handler = run_probe_limit
        kwargs = {"endpoint": args.endpoint}
    elif args.probe == "probe-absence":
        handler = run_probe_absence
        kwargs = {
            "absence_symbol": args.absence_symbol,
            "control_symbol": args.control_symbol,
        }
    elif args.probe == "probe-latency":
        handler = run_probe_latency
        kwargs = {"endpoint": args.endpoint, "repeats": args.repeats}
    else:
        handler = run_probe_counters
        kwargs = {"factor_batch_size": args.factor_batch_size}
    try:
        handler(
            symbols,
            args.symbols_file,
            args.evidence,
            args.window_start,
            args.window_end,
            args.factor_end,
            args.timeout_seconds,
            **kwargs,
        )
    except AuthenticationError as error:
        print(f"authentication rejected: {error}")
        print(
            "(credentials are read by the adapters from the AD_* environment "
            "variables alone; values are never read, printed or written here)"
        )
        return 2
    except (ServerError, ContractError) as error:
        print(f"supplier call failed: {type(error).__name__}: {error}")
        return 1
    except Exception as error:  # noqa: BLE001 - report, never traceback-leak
        print(f"unexpected failure ({type(error).__name__}): {error}")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
