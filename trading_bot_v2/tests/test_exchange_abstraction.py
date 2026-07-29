"""Tests for the exchange abstraction layer (trading_bot_v2.exchanges).

Covers: factory selection/registry errors, side and amount vocabulary
round-trips, pacifica-native wire format produced from normalized args,
position/balance normalization (ghost filtering), funding capabilities,
FundingArb interval awareness, and TradingBot factory wiring.
"""

from unittest.mock import MagicMock, patch

import base58
import pytest
from solders.keypair import Keypair

from trading_bot_v2.exchanges import (
    BlofinExchange,
    EXCHANGE_REGISTRY,
    PacificaExchange,
    get_exchange_capabilities,
    get_exchange_client,
    reset_exchange_singletons,
)
from trading_bot_v2.exchanges.base import (
    ExchangeBalance,
    ExchangePosition,
    PositionSide,
)
from trading_bot_v2.models import OrderSide, OrderType


@pytest.fixture(autouse=True)
def _clean_factory_state(monkeypatch):
    """Isolate factory env/singleton state per test."""
    monkeypatch.delenv("EXCHANGE", raising=False)
    reset_exchange_singletons()
    yield
    reset_exchange_singletons()


def _make_pacifica_with_mock_rest():
    """PacificaExchange wrapping a MagicMock native client."""
    rest = MagicMock()
    return PacificaExchange(rest_client=rest, ws_client=MagicMock()), rest


def _make_real_pacifica_client():
    """Real PacificaClient with a throwaway keypair (no network use)."""
    from trading_bot_v2.pacifica_client import PacificaClient

    keypair = Keypair()
    private_b58 = base58.b58encode(bytes(keypair)).decode("ascii")
    return PacificaClient(
        agent_wallet_private_key=private_b58,
        account_public_key=str(keypair.pubkey()),
        testnet=True,
    )


# ======================================================================
# Factory
# ======================================================================


class TestFactory:
    def test_default_returns_pacifica(self):
        exchange = get_exchange_client()
        assert isinstance(exchange, PacificaExchange)

    def test_default_is_singleton(self):
        assert get_exchange_client() is get_exchange_client()

    def test_env_selects_blofin(self, monkeypatch):
        monkeypatch.setenv("EXCHANGE", "blofin")
        exchange = get_exchange_client()
        assert isinstance(exchange, BlofinExchange)

    def test_unknown_exchange_raises_with_choices(self, monkeypatch):
        monkeypatch.setenv("EXCHANGE", "binance")
        with pytest.raises(ValueError) as excinfo:
            get_exchange_client()
        message = str(excinfo.value)
        assert "binance" in message
        for name in EXCHANGE_REGISTRY:
            assert name in message

    def test_injected_client_returns_fresh_instance(self):
        rest_a, rest_b = MagicMock(), MagicMock()
        ex_a = get_exchange_client(rest_client=rest_a)
        ex_b = get_exchange_client(rest_client=rest_b)
        assert ex_a is not ex_b
        assert ex_a.rest_client is rest_a
        assert ex_b.rest_client is rest_b

    def test_name_case_insensitive(self):
        assert isinstance(get_exchange_client("PACIFICA"), PacificaExchange)

    def test_blofin_without_credentials_raises_clear_auth_error(
        self, monkeypatch
    ):
        """Private Blofin calls without keys fail with setup guidance."""
        from trading_bot_v2.blofin_client import BlofinAuthError, BlofinClient

        monkeypatch.setenv("EXCHANGE", "blofin")
        rest = BlofinClient(api_key="", api_secret="", passphrase="", demo=True)
        exchange = get_exchange_client(rest_client=rest)
        assert isinstance(exchange, BlofinExchange)
        for call in (
            lambda: exchange.place_order("BTC", OrderSide.BUY, 1.0),
            exchange.get_positions,
            exchange.get_balance,
            exchange.cancel_all_orders,
        ):
            with pytest.raises(BlofinAuthError) as excinfo:
                call()
            assert "BLOFIN_API_KEY" in str(excinfo.value)
            assert ".env" in str(excinfo.value)


