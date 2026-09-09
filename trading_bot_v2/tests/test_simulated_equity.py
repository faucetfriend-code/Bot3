"""
Regression tests for SimulatedExchange equity accounting.

Background
----------
``get_account_balance()`` reported ``cash + unrealised_pnl``. But
``_open_or_add_position`` debits a position's whole cost basis
(``quantity * entry_price``) from cash when it opens and credits it back
only when it closes, so an open position looked like an instant loss of
its entire notional. With zero costs and a flat price, opening 2 units at
100 took reported equity from 10,000 to 9,800.

The consequences were confined to *reported* metrics but were wide:
``final_equity``, ``total_return_pct`` and ``cagr_pct`` were understated
whenever a run ended holding a position, and the equity curve was
depressed for the whole time any position was open, inflating max
drawdown and depressing Sharpe, Sortino, Calmar and PSR.

Position sizing was never affected: the engine sizes off
``exchange.balance`` (raw cash) directly, not through this method, so
trade counts, win rate and profit factor are identical before and after
the fix. ``test_sizing_reads_raw_cash_not_equity`` pins that.

Found 2026-07-29 while measuring VWAP execution costs; see
docs/VWAP-LEVERS.md and docs/BACKTESTING_GUIDE.md.
"""

import pytest

from trading_bot_v2.backtesting.cost_model import CostTable
from trading_bot_v2.backtesting.simulated_exchange import SimulatedExchange

FLAT_BAR = {"open": 100, "high": 100, "low": 100, "close": 100, "volume": 1000}


def _frictionless_exchange(capital=10000.0):
    """An exchange with no fees and no slippage, so equity arithmetic is exact."""
    costs = CostTable(
        profile="legacy",
        base_maker_fee_pct=0.0,
        base_taker_fee_pct=0.0,
        base_slippage_pct=0.0,
        read_env_overrides=False,
    )
    return SimulatedExchange(
        initial_capital=capital, cost_table=costs, funding_hourly_pct=0.0
    )


def _open(exchange, side="bid", qty="2", bar=FLAT_BAR, ts="2024-01-01T00:00:00"):
    exchange.advance(bar, ts)
    exchange.place_order(
        symbol="BTC-USDC", side=side, quantity=qty, order_type="market"
    )


class TestFlatPriceEquityIsPreserved:
    """Opening a position at a flat price must not move equity at all."""

    def test_long_open_preserves_equity(self):
        """THE regression: this reported 9800.0 before the fix."""
        ex = _frictionless_exchange()
        _open(ex, side="bid")
        ex.advance(FLAT_BAR, "2024-01-01T00:05:00")

        assert float(ex.get_account_balance()["balance"]) == pytest.approx(10000.0)

    def test_short_open_preserves_equity(self):
        """Shorts debit cash identically, so they must credit back identically.

        `_open_or_add_position` is side-independent - it does
        `self.balance -= qty * price` for a short exactly as for a long -
        so the correction cannot be applied with a long-only sign.
        """
        ex = _frictionless_exchange()
        _open(ex, side="ask")
        ex.advance(FLAT_BAR, "2024-01-01T00:05:00")

        assert float(ex.get_account_balance()["balance"]) == pytest.approx(10000.0)

    def test_equity_is_unchanged_by_opening_at_all(self):
        ex = _frictionless_exchange()
        ex.advance(FLAT_BAR, "2024-01-01T00:00:00")
        before = ex.equity()
        ex.place_order(symbol="BTC-USDC", side="bid", quantity="2", order_type="market")
        assert ex.equity() == pytest.approx(before)

    def test_get_balance_equity_key_agrees(self):
        """Both equity surfaces had the same bug; both must be fixed."""
        ex = _frictionless_exchange()
        _open(ex)
        ex.advance(FLAT_BAR, "2024-01-01T00:05:00")

        assert ex.get_balance()["equity"] == pytest.approx(
            float(ex.get_account_balance()["balance"])
        )


class TestEquityTracksPriceMoves:
    """Equity must respond to marks, not merely be restored to par."""

    @pytest.mark.parametrize(
        "side,close,expected",
        [
            ("bid", 110, 10020.0),  # long 2 units, +10 each
            ("bid", 90, 9980.0),
            ("ask", 90, 10020.0),  # short 2 units, +10 each
            ("ask", 110, 9980.0),
        ],
    )
    def test_unrealised_moves_equity_by_the_right_sign(self, side, close, expected):
        ex = _frictionless_exchange()
        _open(ex, side=side)
        moved = dict(FLAT_BAR, open=close, high=close, low=close, close=close)
        ex.advance(moved, "2024-01-01T00:05:00")

        assert ex.equity() == pytest.approx(expected)

    def test_realised_and_unrealised_agree_after_a_round_trip(self):
        """Closing must not change equity at the price it is marked at."""
        ex = _frictionless_exchange()
        _open(ex, side="bid")
        moved = dict(FLAT_BAR, open=110, high=110, low=110, close=110)
        ex.advance(moved, "2024-01-01T00:05:00")
        marked = ex.equity()

        ex.place_order(symbol="BTC-USDC", side="ask", quantity="2", order_type="market")
        assert ex.equity() == pytest.approx(marked)
        assert ex.balance == pytest.approx(marked), "flat book: equity is all cash"


class TestAccountingInvariants:
    def test_available_stays_bare_cash(self):
        """`available` is free margin. The engine's balance guard needs it."""
        ex = _frictionless_exchange()
        _open(ex)
        ex.advance(FLAT_BAR, "2024-01-01T00:05:00")

        assert float(ex.get_account_balance()["available"]) == pytest.approx(ex.balance)
        assert ex.balance == pytest.approx(9800.0), "cost basis really did leave cash"

    def test_locked_reports_the_cost_basis(self):
        ex = _frictionless_exchange()
        _open(ex)
        ex.advance(FLAT_BAR, "2024-01-01T00:05:00")

        assert float(ex.get_account_balance()["locked"]) == pytest.approx(200.0)

    def test_available_plus_locked_reconciles_to_equity_when_flat_priced(self):
        ex = _frictionless_exchange()
        _open(ex)
        ex.advance(FLAT_BAR, "2024-01-01T00:05:00")
        b = ex.get_account_balance()

        assert float(b["available"]) + float(b["locked"]) == pytest.approx(
            float(b["balance"])
        )

    def test_no_positions_means_equity_is_cash(self):
        ex = _frictionless_exchange()
        ex.advance(FLAT_BAR, "2024-01-01T00:00:00")

        assert ex.equity() == pytest.approx(10000.0)
        assert ex.open_cost_basis() == 0.0


class TestSizingIsUnaffected:
    def test_sizing_reads_raw_cash_not_equity(self):
        """Pins why this fix cannot change any trade.

        BacktestEngine sizes from `exchange.balance` directly
        (engine.py, `available = exchange.balance`). If that ever became
        `get_account_balance()`, the correction would start changing
        position sizes and every historical comparison would break.
        """
        import inspect

        from trading_bot_v2.backtesting import engine

        source = inspect.getsource(engine.BacktestEngine)
        assert "available = exchange.balance" in source, (
            "engine no longer sizes off raw cash; re-check whether the equity "
            "correction now feeds position sizing"
        )
