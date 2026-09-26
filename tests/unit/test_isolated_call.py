"""The hard-timeout boundary: a supplier call the parent can always kill."""

from __future__ import annotations

import os
import time

import pytest

from stock_quant.data_sources._isolated import run_isolated
from stock_quant.data_sources.base import (
    AuthenticationError,
    ContractError,
    ServerError,
)


def _answer(value: int) -> int:
    return value * 2


def _never_returns() -> int:
    while True:  # noqa: PLW0127 - deliberate
        time.sleep(0.05)


def _raises_auth() -> None:
    raise AuthenticationError("bad credentials")


def _raises_contract() -> None:
    raise ContractError("unexpected frame")


def _raises_arbitrary() -> None:
    raise RuntimeError("vendor exploded")


def _leaks_environment() -> dict[str, str]:
    return dict(os.environ)


def test_a_finished_call_returns_its_payload_in_the_parent() -> None:
    assert run_isolated(_answer, timeout_seconds=10, value=21) == 42


def test_a_call_that_never_returns_is_killed_and_reported_transient() -> None:
    started = time.monotonic()
    with pytest.raises(ServerError) as caught:
        run_isolated(_never_returns, timeout_seconds=1)
    elapsed = time.monotonic() - started
    assert "timeout" in str(caught.value).lower()
    assert elapsed < 5, f"the parent waited {elapsed:.1f}s past its own bound"


def test_an_authentication_failure_stays_permanent() -> None:
    with pytest.raises(AuthenticationError):
        run_isolated(_raises_auth, timeout_seconds=10)


def test_a_contract_failure_stays_a_contract_failure() -> None:
    with pytest.raises(ContractError):
        run_isolated(_raises_contract, timeout_seconds=10)


def test_an_unexpected_child_failure_is_reported_transient_with_its_type() -> None:
    with pytest.raises(ServerError) as caught:
        run_isolated(_raises_arbitrary, timeout_seconds=10)
    assert "RuntimeError" in str(caught.value)


def test_the_child_payload_is_what_the_target_returned_never_the_environment() -> None:
    os.environ["AD_PASSWORD"] = "must-not-travel"
    try:
        leaked = run_isolated(_leaks_environment, timeout_seconds=10)
    finally:
        os.environ.pop("AD_PASSWORD", None)
    assert leaked["AD_PASSWORD"] == "must-not-travel"