# ======================================================================
# Vocabulary round-trips
# ======================================================================


class TestVocabulary:
    def test_order_side_round_trip(self):
        assert PacificaExchange.to_native_order_side(OrderSide.BUY) == "bid"
        assert PacificaExchange.to_native_order_side(OrderSide.SELL) == "ask"
        assert PacificaExchange.from_native_order_side("bid") == OrderSide.BUY
        assert PacificaExchange.from_native_order_side("ask") == OrderSide.SELL
        # Full round trips
        for side in (OrderSide.BUY, OrderSide.SELL):
            native = PacificaExchange.to_native_order_side(side)
            assert PacificaExchange.from_native_order_side(native) == side

    def test_order_side_tolerates_strings_and_casing(self):
        assert PacificaExchange.to_native_order_side("buy") == "bid"
        assert PacificaExchange.to_native_order_side("BUY") == "bid"
        assert PacificaExchange.to_native_order_side("SELL") == "ask"
        assert PacificaExchange.to_native_order_side("bid") == "bid"
        assert PacificaExchange.to_native_order_side("ask") == "ask"

    def test_unknown_order_side_raises(self):
        with pytest.raises(ValueError):
            PacificaExchange.to_native_order_side("sideways")

    def test_position_side_round_trip(self):
        assert PacificaExchange.to_native_position_side(PositionSide.LONG) == "long"
        assert PacificaExchange.to_native_position_side(PositionSide.SHORT) == "short"
        assert (
            PacificaExchange.from_native_position_side("long") == PositionSide.LONG
        )
        assert (
            PacificaExchange.from_native_position_side("short") == PositionSide.SHORT
        )
        for side in (PositionSide.LONG, PositionSide.SHORT):
            native = PacificaExchange.to_native_position_side(side)
            assert PacificaExchange.from_native_position_side(native) == side

    def test_position_side_tolerates_leaked_order_vocab(self):
        # Mirrors api_server._normalize_position_side behavior
        assert PacificaExchange.from_native_position_side("bid") == PositionSide.LONG
        assert PacificaExchange.from_native_position_side("ask") == PositionSide.SHORT
        assert PacificaExchange.from_native_position_side("LONG") == PositionSide.LONG
        assert PacificaExchange.from_native_position_side(None) == PositionSide.LONG

    def test_amount_numeric_to_string(self):
        assert PacificaExchange.amount_to_native(1.5) == "1.5"
        assert PacificaExchange.amount_to_native(2.0) == "2.0"


# ======================================================================
# Wire format: normalized args -> pacifica-native payload
# ======================================================================


class TestPacificaWireFormat:
    def test_market_buy_produces_bid_and_string_amount(self):
        client = _make_real_pacifica_client()
        exchange = PacificaExchange(rest_client=client)
        with patch.object(
            client, "_make_signed_request", return_value={"success": True, "data": {}}
        ) as signed:
            exchange.place_order("BTC", OrderSide.BUY, 0.5, OrderType.MARKET)
        endpoint, payload, request_type = signed.call_args[0]
        assert endpoint == "/orders/create_market"
        assert request_type == "create_market_order"
        assert payload["side"] == "bid"
        assert payload["amount"] == "0.5"
        assert isinstance(payload["amount"], str)
        assert payload["symbol"] == "BTC"

    def test_limit_sell_produces_ask_with_string_price(self):
        client = _make_real_pacifica_client()
        exchange = PacificaExchange(rest_client=client)
        with patch.object(
            client, "_make_signed_request", return_value={"success": True, "data": {}}
        ) as signed:
            exchange.place_order(
                "SUI", OrderSide.SELL, 10.0, OrderType.LIMIT, price=3.25
            )
        endpoint, payload, request_type = signed.call_args[0]
        assert endpoint == "/orders/create"
        assert request_type == "create_order"
        assert payload["side"] == "ask"
        assert payload["amount"] == "10.0"
        assert payload["price"] == "3.25"
        assert payload["tif"] == "GTC"

    def test_limit_without_price_raises(self):
        exchange, _ = _make_pacifica_with_mock_rest()
        with pytest.raises(ValueError):
            exchange.place_order("BTC", OrderSide.BUY, 1.0, OrderType.LIMIT)

    def test_delegates_exact_buy_sell_strings_to_native_client(self):
        # The native client maps anything != "buy" to "ask", so the
        # adapter must pass exact lowercase strings.
        exchange, rest = _make_pacifica_with_mock_rest()
        exchange.place_order("BTC", "BUY", 1.0, "market")
        rest.place_order.assert_called_once_with(
            symbol="BTC", side="buy", quantity=1.0, order_type="market"
        )
        rest.reset_mock()
        exchange.place_order("BTC", OrderSide.SELL, 2.0, OrderType.LIMIT, price=9.0)
        rest.place_order.assert_called_once_with(
            symbol="BTC", side="sell", quantity=2.0, order_type="limit", price=9.0
        )


