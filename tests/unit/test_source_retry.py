from datetime import date

import pandas as pd
import pytest

from stock_quant.data_sources.base import (
    AuthenticationError,
    DataRequest,
    FetchResult,
    RateLimitError,
    RetryPolicy,
    fetch_with_retry,
    translate_supplier_error,
)


class FakeSource:
    name = "fake"

    def __init__(self):
        self.failures: list[Exception | None] = []
        self.calls = 0

    def fetch(self, request: DataRequest) -> FetchResult:
        self.calls += 1
        outcome = self.failures.pop(0)
        if outcome is not None:
            raise outcome
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key="fake-request",
            frame=pd.DataFrame({"symbol": [request.symbols[0]]}),
            metadata={"transport_id": self.name},
        )


@pytest.fixture
def fake_source() -> FakeSource:
    return FakeSource()


@pytest.fixture
def data_request() -> DataRequest:
    return DataRequest(
        endpoint="daily",
        symbols=("000001.SZ",),
        start_date=date(2020, 1, 1),
        end_date=date(2020, 1, 2),
    )


def test_retry_retries_rate_limit_but_not_authentication(fake_source, data_request):
    """Retry classification must never repeat calls rejected for bad credentials."""
    fake_source.failures = [RateLimitError("slow"), None]
    sleeps: list[float] = []

    assert (
        fetch_with_retry(
            fake_source,
            data_request,
            RetryPolicy(max_attempts=3),
            sleeper=sleeps.append,
        ).source
        == "fake"
    )
    assert fake_source.calls == 2
    assert sleeps == [1.0]

    fake_source.failures = [AuthenticationError("bad token")]
    with pytest.raises(AuthenticationError):
        fetch_with_retry(
            fake_source,
            data_request,
            RetryPolicy(max_attempts=3),
            sleeper=sleeps.append,
        )
    assert fake_source.calls == 3
    assert sleeps == [1.0]


def test_retry_policy_refuses_limits_beyond_the_supplier_contract():
    """A caller cannot override the three-attempt, 30-second safety limits."""
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=4)
    with pytest.raises(ValueError):
        RetryPolicy(maximum_wait_seconds=31)


def test_type_and_parameter_errors_escape_without_retry(fake_source, data_request):
    """Message text must not turn caller mistakes into retryable source failures."""
    type_error = TypeError("connection argument has the wrong type")
    parameter_error = ValueError("rate limit parameter is invalid")

    assert translate_supplier_error(type_error) is type_error
    assert translate_supplier_error(parameter_error) is parameter_error

    fake_source.failures = [parameter_error]
    with pytest.raises(ValueError, match="rate limit parameter"):
        fetch_with_retry(fake_source, data_request, RetryPolicy(max_attempts=3))
    assert fake_source.calls == 1
