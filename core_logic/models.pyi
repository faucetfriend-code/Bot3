# Type stubs for models module
from typing import Dict, Any, Optional
from enum import Enum

class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"

class Signal:
    def __init__(self, **kwargs): ...
    # Add other attributes as needed