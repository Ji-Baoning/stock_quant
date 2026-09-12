"""Offline relay client: the tushare SDK protocol pointed at a third-party relay.

Scope boundary (deliberate -- do not expand casually)
----------------------------------------------------
This client exists for **offline collectors and cross-checks only**.  It is
not a ``DataSource`` and ``TushareSource``/``DataPipeline`` never constructs
it.  Routing a relay through the pipeline would bind the relay's identity into
the dataset version's ``supplier_endpoint`` evidence -- presenting relayed data
as though it came from the source, which is exactly the attribution error the
evidence chain exists to prevent.  A pipeline relay is a separate, deliberate
change with its own contract tests, not a side effect of adding this module.

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
        without_scheme = self.base_url.split("//", 1)[-1]
        return without_scheme.split("/", 1)[0].split("?", 1)[0]

    def query(self, endpoint: str, **params: object) -> pd.DataFrame:
        """One relay read; transport and JSON parsing belong to the SDK."""
        return self._api.query(endpoint, **params)
