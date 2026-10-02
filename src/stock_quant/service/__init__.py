"""The local, read-only FastAPI query surface over published artefacts.

The service never imports the data pipeline and holds no publisher: it
reads immutable dataset versions, the acceptance registry's on-disk store
and generated experiment reports (spec §8.1, ADR-021). All routes are GET.
"""

from stock_quant.service.app import create_app

__all__ = ["create_app"]
