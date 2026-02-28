"""
Cost Model
==========

Applies realistic transaction cost estimates to signals BEFORE
they are routed to the simulated exchange.

Why here (not in the exchange): Strategies should see cost-adjusted
expected values when calculating RRR, so low-RRR signals are
filtered out under realistic cost assumptions.
"""

from loguru import logger
from ..models import Signal


class CostModel:
    """
    Adjusts signal stop/target for realistic round-trip costs.

    Costs applied:
      - Taker fee x 2 (entry + exit, worst case)
      - Slippage on entry
    """

    def __init__(self, slippage_pct: float = 0.002, taker_fee_pct: float = 0.0006):
        self.slippage_pct = slippage_pct
        self.taker_fee_pct = taker_fee_pct
        self.round_trip_cost_pct = (taker_fee_pct * 2) + slippage_pct

    def apply(self, signal: Signal, current_price: float) -> None:
        """
        Adjusts signal expected_return to account for costs.
        Signals below break-even after costs are flagged (not blocked --
        RiskManager handles final go/no-go).
        """
        if signal.take_profit and signal.stop_loss:
            gross_rr = abs(signal.take_profit - current_price) / abs(current_price - signal.stop_loss)
            cost_drag = self.round_trip_cost_pct * current_price
            net_target = signal.take_profit - cost_drag if signal.take_profit > current_price else signal.take_profit + cost_drag
            signal.take_profit = net_target
            if gross_rr < 1.0:
                logger.debug(f"Low RRR signal from {signal.strategy}: gross_rr={gross_rr:.2f}")
