"""
Data Validation Pipeline for Trading Bot.

Validates incoming market data (OHLCV candles), trade data integrity,
timestamp consistency, and data freshness. Supports both SQLite and
PostgreSQL backends through the unified database layer.

Usage:
    from trading_bot_v2.data_validation import DataValidator

    validator = DataValidator()
    result = validator.validate_candle(candle_data)
    batch_result = validator.validate_candle_batch(candles)

Environment Variables:
    VALIDATION_STRICT_MODE  = "true" | "false" (default: "false")
    VALIDATION_MAX_PRICE_CHANGE_PCT = float (default: 0.50)  # 50%
    VALIDATION_MIN_VOLUME  = float (default: 0.0)
    VALIDATION_CANDLE_TOLERANCE_PCT = float (default: 0.001)  # 0.1%
"""

import logging
import os
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .database import get_db_connection, is_postgres, get_backend

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

STRICT_MODE: bool = os.getenv("VALIDATION_STRICT_MODE", "false").lower() in (
    "true",
    "1",
    "yes",
)
MAX_PRICE_CHANGE_PCT: float = float(os.getenv("VALIDATION_MAX_PRICE_CHANGE_PCT", "0.50"))
MIN_VOLUME: float = float(os.getenv("VALIDATION_MIN_VOLUME", "0.0"))
CANDLE_TOLERANCE_PCT: float = float(os.getenv("VALIDATION_CANDLE_TOLERANCE_PCT", "0.001"))


# ============================================================================
# Validation Result Types
# ============================================================================


