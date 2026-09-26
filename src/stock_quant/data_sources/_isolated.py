"""Run one supplier SDK call behind a boundary the parent can always kill.

The tgw SDK speaks a broker TCP protocol with a callback thread and exposes no
timeout of its own; ``requests``-oriented timeouts (``default_request_timeout``
in ``base.py``) cannot reach it.  A peer that completes the handshake and then
stops answering would therefore hold the whole update forever, which the
repository's fail-closed rules do not allow.  The only bound that actually
holds is a child process the parent can terminate.

Design notes that matter downstream:

* The parent must be able to import the SDK but must never *call* it -- every
  live call goes through this module, so no connection or session state leaks
  across the fork.
* The child reports a redacted ``(type name, message)`` pair, never its
  environment: credentials are read from ``AD_*`` variables and must not travel
  back into a report, a snapshot or a log line.
* ``fork`` (not ``spawn``) is deliberate: the tests install fake SDK modules at
  runtime and only fork inherits them.  This repository targets Linux.
"""

from __future__ import annotations

import multiprocessing as mp
import warnings
from dataclasses import dataclass
from typing import Any, Callable

from stock_quant.data_sources.base import (
    AuthenticationError,
    ContractError,
    ServerError,
)

#: Exception names the child may report that keep their own type in the parent.
_PERMANENT_CHILD_ERRORS = {
    "AuthenticationError": AuthenticationError,
    "ContractError": ContractError,
}


@dataclass(frozen=True)
class _ChildOutcome:
    """What the child sends back: a payload, or a redacted failure."""

    payload: Any = None
    error_type: str | None = None
    error_message: str = ""


def _child(connection: Any, target: Callable[..., Any], kwargs: dict[str, Any]) -> None:
    try:
        payload = target(**kwargs)
    except BaseException as error:  # noqa: BLE001 - the child only reports
        outcome = _ChildOutcome(
            error_type=type(error).__name__, error_message=str(error)
        )
    else:
        outcome = _ChildOutcome(payload=payload)
    try:
        connection.send(outcome)
    except Exception:  # noqa: BLE001 - the parent is gone; nothing to report to
        pass
    finally:
        connection.close()


def run_isolated(
    target: Callable[..., Any], *, timeout_seconds: float, **kwargs: Any
) -> Any:
    """Run ``target(**kwargs)`` in a child, bounded by ``timeout_seconds``.

    Raises ``ServerError`` when the child exceeds the bound or dies without
    reporting -- both are transient from this side, and ``fetch_with_retry``
    owns what happens next.  ``AuthenticationError`` and ``ContractError``
    survive as themselves: retrying bad credentials or a malformed frame is
    waste, not diligence.
    """
    context = mp.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_child, args=(sender, target, kwargs), daemon=True
    )
    with warnings.catch_warnings():
        # A threaded parent forking a child is this module's deliberate
        # design (see the module docstring); CPython 3.12 deprecates it.
        warnings.filterwarnings(
            "ignore",
            message="This process.*is multi-threaded",
            category=DeprecationWarning,
        )
        process.start()
    sender.close()
    try:
        if not receiver.poll(timeout_seconds):
            raise ServerError(
                f"supplier call exceeded the {timeout_seconds}s process timeout"
            )
        try:
            outcome: _ChildOutcome = receiver.recv()
        except EOFError:
            # The pipe closed with nothing sent: the child died without
            # reporting (killed, OOM, native crash) or could not serialise its
            # outcome.  Either way it is transient from this side.
            raise ServerError("supplier worker died without reporting") from None
    finally:
        _reap(process)
        receiver.close()
    if outcome.error_type is None:
        return outcome.payload
    known = _PERMANENT_CHILD_ERRORS.get(outcome.error_type)
    if known is not None:
        raise known(outcome.error_message)
    raise ServerError(
        f"supplier call failed in its worker ({outcome.error_type}): "
        f"{outcome.error_message}"
    )


def _reap(process: Any) -> None:
    """Terminate, then kill: a broker callback thread can ignore SIGTERM."""
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)
