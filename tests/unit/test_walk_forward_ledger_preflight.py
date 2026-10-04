"""Walk-forward fold ledger preflight (`_ledger_preflight`).

Every submitted order is accounted exactly once: ``filled + rejected ==
submitted``.  A cash-partial order legitimately carries BOTH a fill and a
rejection for its unaffordable remainder -- the engine's ``_execute_buy``
emits exactly that shape (fill for the affordable whole-lot quantity, one
rejection with ``rejected_quantity`` for the rest), and the golden
single-window ledgers pin it.  An order with neither, a sum mismatch, or a
duplicate rejection record is an ``OOSIntegrityError`` -- never silently
absorbed.
"""

from __future__ import annotations

import pandas as pd
import pytest

from stock_quant.research.walk_forward.runner import _ledger_preflight


def _frames(
    submitted: list[tuple[str, int]],
    fills: list[tuple[str, int]],
    rejections: list[tuple[str, int]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    submitted_frame = pd.DataFrame(
        [{"order_id": order_id, "quantity": quantity} for order_id, quantity in submitted]
    )
    fills_frame = pd.DataFrame(
        [{"order_id": order_id, "quantity": quantity} for order_id, quantity in fills]
    )
    rejections_frame = pd.DataFrame(
        [
            {"order_id": order_id, "rejected_quantity": quantity}
            for order_id, quantity in rejections
        ]
    )
    return submitted_frame, fills_frame, rejections_frame


def test_partial_fill_with_rejected_remainder_is_exact_accounting():
    """The engine's cash-partial shape: one fill plus the remainder rejection.

    This is the exact ledger shape the first formal walk-forward run hit
    (run_369ee9a3… folds 2021/2022/2024): the preflight must accept it.
    """
    submitted, fills, rejections = _frames(
        submitted=[("r000116", 500)],
        fills=[("r000116", 300)],
        rejections=[("r000116", 200)],
    )
    _ledger_preflight(submitted, fills, rejections)


def test_multiple_fills_plus_rejected_remainder_sum_to_the_submission():
    submitted, fills, rejections = _frames(
        submitted=[("r1", 500)],
        fills=[("r1", 200), ("r1", 100)],
        rejections=[("r1", 200)],
    )
    _ledger_preflight(submitted, fills, rejections)


def test_fully_filled_or_fully_rejected_still_pass():
    submitted, fills, rejections = _frames(
        submitted=[("r1", 100), ("r2", 100)],
        fills=[("r1", 100)],
        rejections=[("r2", 100)],
    )
    _ledger_preflight(submitted, fills, rejections)


def test_order_with_neither_fill_nor_rejection_raises():
    submitted, fills, rejections = _frames(
        submitted=[("r1", 100)],
        fills=[],
        rejections=[],
    )
    with pytest.raises(Exception, match="neither a fill nor a rejection"):
        _ledger_preflight(submitted, fills, rejections)


def test_filled_plus_rejected_mismatch_raises():
    submitted, fills, rejections = _frames(
        submitted=[("r1", 500)],
        fills=[("r1", 300)],
        rejections=[("r1", 100)],
    )
    with pytest.raises(Exception, match="filled 300 \\+ rejected 100"):
        _ledger_preflight(submitted, fills, rejections)


def test_duplicate_rejection_record_raises():
    submitted, fills, rejections = _frames(
        submitted=[("r1", 500)],
        fills=[],
        rejections=[("r1", 200), ("r1", 300)],
    )
    with pytest.raises(Exception, match="multiple rejection records"):
        _ledger_preflight(submitted, fills, rejections)