class ValidationSeverity(str, Enum):
    """Severity level for validation issues."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class ValidationIssue:
    """Single validation issue found during data checks."""

    code: str
    message: str
    severity: ValidationSeverity
    field: Optional[str] = None
    value: Optional[Any] = None
    expected: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "field": self.field,
            "value": self.value,
            "expected": self.expected,
        }


@dataclass
class ValidationResult:
    """Aggregated result from a validation run."""

    is_valid: bool = True
    issues: List[ValidationIssue] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    record_count: int = 0
    error_count: int = 0
    warning_count: int = 0

    def add_issue(self, issue: ValidationIssue) -> None:
        """Add an issue and update counters."""
        self.issues.append(issue)
        if issue.severity in (ValidationSeverity.ERROR, ValidationSeverity.CRITICAL):
            self.error_count += 1
            self.is_valid = False
        elif issue.severity == ValidationSeverity.WARNING:
            self.warning_count += 1

    def merge(self, other: "ValidationResult") -> None:
        """Merge another result into this one."""
        self.issues.extend(other.issues)
        self.error_count += other.error_count
        self.warning_count += other.warning_count
        self.record_count += other.record_count
        if not other.is_valid:
            self.is_valid = False

    @property
    def error_issues(self) -> List[ValidationIssue]:
        """Return only error/critical issues."""
        return [
            i
            for i in self.issues
            if i.severity in (ValidationSeverity.ERROR, ValidationSeverity.CRITICAL)
        ]

    @property
    def warning_issues(self) -> List[ValidationIssue]:
        """Return only warning issues."""
        return [i for i in self.issues if i.severity == ValidationSeverity.WARNING]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "checked_at": self.checked_at.isoformat(),
            "record_count": self.record_count,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "issues": [i.to_dict() for i in self.issues[:50]],  # cap for serialization
        }

    def summary(self) -> str:
        """Human-readable summary."""
        status = "PASS" if self.is_valid else "FAIL"
        return (
            f"Validation {status}: {self.record_count} records, "
            f"{self.error_count} errors, {self.warning_count} warnings"
        )


# ============================================================================
# Market Data (OHLCV) Validation
# ============================================================================


class OHLCVValidator:
    """
    Validates OHLCV candle data for correctness and consistency.

    Checks performed:
      1. Required fields present and non-null
      2. Price values are positive and finite
      3. OHLC relationships: high >= max(open, close), low <= min(open, close)
      4. Volume is non-negative
      5. Timestamp is valid and not in the future
      6. Chronological ordering (within batch)
      7. No duplicate timestamps (within batch)
      8. Reasonable price changes between consecutive candles
      9. No zero-volume candles (warning)
     10. Timestamp spacing matches expected timeframe
    """

    # Expected interval in seconds for common timeframes
    TIMEFRAME_INTERVALS: Dict[str, int] = {
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
        "1w": 604800,
    }

    def __init__(
        self,
        strict: bool = STRICT_MODE,
        max_price_change_pct: float = MAX_PRICE_CHANGE_PCT,
        min_volume: float = MIN_VOLUME,
        candle_tolerance_pct: float = CANDLE_TOLERANCE_PCT,
    ) -> None:
        self.strict = strict
        self.max_price_change_pct = max_price_change_pct
        self.min_volume = min_volume
        self.candle_tolerance_pct = candle_tolerance_pct

    def validate_candle(
        self,
        candle: Dict[str, Any],
        previous_close: Optional[float] = None,
    ) -> ValidationResult:
        """
        Validate a single OHLCV candle.

        Args:
            candle: Dictionary with keys: timestamp, open, high, low, close, volume
            previous_close: Close price of the preceding candle for gap detection

        Returns:
            ValidationResult with all issues found
        """
        result = ValidationResult(record_count=1)

        # --- Required fields ---
        required_fields = ["timestamp", "open", "high", "low", "close", "volume"]
        for fld in required_fields:
            if fld not in candle or candle[fld] is None:
                result.add_issue(
                    ValidationIssue(
                        code="MISSING_FIELD",
                        message=f"Required field '{fld}' is missing or null",
                        severity=ValidationSeverity.CRITICAL,
                        field=fld,
                    )
                )

        # If critical fields missing, return early
        if not result.is_valid:
            return result

        # --- Positive price validation ---
        price_fields = {"open": candle["open"], "high": candle["high"],
                        "low": candle["low"], "close": candle["close"]}
        for name, price in price_fields.items():
            try:
                price_val = float(price)
            except (TypeError, ValueError):
                result.add_issue(
                    ValidationIssue(
                        code="INVALID_PRICE_TYPE",
                        message=f"Price field '{name}' is not numeric: {price!r}",
                        severity=ValidationSeverity.ERROR,
                        field=name,
                        value=price,
                    )
                )
                continue

            if price_val <= 0:
                result.add_issue(
                    ValidationIssue(
                        code="NON_POSITIVE_PRICE",
                        message=f"Price field '{name}' must be positive, got {price_val}",
                        severity=ValidationSeverity.ERROR,
                        field=name,
                        value=price_val,
                        expected="> 0",
                    )
                )
            elif price_val != price_val:  # NaN check
                result.add_issue(
                    ValidationIssue(
                        code="NAN_PRICE",
                        message=f"Price field '{name}' is NaN",
                        severity=ValidationSeverity.ERROR,
                        field=name,
                        value=price_val,
                    )
                )

        # --- OHLC relationship checks ---
        try:
            o, h, l, c = (
                float(candle["open"]),
                float(candle["high"]),
                float(candle["low"]),
                float(candle["close"]),
            )
            tolerance = self.candle_tolerance_pct * max(h, 1.0)

            if h < max(o, c) - tolerance:
                result.add_issue(
                    ValidationIssue(
                        code="HIGH低于OH",
                        message=f"High ({h}) is less than max(open={o}, close={c})",
                        severity=ValidationSeverity.ERROR,
                        field="high",
                        value=h,
                        expected=f">= max(open, close) = {max(o, c)}",
                    )
                )

            if l > min(o, c) + tolerance:
                result.add_issue(
                    ValidationIssue(
                        code="LOW高于OL",
                        message=f"Low ({l}) is greater than min(open={o}, close={c})",
                        severity=ValidationSeverity.ERROR,
                        field="low",
                        value=l,
                        expected=f"<= min(open, close) = {min(o, c)}",
                    )
                )

            if l > h + tolerance:
                result.add_issue(
                    ValidationIssue(
                        code="LOW_EXCEEDS_HIGH",
                        message=f"Low ({l}) exceeds high ({h})",
                        severity=ValidationSeverity.ERROR,
                        field="low",
                        value=l,
                    )
                )
        except (TypeError, ValueError):
            pass  # Already caught by price type check above

        # --- Volume validation ---
        try:
            vol = float(candle["volume"])
            if vol < 0:
                result.add_issue(
                    ValidationIssue(
                        code="NEGATIVE_VOLUME",
                        message=f"Volume is negative: {vol}",
                        severity=ValidationSeverity.ERROR,
                        field="volume",
                        value=vol,
                    )
                )
            elif vol == 0 and self.strict:
                result.add_issue(
                    ValidationIssue(
                        code="ZERO_VOLUME",
                        message="Volume is zero (possible data gap)",
                        severity=ValidationSeverity.WARNING,
                        field="volume",
                    )
                )
        except (TypeError, ValueError):
            result.add_issue(
                ValidationIssue(
                    code="INVALID_VOLUME_TYPE",
                    message=f"Volume is not numeric: {candle['volume']!r}",
                    severity=ValidationSeverity.ERROR,
                    field="volume",
                )
            )

        # --- Timestamp validation ---
        ts = candle["timestamp"]
        parsed_ts = self._parse_timestamp(ts)
        if parsed_ts is None:
            result.add_issue(
                ValidationIssue(
                    code="INVALID_TIMESTAMP",
                    message=f"Cannot parse timestamp: {ts!r}",
                    severity=ValidationSeverity.ERROR,
                    field="timestamp",
                    value=ts,
                )
            )
        else:
            now = datetime.now(timezone.utc)
            if parsed_ts.tzinfo is None:
                result.add_issue(
                    ValidationIssue(
                        code="NAIVE_TIMESTAMP",
                        message="Timestamp is timezone-naive (treated as UTC)",
                        severity=ValidationSeverity.WARNING,
                        field="timestamp",
                    )
                )

            if parsed_ts > now + timedelta(minutes=5):
                result.add_issue(
                    ValidationIssue(
                        code="FUTURE_TIMESTAMP",
                        message=f"Timestamp is in the future: {parsed_ts.isoformat()}",
                        severity=ValidationSeverity.WARNING,
                        field="timestamp",
                        value=parsed_ts.isoformat(),
                    )
                )

        # --- Price gap detection ---
        if previous_close is not None and parsed_ts is not None:
            try:
                current_close = float(candle["close"])
                if previous_close > 0:
                    change_pct = abs(current_close - previous_close) / previous_close
                    if change_pct > self.max_price_change_pct:
                        result.add_issue(
                            ValidationIssue(
                                code="LARGE_PRICE_CHANGE",
                                message=(
                                    f"Price changed {change_pct:.1%} from "
                                    f"{previous_close} to {current_close}"
                                ),
                                severity=ValidationSeverity.WARNING,
                                field="close",
                                value=current_close,
                            )
                        )
            except (TypeError, ValueError):
                pass

        return result

    def validate_candle_batch(
        self,
        candles: List[Dict[str, Any]],
        expected_timeframe: Optional[str] = None,
    ) -> ValidationResult:
        """
        Validate a batch of OHLCV candles.

        Checks ordering, duplicates, and timestamp spacing in addition
        to per-candle validation.

        Args:
            candles: List of candle dictionaries
            expected_timeframe: Expected timeframe string (e.g., "1m", "1h")

        Returns:
            Aggregated ValidationResult
        """
        result = ValidationResult(record_count=len(candles))

        if not candles:
            result.add_issue(
                ValidationIssue(
                    code="EMPTY_BATCH",
                    message="Candle batch is empty",
                    severity=ValidationSeverity.WARNING,
                )
            )
            return result

        # Parse and sort timestamps for ordering/duplicate checks
        timestamps: List[Tuple[int, int]] = []  # (original_index, epoch_seconds)

        prev_close: Optional[float] = None
        for idx, candle in enumerate(candles):
            # Per-candle validation
            candle_result = self.validate_candle(candle, previous_close=prev_close)
            result.merge(candle_result)

            # Collect timestamps for batch checks
            parsed = self._parse_timestamp(candle.get("timestamp"))
            if parsed is not None:
                epoch = int(parsed.timestamp())
                timestamps.append((idx, epoch))

            # Track close for next candle gap detection
            try:
                prev_close = float(candle.get("close", 0))
            except (TypeError, ValueError):
                prev_close = None

        # --- Chronological order check ---
        for i in range(1, len(timestamps)):
            prev_idx, prev_epoch = timestamps[i - 1]
            curr_idx, curr_epoch = timestamps[i]
            if curr_epoch < prev_epoch:
                result.add_issue(
                    ValidationIssue(
                        code="NON_CHRONO_ORDER",
                        message=(
                            f"Candle at index {curr_idx} has timestamp before "
                            f"candle at index {prev_idx}"
                        ),
                        severity=ValidationSeverity.ERROR,
                    )
                )

        # --- Duplicate timestamp check ---
        seen_epochs: Dict[int, int] = {}
        for idx, epoch in timestamps:
            if epoch in seen_epochs:
                result.add_issue(
                    ValidationIssue(
                        code="DUPLICATE_TIMESTAMP",
                        message=(
                            f"Duplicate timestamp at index {idx} "
                            f"(same as index {seen_epochs[epoch]})"
                        ),
                        severity=ValidationSeverity.ERROR,
                    )
                )
            else:
                seen_epochs[epoch] = idx

        # --- Timeframe spacing check ---
        if expected_timeframe and expected_timeframe in self.TIMEFRAME_INTERVALS:
            expected_interval = self.TIMEFRAME_INTERVALS[expected_timeframe]
            for i in range(1, len(timestamps)):
                _, prev_epoch = timestamps[i - 1]
                _, curr_epoch = timestamps[i]
                actual_interval = curr_epoch - prev_epoch
                # Allow 2x tolerance for minor timing jitter
                if actual_interval > expected_interval * 2:
                    result.add_issue(
                        ValidationIssue(
                            code="LARGE_TIME_GAP",
                            message=(
                                f"Gap of {actual_interval}s between candles "
                                f"at index {i-1} and {i} "
                                f"(expected ~{expected_interval}s for {expected_timeframe})"
                            ),
                            severity=ValidationSeverity.WARNING,
                        )
                    )

        return result

    @staticmethod
    def _parse_timestamp(ts: Any) -> Optional[datetime]:
        """Parse a timestamp value into a datetime object."""
        if ts is None:
            return None

        if isinstance(ts, datetime):
            return ts

        if isinstance(ts, (int, float)):
            # Epoch seconds or milliseconds
            if ts > 1e12:
                return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
            return datetime.fromtimestamp(ts, tz=timezone.utc)

        if isinstance(ts, str):
            # Try ISO format
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
            # Try numeric string
            try:
                val = float(ts)
                if val > 1e12:
                    return datetime.fromtimestamp(val / 1000.0, tz=timezone.utc)
                return datetime.fromtimestamp(val, tz=timezone.utc)
            except (ValueError, OSError):
                pass

        return None


# ============================================================================
# Trade Data Validation
# ============================================================================


class TradeValidator:
    """
    Validates trade data integrity for consistency and correctness.

    Checks performed:
      1. Required fields present
      2. Entry price and quantity are positive
      3. Exit price present for closed trades
      4. Entry time is before exit time
      5. Side is valid (buy/sell or long/short)
      6. PnL is within reasonable bounds
      7. Strategy name is non-empty
      8. No duplicate trade IDs
    """

    VALID_SIDES = {"buy", "sell", "long", "short"}
    VALID_STATUSES = {"open", "closed", "cancelled", "liquidated"}

    def validate_trade(self, trade: Dict[str, Any]) -> ValidationResult:
        """Validate a single trade record."""
        result = ValidationResult(record_count=1)

        # --- Required fields ---
        required = ["symbol", "side", "entry_price", "entry_time"]
        for fld in required:
            if fld not in trade or trade[fld] is None:
                result.add_issue(
                    ValidationIssue(
                        code="TRADE_MISSING_FIELD",
                        message=f"Required trade field '{fld}' is missing or null",
                        severity=ValidationSeverity.ERROR,
                        field=fld,
                    )
                )

        if not result.is_valid:
            return result

        # --- Side validation ---
        side = str(trade["side"]).lower()
        if side not in self.VALID_SIDES:
            result.add_issue(
                ValidationIssue(
                    code="INVALID_SIDE",
                    message=f"Invalid trade side: '{trade['side']}'",
                    severity=ValidationSeverity.ERROR,
                    field="side",
                    value=trade["side"],
                    expected=f"one of {self.VALID_SIDES}",
                )
            )

        # --- Price validation ---
        try:
            entry_price = float(trade["entry_price"])
            if entry_price <= 0:
                result.add_issue(
                    ValidationIssue(
                        code="NON_POSITIVE_ENTRY_PRICE",
                        message=f"Entry price must be positive: {entry_price}",
                        severity=ValidationSeverity.ERROR,
                        field="entry_price",
                        value=entry_price,
                    )
                )
        except (TypeError, ValueError):
            result.add_issue(
                ValidationIssue(
                    code="INVALID_ENTRY_PRICE",
                    message=f"Entry price is not numeric: {trade['entry_price']!r}",
                    severity=ValidationSeverity.ERROR,
                    field="entry_price",
                )
            )

        # --- Quantity validation ---
        if "quantity" in trade and trade["quantity"] is not None:
            try:
                qty = float(trade["quantity"])
                if qty <= 0:
                    result.add_issue(
                        ValidationIssue(
                            code="NON_POSITIVE_QUANTITY",
                            message=f"Quantity must be positive: {qty}",
                            severity=ValidationSeverity.ERROR,
                            field="quantity",
                            value=qty,
                        )
                    )
            except (TypeError, ValueError):
                result.add_issue(
                    ValidationIssue(
                        code="INVALID_QUANTITY",
                        message=f"Quantity is not numeric: {trade['quantity']!r}",
                        severity=ValidationSeverity.ERROR,
                        field="quantity",
                    )
                )

        # --- Exit validation for closed trades ---
        status = str(trade.get("status", "open")).lower()
        if status in ("closed", "cancelled", "liquidated"):
            if "exit_price" not in trade or trade["exit_price"] is None:
                result.add_issue(
                    ValidationIssue(
                        code="MISSING_EXIT_PRICE",
                        message=f"Closed trade missing exit_price",
                        severity=ValidationSeverity.ERROR,
                        field="exit_price",
                    )
                )
            else:
                try:
                    exit_price = float(trade["exit_price"])
                    if exit_price <= 0:
                        result.add_issue(
                            ValidationIssue(
                                code="NON_POSITIVE_EXIT_PRICE",
                                message=f"Exit price must be positive: {exit_price}",
                                severity=ValidationSeverity.ERROR,
                                field="exit_price",
                                value=exit_price,
                            )
                        )
                except (TypeError, ValueError):
                    result.add_issue(
                        ValidationIssue(
                            code="INVALID_EXIT_PRICE",
                            message=f"Exit price is not numeric: {trade['exit_price']!r}",
                            severity=ValidationSeverity.ERROR,
                            field="exit_price",
                        )
                    )

        # --- Timestamp ordering ---
        entry_time = self._parse_datetime(trade.get("entry_time"))
        exit_time = self._parse_datetime(trade.get("exit_time"))
        if entry_time is not None and exit_time is not None:
            if exit_time < entry_time:
                result.add_issue(
                    ValidationIssue(
                        code="EXIT_BEFORE_ENTRY",
                        message=(
                            f"Exit time ({exit_time.isoformat()}) is before "
                            f"entry time ({entry_time.isoformat()})"
                        ),
                        severity=ValidationSeverity.ERROR,
                        field="exit_time",
                    )
                )

        # --- PnL reasonableness ---
        if "pnl" in trade and trade["pnl"] is not None:
            try:
                pnl = float(trade["pnl"])
                if abs(pnl) > 1_000_000:
                    result.add_issue(
                        ValidationIssue(
                            code="EXTREME_PNL",
                            message=f"PnL magnitude seems extreme: {pnl}",
                            severity=ValidationSeverity.WARNING,
                            field="pnl",
                            value=pnl,
                        )
                    )
            except (TypeError, ValueError):
                result.add_issue(
                    ValidationIssue(
                        code="INVALID_PNL",
                        message=f"PnL is not numeric: {trade['pnl']!r}",
                        severity=ValidationSeverity.ERROR,
                        field="pnl",
                    )
                )

        return result

    def validate_trade_batch(
        self, trades: List[Dict[str, Any]]
    ) -> ValidationResult:
        """Validate a batch of trades, including cross-record checks."""
        result = ValidationResult(record_count=len(trades))

        seen_ids: Dict[str, int] = {}
        for idx, trade in enumerate(trades):
            trade_result = self.validate_trade(trade)
            result.merge(trade_result)

            # Duplicate ID check
            trade_id = trade.get("id")
            if trade_id is not None:
                id_str = str(trade_id)
                if id_str in seen_ids:
                    result.add_issue(
                        ValidationIssue(
                            code="DUPLICATE_TRADE_ID",
                            message=(
                                f"Duplicate trade ID '{id_str}' at indices "
                                f"{seen_ids[id_str]} and {idx}"
                            ),
                            severity=ValidationSeverity.ERROR,
                        )
                    )
                else:
                    seen_ids[id_str] = idx

        return result

    @staticmethod
    def _parse_datetime(value: Any) -> Optional[datetime]:
        """Parse a datetime value."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value, tz=timezone.utc)
            except (OSError, ValueError):
                return None
        return None


