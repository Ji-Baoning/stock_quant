"""Offline relay client: the tushare SDK protocol pointed at a third-party relay.

Scope boundary (revised 2026-09-12, design spec §1)
--------------------------------------------------
This client started as an offline-only collector helper precisely because
routing a relay through the pipeline would have bound the relay's identity
into the dataset version's ``supplier_endpoint`` evidence -- presenting
relayed data as though it came from the source.  The role-division design
resolves that concern the other way round: the relay is now the pipeline's
primary tushare transport, and the attribution is kept honest by *recording*
the relay path everywhere the evidence is read (``transport_id`` in the raw
snapshot path and manifest, ``tushare_relay.<host>.<endpoint>`` as the
supplier label) rather than by refusing to use it.  The stage 0
silent-substitution probe gates that promotion.  What remains forbidden is
the opposite error: labelling relay-answered data as ``tushare.pro.*``.

Why the SDK protocol, not a custom HTTP client
----------------------------------------------
``TushareProxyClient`` speaks a bespoke ``/capabilities`` + ``X-API-Key`` +
``/pro/{endpoint}`` surface.  A relay of this kind instead drops into the
official SDK: it accepts ``api_name`` / ``token`` / ``params`` / ``fields`` and
answers tushare-shaped JSON.  The only integration point is the SDK's own base
URL (``DataApi.__http_url``), a private attribute -- the official SDK exposes
no public override, so the mangled name is set deliberately, in one place.
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd

from stock_quant.data_sources.base import host_of


class TushareRelayClient:
    """One tushare-SDK session whose base URL is a third-party relay."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_seconds: int = 30,
        sdk: Any | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("relay base_url must not be blank")
        if not token.strip():
            raise ValueError("relay token must not be blank")
        # The SDK POSTs this string verbatim; normalizing it would break the
        # relay's routing, so only surrounding whitespace is stripped.
        self.base_url = base_url.strip()
        self._token = token.strip()
        if sdk is None:
            import tushare as ts

            sdk = ts
        self.sdk_version = getattr(sdk, "__version__", "unknown")
        api = sdk.pro_api(self._token, timeout=timeout_seconds)
        setattr(api, "_DataApi__http_url", self.base_url)
        self._api = api

    @classmethod
    def from_env(
        cls,
        *,
        timeout_seconds: int = 30,
        sdk: Any | None = None,
    ) -> "TushareRelayClient | None":
        """Build from ``TUSHARE_RELAY_URL`` / ``TUSHARE_RELAY_KEY``.

        Returns ``None`` when either variable is unset or blank, so an offline
        script can fall back to the official SDK path unchanged.
        """
        base_url = os.environ.get("TUSHARE_RELAY_URL", "").strip()
        token = os.environ.get("TUSHARE_RELAY_KEY", "").strip()
        if not base_url or not token:
            return None
        return cls(base_url, token, timeout_seconds=timeout_seconds, sdk=sdk)

    @property
    def host(self) -> str:
        """The bare host of the relay (audit metadata only)."""
        return host_of(self.base_url)

    @property
    def api(self) -> Any:
        """The official SDK session this relay is.

        The relay IS the official ``DataApi`` with its base URL rewritten, so
        it is simultaneously the request client (``.daily`` / ``.index_daily``
        / ``.stock_basic`` all work) and -- by definition -- not the official
        transport.  That is why provenance cannot be an ``isinstance`` test.
        """
        return self._api

    def query(self, endpoint: str, **params: object) -> pd.DataFrame:
        """One relay read; transport and JSON parsing belong to the SDK."""
        return self._api.query(endpoint, **params)
