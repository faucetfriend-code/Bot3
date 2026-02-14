"""
Signal Logger - Comprehensive logging for trading signals.

Features:
- In-memory log for quick API access
- Database persistence via DatabaseManager
- Auto-save to CSV file for easy viewing in Google Sheets
"""

import csv
import os
import threading
import time
from datetime import datetime
from typing import Dict, List, Any, Optional, Set
from pathlib import Path
from loguru import logger


class SignalLogger:
    """
    Comprehensive signal logging with multiple outputs.

    Logs to:
    1. In-memory list (for API access)
    2. Database (for historical queries)
    3. CSV file (for Google Sheets viewing)
    """

    def __init__(
        self,
        db_manager=None,
        csv_path: Optional[str] = None,
        max_memory_entries: int = 1000,
    ):
        """
        Initialize signal logger.

        Args:
            db_manager: DatabaseManager instance for persistence
            csv_path: Path to CSV file (default: signals_log.csv in project root)
            max_memory_entries: Max signals to keep in memory
        """
        self.db_manager = db_manager
        self.max_memory_entries = max_memory_entries
        self._lock = threading.Lock()

        # In-memory log
        self._signal_log: List[Dict[str, Any]] = []

        # Signal deduplication tracking (60-second window)
        self._recent_signal_ids: Set[str] = set()
        self._signal_timestamps: Dict[str, float] = {}
        self._dedup_window_seconds: int = 60

        # CSV setup
        if csv_path:
            self.csv_path = Path(csv_path)
        else:
            # Default to project root
            self.csv_path = Path(__file__).parent.parent / "signals_log.csv"

        # Initialize CSV with headers if it doesn't exist
        self._init_csv()

        logger.info(f"SignalLogger initialized - CSV path: {self.csv_path}, dedup window: {self._dedup_window_seconds}s")

    def _init_csv(self):
        """Initialize CSV file with headers if it doesn't exist."""
        if not self.csv_path.exists():
            with open(self.csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(self._get_csv_headers())
            logger.info(f"Created new signals log CSV: {self.csv_path}")

    def _get_csv_headers(self) -> List[str]:
        """Get CSV column headers."""
        return [
            "timestamp",
            "symbol",
            "strategy",
            "side",
            "entry_price",
            "stop_loss",
            "take_profit",
            "confidence",
            "quality",
            "regime",
            "status",  # generated, executed, rejected, failed, position_updated
            "rejection_reason",
            "execution_result",
            "order_id",
            "filled_price",
            "filled_quantity",
            "pnl",
            "notes",
        ]

    def _generate_signal_id(self, signal) -> str:
        """
        Generate unique signal ID from key fields.
        
        Args:
            signal: Signal object
            
        Returns:
            Unique signal identifier string
        """
        try:
            # Extract key identifying fields
            symbol = getattr(signal, 'asset', str(signal))
            strategy = getattr(signal.strategy, 'name', str(signal.strategy)) if hasattr(signal, 'strategy') else ""
            side = getattr(signal.side, 'name', str(signal.side)) if hasattr(signal, 'side') else ""
            
            # Create unique ID from identifying fields (EXCLUDING entry_price!)
            # Entry price is excluded because:
            # 1. Prices change frequently in the market (~30s intervals)
            # 2. We want to prevent duplicate signal EXECUTION within the dedup window
            # 3. Including price would create a new ID for each price change, defeating dedup
            signal_id = f"{symbol}:{strategy}:{side}"
            return signal_id
        except Exception as e:
            logger.warning(f"Failed to generate signal ID: {e}")
            # Fallback to string representation
            return str(signal)

    def _is_duplicate(self, signal_id: str) -> bool:
        """
        Check if signal ID is a duplicate within deduplication window.
        
        Args:
            signal_id: Signal identifier to check
            
        Returns:
            True if duplicate, False if new signal
        """
        current_time = time.time()
        
        # Clean up old entries outside the window
        self._cleanup_old_signals(current_time)
        
        # Check if signal exists in recent signals
        if signal_id in self._recent_signal_ids:
            logger.debug(f"🔄 Duplicate signal detected: {signal_id}")
            return True
            
        # Add to recent signals
        self._recent_signal_ids.add(signal_id)
        self._signal_timestamps[signal_id] = current_time
        return False

    def _cleanup_old_signals(self, current_time: float) -> None:
        """
        Remove signals outside the deduplication window.
        
        Args:
            current_time: Current timestamp
        """
        cutoff_time = current_time - self._dedup_window_seconds
        
        # Find and remove old signals
        old_signal_ids = [
            signal_id for signal_id, timestamp in self._signal_timestamps.items()
            if timestamp < cutoff_time
        ]
        
        for signal_id in old_signal_ids:
            self._recent_signal_ids.discard(signal_id)
            del self._signal_timestamps[signal_id]
            
        if old_signal_ids:
            logger.debug(f"🧹 Cleaned up {len(old_signal_ids)} old signal IDs from deduplication tracker")

    def log_signal_generated(
        self,
        signal,
        regime: str = "",
        notes: str = "",
    ) -> Dict[str, Any]:
        """
        Log a newly generated signal.

        Args:
            signal: Signal object
            regime: Current market regime
            notes: Additional notes

        Returns:
            Log entry dict (empty if duplicate)
        """
        # Check for duplicate signals
        signal_id = self._generate_signal_id(signal)
        if self._is_duplicate(signal_id):
            # Return empty dict for duplicates to prevent double logging
            logger.debug(f"🔄 Skipping duplicate signal log: {signal_id}")
            return {}
        
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": getattr(signal, 'asset', str(signal)),
            "strategy": getattr(signal.strategy, 'name', str(signal.strategy)) if hasattr(signal, 'strategy') else "",
            "side": getattr(signal.side, 'name', str(signal.side)) if hasattr(signal, 'side') else "",
            "entry_price": getattr(signal, 'entry_price', 0),
            "stop_loss": getattr(signal, 'stop_loss', 0),
            "take_profit": getattr(signal, 'take_profit', 0),
            "confidence": getattr(signal, 'confidence', 0),
            "quality": getattr(signal.quality, 'name', str(signal.quality)) if hasattr(signal, 'quality') else "",
            "regime": regime,
            "status": "generated",
            "rejection_reason": "",
            "execution_result": "",
            "order_id": "",
            "filled_price": "",
            "filled_quantity": "",
            "pnl": "",
            "notes": notes,
        }

        self._add_entry(entry)
        logger.debug(f"📝 Logged generated signal: {signal_id}")
        return entry

    def log_signal_rejected(
        self,
        signal,
        reason: str,
        regime: str = "",
        notes: str = "",
    ) -> Dict[str, Any]:
        """
        Log a rejected signal (failed validation).

        Args:
            signal: Signal object
            reason: Why the signal was rejected
            regime: Current market regime
            notes: Additional notes

        Returns:
            Log entry dict
        """
        # Check if this is a duplicate rejection
        signal_id = self._generate_signal_id(signal)
        is_duplicate = signal_id in self._recent_signal_ids
        
        # Add note about duplicate rejection if applicable
        if is_duplicate and not notes:
            notes = "Duplicate rejection"
        elif is_duplicate and notes:
            notes = f"{notes} (Duplicate rejection)"
        
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": getattr(signal, 'asset', str(signal)),
            "strategy": getattr(signal.strategy, 'name', str(signal.strategy)) if hasattr(signal, 'strategy') else "",
            "side": getattr(signal.side, 'name', str(signal.side)) if hasattr(signal, 'side') else "",
            "entry_price": getattr(signal, 'entry_price', 0),
            "stop_loss": getattr(signal, 'stop_loss', 0),
            "take_profit": getattr(signal, 'take_profit', 0),
            "confidence": getattr(signal, 'confidence', 0),
            "quality": getattr(signal.quality, 'name', str(signal.quality)) if hasattr(signal, 'quality') else "",
            "regime": regime,
            "status": "rejected",
            "rejection_reason": reason,
            "execution_result": "",
            "order_id": "",
            "filled_price": "",
            "filled_quantity": "",
            "pnl": "",
            "notes": notes,
        }

        self._add_entry(entry)
        if is_duplicate:
            logger.debug(f"🔄 Logged duplicate rejection: {signal_id}")
        else:
            logger.debug(f"📝 Logged rejection: {signal_id} - {reason}")
        return entry

    def log_signal_executed(
        self,
        signal,
        order_id: str = "",
        filled_price: float = 0,
        filled_quantity: float = 0,
        execution_result: str = "success",
        regime: str = "",
        notes: str = "",
        status: str = "executed",
    ) -> Dict[str, Any]:
        """
        Log an executed signal.

        Args:
            signal: Signal object
            order_id: Exchange order ID
            filled_price: Actual fill price
            filled_quantity: Filled quantity
            execution_result: Result description
            regime: Current market regime
            notes: Additional notes
            status: Signal status - "executed" for new positions, "position_updated" for updates

        Returns:
            Log entry dict
        """
        # Validate status - only allow "executed" or "position_updated"
        if status not in ("executed", "position_updated"):
            logger.warning(f"Invalid status '{status}' - defaulting to 'executed'")
            status = "executed"
            
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": getattr(signal, 'asset', str(signal)),
            "strategy": getattr(signal.strategy, 'name', str(signal.strategy)) if hasattr(signal, 'strategy') else "",
            "side": getattr(signal.side, 'name', str(signal.side)) if hasattr(signal, 'side') else "",
            "entry_price": getattr(signal, 'entry_price', 0),
            "stop_loss": getattr(signal, 'stop_loss', 0),
            "take_profit": getattr(signal, 'take_profit', 0),
            "confidence": getattr(signal, 'confidence', 0),
            "quality": getattr(signal.quality, 'name', str(signal.quality)) if hasattr(signal, 'quality') else "",
            "regime": regime,
            "status": status,
            "rejection_reason": "",
            "execution_result": execution_result,
            "order_id": str(order_id),
            "filled_price": filled_price,
            "filled_quantity": filled_quantity,
            "pnl": "",
            "notes": notes,
        }

        self._add_entry(entry)
        
        if status == "position_updated":
            logger.info(f"📝 Logged position update: {getattr(signal, 'asset', str(signal))} {getattr(signal.strategy, 'name', str(signal.strategy))}")
        else:
            logger.debug(f"📝 Logged signal execution: {getattr(signal, 'asset', str(signal))}")
            
        return entry

    def log_signal_failed(
        self,
        signal,
        error: str,
        regime: str = "",
        notes: str = "",
    ) -> Dict[str, Any]:
        """
        Log a failed signal execution.

        Args:
            signal: Signal object
            error: Error message
            regime: Current market regime
            notes: Additional notes

        Returns:
            Log entry dict
        """
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": getattr(signal, 'asset', str(signal)),
            "strategy": getattr(signal.strategy, 'name', str(signal.strategy)) if hasattr(signal, 'strategy') else "",
            "side": getattr(signal.side, 'name', str(signal.side)) if hasattr(signal, 'side') else "",
            "entry_price": getattr(signal, 'entry_price', 0),
            "stop_loss": getattr(signal, 'stop_loss', 0),
            "take_profit": getattr(signal, 'take_profit', 0),
            "confidence": getattr(signal, 'confidence', 0),
            "quality": getattr(signal.quality, 'name', str(signal.quality)) if hasattr(signal, 'quality') else "",
            "regime": regime,
            "status": "failed",
            "rejection_reason": "",
            "execution_result": f"ERROR: {error}",
            "order_id": "",
            "filled_price": "",
            "filled_quantity": "",
            "pnl": "",
            "notes": notes,
        }

        self._add_entry(entry)
        return entry

    def _add_entry(self, entry: Dict[str, Any]):
        """Add entry to all outputs (memory, database, CSV)."""
        with self._lock:
            # Add to memory
            self._signal_log.append(entry)

            # Trim memory if needed
            if len(self._signal_log) > self.max_memory_entries:
                self._signal_log = self._signal_log[-self.max_memory_entries:]

            # Save to CSV
            self._append_to_csv(entry)

            # Save to database
            self._save_to_database(entry)

    def _append_to_csv(self, entry: Dict[str, Any]):
        """Append entry to CSV file."""
        try:
            with open(self.csv_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                row = [entry.get(h, "") for h in self._get_csv_headers()]
                writer.writerow(row)
        except Exception as e:
            logger.error(f"Failed to write signal to CSV: {e}")

    def _save_to_database(self, entry: Dict[str, Any]):
        """Save signal to database."""
        try:
            from datetime import datetime
            
            # Defensive: Ensure all keys exist to prevent KeyError during string formatting
            safe_entry = entry.copy()
            for key in ["symbol", "asset_class", "strategy", "signal_type", "confidence", 
                       "side", "entry_price", "stop_loss", "take_profit", "quality", 
                       "regime", "status", "rejection_reason", "notes", "timestamp"]:
                safe_entry.setdefault(key, "" if key in ["symbol", "asset_class", "strategy", "signal_type", "side", "quality", "regime", "status", "rejection_reason", "notes"] else 0)
            
            signal_data = {
                "symbol": safe_entry.get("symbol", ""),
                "asset_class": safe_entry.get("asset_class", "perpetual"),
                "signal_type": safe_entry.get("strategy", safe_entry.get("signal_type", "unknown")),
                "strength": safe_entry.get("confidence", 0),
                "indicators": {
                    "side": safe_entry.get("side", ""),
                    "entry_price": safe_entry.get("entry_price", 0),
                    "stop_loss": safe_entry.get("stop_loss", 0),
                    "take_profit": safe_entry.get("take_profit", 0),
                    "quality": safe_entry.get("quality", ""),
                    "regime": safe_entry.get("regime", ""),
                    "status": safe_entry.get("status", ""),
                    "rejection_reason": safe_entry.get("rejection_reason", ""),
                    "notes": safe_entry.get("notes", ""),
                },
                "timestamp": safe_entry.get("timestamp", datetime.now().isoformat()),
            }
            self.db_manager.save_signal(signal_data)
        except Exception as e:
            logger.error(f"Failed to save signal to database: {e}")

    def get_recent_signals(self, count: int = 50) -> List[Dict[str, Any]]:
        """Get recent signals from memory."""
        with self._lock:
            return self._signal_log[-count:] if self._signal_log else []

    def get_signals_by_status(self, status: str) -> List[Dict[str, Any]]:
        """Get signals filtered by status."""
        with self._lock:
            return [s for s in self._signal_log if s.get("status") == status]

    def get_signals_by_symbol(self, symbol: str) -> List[Dict[str, Any]]:
        """Get signals filtered by symbol."""
        with self._lock:
            return [s for s in self._signal_log if s.get("symbol") == symbol]

    def get_statistics(self) -> Dict[str, Any]:
        """Get signal statistics."""
        with self._lock:
            total = len(self._signal_log)
            if total == 0:
                return {
                    "total": 0,
                    "generated": 0,
                    "executed": 0,
                    "rejected": 0,
                    "failed": 0,
                    "execution_rate": 0,
                }

            by_status = {}
            for entry in self._signal_log:
                status = entry.get("status", "unknown")
                by_status[status] = by_status.get(status, 0) + 1

            executed = by_status.get("executed", 0)
            generated = by_status.get("generated", 0) + executed

            return {
                "total": total,
                "generated": generated,
                "executed": executed,
                "rejected": by_status.get("rejected", 0),
                "failed": by_status.get("failed", 0),
                "execution_rate": (executed / generated * 100) if generated > 0 else 0,
                "by_status": by_status,
            }

    def clear_memory(self):
        """Clear in-memory log (CSV and database are preserved)."""
        with self._lock:
            self._signal_log = []
            logger.info("Signal log memory cleared")