# ======================================================================
# Position / balance normalization
# ======================================================================


class TestNormalization:
    def test_positions_normalize_sides_and_filter_ghosts(self):
        exchange, rest = _make_pacifica_with_mock_rest()
        rest.get_positions.return_value = [
            {"symbol": "BTC", "side": "long", "amount": "1.5", "entry_price": "100"},
            {"symbol": "ETH", "side": "short", "quantity": "2", "entry_price": "50"},
            {"symbol": "SOL", "side": "long", "amount": "0"},  # ghost
            {"symbol": "XRP", "side": "short"},  # no quantity -> ghost
        ]
        positions = exchange.get_positions()
        assert len(positions) == 2
        btc, eth = positions
        assert isinstance(btc, ExchangePosition)
        assert btc.side == PositionSide.LONG
        assert btc.quantity == 1.5
        assert btc.entry_price == 100.0
        assert eth.side == PositionSide.SHORT
        assert eth.quantity == 2.0

    def test_positions_empty_and_none_are_safe(self):
        exchange, rest = _make_pacifica_with_mock_rest()
        rest.get_positions.return_value = None
        assert exchange.get_positions() == []
        rest.get_positions.return_value = []
        assert exchange.get_positions() == []

    def test_balance_extracts_balance_key(self):
        exchange, rest = _make_pacifica_with_mock_rest()
        rest.get_balance.return_value = {"balance": "123.45"}
        balance = exchange.get_balance()
        assert isinstance(balance, ExchangeBalance)
        assert balance.equity == 123.45

    def test_balance_falls_back_to_account_equity(self):
        exchange, rest = _make_pacifica_with_mock_rest()
        rest.get_balance.return_value = {"account_equity": "99.9"}
        assert exchange.get_balance().equity == 99.9

    def test_balance_bad_data_is_zero(self):
        exchange, rest = _make_pacifica_with_mock_rest()
        rest.get_balance.return_value = {"balance": "not-a-number"}
        assert exchange.get_balance().equity == 0.0


# ======================================================================
# Capabilities / funding awareness
# ======================================================================


class TestCapabilities:
    def test_pacifica_funding_interval_is_hourly(self):
        caps = PacificaExchange.capabilities()
        assert caps.funding_interval_hours == 1
        assert caps.name == "pacifica"
        assert caps.native_order_sides == ("bid", "ask")
        assert caps.amounts_as_strings is True

    def test_blofin_funding_interval_is_8h(self):
        assert BlofinExchange.capabilities().funding_interval_hours == 8

    def test_get_exchange_capabilities_uses_env(self, monkeypatch):
        assert get_exchange_capabilities().funding_interval_hours == 1
        monkeypatch.setenv("EXCHANGE", "blofin")
        assert get_exchange_capabilities().funding_interval_hours == 8

    def test_funding_info_carries_interval(self):
        exchange, rest = _make_pacifica_with_mock_rest()
        rest.get_funding_rate.return_value = {
            "funding_rate": "0.0002",
            "next_funding_time": 12345,
            "symbol": "BTC",
        }
        info = exchange.get_funding_info("BTC")
        assert info.funding_rate == 0.0002
        assert info.funding_interval_hours == 1
        assert info.next_funding_time == 12345


