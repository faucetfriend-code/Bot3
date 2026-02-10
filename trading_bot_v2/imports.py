# DEPRECATED: This file contained forbidden sys.path.insert() calls
#
# All imports should now use absolute package paths:
#   from core_logic.models import Signal, OrderSide, TradeQuality
#   from core_logic.indicators import calculate_adx, calculate_atr, calculate_bollinger_bands
#   from .database import DatabaseManager
#   from core_logic.pacifica_client import PacificaClient, PacificaEnvironment
#
# LSP compatibility is achieved through:
# - pyrightconfig.json with proper extraPaths
# - PYTHONPATH set via launcher scripts (run_bot.bat/run_bot.sh)
# - Absolute imports without dynamic path manipulation
#
# This file is kept for reference but should not be imported.
