"""Strict contracts for the data-acceptance results formal Research consumes.

An :class:`AcceptanceResult` is one named, deterministic verdict about the
pinned dataset evidence. Formal research requires the results in
``REQUIRED_ACCEPTANCE_RESULTS`` -- currently ``index_membership_evidence`` --
to be present and ``PASS`` before any factor work: a missing or failing
mandatory result stops the run through :func:`enforce_required_results`.

Determinism is enforced structurally: ``details`` may only carry the four
fixed keys (``coverage``, ``counts``, ``hashes``, ``error_codes``), every
value is JSON-safe, and ``error_codes`` must be sorted and unique. Verdicts
therefore never embed verbose rows, exception text or timestamps, and
identical evidence always renders identical bytes.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

#: The acceptance-payload schema version pinned by ``Literal[1]`` fields.
ACCEPTANCE_SCHEMA_VERSION = 1

#: Acceptance results a formal Research run requires present and ``PASS``.
REQUIRED_ACCEPTANCE_RESULTS = ("index_membership_evidence",)

#: The only detail keys a deterministic acceptance result may carry: the
#: covered window, the member/fact counts, the pinned hashes and the sorted
#: fatal error codes. Anything else (rows, symbols lists, messages) is not a
#: deterministic detail and must stay out.
ALLOWED_DETAIL_KEYS = (
    "coverage",
    "counts",
    "hashes",
    "error_codes",
)


class AcceptanceStatus(str, Enum):
    """Outcome of one acceptance result."""

    PASS = "PASS"
    FAIL = "FAIL"


class AcceptanceResult(BaseModel):
    """One named, deterministic acceptance verdict over pinned evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = ACCEPTANCE_SCHEMA_VERSION
    code: str = Field(min_length=1)
    status: AcceptanceStatus
    summary: str = Field(min_length=1)
    details: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("code")
    @classmethod
    def _known_code(cls, value: str) -> str:
        if value not in REQUIRED_ACCEPTANCE_RESULTS:
            raise ValueError(
                f"acceptance result code {value!r} is not in the required "
                f"vocabulary {REQUIRED_ACCEPTANCE_RESULTS}"
            )
        return value

    @model_validator(mode="after")
    def _deterministic_details(self) -> "AcceptanceResult":
        unknown = sorted(set(self.details) - set(ALLOWED_DETAIL_KEYS))
        if unknown:
            raise ValueError(
                "acceptance details carry non-deterministic keys "
                f"{unknown}; allowed: {list(ALLOWED_DETAIL_KEYS)}"
            )
        error_codes = self.details.get("error_codes")
        if error_codes is not None:
            if not isinstance(error_codes, list) or not all(
                isinstance(code, str) for code in error_codes
            ):
                raise ValueError("error_codes must be a list of strings")
            if error_codes != sorted(set(error_codes)):
                raise ValueError(
                    "error_codes must be sorted and unique: "
                    f"{error_codes}"
                )
        return self


class AcceptanceGateError(ValueError):
    """A mandatory acceptance result is missing or did not pass."""


def enforce_required_results(
    results: Sequence[AcceptanceResult],
) -> None:
    """Raise :class:`AcceptanceGateError` unless every mandatory result passes.

    The error message names only stable codes -- never evidence payloads --
    so a blocked run is diagnosable without leaking verbose data.
    """
    by_code = {result.code: result for result in results}
    missing = [
        code for code in REQUIRED_ACCEPTANCE_RESULTS if code not in by_code
    ]
    failed = sorted(
        result.code
        for result in results
        if result.status is AcceptanceStatus.FAIL
    )
    if not missing and not failed:
        return
    problems: list[str] = []
    if missing:
        problems.append(f"missing required results {missing}")
    if failed:
        problems.append(f"failing results {failed}")
    raise AcceptanceGateError(
        "universe acceptance gate failed: " + "; ".join(problems)
    )
