"""
Current-price lookup shared by the position managers.

MigratedPositionManager and RegimePositionReviewer both need "the price of
this symbol right now". They used to call ``client.get_ticker(symbol)``,
which only the backtest stand-in (SimulatedExchange) implements: the live
REST clients (PacificaClient, BlofinClient) have no ``get_ticker``, so the
call raised AttributeError, the callers swallowed it and got no price.

The lookup order here is:

1. ``price_lookup`` - a callable injected by TradingBot (its WebSocket
   lookup with REST fallback). This is the live path.
2. ``client.get_ticker(symbol)`` - when the client really has one
   (SimulatedExchange, test doubles).
3. ``client.get_market_data(symbol)`` - the REST price snapshot every live
   client implements.
"""

from typing import Any, Callable, Dict, Optional

# A ticker source: symbol -> ticker-shaped dict (or None when unavailable).
PriceLookup = Callable[[str], Optional[Dict[str, Any]]]

# Price fields in preference order. "mark" is what the venue price feeds
# publish; the rest are the fields the bot's own ticker dicts carry.
_PRICE_FIELDS = ("last", "price", "mark_price", "mark")


def _price_from_ticker(ticker: Any) -> Optional[float]:
    """Return the first positive price in a ticker-shaped dict, else None."""
    if not isinstance(ticker, dict):
        return None
    for field in _PRICE_FIELDS:
        raw = ticker.get(field)
        if raw is None or raw == "":
            continue
        try:
            price = float(raw)
        except (TypeError, ValueError):
            continue
        if price > 0:
            return price
    return None


def fetch_current_price(
    client: Any, symbol: str, price_lookup: Optional[PriceLookup] = None
) -> Optional[float]:
    """
    Fetch the current price for a symbol from a source that exists live.

    Args:
        client: Exchange client (PacificaClient, BlofinClient or the
            backtest SimulatedExchange).
        symbol: Trading symbol, with or without the -PERP suffix.
        price_lookup: Optional injected ticker source. When given it is the
            only source used, so its own fallbacks are not duplicated.

    Returns:
        A positive price, or None when the source has no usable price.

    Raises:
        Exception: Whatever the underlying source raises. Callers decide
            how to log and degrade.
    """
    if price_lookup is not None:
        return _price_from_ticker(price_lookup(symbol))

    get_ticker = getattr(client, "get_ticker", None)
    if callable(get_ticker):
        return _price_from_ticker(get_ticker(symbol))

    clean_symbol = symbol.replace("-PERP", "").upper()
    return _price_from_ticker(client.get_market_data(clean_symbol))
