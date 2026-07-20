"""
Adaptive Per-Regime Strategy Weights
====================================

Computes per-(regime, strategy) performance multipliers from closed trades
so the static regime weight tables in MarketRegimeDetector.get_strategy_weights
adapt to realized performance ("always updating" allocation).

Scoring model
-------------
For every (regime, strategy) cell over closed trades (pnl-pct-per-trade,
where pnl_pct = pnl / (entry_price * quantity) * 100):

- recent_expectancy: recency-weighted mean of pnl_pct using exponential
  decay with half-life ADAPTIVE_WEIGHT_HALFLIFE_DAYS (default 14):
  weight = 0.5 ** (age_days / halflife)
- lifetime_expectancy: plain unweighted mean of pnl_pct
- score = 0.7 * recent_expectancy + 0.3 * lifetime_expectancy

Score-to-multiplier mapping (documented, env-tunable):

    slope = (max_mult - min_mult) / (2.0 * SCALE)
    multiplier = clamp(1.0 + score * slope, min_mult, max_mult)

With defaults (SCALE=2.0, clamp [0.5, 1.5]) the slope is 0.25, so a score
of +2 pnl-pct saturates the upper clamp (1.5), -2 saturates the lower
clamp (0.5), and +1 pnl-pct per trade maps to 1.25. SCALE is therefore
"the |score| in pnl-pct at which the multiplier saturates the clamp".

Evidence gate: cells with fewer than ADAPTIVE_WEIGHT_MIN_TRADES closed
trades return a neutral 1.0 (static weights apply unchanged until enough
evidence accumulates).

Caching: multipliers are recomputed at most every
ADAPTIVE_WEIGHT_REFRESH_MINUTES (default 60), guarded by a lock for
thread safety. Each recompute persists a snapshot to the adaptive_weights
table via DatabaseManager for observability (GET /api/weights/adaptive).
"""

