"""Exchange-aware trade recording and querying over the trades table.

``TradeStore`` is the single owner of trade-history access.  Consumers
(StrategyMonitor, KellyPositionSizer, AdaptiveWeightManager, the API)
query trades and aggregate performance through it instead of rolling
their own SQL.

Exchange tagging:
    The ``trades.exchange`` column records which exchange executed the
    trade.  Rows written before the column existed are NULL and are
    treated as 'pacifica' at query time via ``COALESCE`` (Pacifica was
    the only exchange before multi-exchange support landed).
    ``record_trade`` tags new rows automatically from the active
    exchange (EXCHANGE env var, default 'pacifica') unless the caller
    supplies an explicit ``exchange``.
"""

import os
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from ..database import get_db_connection
from . import metrics

if TYPE_CHECKING:
    from ..database import DatabaseManager

DEFAULT_EXCHANGE = "pacifica"

# Column list shared by the closed/open trade queries.  ``exchange`` is
# COALESCE'd so legacy NULL rows read back as the historical default.
_TRADE_COLUMNS = (
    "id, account_id, symbol, side, quantity, entry_price, exit_price, "
    "pnl, commission, strategy, regime, "
    f"COALESCE(exchange, '{DEFAULT_EXCHANGE}') AS exchange, "
    "entry_time, exit_time, status"
)

_TRADE_KEYS = (
    "id",
    "account_id",
    "symbol",
    "side",
    "quantity",
    "entry_price",
    "exit_price",
    "pnl",
    "commission",
    "strategy",
    "regime",
    "exchange",
    "entry_time",
    "exit_time",
    "status",
)


def get_active_exchange_name() -> str:
    """Return the active exchange name from the EXCHANGE env var.

    Uses the same resolution rule as the exchanges factory (lowercased,
    stripped, default 'pacifica') without importing the adapter modules,
    so history stays import-light and cycle-free.
    """
    name = (os.getenv("EXCHANGE") or DEFAULT_EXCHANGE).strip().lower()
    return name or DEFAULT_EXCHANGE


def _normalize_since(since: Any) -> Optional[str]:
    """Convert a since filter (datetime or string) to an ISO string."""
    if since is None:
        return None
    if isinstance(since, datetime):
        return since.isoformat()
    return str(since)


