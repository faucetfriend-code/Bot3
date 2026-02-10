#!/usr/bin/env python3
"""
Pacifica.fi Hourly Funding Rate Tracker

Standalone service for tracking and managing hourly funding payments.

⚠️ CRITICAL DIFFERENCES FROM STANDARD EXCHANGES:
- Standard exchanges: 3 funding payments per day (every 8 hours)
- Pacifica.fi: 24 funding payments per day (EVERY HOUR)
- Same rate = 8x MORE funding cost on Pacifica!

Features:
- Real-time funding rate monitoring (updates every 5 seconds)
- Hourly funding payment application (24x per day)
- Cumulative funding cost tracking
- Funding cost alerts and warnings
- Funding analytics and reports
- Database integration for persistent tracking
"""

import asyncio
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum
import time
from loguru import logger

from database import DatabaseManager
from models import Position
from risk import calculate_pacifica_funding_cost


class FundingAlertLevel(Enum):
    """Funding cost alert levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class FundingRateSnapshot:
    """Snapshot of funding rate for a market."""
    symbol: str
    funding_rate: float  # Hourly rate
    premium_index: Optional[float] = None
    next_payment_time: Optional[datetime] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

    @property
    def daily_rate(self) -> float:
        """Calculate daily funding rate (24x hourly rate)."""
        return self.funding_rate * 24

    @property
    def annual_rate(self) -> float:
        """Calculate annualized funding rate."""
        return self.daily_rate * 365


@dataclass
class FundingPayment:
    """Record of a single funding payment."""
    position_id: str
    symbol: str
    funding_rate: float
    payment_amount: float
    position_value: float
    margin_mode: str
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database storage."""
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "funding_rate": self.funding_rate,
            "payment_amount": self.payment_amount,
            "position_value": self.position_value,
            "margin_mode": self.margin_mode,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class FundingSummary:
    """Summary of funding costs for a position."""
    position_id: str
    symbol: str
    payment_count: int
    total_paid: float
    avg_rate: float
    min_rate: float
    max_rate: float
    hours_elapsed: float
    daily_cost_estimate: float
    margin_percentage: float  # Funding as % of margin

    @property
    def hourly_cost_avg(self) -> float:
        """Average hourly funding cost."""
        return self.total_paid / self.hours_elapsed if self.hours_elapsed > 0 else 0


