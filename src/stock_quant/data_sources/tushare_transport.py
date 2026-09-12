"""Which transport answers a tushare request, and what that transport is called.

Design spec §1.  Two jobs that used to be conflated in one object are split
here:

* the **request client** -- an object that really has ``daily`` /
  ``index_daily`` / ``stock_basic``.  For the relay and the official direct
  path that is the official ``DataApi`` (its ``__getattr__`` returns
  ``partial(self.query, name)``); for the shared GET proxy it is
  ``TushareProxyClient``.
* the **transport descriptor** -- provenance only: the kind, the SDK version
  and the host actually reached.  ``TushareSource`` no longer guesses its
  supplier from ``isinstance(client, TushareProxyClient)``, because that test
  cannot tell an official session from one whose ``_DataApi__http_url`` was
  rewritten to a relay -- they are the same class.

Selection policy (§1.2): a published build must name its transport explicitly
and may only name ``relay``.  ``proxy`` is development/diagnostic only, and
``official`` additionally requires the two-step break-glass variable.  Nothing
here ever falls back on a credential or initialization failure.

``environ`` is a real parameter throughout, not decoration: these constructors
read credentials from the mapping they are given rather than from the process
environment, so tests and the readiness probe are pure functions of their
input.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import AuthenticationError, host_of
from stock_quant.data_sources.tushare_proxy import TushareProxyClient
from stock_quant.data_sources.tushare_relay import TushareRelayClient

LOGGER = logging.getLogger("stock_quant.data_sources.tushare_transport")

TRANSPORT_ENV = "TUSHARE_TRANSPORT"
OFFICIAL_PUBLISH_ENV = "TUSHARE_ALLOW_OFFICIAL_PUBLISH"
OFFICIAL_HOST = "api.waditu.com"

RELAY = "relay"
OFFICIAL = "official"
PROXY = "proxy"

_KINDS = (RELAY, OFFICIAL, PROXY)
#: The order an explicitly-permissive (development/diagnostic) caller tries.
#: ``proxy`` is deliberately absent: it answers with fallback semantics that
#: no automatic path may pick up behind the operator's back.
_AUTO_ORDER = (RELAY, OFFICIAL)
#: Labels used only when a caller injects a client that has no reachable URL
#: (test doubles).  ``resolve_transport`` never produces these.
_STUB_HOST = {OFFICIAL: OFFICIAL_HOST, PROXY: PROXY, RELAY: RELAY}


class CredentialsMissing(AuthenticationError):
    """A transport is not configured at all (as opposed to failing to start).

    Only this failure is fallback-worthy, and only inside the development
    auto-order: a *half*-configured or failing transport never falls back.
    """


@dataclass(frozen=True)
class TushareTransport:
    """Provenance for one resolved tushare transport."""

    kind: str
    client: Any
    sdk_version: str
    host: str

    @property
    def transport_id(self) -> str:
        """The path-safe identity of the answering party (design §2.3)."""
        return self.host

    def supplier_endpoint(self, endpoint: str) -> str:
        """The audited supplier label for one endpoint on this transport."""
        if self.kind == RELAY:
            return f"tushare_relay.{self.host}.{endpoint}"
        if self.kind == PROXY:
            return f"tushare_proxy.{endpoint}"
        return f"tushare.pro.{endpoint}"


def _setting(environ: Mapping[str, str], key: str) -> str:
    return str(environ.get(key, "")).strip()


def client_host(client: Any) -> str:
    """The host a client will reach, tolerating stubs that omit it.

    The lookup of ``.host`` is wrapped in ``try``/``except AttributeError``:
    a stub with no ``base_url`` raises ``AttributeError`` *inside* that
    property, and the wrapper is what swallows it.  Only when ``.host`` is
    absent or blank does the ``getattr(client, "base_url", "")`` fallback run
    -- there the ``getattr`` default is what keeps a client without a
    ``base_url`` attribute from raising.  Public because
    ``injected_transport`` (in ``tushare.py``) needs the same tolerance.
    """
    try:
        host = client.host
    except AttributeError:
        host = None
    if isinstance(host, str) and host:
        return host
    return host_of(getattr(client, "base_url", ""))


def official_transport(
    environ: Mapping[str, str], *, sdk: Any | None = None
) -> TushareTransport:
    """Build the direct session, refusing to label anything else as official."""
    token = _setting(environ, "TUSHARE_TOKEN")
    if not token:
        raise CredentialsMissing("the official transport requires TUSHARE_TOKEN")
    if sdk is None:
        import tushare as ts

        sdk = ts
    try:
        client = sdk.pro_api(token)
    except Exception:
        # The message may embed the token; never let it travel.
        raise AuthenticationError("Tushare client initialization failed") from None
    host = host_of(getattr(client, "_DataApi__http_url", ""))
    if host != OFFICIAL_HOST:
        raise AuthenticationError(
            "refusing to label this transport as official: its SDK base URL "
            f"host is {host!r}, not {OFFICIAL_HOST!r}"
        )
    return TushareTransport(
        kind=OFFICIAL,
        client=client,
        sdk_version=getattr(sdk, "__version__", "unknown"),
        host=host,
    )


def build_transport(
    kind: str,
    config: SourceConfig,
    *,
    sdk: Any | None = None,
    environ: Mapping[str, str] | None = None,
) -> TushareTransport:
    """Construct one transport by name, ignoring all publication policy.

    Pure mechanism, so diagnostic scripts can ask for a specific transport
    (including the proxy) without re-deriving the rules.  Credential and
    initialization failures raise ``AuthenticationError`` and never fall back.

    Credentials are read from ``environ`` and passed to the client
    constructors directly -- ``from_env`` would read ``os.environ`` and make
    this function untestable.
    """
    source = os.environ if environ is None else environ
    if kind == RELAY:
        base_url = _setting(source, "TUSHARE_RELAY_URL")
        token = _setting(source, "TUSHARE_RELAY_KEY")
        if not base_url or not token:
            raise CredentialsMissing(
                "the relay transport requires TUSHARE_RELAY_URL and TUSHARE_RELAY_KEY"
            )
        try:
            relay = TushareRelayClient(
                base_url, token, timeout_seconds=config.timeout_seconds, sdk=sdk
            )
        except Exception:
            # ``pro_api`` performs the handshake, so this is where a relay
            # outage surfaces.  It must arrive as AuthenticationError -- never
            # as a bare RuntimeError, and never with the SDK's own message,
            # which may embed the token.
            raise AuthenticationError(
                f"tushare relay initialization failed for host {host_of(base_url)!r}"
            ) from None
        transport = TushareTransport(
            kind=RELAY,
            client=relay.api,
            sdk_version=relay.sdk_version,
            host=client_host(relay),
        )
    elif kind == PROXY:
        base_url = _setting(source, "TUSHARE_PROXY_URL")
        api_key = _setting(source, "TUSHARE_PROXY_KEY")
        if not base_url or not api_key:
            raise CredentialsMissing(
                "the proxy transport requires TUSHARE_PROXY_URL and TUSHARE_PROXY_KEY"
            )
        # Not wrapped: this constructor only builds a requests.Session and sets
        # headers -- it performs no network I/O, so it has nothing to fail with.
        proxy = TushareProxyClient(
            base_url,
            api_key,
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
        )
        transport = TushareTransport(
            kind=PROXY,
            client=proxy,
            sdk_version=proxy.sdk_version,
            host=client_host(proxy),
        )
    elif kind == OFFICIAL:
        transport = official_transport(source, sdk=sdk)
    else:
        raise AuthenticationError(
            f"unknown {TRANSPORT_ENV}={kind!r} (expected one of: {', '.join(_KINDS)})"
        )
    LOGGER.info(
        "tushare transport: kind=%s host=%s sdk_version=%s",
        transport.kind,
        transport.host,
        transport.sdk_version,
    )
    return transport


def resolve_transport(
    config: SourceConfig,
    *,
    allow_auto_transport: bool = False,
    sdk: Any | None = None,
    environ: Mapping[str, str] | None = None,
) -> TushareTransport:
    """Resolve the transport a caller is allowed to use (design §1.2).

    ``allow_auto_transport`` is the development/diagnostic permission: it
    unlocks the ``relay -> official`` auto-order and the explicitly requested
    proxy.  A published build leaves it ``False`` and must name ``relay``.
    """
    source = os.environ if environ is None else environ
    requested = _setting(source, TRANSPORT_ENV).lower()

    if not requested:
        if not allow_auto_transport:
            raise AuthenticationError(
                f"{TRANSPORT_ENV} must be set explicitly for a published build "
                f"(expected {RELAY!r}); this path never falls back"
            )
        for kind in _AUTO_ORDER:
            try:
                return build_transport(kind, config, sdk=sdk, environ=source)
            except CredentialsMissing:
                continue
        raise AuthenticationError(
            "no tushare transport is configured: set TUSHARE_RELAY_URL and "
            "TUSHARE_RELAY_KEY (preferred), or TUSHARE_TOKEN"
        )

    if requested not in _KINDS:
        raise AuthenticationError(
            f"unknown {TRANSPORT_ENV}={requested!r} "
            f"(expected one of: {', '.join(_KINDS)})"
        )
    if requested == PROXY and not allow_auto_transport:
        raise AuthenticationError(
            f"{TRANSPORT_ENV}={PROXY} is development/diagnostic only and is "
            "never allowed for a published build"
        )
    if (
        requested == OFFICIAL
        and not allow_auto_transport
        and _setting(source, OFFICIAL_PUBLISH_ENV) != "1"
    ):
        raise AuthenticationError(
            f"{TRANSPORT_ENV}={OFFICIAL} requires "
            f"{OFFICIAL_PUBLISH_ENV}=1 for a published build (break-glass)"
        )
    return build_transport(requested, config, sdk=sdk, environ=source)
