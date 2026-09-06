"""A-share order, execution, cost and T+1 account-ledger simulation.

Task 9 of the phase-one quant system provides the immutable order/fill/position
records and the cash-ledger account that the weekly backtest engine (Task 10)
drives: sells first, buys reduced to affordable whole lots, T+1 sellability
handed by per-lot ``available_date``, all money arithmetic in ``Decimal``.
"""

__all__ = [
    "account",
    "costs",
    "execution",
    "models",
]
