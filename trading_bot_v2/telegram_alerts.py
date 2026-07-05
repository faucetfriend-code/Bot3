"""
Telegram Alerts Module for Bot 3 Trading System.

Provides async Telegram notifications for trade executions, errors,
and daily summaries.

Usage:
    from trading_bot_v2.telegram_alerts import telegram_alerts

    # Send trade alert
    await telegram_alerts.send_trade_alert(
        strategy="mean_reversion",
        symbol="BTC-USDC",
        side="long",
        entry_price=50000.0,
        quantity=0.001
    )

    # Send error alert
    await telegram_alerts.send_error_alert("exchange_timeout", "Connection timed out")

    # Send daily summary
    await telegram_alerts.send_daily_summary(
        total_trades=15,
        winning_trades=9,
        total_pnl=250.50
    )
"""

import os
import asyncio
from typing import Optional, Dict, Any
from datetime import datetime
import httpx
from loguru import logger

from .event_system import Event, EventType


class TelegramAlerts:
    """Telegram notification system for trading bot."""

    def __init__(self):
        """Initialize Telegram alerts from environment variables."""
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.bot_token and self.chat_id)

        if self.enabled:
            self.api_url = f"https://api.telegram.org/bot{self.bot_token}"
            logger.info("Telegram alerts enabled")
        else:
            logger.warning("Telegram alerts disabled (missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)")

    async def _send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        Send a message to Telegram.

        Args:
            text: Message text (HTML or Markdown)
            parse_mode: Parse mode (HTML or Markdown)

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.enabled:
            return False

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": parse_mode
                    },
                    timeout=10.0
                )

                if response.status_code == 200:
                    logger.debug("Telegram message sent successfully")
                    return True
                else:
                    logger.error(f"Telegram API error: {response.status_code} - {response.text}")
                    return False

        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def send_trade_alert(
        self,
        strategy: str,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        confidence: Optional[float] = None
    ) -> bool:
        """
        Send trade execution alert.

        Args:
            strategy: Strategy name
            symbol: Trading pair
            side: Trade side (long/short)
            entry_price: Entry price
            quantity: Position size
            stop_loss: Stop loss price (optional)
            take_profit: Take profit price (optional)
            confidence: Signal confidence (optional)

        Returns:
            True if sent successfully
        """
        emoji = "🟢" if side.lower() == "long" else "🔴"
        side_emoji = "📈" if side.lower() == "long" else "📉"

        text = f"""
{emoji} <b>Trade Executed</b> {side_emoji}

<b>Strategy:</b> {strategy}
<b>Symbol:</b> {symbol}
<b>Side:</b> {side.upper()}
<b>Entry:</b> ${entry_price:,.2f}
<b>Quantity:</b> {quantity:.6f}
"""

        if stop_loss:
            text += f"<b>Stop Loss:</b> ${stop_loss:,.2f}\n"
        if take_profit:
            text += f"<b>Take Profit:</b> ${take_profit:,.2f}\n"
        if confidence:
            text += f"<b>Confidence:</b> {confidence:.2%}\n"

        text += f"\n<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"

        return await self._send_message(text)

    async def send_error_alert(
        self,
        error_type: str,
        error_message: str,
        context: Optional[str] = None
    ) -> bool:
        """
        Send error alert.

        Args:
            error_type: Error type (e.g., exchange_timeout, risk_violation)
            error_message: Error description
            context: Additional context (optional)

        Returns:
            True if sent successfully
        """
        text = f"""
🚨 <b>Error Alert</b>

<b>Type:</b> {error_type}
<b>Message:</b> {error_message}
"""

        if context:
            text += f"<b>Context:</b> {context}\n"

        text += f"\n<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"

        return await self._send_message(text)

    async def send_daily_summary(
        self,
        total_trades: int,
        winning_trades: int,
        total_pnl: float,
        open_positions: int = 0,
        balance: float = 0.0
    ) -> bool:
        """
        Send daily trading summary.

        Args:
            total_trades: Total trades executed today
            winning_trades: Winning trades count
            total_pnl: Total P&L in USD
            open_positions: Number of open positions
            balance: Current account balance

        Returns:
            True if sent successfully
        """
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        pnl_emoji = "💰" if total_pnl >= 0 else "💸"

        text = f"""
📊 <b>Daily Trading Summary</b>

<b>Total Trades:</b> {total_trades}
<b>Winning Trades:</b> {winning_trades}
<b>Win Rate:</b> {win_rate:.1f}%
<b>Total P&L:</b> {pnl_emoji} ${total_pnl:,.2f}

<b>Open Positions:</b> {open_positions}
<b>Account Balance:</b> ${balance:,.2f}

<i>{datetime.now().strftime('%Y-%m-%d')}</i>
"""

        return await self._send_message(text)

    async def send_circuit_breaker_alert(
        self,
        loss_percent: float,
        threshold_percent: float
    ) -> bool:
        """
        Send circuit breaker triggered alert.

        Args:
            loss_percent: Current loss percentage
            threshold_percent: Circuit breaker threshold

        Returns:
            True if sent successfully
        """
        text = f"""
⚠️ <b>Circuit Breaker Triggered</b>

<b>Current Loss:</b> {loss_percent:.2f}%
<b>Threshold:</b> {threshold_percent:.2f}%
<b>Status:</b> Trading halted

<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>
"""

        return await self._send_message(text)

    async def send_regime_change_alert(
        self,
        old_regime: str,
        new_regime: str,
        affected_strategies: list
    ) -> bool:
        """
        Send market regime change alert.

        Args:
            old_regime: Previous market regime
            new_regime: New market regime
            affected_strategies: List of affected strategies

        Returns:
            True if sent successfully
        """
        strategies_text = ", ".join(affected_strategies) if affected_strategies else "None"

        text = f"""
🔄 <b>Market Regime Change</b>

<b>From:</b> {old_regime}
<b>To:</b> {new_regime}
<b>Affected Strategies:</b> {strategies_text}

<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>
"""

        return await self._send_message(text)

    async def _handle_event(self, event: Event) -> None:
        """
        Handle EventBus events and dispatch to appropriate alert methods.

        Designed as an async callback for the EventBus integration. Dispatches
        based on event type to the relevant Telegram notification method.

        Args:
            event: Event object from the EventBus
        """
        try:
            if event.event_type == EventType.SIGNAL_EXECUTED:
                data = event.data if isinstance(event.data, dict) else {}
                await self.send_trade_alert(
                    strategy=data.get("strategy", "unknown"),
                    symbol=data.get("symbol", "unknown"),
                    side=data.get("side", "unknown"),
                    entry_price=float(data.get("entry_price", 0)),
                    quantity=float(data.get("quantity", 0)),
                    stop_loss=data.get("stop_loss"),
                    take_profit=data.get("take_profit"),
                    confidence=data.get("confidence"),
                )

            elif event.event_type == EventType.RISK_LIMIT_EXCEEDED:
                data = event.data if isinstance(event.data, dict) else {}
                await self.send_error_alert(
                    error_type="risk_limit_exceeded",
                    error_message=data.get(
                        "message",
                        f"Risk limit exceeded: {data.get('risk_type', 'unknown')} "
                        f"({data.get('current_value', '?')}/{data.get('limit', '?')})",
                    ),
                    context=f"Source: {event.source}",
                )

            elif event.event_type == EventType.REGIME_CHANGED:
                data = event.data if isinstance(event.data, dict) else {}
                old_regime = data.get("old_regime", data.get("previous_regime", "unknown"))
                new_regime = data.get("new_regime", data.get("current_regime", "unknown"))
                affected = data.get("affected_strategies", [])
                await self.send_regime_change_alert(
                    old_regime=str(old_regime),
                    new_regime=str(new_regime),
                    affected_strategies=affected if isinstance(affected, list) else [],
                )

            elif event.event_type == EventType.COMPONENT_FAILURE:
                data = event.data if isinstance(event.data, dict) else {}
                await self.send_error_alert(
                    error_type="component_failure",
                    error_message=data.get(
                        "error",
                        data.get("message", "Unknown component failure"),
                    ),
                    context=(
                        f"Component: {data.get('component', event.source)}, "
                        f"Event source: {event.source}"
                    ),
                )

            elif event.event_type == EventType.POSITION_DISCREPANCY:
                data = event.data if isinstance(event.data, dict) else {}
                await self.send_error_alert(
                    error_type="position_discrepancy",
                    error_message=data.get(
                        "message",
                        f"Position discrepancy detected for "
                        f"{data.get('symbol', 'unknown')}: "
                        f"{data.get('discrepancy_count', 0)} discrepancy(ies)",
                    ),
                    context=f"Source: {event.source}",
                )

            else:
                logger.debug(
                    f"Telegram alerts: unhandled event type {event.event_type.value}"
                )

        except Exception as e:
            logger.error(
                f"Error in telegram event handler for {event.event_type.value}: {e}"
            )

    def _handle_event_sync(self, event: Event) -> None:
        """
        Synchronous wrapper for EventBus callback compatibility.

        The EventBus invokes callbacks synchronously, but Telegram sends require
        async I/O. This bridge schedules the async handler via asyncio.run().

        Args:
            event: Event object from the EventBus
        """
        try:
            asyncio.run(self._handle_event(event))
        except Exception as e:
            logger.error(
                f"Failed to execute telegram event handler for "
                f"{event.event_type.value}: {e}"
            )


# Global Telegram alerts instance
telegram_alerts = TelegramAlerts()
