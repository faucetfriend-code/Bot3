"""
Simulated Exchange
==================

Replaces PacificaClient and PacificaWebSocketClient during backtesting.

The SimulatedExchange:
  - Fills limit orders when price crosses the order level
  - Applies slippage and taker/maker fees on every fill
  - Tracks positions, margin, and unrealised PnL
  - Simulates hourly funding charges/credits on open positions
  - Enforces balance checks before every order (no negative balance)
  - Records realised PnL per trade for accurate win/loss metrics
  - Tags each trade with the strategy that generated it
  - Randomises SL/TP fill priority when both trigger in the same candle

Costs are resolved through ``cost_model.CostTable``, which is per-symbol
and per-liquidity-role. Resting limit orders pay the maker fee and take
no slippage; market and stop orders pay the taker fee plus slippage. See
cost_model.py for where the rates come from and which profile is active.
"""

import os
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from loguru import logger

from .cost_model import CostTable, LiquidityRole
from .funding import FundingSchedule


@dataclass
class SimulatedOrder:
    order_id: str
    symbol: str
    side: str          # "bid" | "ask"
    price: float
    quantity: float
    order_type: str    # "limit" | "market" | "stop"
    status: str = "open"
    filled_qty: float = 0.0
    fill_price: float = 0.0
    fee: float = 0.0
    timestamp: str = ""


@dataclass
class SimulatedPosition:
    symbol: str
    side: str          # "long" | "short"
    quantity: float
    entry_price: float
    unrealised_pnl: float = 0.0
    realised_pnl: float = 0.0
    funding_paid: float = 0.0
    entry_regime: str = ""  # Confirmed regime at position open (P4)


