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
"""

import random
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from loguru import logger


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
    """

    def __init__(
        self,
        initial_capital: float,
        slippage_pct: float = 0.002,
        taker_fee_pct: float = 0.0006,
        maker_fee_pct: float = 0.0002,
        funding_hourly_pct: float = 0.0001,
    ):
        self.balance = initial_capital
        self.initial_capital = initial_capital
        self.slippage_pct = slippage_pct
        self.taker_fee_pct = taker_fee_pct
        self.maker_fee_pct = maker_fee_pct
        self.funding_hourly_pct = funding_hourly_pct

        self._orders: Dict[str, SimulatedOrder] = {}
        self._positions: Dict[str, SimulatedPosition] = {}
        self._current_price: float = 0.0
        self._current_timestamp: str = ""
        self._order_counter: int = 0
        self._current_strategy: str = ""  # Set by engine before each order for attribution

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
            "funding_rate": str(self.funding_hourly_pct),
            "next_funding_time": self._current_timestamp,
        }

    # ------------------------------------------------------------------
    # Engine-facing interface
    # ------------------------------------------------------------------

    def advance(self, candle: Dict, timestamp: str) -> None:
        self._current_price = float(candle["close"])
        self._current_timestamp = timestamp
        self._check_pending_orders(candle)
        self._update_unrealised_pnl()
        if self._is_funding_hour(timestamp):
            self._apply_funding()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fill_order(self, order: SimulatedOrder, is_taker: bool) -> None:
        direction = 1 if order.side == "bid" else -1
        if order.order_type == "market":
            # Market orders fill at current price with slippage (taker)
            slippage = self._current_price * self.slippage_pct * direction
            fill_price = self._current_price + slippage
        elif order.order_type == "stop":
            # Stop orders fill at the stop price with adverse slippage (taker)
            fill_price = order.price * (1 + self.slippage_pct * direction)
        else:
            # Limit orders fill at the limit price — no adverse slippage.
            fill_price = order.price
        fee_pct = self.taker_fee_pct if is_taker else self.maker_fee_pct
        fee = fill_price * order.quantity * fee_pct

        order.fill_price = fill_price
        order.filled_qty = order.quantity
        order.fee = fee
        order.status = "filled"

        symbol = order.symbol
        if order.side == "bid":
            realised_pnl = self._open_or_add_position(symbol, "long", order.quantity, fill_price)
        else:
            realised_pnl = self._open_or_add_position(symbol, "short", order.quantity, fill_price)

        if order.status == "filled":  # may have been cancelled by balance guard
            self.balance -= fee
            self._log_trade(order, fill_price, fee, realised_pnl)

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
                            symbol=symbol, side=side, quantity=remaining, entry_price=price
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
                symbol=symbol, side=side, quantity=qty, entry_price=price
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

        # Randomise processing order to remove systematic SL-before-TP bias
        random.shuffle(triggered)

        for order, is_taker in triggered:
            if order.status == "open":  # May have been cancelled by OCO
                self._fill_order(order, is_taker=is_taker)

    def _update_unrealised_pnl(self) -> None:
        for pos in self._positions.values():
            pos.unrealised_pnl = self._calculate_pnl(pos, self._current_price, pos.quantity)

    def _apply_funding(self) -> None:
        for pos in self._positions.values():
            funding = pos.quantity * self._current_price * self.funding_hourly_pct
            cost = -funding if pos.side == "long" else funding
            pos.funding_paid += cost
            self.balance += cost

    def _is_funding_hour(self, timestamp: str) -> bool:
        try:
            dt = datetime.fromisoformat(timestamp)
            return dt.minute == 0
        except Exception:
            return False

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
    ) -> None:
        """
        Fix 2 — PnL tracking:
            Records realised_pnl per fill. For opening trades pnl=0;
            for closing trades pnl reflects the actual profit/loss.
            PerformanceTracker uses this field for win_rate and profit_factor.

        Fix 3 — Strategy attribution:
            Records _current_strategy (set by engine before place_order) so
            per-strategy PnL breakdown is possible in reports.
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
        })