class FundingTracker:
    """
    Hourly funding rate tracker for Pacifica.fi.

    ⚠️ CRITICAL: Manages HOURLY funding payments (24x per day)
    """

    def __init__(
        self,
        db: DatabaseManager,
        alert_threshold_warning: float = 0.10,  # 10% of margin
        alert_threshold_critical: float = 0.25,  # 25% of margin
    ):
        """
        Initialize funding tracker.

        Args:
            db: Database manager
            alert_threshold_warning: Warning threshold (funding / margin)
            alert_threshold_critical: Critical threshold (funding / margin)
        """
        self.db = db
        self.alert_threshold_warning = alert_threshold_warning
        self.alert_threshold_critical = alert_threshold_critical

        # Current funding rates (updated every 5 seconds via WebSocket)
        self.current_rates: Dict[str, FundingRateSnapshot] = {}

        # Tracked positions
        self.tracked_positions: Dict[str, Position] = {}

        # Alert callbacks
        self.alert_callbacks: List[Callable[[FundingAlertLevel, str, Dict], None]] = []

        # Statistics
        self.total_payments_processed = 0
        self.last_payment_time: Optional[datetime] = None

        # Background tasks
        self._funding_task: Optional[asyncio.Task] = None
        self._monitoring_task: Optional[asyncio.Task] = None
        self.running = False

        logger.info("Initialized Pacifica Funding Tracker (HOURLY payments)")

    def register_alert_callback(
        self, callback: Callable[[FundingAlertLevel, str, Dict], None]
    ):
        """
        Register callback for funding alerts.

        Args:
            callback: Function called with (level, message, data)
        """
        self.alert_callbacks.append(callback)
        logger.info(f"Registered funding alert callback: {callback.__name__}")

    def update_funding_rate(
        self,
        symbol: str,
        funding_rate: float,
        premium_index: Optional[float] = None,
        next_payment_time: Optional[datetime] = None,
    ):
        """
        Update current funding rate for a market.

        ⚠️ Called every 5 seconds from WebSocket stream.

        Args:
            symbol: Market symbol
            funding_rate: Hourly funding rate
            premium_index: Premium index component
            next_payment_time: Next payment timestamp
        """
        snapshot = FundingRateSnapshot(
            symbol=symbol,
            funding_rate=funding_rate,
            premium_index=premium_index,
            next_payment_time=next_payment_time,
        )

        self.current_rates[symbol] = snapshot

        # Save to database
        try:
            self.db.execute(
                """
                INSERT INTO funding_rate_history
                (symbol, funding_rate, premium_index, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (symbol, funding_rate, premium_index, datetime.now()),
            )
        except Exception as e:
            logger.error(f"Failed to save funding rate: {e}")

        logger.debug(
            f"💰 {symbol}: {funding_rate*100:.4f}% per hour "
            f"({funding_rate*24*100:.4f}% per day)"
        )

    def track_position(self, position: Position):
        """
        Start tracking a position for funding payments.

        Args:
            position: Position to track
        """
        self.tracked_positions[position.position_id] = position
        logger.info(
            f"📊 Tracking position {position.position_id} ({position.symbol}) "
            f"for hourly funding"
        )

    def untrack_position(self, position_id: str):
        """
        Stop tracking a position.

        Args:
            position_id: Position ID to untrack
        """
        if position_id in self.tracked_positions:
            del self.tracked_positions[position_id]
            logger.info(f"Stopped tracking position {position_id}")

    async def apply_hourly_funding(self) -> List[FundingPayment]:
        """
        Apply hourly funding to all tracked positions.

        ⚠️ CRITICAL: Called EVERY HOUR (24x per day)

        Returns:
            List of funding payments applied
        """
        payments = []
        timestamp = datetime.now()

        logger.info(
            f"⏰ Applying HOURLY funding to {len(self.tracked_positions)} positions..."
        )

        for position_id, position in self.tracked_positions.items():
            try:
                # Get current funding rate
                funding_snapshot = self.current_rates.get(position.symbol)
                if not funding_snapshot:
                    logger.warning(
                        f"No funding rate available for {position.symbol}, skipping"
                    )
                    continue

                funding_rate = funding_snapshot.funding_rate
                position_value = position.position_value

                # Calculate payment
                payment_amount = position_value * funding_rate

                # Apply to position
                position.apply_hourly_funding_payment(funding_rate)

                # Create payment record
                payment = FundingPayment(
                    position_id=position_id,
                    symbol=position.symbol,
                    funding_rate=funding_rate,
                    payment_amount=payment_amount,
                    position_value=position_value,
                    margin_mode=position.margin_mode,
                    timestamp=timestamp,
                )

                payments.append(payment)

                # Save to database
                self.db.save_funding_payment(
                    position_id=position_id,
                    symbol=position.symbol,
                    funding_rate=funding_rate,
                    payment_amount=payment_amount,
                    position_value=position_value,
                    margin_mode=position.margin_mode,
                    timestamp=timestamp,
                )

                logger.info(
                    f"💸 {position.symbol}: Paid ${abs(payment_amount):.4f} "
                    f"(Rate: {funding_rate*100:.4f}% per hour)"
                )

                # Update position in database
                self.db.update_pacifica_position_funding(
                    position_id=position_id,
                    cumulative_funding_paid=position.cumulative_funding_paid,
                    last_funding_timestamp=timestamp,
                )

                # Check for alerts
                await self._check_funding_alerts(position, funding_rate)

            except Exception as e:
                logger.error(f"Error applying funding to {position_id}: {e}")

        self.total_payments_processed += len(payments)
        self.last_payment_time = timestamp

        logger.info(f"✅ Applied {len(payments)} hourly funding payments")
        return payments

    async def _check_funding_alerts(self, position: Position, funding_rate: float):
        """
        Check if funding costs exceed alert thresholds.

        Args:
            position: Position to check
            funding_rate: Current funding rate
        """
        # Calculate funding as percentage of margin
        margin_used = position.margin_used
        if margin_used <= 0:
            return

        cumulative_funding = abs(position.cumulative_funding_paid)
        funding_percentage = cumulative_funding / margin_used

        # Calculate daily cost estimate
        daily_cost = abs(position.position_value * funding_rate * 24)
        daily_percentage = daily_cost / margin_used

        # Check thresholds
        if funding_percentage >= self.alert_threshold_critical:
            await self._trigger_alert(
                FundingAlertLevel.CRITICAL,
                f"🚨 CRITICAL: {position.symbol} funding costs at "
                f"{funding_percentage*100:.1f}% of margin!",
                {
                    "position_id": position.position_id,
                    "symbol": position.symbol,
                    "cumulative_funding": cumulative_funding,
                    "margin_used": margin_used,
                    "funding_percentage": funding_percentage,
                    "daily_cost": daily_cost,
                    "current_rate": funding_rate,
                },
            )
        elif funding_percentage >= self.alert_threshold_warning:
            await self._trigger_alert(
                FundingAlertLevel.WARNING,
                f"⚠️ WARNING: {position.symbol} funding costs at "
                f"{funding_percentage*100:.1f}% of margin",
                {
                    "position_id": position.position_id,
                    "symbol": position.symbol,
                    "cumulative_funding": cumulative_funding,
                    "margin_used": margin_used,
                    "funding_percentage": funding_percentage,
                    "daily_cost": daily_cost,
                    "current_rate": funding_rate,
                },
            )

        # Check for extreme daily costs (>10% of margin per day)
        if daily_percentage > 0.10:
            await self._trigger_alert(
                FundingAlertLevel.WARNING,
                f"⚠️ HIGH FUNDING: {position.symbol} costing "
                f"{daily_percentage*100:.1f}% of margin per day!",
                {
                    "position_id": position.position_id,
                    "symbol": position.symbol,
                    "daily_cost": daily_cost,
                    "daily_percentage": daily_percentage,
                    "current_rate": funding_rate,
                },
            )

    async def _trigger_alert(
        self, level: FundingAlertLevel, message: str, data: Dict[str, Any]
    ):
        """
        Trigger funding alert to all registered callbacks.

        Args:
            level: Alert severity level
            message: Alert message
            data: Additional alert data
        """
        logger.warning(message)

        for callback in self.alert_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(level, message, data)
                else:
                    callback(level, message, data)
            except Exception as e:
                logger.error(f"Error in alert callback: {e}")

    def get_funding_summary(self, position_id: str) -> Optional[FundingSummary]:
        """
        Get funding summary for a position.

        Args:
            position_id: Position ID

        Returns:
            FundingSummary or None if not found
        """
        summary_data = self.db.get_funding_summary(position_id)
        if not summary_data:
            return None

        position = self.tracked_positions.get(position_id)
        if not position:
            return None

        # Calculate funding as percentage of margin
        margin_percentage = (
            summary_data["total_paid"] / position.margin_used
            if position.margin_used > 0
            else 0
        )

        return FundingSummary(
            position_id=position_id,
            symbol=position.symbol,
            payment_count=summary_data["payment_count"],
            total_paid=summary_data["total_paid"],
            avg_rate=summary_data["avg_rate"],
            min_rate=summary_data["min_rate"],
            max_rate=summary_data["max_rate"],
            hours_elapsed=summary_data["payment_count"],  # 1 payment per hour
            daily_cost_estimate=summary_data["funding_per_day_avg"],
            margin_percentage=margin_percentage,
        )

    def get_all_summaries(self) -> Dict[str, FundingSummary]:
        """
        Get funding summaries for all tracked positions.

        Returns:
            Dict mapping position_id to FundingSummary
        """
        summaries = {}
        for position_id in self.tracked_positions:
            summary = self.get_funding_summary(position_id)
            if summary:
                summaries[position_id] = summary
        return summaries

    async def start(self):
        """Start funding tracker background tasks."""
        if self.running:
            logger.warning("Funding tracker already running")
            return

        self.running = True
        logger.info("Starting funding tracker...")

        # Start hourly payment task
        self._funding_task = asyncio.create_task(self._funding_payment_loop())

        # Start monitoring task
        self._monitoring_task = asyncio.create_task(self._monitoring_loop())

        logger.info("✅ Funding tracker started (HOURLY payments)")

    async def stop(self):
        """Stop funding tracker background tasks."""
        if not self.running:
            return

        logger.info("Stopping funding tracker...")
        self.running = False

        # Cancel tasks
        if self._funding_task and not self._funding_task.done():
            self._funding_task.cancel()
            try:
                await self._funding_task
            except asyncio.CancelledError:
                pass

        if self._monitoring_task and not self._monitoring_task.done():
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass

        logger.info("Funding tracker stopped")

    async def _funding_payment_loop(self):
        """
        Background task that applies funding every hour.

        ⚠️ CRITICAL: Runs every hour (24x per day)
        """
        while self.running:
            try:
                # Calculate time until next hour
                now = datetime.now()
                next_hour = (now + timedelta(hours=1)).replace(
                    minute=0, second=0, microsecond=0
                )
                seconds_until_next_hour = (next_hour - now).total_seconds()

                logger.info(
                    f"⏰ Next hourly funding payment in {seconds_until_next_hour/60:.1f} minutes"
                )

                # Wait until next hour
                await asyncio.sleep(seconds_until_next_hour)

                # Apply hourly funding
                if self.running:
                    await self.apply_hourly_funding()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in funding payment loop: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error

    async def _monitoring_loop(self):
        """
        Background task for monitoring funding rates and positions.

        Runs every 30 seconds to check for issues.
        """
        while self.running:
            try:
                await asyncio.sleep(30)

                if not self.tracked_positions:
                    continue

                # Check for stale funding rates
                now = datetime.now()
                for symbol, snapshot in self.current_rates.items():
                    age = (now - snapshot.timestamp).total_seconds()
                    if age > 60:  # Stale if older than 1 minute
                        logger.warning(
                            f"⚠️ Stale funding rate for {symbol} "
                            f"(last update {age:.0f}s ago)"
                        )

                # Log summary
                if self.tracked_positions:
                    total_positions = len(self.tracked_positions)
                    total_funding = sum(
                        abs(p.cumulative_funding_paid)
                        for p in self.tracked_positions.values()
                    )
                    logger.debug(
                        f"📊 Tracking {total_positions} positions, "
                        f"${total_funding:.2f} total funding paid"
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """
        Get funding tracker statistics.

        Returns:
            Statistics dictionary
        """
        total_funding = sum(
            abs(p.cumulative_funding_paid) for p in self.tracked_positions.values()
        )

        return {
            "running": self.running,
            "tracked_positions": len(self.tracked_positions),
            "total_payments_processed": self.total_payments_processed,
            "total_funding_paid": total_funding,
            "last_payment_time": (
                self.last_payment_time.isoformat() if self.last_payment_time else None
            ),
            "alert_threshold_warning": self.alert_threshold_warning,
            "alert_threshold_critical": self.alert_threshold_critical,
            "current_rates_count": len(self.current_rates),
        }

    def __repr__(self) -> str:
        return (
            f"<FundingTracker positions={len(self.tracked_positions)} "
            f"payments={self.total_payments_processed}>"
        )


# Example usage
async def example_funding_alert_callback(
    level: FundingAlertLevel, message: str, data: Dict[str, Any]
):
    """Example funding alert callback."""
    logger.warning(f"[{level.value.upper()}] {message}")
    logger.warning(f"Alert data: {data}")


async def main():
    """Example usage of FundingTracker."""
    from database import DatabaseManager

    # Initialize
    db = DatabaseManager("trading_bot.db")
    tracker = FundingTracker(db)

    # Register alert callback
    tracker.register_alert_callback(example_funding_alert_callback)

    # Start tracker
    await tracker.start()

    # Simulate tracking positions
    # In production, positions would be added when opened
    # and removed when closed

    try:
        # Keep running
        await asyncio.sleep(3600)  # Run for 1 hour
    finally:
        await tracker.stop()


if __name__ == "__main__":
    asyncio.run(main())