class TradeStore:
    """Facade over the trades table: record, query, and aggregate.

    Query methods run their own SQL through ``get_db_connection()`` (the
    shared backend-agnostic connection manager).  ``record_trade``
    delegates persistence to ``DatabaseManager.save_trade`` so cache
    invalidation and backend translation stay in one place.

    Args:
        db: Optional DatabaseManager (or compatible mock) used for
            ``record_trade``.  Lazily constructed when omitted.
        default_exchange: Exchange tag applied by ``record_trade`` when
            the trade dict has none.  Defaults to the active exchange
            resolved from the EXCHANGE env var at record time.
    """

    def __init__(
        self,
        db: Optional["DatabaseManager"] = None,
        default_exchange: Optional[str] = None,
    ) -> None:
        self._db = db
        self._default_exchange = default_exchange

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    @property
    def db(self) -> "DatabaseManager":
        """Return the wired DatabaseManager, constructing one lazily."""
        if self._db is None:
            from ..database import DatabaseManager

            self._db = DatabaseManager()
        return self._db

    def record_trade(
        self, trade_data: Dict[str, Any], account_id: str = "sub_1"
    ) -> Optional[int]:
        """Persist a trade, tagging the active exchange when untagged.

        Args:
            trade_data: Trade dict accepted by
                ``DatabaseManager.save_trade``; an ``exchange`` key, if
                present and truthy, is preserved as-is.
            account_id: Owning account id.

        Returns:
            Row id of the inserted trade (backend permitting).
        """
        data = dict(trade_data)
        if not data.get("exchange"):
            data["exchange"] = self._default_exchange or get_active_exchange_name()
        return self.db.save_trade(data, account_id=account_id)

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------
    def get_closed_trades(
        self,
        strategy: Optional[str] = None,
        regime: Optional[str] = None,
        exchange: Optional[str] = None,
        symbol: Optional[str] = None,
        since: Optional[Any] = None,
        limit: Optional[int] = None,
        account_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch closed trades, newest first, with optional filters.

        Args:
            strategy: Exact strategy tag filter (e.g. "MEAN_REVERSION").
            regime: Exact regime tag filter (e.g. "ranging_calm").
            exchange: Exchange filter; legacy NULL rows match 'pacifica'.
            symbol: Exact symbol filter.
            since: Only trades with ``exit_time >= since`` (datetime or
                ISO string).
            limit: Max rows (most recent by exit_time).
            account_id: Owning account filter.

        Returns:
            List of trade dicts (see ``_TRADE_KEYS``) ordered by
            exit_time descending.
        """
        return self._query_trades(
            status="closed",
            strategy=strategy,
            regime=regime,
            exchange=exchange,
            symbol=symbol,
            since=since,
            limit=limit,
            account_id=account_id,
            order_by="exit_time DESC",
        )

    def get_open_trades(
        self,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None,
        regime: Optional[str] = None,
        exchange: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch open trades, newest first, with optional filters.

        Args:
            symbol: Exact symbol filter.
            strategy: Exact strategy tag filter.
            regime: Exact regime tag filter.
            exchange: Exchange filter; legacy NULL rows match 'pacifica'.
            account_id: Owning account filter.

        Returns:
            List of trade dicts ordered by entry_time descending.
        """
        return self._query_trades(
            status="open",
            strategy=strategy,
            regime=regime,
            exchange=exchange,
            symbol=symbol,
            since=None,
            limit=None,
            account_id=account_id,
            order_by="entry_time DESC",
        )

    def get_recent_trades(
        self,
        limit: int = 100,
        status: Optional[str] = None,
        exchange: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch recent trades of any status for API/history views.

        Mirrors the legacy ``DatabaseManager.get_trades`` result shape
        (including the constant ``type: "trade"`` marker) with an added
        ``exchange`` field, ordered by entry_time descending.

        Args:
            limit: Max rows.
            status: Optional status filter ('open'/'closed'/...).
            exchange: Exchange filter; legacy NULL rows match 'pacifica'.

        Returns:
            List of trade dicts with id, symbol, asset_class, side,
            quantity, entry_price, exit_price, pnl, entry_time,
            exit_time, type, status, exchange.
        """
        query = (
            "SELECT id, symbol, asset_class, side, quantity, entry_price, "
            "exit_price, pnl, entry_time, exit_time, 'trade' AS type, status, "
            f"COALESCE(exchange, '{DEFAULT_EXCHANGE}') AS exchange "
            "FROM trades"
        )
        clauses: List[str] = []
        params: List[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if exchange:
            clauses.append(f"COALESCE(exchange, '{DEFAULT_EXCHANGE}') = ?")
            params.append(str(exchange).strip().lower())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY entry_time DESC LIMIT ?"
        params.append(limit)

        keys = (
            "id",
            "symbol",
            "asset_class",
            "side",
            "quantity",
            "entry_price",
            "exit_price",
            "pnl",
            "entry_time",
            "exit_time",
            "type",
            "status",
            "exchange",
        )
        with get_db_connection() as conn:
            cursor = conn.execute(query, tuple(params))
            rows = cursor.fetchall()
        return [dict(zip(keys, row)) for row in rows]

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------
    def aggregate(
        self,
        group_by: Sequence[str] = ("strategy", "regime"),
        strategy: Optional[str] = None,
        regime: Optional[str] = None,
        exchange: Optional[str] = None,
        symbol: Optional[str] = None,
        since: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Aggregate closed-trade performance grouped by the given keys.

        Stats are computed on dollar PnL via :mod:`metrics`.  Missing
        group values map to "UNKNOWN" (strategy) / "UNTAGGED" (regime).

        Args:
            group_by: Ordered grouping keys, each one of "strategy",
                "regime", "exchange", "symbol".
            strategy: Optional pre-filter, as in ``get_closed_trades``.
            regime: Optional pre-filter.
            exchange: Optional pre-filter.
            symbol: Optional pre-filter.
            since: Optional pre-filter on exit_time.

        Returns:
            Nested dict following ``group_by`` order whose leaves are
            stats dicts with trade_count, win_rate, profit_factor,
            expectancy, total_pnl.

        Raises:
            ValueError: If ``group_by`` contains an unsupported key.
        """
        valid = {"strategy", "regime", "exchange", "symbol"}
        for key in group_by:
            if key not in valid:
                raise ValueError(
                    f"Unsupported group_by key {key!r}; valid: {sorted(valid)}"
                )

        trades = self.get_closed_trades(
            strategy=strategy,
            regime=regime,
            exchange=exchange,
            symbol=symbol,
            since=since,
        )

        grouped: Dict[Tuple[str, ...], List[float]] = {}
        for trade in trades:
            path = tuple(self._group_value(trade, key) for key in group_by)
            try:
                pnl = float(trade.get("pnl") or 0.0)
            except (TypeError, ValueError):
                continue
            grouped.setdefault(path, []).append(pnl)

        result: Dict[str, Any] = {}
        for path, pnls in grouped.items():
            node = result
            for part in path[:-1]:
                node = node.setdefault(part, {})
            leaf = path[-1] if path else "ALL"
            pf = metrics.profit_factor(pnls)
            node[leaf] = {
                "trade_count": len(pnls),
                "win_rate": round(metrics.win_rate(pnls), 4),
                "profit_factor": round(pf, 4) if pf is not None else None,
                "expectancy": round(metrics.expectancy(pnls), 4),
                "total_pnl": round(sum(pnls), 4),
            }
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _group_value(trade: Dict[str, Any], key: str) -> str:
        """Resolve a grouping value with the untagged-bucket conventions."""
        value = trade.get(key)
        if value:
            return str(value)
        if key == "strategy":
            return "UNKNOWN"
        if key == "regime":
            return "UNTAGGED"
        if key == "exchange":
            return DEFAULT_EXCHANGE
        return "UNKNOWN"

    def _query_trades(
        self,
        status: str,
        strategy: Optional[str],
        regime: Optional[str],
        exchange: Optional[str],
        symbol: Optional[str],
        since: Optional[Any],
        limit: Optional[int],
        account_id: Optional[str],
        order_by: str,
    ) -> List[Dict[str, Any]]:
        """Build and run a filtered trades query, returning dicts."""
        query = f"SELECT {_TRADE_COLUMNS} FROM trades WHERE status = ?"
        params: List[Any] = [status]
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        if regime:
            query += " AND regime = ?"
            params.append(regime)
        if exchange:
            query += f" AND COALESCE(exchange, '{DEFAULT_EXCHANGE}') = ?"
            params.append(str(exchange).strip().lower())
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        since_iso = _normalize_since(since)
        if since_iso is not None:
            query += " AND exit_time >= ?"
            params.append(since_iso)
        if account_id:
            query += " AND account_id = ?"
            params.append(account_id)
        query += f" ORDER BY {order_by}"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        with get_db_connection() as conn:
            cursor = conn.execute(query, tuple(params))
            rows = cursor.fetchall()
        return [dict(zip(_TRADE_KEYS, row)) for row in rows]
