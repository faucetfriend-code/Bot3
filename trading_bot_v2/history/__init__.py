"""Trade history and performance metrics module.

This package is the single owner of trade-history access and the
performance-metrics math for the bot:

- :mod:`trading_bot_v2.history.metrics` - pure metric functions
  (profit factor, max drawdown, win rate, expectancy,
  recency-weighted expectancy).
- :mod:`trading_bot_v2.history.trade_store` - ``TradeStore``, the
  exchange-aware facade for recording, querying, and aggregating
  trades.

StrategyMonitor, KellyPositionSizer, AdaptiveWeightManager, and the
API consume trade history through this module instead of issuing their
own SQL against the trades table.
"""

from . import metrics
from .trade_store import DEFAULT_EXCHANGE, TradeStore, get_active_exchange_name

__all__ = [
    "metrics",
    "TradeStore",
    "DEFAULT_EXCHANGE",
    "get_active_exchange_name",
]