import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from .history import metrics as history_metrics


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, falling back to default on error."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(f"Invalid float for {name}={raw!r}, using default {default}")
        return default


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, falling back to default on error."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"Invalid int for {name}={raw!r}, using default {default}")
        return default


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean from the environment (true/1/yes/on, false/0/no/off)."""
    value = os.getenv(name, "").lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    return default


def _normalize_strategy(name: Any) -> str:
    """Normalize a strategy identifier to a canonical lookup key.

    Handles enum values ("MOMENTUM_SCALPING"), display names
    ("MomentumScalping") and spaced variants ("momentum scalping") by
    lowercasing and stripping underscores and spaces.
    """
    if name is None:
        return ""
    raw = getattr(name, "value", name)
    return str(raw).lower().replace("_", "").replace(" ", "")


def _normalize_regime(regime: Any) -> str:
    """Normalize a regime identifier (enum or string) to its lowercase value."""
    if regime is None:
        return ""
    raw = getattr(regime, "value", regime)
    return str(raw).lower()


class AdaptiveWeightManager:
    """
    Computes and caches per-(regime, strategy) weight multipliers.

    The manager is deliberately failure-safe: any error while reading
    trades, computing scores, or persisting snapshots degrades to neutral
    multipliers (1.0) rather than raising into the signal path.

    Args:
        db: DatabaseManager-like object exposing
            get_closed_trades_for_weights() and
            save_adaptive_weight_snapshot(rows). When None, the manager
            stays neutral (all multipliers 1.0) - this keeps unit tests
            and backtests hermetic; the live bot wires the real db in.
        trade_store: Optional history.TradeStore used to fetch closed
            trades (preferred source; the db fallback is kept for
            legacy/duck-typed wiring). Requires db for snapshots.
        enabled: Override for ENABLE_ADAPTIVE_WEIGHTS (default env/true).
        halflife_days: Override for ADAPTIVE_WEIGHT_HALFLIFE_DAYS.
        min_trades: Override for ADAPTIVE_WEIGHT_MIN_TRADES.
        refresh_minutes: Override for ADAPTIVE_WEIGHT_REFRESH_MINUTES.
        scale: Override for ADAPTIVE_WEIGHT_SCALE.
        min_mult: Override for ADAPTIVE_WEIGHT_MIN_MULT.
        max_mult: Override for ADAPTIVE_WEIGHT_MAX_MULT.
    """

    RECENT_BLEND = 0.7
    LIFETIME_BLEND = 0.3

    def __init__(
        self,
        db: Optional[Any] = None,
        trade_store: Optional[Any] = None,
        enabled: Optional[bool] = None,
        halflife_days: Optional[float] = None,
        min_trades: Optional[int] = None,
        refresh_minutes: Optional[float] = None,
        scale: Optional[float] = None,
        min_mult: Optional[float] = None,
        max_mult: Optional[float] = None,
    ):
        self.db = db
        self.trade_store = trade_store
        self.enabled = (
            enabled
            if enabled is not None
            else _env_bool("ENABLE_ADAPTIVE_WEIGHTS", True)
        )
        self.halflife_days = (
            halflife_days
            if halflife_days is not None
            else _env_float("ADAPTIVE_WEIGHT_HALFLIFE_DAYS", 14.0)
        )
        self.min_trades = (
            min_trades
            if min_trades is not None
            else _env_int("ADAPTIVE_WEIGHT_MIN_TRADES", 10)
        )
        self.refresh_minutes = (
            refresh_minutes
            if refresh_minutes is not None
            else _env_float("ADAPTIVE_WEIGHT_REFRESH_MINUTES", 60.0)
        )
        self.scale = (
            scale if scale is not None else _env_float("ADAPTIVE_WEIGHT_SCALE", 2.0)
        )
        self.min_mult = (
            min_mult
            if min_mult is not None
            else _env_float("ADAPTIVE_WEIGHT_MIN_MULT", 0.5)
        )
        self.max_mult = (
            max_mult
            if max_mult is not None
            else _env_float("ADAPTIVE_WEIGHT_MAX_MULT", 1.5)
        )

        # Injectable clocks for deterministic tests.
        self._monotonic = time.monotonic
        self._now = datetime.now

        self._lock = threading.Lock()
        # (regime_key, strategy_key) -> multiplier
        self._multipliers: Dict[Tuple[str, str], float] = {}
        # (regime_key, strategy_key) -> cell stats for observability
        self._cells: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._last_refresh_monotonic: Optional[float] = None
        self._last_computed_at: Optional[str] = None

        logger.info(
            f"AdaptiveWeightManager initialized: enabled={self.enabled}, "
            f"halflife={self.halflife_days}d, min_trades={self.min_trades}, "
            f"refresh={self.refresh_minutes}min, scale={self.scale}, "
            f"clamp=[{self.min_mult}, {self.max_mult}], "
            f"db_wired={self.db is not None}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_multiplier(self, regime: Any, strategy: Any) -> float:
        """
        Return the adaptive multiplier for a (regime, strategy) cell.

        Returns neutral 1.0 when disabled, when no db is wired, when the
        cell has insufficient evidence, or on any internal error.

        Args:
            regime: MarketRegime enum or regime value string.
            strategy: Strategy display name, enum, or enum value string.

        Returns:
            Multiplier in [min_mult, max_mult], or exactly 1.0 (neutral).
        """
        if not self.enabled or (self.db is None and self.trade_store is None):
            return 1.0
        try:
            self._maybe_refresh()
            key = (_normalize_regime(regime), _normalize_strategy(strategy))
            return self._multipliers.get(key, 1.0)
        except Exception as e:
            logger.warning(f"Adaptive multiplier lookup failed: {e}")
            return 1.0

    def refresh(self, force: bool = False) -> bool:
        """
        Recompute multipliers from closed trades if the cache is stale.

        Args:
            force: Recompute even if the refresh interval has not elapsed.

        Returns:
            True if a recompute ran, False if the cache was still fresh
            or the manager is disabled / has no db.
        """
        if not self.enabled or (self.db is None and self.trade_store is None):
            return False
        with self._lock:
            if not force and not self._is_stale_locked():
                return False
            try:
                self._recompute_locked()
                return True
            except Exception as e:
                # Failure-safe: mark refreshed so we do not hammer a broken
                # db on every signal; retry after the normal interval.
                self._last_refresh_monotonic = self._monotonic()
                logger.error(f"Adaptive weight recompute failed: {e}")
                return False

    def get_status(self) -> Dict[str, Any]:
        """
        Return current multipliers, evidence counts and last compute time.

        Returns:
            JSON-serialisable dict with enabled flag, config, last compute
            time, and per-regime multiplier / evidence maps.
        """
        with self._lock:
            multipliers: Dict[str, Dict[str, float]] = {}
            evidence: Dict[str, Dict[str, Dict[str, Any]]] = {}
            for (regime_key, strategy_key), mult in self._multipliers.items():
                multipliers.setdefault(regime_key, {})[strategy_key] = round(
                    mult, 4
                )
            for (regime_key, strategy_key), cell in self._cells.items():
                evidence.setdefault(regime_key, {})[strategy_key] = {
                    "trade_count": cell.get("trade_count", 0),
                    "recent_expectancy": cell.get("recent_expectancy"),
                    "lifetime_expectancy": cell.get("lifetime_expectancy"),
                    "multiplier": cell.get("multiplier", 1.0),
                }
            return {
                "enabled": self.enabled,
                "db_wired": self.db is not None,
                "last_computed_at": self._last_computed_at,
                "config": {
                    "halflife_days": self.halflife_days,
                    "min_trades": self.min_trades,
                    "refresh_minutes": self.refresh_minutes,
                    "scale": self.scale,
                    "min_mult": self.min_mult,
                    "max_mult": self.max_mult,
                },
                "multipliers": multipliers,
                "cells": evidence,
            }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _maybe_refresh(self) -> None:
        """Refresh the multiplier cache if the refresh interval elapsed."""
        with self._lock:
            if not self._is_stale_locked():
                return
            try:
                self._recompute_locked()
            except Exception as e:
                self._last_refresh_monotonic = self._monotonic()
                logger.error(f"Adaptive weight recompute failed: {e}")

    def _is_stale_locked(self) -> bool:
        """Check (holding the lock) whether a recompute is due."""
        if self._last_refresh_monotonic is None:
            return True
        elapsed_minutes = (self._monotonic() - self._last_refresh_monotonic) / 60.0
        return elapsed_minutes >= self.refresh_minutes

    def _recompute_locked(self) -> None:
        """Recompute all cell multipliers and persist a snapshot."""
        trades = self._fetch_closed_trades()
        now = self._now()

        # (regime_key, strategy_key) -> list of (pnl_pct, age_days)
        grouped: Dict[Tuple[str, str], List[Tuple[float, float]]] = {}
        for trade in trades:
            regime_key = _normalize_regime(trade.get("regime"))
            strategy_key = _normalize_strategy(trade.get("strategy"))
            if not regime_key or not strategy_key:
                continue
            pnl_pct = history_metrics.trade_pnl_pct(
                trade.get("pnl"), trade.get("entry_price"), trade.get("quantity")
            )
            if pnl_pct is None:
                # Unparseable values or non-positive notional: skip.
                continue
            age_days = self._trade_age_days(trade, now)
            grouped.setdefault((regime_key, strategy_key), []).append(
                (pnl_pct, age_days)
            )

        multipliers: Dict[Tuple[str, str], float] = {}
        cells: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for key, samples in grouped.items():
            trade_count = len(samples)
            lifetime = history_metrics.expectancy([p for p, _ in samples])
            recent = history_metrics.recency_weighted_expectancy(
                samples, self.halflife_days
            )

            if trade_count < self.min_trades:
                mult = 1.0  # Evidence gate: fall back to static weights
            else:
                score = (
                    self.RECENT_BLEND * recent + self.LIFETIME_BLEND * lifetime
                )
                mult = self._score_to_multiplier(score)

            multipliers[key] = mult
            cells[key] = {
                "trade_count": trade_count,
                "recent_expectancy": round(recent, 6),
                "lifetime_expectancy": round(lifetime, 6),
                "multiplier": round(mult, 6),
            }

        computed_at = now.isoformat()
        self._multipliers = multipliers
        self._cells = cells
        self._last_refresh_monotonic = self._monotonic()
        self._last_computed_at = computed_at

        logger.debug(
            f"Adaptive weights recomputed at {computed_at}: "
            f"{len(multipliers)} cells from {len(trades)} closed trades"
        )

        self._persist_snapshot(computed_at)

    def _fetch_closed_trades(self) -> List[Dict[str, Any]]:
        """Fetch closed trades from the TradeStore (db legacy fallback)."""
        if self.trade_store is not None:
            return self.trade_store.get_closed_trades()
        return self.db.get_closed_trades_for_weights()

    def _score_to_multiplier(self, score: float) -> float:
        """Map a blended expectancy score (pnl-pct) to a clamped multiplier.

        multiplier = clamp(1.0 + score * slope, min_mult, max_mult) where
        slope = (max_mult - min_mult) / (2 * scale), so |score| == scale
        saturates the clamp with default symmetric bounds.
        """
        if self.scale <= 0:
            return 1.0
        slope = (self.max_mult - self.min_mult) / (2.0 * self.scale)
        mult = 1.0 + score * slope
        return max(self.min_mult, min(self.max_mult, mult))

    def _trade_age_days(self, trade: Dict[str, Any], now: datetime) -> float:
        """Compute trade age in days from exit_time (entry_time fallback)."""
        raw = trade.get("exit_time") or trade.get("entry_time")
        if raw is None:
            return 0.0
        if isinstance(raw, datetime):
            ts = raw
        else:
            text = str(raw)
            try:
                ts = datetime.fromisoformat(text)
            except ValueError:
                try:
                    ts = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    return 0.0
        # Compare naive-to-naive; DB timestamps are stored naive local/ISO.
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        ref = now.replace(tzinfo=None) if now.tzinfo is not None else now
        return max(0.0, (ref - ts).total_seconds() / 86400.0)

    def _persist_snapshot(self, computed_at: str) -> None:
        """Save the freshly computed cells to the adaptive_weights table."""
        if not self._cells or self.db is None:
            return
        rows = [
            {
                "computed_at": computed_at,
                "regime": regime_key,
                "strategy": strategy_key,
                "trade_count": cell["trade_count"],
                "recent_expectancy": cell["recent_expectancy"],
                "lifetime_expectancy": cell["lifetime_expectancy"],
                "multiplier": cell["multiplier"],
            }
            for (regime_key, strategy_key), cell in self._cells.items()
        ]
        try:
            self.db.save_adaptive_weight_snapshot(rows)
        except Exception as e:
            logger.warning(f"Failed to persist adaptive weight snapshot: {e}")