class TestFundingArbIntervalAwareness:
    def _strategy(self, interval_hours):
        from trading_bot_v2.strategies.funding_arb import FundingArbStrategy

        strategy = FundingArbStrategy(
            min_funding_rate=0.0001,
            funding_interval_hours=interval_hours,
        )
        # Seed cache directly so no client is needed
        strategy.funding_cache["BTC"] = {
            "current_rate": 0.0003,
            "avg_rate_8h": 0.0003,
            "next_payment": None,
        }
        return strategy

    def test_default_hourly_matches_legacy_math(self):
        strategy = self._strategy(1)
        opportunity = strategy.analyze_funding_opportunity("BTC")
        assert opportunity is not None
        assert opportunity["annualized_yield"] == pytest.approx(
            0.0003 * 24 * 365
        )

    def test_8h_interval_scales_daily_yield(self):
        strategy = self._strategy(8)
        opportunity = strategy.analyze_funding_opportunity("BTC")
        assert opportunity is not None
        assert opportunity["annualized_yield"] == pytest.approx(
            0.0003 * 3 * 365
        )

    def test_summary_apy_scales_with_interval(self):
        hourly = self._strategy(1).get_funding_summary()
        eight_hourly = self._strategy(8).get_funding_summary()
        assert hourly["opportunities"][0]["apy"] == pytest.approx(0.0003 * 24 * 365)
        assert eight_hourly["opportunities"][0]["apy"] == pytest.approx(
            0.0003 * 3 * 365
        )


# ======================================================================
# TradingBot wiring
# ======================================================================


class TestTradingBotWiring:
    def _build_bot(self):
        from trading_bot_v2.trading_bot import TradingBot

        with (
            patch("trading_bot_v2.trading_bot.DatabaseManager"),
            patch("trading_bot_v2.trading_bot.PacificaClient"),
            patch("trading_bot_v2.trading_bot.RiskManager"),
            patch("trading_bot_v2.trading_bot.StrategyManager"),
            patch("trading_bot_v2.trading_bot.GridLifecycleManager"),
            patch(
                "trading_bot_v2.trading_bot.config",
                risk_profile="medium",
                enable_websocket=False,
                circuit_breaker_loss_pct=0.1,
            ),
            patch("trading_bot_v2.trading_bot.make_regime_detector"),
            patch("trading_bot_v2.trading_bot.get_ws_client", return_value=None),
            patch(
                "trading_bot_v2.trading_bot.get_exchange_client",
                wraps=get_exchange_client,
            ) as factory,
        ):
            bot = TradingBot()
        return bot, factory

    def test_construction_calls_factory(self):
        bot, factory = self._build_bot()
        assert factory.called
        assert isinstance(bot.exchange, PacificaExchange)
        assert bot.exchange.rest_client is bot.client

    def test_exchange_property_tracks_client_reassignment(self):
        bot, _ = self._build_bot()
        new_client = MagicMock()
        bot.client = new_client
        assert bot.exchange.rest_client is new_client

    def test_execution_path_routes_normalized_side_to_native_client(self):
        """Adapter delivers exact lowercase buy/market to the raw client."""
        bot, _ = self._build_bot()
        raw_client = MagicMock()
        raw_client.place_order.return_value = {
            "success": True,
            "data": {"order_id": "42", "price": 10.0},
        }
        bot.client = raw_client
        bot.exchange.place_order(
            symbol="SUI", side=OrderSide.BUY, quantity=3.0, order_type=OrderType.MARKET
        )
        raw_client.place_order.assert_called_once_with(
            symbol="SUI", side="buy", quantity=3.0, order_type="market"
        )