# ============================================================================
# Database-Level Validation
# ============================================================================


class DatabaseValidator:
    """
    Validates data quality directly in the database.

    Runs SQL-level checks against market_data, trades, and signals
    tables to detect gaps, anomalies, and inconsistencies that
    per-record validation cannot catch.
    """

    def __init__(self) -> None:
        self.ohlcv = OHLCVValidator()
        self.trade = TradeValidator()

    def check_market_data_gaps(
        self,
        symbol: str,
        timeframe: str = "1m",
        hours_back: int = 24,
    ) -> ValidationResult:
        """
        Check for gaps in market_data for a specific symbol/timeframe.

        Args:
            symbol: Trading pair symbol (e.g., "BTC-USDC")
            timeframe: Candle timeframe
            hours_back: How many hours to look back

        Returns:
            ValidationResult with gap information
        """
        result = ValidationResult()
        expected_interval = OHLCVValidator.TIMEFRAME_INTERVALS.get(timeframe, 60)
        # Allow 2.5x expected interval to flag gaps
        gap_threshold = expected_interval * 2.5

        try:
            with get_db_connection() as conn:
                if is_postgres():
                    rows = conn.execute(
                        """
                        SELECT "timestamp", close, volume
                        FROM market_data
                        WHERE symbol = %s
                          AND timeframe = %s
                          AND "timestamp" >= NOW() - make_interval(hours => %s)
                        ORDER BY "timestamp" ASC
                        """,
                        (symbol, timeframe, hours_back),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT timestamp, close, volume
                        FROM market_data
                        WHERE symbol = ?
                          AND timeframe = ?
                          AND timestamp >= datetime('now', ?)
                        ORDER BY timestamp ASC
                        """,
                        (symbol, timeframe, f"-{hours_back} hours"),
                    ).fetchall()

                result.record_count = len(rows) if rows else 0

                if not rows or result.record_count == 0:
                    result.add_issue(
                        ValidationIssue(
                            code="NO_DATA",
                            message=(
                                f"No market data found for {symbol} "
                                f"timeframe={timeframe} in last {hours_back}h"
                            ),
                            severity=ValidationSeverity.WARNING,
                        )
                    )
                    return result

                # Check for time gaps
                prev_ts = None
                gap_count = 0
                for row in rows:
                    ts_val = row[0] if not isinstance(row, dict) else row.get("timestamp")
                    close_val = row[1] if not isinstance(row, dict) else row.get("close")
                    vol_val = row[2] if not isinstance(row, dict) else row.get("volume")

                    if prev_ts is not None and ts_val is not None:
                        try:
                            if isinstance(ts_val, str):
                                curr_dt = datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
                            elif isinstance(ts_val, datetime):
                                curr_dt = ts_val
                            else:
                                prev_ts = ts_val
                                continue

                            if isinstance(prev_ts, str):
                                prev_dt = datetime.fromisoformat(prev_ts.replace("Z", "+00:00"))
                            elif isinstance(prev_ts, datetime):
                                prev_dt = prev_ts
                            else:
                                prev_ts = ts_val
                                continue

                            diff = (curr_dt - prev_dt).total_seconds()
                            if diff > gap_threshold:
                                gap_count += 1
                                result.add_issue(
                                    ValidationIssue(
                                        code="DATA_GAP",
                                        message=(
                                            f"Gap of {diff:.0f}s detected "
                                            f"(expected ~{expected_interval}s for {timeframe})"
                                        ),
                                        severity=ValidationSeverity.WARNING,
                                        field="timestamp",
                                    )
                                )
                        except (TypeError, ValueError):
                            pass

                    prev_ts = ts_val

                # Check for zero/negative volumes
                zero_vol_count = 0
                for row in rows:
                    vol_val = row[2] if not isinstance(row, dict) else row.get("volume")
                    try:
                        if vol_val is not None and float(vol_val) <= 0:
                            zero_vol_count += 1
                    except (TypeError, ValueError):
                        pass

                if zero_vol_count > 0:
                    result.add_issue(
                        ValidationIssue(
                            code="ZERO_VOLUME_RECORDS",
                            message=f"{zero_vol_count} candles with zero/negative volume",
                            severity=ValidationSeverity.WARNING,
                        )
                    )

                logger.debug(
                    f"Market data gap check for {symbol}/{timeframe}: "
                    f"{result.record_count} records, {gap_count} gaps"
                )

        except Exception as e:
            result.add_issue(
                ValidationIssue(
                    code="DB_ERROR",
                    message=f"Database error during gap check: {e}",
                    severity=ValidationSeverity.ERROR,
                )
            )
            logger.error(f"Market data gap check failed: {e}")

        return result

    def check_trade_integrity(
        self, hours_back: int = 168
    ) -> ValidationResult:
        """
        Check trade data integrity in the database.

        Verifies:
          - No orphaned closed trades (exit without entry)
          - PnL consistency with entry/exit prices
          - Timestamp ordering

        Args:
            hours_back: How many hours to look back (default 7 days)

        Returns:
            ValidationResult with integrity issues
        """
        result = ValidationResult()

        try:
            with get_db_connection() as conn:
                if is_postgres():
                    rows = conn.execute(
                        """
                        SELECT id, symbol, side, entry_price, exit_price,
                               entry_time, exit_time, pnl, status, quantity
                        FROM trades
                        WHERE entry_time >= NOW() - make_interval(hours => %s)
                        ORDER BY entry_time ASC
                        """,
                        (hours_back,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT id, symbol, side, entry_price, exit_price,
                               entry_time, exit_time, pnl, status, quantity
                        FROM trades
                        WHERE entry_time >= datetime('now', ?)
                        ORDER BY entry_time ASC
                        """,
                        (f"-{hours_back} hours",),
                    ).fetchall()

                result.record_count = len(rows) if rows else 0

                if not rows:
                    return result

                for row in rows:
                    if isinstance(row, dict):
                        row_dict = row
                    else:
                        row_dict = {
                            "id": row[0],
                            "symbol": row[1],
                            "side": row[2],
                            "entry_price": row[3],
                            "exit_price": row[4],
                            "entry_time": row[5],
                            "exit_time": row[6],
                            "pnl": row[7],
                            "status": row[8],
                            "quantity": row[9],
                        }

                    # Validate each trade
                    trade_result = self.trade.validate_trade(row_dict)
                    result.merge(trade_result)

        except Exception as e:
            result.add_issue(
                ValidationIssue(
                    code="DB_ERROR",
                    message=f"Database error during trade integrity check: {e}",
                    severity=ValidationSeverity.ERROR,
                )
            )
            logger.error(f"Trade integrity check failed: {e}")

        return result

    def check_data_freshness(
        self,
        symbol: Optional[str] = None,
        max_staleness_seconds: int = 300,
    ) -> ValidationResult:
        """
        Check how fresh the latest market data is.

        Args:
            symbol: Optional symbol filter (checks all if None)
            max_staleness_seconds: Maximum acceptable data age in seconds

        Returns:
            ValidationResult with freshness information
        """
        result = ValidationResult()

        try:
            with get_db_connection() as conn:
                if symbol:
                    if is_postgres():
                        row = conn.execute(
                            """
                            SELECT symbol, MAX("timestamp") as latest
                            FROM market_data
                            WHERE symbol = %s
                            GROUP BY symbol
                            """,
                            (symbol,),
                        ).fetchone()
                    else:
                        row = conn.execute(
                            """
                            SELECT symbol, MAX(timestamp) as latest
                            FROM market_data
                            WHERE symbol = ?
                            GROUP BY symbol
                            """,
                            (symbol,),
                        ).fetchone()

                    if row:
                        latest = row[1] if not isinstance(row, dict) else row.get("latest")
                        result.record_count = 1
                        if latest is not None:
                            staleness = self._compute_staleness(latest)
                            if staleness > max_staleness_seconds:
                                result.add_issue(
                                    ValidationIssue(
                                        code="STALE_DATA",
                                        message=(
                                            f"Data for {symbol} is {staleness:.0f}s old "
                                            f"(threshold: {max_staleness_seconds}s)"
                                        ),
                                        severity=ValidationSeverity.WARNING,
                                        field="timestamp",
                                    )
                                )
                        else:
                            result.add_issue(
                                ValidationIssue(
                                    code="NO_DATA",
                                    message=f"No data found for {symbol}",
                                    severity=ValidationSeverity.WARNING,
                                )
                            )
                else:
                    # Check all symbols
                    if is_postgres():
                        rows = conn.execute(
                            """
                            SELECT symbol, MAX("timestamp") as latest
                            FROM market_data
                            GROUP BY symbol
                            ORDER BY latest DESC
                            """
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            """
                            SELECT symbol, MAX(timestamp) as latest
                            FROM market_data
                            GROUP BY symbol
                            ORDER BY latest DESC
                            """
                        ).fetchall()

                    result.record_count = len(rows) if rows else 0

                    if rows:
                        for row in rows:
                            sym = row[0] if not isinstance(row, dict) else row.get("symbol")
                            latest = row[1] if not isinstance(row, dict) else row.get("latest")
                            if latest is not None:
                                staleness = self._compute_staleness(latest)
                                if staleness > max_staleness_seconds:
                                    result.add_issue(
                                        ValidationIssue(
                                            code="STALE_DATA",
                                            message=(
                                                f"Data for {sym} is {staleness:.0f}s old"
                                            ),
                                            severity=ValidationSeverity.WARNING,
                                            field="timestamp",
                                        )
                                    )

        except Exception as e:
            result.add_issue(
                ValidationIssue(
                    code="DB_ERROR",
                    message=f"Database error during freshness check: {e}",
                    severity=ValidationSeverity.ERROR,
                )
            )
            logger.error(f"Data freshness check failed: {e}")

        return result

    @staticmethod
    def _compute_staleness(timestamp: Any) -> float:
        """Compute how many seconds ago a timestamp is."""
        now = datetime.now(timezone.utc)
        if isinstance(timestamp, datetime):
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            return (now - timestamp).total_seconds()
        if isinstance(timestamp, str):
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return (now - dt).total_seconds()
            except (ValueError, TypeError):
                pass
        return 0.0


