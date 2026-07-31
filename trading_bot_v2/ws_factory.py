"""Market-data WebSocket client factory, selected by EXCHANGE.

Until 2026-07-30 the live server and bot constructed the Pacifica WS
singleton unconditionally, so switching EXCHANGE=blofin moved orders
and account data to Blofin while every price, kline and orderbook
still came from Pacifica. This factory is the single switch point:
both clients expose the same used surface (start/stop/is_connected/
get_price/get_kline_data/get_orderbook/subscribe_orderbook/
bootstrap_kline_cache).
"""

import os
from typing import Any


def active_exchange() -> str:
    """The configured exchange key ('pacifica' default, or 'blofin')."""
    return os.getenv("EXCHANGE", "pacifica").strip().lower() or "pacifica"


def get_market_ws_client() -> Any:
    """Return the process-wide market-data WS singleton for the
    configured exchange."""
    if active_exchange() == "blofin":
        from .blofin_ws_client import get_blofin_ws_client

        return get_blofin_ws_client()
    from .pacifica_ws_client import get_ws_client

    return get_ws_client()
