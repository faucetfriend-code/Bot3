"""Risk limits: circuit breaker, exposure caps, capital approval, margin.

Covers ``RiskManager`` (the AUTHORITATIVE sizing / exposure gate), the
``TradingBot`` pre-trade gate ``_should_execute_signal`` and the
coordinator ``_coordinate_signal_execution``, the circuit breaker in
``_monitor_risk``, the exposure reader ``_get_current_exposure`` and
``signal_phases.Phase3ExecutionFilter``.

The numbers are chosen so every assertion is an exact value derived in
a comment, not a ``>= 1.0`` smoke check.  Tests marked
``xfail(strict=True)`` document real defects; a separate change owns
the fixes.  The defects fixed by the A3 logic audit (sizing floor,
exposure read failure, breaker units, breaker trip behaviour) are
asserted here as ordinary passing tests of the fixed behaviour.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from order_risk_fakes import (
    FakeExchange,
    FakeRawClient,
    make_bot,
    make_signal,
    position,
    raiser,
)
from trading_bot_v2.config import StrategyType
from trading_bot_v2.exchanges.base import PositionSide
from trading_bot_v2.models import OrderSide
from trading_bot_v2.risk_manager import RiskManager, RiskProfile
from trading_bot_v2.signal_phases import Phase3ExecutionFilter, PhaseResult

BALANCE = 10_000.0


def sizing_signal(
    entry_price: float = 100.0,
    stop_loss: Optional[float] = 95.0,
    risk_profile: Optional[str] = "medium",
    strategy: Any = StrategyType.MEAN_REVERSION,
) -> SimpleNamespace:
    """Signal-like object carrying only what get_position_size reads."""
    return SimpleNamespace(
        asset="BTC",
        entry_price=entry_price,
        stop_loss=stop_loss,
        risk_profile=risk_profile,
        strategy=strategy,
    )


# ======================================================================
# RiskManager.get_position_size
# ======================================================================


class TestGetPositionSize:
    def test_formula_risk_over_stop_distance(self) -> None:
        # risk 5% of 10k = 500; stop 5% -> notional 10_000; exposure cap
        # 15% = 1_500 binds -> 1_500 / 100 = 15 contracts
        rm = RiskManager()
        qty = rm.get_position_size(sizing_signal(), BALANCE, 0.0)
        assert qty == pytest.approx(15.0)

    def test_uncapped_when_exposure_limit_is_wide(self) -> None:
        rm = RiskManager(max_portfolio_exposure_pct=5.0)
        # 500 / 0.05 = 10_000 notional -> 100 contracts
        assert rm.get_position_size(sizing_signal(), BALANCE, 0.0) == 100.0

    @pytest.mark.parametrize(
        ("profile", "expected"),
        [("low", 50.0), ("medium", 100.0), ("high", 150.0)],
    )
    def test_risk_profile_scales_notional(self, profile: str, expected: float) -> None:
        rm = RiskManager(max_portfolio_exposure_pct=5.0)
        sig = sizing_signal(risk_profile=profile)
        assert rm.get_position_size(sig, BALANCE, 0.0) == pytest.approx(expected)

    def test_missing_profile_is_derived_from_strategy(self) -> None:
        rm = RiskManager(max_portfolio_exposure_pct=5.0)
        sig = sizing_signal(
            risk_profile=None, strategy=StrategyType.LIQUIDATION_CAPTURE
        )
        qty = rm.get_position_size(sig, BALANCE, 0.0)
        assert sig.risk_profile == RiskProfile.HIGH.value
        assert qty == pytest.approx(150.0)

    def test_missing_profile_and_strategy_defaults_to_medium(self) -> None:
        rm = RiskManager(max_portfolio_exposure_pct=5.0)
        sig = sizing_signal(risk_profile=None, strategy=None)
        rm.get_position_size(sig, BALANCE, 0.0)
        assert sig.risk_profile == RiskProfile.MEDIUM.value

    def test_exposure_cap_leaves_only_the_remaining_room(self) -> None:
        rm = RiskManager()
        # 1_500 cap - 1_000 used = 500 notional -> 5 contracts
        assert rm.get_position_size(sizing_signal(), BALANCE, 1_000.0) == 5.0

    def test_exposure_exactly_at_cap_requests_nothing(self) -> None:
        # No room left: notional 0 -> quantity 0.  There is no 1.0-contract
        # floor (audit fix #5): an exhausted budget sizes to nothing.
        rm = RiskManager()
        assert rm.get_position_size(sizing_signal(), BALANCE, 1_500.0) == 0.0

    def test_exposure_over_cap_requests_nothing(self) -> None:
        # Room is negative (1_500 - 9_000); the result is clamped at zero.
        rm = RiskManager()
        assert rm.get_position_size(sizing_signal(), BALANCE, 9_000.0) == 0.0

    @pytest.mark.parametrize("stop", [None, 0.0])
    def test_no_stop_uses_ten_percent_of_risk(self, stop: Optional[float]) -> None:
        rm = RiskManager(max_portfolio_exposure_pct=5.0)
        # 500 * 0.1 = 50 notional -> 0.5 contracts, kept fractional (no floor)
        qty = rm.get_position_size(sizing_signal(stop_loss=stop), BALANCE, 0.0)
        assert qty == pytest.approx(0.5)

    def test_no_stop_small_price_shows_the_ten_percent_rule(self) -> None:
        rm = RiskManager(max_portfolio_exposure_pct=5.0)
        sig = sizing_signal(entry_price=1.0, stop_loss=None)
        assert rm.get_position_size(sig, BALANCE, 0.0) == pytest.approx(50.0)

    def test_stop_equal_to_entry_uses_minimum_notional(self) -> None:
        rm = RiskManager(max_portfolio_exposure_pct=5.0)
        sig = sizing_signal(entry_price=1.0, stop_loss=1.0)
        assert rm.get_position_size(sig, BALANCE, 0.0) == pytest.approx(50.0)

    @pytest.mark.parametrize("price", [0.0, -5.0])
    def test_invalid_entry_price_requests_nothing(self, price: float) -> None:
        # Nothing can be sized without a price; the old 1.0 fallback asked
        # for a whole contract of an asset whose price is unknown.
        rm = RiskManager()
        assert (
            rm.get_position_size(sizing_signal(entry_price=price), BALANCE, 0.0) == 0.0
        )

    def test_signal_without_entry_price_attribute(self) -> None:
        rm = RiskManager()
        sig = SimpleNamespace(asset="BTC")
        assert rm.get_position_size(sig, BALANCE, 0.0) == 0.0

    def test_zero_balance_requests_nothing(self) -> None:
        rm = RiskManager()
        assert rm.get_position_size(sizing_signal(), 0.0, 0.0) == 0.0

    def test_regime_multiplier_applies_before_floor(self) -> None:
        rm = RiskManager()
        base = rm.get_position_size(sizing_signal(), BALANCE, 0.0)
        scaled = rm.get_position_size(
            sizing_signal(), BALANCE, 0.0, regime="indecisive"
        )
        assert scaled == pytest.approx(base * 0.4)


# ======================================================================
# RiskManager.validate_position_size
# ======================================================================


class TestValidatePositionSize:
    rm = RiskManager()  # 5% risk, 15% exposure

    def test_within_limits(self) -> None:
        assert self.rm.validate_position_size(5.0, BALANCE, 0.0, 100.0) is True

    def test_risk_exactly_twice_max_is_allowed(self) -> None:
        # 10 contracts * 100 = 1_000 = 10% = 2 * 5% -> strict > so allowed
        assert self.rm.validate_position_size(10.0, BALANCE, 0.0, 100.0) is True

    def test_risk_just_over_twice_max_is_refused(self) -> None:
        assert self.rm.validate_position_size(10.01, BALANCE, 0.0, 100.0) is False

    def test_exposure_exactly_at_cap_is_allowed(self) -> None:
        # 500 + 1_000 = 1_500 = 15%
        assert self.rm.validate_position_size(5.0, BALANCE, 1_000.0, 100.0) is True

    def test_exposure_over_cap_is_refused(self) -> None:
        assert self.rm.validate_position_size(5.0, BALANCE, 1_000.01, 100.0) is False

    def test_zero_entry_price_treats_quantity_as_notional(self) -> None:
        assert self.rm.validate_position_size(500.0, BALANCE, 0.0) is True
        assert self.rm.validate_position_size(1_001.0, BALANCE, 0.0) is False

    def test_zero_balance_refuses_everything(self) -> None:
        assert self.rm.validate_position_size(0.001, 0.0, 0.0, 100.0) is False

    def test_zero_quantity_passes(self) -> None:
        assert self.rm.validate_position_size(0.0, BALANCE, 0.0, 100.0) is True


# ======================================================================
# RiskManager capital approval gate
# ======================================================================


class TestCapitalAllocation:
    def test_approved_within_room(self) -> None:
        rm = RiskManager()
        out = rm.request_capital_allocation(
            "BTC", 400.0, "MEAN_REVERSION", BALANCE, 0.0
        )
        assert out["approved"] is True and out["allocated_amount"] == 400.0
        assert out["reason"] == "approved" and "approval_id" in out
        assert rm.get_allocation_status(out["approval_id"])["status"] == "approved"

    def test_request_is_clipped_to_remaining_exposure(self) -> None:
        rm = RiskManager()
        out = rm.request_capital_allocation("BTC", 5_000.0, "X", BALANCE, 1_000.0)
        assert out["allocated_amount"] == pytest.approx(500.0)

    @pytest.mark.parametrize("amount", [0.0, -1.0])
    def test_non_positive_request_is_refused(self, amount: float) -> None:
        rm = RiskManager()
        out = rm.request_capital_allocation("BTC", amount, "X", BALANCE, 0.0)
        assert out == {
            "approved": False,
            "allocated_amount": 0.0,
            "reason": "invalid_request_amount",
        }

    def test_zero_balance_is_refused(self) -> None:
        rm = RiskManager()
        out = rm.request_capital_allocation("BTC", 10.0, "X", 0.0, 0.0)
        assert out["reason"] == "insufficient_account_balance"

    def test_exposure_exactly_at_cap_is_refused(self) -> None:
        rm = RiskManager()
        out = rm.request_capital_allocation("BTC", 10.0, "X", BALANCE, 1_500.0)
        assert out["approved"] is False
        assert out["reason"] == "exposure_limit_exceeded"

    def test_exposure_one_cent_under_cap_is_approved(self) -> None:
        rm = RiskManager()
        out = rm.request_capital_allocation("BTC", 10.0, "X", BALANCE, 1_499.99)
        assert out["approved"] is True
        assert out["allocated_amount"] == pytest.approx(0.01)

    def test_grid_strategy_capped_at_four_percent(self) -> None:
        rm = RiskManager()
        out = rm.request_capital_allocation(
            "BTC", 1_000.0, "GRID_TRADING", BALANCE, 0.0
        )
        assert out["allocated_amount"] == pytest.approx(400.0)

    def test_legacy_mode_approves_anything(self) -> None:
        rm = RiskManager()
        rm._approval_required = False
        out = rm.request_capital_allocation("BTC", 1e9, "X", BALANCE, 1e9)
        assert out["approved"] is True and out["allocated_amount"] == 1e9
        assert rm.validate_capital_usage("missing", 1.0) is True

    def test_usage_within_one_percent_tolerance(self) -> None:
        rm = RiskManager()
        out = rm.request_capital_allocation("BTC", 100.0, "X", BALANCE, 0.0)
        approval_id = out["approval_id"]
        assert rm.validate_capital_usage(approval_id, 101.0) is True
        assert rm.get_allocation_status(approval_id)["status"] == "used"

    def test_usage_over_tolerance_is_refused(self) -> None:
        rm = RiskManager()
        out = rm.request_capital_allocation("BTC", 100.0, "X", BALANCE, 0.0)
        assert rm.validate_capital_usage(out["approval_id"], 101.01) is False

    def test_unknown_approval_is_refused(self) -> None:
        assert RiskManager().validate_capital_usage("nope", 1.0) is False

    def test_expired_approvals_are_cleaned_up(self) -> None:
        rm = RiskManager()
        out = rm.request_capital_allocation("BTC", 100.0, "X", BALANCE, 0.0)
        rm._pending_approvals[out["approval_id"]]["timestamp"] -= 7_200
        rm.cleanup_expired_approvals(max_age_seconds=3_600)
        assert rm.get_allocation_status(out["approval_id"]) is None

    def test_exposure_summary_and_emergency_stop(self) -> None:
        rm = RiskManager()
        rm.grid_exposure["BTC"] = 300.0
        rm.request_capital_allocation("BTC", 100.0, "X", BALANCE, 0.0)
        rm.register_migrated_position(
            "ETH", {"side": "long", "qty": 1.0, "entry_price": 10.0, "has_stop": True}
        )
        summary = rm.get_exposure_summary()
        assert summary["total_grid_exposure"] == 300.0
        assert summary["pending_approvals_count"] == 1
        assert summary["approval_mode"] == "authoritative"
        rm.emergency_stop_all()
        assert rm.grid_exposure == {} and rm._pending_approvals == {}
        assert rm.migrated_positions == {}


# ======================================================================
# RiskManager grid allocation and parameter normalization
# ======================================================================


class TestGridLimits:
    def test_grid_capital_is_the_grid_share_of_exposure(self) -> None:
        rm = RiskManager()
        # 15% of 10k = 1_500; 35% of the 1_500 exposure cap = 525 binds
        assert rm.get_grid_capital(BALANCE, 0.0, "BTC") == pytest.approx(525.0)
        assert rm.last_known_balance == BALANCE

    def test_grid_capital_floors_at_one_percent_when_saturated(self) -> None:
        rm = RiskManager()
        rm.grid_exposure["ETH"] = 525.0
        assert rm.get_grid_capital(BALANCE, 0.0, "BTC") == pytest.approx(100.0)

    def test_grid_exposure_symbol_limit_is_half_the_grid_capital(self) -> None:
        rm = RiskManager()
        assert rm.validate_grid_exposure("BTC", 262.5, BALANCE, 0.0) is True
        assert rm.validate_grid_exposure("BTC", 262.51, BALANCE, 0.0) is False

    def test_grid_exposure_total_limit(self) -> None:
        rm = RiskManager()
        rm.grid_exposure["ETH"] = 400.0
        # symbol cap: grid capital = min(1500, 525-400=125) -> 125 -> half 62.5
        assert rm.validate_grid_exposure("BTC", 62.5, BALANCE, 0.0) is True
        rm.grid_exposure["ETH"] = 500.0
        # grid capital floors at 100 -> symbol cap 50; total 500 + 50 > 525
        assert rm.validate_grid_exposure("BTC", 50.0, BALANCE, 0.0) is False

    def test_emergency_exit_resets_symbol_exposure(self) -> None:
        rm = RiskManager()
        rm.grid_exposure["BTC"] = 10.0
        rm.on_grid_emergency_exit("BTC")
        rm.on_grid_emergency_exit("ETH")  # absent symbol is a no-op
        assert rm.grid_exposure == {}

    @pytest.mark.parametrize(
        ("levels", "quantity", "spacing", "expected"),
        [
            (1, 0.0, 0.0, (2, 0.001, 0.001)),
            (20, 50.0, 0.5, (10, 10.0, 0.05)),
            (5, -1.0, -0.1, (5, 0.001, 0.001)),
            (2, 0.001, 0.001, (2, 0.001, 0.001)),
            (10, 10.0, 0.05, (10, 10.0, 0.05)),
        ],
    )
    def test_grid_params_are_clamped(
        self, levels: int, quantity: float, spacing: float, expected: tuple
    ) -> None:
        out = RiskManager().normalize_grid_params(levels, quantity, spacing)
        assert (out["grid_levels"], out["quantity"], out["spacing"]) == expected


# ======================================================================
# RiskManager strategy risk profiles
# ======================================================================


class TestStrategyRiskProfile:
    rm = RiskManager()

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("mean_reversion", "medium"),
            ("MeanReversion", "medium"),
            ("Liquidation Capture", "high"),
            ("momentumscalping", "high"),
            ("unknown_strategy", "medium"),
        ],
    )
    def test_profiles_are_case_and_separator_insensitive(
        self, name: str, expected: str
    ) -> None:
        assert self.rm.get_strategy_risk_profile(name) == expected


# ======================================================================
# RiskManager margin safety
# ======================================================================


class MarginClient:
    """Raw client stand-in serving a canned balance payload."""

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls = 0

    def get_balance(self) -> Any:
        self.calls += 1
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def margin_payload(
    equity: str = "10000", used: str = "1000", mmr: str = "100", avail: str = "8000"
) -> Dict[str, str]:
    """Pacifica-style balance payload (all strings)."""
    return {
        "account_equity": equity,
        "total_margin_used": used,
        "cross_mmr": mmr,
        "available_to_spend": avail,
        "balance": equity,
    }


class TestMarginSafety:
    def test_no_client_is_safe_with_a_warning(self) -> None:
        out = RiskManager().check_margin_safety(100.0)
        assert out["safe"] is True and out["warnings"]
        assert RiskManager().get_margin_summary()["status"] == "unavailable"

    def test_margin_data_parses_strings_and_caches(self) -> None:
        client = MarginClient(margin_payload())
        rm = RiskManager(client=client)
        data = rm.get_margin_data()
        assert data["account_equity"] == 10_000.0
        assert data["margin_utilization_pct"] == pytest.approx(0.1)
        rm.get_margin_data()
        assert client.calls == 1
        rm.get_margin_data(force_refresh=True)
        assert client.calls == 2

    def test_fetch_error_returns_stale_cache_then_none(self) -> None:
        client = MarginClient(margin_payload())
        rm = RiskManager(client=client)
        rm.get_margin_data()
        client.payload = RuntimeError("down")
        assert rm.get_margin_data(force_refresh=True) is not None
        assert (
            RiskManager(client=MarginClient(RuntimeError("x"))).get_margin_data()
            is None
        )

    def test_empty_balance_response_is_none(self) -> None:
        assert RiskManager(client=MarginClient({})).get_margin_data() is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("$1,000.50", 1000.5), (None, 0.0), ("abc", 0.0), (3, 3.0), (" 7 ", 7.0)],
    )
    def test_parse_float_safe(self, raw: Any, expected: float) -> None:
        assert RiskManager()._parse_float_safe(raw) == expected

    def test_healthy_account_is_safe(self) -> None:
        rm = RiskManager(client=MarginClient(margin_payload()))
        out = rm.check_margin_safety(proposed_margin=1_000.0)
        assert out["safe"] is True and out["reason"] == ""
        assert out["utilization_after"] == pytest.approx(0.2)
        # min(available_to_spend 8_000, 0.75 * 10_000 - 1_000 = 6_500)
        assert out["available_margin"] == pytest.approx(6_500.0)

    def test_current_utilization_at_max_is_unsafe(self) -> None:
        rm = RiskManager(client=MarginClient(margin_payload(used="7500")))
        out = rm.check_margin_safety()
        assert out["safe"] is False and "Current margin utilization" in out["reason"]

    def test_proposed_margin_over_max_is_unsafe(self) -> None:
        rm = RiskManager(client=MarginClient(margin_payload(used="7000")))
        assert rm.check_margin_safety(proposed_margin=500.0)["safe"] is True
        assert rm.check_margin_safety(proposed_margin=500.01)["safe"] is False

    def test_equity_under_maintenance_buffer_is_unsafe(self) -> None:
        # required equity = 9_000 * 1.15 = 10_350 > 10_000
        rm = RiskManager(client=MarginClient(margin_payload(mmr="9000")))
        out = rm.check_margin_safety()
        assert out["safe"] is False and "maintenance margin" in out["reason"]

    def test_thin_maintenance_buffer_only_warns(self) -> None:
        # equity 10_000, mmr 8_600: required 9_890 ok; buffer 1_400 > 860 ok
        # mmr 8_690: required 9_993.5 ok; buffer 1_310 > 869 -> no warning
        rm = RiskManager(client=MarginClient(margin_payload(mmr="8690")))
        assert rm.check_margin_safety()["warnings"] == []
        # buffer < 10% of mmr needs equity < 1.1 * mmr, but the 1.15 buffer
        # check fires first; the warning branch is unreachable with the
        # default 15% buffer, so lower it to make the warning observable.
        rm = RiskManager(
            client=MarginClient(margin_payload(mmr="9500")),
            maintenance_margin_buffer_pct=0.0,
        )
        out = rm.check_margin_safety()
        assert (
            out["safe"] is True
            and "Low maintenance margin buffer" in out["warnings"][0]
        )

    def test_insufficient_available_margin_is_unsafe(self) -> None:
        rm = RiskManager(client=MarginClient(margin_payload(avail="100")))
        out = rm.check_margin_safety(proposed_margin=200.0)
        assert out["safe"] is False and "Insufficient available margin" in out["reason"]

    @pytest.mark.parametrize(
        ("used", "status"),
        [("5999", "healthy"), ("6000", "warning"), ("7500", "critical")],
    )
    def test_summary_status_bands(self, used: str, status: str) -> None:
        rm = RiskManager(client=MarginClient(margin_payload(used=used)))
        summary = rm.get_margin_summary()
        assert summary["status"] == status
        assert summary["safe_for_new_positions"] is (status != "critical")

    def test_summary_without_mmr_reports_full_buffer(self) -> None:
        rm = RiskManager(client=MarginClient(margin_payload(mmr="0")))
        assert rm.get_margin_summary()["maintenance_margin_buffer_pct"] == 100.0


# ======================================================================
# RiskManager migrated-position tracking
# ======================================================================


def migrated(qty: float = 1.0, price: float = 100.0, stop: bool = True) -> Dict:
    """Migrated position record."""
    return {"side": "long", "qty": qty, "entry_price": price, "has_stop": stop}


class TestMigratedPositions:
    def test_register_requires_core_fields(self) -> None:
        rm = RiskManager()
        assert rm.register_migrated_position("BTC", {"side": "long"}) is False
        assert rm.has_migrated_positions() is False

    def test_register_without_stop_still_registers(self) -> None:
        rm = RiskManager()
        assert rm.register_migrated_position("BTC", migrated(stop=False)) is True
        assert rm.get_migrated_positions("BTC")[0]["has_stop"] is False

    def test_exposure_is_quantity_times_entry(self) -> None:
        rm = RiskManager()
        rm.register_migrated_position("BTC", migrated(2.0, 100.0))
        rm.register_migrated_position("ETH", migrated(5.0, 10.0))
        rm.grid_exposure["BTC"] = 30.0
        assert rm.get_migrated_exposure("BTC") == 200.0
        assert rm.get_migrated_exposure() == 250.0
        total = rm.get_total_exposure("BTC")
        assert total == {
            "grid_exposure": 30.0,
            "migrated_exposure": 200.0,
            "total_exposure": 230.0,
        }
        assert rm.get_total_exposure()["total_exposure"] == 280.0

    def test_validate_requires_stop_and_caps_total(self) -> None:
        rm = RiskManager()
        bad = rm.validate_migrated_position("BTC", migrated(stop=False), BALANCE)
        assert bad["valid"] is False and "MUST have stop" in bad["errors"][0]
        # 11 * 100 = 1_100 > 10% warns but stays valid
        warn = rm.validate_migrated_position("BTC", migrated(11.0), BALANCE)
        assert warn["valid"] is True and warn["warnings"]
        # 21 * 100 = 2_100 > 20% of account is an error
        big = rm.validate_migrated_position("BTC", migrated(21.0), BALANCE)
        assert big["valid"] is False and "exceed limit" in big["errors"][0]

    def test_unregister_by_side_and_quantity(self) -> None:
        rm = RiskManager()
        rm.register_migrated_position("BTC", migrated(1.0))
        rm.register_migrated_position("BTC", migrated(2.0))
        assert rm.unregister_migrated_position("BTC", "short") is False
        assert rm.unregister_migrated_position("BTC", "long", qty=2.0) is True
        assert len(rm.get_migrated_positions("BTC")) == 1
        assert rm.unregister_migrated_position("BTC", "long") is True
        assert rm.has_migrated_positions("BTC") is False
        assert rm.unregister_migrated_position("BTC", "long") is False

    def test_update_stop(self) -> None:
        rm = RiskManager()
        assert rm.update_migrated_stop("BTC", "long", 90.0) is False
        rm.register_migrated_position("BTC", migrated(stop=False))
        assert rm.update_migrated_stop("BTC", "long", 90.0) is True
        record = rm.get_migrated_positions("BTC")[0]
        assert record["stop_price"] == 90.0 and record["has_stop"] is True
        assert rm.update_migrated_stop("BTC", "short", 90.0) is False


# ======================================================================
# TradingBot pre-trade gate and coordinator
# ======================================================================


@pytest.fixture
def unpaused():
    """Supervisor pause flag off, regardless of any file on disk."""
    control = SimpleNamespace(is_paused=lambda: False, status=lambda: {})
    with patch(
        "trading_bot_v2.supervisor_control.get_supervisor_control",
        return_value=control,
    ):
        yield control


@pytest.fixture
def paused():
    """Supervisor pause flag on."""
    control = SimpleNamespace(
        is_paused=lambda: True,
        status=lambda: {"raw_state": {"reason": "maintenance"}},
    )
    with patch(
        "trading_bot_v2.supervisor_control.get_supervisor_control",
        return_value=control,
    ):
        yield control


def _exposed(balance: float, exposure_notional: float) -> FakeExchange:
    """Exchange reporting one position worth ``exposure_notional`` at entry."""
    positions = []
    if exposure_notional:
        positions.append(
            position(quantity=exposure_notional / 100.0, entry_price=100.0)
        )
    return FakeExchange(balance=balance, positions=positions)


class TestShouldExecuteSignal:
    def test_valid_signal_passes(self, unpaused: Any) -> None:
        bot = make_bot(_exposed(BALANCE, 1_000.0))
        assert bot._should_execute_signal(make_signal()) is True
        assert bot.signal_logger.rejected == []

    def test_circuit_breaker_blocks_before_anything_else(self, paused: Any) -> None:
        bot = make_bot(_exposed(BALANCE, 0.0))
        bot._circuit_breaker_triggered = True
        assert bot._should_execute_signal(make_signal(all_flags=False)) is False
        assert "Circuit breaker" in bot.signal_logger.rejected[0]["reason"]

    def test_supervisor_pause_blocks(self, paused: Any) -> None:
        bot = make_bot(_exposed(BALANCE, 0.0))
        assert bot._should_execute_signal(make_signal()) is False
        assert (
            bot.signal_logger.rejected[0]["reason"] == "supervisor pause: maintenance"
        )

    def test_failed_flags_are_named(self, unpaused: Any) -> None:
        bot = make_bot(_exposed(BALANCE, 0.0))
        sig = make_signal(all_flags=False, volume_confirmation=True)
        assert bot._should_execute_signal(sig) is False
        reason = bot.signal_logger.rejected[0]["reason"]
        assert "multi_timeframe_alignment" in reason
        assert "volume_confirmation" not in reason

    @pytest.mark.parametrize("stop", [None, 0.0, -1.0])
    def test_missing_or_invalid_stop_is_refused(
        self, unpaused: Any, stop: Optional[float]
    ) -> None:
        bot = make_bot(_exposed(BALANCE, 0.0))
        assert bot._should_execute_signal(make_signal(stop_loss=stop)) is False
        assert "stop loss" in bot.signal_logger.rejected[0]["reason"]

    def test_zero_balance_is_refused(self, unpaused: Any) -> None:
        bot = make_bot(_exposed(0.0, 0.0))
        assert bot._should_execute_signal(make_signal()) is False
        assert (
            bot.signal_logger.rejected[0]["reason"] == "Invalid account balance (<=0)"
        )

    def test_balance_fetch_error_reads_as_zero(self, unpaused: Any) -> None:
        exchange = FakeExchange()
        exchange.get_balance = raiser(RuntimeError("x"))
        bot = make_bot(exchange)
        assert bot._get_account_balance() == 0.0
        assert bot._should_execute_signal(make_signal()) is False

    def test_exposure_exactly_eighty_percent_is_refused(self, unpaused: Any) -> None:
        bot = make_bot(_exposed(BALANCE, 8_000.0))
        assert bot._should_execute_signal(make_signal()) is False
        assert "80.0% >= 80%" in bot.signal_logger.rejected[0]["reason"]

    def test_exposure_just_under_eighty_percent_passes(self, unpaused: Any) -> None:
        bot = make_bot(_exposed(BALANCE, 7_999.0))
        assert bot._should_execute_signal(make_signal()) is True

    def test_unexpected_error_is_a_rejection(self, unpaused: Any) -> None:
        bot = make_bot(_exposed(BALANCE, 0.0))
        sig = make_signal()
        sig.is_valid = raiser(ValueError("boom"))  # type: ignore[method-assign]
        assert bot._should_execute_signal(sig) is False
        assert "Validation error" in bot.signal_logger.rejected[0]["reason"]


class TestCurrentExposure:
    def test_sums_quantity_times_live_price(self) -> None:
        exchange = FakeExchange(
            positions=[
                position("BTC", quantity=2.0, entry_price=100.0),
                position("ETH", PositionSide.SHORT, quantity=10.0, entry_price=10.0),
            ]
        )
        bot = make_bot(exchange, ws_prices={"BTC": 110.0})
        # BTC uses the WS price 110, ETH falls back to entry 10
        assert bot._get_current_exposure() == pytest.approx(2 * 110.0 + 10 * 10.0)

    def test_no_positions_is_zero(self) -> None:
        assert make_bot(FakeExchange())._get_current_exposure() == 0.0

    def test_exchange_error_raises_instead_of_reading_as_zero(self) -> None:
        # Audit fix #3: a failed positions read used to count as zero
        # exposure and let the allocator grant the full budget.
        exchange = FakeExchange()
        exchange.get_positions = raiser(RuntimeError("x"))
        with pytest.raises(RuntimeError, match="Exposure unavailable"):
            make_bot(exchange)._get_current_exposure()

    def test_exchange_error_rejects_the_signal(self, unpaused: Any) -> None:
        exchange = FakeExchange(balance=BALANCE)
        exchange.get_positions = raiser(RuntimeError("x"))
        bot = make_bot(exchange)
        assert bot._should_execute_signal(make_signal()) is False
        assert "Exposure unavailable" in bot.signal_logger.rejected[0]["reason"]


class TestCoordinateExecution:
    def test_denied_allocation_sends_nothing(self) -> None:
        exchange = _exposed(BALANCE, 1_500.0)  # exactly at the 15% cap
        bot = make_bot(exchange, risk_manager=RiskManager())
        bot._coordinate_signal_execution(make_signal())
        assert exchange.orders == []
        # The sizer now returns 0 at the cap (no 1.0 floor), so the
        # allocator refuses the empty request before its own cap check.
        reason = bot.signal_logger.rejected[0]["reason"]
        assert reason == "Capital allocation denied: invalid_request_amount"

    def test_order_quantity_comes_from_the_allocation(self) -> None:
        exchange = _exposed(BALANCE, 1_000.0)
        bot = make_bot(exchange, risk_manager=RiskManager())
        bot._coordinate_signal_execution(make_signal(entry_price=100.0, stop_loss=95.0))
        # sizer: 500 room -> 5 contracts -> requested 500 -> allocated 500
        assert exchange.orders[0]["quantity"] == pytest.approx(5.0)

    def test_exposure_cap_bounds_the_order(self) -> None:
        exchange = _exposed(BALANCE, 0.0)
        rm = RiskManager(max_portfolio_exposure_pct=0.15)
        rm.max_portfolio_risk_pct = 0.5  # oversized risk budget
        bot = make_bot(exchange, risk_manager=rm)
        bot._coordinate_signal_execution(make_signal(entry_price=100.0, stop_loss=95.0))
        # cap 1_500 notional -> 15 contracts whatever the risk budget allows
        assert exchange.orders[0]["quantity"] == pytest.approx(15.0)

    def test_grid_signals_route_to_the_grid_path(self) -> None:
        exchange = _exposed(BALANCE, 0.0)
        bot = make_bot(exchange, risk_manager=RiskManager())
        seen: List[Any] = []
        bot._execute_grid_signal_coordinated = lambda s, a, log_entry=None: seen.append(
            a
        )
        bot._coordinate_signal_execution(
            make_signal(strategy=StrategyType.GRID_TRADING)
        )
        assert exchange.orders == [] and len(seen) == 1
        assert seen[0]["allocated_amount"] <= BALANCE * 0.04

    def test_sizer_exception_is_logged_as_failed(self) -> None:
        exchange = _exposed(BALANCE, 0.0)
        rm = RiskManager()
        rm.get_position_size = raiser(KeyError("k"))  # type: ignore[method-assign]
        bot = make_bot(exchange, risk_manager=rm)
        bot._coordinate_signal_execution(make_signal())
        assert exchange.orders == []
        assert (
            bot.signal_logger.failed[0]["notes"]
            == "Exception during signal coordination"
        )


# ======================================================================
# Circuit breaker
# ======================================================================


def _breaker_bot(balance: float, pnl_values: List[float]) -> Any:
    """Bot whose raw client reports positions with the given unrealized PnL."""
    rows = [{"unrealized_pnl": str(v)} for v in pnl_values]
    return make_bot(FakeExchange(balance=balance), raw_client=FakeRawClient(rows))


class TestCircuitBreaker:
    def test_loss_at_threshold_trips_and_keeps_the_loop_running(
        self, unpaused: Any
    ) -> None:
        # A trip blocks new entries but must not stop the loop: the loop
        # is what enforces local stops on venues without venue-side stops.
        bot = _breaker_bot(BALANCE, [-600.0, -400.0])  # -10%
        events: List[Dict[str, Any]] = []
        bot.hub_publish_func = events.append
        bot._monitor_risk()
        assert bot._circuit_breaker_triggered is True and bot.stopped is False
        assert events[0]["type"] == "circuit_breaker"
        assert events[0]["pnl_percentage"] == pytest.approx(-10.0)
        assert bot._should_execute_signal(make_signal()) is False
        assert "Circuit breaker" in bot.signal_logger.rejected[0]["reason"]

    def test_repeated_checks_while_tripped_publish_one_event(self) -> None:
        bot = _breaker_bot(BALANCE, [-1_000.0])
        events: List[Dict[str, Any]] = []
        bot.hub_publish_func = events.append
        bot._monitor_risk()
        bot._monitor_risk()
        assert [e["type"] for e in events] == ["circuit_breaker"]
        assert bot.stopped is False

    def test_profit_never_trips(self) -> None:
        bot = _breaker_bot(BALANCE, [500.0])
        bot._monitor_risk()
        assert bot._circuit_breaker_triggered is False and bot.stopped is False

    @pytest.mark.parametrize("loss", [-100.0, -500.0, -800.0])
    def test_loss_under_threshold_does_not_trip(self, loss: float) -> None:
        bot = _breaker_bot(BALANCE, [loss])  # 1%, 5%, 8%
        bot._monitor_risk()
        assert bot._circuit_breaker_triggered is False
        assert bot.stopped is False

    def test_tiny_loss_under_the_misread_threshold_stays_off(self) -> None:
        # 0.05% loss: below even the old fraction-as-percent threshold
        # (0.1%), so it passed before the units were fixed and still does.
        bot = _breaker_bot(BALANCE, [-5.0])
        bot._monitor_risk()
        assert bot._circuit_breaker_triggered is False

    def test_zero_balance_skips_monitoring(self) -> None:
        bot = _breaker_bot(0.0, [-1e6])
        bot._monitor_risk()
        assert bot._circuit_breaker_triggered is False

    def test_no_positions_skips_monitoring(self) -> None:
        bot = _breaker_bot(BALANCE, [])
        bot._monitor_risk()
        assert bot._circuit_breaker_triggered is False

    def test_client_error_is_swallowed(self) -> None:
        bot = make_bot(FakeExchange(), raw_client=FakeRawClient(RuntimeError("x")))
        bot._monitor_risk()
        assert bot._circuit_breaker_triggered is False

    def test_tripped_breaker_blocks_new_entries(self, unpaused: Any) -> None:
        bot = _breaker_bot(BALANCE, [-1_000.0])
        bot._monitor_risk()
        assert bot._should_execute_signal(make_signal()) is False


# ======================================================================
# Legacy TradingBot sizing wrappers
# ======================================================================


class TestLegacySizingWrappers:
    @pytest.mark.xfail(
        strict=True,
        raises=TypeError,
        reason=(
            "TradingBot._calculate_position_size calls RiskManager."
            "get_position_size(strategy=, entry_price=, stop_loss=, confidence=) "
            "but the authoritative signature is (signal, account_balance, "
            "current_exposure, regime).  Only the retired _execute_grid_signal "
            "path reaches it, but any caller gets a TypeError."
        ),
    )
    def test_calculate_position_size_delegates(self) -> None:
        bot = make_bot(FakeExchange(), risk_manager=RiskManager())
        assert bot._calculate_position_size(make_signal()) > 0

    @pytest.mark.xfail(
        strict=True,
        raises=TypeError,
        reason=(
            "TradingBot._validate_position_size omits the required "
            "account_balance / current_exposure arguments of RiskManager."
            "validate_position_size, so it raises instead of validating."
        ),
    )
    def test_validate_position_size_delegates(self) -> None:
        bot = make_bot(FakeExchange(), risk_manager=RiskManager())
        assert bot._validate_position_size(1.0, 100.0) in (True, False)

    def test_emergency_stop_is_seven_and_a_half_percent_below_entry(self) -> None:
        bot = make_bot(FakeExchange())
        assert bot._calculate_emergency_stop(make_signal(entry_price=200.0)) == 185.0


# ======================================================================
# signal_phases.Phase3ExecutionFilter
# ======================================================================


def phase3(balance: Any = "default", **config: Any) -> Phase3ExecutionFilter:
    """Phase 3 filter over a raw client returning ``balance``."""
    if balance == "default":
        balance = {"total": BALANCE}
    client = SimpleNamespace(get_balance=lambda: balance)
    return Phase3ExecutionFilter(RiskManager(), client, SimpleNamespace(**config))


def p3_signal(**overrides: Any) -> SimpleNamespace:
    """Signal-like object with the ``symbol`` attribute Phase 3 reads."""
    fields = dict(symbol="BTC", entry_price=100.0, stop_loss=95.0, side=OrderSide.BUY)
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestPhase3ExecutionFilter:
    def test_passes_within_limits(self) -> None:
        out = phase3().check(p3_signal(), 5.0)
        assert out.result == PhaseResult.PASS
        assert out.details["exposure_pct"] == pytest.approx(0.05)

    def test_size_below_minimum_blocks_and_at_minimum_passes(self) -> None:
        assert phase3().check(p3_signal(), 0.0009).result == PhaseResult.BLOCK
        assert phase3().check(p3_signal(), 0.001).result == PhaseResult.PASS

    def test_zero_or_negative_size_blocks(self) -> None:
        for size in (0.0, -1.0):
            out = phase3().check(p3_signal(), size)
            assert out.result == PhaseResult.BLOCK and "below minimum" in out.reason

    def test_exposure_at_cap_passes_and_over_blocks(self) -> None:
        assert phase3().check(p3_signal(), 25.0).result == PhaseResult.PASS
        out = phase3().check(p3_signal(), 25.01)
        assert out.result == PhaseResult.BLOCK and "Exposure" in out.reason

    def test_existing_exposure_counts_against_the_cap(self) -> None:
        flt = phase3()
        flt.risk_manager.grid_exposure["BTC"] = 2_000.0
        assert flt.check(p3_signal(), 5.0).result == PhaseResult.PASS
        assert flt.check(p3_signal(), 5.01).result == PhaseResult.BLOCK

    def test_invalid_entry_price_blocks(self) -> None:
        out = phase3().check(p3_signal(entry_price=0.0), 5.0)
        assert out.result == PhaseResult.BLOCK and "entry price" in out.reason

    def test_negative_stop_blocks_but_missing_stop_passes(self) -> None:
        assert (
            phase3().check(p3_signal(stop_loss=-1.0), 5.0).result == PhaseResult.BLOCK
        )
        assert phase3().check(p3_signal(stop_loss=None), 5.0).result == PhaseResult.PASS

    @pytest.mark.parametrize(
        ("balance", "expected"),
        [
            ({"total": 500.0}, 500.0),
            ({"equity": 700.0}, 700.0),
            ({}, 10_000.0),
            (2_500.0, 2_500.0),
            (None, 10_000.0),
        ],
    )
    def test_balance_shapes(self, balance: Any, expected: float) -> None:
        assert phase3(balance)._get_account_balance() == expected

    def test_balance_error_falls_back(self) -> None:
        client = SimpleNamespace(get_balance=raiser(OSError("x")))
        flt = Phase3ExecutionFilter(RiskManager(), client, SimpleNamespace())
        assert flt._get_account_balance() == 10_000.0

    @pytest.mark.xfail(
        strict=True,
        raises=AttributeError,
        reason=(
            "Phase3ExecutionFilter.check reads signal.symbol, but models.Signal "
            "names the field 'asset'.  A real Signal through a real RiskManager "
            "raises AttributeError before any limit is checked."
        ),
    )
    def test_accepts_a_real_signal(self) -> None:
        out = phase3().check(make_signal(), 5.0)
        assert out.result == PhaseResult.PASS
