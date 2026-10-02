"""Bind-host validation and the SQL identifier rule for the query service.

The service binds loopback only (spec §8.1, ADR-021): a non-loopback host
must fail startup. Identifiers reaching DuckDB are quoted with the
repository's existing double-quote doubling rule; values only ever travel
as bound parameters.
"""

from __future__ import annotations

import ipaddress
import socket

# The repository's one quoting rule for SQL identifiers (dataset.py:373).
# Imported rather than reimplemented so the rule cannot drift.
from stock_quant.data_model.dataset import _sql_identifier as quote_identifier

__all__ = ["NonLoopbackBindRejected", "quote_identifier", "validate_bind_host"]


class NonLoopbackBindRejected(RuntimeError):
    """The configured bind host is not loopback; the service refuses to start."""


def validate_bind_host(host: str) -> str:
    """Return ``host`` unchanged when it is a loopback address or name.

    Resolution failures (an unresolvable name, an offline machine) reject
    the host: an unknown host is never assumed loopback.
    """
    candidates = {host.strip().lower()}
    try:
        for info in socket.getaddrinfo(host.strip(), None):
            candidates.add(str(ipaddress.ip_address(info[4][0])))
    except (socket.gaierror, ValueError):
        pass
    loopback = {"127.0.0.1", "::1", "localhost"}
    if not candidates & loopback:
        raise NonLoopbackBindRejected(
            f"refusing to bind non-loopback host {host!r}; the read-only query "
            "service is local-only by design (spec §8.1, ADR-021)"
        )
    return host
