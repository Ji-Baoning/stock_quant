"""Canonical A-share security-code normalization.

A canonical symbol is exactly six digits plus a ``.SH``/``.SZ`` (or ``.BJ``)
exchange suffix. Cleaning only rewrites a supplier's spelling into this form;
it never guesses an exchange that the value does not already encode.
"""

from __future__ import annotations

import re

_SUFFIXED = re.compile(r"^\s*(\d{6})\.(sh|sz|bj)\s*$", re.IGNORECASE)
_PREFIXED = re.compile(r"^\s*(sh|sz|bj)\.(\d{6})\s*$", re.IGNORECASE)


class SymbolNormalizationError(ValueError):
    """A supplier-native code cannot be mapped to canonical form."""


def normalize_symbol(value: object, source: str) -> str:
    """Return the canonical ``600000.SH`` form of a supplier-native code.

    BaoStock reports prefixed codes such as ``sh.600000`` while Tushare reports
    already-suffixed codes such as ``000001.SZ``. The embedded exchange lets
    either spelling be canonicalized without guessing, so ``source`` is kept
    only for the error context and future source-specific validation.
    """
    text = str(value).strip()
    match = _SUFFIXED.match(text)
    if match:
        return f"{match.group(1)}.{match.group(2).upper()}"
    match = _PREFIXED.match(text)
    if match:
        return f"{match.group(2)}.{match.group(1).upper()}"
    raise SymbolNormalizationError(
        f"cannot normalize symbol {value!r} for source {source!r}"
    )