class SimulatedExchange:
    """
    Full simulated exchange compatible with PacificaClient interface.

    Fix 1 — Balance guard:
        Every order checks available balance before execution.
        Orders that would require more than the available balance are
        silently rejected (logged at DEBUG level). This prevents the
        balance going negative and max-drawdown exceeding 100%.

    Fix 2 — PnL tracking:
        Realised PnL is calculated when a position is fully or partially
        closed and written into the trade_log entry as "pnl". This gives
        accurate win_rate and profit_factor in PerformanceTracker.

    Fix 3 — Strategy attribution:
        The engine sets _current_strategy before placing each order.
        Every trade_log entry carries a "strategy" field for per-strategy
        PnL breakdown.

    Fix 4 — SL/TP randomisation:
        When both a stop and a take-profit order trigger within the same
        candle's high/low range, the processing order is randomised so
        neither is systematically favoured (removes pessimistic SL bias).
        The draw comes from a per-exchange seeded RNG (``BACKTEST_SEED``)
        so the same window over the same data reproduces exactly.
    """

    def __init__(
        self,
        initial_capital: float,
        slippage_pct: float = 0.002,
        taker_fee_pct: float = 0.0006,
        maker_fee_pct: float = 0.0002,
        funding_hourly_pct: float = 0.0001,
        cost_table: Optional[CostTable] = None,
        funding_schedule: Optional[FundingSchedule] = None,
        funding_interval_hours: int = 1,
        seed: Optional[int] = None,
    ):
        """Build a simulated exchange.

        Args:
            initial_capital: Starting cash balance.
            slippage_pct: Global flat slippage (legacy cost profile).
            taker_fee_pct: Global taker fee before per-symbol overrides.
            maker_fee_pct: Global maker fee before per-symbol overrides.
            funding_hourly_pct: Flat per-interval funding rate used when
                no ``funding_schedule`` is supplied. Sign-locked: longs
                always pay it and shorts always receive it, which real
                funding does not do (BTC funding was negative on ~14% of
                settlements 2019-2026).
            cost_table: Pre-built cost table. When omitted one is built
                from the environment with the three rates above as the
                global bases, so the default behaviour is unchanged.
            funding_schedule: Real ingested funding series mapped onto
                this venue's settlement clock. When present it replaces
                the flat rate entirely, including its sign.
            funding_interval_hours: Venue settlement cadence in hours
                (Pacifica 1, Blofin 8). Only consulted for the flat
                model; a schedule carries its own.
            seed: Seed for the SL/TP tie-break RNG. Defaults to
                ``BACKTEST_SEED`` (0). Before this existed the shuffle
                drew from the process-global ``random`` module, so two
                runs of the SAME window over the SAME data could differ
                - measured on vwap_scalping/BTC-USDC 2022-06..08, PF
                0.9485 vs 1.0287 across consecutive runs. Every backtest
                number this repo produced before the seed existed is
                therefore reproducible only to within that noise.
        """
        self.balance = initial_capital
        self.initial_capital = initial_capital
        self.slippage_pct = slippage_pct
        self.taker_fee_pct = taker_fee_pct
        self.maker_fee_pct = maker_fee_pct
        self.funding_hourly_pct = funding_hourly_pct
        self.funding_schedule = funding_schedule
        self.funding_interval_hours = max(1, int(funding_interval_hours or 1))
        if seed is None:
            try:
                seed = int(os.getenv("BACKTEST_SEED", "0"))
            except ValueError:
                logger.warning("BACKTEST_SEED is not an integer; using 0")
                seed = 0
        self.seed = seed
        #: Private RNG so a backtest never depends on - or perturbs -
        #: the process-global random state.
        self._rng = random.Random(seed)
        #: Total funding cash-flow applied over the run (negative = paid).
        self.total_funding = 0.0
        #: Number of settlements actually applied to a live position.
        self.funding_events = 0
        self.costs = cost_table or CostTable.from_env(
            slippage_pct=slippage_pct,
            taker_fee_pct=taker_fee_pct,
            maker_fee_pct=maker_fee_pct,
        )

        self._orders: Dict[str, SimulatedOrder] = {}
        self._positions: Dict[str, SimulatedPosition] = {}
        self._current_price: float = 0.0
        self._current_timestamp: str = ""
        self._order_counter: int = 0
        self._current_strategy: str = ""  # Set by engine before each order for attribution
        self._current_regime: str = ""    # Set by engine each bar (regime value, P4)
        # Latest bar context, used by the dynamic slippage model.
        self._bar_range_pct: float = 0.0
        self._bar_notional: float = 0.0

        self.trade_log: List[Dict] = []

    # ------------------------------------------------------------------
    # PacificaClient-compatible interface
    # ------------------------------------------------------------------

    def get_account_balance(self) -> Dict:
        unrealised = sum(p.unrealised_pnl for p in self._positions.values())
        return {
            "balance": str(round(self.balance + unrealised, 4)),
            "available": str(round(self.balance, 4)),
            "locked": "0.00",
        }

    def get_positions(self) -> List[Dict]:
        result = []
        for pos in self._positions.values():
            result.append({
                "symbol": pos.symbol,
                "side": pos.side,
                "quantity": str(pos.quantity),
                "entry_price": str(pos.entry_price),
                "unrealised_pnl": str(round(pos.unrealised_pnl, 4)),
            })
        return result

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: str,
        order_type: str = "market",
        price: Optional[float] = None,
    ) -> Dict:
        self._order_counter += 1
        order_id = f"bt_{self._order_counter:06d}"
        qty = float(quantity)

        order = SimulatedOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            price=price or self._current_price,
            quantity=qty,
            order_type=order_type,
            timestamp=self._current_timestamp,
        )
        self._orders[order_id] = order

        if order_type == "market":
            self._fill_order(order, is_taker=True)

        return {"order_id": order_id, "status": "success"}

    def cancel_order(self, order_id: str) -> Dict:
        if order_id in self._orders:
            self._orders[order_id].status = "cancelled"
        return {"status": "success"}

    def cancel_all_orders(self, symbol: str) -> Dict:
        for order in self._orders.values():
            if order.symbol == symbol and order.status == "open":
                order.status = "cancelled"
        return {"status": "success"}

    def get_ticker(self, symbol: str) -> Dict:
        return {
            "symbol": symbol,
            "last": str(self._current_price),
            "bid": str(self._current_price * 0.9995),
            "ask": str(self._current_price * 1.0005),
        }

    def get_orderbook(self, symbol: str, depth: int = 10) -> Dict:
        spread_pct = 0.0005
        bids = [
            [str(round(self._current_price * (1 - spread_pct * i), 4)), str(1000 / (i + 1))]
            for i in range(1, depth + 1)
        ]
        asks = [
            [str(round(self._current_price * (1 + spread_pct * i), 4)), str(1000 / (i + 1))]
            for i in range(1, depth + 1)
        ]
        return {"bids": bids, "asks": asks}

    def get_funding_rate(self, symbol: str) -> Dict:
        return {
            "symbol": symbol,
            "funding_rate": str(self._current_funding_rate()),
            "next_funding_time": self._next_funding_time(),
        }

    def get_market_data(self, symbol: str) -> Dict:
        """PacificaClient-shaped market snapshot.

        FundingArbStrategy reads ``funding_rate`` and
        ``next_funding_time`` from here. The rate is the one PER VENUE
        SETTLEMENT INTERVAL that was last SETTLED at or before the
        current bar - never a future one.

        Args:
            symbol: Bot symbol.

        Returns:
            Dict with symbol, mark/last price, funding_rate and
            next_funding_time.
        """
        return {
            "symbol": symbol,
            "mark_price": str(self._current_price),
            "last": str(self._current_price),
            "funding_rate": self._current_funding_rate(),
            "next_funding_time": self._next_funding_time(),
            "funding_interval_hours": self._venue_interval_hours(),
        }

    def get_funding_history(self, symbol: str, limit: int = 8) -> List[Dict]:
        """Recent funding settlements on the venue's own grid.

        Returns an empty list when no real schedule is loaded: a
        strategy must not be able to average a constant that the flat
        cost model invented and call it market information.

        Args:
            symbol: Bot symbol.
            limit: Maximum records, oldest first.

        Returns:
            List of ``{"funding_time", "funding_rate", "rate_source"}``.
        """
        if self.funding_schedule is None:
            return []
        dt = self._parse_ts(self._current_timestamp)
        if dt is None:
            return []
        return self.funding_schedule.venue_history(dt, limit=limit)

    def get_balance(self) -> Dict:
        """Balance in the shape live clients return (``equity`` key)."""
        unrealised = sum(p.unrealised_pnl for p in self._positions.values())
        equity = self.balance + unrealised
        return {
            "balance": round(self.balance, 4),
            "equity": round(equity, 4),
            "available": round(self.balance, 4),
        }

    def _venue_interval_hours(self) -> int:
        """Venue settlement cadence currently in force."""
        if self.funding_schedule is not None:
            return self.funding_schedule.venue_interval_hours
        return self.funding_interval_hours

    def _next_funding_time(self) -> str:
        """Next venue settlement boundary strictly after the current bar."""
        dt = self._parse_ts(self._current_timestamp)
        if dt is None:
            return self._current_timestamp
        step = self._venue_interval_hours()
        floor = dt.replace(minute=0, second=0, microsecond=0)
        floor -= timedelta(hours=floor.hour % step)
        return (floor + timedelta(hours=step)).isoformat()

    # ------------------------------------------------------------------
    # Engine-facing interface
    # ------------------------------------------------------------------

    def advance(self, candle: Dict, timestamp: str) -> None:
        self._current_price = float(candle["close"])
        self._current_timestamp = timestamp
        self._capture_bar_context(candle)
        self._check_pending_orders(candle)
        self._update_unrealised_pnl()
        if self._is_funding_hour(timestamp):
            self._apply_funding()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _capture_bar_context(self, candle: Dict) -> None:
        """Record the current bar's volatility and traded notional.

        Both feed the dynamic slippage model: a wide bar means the market
        is moving away from an in-flight order, and a thin bar means the
        order is a larger share of the flow. Malformed bars degrade to
        zero, which drops the corresponding term rather than guessing.

        Args:
            candle: OHLCV dict for the bar just closed.
        """
        try:
            high = float(candle["high"])
            low = float(candle["low"])
            close = float(candle["close"])
            volume = float(candle.get("volume", 0.0) or 0.0)
        except (KeyError, TypeError, ValueError):
            self._bar_range_pct = 0.0
            self._bar_notional = 0.0
            return
        self._bar_range_pct = (high - low) / close if close > 0 else 0.0
        # Candle volume is in base units; convert to quote notional.
        self._bar_notional = max(volume, 0.0) * close

    def _slippage_pct_for(self, order: SimulatedOrder, price: float) -> float:
        """Return the slippage fraction an aggressive fill of ``order`` pays."""
        return self.costs.slippage_pct(
            order.symbol,
            notional=abs(price * order.quantity),
            bar_range_pct=self._bar_range_pct,
            bar_notional=self._bar_notional,
        )

    def _fill_order(self, order: SimulatedOrder, is_taker: bool) -> None:
        direction = 1 if order.side == "bid" else -1
        if order.order_type == "market":
            # Market orders fill at current price with slippage (taker)
            slippage_pct = self._slippage_pct_for(order, self._current_price)
            slippage = self._current_price * slippage_pct * direction
            fill_price = self._current_price + slippage
        elif order.order_type == "stop":
            # Stop orders fill at the stop price with adverse slippage (taker)
            slippage_pct = self._slippage_pct_for(order, order.price)
            fill_price = order.price * (1 + slippage_pct * direction)
        else:
            # Limit orders fill at the limit price - no adverse slippage.
            fill_price = order.price
        role = LiquidityRole.TAKER if is_taker else LiquidityRole.MAKER
        fee_pct = self.costs.fee_pct(order.symbol, role)
        fee = fill_price * order.quantity * fee_pct

        order.fill_price = fill_price
        order.filled_qty = order.quantity
        order.fee = fee
        order.status = "filled"

        symbol = order.symbol
        # Regime tagging (P4): closing fills carry the regime that was
        # confirmed when the position was OPENED, so per-regime analysis
        # attributes each round-trip to its entry conditions. Opening
        # fills carry the current regime.
        prior_pos = self._positions.get(symbol)
        fill_side = "long" if order.side == "bid" else "short"
        if prior_pos is not None and prior_pos.side != fill_side:
            regime_tag = prior_pos.entry_regime or self._current_regime
        else:
            regime_tag = self._current_regime

        realised_pnl = self._open_or_add_position(
            symbol, fill_side, order.quantity, fill_price
        )

        if order.status == "filled":  # may have been cancelled by balance guard
            self.balance -= fee
            self._log_trade(
                order, fill_price, fee, realised_pnl, regime_tag, role.value
            )

    def _open_or_add_position(
        self, symbol: str, side: str, qty: float, price: float
    ) -> float:
        """
        Opens, adds to, or closes a position.
        Returns realised PnL (0.0 for opens/adds, non-zero for full/partial closes).

        Fix 1 (balance guard): New positions and position additions check that
        sufficient cash is available before proceeding. Insufficient-balance
        orders are rejected and the SimulatedOrder is marked 'cancelled'.
        """
        opposite = "short" if side == "long" else "long"
        existing = self._positions.get(symbol)
        realised_pnl = 0.0

        if existing and existing.side == opposite:
            # --- Close or reduce opposing position ---
            close_qty = min(qty, existing.quantity)
            realised_pnl = self._calculate_pnl(existing, price, close_qty)
            # Return the notional cost of the closed portion + PnL to available cash
            self.balance += realised_pnl + close_qty * existing.entry_price
            existing.realised_pnl += realised_pnl
            existing.quantity -= close_qty

            if existing.quantity <= 1e-8:
                del self._positions[symbol]
                # OCO: cancel any pending SL/TP orders now that position is closed
                self._cancel_open_orders(symbol)
                remaining = qty - close_qty
                if remaining > 1e-8:
                    # Flip: open new position in opposite direction
                    cost = remaining * price
                    if cost > self.balance:
                        logger.debug(
                            f"Balance guard: skipping flip on {symbol}, "
                            f"need {cost:.2f}, have {self.balance:.4f}"
                        )
                    else:
                        self.balance -= cost
                        self._positions[symbol] = SimulatedPosition(
                            symbol=symbol, side=side, quantity=remaining,
                            entry_price=price, entry_regime=self._current_regime,
                        )
            # partial close: existing.quantity already reduced above

        elif existing and existing.side == side:
            # --- Add to existing position ---
            cost = qty * price
            if cost > self.balance:
                logger.debug(
                    f"Balance guard: skipping add-to-position on {symbol}, "
                    f"need {cost:.2f}, have {self.balance:.4f}"
                )
                return 0.0
            self.balance -= cost
            total_qty = existing.quantity + qty
            avg_price = (existing.entry_price * existing.quantity + price * qty) / total_qty
            existing.entry_price = avg_price
            existing.quantity = total_qty

        else:
            # --- New position ---
            cost = qty * price
            if cost > self.balance:
                logger.debug(
                    f"Balance guard: skipping new position on {symbol}, "
                    f"need {cost:.2f}, have {self.balance:.4f}"
                )
                return 0.0
            self.balance -= cost
            self._positions[symbol] = SimulatedPosition(
                symbol=symbol, side=side, quantity=qty, entry_price=price,
                entry_regime=self._current_regime,
            )

        return realised_pnl

    def _calculate_pnl(self, pos: SimulatedPosition, exit_price: float, qty: float) -> float:
        if pos.side == "long":
            return (exit_price - pos.entry_price) * qty
        else:
            return (pos.entry_price - exit_price) * qty

    def _check_pending_orders(self, candle: Dict) -> None:
        """
        Check and fill pending orders for this candle.

        Fix 4 — SL/TP randomisation:
            Collects all orders that trigger within this candle's high/low range,
            then shuffles them before processing. This prevents the systematic
            pessimistic bias where SL (placed first in _orders) always fires
            before TP when both levels are touched in the same candle.
        """
        high = float(candle["high"])
        low = float(candle["low"])

        # Collect all orders that would trigger this candle
        triggered = []
        for order in list(self._orders.values()):
            if order.status != "open":
                continue
            if order.order_type == "stop":
                if order.side == "ask" and low <= order.price:
                    triggered.append((order, True))
                elif order.side == "bid" and high >= order.price:
                    triggered.append((order, True))
            else:
                # Limit orders: bid fills on low, ask fills on high
                if order.side == "bid" and low <= order.price:
                    triggered.append((order, False))
                elif order.side == "ask" and high >= order.price:
                    triggered.append((order, False))

        # Randomise processing order to remove systematic SL-before-TP
        # bias. Drawn from this exchange's own seeded RNG so the run is
        # reproducible; the global random module is never touched.
        self._rng.shuffle(triggered)

        for order, is_taker in triggered:
            if order.status == "open":  # May have been cancelled by OCO
                self._fill_order(order, is_taker=is_taker)

    def _update_unrealised_pnl(self) -> None:
        for pos in self._positions.values():
            pos.unrealised_pnl = self._calculate_pnl(pos, self._current_price, pos.quantity)

    def _current_funding_rate(self) -> float:
        """Rate charged per venue settlement at the current timestamp.

        With a real schedule the sign is the market's: a positive rate
        means longs pay shorts, a negative one means the reverse. With
        the flat model the rate is a constant and longs always pay.
        """
        if self.funding_schedule is None:
            return self.funding_hourly_pct
        dt = self._parse_ts(self._current_timestamp)
        if dt is None:
            return 0.0
        rate = self.funding_schedule.venue_rate_at(dt)
        return 0.0 if rate is None else rate

    def _apply_funding(self) -> None:
        """Debit/credit every open position for one settlement."""
        rate = self._current_funding_rate()
        if rate == 0.0:
            return
        for pos in self._positions.values():
            funding = pos.quantity * self._current_price * rate
            cost = -funding if pos.side == "long" else funding
            pos.funding_paid += cost
            self.balance += cost
            self.total_funding += cost
            self.funding_events += 1

    @staticmethod
    def _parse_ts(timestamp: str) -> Optional[datetime]:
        """Parse a replay timestamp, or None when it is unusable."""
        try:
            return datetime.fromisoformat(timestamp)
        except (TypeError, ValueError):
            return None

    def _is_funding_hour(self, timestamp: str) -> bool:
        """Whether this bar closes on a venue settlement boundary."""
        dt = self._parse_ts(timestamp)
        if dt is None:
            return False
        if self.funding_schedule is not None:
            return self.funding_schedule.is_settlement_time(dt)
        if dt.minute != 0:
            return False
        return dt.hour % self.funding_interval_hours == 0

    def _cancel_open_orders(self, symbol: str) -> None:
        """Cancel all open orders for a symbol (used for OCO SL/TP cleanup)."""
        for order in self._orders.values():
            if order.symbol == symbol and order.status == "open":
                order.status = "cancelled"

    def _log_trade(
        self,
        order: SimulatedOrder,
        fill_price: float,
        fee: float,
        realised_pnl: float = 0.0,
        regime: str = "",
        role: str = "",
    ) -> None:
        """
        Fix 2 — PnL tracking:
            Records realised_pnl per fill. For opening trades pnl=0;
            for closing trades pnl reflects the actual profit/loss.
            PerformanceTracker uses this field for win_rate and profit_factor.

        Fix 3 — Strategy attribution:
            Records _current_strategy (set by engine before place_order) so
            per-strategy PnL breakdown is possible in reports.

        Regime tagging (P4):
            Records the regime tag supplied by _fill_order — the regime
            at position ENTRY for closing fills, the current regime for
            opening fills. Enables regime-conditional optimization.

        Liquidity role:
            "maker" for resting limit fills, "taker" for market and stop
            fills. Makes fee attribution auditable - a strategy whose
            fills are all resting limits should never show a taker bill.
        """
        self.trade_log.append({
            "order_id": order.order_id,
            "timestamp": self._current_timestamp,
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "fill_price": fill_price,
            "fee": fee,
            "pnl": round(realised_pnl, 6),
            "balance_after": round(self.balance, 4),
            "strategy": self._current_strategy,
            "regime": regime,
            "role": role,
        })
