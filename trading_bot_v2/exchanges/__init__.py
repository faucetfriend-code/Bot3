"""Exchange adapter factory and registry.

Select the active exchange with the ``EXCHANGE`` environment variable
(default: "pacifica").  Adding a new exchange is one registry entry plus
an adapter module implementing ``ExchangeClient``.

Usage:
    from trading_bot_v2.exchanges import get_exchange_client

    exchange = get_exchange_client()  # per-process singleton
    exchange.place_order("BTC", OrderSide.BUY, 0.5)
"""

import os
import threading
from typing import Any, Dict, Optional, Protocol

from .base import (
    ExchangeBalance,
    ExchangeCapabilities,
    ExchangeClient,
    ExchangeFill,
    ExchangeOrder,
    ExchangePosition,
    FundingInfo,
    PositionSide,
)
from .blofin import BlofinExchange
from .pacifica import PacificaExchange

__all__ = [
    "ExchangeBalance",
    "ExchangeCapabilities",
    "ExchangeClient",
    "ExchangeFill",
    "ExchangeOrder",
    "ExchangePosition",
    "FundingInfo",
    "PositionSide",
    "PacificaExchange",
    "BlofinExchange",
    "get_exchange_client",
    "get_exchange_capabilities",
    "reset_exchange_singletons",
]

DEFAULT_EXCHANGE = "pacifica"


class _AdapterFactory(Protocol):
    # Typing-only view of an adapter class: constructible with the optional
    # injected native clients, and exposing the capabilities() classmethod.
    def __call__(
        self, rest_client: Any = ..., ws_client: Any = ...
    ) -> ExchangeClient: ...

    def capabilities(self) -> ExchangeCapabilities: ...


# Adding an exchange == one entry here (plus the adapter module).
EXCHANGE_REGISTRY: Dict[str, _AdapterFactory] = {
    "pacifica": PacificaExchange,
    "blofin": BlofinExchange,
}

_singletons: Dict[str, ExchangeClient] = {}
_singleton_lock = threading.Lock()


def _resolve_name(name: Optional[str]) -> str:
    """Resolve and validate the exchange name (env fallback, defaults)."""
    resolved = (name or os.getenv("EXCHANGE") or DEFAULT_EXCHANGE).strip().lower()
    resolved = resolved or DEFAULT_EXCHANGE
    if resolved not in EXCHANGE_REGISTRY:
        valid = ", ".join(sorted(EXCHANGE_REGISTRY))
        raise ValueError(
            f"Unknown EXCHANGE '{resolved}'. Valid choices: {valid}. "
            "Set the EXCHANGE environment variable to one of these."
        )
    return resolved


def get_exchange_client(
    name: Optional[str] = None,
    rest_client: Any = None,
    ws_client: Any = None,
) -> ExchangeClient:
    """Return the exchange adapter selected by ``EXCHANGE`` (or ``name``).

    Args:
        name: Exchange name override; defaults to the EXCHANGE env var,
            then to "pacifica".
        rest_client: Optional existing native REST client to wrap.  When
            provided, a FRESH adapter instance is returned (not the
            process singleton) so injected clients - e.g. test mocks -
            are never shared across owners.
        ws_client: Optional existing native WebSocket client to wrap
            (same fresh-instance rule as ``rest_client``).

    Returns:
        An ExchangeClient adapter.  Without injected clients this is a
        per-process singleton per exchange name.

    Raises:
        ValueError: If the resolved exchange name is not registered.
    """
    resolved = _resolve_name(name)
    adapter_cls = EXCHANGE_REGISTRY[resolved]

    if rest_client is not None or ws_client is not None:
        return adapter_cls(rest_client=rest_client, ws_client=ws_client)

    with _singleton_lock:
        if resolved not in _singletons:
            _singletons[resolved] = adapter_cls()
        return _singletons[resolved]


def get_exchange_capabilities(name: Optional[str] = None) -> ExchangeCapabilities:
    """Return capabilities for the selected exchange without construction.

    Args:
        name: Exchange name override; same resolution as
            ``get_exchange_client``.

    Returns:
        The static ExchangeCapabilities of the selected exchange.
    """
    return EXCHANGE_REGISTRY[_resolve_name(name)].capabilities()


def reset_exchange_singletons() -> None:
    """Clear cached singleton adapters (intended for tests)."""
    with _singleton_lock:
        _singletons.clear()
