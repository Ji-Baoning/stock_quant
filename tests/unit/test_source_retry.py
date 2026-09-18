import socket
import time
from datetime import date

import pandas as pd
import pytest
import requests

from stock_quant.config import SourceConfig
from stock_quant.data_sources.akshare import AkShareSource
from stock_quant.data_sources.base import (
    AuthenticationError,
    DataRequest,
    FetchResult,
    RateLimitError,
    RetryPolicy,
    ServerError,
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
    with pytest.raises(ValueError):
        RetryPolicy(call_timeout_seconds=0)
    with pytest.raises(ValueError):
        RetryPolicy(call_timeout_seconds=121)


def _silent_peer() -> tuple[socket.socket, str]:
    """A peer that completes the TCP handshake and then never answers.

    This is the shape that cost the 2026-09-17 rebuild seven hours: the kernel
    finishes the connection from the listen backlog, so the client sees an open
    socket rather than a refused one, and waits forever for bytes.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    return listener, f"http://127.0.0.1:{listener.getsockname()[1]}/index"


class SilentHttpSource:
    """A source whose SDK call is a bare ``requests.get`` -- the akshare shape.

    ``fetch`` translates its own expiry the way ``AkShareSource.fetch`` does
    (akshare 1.18.23 ``stock_zh_index_daily_em`` is exactly this call), so these
    tests exercise the retry boundary rather than the adapter's translation.
    """

    name = "silent-http"

    def __init__(self, url: str, own_timeout: float | None = None) -> None:
        self.url = url
        self.own_timeout = own_timeout
        self.calls = 0

    def fetch(self, request: DataRequest) -> FetchResult:
        self.calls += 1
        try:
            if self.own_timeout is None:
                requests.get(self.url)
            else:
                requests.get(self.url, timeout=self.own_timeout)
        except Exception as error:  # noqa: BLE001 - adapter translation boundary
            raise translate_supplier_error(error) from None
        raise AssertionError("a silent peer cannot answer")


def test_a_silent_supplier_cannot_hold_the_run(data_request):
    """The call itself is bounded, so silence costs seconds instead of hours.

    Nothing bounded these calls before: ``config.timeout_seconds`` was never
    passed down to the SDK, and the process socket default does not apply to
    ``requests``, which re-sets the socket timeout explicitly.
    """
    listener, url = _silent_peer()
    source = SilentHttpSource(url)
    before = requests.sessions.Session.request
    started = time.monotonic()
    try:
        with pytest.raises(ServerError):
            fetch_with_retry(
                source,
                data_request,
                RetryPolicy(max_attempts=2, call_timeout_seconds=1),
                sleeper=lambda _: None,
            )
    finally:
        listener.close()
        elapsed = time.monotonic() - started

    assert source.calls == 2, "an expired call is transient and must be retried"
    assert elapsed < 10, "the bound, not the peer's silence, must end the call"
    assert requests.sessions.Session.request is before, "the shim must not leak"


class HangingAkClient:
    """An AKShare client whose index interface is a bare ``requests.get``.

    ``stock_zh_index_daily_em`` really is one, in akshare 1.18.23: no session,
    no ``timeout``, no retry wrapper.  So the adapter cannot leave bounding it
    to the caller.
    """

    __version__ = "ak-hang"

    def __init__(self, url: str) -> None:
        self.url = url

    def stock_zh_index_daily_em(self, **kwargs):
        requests.get(self.url)
        raise AssertionError("a silent peer cannot answer")


def test_the_adapter_bounds_its_own_sdk_calls(data_request):
    """``config.timeout_seconds`` must reach the SDK, not only the retry loop.

    The adapter is also driven without ``fetch_with_retry`` -- ``project/``'s
    read-only probes call ``source.fetch`` directly -- so the bound belongs at
    the boundary that owns the config.  Before the fix the adapter stored
    ``self.config`` and never read it, which is why the configured timeout was
    inert for every akshare endpoint.
    """
    listener, url = _silent_peer()
    source = AkShareSource(SourceConfig(timeout_seconds=1), HangingAkClient(url))
    started = time.monotonic()
    try:
        with pytest.raises(ServerError):
            source.fetch(
                DataRequest(
                    "index_history",
                    ("000300.SH",),
                    data_request.start_date,
                    data_request.end_date,
                    {},
                )
            )
    finally:
        listener.close()
        elapsed = time.monotonic() - started

    assert elapsed < 10, "the configured 1s bound, not the peer's silence"


def test_a_transport_that_sets_its_own_timeout_keeps_it(data_request):
    """The shim fills an absent default; it must never overwrite a chosen one.

    The tushare proxy relies on this: it passes a longer read timeout than the
    configured call bound, and that choice has to survive.
    """
    listener, url = _silent_peer()
    source = SilentHttpSource(url, own_timeout=0.25)
    started = time.monotonic()
    try:
        with pytest.raises(ServerError):
            fetch_with_retry(
                source,
                data_request,
                RetryPolicy(max_attempts=1, call_timeout_seconds=5),
                sleeper=lambda _: None,
            )
    finally:
        listener.close()
        elapsed = time.monotonic() - started

    assert elapsed < 2, "the transport's own 0.25s bound, not the 5s default"


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
