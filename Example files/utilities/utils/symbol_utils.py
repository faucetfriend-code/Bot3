def normalize_symbol(symbol: str) -> str:
    """Normalize symbol formats across the system.

    Handles Pacifica :USD suffixes and ensures consistency.
    """
    if not symbol:
        return symbol

    # Remove Pacifica :USD suffix if present
    symbol = symbol.replace(':USD', '')

    # Ensure consistent format (BTC/USD not BTC-USD)
    symbol = symbol.replace('-', '/')

    return symbol

def add_pacifica_suffix(symbol: str) -> str:
    """Add Pacifica :USD suffix for API calls."""
    if not symbol:
        return symbol

    # Only add if not already present
    if not symbol.endswith(':USD'):
        return f"{symbol}:USD"

    return symbol