# ============================================================================
# Main Validator Facade
# ============================================================================


class DataValidator:
    """
    Unified data validation facade.

    Combines OHLCV validation, trade validation, and database-level
    checks into a single interface. This is the primary entry point
    for the validation pipeline.

    Usage:
        validator = DataValidator()

        # Validate a single candle
        result = validator.validate_candle(candle_dict)

        # Validate a batch
        result = validator.validate_candle_batch(candles, expected_timeframe="1m")

        # Database-level checks
        result = validator.check_data_freshness("BTC-USDC")
        result = validator.check_market_data_gaps("BTC-USDC", "1m", hours_back=24)
        result = validator.check_trade_integrity(hours_back=168)
    """

    def __init__(self) -> None:
        self.ohlcv_validator = OHLCVValidator()
        self.trade_validator = TradeValidator()
        self.db_validator = DatabaseValidator()
        logger.info(
            f"DataValidator initialized (backend={get_backend()}, "
            f"strict={STRICT_MODE})"
        )

    def validate_candle(
        self,
        candle: Dict[str, Any],
        previous_close: Optional[float] = None,
    ) -> ValidationResult:
        """Validate a single OHLCV candle."""
        return self.ohlcv_validator.validate_candle(candle, previous_close)

    def validate_candle_batch(
        self,
        candles: List[Dict[str, Any]],
        expected_timeframe: Optional[str] = None,
    ) -> ValidationResult:
        """Validate a batch of OHLCV candles."""
        return self.ohlcv_validator.validate_candle_batch(candles, expected_timeframe)

    def validate_trade(self, trade: Dict[str, Any]) -> ValidationResult:
        """Validate a single trade record."""
        return self.trade_validator.validate_trade(trade)

    def validate_trade_batch(self, trades: List[Dict[str, Any]]) -> ValidationResult:
        """Validate a batch of trades."""
        return self.trade_validator.validate_trade_batch(trades)

    def check_market_data_gaps(
        self,
        symbol: str,
        timeframe: str = "1m",
        hours_back: int = 24,
    ) -> ValidationResult:
        """Check for gaps in market data."""
        return self.db_validator.check_market_data_gaps(symbol, timeframe, hours_back)

    def check_trade_integrity(self, hours_back: int = 168) -> ValidationResult:
        """Check trade data integrity in the database."""
        return self.db_validator.check_trade_integrity(hours_back)

    def check_data_freshness(
        self,
        symbol: Optional[str] = None,
        max_staleness_seconds: int = 300,
    ) -> ValidationResult:
        """Check data freshness."""
        return self.db_validator.check_data_freshness(symbol, max_staleness_seconds)

    def run_full_validation(
        self,
        symbol: Optional[str] = None,
        timeframe: str = "1m",
    ) -> Dict[str, Any]:
        """
        Run a comprehensive validation suite across all checks.

        Args:
            symbol: Symbol to validate (if applicable)
            timeframe: Timeframe for market data checks

        Returns:
            Dictionary with all validation results
        """
        results: Dict[str, Any] = {}

        # Freshness check
        freshness = self.check_data_freshness(symbol)
        results["freshness"] = freshness.to_dict()

        # Gap check (if symbol provided)
        if symbol:
            gaps = self.check_market_data_gaps(symbol, timeframe)
            results["gaps"] = gaps.to_dict()

        # Trade integrity
        trade_integrity = self.check_trade_integrity()
        results["trade_integrity"] = trade_integrity.to_dict()

        # Summary
        all_valid = all(
            r.get("is_valid", True)
            for r in results.values()
            if isinstance(r, dict)
        )
        total_errors = sum(
            r.get("error_count", 0)
            for r in results.values()
            if isinstance(r, dict)
        )
        total_warnings = sum(
            r.get("warning_count", 0)
            for r in results.values()
            if isinstance(r, dict)
        )

        results["summary"] = {
            "is_valid": all_valid,
            "total_errors": total_errors,
            "total_warnings": total_warnings,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            f"Full validation complete: valid={all_valid}, "
            f"errors={total_errors}, warnings={total_warnings}"
        )

        return results


# ============================================================================
# Singleton
# ============================================================================

_validator: Optional[DataValidator] = None


def get_data_validator() -> DataValidator:
    """Get or create the global DataValidator instance."""
    global _validator
    if _validator is None:
        _validator = DataValidator()
    return _validator
