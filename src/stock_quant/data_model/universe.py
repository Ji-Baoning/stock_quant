"""Versioned fixed engineering universe (Task 5).

The universe is 30 curated boundary samples covering the four A-share boards
in fixed quotas; it is configuration, not a recommendation and never rotates
automatically (design spec §7). ``Universe.version`` is the SHA-256 of a
canonical serialization of the parsed entries, so any content change yields a
new version and identical content hashes identically regardless of YAML key or
entry ordering.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stock_quant.safe_yaml import read_yaml

Exchange = Literal["SH", "SZ", "BJ"]
Board = Literal["sh_main", "sz_main", "chinext", "star", "bj"]

_KNOWN_BOARDS = ("sh_main", "sz_main", "chinext", "star", "bj")
_CANONICAL_SYMBOL = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


class UniverseEntry(BaseModel):
    """One fixed engineering sample as selected at ``selected_as_of``."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    name_at_selection: str = Field(min_length=1)
    exchange: Exchange
    board: Board
    selected_as_of: date
    boundary_tags: list[str] = Field(min_length=1)
    selection_reason: str = Field(min_length=1)

    @field_validator("symbol")
    @classmethod
    def _canonical_symbol(cls, value: str) -> str:
        if not _CANONICAL_SYMBOL.fullmatch(value):
            message = f"symbol must be canonical six-digit + exchange: {value!r}"
            raise ValueError(message)
        return value


class Universe:
    """The immutable fixed engineering universe and its content version."""

    def __init__(self, entries: Iterable[UniverseEntry]) -> None:
        self._entries = tuple(sorted(entries, key=lambda entry: entry.symbol))
        symbols = [entry.symbol for entry in self._entries]
        if len(symbols) != len(set(symbols)):
            raise ValueError("universe entries must be unique by symbol")
        if self._entries:
            if not all(entry.board in _KNOWN_BOARDS for entry in self._entries):
                raise ValueError("universe contains an unknown board")
        self.version = _content_hash(self._entries)

    @classmethod
    def from_entries(cls, entries: Iterable[UniverseEntry]) -> "Universe":
        return cls(entries)

    @classmethod
    def from_yaml(cls, path: Path) -> "Universe":
        """Load a universe from a ``configs/universe.yml``-shaped file."""
        path = Path(path)
        document = read_yaml(path)
        if not isinstance(document, dict) or "entries" not in document:
            raise ValueError(f"universe yaml {path} must contain an 'entries' list")
        default_as_of = document.get("selected_as_of")
        entries: list[UniverseEntry] = []
        for raw in document["entries"]:
            if not isinstance(raw, dict):
                raise ValueError(f"universe entry must be a mapping in {path}")
            if default_as_of is not None and raw.get("selected_as_of") is None:
                raw = {**raw, "selected_as_of": default_as_of}
            entries.append(UniverseEntry.model_validate(raw))
        return cls(entries)

    @property
    def entries(self) -> tuple[UniverseEntry, ...]:
        return self._entries

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(entry.symbol for entry in self._entries)

    def entry(self, symbol: str) -> UniverseEntry | None:
        """Return the entry for ``symbol``, or ``None`` when not in the universe."""
        for entry in self._entries:
            if entry.symbol == symbol:
                return entry
        return None

    def contains(self, symbol: str) -> bool:
        return any(entry.symbol == symbol for entry in self._entries)

    def counts_by_board(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self._entries:
            counts[entry.board] = counts.get(entry.board, 0) + 1
        return counts


def _content_hash(entries: Iterable[UniverseEntry]) -> str:
    """SHA-256 of a canonical serialization of the parsed universe content."""
    ordered = sorted(entries, key=lambda entry: entry.symbol)
    payload: list[dict[str, Any]] = []
    for entry in ordered:
        payload.append(entry.model_dump(mode="json"))
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
