"""Logic audit A3: order handling and risk controls.

Each test here reproduces one defect found by reading the order-handling
and risk-control paths end to end.  The test was written first and fails
against the code as found; the matching fix is the smallest change that
makes it pass.  No network, no live database (conftest isolates
DATABASE_PATH); the exchange is a SimpleNamespace / MagicMock stand-in.

Covered:
* the portfolio circuit breaker is evaluated by the loop's risk monitor;
* the breaker compares a percent loss against a percent threshold;
* an exposure read failure rejects the signal instead of counting as 0;
* raw Pacifica positions (key ``amount``) are not treated as ghosts by
  position reconciliation;
* RiskManager sizing has no 1.0-contract floor that discards regime and
  risk-profile scaling on high-priced assets.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from trading_bot_v2.config import AssetClass, MarketState, StrategyType, TradeQuality
from trading_bot_v2.market_regime import MarketRegime
from trading_bot_v2.models import OrderSide, Signal
from trading_bot_v2.position_reconciler import _filter_real_positions
from trading_bot_v2.risk_manager import RiskManager
from trading_bot_v2.trading_bot import TradingBot


def _signal(entry_price: float = 100.0, stop_loss: float = 95.0) -> Signal:
    """A fully flagged BUY signal."""
    return Signal(
        strategy=StrategyType.MEAN_REVERSION,
        asset="BTC",
        asset_class=AssetClass.PERPETUAL,
        side=OrderSide.BUY,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=110.0,
        confidence=0.8,
        quality=TradeQuality.STANDARD,
        market_state=MarketState.RANGE,
        volume_confirmation=True,
        multi_timeframe_alignment=True,
        support_resistance_valid=True,
        rrr_meets_minimum=True,
        liquidation_buffer_safe=True,
        account_risk_ok=True,
        margin_drawdown_ok=True,
        forbidden_conditions_clear=True,
    )


def _bind(bot: SimpleNamespace, *names: str) -> SimpleNamespace:
    """Bind TradingBot methods onto a duck-typed stand-in."""
    for name in names:
        setattr(bot, name, getattr(TradingBot, name).__get__(bot))
    return bot


def _breaker_bot(balance: float, unrealized_pnl: float) -> SimpleNamespace:
    """Stand-in with one open position carrying ``unrealized_pnl``."""
    client = MagicMock()
    client.get_positions.return_value = [
        {
            "symbol": "BTC",
            "side": "long",
            "quantity": 1.0,
            "unrealized_pnl": unrealized_pnl,
        }
    ]
    bot = SimpleNamespace(
        client=client,
        hub_publish_func=None,
        event_bus=MagicMock(),
        _circuit_breaker_triggered=False,
        _circuit_breaker_loss_pct=0.10,
        stop=MagicMock(),
        _get_account_balance=lambda: balance,
        _get_current_exposure=lambda: 0.0,
    )
    return _bind(bot, "_monitor_risk", "_monitor_risk_coordinated")


class TestCircuitBreaker:
    """CIRCUIT_BREAKER_LOSS_PCT=0.10 means a 10 percent portfolio loss."""

    def test_loop_risk_monitor_trips_breaker_on_threshold_loss(self):
        bot = _breaker_bot(balance=10_000.0, unrealized_pnl=-1_200.0)
        bot._monitor_risk_coordinated()
        assert bot._circuit_breaker_triggered is True
        bot.stop.assert_called_once()

    def test_breaker_trips_at_threshold(self):
        bot = _breaker_bot(balance=10_000.0, unrealized_pnl=-1_000.0)
        bot._monitor_risk()
        assert bot._circuit_breaker_triggered is True

    def test_breaker_does_not_trip_below_threshold(self):
        bot = _breaker_bot(balance=10_000.0, unrealized_pnl=-500.0)
        bot._monitor_risk()
        assert bot._circuit_breaker_triggered is False
        bot.stop.assert_not_called()


class RejectLogger:
    """Minimal signal logger recording rejection reasons."""

    def __init__(self):
        self.rejected = []

    def log_signal_rejected(self, signal, reason, **kwargs):
        self.rejected.append(reason)


class TestExposureReadFailure:
    """A failed positions read must block entries, not count as zero exposure."""

    def _bot(self) -> SimpleNamespace:
        exchange = MagicMock()
        exchange.get_balance.return_value = SimpleNamespace(equity=10_000.0, raw={})
        exchange.get_positions.side_effect = RuntimeError("positions unavailable")
        bot = SimpleNamespace(
            exchange=exchange,
            client=MagicMock(),
            signal_logger=RejectLogger(),
            _circuit_breaker_triggered=False,
            _circuit_breaker_loss_pct=0.10,
        )
        return _bind(
            bot,
            "_should_execute_signal",
            "_get_account_balance",
            "_get_current_exposure",
        )

    def test_signal_rejected_when_exposure_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "trading_bot_v2.supervisor_control.get_supervisor_control",
            lambda: SimpleNamespace(is_paused=lambda: False),
        )
        bot = self._bot()
        assert bot._should_execute_signal(_signal()) is False
        assert bot.signal_logger.rejected
        assert "xposure" in bot.signal_logger.rejected[-1]


class TestPacificaPositionQuantity:
    """Pacifica reports position size under ``amount``, never ``quantity``."""

    def test_filter_real_positions_reads_amount(self):
        rows = [{"symbol": "BTC", "side": "bid", "amount": "1.5", "entry_price": "100"}]
        result = _filter_real_positions(rows)
        assert result == {
            "BTC:LONG": {
                "symbol": "BTC",
                "side": "LONG",
                "quantity": 1.5,
                "entry_price": 100.0,
            }
        }

    def test_stale_close_keeps_position_present_under_amount(self):
        db = MagicMock()
        db.get_positions.return_value = [
            {"symbol": "BTC", "side": "LONG", "quantity": 1.5, "entry_price": 100.0}
        ]
        bot = _bind(SimpleNamespace(db=db), "_close_stale_local_positions")
        bot._close_stale_local_positions(
            [{"symbol": "BTC", "side": "bid", "amount": "1.5", "entry_price": "100"}]
        )
        db.close_position.assert_not_called()


class TestSizingFloor:
    """No 1.0-contract floor: it discards scaling on high-priced assets."""

    def test_regime_multiplier_survives_for_high_priced_asset(self):
        rm = RiskManager(max_portfolio_risk_pct=0.05, max_portfolio_exposure_pct=0.15)
        sig = _signal(entry_price=60_000.0, stop_loss=58_800.0)
        base = rm.get_position_size(sig, account_balance=10_000.0, current_exposure=0.0)
        scaled = rm.get_position_size(
            sig,
            account_balance=10_000.0,
            current_exposure=0.0,
            regime=MarketRegime.INDECISIVE,
        )
        assert base == pytest.approx(1_500.0 / 60_000.0)
        assert scaled == pytest.approx(base * 0.4)

    def test_exhausted_exposure_requests_nothing(self):
        rm = RiskManager(max_portfolio_risk_pct=0.05, max_portfolio_exposure_pct=0.15)
        sig = _signal(entry_price=60_000.0, stop_loss=58_800.0)
        qty = rm.get_position_size(
            sig, account_balance=10_000.0, current_exposure=2_000.0
        )
        assert qty == 0.0

    def test_invalid_entry_price_requests_nothing(self):
        rm = RiskManager()
        sig = _signal(entry_price=0.0, stop_loss=0.0)
        assert (
            rm.get_position_size(sig, account_balance=10_000.0, current_exposure=0.0)
            == 0.0
        )
