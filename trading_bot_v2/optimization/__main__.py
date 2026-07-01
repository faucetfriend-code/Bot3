"""
Optimization Module Entry Point
================================

Allows running the optimization module directly:
    python -m trading_bot_v2.optimization --strategy mean_reversion --trials 100
"""

from .run_optimize import main

if __name__ == "__main__":
    main()
